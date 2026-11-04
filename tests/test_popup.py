import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.settings import Settings
from smartcal.ui.popup import AgendaPopup

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=NY)


def ev(id, title, h, m=0, cal="primary", color="#4285f4"):
    start = datetime(2026, 11, 4, h, m, tzinfo=NY)
    return Event(id=id, account="a", calendar_id=cal, calendar_name=cal.title(), color=color,
                 title=title, start=start, end=start + timedelta(minutes=30))


def make(tmp_path, events):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True),
                                 CalendarInfo("work", "Work", "#0b8043", True)])
    for e in events:
        cache.upsert_events("a", [e])
    return cache, Settings(tmp_path / "s.json")


def test_popup_lists_todays_events_from_cache(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Standup", 10), ev("2", "Lunch", 12, cal="work")])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    assert [r.title for r in popup.rows()] == ["Standup", "Lunch"]
    assert "Nov" in popup.header_text()


def test_popup_empty_state(qtbot, tmp_path):
    cache, settings = make(tmp_path, [])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    assert popup.rows() == []
    assert "Nothing scheduled today" in popup.empty_text()


def test_popup_highlights_next_event(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Early", 8), ev("2", "Standup", 9, 12),
                                      ev("3", "Later", 14)])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    assert [r.title for r in popup.rows() if r.is_next] == ["Standup"]
    assert popup.next_text() == "Next: Standup in 12 min"


def test_popup_hides_on_focus_loss(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Standup", 10)])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.show()
    qtbot.waitExposed(popup)
    assert popup.isVisible()
    QApplication.sendEvent(popup, QEvent(QEvent.Type.WindowDeactivate))
    assert not popup.isVisible()


def test_popup_show_under_200ms(qtbot, tmp_path):
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20, cal="primary" if i % 2 else "work")
              for i in range(30)]
    cache, settings = make(tmp_path, events)
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    t0 = time.perf_counter()
    popup.show()
    qtbot.waitExposed(popup)
    QApplication.processEvents()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"popup show to first paint: {elapsed:.1f} ms")
    assert elapsed < 200


def test_popup_status_note(qtbot, tmp_path):
    cache, settings = make(tmp_path, [])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.set_account_status("a@x.com", "offline")
    assert "Offline" in popup.status_text()
    popup.set_account_status("a@x.com", "reauth")
    assert "Sign in again for a@x.com" in popup.status_text()
    popup.set_account_status("a@x.com", "ok")
    assert popup.status_text() == ""


def test_popup_view_signal(qtbot, tmp_path):
    cache, settings = make(tmp_path, [])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    with qtbot.waitSignal(popup.view_requested) as sig:
        popup.request_view("week")
    assert sig.args == ["week"]
