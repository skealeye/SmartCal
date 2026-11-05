"""User settings, persisted as JSON."""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import date
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

MAX_MEETING_ENTRIES = 500      # cap for hidden meetings and for meeting colors
MAX_MEETING_TITLE = 100
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
HIDE_SCOPES = ("event", "series")


def occurrence_key(event) -> str:
    """Settings key for one meeting (one occurrence of a series)."""
    return f"e:{event.calendar_id}:{event.id}"


def series_key(event) -> str | None:
    """Settings key for a whole recurring series; None for a one-off meeting."""
    rid = getattr(event, "recurring_id", None)
    return f"s:{event.calendar_id}:{rid}" if rid else None


def color_key(event) -> str:
    """Colors follow the series, so every occurrence of a recurring meeting matches."""
    return series_key(event) or occurrence_key(event)


def _valid_key(k) -> bool:
    return isinstance(k, str) and k.startswith(("e:", "s:")) and len(k) <= 2000


def _title(value) -> str:
    return (value if isinstance(value, str) else "")[:MAX_MEETING_TITLE]


def _capped(d: dict) -> dict:
    """Keep the newest MAX_MEETING_ENTRIES entries (dicts keep insertion order)."""
    if len(d) <= MAX_MEETING_ENTRIES:
        return d
    return dict(list(d.items())[-MAX_MEETING_ENTRIES:])


def _evict_hidden(hidden: dict, today: date, keep: str | None = None) -> dict:
    """Cap hidden meetings at MAX_MEETING_ENTRIES. Evicted first: one-off hides whose
    date has passed (oldest first), then other one-off hides (oldest first), and only
    then whole-series hides (oldest first): a hidden series keeps mattering. `keep`
    (the entry just added) is never evicted."""
    excess = len(hidden) - MAX_MEETING_ENTRIES
    if excess <= 0:
        return hidden
    today_iso = today.isoformat()
    order = [k for k in hidden if k != keep]              # insertion order = age
    expired = [k for k in order if hidden[k]["scope"] == "event"
               and hidden[k]["when"] < today_iso]
    expired.sort(key=lambda k: hidden[k]["when"])         # stable: ties keep age order
    current = [k for k in order if hidden[k]["scope"] == "event"
               and hidden[k]["when"] >= today_iso]
    series = [k for k in order if hidden[k]["scope"] != "event"]
    drop = set((expired + current + series)[:excess])
    return {k: v for k, v in hidden.items() if k not in drop}


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
        # SmartCal-local only (never sent to Google). Keys: occurrence_key / series_key.
        self.hidden_events: dict[str, dict] = {}
        self.event_colors: dict[str, str] = {}
        self.event_color_titles: dict[str, str] = {}   # for the Settings > Meetings list
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
        self.hidden_events = self._load_hidden(data.get("hidden_events"))
        self.event_colors = self._load_colors(data.get("event_colors"))
        titles = data.get("event_color_titles")
        self.event_color_titles = ({k: _title(v) for k, v in titles.items()
                                    if k in self.event_colors and isinstance(v, str)}
                                   if isinstance(titles, dict) else {})

    @staticmethod
    def _load_hidden(value) -> dict[str, dict]:
        if not isinstance(value, dict):
            return {}
        out = {}
        for k, v in value.items():
            if not _valid_key(k) or not isinstance(v, dict):
                continue
            scope, when, title = v.get("scope"), v.get("when"), v.get("title", "")
            if scope not in HIDE_SCOPES or not isinstance(title, str) \
                    or not isinstance(when, str):
                continue
            if (scope == "series") != k.startswith("s:"):
                continue
            try:
                date.fromisoformat(when)
            except ValueError:
                continue
            out[k] = {"title": _title(title), "scope": scope, "when": when}
        return _evict_hidden(out, date.today())

    @staticmethod
    def _load_colors(value) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return _capped({k: v.lower() for k, v in value.items()
                        if _valid_key(k) and isinstance(v, str) and _COLOR.fullmatch(v)})

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
                "claude_model": self.claude_model,
                "hidden_events": self.hidden_events,
                "event_colors": self.event_colors,
                "event_color_titles": {k: v for k, v in self.event_color_titles.items()
                                       if k in self.event_colors}}
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

    # -- hidden meetings (local to SmartCal) ---------------------------------
    # Every mutator saves at once. If the save fails (OSError) the in-memory change is
    # rolled back and the OSError re-raised, so the UI can say so and nothing half-applies.
    def _meeting_state(self) -> tuple:
        return (dict(self.hidden_events), dict(self.event_colors),
                dict(self.event_color_titles))

    def _save_or_rollback(self, before: tuple) -> None:
        try:
            self.save()
        except OSError:
            self.hidden_events, self.event_colors, self.event_color_titles = before
            log.warning("could not save %s; meeting change rolled back", self.path)
            raise

    def hide_event(self, event, scope: str = "event") -> str:
        """Hide one occurrence ("event") or a whole recurring series ("series") in
        SmartCal's agenda, windows and toasts. Returns the key used."""
        if scope not in HIDE_SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        key = series_key(event) if scope == "series" else occurrence_key(event)
        if key is None:
            raise ValueError("not a recurring meeting")
        try:
            start = event.start if event.all_day else event.start.astimezone()
            when = start.date().isoformat()
        except (AttributeError, ValueError):
            when = date.today().isoformat()
        before = self._meeting_state()
        hidden = dict(self.hidden_events)
        hidden.pop(key, None)                   # re-hiding moves it to the newest end
        hidden[key] = {"title": _title(getattr(event, "title", "")),
                       "scope": scope, "when": when}
        self.hidden_events = _evict_hidden(hidden, date.today(), keep=key)
        self._save_or_rollback(before)
        return key

    def unhide(self, key: str) -> None:
        if key not in self.hidden_events:
            return
        before = self._meeting_state()
        self.hidden_events = {k: v for k, v in self.hidden_events.items() if k != key}
        self._save_or_rollback(before)

    def is_event_hidden(self, event) -> bool:
        if not self.hidden_events:
            return False
        if occurrence_key(event) in self.hidden_events:
            return True
        skey = series_key(event)
        return skey is not None and skey in self.hidden_events

    # -- meeting colors (local to SmartCal) -----------------------------------
    def set_event_color(self, event, color: str) -> str:
        """Color a meeting; a recurring meeting's color applies to its whole series."""
        if not isinstance(color, str) or not _COLOR.fullmatch(color):
            raise ValueError(f"not a #rrggbb color: {color!r}")
        key = color_key(event)
        before = self._meeting_state()
        colors = dict(self.event_colors)
        colors.pop(key, None)
        colors[key] = color.lower()
        self.event_colors = _capped(colors)
        titles = dict(self.event_color_titles)
        titles[key] = _title(getattr(event, "title", ""))
        self.event_color_titles = {k: v for k, v in titles.items() if k in self.event_colors}
        self._save_or_rollback(before)
        return key

    def clear_event_color(self, event) -> None:
        self.clear_color_key(color_key(event))

    def clear_color_key(self, key: str) -> None:
        if key not in self.event_colors:
            return
        before = self._meeting_state()
        self.event_colors = {k: v for k, v in self.event_colors.items() if k != key}
        self.event_color_titles = {k: v for k, v in self.event_color_titles.items()
                                   if k != key}
        self._save_or_rollback(before)

    def event_color(self, event) -> str | None:
        return self.event_colors.get(color_key(event)) if self.event_colors else None
