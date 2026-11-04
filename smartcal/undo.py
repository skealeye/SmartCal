"""Persistent undo journal for calendar writes. Qt-free.

Each write made through `Actions` records a `Change` holding the event's state
before the write (fetched from Google, not the cache). `UndoStore.undo_last`
reverses the newest change, unless the event was changed elsewhere since.

A change is journaled `pending` (with Google's `updated` from before the write)
and finalized with `after_updated` once the write succeeded. Undo never
overwrites an event unless its current `updated` equals `after_updated`
(or the caller forces it).
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
_PLACEHOLDER = "(no title)"


@dataclass
class Change:
    kind: str                      # "create" | "update" | "move" | "delete"
    account: str
    calendar_id: str
    event_id: str
    before: dict | None            # event state before the write; None for create
    after_updated: str | None      # Google `updated` right after our write (None while pending)
    summary: str                   # event title (user-facing only, never logged)
    at: str                        # ISO time of the write
    before_updated: str | None = None  # Google `updated` right before the write
    pending: bool = False          # True until the write finished and was finalized


def snapshot(ev: Event) -> dict:
    """JSON-safe copy of the fields undo can restore.

    The client's "(no title)" placeholder is stored as "" (what Google holds), so
    restoring never writes the placeholder into the calendar.
    """
    return {"title": "" if ev.title == _PLACEHOLDER else ev.title,
            "start": ev.start.isoformat(), "end": ev.end.isoformat(),
            "all_day": ev.all_day, "location": ev.location, "description": ev.description,
            "recurring_id": ev.recurring_id, "recurrence": ev.recurrence}


def _event_from(change: Change) -> Event:
    b = change.before
    return Event(id=change.event_id, account=change.account, calendar_id=change.calendar_id,
                 calendar_name="", color="", title=b["title"],
                 start=datetime.fromisoformat(b["start"]), end=datetime.fromisoformat(b["end"]),
                 all_day=b["all_day"], location=b["location"], description=b["description"],
                 recurring_id=b.get("recurring_id"), recurrence=b.get("recurrence"),
                 is_series_master=bool(b.get("recurrence")))


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
        for k in ("after_updated", "before_updated"):
            if d.get(k) is not None and not isinstance(d[k], str):
                return False
        if not isinstance(d.get("pending", False), bool):
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
        self.lock = threading.RLock()
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
        if not isinstance(items, list):
            self._quarantine("invalid contents")
            return
        good = []
        for i, d in enumerate(items):
            if _valid(d):
                good.append(Change(**{k: d[k] for k in Change.__dataclass_fields__ if k in d}))
            else:
                log.warning("skipping malformed undo entry %d in %s", i, self.path)
        self._items = good[-self.limit:]

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
        with self.lock:
            self._items.append(change)
            del self._items[:-self.limit]
            self._save()
        return change

    def discard(self, change: Change) -> None:
        """Drop an entry (by identity), e.g. when its write failed."""
        with self.lock:
            self._items = [c for c in self._items if c is not change]
            self._save()

    def finalize(self, change: Change, after_updated: str | None) -> None:
        with self.lock:
            change.after_updated = after_updated
            change.pending = False
            self._save()

    def peek(self) -> Change | None:
        with self.lock:
            return self._items[-1] if self._items else None

    def __len__(self) -> int:
        with self.lock:
            return len(self._items)

    def _carry_updated(self, done: Change, updated: str | None) -> None:
        """After undoing `done`, the next older entry for the same event now expects
        the event at `updated`, so a second undo in a row does not falsely warn."""
        if updated is None:
            return
        key = (done.account, done.calendar_id, done.event_id)
        for c in reversed(self._items):
            if c is not done and (c.account, c.calendar_id, c.event_id) == key \
                    and self._items.index(c) < self._items.index(done):
                c.after_updated, c.pending = updated, False
                return

    def undo_last(self, actions, force: bool = False) -> str:
        """Reverse the newest change; returns a plain-text line for the user.

        Nothing is overwritten unless Google's current `updated` equals the stamp
        recorded after our write (or force=True); otherwise a warning is returned
        and the entry is kept. A failing Google call propagates and keeps the
        entry. Undo itself is not journaled.
        """
        with self.lock:
            c = self.peek()
            if c is None:
                return "Nothing to undo."
            client, cache = actions.client(c.account), actions.cache
            try:
                current = client.get_event(c.event_id, calendar_id=c.calendar_id)
            except Exception as exc:  # noqa: BLE001
                if not _gone(exc):
                    raise
                current = None
            absent = current is None or current.status == "cancelled"
            not_yet = (f"Nothing to undo yet: the last change to {c.summary} was not "
                       f"applied.")
            if c.kind == "delete":
                if not absent:
                    self.discard(c)
                    if c.pending and current.updated == c.before_updated:
                        return not_yet
                    return f"Can't undo: {c.summary} is already back on the calendar."
            else:
                if absent:
                    cache.delete_event(c.account, c.calendar_id, c.event_id)
                    self.discard(c)
                    return "Can't undo: the event no longer exists."
                if c.pending and current.updated == c.before_updated:
                    self.discard(c)      # the write never happened
                    return not_yet
                if not force:
                    if c.after_updated is None:
                        return (f"Not undone: the last change to {c.summary} did not "
                                f"finish recording, so it cannot be told apart from an "
                                f"edit made elsewhere. Undo again with force to overwrite.")
                    if current.updated != c.after_updated:
                        return (f"Not undone: {c.summary} was changed elsewhere since "
                                f"SmartCal edited it. Undo again with force to overwrite "
                                f"those changes.")
            restored = None
            if c.kind == "create":
                client.delete_event(c.event_id, calendar_id=c.calendar_id)
                cache.delete_event(c.account, c.calendar_id, c.event_id)
                line = f"Undone: removed {c.summary}"
            elif c.kind == "delete":
                restored = client.restore_event(_event_from(c))
                cache.upsert_events(c.account, [restored])
                line = f"Undone: restored {c.summary}"
            else:
                b = c.before
                restored = client.update_event(
                    c.event_id, calendar_id=c.calendar_id,
                    **{k: (datetime.fromisoformat(b[k]) if k in ("start", "end") else b[k])
                       for k in _FIELDS})
                cache.upsert_events(c.account, [restored])
                name = b["title"] or _PLACEHOLDER
                if c.kind == "move":
                    line = (f"Undone: moved {name} back to "
                            f"{_when(datetime.fromisoformat(b['start']))}")
                else:
                    line = f"Undone: reverted changes to {name}"
            if restored is not None and restored.id == c.event_id:
                self._carry_updated(c, restored.updated)
            self.discard(c)
            return line
