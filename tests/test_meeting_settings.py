"""Hidden meetings and meeting colors: Settings, agenda, notifier, theme (no widgets)."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PyQt6.QtGui import QColor

from fakes import FakeClient
from smartcal import agenda
from smartcal.actions import Actions
from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.notifier import Notifier
from smartcal.settings import MAX_MEETING_ENTRIES, Settings
from smartcal.ui import theme
from smartcal.undo import UndoStore

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=NY)


def ev(id, title, h, m=0, day=4, cal="primary", rid=None, color="#4285f4"):
    start = datetime(2026, 11, day, h, m, tzinfo=NY)
    return Event(id=id, account="a", calendar_id=cal, calendar_name=cal.title(), color=color,
                 title=title, start=start, end=start + timedelta(minutes=30), recurring_id=rid)


def make(tmp_path, events):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True)])
    cache.upsert_events("a", events)
    return cache, Settings(tmp_path / "s.json")


SERIES = [ev("s1_1104", "Standup", 10, rid="s1"), ev("s1_1105", "Standup", 10, day=5, rid="s1")]


def test_hide_this_occurrence_only(tmp_path):
    other = ev("x", "Lunch", 12)
    cache, s = make(tmp_path, SERIES + [other])
    key = s.hide_event(SERIES[0], "event")
    assert key == "e:primary:s1_1104"
    assert s.is_event_hidden(SERIES[0])
    assert not s.is_event_hidden(SERIES[1]) and not s.is_event_hidden(other)
    assert [e.title for e in agenda.today(cache, s, NOW)] == ["Lunch"]
    tomorrow = NOW + timedelta(days=1)
    assert [e.id for e in agenda.today(cache, s, tomorrow)] == ["s1_1105"]
    # "when" is the meeting's own local date, whatever the machine's time zone.
    local_day = SERIES[0].start.astimezone().date().isoformat()
    assert s.hidden_events[key] == {"title": "Standup", "scope": "event", "when": local_day}


def test_hide_series_hides_all_occurrences(tmp_path):
    cache, s = make(tmp_path, SERIES + [ev("x", "Lunch", 12)])
    key = s.hide_event(SERIES[1], "series")
    assert key == "s:primary:s1"
    assert all(s.is_event_hidden(e) for e in SERIES)
    assert [e.title for e in agenda.today(cache, s, NOW)] == ["Lunch"]
    assert agenda.today(cache, s, NOW + timedelta(days=1)) == []
    assert s.hidden_events[key]["scope"] == "series"
    with pytest.raises(ValueError):
        s.hide_event(ev("x", "Lunch", 12), "series")   # not recurring
    with pytest.raises(ValueError):
        s.hide_event(SERIES[0], "everything")
    s.unhide(key)
    assert not s.is_event_hidden(SERIES[0])
    assert Settings(tmp_path / "s.json").hidden_events == {}


def test_hidden_meeting_not_in_next_or_toasts(tmp_path):
    a, b = ev("a", "Hidden one", 9, 5), ev("b", "Shown one", 9, 8)
    cache, s = make(tmp_path, [a, b])
    s.hide_event(a, "event")
    assert agenda.next_event(cache, s, NOW).id == "b"
    sent = []
    n = Notifier(cache, s, lambda *args: sent.append(args), clock=lambda: NOW,
                 fired_path=tmp_path / "n.json")
    n.check(NOW)
    assert [t for t, *_ in sent] == ["Shown one"]


def test_hidden_meeting_still_busy_for_free_slots_and_visible_to_assistant(tmp_path):
    from smartcal.assistant import Assistant
    meeting = ev("m", "Board review", 10, day=6)
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True)])
    cache.upsert_events("a", [meeting])
    s = Settings(tmp_path / "s.json")
    s.hide_event(meeting, "event")
    acts = Actions(lambda a: FakeClient(a), cache, UndoStore(tmp_path / "u.json"), s)
    slots = acts.free_slots(date(2026, 11, 6), timedelta(minutes=30), now=NOW)
    t10 = datetime(2026, 11, 6, 10, tzinfo=NY)
    assert all(not (a <= t10 < b) for a, b in slots)     # 10:00 is still busy
    assert [e.id for e in acts.find("board")] == ["m"]
    # The assistant's own event view (its context and tools) still includes it.
    assistant = Assistant.__new__(Assistant)
    assistant.cache, assistant.settings = cache, s
    assert [e.id for e in assistant._visible(cache.events_between(
        t10 - timedelta(hours=1), t10 + timedelta(hours=2)))] == ["m"]
    # Default visible_events (used by actions and the assistant) is unchanged.
    assert [e.id for e in agenda.visible_events(cache, s, [meeting])] == ["m"]
    assert agenda.visible_events(cache, s, [meeting], hide_user_hidden=True) == []


def test_hidden_events_persist_and_wrong_types_ignored(tmp_path):
    path = tmp_path / "s.json"
    good = {"title": "Standup", "scope": "series", "when": "2026-11-04"}
    path.write_text(json.dumps({
        "hidden_events": {
            "s:primary:s1": good,
            "e:primary:x": {"title": "T" * 500, "scope": "event", "when": "2026-11-04"},
            "e:primary:bad-scope": {"title": "x", "scope": "all", "when": "2026-11-04"},
            "e:primary:series-scope-on-event-key": {"title": "x", "scope": "series",
                                                     "when": "2026-11-04"},
            "e:primary:bad-date": {"title": "x", "scope": "event", "when": "yesterday"},
            "e:primary:bad-title": {"title": 5, "scope": "event", "when": "2026-11-04"},
            "e:primary:not-a-dict": ["x"],
            "nokey": good,
        },
        "event_colors": {"s:primary:s1": "#AABBCC", "e:primary:y": "red",
                         "e:primary:z": "#12345", "e:primary:w": 5, "bad": "#123456"},
    }), encoding="utf-8")
    s = Settings(path)
    assert set(s.hidden_events) == {"s:primary:s1", "e:primary:x"}
    assert len(s.hidden_events["e:primary:x"]["title"]) == 100
    assert s.event_colors == {"s:primary:s1": "#aabbcc"}
    for wrong in ([], "x", 5, None):
        path.write_text(json.dumps({"hidden_events": wrong, "event_colors": wrong}),
                        encoding="utf-8")
        s = Settings(path)
        assert s.hidden_events == {} and s.event_colors == {} and not s.load_failed
    # round trip
    s.hide_event(SERIES[0], "series")
    s.set_event_color(SERIES[0], "#3fa364")
    s2 = Settings(path)
    assert s2.is_event_hidden(SERIES[1]) and s2.event_color(SERIES[1]) == "#3fa364"
    assert s2.event_color_titles == {"s:primary:s1": "Standup"}


def test_old_settings_file_loads_with_empty_meeting_keys(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"hidden_calendars": ["w"], "autostart": False}), encoding="utf-8")
    s = Settings(path)
    assert s.hidden_events == {} and s.event_colors == {} and s.hidden_calendars == {"w"}


def test_meeting_entries_are_capped(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.save = lambda: None
    for i in range(MAX_MEETING_ENTRIES + 20):
        e = ev(f"e{i}", f"M{i}", 10)
        s.hide_event(e, "event")
        s.set_event_color(e, "#e8594f")
    assert len(s.hidden_events) == MAX_MEETING_ENTRIES
    assert len(s.event_colors) == MAX_MEETING_ENTRIES
    assert "e:primary:e0" not in s.hidden_events            # oldest dropped
    assert f"e:primary:e{MAX_MEETING_ENTRIES + 19}" in s.event_colors
    big = {f"e:c:{i}": {"title": "t", "scope": "event", "when": "2026-11-04"}
           for i in range(MAX_MEETING_ENTRIES + 5)}
    (tmp_path / "b.json").write_text(json.dumps({"hidden_events": big}), encoding="utf-8")
    assert len(Settings(tmp_path / "b.json").hidden_events) == MAX_MEETING_ENTRIES


def test_color_applies_to_series_for_recurring_events(tmp_path):
    _, s = make(tmp_path, SERIES)
    key = s.set_event_color(SERIES[0], "#E8594F")
    assert key == "s:primary:s1"
    assert s.event_color(SERIES[1]) == "#e8594f"
    assert theme.effective_color(SERIES[1], s) == "#e8594f"


def test_color_applies_to_single_event_only(tmp_path):
    a, b = ev("a", "One", 10), ev("b", "Two", 11)
    _, s = make(tmp_path, [a, b])
    assert s.set_event_color(a, "#5fbf8c") == "e:primary:a"
    assert s.event_color(a) == "#5fbf8c" and s.event_color(b) is None
    assert theme.effective_color(b, s) == "#4285f4"


def test_invalid_color_value_ignored(tmp_path):
    _, s = make(tmp_path, [])
    a = ev("a", "One", 10)
    for bad in ("red", "#fff", "#12345g", "javascript:x", "#123456;x", None, 5):
        with pytest.raises(ValueError):
            s.set_event_color(a, bad)
    assert s.event_colors == {}
    s.event_colors["e:primary:a"] = "#123456;background:url(x)"    # hand-edited in memory
    assert theme.effective_color(a, s) == "#4285f4"                 # never reaches CSS


def test_reset_color(tmp_path):
    _, s = make(tmp_path, SERIES)
    s.set_event_color(SERIES[0], "#b36ad6")
    s.clear_event_color(SERIES[1])          # any occurrence resets the series
    assert s.event_color(SERIES[0]) is None
    assert theme.effective_color(SERIES[0], s) == "#4285f4"
    assert Settings(tmp_path / "s.json").event_colors == {}
    assert theme.effective_color(SERIES[0], None) == "#4285f4"


def _luminance(hex_color):
    c = QColor(hex_color)

    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def test_palette_contrast_against_popup_backgrounds():
    assert len(theme.PALETTE) == 10
    for name, color in theme.PALETTE:
        for bg in (theme.BG, theme.BG_NEXT):
            hi, lo = sorted((_luminance(color), _luminance(bg)), reverse=True)
            assert (hi + 0.05) / (lo + 0.05) >= 3.0, (name, bg)
