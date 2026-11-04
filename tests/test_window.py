from datetime import date, datetime
from zoneinfo import ZoneInfo

from PyQt6.QtCore import QSettings, Qt

from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.settings import Settings
from smartcal.ui.window import CalendarWindow

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=NY)


def ev(id, title, start, end, all_day=False, cal="primary", color="#4285f4", **kw):
    return Event(id=id, account="a", calendar_id=cal, calendar_name=cal.title(), color=color,
                 title=title, start=start, end=end, all_day=all_day, **kw)


def make(qtbot, tmp_path, events, view="week"):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True),
                                 CalendarInfo("other", "Other", "#0b8043", False)])
    cache.upsert_events("a", events)
    w = CalendarWindow(cache, Settings(tmp_path / "s.json"),
                       QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat))
    w.now = lambda: NOW
    qtbot.addWidget(w)
    w.go_to(date(2026, 11, 4))
    w.set_view(view)
    w.resize(1000, 700)
    w.show()
    return w


def test_month_view_places_multiday_event_on_each_day(qtbot, tmp_path):
    trip = ev("1", "Trip", datetime(2026, 11, 10, tzinfo=NY), datetime(2026, 11, 13, tzinfo=NY), True)
    w = make(qtbot, tmp_path, [trip], "month")
    cells = w.month_view.cells
    assert len(cells) == 42
    for d in (10, 11, 12):
        chips = cells[date(2026, 11, d)].chips
        assert [c.text() for c in chips] == ["Trip"]
        assert chips[0].isVisible()
    assert cells[date(2026, 11, 13)].chips == []  # all-day end is exclusive
    assert cells[date(2026, 11, 9)].chips == []


def test_month_view_more_and_dimmed_and_today(qtbot, tmp_path):
    evs = [ev(str(i), f"E{i}", datetime(2026, 11, 4, 8 + i, tzinfo=NY),
              datetime(2026, 11, 4, 8 + i, 30, tzinfo=NY)) for i in range(5)]
    evs.append(ev("ad", "Holiday", datetime(2026, 11, 4, tzinfo=NY), datetime(2026, 11, 5, tzinfo=NY), True))
    w = make(qtbot, tmp_path, evs, "month")
    cell = w.month_view.cells[date(2026, 11, 4)]
    assert len(cell.chips) == 3 and cell.chips[0].text() == "Holiday"
    assert cell.more_label.text() == "+3 more"
    assert w.month_view.cells[date(2026, 12, 1)].dimmed
    assert not cell.dimmed
    assert w.title_label.text() == "Nov 2026"


def test_week_view_overlapping_events_do_not_hide_each_other(qtbot, tmp_path):
    a = ev("1", "A", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 12, tzinfo=NY))
    b = ev("2", "B", datetime(2026, 11, 4, 10, 30, tzinfo=NY), datetime(2026, 11, 4, 11, 30, tzinfo=NY))
    c = ev("3", "C", datetime(2026, 11, 4, 11, tzinfo=NY), datetime(2026, 11, 4, 12, tzinfo=NY))
    w = make(qtbot, tmp_path, [a, b, c], "week")
    assert len(w.time_view.columns) == 7
    col = w.time_view.columns[3]  # Sun, Mon, Tue, Wed
    assert col.day == date(2026, 11, 4)
    chips = {ch.event.id: ch.geometry() for ch in col.chips}
    assert set(chips) == {"1", "2", "3"}
    assert all(ch.isVisible() for ch in col.chips)
    rects = list(chips.values())
    for i in range(3):
        for j in range(i + 1, 3):
            vertical = rects[i].top() < rects[j].bottom() and rects[j].top() < rects[i].bottom()
            if vertical:
                assert not rects[i].intersects(rects[j]), (i, j)
    assert chips["1"].width() < col.width()
    assert w.time_view.columns[0].chips == []


def test_hidden_calendar_events_are_excluded(qtbot, tmp_path):
    shown = ev("1", "Mine", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 11, tzinfo=NY))
    hid = ev("2", "Other", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 11, tzinfo=NY),
             cal="other")
    w = make(qtbot, tmp_path, [shown, hid], "day")
    assert [c.event.title for c in w.time_view.columns[0].chips] == ["Mine"]
    w.settings.set_hidden("primary", True)
    w.refresh()
    assert w.time_view.columns[0].chips == []


def test_day_view_all_day_strip_and_navigation(qtbot, tmp_path):
    ad = ev("1", "Holiday", datetime(2026, 11, 4, tzinfo=NY), datetime(2026, 11, 5, tzinfo=NY), True)
    w = make(qtbot, tmp_path, [ad], "day")
    assert [c.text() for c in w.time_view.allday_chips] == ["Holiday"]
    assert w.title_label.text() == "Wed, Nov 4, 2026"
    qtbot.mouseClick(w.next_btn, Qt.MouseButton.LeftButton)
    assert w.anchor == date(2026, 11, 5) and w.time_view.allday_chips == []
    qtbot.mouseClick(w.today_btn, Qt.MouseButton.LeftButton)
    assert w.anchor == date(2026, 11, 4)
    qtbot.mouseClick(w.view_buttons["month"], Qt.MouseButton.LeftButton)
    assert w.view == "month"


def test_day_view_scrolls_to_current_hour_on_today(qtbot, tmp_path):
    w = make(qtbot, tmp_path, [], "day")
    qtbot.waitUntil(lambda: w.time_view.scroll_value() == 8 * 48, timeout=2000)


def test_click_event_shows_details(qtbot, tmp_path):
    e = ev("1", "Standup", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 10, 30, tzinfo=NY),
           location="Room 4", description="Bring notes", meeting_url="https://meet.example/abc")
    w = make(qtbot, tmp_path, [e], "week")
    chip = w.time_view.columns[3].chips[0]
    qtbot.mouseClick(chip, Qt.MouseButton.LeftButton)
    d = w.details
    assert d is not None and d.isVisible()
    assert d.title_label.text() == "Standup"
    assert d.time_label.text() == "Wed, Nov 4, 2026 · 10:00 AM – 10:30 AM"
    assert "Primary" in d.calendar_label.text()
    assert d.location_label.text() == "Room 4"
    assert "https://meet.example/abc" in d.link_label.text()
    assert d.description_box.toPlainText() == "Bring notes" and d.description_box.isReadOnly()
    # also from the month view
    w.set_view("month")
    qtbot.mouseClick(w.month_view.cells[date(2026, 11, 4)].chips[0], Qt.MouseButton.LeftButton)
    assert w.details.event.id == "1"


def test_geometry_remembered(qtbot, tmp_path):
    w = make(qtbot, tmp_path, [], "week")
    w.resize(777, 555)
    w.close()
    q = QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat)
    assert q.value("window/geometry") is not None


# -- fix round 1 ---------------------------------------------------------------
from datetime import timezone  # noqa: E402

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QTextDocument  # noqa: E402

from smartcal import agenda  # noqa: E402,F401
from smartcal.ui import theme, window as window_mod  # noqa: E402


def test_month_cell_click_opens_day_view(qtbot, tmp_path):
    w = make(qtbot, tmp_path, [], "month")
    cell = w.month_view.cells[date(2026, 11, 12)]
    qtbot.mouseClick(cell, Qt.MouseButton.LeftButton, pos=QPoint(3, 3))
    assert w.view == "day" and w.anchor == date(2026, 11, 12)


def test_week_header_click_opens_day_view(qtbot, tmp_path):
    w = make(qtbot, tmp_path, [], "week")
    head = w.time_view.headers[5]
    qtbot.mouseClick(head, Qt.MouseButton.LeftButton)
    assert w.view == "day" and w.anchor == head.day == date(2026, 11, 6)


def test_chip_click_in_cell_opens_details_not_day_view(qtbot, tmp_path):
    e = ev("1", "Standup", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 11, tzinfo=NY))
    w = make(qtbot, tmp_path, [e], "month")
    qtbot.mouseClick(w.month_view.cells[date(2026, 11, 4)].chips[0], Qt.MouseButton.LeftButton)
    assert w.details is not None and w.details.isVisible()
    assert w.view == "month"


def test_month_chip_time_is_converted_to_local(qtbot, tmp_path):
    # 15:00 UTC is 10:00 in New York (EST).
    e = ev("1", "Call", datetime(2026, 11, 4, 15, tzinfo=timezone.utc),
           datetime(2026, 11, 4, 16, tzinfo=timezone.utc))
    w = make(qtbot, tmp_path, [e], "month")
    assert w.month_view.cells[date(2026, 11, 4)].chips[0].text() == "10a Call"


def test_tooltip_title_is_not_rich_text(qtbot, tmp_path):
    e = ev("1", "<b>x</b>", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 11, tzinfo=NY),
           meeting_url="https://m.example/<i>")
    w = make(qtbot, tmp_path, [e], "week")
    tip = w.time_view.columns[3].chips[0].toolTip()
    assert "&lt;b&gt;x&lt;/b&gt;" in tip and "<b>" not in tip
    doc = QTextDocument()
    doc.setHtml(tip)
    assert doc.toPlainText().strip() == "<b>x</b>"
    qtbot.mouseClick(w.time_view.columns[3].chips[0], Qt.MouseButton.LeftButton)
    assert "<i>" not in w.details.link_label.toolTip()


def test_html_in_title_and_location_renders_verbatim(qtbot, tmp_path):
    e = ev("1", "<b>x</b>", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 11, tzinfo=NY),
           location="<u>Room</u> & co", description="<i>d</i>")
    e.calendar_name = "<i>c</i>"
    w = make(qtbot, tmp_path, [e], "week")
    chip = w.time_view.columns[3].chips[0]
    assert chip.textFormat() == Qt.TextFormat.PlainText and "<b>x</b>" in chip.text()
    qtbot.mouseClick(chip, Qt.MouseButton.LeftButton)
    d = w.details
    assert d.title_label.textFormat() == Qt.TextFormat.PlainText and d.title_label.text() == "<b>x</b>"
    assert d.location_label.textFormat() == Qt.TextFormat.PlainText
    assert d.location_label.text() == "<u>Room</u> & co"
    doc = QTextDocument()
    doc.setHtml(d.calendar_label.text())
    assert doc.toPlainText().endswith("<i>c</i>")
    assert d.description_box.toPlainText() == "<i>d</i>"


def test_open_link_rejects_javascript_and_file_schemes(monkeypatch):
    opened = []

    class Stub:
        @staticmethod
        def openUrl(url):
            opened.append(url.toString())

    monkeypatch.setattr(window_mod, "QDesktopServices", Stub)
    for bad in ("javascript:alert(1)", "file:///C:/Windows/notepad.exe", "ftp://x", ""):
        window_mod.EventDetails.open_link(bad)
    assert opened == []
    window_mod.EventDetails.open_link("https://meet.example/a")
    assert opened == ["https://meet.example/a"]


def test_zero_duration_event_gets_min_height(qtbot, tmp_path):
    t = datetime(2026, 11, 4, 10, tzinfo=NY)
    w = make(qtbot, tmp_path, [ev("1", "Ping", t, t)], "day")
    chip = w.time_view.columns[0].chips[0]
    assert chip.isVisible() and chip.height() >= 10


def test_multiday_timed_event_in_month_covers_each_day(qtbot, tmp_path):
    e = ev("1", "Overnight", datetime(2026, 11, 4, 22, tzinfo=NY), datetime(2026, 11, 6, 2, tzinfo=NY))
    w = make(qtbot, tmp_path, [e], "month")
    for d in (4, 5, 6):
        assert len(w.month_view.cells[date(2026, 11, d)].chips) == 1
    assert w.month_view.cells[date(2026, 11, 7)].chips == []
    assert w.month_view.cells[date(2026, 11, 3)].chips == []


def test_hidden_calendar_excluded_in_week_and_month(qtbot, tmp_path):
    hid = ev("2", "Other", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 11, tzinfo=NY),
             cal="other")
    w = make(qtbot, tmp_path, [hid], "week")
    assert all(c.chips == [] for c in w.time_view.columns)
    w.set_view("month")
    assert all(c.chips == [] for c in w.month_view.cells.values())


def test_hidden_window_does_not_rebuild_on_synced(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path, [], "week")
    w.hide()
    calls = []
    monkeypatch.setattr(w.time_view, "set_data", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(w.month_view, "set_data", lambda *a, **k: calls.append(1))
    w.refresh()
    assert calls == []


def test_show_refreshes_stale_content(qtbot, tmp_path):
    w = make(qtbot, tmp_path, [], "week")
    w.hide()
    w.cache.upsert_events("a", [ev("1", "New", datetime(2026, 11, 4, 10, tzinfo=NY),
                                   datetime(2026, 11, 4, 11, tzinfo=NY))])
    w.refresh()
    w.show()
    assert [c.event.title for c in w.time_view.columns[3].chips] == ["New"]


def test_bad_saved_geometry_falls_back_on_screen(qtbot, tmp_path):
    q = QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat)
    q.setValue("window/geometry", "junk")
    q.sync()
    cache = Cache(tmp_path / "c.db")
    w = CalendarWindow(cache, Settings(tmp_path / "s.json"), q)
    qtbot.addWidget(w)
    w.show()
    assert any(s.availableGeometry().intersects(w.frameGeometry()) for s in QGuiApplication.screens())


def test_hide_saves_geometry_and_closes_details(qtbot, tmp_path):
    e = ev("1", "S", datetime(2026, 11, 4, 10, tzinfo=NY), datetime(2026, 11, 4, 11, tzinfo=NY))
    w = make(qtbot, tmp_path, [e], "week")
    qtbot.mouseClick(w.time_view.columns[3].chips[0], Qt.MouseButton.LeftButton)
    assert w.details.isVisible()
    w.hide()
    assert not w.details.isVisible()
    q = QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat)
    assert q.value("window/geometry") is not None


def test_invalid_color_falls_back():
    assert theme.stripe_color("#0b8043") == "#0b8043"
    assert theme.stripe_color("#abc") == "#aabbcc"
    for bad in ("red", "#12345", "#gggggg", "#80ff0000", "url(x);}", "", None):
        assert theme.stripe_color(bad) == "#4285f4"
