"""Event reminders. Qt-free: run `check()` from a timer (every 30 s)."""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

from .config import data_dir

log = logging.getLogger(__name__)

PRUNE_AFTER = timedelta(days=2)
MAX_LEAD_MINUTES = 1440


class Notifier:
    def __init__(self, cache, settings, send_toast, clock=datetime.now, fired_path=None):
        self.cache, self.settings, self.send_toast = cache, settings, send_toast
        self.clock = clock
        self.fired_path = Path(fired_path) if fired_path else data_dir() / "notified.json"
        self.fired: dict[str, float] = self._load()  # key -> event start (epoch seconds)

    # -- persistence -------------------------------------------------------
    def _load(self) -> dict[str, float]:
        try:
            data = json.loads(self.fired_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: float(v) for k, v in data.items()
                if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)}

    def _save(self) -> None:
        try:
            self.fired_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.fired_path.with_name(self.fired_path.name + ".tmp")
            tmp.write_text(json.dumps(self.fired), encoding="utf-8")
            os.replace(tmp, self.fired_path)
        except OSError:
            log.exception("could not save %s", self.fired_path)

    @staticmethod
    def _key(ev) -> str:
        return f"{ev.account}|{ev.calendar_id}|{ev.id}|{ev.start.isoformat()}"

    # -- main entry --------------------------------------------------------
    def check(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        if now.tzinfo is None:
            now = now.astimezone()
        changed = self._prune(now)
        primary = {c.id for c in self.cache.calendars() if c.primary}
        horizon = now + timedelta(minutes=MAX_LEAD_MINUTES + 1)
        for ev in self.cache.events_between(now, horizon):
            if ev.all_day:
                continue
            if self.settings.is_hidden(ev.calendar_id, ev.calendar_id in primary):
                continue
            if self.settings.is_muted(ev.calendar_id):
                continue
            key = self._key(ev)
            if key in self.fired:
                continue
            if ev.start > now:
                lead = self.settings.lead_for(ev.calendar_id)
                if ev.start - now > timedelta(minutes=lead):
                    continue
                mins = max(1, math.ceil((ev.start - now).total_seconds() / 60))
                lead_text = f"Starts in {mins} min"
            elif now < ev.end:
                lead_text = f"Started {ev.start.astimezone():%H:%M}"
            else:
                continue  # already over: dropped silently
            body = f"{lead_text} · {ev.start.astimezone():%H:%M} · {ev.calendar_name}"
            if ev.start <= now:
                body = f"{lead_text} · {ev.calendar_name}"
            # Mark before sending: a failing toast must not repeat every 30 s.
            self.fired[key] = ev.start.timestamp()
            changed = True
            try:
                self.send_toast(ev.title, body, ev.meeting_url)
            except Exception:  # noqa: BLE001
                log.exception("toast for %r failed", ev.title)
        if changed:
            self._save()

    def _prune(self, now: datetime) -> bool:
        cutoff = (now - PRUNE_AFTER).timestamp()
        old = [k for k, ts in self.fired.items() if ts < cutoff]
        for k in old:
            del self.fired[k]
        return bool(old)
