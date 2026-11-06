"""Local reminders: persisted as JSON, fired as toasts by `ReminderFirer`. Qt-free."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from .agenda import clock_text
from .config import data_dir

log = logging.getLogger(__name__)

MAX_TEXT = 200
PRUNE_AFTER = timedelta(days=2)
LATE_AFTER = timedelta(minutes=5)
MAX_UNFIRED = 500
# v0.12.0: at most this many reminder toasts at once (e.g. SmartCal starts after a week
# off with many reminders overdue): the earliest ones each get their own toast, the rest
# one summary toast. Every toast starts a PowerShell, so a flood is never sent.
MAX_TOASTS_PER_CHECK = 3


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
        self._next_id = max(self._next_id, self._read_sidecar())
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self._quarantine("unreadable")
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
            self._quarantine("invalid contents")
            return
        self._items = items
        self._next_id = max([self._next_id, nxt, *[i + 1 for i in items]])

    @property
    def _sidecar(self) -> Path:
        return self.path.with_name(self.path.name + ".nextid")

    def _read_sidecar(self) -> int:
        try:
            v = int(self._sidecar.read_text(encoding="utf-8").strip())
            return v if v > 0 else 1
        except (OSError, ValueError):
            return 1

    def _quarantine(self, why: str) -> None:
        """Keep a bad file as .corrupt and make sure old ids cannot be reused."""
        bak = self.path.with_name(self.path.name + ".corrupt")
        try:
            os.replace(self.path, bak)
            log.warning("%s in %s; moved to %s, starting empty", why, self.path, bak)
        except OSError:
            log.warning("%s in %s (could not back it up); starting empty", why, self.path)
        self._next_id = max(self._next_id, int(time.time()))

    def _save(self, raise_on_error: bool = False) -> None:
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
            if raise_on_error:
                raise
            return
        try:
            self._sidecar.write_text(str(self._next_id), encoding="utf-8")
        except OSError:
            log.warning("could not save %s", self._sidecar)

    # -- API ---------------------------------------------------------------
    def add(self, text: str, fire_at: datetime) -> int:
        text = str(text or "").strip()[:MAX_TEXT].strip()
        if not text:
            raise ValueError("reminder text is empty")
        if fire_at.tzinfo is None:
            raise ValueError("fire_at must be timezone-aware")
        with self._lock:
            if sum(1 for r in self._items.values() if not r.fired) >= MAX_UNFIRED:
                raise ValueError("too many reminders")
            rid = self._next_id
            self._next_id += 1
            self._items[rid] = Reminder(rid, text, fire_at, _aware(self.clock()))
            try:
                self._save(raise_on_error=True)
            except OSError:
                del self._items[rid]
                self._next_id = rid
                raise
            return rid

    def _prune(self, now: datetime) -> bool:
        old = [i for i, r in self._items.items()
               if r.fired and now - r.fire_at > PRUNE_AFTER]
        for i in old:
            del self._items[i]
        return bool(old)

    def _due(self, now: datetime) -> list[Reminder]:
        return sorted((r for r in self._items.values() if not r.fired and r.fire_at <= now),
                      key=lambda r: (r.fire_at, r.id))

    def due(self, now: datetime | None = None) -> list[Reminder]:
        now = _aware(now or self.clock())
        with self._lock:
            if self._prune(now):
                self._save()
            return [replace(r) for r in self._due(now)]

    def claim_due(self, now: datetime | None = None) -> list[Reminder]:
        """Atomically mark every due reminder fired (and save) and return copies."""
        now = _aware(now or self.clock())
        with self._lock:
            changed = self._prune(now)
            claimed = self._due(now)
            for r in claimed:
                r.fired = True
            if changed or claimed:
                self._save()
            return [replace(r) for r in claimed]

    def next_fire_at(self) -> datetime | None:
        """v0.12.0: when the earliest reminder not yet fired is due (None: none)."""
        with self._lock:
            pending = [r.fire_at for r in self._items.values() if not r.fired]
            return min(pending) if pending else None

    def list(self) -> list[Reminder]:
        with self._lock:
            return [replace(r) for r in sorted(
                (r for r in self._items.values() if not r.fired),
                key=lambda r: (r.fire_at, r.id))]

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

    def __init__(self, reminders: Reminders, send_toast, clock=datetime.now, on_fired=None):
        self.reminders, self.send_toast, self.clock = reminders, send_toast, clock
        self.on_fired = on_fired

    def check(self, now: datetime | None = None) -> None:
        now = _aware(now or self.clock())
        claimed = self.reminders.claim_due(now)  # marked fired before any sending
        if len(claimed) > MAX_TOASTS_PER_CHECK:
            shown, rest = claimed[:MAX_TOASTS_PER_CHECK - 1], claimed[MAX_TOASTS_PER_CHECK - 1:]
        else:
            shown, rest = claimed, []
        for r in shown:
            body = r.text
            if now - r.fire_at > LATE_AFTER:
                body = f"(was due {due_text(r.fire_at, now)}) {r.text}"
            self._send("Reminder", body, r.id)
        if rest:
            texts = "; ".join(r.text for r in rest[:3]) + ("; …" if len(rest) > 3 else "")
            self._send("Reminders", f"{len(rest)} more were due: {texts}", None)
        if claimed and self.on_fired:
            try:
                self.on_fired()
            except Exception:  # noqa: BLE001
                log.exception("on_fired callback failed")

    def _send(self, title: str, body: str, rid) -> None:
        try:
            self.send_toast(title, body, None)
        except Exception:  # noqa: BLE001
            log.exception("toast for reminder %s failed", rid)


def due_text(fire_at: datetime, now: datetime) -> str:
    """When an overdue reminder was due: '3:45 PM' today, else 'Wed 3:45 PM' within the
    week, else 'Nov 4, 3:45 PM' (local time)."""
    when, here = fire_at.astimezone(), now.astimezone()
    days = (here.date() - when.date()).days
    if days == 0:
        return clock_text(when)
    if 0 < days < 7:
        return f"{when:%a} {clock_text(when)}"
    return f"{when:%b} {when.day}, {clock_text(when)}"
