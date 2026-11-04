"""User settings, persisted as JSON."""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path

from .config import data_dir


DEFAULT_LEAD_MINUTES = 10
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"


def clamp_lead(value, default: int = DEFAULT_LEAD_MINUTES) -> int:
    """Lead time in minutes, clamped to 0-1440; bad values fall back to `default`."""
    if isinstance(value, bool) or not isinstance(value, (int, float))             or not math.isfinite(value):
        return default
    return max(0, min(1440, int(value)))


log = logging.getLogger(__name__)


class Settings:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else data_dir() / "settings.json"
        self.hidden_calendars: set[str] = set()
        self.shown_calendars: set[str] = set()
        self.lead_minutes: dict[str, int] = {}
        self.default_lead_minutes: int = DEFAULT_LEAD_MINUTES
        self.muted: set[str] = set()
        self.autostart: bool = True
        self.first_run_done: bool = False
        self.claude_model: str = DEFAULT_CLAUDE_MODEL
        self.load_failed = False  # file exists but is unreadable/corrupt
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self._mark_corrupt()
            return
        if not isinstance(data, dict):
            self._mark_corrupt()
            return
        self.hidden_calendars = self._str_set(data.get("hidden_calendars"))
        self.shown_calendars = self._str_set(data.get("shown_calendars"))
        self.default_lead_minutes = clamp_lead(data.get("default_lead_minutes"))
        leads = data.get("lead_minutes")
        self.lead_minutes = ({k: clamp_lead(v, self.default_lead_minutes)
                              for k, v in leads.items() if isinstance(k, str)}
                             if isinstance(leads, dict) else {})
        self.muted = self._str_set(data.get("muted"))
        if isinstance(data.get("autostart"), bool):
            self.autostart = data["autostart"]
        self.first_run_done = data.get("first_run_done") is True
        model = data.get("claude_model")
        if isinstance(model, str) and model.strip():
            self.claude_model = model.strip()

    def _mark_corrupt(self) -> None:
        """Unreadable settings must never look like a first run (which enables autostart)."""
        log.warning("could not read %s; using defaults", self.path)
        self.load_failed = True
        self.first_run_done = True
        self.autostart = False

    @staticmethod
    def _str_set(value) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {v for v in value if isinstance(v, str)}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"hidden_calendars": sorted(self.hidden_calendars),
                "shown_calendars": sorted(self.shown_calendars),
                "lead_minutes": dict(sorted(self.lead_minutes.items())),
                "default_lead_minutes": self.default_lead_minutes,
                "muted": sorted(self.muted),
                "autostart": self.autostart,
                "first_run_done": self.first_run_done,
                "claude_model": self.claude_model}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def is_hidden(self, calendar_id: str, primary: bool) -> bool:
        """Explicit hide wins, then explicit show; otherwise only primary calendars show."""
        if calendar_id in self.hidden_calendars:
            return True
        if calendar_id in self.shown_calendars:
            return False
        return not primary

    def set_hidden(self, calendar_id: str, hidden: bool) -> None:
        self.set_hidden_nosave(calendar_id, hidden)
        self.save()

    def set_hidden_nosave(self, calendar_id: str, hidden: bool) -> None:
        if hidden:
            self.hidden_calendars.add(calendar_id)
            self.shown_calendars.discard(calendar_id)
        else:
            self.hidden_calendars.discard(calendar_id)
            self.shown_calendars.add(calendar_id)

    def lead_for(self, calendar_id: str) -> int:
        return clamp_lead(self.lead_minutes.get(calendar_id, self.default_lead_minutes),
                          self.default_lead_minutes)

    def set_lead(self, calendar_id: str, minutes: int) -> None:
        self.lead_minutes[calendar_id] = clamp_lead(minutes, self.default_lead_minutes)
        self.save()

    def set_default_lead(self, minutes: int) -> None:
        self.default_lead_minutes = clamp_lead(minutes)
        self.save()

    def is_muted(self, calendar_id: str) -> bool:
        return calendar_id in self.muted

    def set_muted(self, calendar_id: str, muted: bool) -> None:
        (self.muted.add if muted else self.muted.discard)(calendar_id)
        self.save()

    def set_autostart(self, enabled: bool) -> None:
        self.autostart = bool(enabled)
        self.save()

    def set_claude_model(self, model: str) -> None:
        self.claude_model = model.strip() or DEFAULT_CLAUDE_MODEL
        self.save()
