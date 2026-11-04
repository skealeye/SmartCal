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
