"""SQLite event cache.

Datetimes are stored twice per column pair: an ISO-8601 string with UTC offset
(exact round-trip of tz-aware values) and a UTC epoch REAL (for range queries).
All-day events keep all_day=True with start/end as tz-aware local midnight
datetimes (end exclusive), stored the same way.

Thread safety: every call opens its own short-lived sqlite3 connection, so the
Cache can be shared between the sync thread and the UI thread.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .models import CalendarInfo, Event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    account TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    id TEXT NOT NULL,
    calendar_name TEXT NOT NULL,
    color TEXT NOT NULL,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    all_day INTEGER NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL,
    meeting_url TEXT,
    recurring_id TEXT,
    busy INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (account, calendar_id, id)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events (start_ts);
CREATE TABLE IF NOT EXISTS calendars (
    account TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    is_primary INTEGER NOT NULL,
    PRIMARY KEY (account, id)
);
"""

_INSERT = """
INSERT OR REPLACE INTO events (account, calendar_id, id, calendar_name, color, title,
    start, end, start_ts, end_ts, all_day, location, description, meeting_url, recurring_id, busy)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.astimezone()


def _row(account: str, e: Event) -> tuple:
    s, en = _aware(e.start), _aware(e.end)
    return (account, e.calendar_id, e.id, e.calendar_name, e.color, e.title,
            s.isoformat(), en.isoformat(), s.timestamp(), en.timestamp(),
            int(e.all_day), e.location or "", e.description or "",
            e.meeting_url, e.recurring_id, int(e.busy))


class Cache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con, con:
            con.executescript(_SCHEMA)
            cols = {r["name"] for r in con.execute("PRAGMA table_info(events)")}
            if "busy" not in cols:  # database created before v0.1.18
                con.execute("ALTER TABLE events ADD COLUMN busy INTEGER NOT NULL DEFAULT 1")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def upsert_events(self, account: str, events: list[Event]) -> None:
        with closing(self._connect()) as con, con:
            con.executemany(_INSERT, [_row(account, e) for e in events])

    def delete_event(self, account: str, calendar_id: str, event_id: str) -> None:
        with closing(self._connect()) as con, con:
            con.execute("DELETE FROM events WHERE account = ? AND calendar_id = ? AND id = ?",
                        (account, calendar_id, event_id))

    def events_by_id(self, event_id: str) -> list[Event]:
        """Every cached event with this id (ids are only unique per calendar)."""
        with closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM events WHERE id = ? ORDER BY account, calendar_id",
                               (event_id,)).fetchall()
        return [self._event(r) for r in rows]

    def upsert_calendars(self, account: str, calendars: list[CalendarInfo]) -> None:
        """Replace the account's stored calendar list."""
        with closing(self._connect()) as con, con:
            con.execute("DELETE FROM calendars WHERE account = ?", (account,))
            con.executemany(
                "INSERT INTO calendars (account, id, name, color, is_primary) VALUES (?,?,?,?,?)",
                [(account, c.id, c.name, c.color, int(c.primary)) for c in calendars])

    def calendars(self) -> list[CalendarInfo]:
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT * FROM calendars ORDER BY account, is_primary DESC, name, id").fetchall()
        return [CalendarInfo(r["id"], r["name"], r["color"], bool(r["is_primary"])) for r in rows]

    def delete_account(self, account: str) -> None:
        """Purge every event and calendar row belonging to `account`."""
        with closing(self._connect()) as con, con:
            con.execute("DELETE FROM events WHERE account = ?", (account,))
            con.execute("DELETE FROM calendars WHERE account = ?", (account,))

    def calendars_by_account(self) -> dict[str, list[CalendarInfo]]:
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT * FROM calendars ORDER BY account, is_primary DESC, name, id").fetchall()
        out: dict[str, list[CalendarInfo]] = {}
        for r in rows:
            out.setdefault(r["account"], []).append(
                CalendarInfo(r["id"], r["name"], r["color"], bool(r["is_primary"])))
        return out

    def events_between(self, start: datetime, end: datetime) -> list[Event]:
        """Events overlapping [start, end), sorted by start then title."""
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT * FROM events WHERE start_ts < ? AND end_ts > ? "
                "ORDER BY start_ts, title, id",
                (_aware(end).timestamp(), _aware(start).timestamp()),
            ).fetchall()
        return [self._event(r) for r in rows]

    def replace_range(self, account: str, start: datetime, end: datetime,
                      events: list[Event]) -> None:
        """Atomically drop the account's events overlapping [start, end) and store `events`."""
        with closing(self._connect()) as con, con:
            con.execute(
                "DELETE FROM events WHERE account = ? AND start_ts < ? AND end_ts > ?",
                (account, _aware(end).timestamp(), _aware(start).timestamp()),
            )
            con.executemany(_INSERT, [_row(account, e) for e in events])

    @staticmethod
    def _event(r: sqlite3.Row) -> Event:
        return Event(
            id=r["id"], account=r["account"], calendar_id=r["calendar_id"],
            calendar_name=r["calendar_name"], color=r["color"], title=r["title"],
            start=datetime.fromisoformat(r["start"]), end=datetime.fromisoformat(r["end"]),
            all_day=bool(r["all_day"]), location=r["location"],
            description=r["description"], meeting_url=r["meeting_url"],
            recurring_id=r["recurring_id"], busy=bool(r["busy"]),
        )
