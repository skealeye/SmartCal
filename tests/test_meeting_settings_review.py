import time
from datetime import date, timedelta

import pytest

from smartcal.settings import MAX_MEETING_ENTRIES, Settings
from smartcal.textutil import linkify_to_html
from test_meeting_settings import SERIES, ev


def test_trim_url_is_linear_on_many_closing_parens():
    plain = "https://example.com/a" + ")" * 20_000
    t0 = time.perf_counter()
    html = linkify_to_html(plain)
    assert (time.perf_counter() - t0) * 1000 < 50
    assert 'href="https://example.com/a"' in html
    plain = "https://example.com/" + "]" * 20_000
    t0 = time.perf_counter()
    linkify_to_html(plain)
    assert (time.perf_counter() - t0) * 1000 < 50


def test_old_series_hide_survives_many_one_off_hides(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.save = lambda: None
    s.hide_event(SERIES[0], "series")             # oldest entry of all
    for i in range(MAX_MEETING_ENTRIES * 2):
        s.hide_event(ev(f"e{i}", f"M{i}", 10), "event")
    assert len(s.hidden_events) == MAX_MEETING_ENTRIES
    assert "s:primary:s1" in s.hidden_events


def test_expired_one_offs_are_evicted_before_current_ones(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.save = lambda: None
    today = date.today()
    s.hidden_events = {f"e:c:future{i}": {"title": "f", "scope": "event",
                                          "when": (today + timedelta(days=1)).isoformat()}
                       for i in range(MAX_MEETING_ENTRIES - 1)}
    s.hidden_events["e:c:past"] = {"title": "p", "scope": "event",
                                   "when": (today - timedelta(days=30)).isoformat()}
    s.hide_event(ev("new", "New", 10), "event")   # full: the expired one goes first
    assert "e:c:past" not in s.hidden_events
    assert "e:primary:new" in s.hidden_events and "e:c:future0" in s.hidden_events


def _fail():
    raise PermissionError("read-only")


def test_failed_save_rolls_back_every_meeting_change(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.hide_event(SERIES[0], "event")
    s.set_event_color(SERIES[0], "#3fa364")
    before = (dict(s.hidden_events), dict(s.event_colors), dict(s.event_color_titles))
    s.save = _fail
    for change in (lambda: s.hide_event(SERIES[1], "series"),
                   lambda: s.unhide("e:primary:s1_1104"),
                   lambda: s.set_event_color(SERIES[1], "#e8594f"),
                   lambda: s.clear_event_color(SERIES[1])):
        with pytest.raises(OSError):
            change()
        assert (s.hidden_events, s.event_colors, s.event_color_titles) == before
