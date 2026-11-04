"""Persistent undo journal for calendar writes. Qt-free.

Each write made through `Actions` records a `Change` holding the event's state
before the write (fetched from Google, not the cache). `UndoStore.undo_last`
reverses the newest change, unless the event was changed elsewhere since.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import data_dir
from .models import Event

log = logging.getLogger(__name__)

KINDS = ("create", "update", "move", "delete")
_FIELDS = ("title", "start", "end", "all_day", "location", "description")


@dataclass
class Change:
    kind: str                      # "create" | "update" | "move" | "delete"
    account: str
    calendar_id: str
    event_id: str
    before: dict | None            # event state before the write; None for create
    after_updated: str | None      # Google `updated` stamp right after our write
    summary: str                   # event title (user-facing only, never logged)
    at: str                        # ISO time of the write


def snapshot(ev: Event) -> dict:
    """JSON-safe copy of the fields undo can restore."""
    return {"title": ev.title, "start": ev.start.isoformat(), "end": ev.end.isoformat(),
            "all_day": ev.all_day, "location": ev.location, "description": ev.description,
            "recurring_id": ev.recurring_id}


def _event_from(change: Change) -> Event:
    b = change.before
    return Event(id=change.event_id, account=change.account, calendar_id=change.calendar_id,
                 calendar_name="", color="", title=b["title"],
                 start=datetime.fromisoformat(b["start"]), end=datetime.fromisoformat(b["end"]),
                 all_day=b["all_day"], location=b["location"], description=b["description"],
                 recurring_id=b.get("recurring_id"))


def _when(dt: datetime) -> str:
    dt = dt.astimezone() if dt.tzinfo is None else dt
    return f"{dt:%a} {dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _valid(d) -> bool:
    if not isinstance(d, dict):
        return False
    try:
        if d["kind"] not in KINDS:
            return False
        for k in ("account", "calendar_id", "event_id", "summary", "at"):
            if not isinstance(d[k], str):
                return False
        if d["after_updated"] is not None and not isinstance(d["after_updated"], str):
            return False
        b = d["before"]
        if b is None:
            return d["kind"] == "create"
        if d["kind"] == "create":
            return False
        datetime.fromisoformat(b["start"])
        datetime.fromisoformat(b["end"])
        return (isinstance(b["title"], str) and isinstance(b["all_day"], bool)
                and isinstance(b["location"], str) and isinstance(b["description"], str))
    except (KeyError, TypeError, ValueError):
        return False


def _gone(exc: Exception) -> bool:
    return getattr(getattr(exc, "resp", None), "status", None) in (404, 410)


class UndoStore:
    def __init__(self, path=None, limit: int = 20):
        self.path = Path(path) if path else data_dir() / "undo.json"
        self.limit = limit
        self._lock = threading.RLock()
        self._items: list[Change] = []
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self._quarantine("unreadable")
            return
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not all(_valid(d) for d in items):
            self._quarantine("invalid contents")
            return
        self._items = [Change(**{k: d[k] for k in Change.__dataclass_fields__})
                       for d in items][-self.limit:]

    def _quarantine(self, why: str) -> None:
        bak = self.path.with_name(self.path.name + ".corrupt")
        try:
            os.replace(self.path, bak)
            log.warning("%s in %s; moved to %s, starting empty", why, self.path, bak)
        except OSError:
            log.warning("%s in %s (could not back it up); starting empty", why, self.path)

    def _save(self) -> None:
        data = {"items": [asdict(c) for c in self._items]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("could not save %s", self.path)

    # -- API ---------------------------------------------------------------
    def record(self, change: Change) -> Change:
        with self._lock:
            self._items.append(change)
            del self._items[:-self.limit]
            self._save()
        return change

    def discard(self, change: Change) -> None:
        """Drop an entry (by identity), e.g. when its write failed."""
        with self._lock:
            self._items = [c for c in self._items if c is not change]
            self._save()

    def finalize(self, change: Change, after_updated: str | None) -> None:
        with self._lock:
            change.after_updated = after_updated
            self._save()

    def peek(self) -> Change | None:
        with self._lock:
            return self._items[-1] if self._items else None

    def __len__(self) -> int:
        return len(self._items)

    def undo_last(self, actions, force: bool = False) -> str:
        """Reverse the newest change; returns a plain-text line for the user.

        If Google's current `updated` stamp differs from the one recorded right
        after our write, the event changed elsewhere: return a warning and keep
        the entry unless force=True. A failing Google call propagates and keeps
        the entry. Undo itself is never journaled.
        """
        with self._lock:
            c = self.peek()
            if c is None:
                return "Nothing to undo."
            client, cache = actions.client(c.account), actions.cache
            if c.kind != "delete":
                try:
                    current = client.get_event(c.event_id, calendar_id=c.calendar_id)
                except Exception as exc:  # noqa: BLE001
                    if not _gone(exc):
                        raise
                    cache.delete_event(c.account, c.calendar_id, c.event_id)
                    self.discard(c)
                    if c.kind == "create":
                        return f"Undone: {c.summary} was already deleted"
                    return f"Not undone: {c.summary} no longer exists"
                if (not force and c.after_updated is not None
                        and current.updated != c.after_updated):
                    return (f"Not undone: {c.summary} was changed elsewhere since SmartCal's "
                            f"edit. Undo again with force to overwrite those changes.")
            if c.kind == "create":
                client.delete_event(c.event_id, calendar_id=c.calendar_id)
                cache.delete_event(c.account, c.calendar_id, c.event_id)
                line = f"Undone: removed {c.summary}"
            elif c.kind == "delete":
                cache.upsert_events(c.account, [client.restore_event(_event_from(c))])
                line = f"Undone: restored {c.summary}"
            else:
                b = c.before
                restored = client.update_event(
                    c.event_id, calendar_id=c.calendar_id,
                    **{k: (datetime.fromisoformat(b[k]) if k in ("start", "end") else b[k])
                       for k in _FIELDS})
                cache.upsert_events(c.account, [restored])
                if c.kind == "move":
                    line = (f"Undone: moved {b['title']} back to "
                            f"{_when(datetime.fromisoformat(b['start']))}")
                else:
                    line = f"Undone: reverted changes to {b['title']}"
            self.discard(c)
            return line
