from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from smartcal.models import Event
from smartcal.ui import layout

NY = ZoneInfo("America/New_York")
REF = datetime(2026, 11, 4, 9, 0, tzinfo=NY)


def ev(id, start, end, all_day=False, title="t"):
    return Event(id=id, account="a", calendar_id="c", calendar_name="C", color="#4285f4",
                 title=title, start=start, end=end, all_day=all_day)


def test_overlap_packing_three_way():
    # A overlaps B and C; B and C do not overlap each other.
    res = layout.pack_columns([(540, 660), (550, 600), (610, 650)])
    assert res == [(0, 2), (1, 2), (1, 2)]
    # Fully concurrent trio gets three columns.
    assert layout.pack_columns([(0, 60), (0, 60), (30, 90)]) == [(0, 3), (1, 3), (2, 3)]
    # Separate clusters stay full-width.
    assert layout.pack_columns([(0, 30), (30, 60)]) == [(0, 1), (0, 1)]


def test_month_grid_starts_on_sunday_and_has_42_cells():
    g = layout.month_grid(2026, 11)  # Nov 1 2026 is a Sunday
    assert len(g) == 42 and g[0] == date(2026, 11, 1)
    g = layout.month_grid(2026, 10)  # Oct 1 is a Thursday
    assert g[0] == date(2026, 9, 27) and g[0].weekday() == 6
    assert g[-1] == g[0] + timedelta(days=41)


def test_more_count_when_over_three_events():
    assert layout.more_count(3) == 0
    assert layout.more_count(4) == 1
    assert layout.more_count(7) == 4
    shown, more = layout.split_for_month(list(range(5)))
    assert shown == [0, 1, 2] and more == 2


def test_event_ending_at_midnight_not_on_next_day():
    e = ev("1", datetime(2026, 11, 4, 22, 0, tzinfo=NY), datetime(2026, 11, 5, 0, 0, tzinfo=NY))
    assert layout.event_days(e, REF) == [date(2026, 11, 4)]
    assert layout.day_blocks([e], date(2026, 11, 5), REF) == []
    b = layout.day_blocks([e], date(2026, 11, 4), REF)
    assert (b[0].start_min, b[0].end_min) == (22 * 60, 1440)
    # All-day end is exclusive too.
    a = ev("2", datetime(2026, 11, 4, tzinfo=NY), datetime(2026, 11, 6, tzinfo=NY), True)
    assert layout.event_days(a, REF) == [date(2026, 11, 4), date(2026, 11, 5)]


def test_dst_week_has_correct_day_columns():
    # US DST ends Sun Nov 1 2026: that day is 25 hours long.
    days = layout.week_dates(date(2026, 11, 4))
    assert days == [date(2026, 11, 1) + timedelta(days=i) for i in range(7)]
    s, e = layout.day_bounds(date(2026, 11, 1), REF)
    assert e.timestamp() - s.timestamp() == 25 * 3600
    late = ev("1", datetime(2026, 11, 1, 23, 30, tzinfo=NY), datetime(2026, 11, 1, 23, 50, tzinfo=NY))
    early = ev("2", datetime(2026, 11, 2, 0, 30, tzinfo=NY), datetime(2026, 11, 2, 1, 0, tzinfo=NY))
    by = layout.events_by_day([late, early], days, REF)
    assert by[date(2026, 11, 1)] == [late] and by[date(2026, 11, 2)] == [early]


def test_titles():
    assert layout.title_for("month", date(2026, 11, 4)) == "Nov 2026"
    assert layout.title_for("week", date(2026, 11, 4)) == "Nov 1 – 7, 2026"
    assert layout.title_for("week", date(2026, 12, 1)) == "Nov 29 – Dec 5, 2026"
    assert layout.title_for("day", date(2026, 11, 4)) == "Wed, Nov 4, 2026"
    assert layout.shift_month(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_cross_midnight_event_clipped_on_second_day():
    e = ev("1", datetime(2026, 11, 4, 22, tzinfo=NY), datetime(2026, 11, 5, 2, tzinfo=NY))
    b = layout.day_blocks([e], date(2026, 11, 5), REF)
    assert [(x.start_min, x.end_min) for x in b] == [(0, 120)]
    assert layout.event_days(e, REF) == [date(2026, 11, 4), date(2026, 11, 5)]


def test_zero_duration_event_gets_min_height():
    t = datetime(2026, 11, 4, 10, tzinfo=NY)
    b = layout.day_blocks([ev("1", t, t)], date(2026, 11, 4), REF)
    assert b[0].end_min - b[0].start_min == layout.MIN_BLOCK_MIN
