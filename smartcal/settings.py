"""User settings, persisted as JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import data_dir


class Settings:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else data_dir() / "settings.json"
        self.hidden_calendars: set[str] = set()
        self.shown_calendars: set[str] = set()
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self.hidden_calendars = self._str_set(data.get("hidden_calendars"))
        self.shown_calendars = self._str_set(data.get("shown_calendars"))

    @staticmethod
    def _str_set(value) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {v for v in value if isinstance(v, str)}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"hidden_calendars": sorted(self.hidden_calendars),
                "shown_calendars": sorted(self.shown_calendars)}
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
        if hidden:
            self.hidden_calendars.add(calendar_id)
            self.shown_calendars.discard(calendar_id)
        else:
            self.hidden_calendars.discard(calendar_id)
            self.shown_calendars.add(calendar_id)
        self.save()
