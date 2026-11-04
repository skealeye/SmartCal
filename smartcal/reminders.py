"""Local reminders: persisted as JSON, fired as toasts by `ReminderFirer`. Qt-free."""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import data_dir

log = logging.getLogger(__name__)

MAX_TEXT = 200
PRUNE_AFTER = timedelta(days=2)
LATE_AFTER = timedelta(minutes=5)


@dataclass
class Reminder:
    id: int
    text: str
    fire_at: datetime  # tz-aware
    created_at: datetime
    fired: bool = False


def _aware(dt: datetime) -> datetime:
    return dt.astimezone() if dt.tzinfo is None else dt


class Reminders:
    def __init__(self, path=None, clock=datetime.now):
        self.path = Path(path) if path else data_dir() / "reminders.json"
        self.clock = clock
        self._lock = threading.RLock()
        self._items: dict[int, Reminder] = {}
        self._next_id = 1
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log.warning("could not read %s; starting empty", self.path)
            return
        try:
            items = {}
            for d in data["items"]:
                if isinstance(d["id"], bool) or not isinstance(d["id"], int):
                    raise TypeError("bad id")
                if not isinstance(d["text"], str) or not isinstance(d["fired"], bool):
                    raise TypeError("bad field")
                r = Reminder(id=d["id"], text=d["text"][:MAX_TEXT],
                             fire_at=_aware(datetime.fromisoformat(d["fire_at"])),
                             created_at=_aware(datetime.fromisoformat(d["created_at"])),
                             fired=d["fired"])
                items[r.id] = r
            nxt = data["next_id"]
            if not isinstance(nxt, int) or isinstance(nxt, bool):
                raise TypeError("bad next_id")
        except (KeyError, TypeError, ValueError, AttributeError):
            log.warning("invalid contents in %s; starting empty", self.path)
            return
        self._items = items
        self._next_id = max([nxt, *[i + 1 for i in items]])

    def _save(self) -> None:
        data = {"next_id": self._next_id, "items": [
            {"id": r.id, "text": r.text, "fire_at": r.fire_at.isoformat(),
             "created_at": r.created_at.isoformat(), "fired": r.fired}
            for r in self._items.values()]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("could not save %s", self.path)

    # -- API ---------------------------------------------------------------
    def add(self, text: str, fire_at: datetime) -> int:
        text = str(text or "").strip()[:MAX_TEXT].strip()
        if not text:
            raise ValueError("reminder text is empty")
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            self._items[rid] = Reminder(rid, text, _aware(fire_at), _aware(self.clock()))
            self._save()
            return rid

    def due(self, now: datetime | None = None) -> list[Reminder]:
        now = _aware(now or self.clock())
        with self._lock:
            old = [i for i, r in self._items.items()
                   if r.fired and now - r.fire_at > PRUNE_AFTER]
            for i in old:
                del self._items[i]
            if old:
                self._save()
            return sorted((r for r in self._items.values()
                           if not r.fired and r.fire_at <= now),
                          key=lambda r: (r.fire_at, r.id))

    def list(self) -> list[Reminder]:
        with self._lock:
            return sorted((r for r in self._items.values() if not r.fired),
                          key=lambda r: (r.fire_at, r.id))

    def mark_fired(self, rid: int) -> None:
        with self._lock:
            r = self._items.get(rid)
            if r and not r.fired:
                r.fired = True
                self._save()

    def cancel(self, rid: int) -> None:
        with self._lock:
            if self._items.pop(rid, None) is not None:
                self._save()


class ReminderFirer:
    """Fires due reminders through `send_toast(title, body, url)`; call `check()` from a timer."""

    def __init__(self, reminders: Reminders, send_toast, clock=datetime.now):
        self.reminders, self.send_toast, self.clock = reminders, send_toast, clock

    def check(self, now: datetime | None = None) -> None:
        now = _aware(now or self.clock())
        for r in self.reminders.due(now):
            body = r.text
            if now - r.fire_at > LATE_AFTER:
                body = f"(was due {r.fire_at.astimezone():%H:%M}) {r.text}"
            # Mark before sending: a crash or failing toast must not repeat it.
            self.reminders.mark_fired(r.id)
            try:
                self.send_toast("Reminder", body, None)
            except Exception:  # noqa: BLE001
                log.exception("toast for reminder %s failed", r.id)
