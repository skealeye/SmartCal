"""Popup details flyout, linkified descriptions, Hide…/Color… in the popup, the window
and Settings > Meetings. Offscreen Qt, real widgets, real Settings and Cache."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PyQt6.QtCore import QEvent, QRect, QSettings, Qt, QUrl
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QTextBrowser

from smartcal import agenda
from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.notifier import Notifier
from smartcal.settings import Settings
from smartcal.ui import linktext, meeting_menus, theme
from smartcal.ui import popup as popup_mod
from smartcal.ui.popup import AgendaPopup, AgendaRow
from smartcal.ui.settings_dialog import SettingsDialog
from smartcal.ui.window import CalendarWindow, EventDetails

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=NY)
DESC = ("Agenda: <b>not bold</b> & more\nNotes at https://docs.example.com/a?x=1&y=2.\n"
        "Bad: javascript:alert(1) file:///C:/x www.example.com")


def ev(id, title, h, m=0, day=4, rid=None, **kw):
    start = datetime(2026, 11, day, h, m, tzinfo=NY)
    return Event(id=id, account="a", calendar_id="primary", calendar_name="Primary",
                 color="#4285f4", title=title, start=start,
                 end=start + timedelta(minutes=30), recurring_id=rid, **kw)


def store(tmp_path, events):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True)])
    cache.upsert_events("a", events)
    return cache, Settings(tmp_path / "s.json")


def settle_qt():
    """Deliver everything Qt still has queued: deferred deletes of earlier tests' windows
    and pending activation changes. Tests that fake the focus state (active_window, ...)
    must not have stale real WindowActivate/WindowDeactivate events arrive later and be
    judged against the fake."""
    for _ in range(3):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QApplication.processEvents()


def wait_active(qtbot, window):
    """Wait until the platform has made `window` the active one, then drain the queue.
    (Offscreen activates a window when it is shown; the flyout's show included.)"""
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window)
    settle_qt()


def open_details(qtbot, p, row):
    """Select `row` with the mouse and let the real activation finish before a test
    starts faking focus state."""
    click(qtbot, row)
    wait_active(qtbot, p.flyout())
    return p.flyout()


def make_popup(qtbot, tmp_path, events, show=True):
    settle_qt()                       # nothing left over from the previous test
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    if show:
        p.show_near_tray(QRect(700, 570, 20, 20))
        qtbot.waitExposed(p)
        wait_active(qtbot, p)
    return p


def make_window(qtbot, tmp_path, events, cache=None, settings=None):
    if cache is None:
        cache, settings = store(tmp_path, events)
    w = CalendarWindow(cache, settings,
                       QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat))
    w.now = lambda: NOW
    qtbot.addWidget(w)
    w.go_to(date(2026, 11, 4))
    w.set_view("week")
    w.resize(1000, 700)
    w.show()
    return w


def rows(p) -> list[AgendaRow]:
    return [r for r in p.row_widgets() if isinstance(r, AgendaRow)]


def titles(p):
    return [r.title for r in p.rows()]


def click(qtbot, widget):
    assert widget is not None            # QTest would crash on None
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


def right_click(widget, pos=None):
    """A right-click as Windows delivers it: the context-menu event (the menu opens)."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QContextMenuEvent
    assert widget is not None
    pos = pos or QPoint(5, 5)
    QApplication.sendEvent(widget, QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, pos, widget.mapToGlobal(pos)))
    QApplication.processEvents()


def row_menu(p, row):
    """Right-click `row` in popup `p`; returns the open menu (v0.8.0: Hide and Color live
    only in this menu, no longer in the details panel)."""
    right_click(row)
    return p.menu()


class Opened:
    def __init__(self):
        self.urls = []

    def openUrl(self, url):
        self.urls.append(url.toString())


@pytest.fixture
def opened(monkeypatch):
    o = Opened()
    monkeypatch.setattr(linktext, "QDesktopServices", o)
    monkeypatch.setattr(popup_mod, "QDesktopServices", o)
    return o


# -- details flyout (was: inline expansion) ------------------------------------------
def details(p):
    """The flyout's content for the selected meeting."""
    return p.flyout().body()


def test_popup_row_click_opens_and_closes_flyout(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "Standup", 10, location="Room 4", description=DESC)])
    [row] = rows(p)
    assert p.flyout() is None                        # built lazily, on first selection
    h0 = p.height()
    click(qtbot, row.findChild(QLabel, "title"))     # a click on the title reaches the row
    assert row.is_selected() and p.selected_row() is row and p.flyout_open()
    assert p.height() == h0                          # the list does not grow
    d = details(p)
    assert d.findChild(QLabel, "when").text() == "Wed, Nov 4, 2026 · 10:00 AM – 10:30 AM"
    assert d.findChild(QLabel, "location").text() == "Room 4"
    assert "Primary" in d.findChild(QLabel, "calendar").text()
    # v0.8.0: no Hide…/Color… in the details (right-click the meeting instead).
    assert d.findChild(QPushButton, "hide") is None
    assert d.findChild(QPushButton, "color") is None
    click(qtbot, row.findChild(QFrame, "stripe"))
    assert not row.is_selected() and p.selected_row() is None and not p.flyout_open()
    assert p.height() == h0


def test_only_one_row_selected(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11), ev("3", "C", 12)])
    a, b, c = rows(p)
    click(qtbot, a)
    click(qtbot, b)
    assert [r.is_selected() for r in rows(p)] == [False, True, False]
    click(qtbot, c)
    assert [r.is_selected() for r in rows(p)] == [False, False, True]
    assert p.flyout().event_shown.id == "3"


def test_row_join_button_does_not_toggle(qtbot, tmp_path, opened):
    z = "https://us02web.zoom.us/j/1"
    p = make_popup(qtbot, tmp_path, [ev("1", "Zoom", 10, meeting_url=z)])
    [row] = rows(p)
    join = row.findChild(QPushButton, "join")
    click(qtbot, join)
    assert opened.urls == [z]
    assert not row.is_selected() and p.flyout() is None


def test_enter_toggles_flyout(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    a, b = rows(p)
    assert a.focusPolicy() & Qt.FocusPolicy.TabFocus
    qtbot.keyClick(a, Qt.Key.Key_Return)
    assert a.is_selected() and p.flyout_open()
    qtbot.keyClick(a, Qt.Key.Key_Space)
    assert not a.is_selected() and not p.flyout_open()
    qtbot.keyClick(b, Qt.Key.Key_Enter)
    assert b.is_selected() and p.flyout_open()
    qtbot.keyClick(b, Qt.Key.Key_Escape)            # Esc closes the flyout first...
    assert not b.is_selected() and not p.flyout_open() and p.isVisible()
    qtbot.keyClick(p, Qt.Key.Key_Escape)            # ...then closes the popup
    assert not p.isVisible()


def test_flyout_shows_linkified_description(qtbot, tmp_path, opened):
    z = "https://meet.google.com/abc-defg-hij"
    p = make_popup(qtbot, tmp_path, [ev("1", "Sync", 10, description=DESC, meeting_url=z)])
    [row] = rows(p)
    click(qtbot, row)
    box = details(p).findChild(QTextBrowser, "description")
    assert box.isReadOnly() and not box.openLinks() and not box.openExternalLinks()
    assert box.toPlainText() == ("Agenda: not bold & more\n"
                                 "Notes at https://docs.example.com/a?x=1&y=2.\n"
                                 "Bad: javascript:alert(1) file:///C:/x www.example.com")
    assert box.anchors() == ["https://docs.example.com/a?x=1&y=2"]
    big = details(p).findChild(QPushButton, "bigjoin")
    assert big.text() == "Join Google Meet"
    # Only http(s) opens, whatever the anchor says.
    for bad in ("javascript:alert(1)", "file:///C:/Windows/notepad.exe", "mailto:a@b.c"):
        box.anchorClicked.emit(QUrl(bad))
    assert opened.urls == [] and p.isVisible()
    box.anchorClicked.emit(QUrl("https://docs.example.com/a?x=1&y=2"))
    assert opened.urls == ["https://docs.example.com/a?x=1&y=2"]
    assert not p.isVisible() and not p.flyout().isVisible()   # like Join: closes both


def test_long_description_stays_inside_the_panel_and_scrolls(qtbot, tmp_path):
    # v0.6.2: no 10-line cap; the notes fill the panel (as tall as the popup) and scroll.
    long = "\n".join(f"line {i}" for i in range(60))
    p = make_popup(qtbot, tmp_path, [ev("1", "Sync", 10, description=long)])
    click(qtbot, rows(p)[0])
    QApplication.processEvents()
    box = details(p).findChild(QTextBrowser, "description")
    assert box.height() < p.flyout().height()
    assert box.verticalScrollBar().maximum() > 0      # the rest scrolls


def test_refresh_keeps_the_flyout_open(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    click(qtbot, rows(p)[1])
    fly = p.flyout()
    p.refresh()
    assert [r.is_selected() for r in rows(p)] == [False, True]
    assert p.flyout() is fly and fly.isVisible() and fly.event_shown.id == "2"
    p.hide()
    p.refresh()
    assert p.selected_row() is None and not fly.isVisible()   # reopens with nothing open


def test_popup_height_cap_with_flyout_open(qtbot, tmp_path):
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20) for i in range(30)]
    events[10].description = "\n".join(f"line {i}" for i in range(200))
    events[10].location = "Room " * 40
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p._area = lambda point=None: QRect(0, 0, 1000, 600)
    p.refresh()
    p.show_near_tray(QRect(980, 580, 20, 20))
    qtbot.waitExposed(p)
    h0 = p.height()
    click(qtbot, rows(p)[10])
    assert rows(p)[10].is_selected() and p.flyout_open()
    assert p.height() == h0 <= min(480, 600 - 24)
    assert QRect(0, 0, 1000, 600).contains(p.geometry())
    assert QRect(0, 0, 1000, 600).contains(p.flyout().geometry())
    assert p._footer.isVisible() and p._footer.geometry().bottom() <= p.rect().bottom()
    assert p._header.isVisible()


def test_opening_flyout_is_fast(qtbot, tmp_path):
    import time
    p = make_popup(qtbot, tmp_path, [ev("1", "Sync", 10, description=DESC * 50)])
    t0 = time.perf_counter()
    click(qtbot, rows(p)[0])
    QApplication.processEvents()
    assert (time.perf_counter() - t0) * 1000 < 200


# -- menus keep the popup open ------------------------------------------------------
def _open_color_menu(qtbot, p):
    """With the details open, the meeting's right-click menu (Color ▸ inside)."""
    open_details(qtbot, p, rows(p)[0])
    menu = row_menu(p, rows(p)[0])
    QApplication.processEvents()      # deliver what the platform queued for the open menu
    return menu


def test_menu_does_not_hide_popup(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    activated = []
    p.activateWindow = lambda: activated.append(1)
    menu = _open_color_menu(qtbot, p)
    assert menu is not None
    p.active_popup = lambda: menu
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert p.isVisible()
    # The menu closes (Esc or a choice) while the popup's window is still the active
    # one: it stays open and takes focus back, one event-loop pass later.
    p.active_popup = lambda: None
    p.active_window = lambda: p
    p.app_active = lambda: True
    menu.hide()
    qtbot.waitUntil(lambda: activated == [1])
    assert p.isVisible()
    p.active_window = lambda: None                    # now another app takes focus
    p.app_active = lambda: False
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    qtbot.waitUntil(lambda: not p.isVisible())        # no menu any more: normal hide
    assert not p.flyout().isVisible()


# -- hide ---------------------------------------------------------------------------
SERIES = [ev("s1_1104", "Standup", 10, rid="s1"), ev("s1_1105", "Standup", 10, day=5, rid="s1")]


def _menu_action(p_or_d, text):
    act = meeting_menus.find_action(p_or_d.menu(), text)
    assert act is not None, text
    return act


def test_hide_this_occurrence_only_from_popup(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, SERIES + [ev("x", "Lunch", 12)])
    changed = []
    p.meetings_changed.connect(lambda: changed.append(1))
    click(qtbot, rows(p)[0])
    menu = row_menu(p, rows(p)[0])
    assert [a.text() for a in menu.actions()][:2] == [meeting_menus.HIDE_ONE,
                                                      meeting_menus.HIDE_SERIES]
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    assert titles(p) == ["Lunch"] and changed == [1]
    assert p.notice_text() == "Hidden. Restore from the tray icon."
    assert p.settings.is_event_hidden(SERIES[0]) and not p.settings.is_event_hidden(SERIES[1])
    p.now = lambda: NOW + timedelta(days=1)
    p.refresh()
    assert titles(p) == ["Standup"]


def test_hide_series_from_window_right_click_closes_its_details(qtbot, tmp_path):
    w = make_window(qtbot, tmp_path, SERIES)
    changed = []
    w.meetings_changed.connect(lambda: changed.append(1))
    chips = [c for col in w.time_view.columns for c in col.chips]
    assert len(chips) == 2
    qtbot.mouseClick(chips[0], Qt.MouseButton.LeftButton)
    d = w.details
    assert not hasattr(d, "hide_button")               # v0.8.0: right-click only
    right_click(chips[0])
    _menu_action(w, meeting_menus.HIDE_SERIES).trigger()
    assert [c for col in w.time_view.columns for c in col.chips] == []
    assert not d.isVisible() and changed == [1]
    assert w.notice_text() == "Hidden. Restore from the tray icon."


def test_one_off_meeting_has_no_series_option(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    click(qtbot, rows(p)[0])
    menu = row_menu(p, rows(p)[0])
    assert meeting_menus.find_action(menu, meeting_menus.HIDE_SERIES) is None
    assert [a.text() for a in menu.actions()][0] == meeting_menus.HIDE_ONE


def test_hidden_meeting_not_in_popup_window_next_or_toasts(qtbot, tmp_path):
    hidden, shown = ev("h", "Secret", 9, 5), ev("s", "Visible", 9, 8)
    cache, settings = store(tmp_path, [hidden, shown])
    settings.hide_event(hidden, "event")
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    assert titles(p) == ["Visible"] and p.next_text() == "Next: Visible in 8 min"
    w = make_window(qtbot, tmp_path, [], cache=cache, settings=settings)
    assert [c.event.id for col in w.time_view.columns for c in col.chips] == ["s"]
    w.set_view("month")
    assert [c.event.id for c in w.month_view.cells[date(2026, 11, 4)].chips] == ["s"]
    sent = []
    Notifier(cache, settings, lambda *a: sent.append(a[0]), clock=lambda: NOW,
             fired_path=tmp_path / "n.json").check(NOW)
    assert sent == ["Visible"]
    assert agenda.next_event(cache, settings, NOW).id == "s"


# -- colors ---------------------------------------------------------------------------
def test_color_from_popup_applies_to_series_and_keeps_flyout_open(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, SERIES)
    click(qtbot, rows(p)[0])
    menu = meeting_menus.submenu(row_menu(p, rows(p)[0]), meeting_menus.COLOR_SUBMENU)
    assert menu.actions()[0].text() == "Color for the whole series"
    names = [a.text() for a in menu.actions() if a.data()]
    assert names == [n for n, _ in theme.PALETTE]
    assert not _menu_action(p, meeting_menus.RESET_COLOR).isEnabled()
    _menu_action(p, "Basil").trigger()
    assert rows(p)[0].is_selected() and p.flyout_open()
    assert "#3fa364" in rows(p)[0].findChild(QFrame, "stripe").styleSheet()
    assert p.settings.event_color(SERIES[1]) == "#3fa364"
    row_menu(p, rows(p)[0])
    assert _menu_action(p, "Basil").isChecked()
    _menu_action(p, meeting_menus.RESET_COLOR).trigger()
    assert "#4285f4" in rows(p)[0].findChild(QFrame, "stripe").styleSheet()


def test_effective_color_used_in_popup_and_window(qtbot, tmp_path):
    one, other = ev("1", "Colored", 10), ev("2", "Plain", 11)
    allday = Event(id="ad", account="a", calendar_id="primary", calendar_name="Primary",
                   color="#4285f4", title="Holiday", start=datetime(2026, 11, 4, tzinfo=NY),
                   end=datetime(2026, 11, 5, tzinfo=NY), all_day=True)
    cache, settings = store(tmp_path, [one, other, allday])
    settings.set_event_color(one, "#b36ad6")
    settings.set_event_color(allday, "#f6c445")
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    stripes = [r.findChild(QFrame, "stripe").styleSheet() for r in rows(p)]
    assert "#f6c445" in stripes[0] and "#b36ad6" in stripes[1] and "#4285f4" in stripes[2]
    w = make_window(qtbot, tmp_path, [], cache=cache, settings=settings)
    chips = {c.event.id: c for col in w.time_view.columns for c in col.chips}
    # v0.11.0: tiles are painted; the stripe (Side stripe style) is the meeting's color
    assert chips["1"].color == "#b36ad6" and chips["1"].colors()[3] == "#b36ad6"
    assert chips["2"].color == "#4285f4"
    assert w.time_view.allday_chips[0].color == "#f6c445"
    w.set_view("month")
    month = {c.event.id: c.color for c in w.month_view.cells[date(2026, 11, 4)].chips}
    assert month == {"ad": "#f6c445", "1": "#b36ad6", "2": "#4285f4"}
    w.set_view("week")
    qtbot.mouseClick(chips_by_id(w)["1"], Qt.MouseButton.LeftButton)
    assert "#b36ad6" in w.details.calendar_label.text()
    right_click(chips_by_id(w)["1"])                   # the event's right-click menu:
    _menu_action(w, "Peacock").trigger()               # the open details follow at once
    assert "#2aa3d8" in w.details.calendar_label.text()
    assert chips_by_id(w)["1"].color == "#2aa3d8"


def chips_by_id(w):
    return {c.event.id: c for col in w.time_view.columns for c in col.chips}


# -- details window description --------------------------------------------------------
def test_details_description_links_open_only_http(qtbot, tmp_path, opened):
    d = EventDetails(ev("1", "S", 10, description=DESC), NOW)
    qtbot.addWidget(d)
    box = d.description_box
    assert isinstance(box, QTextBrowser) and box.isReadOnly()
    assert box.anchors() == ["https://docs.example.com/a?x=1&y=2"]
    assert not hasattr(d, "hide_button")             # read-only details (v0.8.0)
    for bad in ("javascript:alert(1)", "file:///C:/Windows/notepad.exe", "ftp://x",
                "https://user@evil.com/"):
        assert box.open_link(QUrl(bad)) is False
        box.anchorClicked.emit(QUrl(bad))
    assert opened.urls == []
    box.anchorClicked.emit(QUrl("https://docs.example.com/a?x=1&y=2"))
    assert opened.urls == ["https://docs.example.com/a?x=1&y=2"]


def test_injected_markup_in_description_stays_text(qtbot, tmp_path):
    nasty = ('<a href="javascript:alert(1)">click</a> https://x.com/"onclick="alert(1) '
             '&lt;img src=x onerror=alert(1)&gt;')
    d = EventDetails(ev("1", "S", 10, description=nasty), NOW)
    qtbot.addWidget(d)
    box = d.description_box
    assert box.anchors() == ["https://x.com/"]
    assert "<img src=x onerror=alert(1)>" in box.toPlainText()
    box._hover(QUrl("https://x.com/<b>"))
    from PyQt6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(box.toolTip())
    assert doc.toPlainText().strip() == QUrl("https://x.com/<b>").toString()


# -- Settings > Meetings ----------------------------------------------------------------
def build_dialog(qtbot, tmp_path, settings, cache):
    dlg = SettingsDialog(settings, cache, lambda: [], lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    return dlg


def test_unhide_from_settings_dialog(qtbot, tmp_path):
    cache, settings = store(tmp_path, SERIES + [ev("x", "Lunch", 12)])
    settings.hide_event(SERIES[0], "series")
    settings.hide_event(ev("x", "Lunch", 12), "event")
    settings.set_event_color(ev("x", "Lunch", 12), "#e8594f")
    dlg = build_dialog(qtbot, tmp_path, settings, cache)
    assert set(dlg.hidden_rows) == {"s:primary:s1", "e:primary:x"}
    assert dlg.hidden_rows["s:primary:s1"][0].text() == "Standup"
    assert set(dlg.color_rows) == {"e:primary:x"}
    label, button = dlg.hidden_rows["s:primary:s1"]
    button.click()
    assert button.text() == "Undo unhide" and label.font().strikeOut()
    assert settings.is_event_hidden(SERIES[0])      # nothing applied before OK
    dlg.color_rows["e:primary:x"][1].click()
    dlg.accept()
    s2 = Settings(tmp_path / "s.json")
    assert not s2.is_event_hidden(SERIES[0]) and s2.is_event_hidden(ev("x", "Lunch", 12))
    assert s2.event_colors == {}
    # Cancel applies nothing.
    dlg2 = build_dialog(qtbot, tmp_path, s2, cache)
    dlg2.hidden_rows["e:primary:x"][1].click()
    dlg2.reject()
    assert Settings(tmp_path / "s.json").is_event_hidden(ev("x", "Lunch", 12))


def test_meetings_tab_titles_are_plain_text(qtbot, tmp_path):
    cache, settings = store(tmp_path, [])
    evil = ev("x", "<b>bold</b> & <img src=x>", 12)
    settings.hide_event(evil, "event")
    dlg = build_dialog(qtbot, tmp_path, settings, cache)
    label = dlg.hidden_rows["e:primary:x"][0]
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == "<b>bold</b> & <img src=x>"
