"""Popup row expansion, linkified descriptions, Hide…/Color… in the popup, the window
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


def make_popup(qtbot, tmp_path, events, show=True):
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    if show:
        p.show_near_tray(QRect(700, 570, 20, 20))
        qtbot.waitExposed(p)
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
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


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


# -- expansion ----------------------------------------------------------------
def test_popup_row_click_expands_and_collapses(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "Standup", 10, location="Room 4", description=DESC)])
    [row] = rows(p)
    assert row.details is None                       # built lazily, on first expand
    h0 = p.height()
    click(qtbot, row.findChild(QLabel, "title"))     # a click on the title reaches the row
    assert row.is_expanded() and p.expanded_row() is row
    assert p.height() > h0
    d = row.details
    assert d.findChild(QLabel, "when").text() == "Wed, Nov 4, 2026 · 10:00 AM – 10:30 AM"
    assert d.findChild(QLabel, "location").text() == "Room 4"
    assert "Primary" in d.findChild(QLabel, "calendar").text()
    assert d.findChild(QPushButton, "hide").text() == "Hide…"
    assert d.findChild(QPushButton, "color").text() == "Color…"
    click(qtbot, row.findChild(QFrame, "stripe"))
    assert not row.is_expanded() and p.expanded_row() is None
    assert p.height() == h0


def test_only_one_row_expanded(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11), ev("3", "C", 12)])
    a, b, c = rows(p)
    click(qtbot, a)
    click(qtbot, b)
    assert [r.is_expanded() for r in rows(p)] == [False, True, False]
    click(qtbot, c)
    assert [r.is_expanded() for r in rows(p)] == [False, False, True]


def test_row_join_button_does_not_toggle(qtbot, tmp_path, opened):
    z = "https://us02web.zoom.us/j/1"
    p = make_popup(qtbot, tmp_path, [ev("1", "Zoom", 10, meeting_url=z)])
    [row] = rows(p)
    join = row.findChild(QPushButton, "join")
    click(qtbot, join)
    assert opened.urls == [z]
    assert not row.is_expanded() and row.details is None


def test_enter_toggles_row(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    a, b = rows(p)
    assert a.focusPolicy() & Qt.FocusPolicy.TabFocus
    qtbot.keyClick(a, Qt.Key.Key_Return)
    assert a.is_expanded()
    qtbot.keyClick(a, Qt.Key.Key_Space)
    assert not a.is_expanded()
    qtbot.keyClick(b, Qt.Key.Key_Enter)
    assert b.is_expanded()
    qtbot.keyClick(b, Qt.Key.Key_Escape)            # Esc collapses first...
    assert not b.is_expanded() and p.isVisible()
    qtbot.keyClick(p, Qt.Key.Key_Escape)            # ...then closes the popup
    assert not p.isVisible()


def test_expanded_row_shows_linkified_description(qtbot, tmp_path, opened):
    z = "https://meet.google.com/abc-defg-hij"
    p = make_popup(qtbot, tmp_path, [ev("1", "Sync", 10, description=DESC, meeting_url=z)])
    [row] = rows(p)
    click(qtbot, row)
    box = row.details.findChild(QTextBrowser, "description")
    assert box.isReadOnly() and not box.openLinks() and not box.openExternalLinks()
    assert box.toPlainText() == ("Agenda: not bold & more\n"
                                 "Notes at https://docs.example.com/a?x=1&y=2.\n"
                                 "Bad: javascript:alert(1) file:///C:/x www.example.com")
    assert box.anchors() == ["https://docs.example.com/a?x=1&y=2"]
    big = row.details.findChild(QPushButton, "bigjoin")
    assert big.text() == "Join Google Meet"
    # Only http(s) opens, whatever the anchor says.
    for bad in ("javascript:alert(1)", "file:///C:/Windows/notepad.exe", "mailto:a@b.c"):
        box.anchorClicked.emit(QUrl(bad))
    assert opened.urls == [] and p.isVisible()
    box.anchorClicked.emit(QUrl("https://docs.example.com/a?x=1&y=2"))
    assert opened.urls == ["https://docs.example.com/a?x=1&y=2"]
    assert not p.isVisible()                          # like Join: opening a link closes it


def test_description_is_capped_at_about_eight_lines(qtbot, tmp_path):
    long = "\n".join(f"line {i}" for i in range(60))
    p = make_popup(qtbot, tmp_path, [ev("1", "Sync", 10, description=long)])
    click(qtbot, rows(p)[0])
    box = rows(p)[0].details.findChild(QTextBrowser, "description")
    assert box.height() <= 10 * box.fontMetrics().lineSpacing() + 20
    assert box.verticalScrollBar().maximum() > 0      # the rest scrolls


def test_refresh_keeps_the_open_row_open(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)])
    click(qtbot, rows(p)[1])
    p.refresh()
    assert [r.is_expanded() for r in rows(p)] == [False, True]
    p.hide()
    p.refresh()
    assert p.expanded_row() is None                   # reopens collapsed


def test_popup_height_cap_with_expanded_row(qtbot, tmp_path):
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
    click(qtbot, rows(p)[10])
    assert rows(p)[10].is_expanded()
    assert p.height() <= min(480, 600 - 24)
    assert QRect(0, 0, 1000, 600).contains(p.geometry())
    assert p._footer.isVisible() and p._footer.geometry().bottom() <= p.rect().bottom()
    assert p._header.isVisible()


def test_expanding_is_fast(qtbot, tmp_path):
    import time
    p = make_popup(qtbot, tmp_path, [ev("1", "Sync", 10, description=DESC * 50)])
    t0 = time.perf_counter()
    click(qtbot, rows(p)[0])
    QApplication.processEvents()
    assert (time.perf_counter() - t0) * 1000 < 200


# -- menus keep the popup open ------------------------------------------------------
def _open_color_menu(qtbot, p):
    click(qtbot, rows(p)[0])
    click(qtbot, rows(p)[0].details.findChild(QPushButton, "color"))
    QApplication.processEvents()      # deliver what the platform queued for the open menu
    return p.menu()


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
    QApplication.sendEvent(p, QEvent(QEvent.Type.WindowDeactivate))
    assert not p.isVisible()                          # no menu any more: normal hide


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
    click(qtbot, rows(p)[0].details.findChild(QPushButton, "hide"))
    assert [a.text() for a in p.menu().actions()] == [meeting_menus.HIDE_ONE,
                                                      meeting_menus.HIDE_SERIES]
    _menu_action(p, meeting_menus.HIDE_ONE).trigger()
    assert titles(p) == ["Lunch"] and changed == [1]
    assert p.notice_text() == "Hidden. Undo in Settings > Meetings."
    assert p.settings.is_event_hidden(SERIES[0]) and not p.settings.is_event_hidden(SERIES[1])
    p.now = lambda: NOW + timedelta(days=1)
    p.refresh()
    assert titles(p) == ["Standup"]


def test_hide_series_from_window_details(qtbot, tmp_path):
    w = make_window(qtbot, tmp_path, SERIES)
    changed = []
    w.meetings_changed.connect(lambda: changed.append(1))
    chips = [c for col in w.time_view.columns for c in col.chips]
    assert len(chips) == 2
    qtbot.mouseClick(chips[0], Qt.MouseButton.LeftButton)
    d = w.details
    d.hide_button.click()
    _menu_action(d, meeting_menus.HIDE_SERIES).trigger()
    assert [c for col in w.time_view.columns for c in col.chips] == []
    assert not d.isVisible() and changed == [1]
    assert w.notice_text() == "Hidden. Undo in Settings > Meetings."


def test_one_off_meeting_has_no_series_option(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, [ev("1", "A", 10)])
    click(qtbot, rows(p)[0])
    click(qtbot, rows(p)[0].details.findChild(QPushButton, "hide"))
    assert [a.text() for a in p.menu().actions()] == [meeting_menus.HIDE_ONE]


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
def test_color_from_popup_applies_to_series_and_keeps_row_open(qtbot, tmp_path):
    p = make_popup(qtbot, tmp_path, SERIES)
    click(qtbot, rows(p)[0])
    color_btn = rows(p)[0].details.findChild(QPushButton, "color")
    assert color_btn.text() == "Color… (series)"
    click(qtbot, color_btn)
    menu = p.menu()
    names = [a.text() for a in menu.actions() if a.data()]
    assert names == [n for n, _ in theme.PALETTE]
    assert not _menu_action(p, meeting_menus.RESET_COLOR).isEnabled()
    _menu_action(p, "Basil").trigger()
    assert rows(p)[0].is_expanded()
    assert "#3fa364" in rows(p)[0].findChild(QFrame, "stripe").styleSheet()
    assert p.settings.event_color(SERIES[1]) == "#3fa364"
    click(qtbot, rows(p)[0].details.findChild(QPushButton, "color"))
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
    assert chips["1"].color == "#b36ad6" and "#b36ad6" in chips["1"].styleSheet()
    assert chips["2"].color == "#4285f4"
    assert w.time_view.allday_chips[0].color == "#f6c445"
    w.set_view("month")
    month = {c.event.id: c.color for c in w.month_view.cells[date(2026, 11, 4)].chips}
    assert month == {"ad": "#f6c445", "1": "#b36ad6", "2": "#4285f4"}
    w.set_view("week")
    qtbot.mouseClick(chips_by_id(w)["1"], Qt.MouseButton.LeftButton)
    assert "#b36ad6" in w.details.calendar_label.text()
    w.details.color_button.click()
    _menu_action(w.details, "Peacock").trigger()      # window details: applies at once
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
    assert d.hide_button is None                     # no settings: read-only details
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
