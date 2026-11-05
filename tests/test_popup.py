import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PyQt6.QtCore import QEvent, QRect
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import QApplication, QFrame, QLabel

from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.settings import Settings
from smartcal.ui.popup import AgendaPopup

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=NY)
AREA = QRect(0, 0, 1000, 800)


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


def _plain(html):
    doc = QTextDocument()
    doc.setHtml(html)
    return doc.toPlainText()


def test_popup_lists_todays_events_from_cache(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Standup", 10), ev("2", "Lunch", 12, cal="work")])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    assert [r.title for r in popup.rows()] == ["Standup", "Lunch"]
    assert len(popup.row_widgets()) == 2
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


def test_popup_stays_open_while_a_modal_box_is_up(qtbot, tmp_path):
    from PyQt6.QtCore import QObject, pyqtSignal

    class FakeModal(QObject):          # stands in for the "Undo anyway" confirm box
        finished = pyqtSignal(int)

    cache, settings = make(tmp_path, [ev("1", "Standup", 10)])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    modal = FakeModal()
    popup.active_modal = lambda: modal
    activated = []
    popup.activateWindow = lambda: activated.append(1)
    popup.show()
    qtbot.waitExposed(popup)
    QApplication.sendEvent(popup, QEvent(QEvent.Type.WindowDeactivate))
    QApplication.sendEvent(popup, QEvent(QEvent.Type.WindowDeactivate))   # connected once
    assert popup.isVisible()
    popup.active_modal = lambda: None
    popup.active_window = lambda: popup    # the popup's window is still the active one
    popup.app_active = lambda: True
    modal.finished.emit(0)             # the box closed: one event-loop pass later...
    qtbot.waitUntil(lambda: activated == [1])   # ...the popup takes focus back
    assert popup.isVisible()
    QApplication.sendEvent(popup, QEvent(QEvent.Type.WindowDeactivate))
    assert not popup.isVisible()       # no modal any more: normal hide


def test_popup_show_under_200ms(qtbot, tmp_path):
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20, cal="primary" if i % 2 else "work")
              for i in range(30)]
    cache, settings = make(tmp_path, events)
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    t0 = time.perf_counter()
    popup.refresh(NOW)
    popup.show_near_tray(QRect(700, 570, 20, 20))
    qtbot.waitExposed(popup)
    QApplication.processEvents()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"popup refresh + show_near_tray to first paint: {elapsed:.1f} ms")
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


def test_row_shows_stripe_color_and_all_day_text_but_no_calendar_line(qtbot, tmp_path):
    allday = Event(id="ad", account="a", calendar_id="work", calendar_name="Work",
                   color="#0b8043", title="Holiday",
                   start=datetime(2026, 11, 4, tzinfo=NY), end=datetime(2026, 11, 5, tzinfo=NY),
                   all_day=True)
    cache, settings = make(tmp_path, [allday, ev("1", "Standup", 10, color="#ff0000")])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    first, second = popup.row_widgets()
    assert "#0b8043" in first.findChild(QFrame, "stripe").styleSheet()
    assert first.findChild(QLabel, "calname") is None    # v0.7.0: no calendar/email line
    assert _plain(first.findChild(QLabel, "title").text()).startswith("All day")
    assert "#ff0000" in second.findChild(QFrame, "stripe").styleSheet()
    assert _plain(second.findChild(QLabel, "title").text()).startswith("10:00 AM")


def test_title_with_angle_brackets_renders_verbatim(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Sync <Alice/Bob> & co", 10)])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    label = popup.row_widgets()[0].findChild(QLabel, "title")
    assert _plain(label.text()) == "10:00 AM Sync <Alice/Bob> & co"
    popup.set_account_status("<x>@y.com", "reauth")
    assert popup.status_text() == "Sign in again for <x>@y.com"


def test_error_status_note_shown_and_cleared_on_ok(qtbot, tmp_path):
    cache, settings = make(tmp_path, [])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    popup.show()
    qtbot.waitExposed(popup)
    h0 = popup.height()
    popup.set_account_status("a@x.com", "error")
    assert popup.status_text() == "Sync problem"
    assert popup._status.isVisible()
    assert popup.height() > h0
    popup.set_account_status("a@x.com", "ok")
    assert not popup._status.isVisible()
    assert popup.height() == h0


def test_prune_accounts_clears_stale_status(qtbot, tmp_path):
    cache, settings = make(tmp_path, [])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.set_account_status("gone@x.com", "reauth")
    popup.prune_accounts(["other@x.com"])
    assert popup.status_text() == ""


def test_no_accounts_message(qtbot, tmp_path):
    cache, settings = make(tmp_path, [])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.set_no_accounts(True)
    popup.refresh(NOW)
    assert popup.empty_text() == ("No calendars connected — right-click the tray icon "
                                  "> Add Google account…")
    popup.show()
    qtbot.waitExposed(popup)
    assert popup._empty.isVisible()


def test_show_near_tray_clamps_to_work_area_all_four_edges(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Standup", 10)])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup._area = lambda point=None: AREA
    popup.refresh(NOW)
    trays = {
        "bottom-left": QRect(0, 780, 20, 20),
        "bottom-right": QRect(980, 780, 20, 20),
        "top-left": QRect(0, 0, 20, 20),
        "top-right": QRect(980, 0, 20, 20),
        "left-mid": QRect(0, 400, 20, 20),
        "right-mid": QRect(980, 400, 20, 20),
    }
    for name, tray in trays.items():
        popup.show_near_tray(tray)
        assert AREA.contains(popup.geometry()), (name, popup.geometry())
        popup.hide()
    popup.show_near_tray(trays["bottom-right"])
    assert popup.geometry().bottom() < 780  # above the tray
    popup.hide()
    popup.show_near_tray(trays["top-left"])
    assert popup.geometry().top() > 20  # below the tray


def test_empty_tray_geometry_falls_back_to_bottom_right(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Standup", 10)])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup._area = lambda point=None: AREA
    popup.refresh(NOW)
    popup.show_near_tray(QRect())
    g = popup.geometry()
    assert AREA.contains(g)
    assert g.right() > AREA.right() - 30 and g.bottom() > AREA.bottom() - 30


def test_popup_height_capped_with_30_events_footer_visible(qtbot, tmp_path):
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20) for i in range(30)]
    cache, settings = make(tmp_path, events)
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup._area = lambda point=None: QRect(0, 0, 1000, 600)
    popup.refresh(NOW)
    popup.show_near_tray(QRect(980, 580, 20, 20))
    qtbot.waitExposed(popup)
    assert popup.height() <= min(480, 600 - 24)
    footer = popup._footer
    assert footer.isVisible()
    assert footer.geometry().bottom() <= popup.rect().bottom()
    assert popup._header.isVisible()
    assert " · " in _plain(footer.text())


def test_reanchors_when_height_changes_while_visible(qtbot, tmp_path):
    cache, settings = make(tmp_path, [ev("1", "Standup", 10)])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup._area = lambda point=None: AREA
    popup.refresh(NOW)
    popup.show_near_tray(QRect(980, 780, 20, 20))
    bottom = popup.geometry().bottom()
    popup.set_account_status("a@x.com", "error")
    assert popup.geometry().bottom() == bottom


def _popup_with(qtbot, tmp_path, url):
    e = ev("1", "Zoom call", 10)
    e.meeting_url = url
    cache, settings = make(tmp_path, [e])
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.refresh(NOW)
    popup.show_near_tray(QRect(700, 570, 20, 20))
    return popup


def _join_buttons(popup):
    from PyQt6.QtWidgets import QPushButton
    return [b for r in popup.row_widgets() for b in r.findChildren(QPushButton)]


def test_popup_row_join_button_opens_link_and_ignores_non_http(qtbot, tmp_path, monkeypatch):
    from smartcal.ui import popup as popup_mod
    opened = []

    class Stub:
        @staticmethod
        def openUrl(url):
            opened.append(url.toString())

    monkeypatch.setattr(popup_mod, "QDesktopServices", Stub)
    z = "https://us02web.zoom.us/j/123?pwd=abc"
    popup = _popup_with(qtbot, tmp_path, z)
    [b] = _join_buttons(popup)
    assert b.text() == "Join" and b.toolTip() == "Join Zoom meeting"
    assert popup.isVisible()
    b.click()
    assert opened == [z] and not popup.isVisible()
    # non-http(s): no button, and the handler refuses anyway
    popup2 = _popup_with(qtbot, tmp_path, "javascript:alert(1)")
    assert _join_buttons(popup2) == []
    popup2._join("file:///C:/Windows/notepad.exe")
    assert opened == [z] and popup2.isVisible()


def test_popup_row_without_link_has_no_join_button(qtbot, tmp_path):
    popup = _popup_with(qtbot, tmp_path, None)
    assert _join_buttons(popup) == []
