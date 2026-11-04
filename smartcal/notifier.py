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
CATCH_UP_WINDOW = timedelta(hours=24)


class Notifier:
    def __init__(self, cache, settings, send_toast, clock=datetime.now, fired_path=None):
        self.cache, self.settings, self.send_toast = cache, settings, send_toast
        self.clock = clock
        self.fired_path = Path(fired_path) if fired_path else data_dir() / "notified.json"
        # key -> (event end epoch, fired-at epoch)
        self.fired: dict[str, tuple[float, float]] = self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> dict[str, tuple[float, float]]:
        try:
            data = json.loads(self.fired_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}

        def num(v):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)                 and math.isfinite(v) else None

        out = {}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            if isinstance(v, list) and len(v) == 2 and num(v[0]) is not None                     and num(v[1]) is not None:
                out[k] = (float(v[0]), float(v[1]))
            elif num(v) is not None:  # older format: a single timestamp
                out[k] = (float(v), float(v))
        return out

    def _save(self) -> None:
        try:
            self.fired_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.fired_path.with_name(self.fired_path.name + ".tmp")
            tmp.write_text(json.dumps({k: list(v) for k, v in self.fired.items()}),
                           encoding="utf-8")
            os.replace(tmp, self.fired_path)
        except OSError:
            log.exception("could not save %s", self.fired_path)

    @staticmethod
    def _key(ev) -> str:
        # UTC epoch, so a change of the display time zone does not look like a new event.
        return f"{ev.account}|{ev.calendar_id}|{ev.id}|{int(ev.start.timestamp())}"

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
                body = f"{lead_text} · {ev.start.astimezone():%H:%M} · {ev.calendar_name}"
            elif now < ev.end and now - ev.start <= CATCH_UP_WINDOW:
                body = f"Started {ev.start.astimezone():%H:%M} · {ev.calendar_name}"
            else:
                continue  # over, or began too long ago to be news: dropped silently
            # Mark before sending: a failing toast must not repeat every 30 s.
            self.fired[key] = (ev.end.timestamp(), now.timestamp())
            changed = True
            try:
                self.send_toast(ev.title, body, ev.meeting_url)
            except Exception:  # noqa: BLE001
                log.exception("toast for event %s (calendar %s) failed", ev.id, ev.calendar_id)
        if changed:
            self._save()

    def _prune(self, now: datetime) -> bool:
        """Drop entries once the event has ended and 2 days have passed since firing."""
        t = now.timestamp()
        span = PRUNE_AFTER.total_seconds()
        old = [k for k, (end, fired) in self.fired.items() if t > max(end, fired + span)]
        for k in old:
            del self.fired[k]
        return bool(old)
