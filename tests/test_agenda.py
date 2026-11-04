from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from smartcal import agenda
from smartcal.db import Cache
from smartcal.models import CalendarInfo, Event
from smartcal.settings import Settings

NY = ZoneInfo("America/New_York")


def ev(id, title, start, end, account="a", cal="primary", all_day=False):
    return Event(id=id, account=account, calendar_id=cal, calendar_name=cal, color="#111",
                 title=title, start=start, end=end, all_day=all_day)


def make(tmp_path, events, cals=None):
    cache = Cache(tmp_path / "c.db")
    for e in events:
        cache.upsert_events(e.account, [e])
    cache.upsert_calendars("a", cals or [CalendarInfo("primary", "Me", "#111", True),
                                         CalendarInfo("ooo", "Out of Office", "#222", False)])
    return cache, Settings(tmp_path / "s.json")


def at(h, m=0, d=4, mo=11):
    return datetime(2026, mo, d, h, m, tzinfo=NY)


def test_hidden_calendar_excluded_from_agenda(tmp_path):
    cache, st = make(tmp_path, [ev("1", "Standup", at(9), at(10)),
                                ev("2", "OOO", at(9), at(17), cal="ooo")])
    assert [e.title for e in agenda.today(cache, st, at(8))] == ["Standup"]
    st.set_hidden("ooo", False)
    assert len(agenda.today(cache, st, at(8))) == 2
    st.set_hidden("primary", True)
    assert [e.title for e in agenda.today(cache, st, at(8))] == ["OOO"]
    assert agenda.next_event(cache, st, at(8)).title == "OOO"


def test_new_non_primary_calendar_defaults_hidden(tmp_path):
    cache, st = make(tmp_path, [ev("2", "OOO", at(9), at(17), cal="ooo")])
    assert st.is_hidden("ooo", primary=False)
    assert not st.is_hidden("p", primary=True)
    assert agenda.today(cache, st, at(8)) == []
    st.set_hidden("ooo", False)
    st2 = Settings(tmp_path / "s.json")  # persisted
    assert not st2.is_hidden("ooo", primary=False)
    assert len(agenda.today(cache, st2, at(8))) == 1


def test_agenda_merges_two_accounts_sorted(tmp_path):
    cache, st = make(tmp_path, [ev("1", "Later", at(14), at(15), account="a"),
                                ev("2", "Early", at(9), at(10), account="b")])
    cache.upsert_calendars("b", [CalendarInfo("primary", "Work", "#333", True)])
    assert [e.title for e in agenda.today(cache, st, at(8))] == ["Early", "Later"]


def test_all_day_listed_as_all_day_first(tmp_path):
    cache, st = make(tmp_path, [
        ev("1", "Meeting", at(0, 30), at(1)),
        ev("2", "Holiday", at(0), at(0, d=5), all_day=True),
        ev("3", "Yesterday", at(0, d=3), at(0, d=4), all_day=True),
        ev("4", "Tomorrow", at(0, d=5), at(0, d=6), all_day=True)])
    got = agenda.today(cache, st, at(12))
    assert [e.title for e in got] == ["Holiday", "Meeting"]
    assert got[0].all_day


def test_next_event_skips_ended_and_all_day(tmp_path):
    cache, st = make(tmp_path, [
        ev("1", "Done", at(8), at(9)),
        ev("2", "Holiday", at(0), at(0, d=5), all_day=True),
        ev("3", "Next", at(11), at(12)),
        ev("4", "After", at(13), at(14))])
    assert agenda.next_event(cache, st, at(10)).title == "Next"
    assert agenda.next_event(cache, st, at(11, 30)).title == "Next"  # in progress
    assert agenda.next_event(cache, st, at(15)) is None


def test_describe_next_in_minutes():
    now = at(9)
    e = ev("1", "Standup", now + timedelta(minutes=12), now + timedelta(hours=1))
    assert agenda.describe_next(e, now) == "Standup in 12 min"
    e.start = now + timedelta(minutes=65)
    assert agenda.describe_next(e, now) == "Standup in 1 h 5 min"
    e.start = now + timedelta(hours=2)
    assert agenda.describe_next(e, now) == "Standup in 2 h"
    e.start = now - timedelta(minutes=5)
    assert agenda.describe_next(e, now) == "Standup now"


def test_events_display_in_local_time_across_dst(tmp_path):
    # 2026-11-01: US fall back, a 25-hour day. Cached offsets are fixed (EDT then EST).
    edt, est = timezone(timedelta(hours=-4)), timezone(timedelta(hours=-5))
    cache, st = make(tmp_path, [
        ev("0", "Prev", datetime(2026, 10, 31, 23, 30, tzinfo=edt), datetime(2026, 11, 1, 0, 0, tzinfo=edt)),
        ev("1", "Early", datetime(2026, 11, 1, 0, 30, tzinfo=edt), datetime(2026, 11, 1, 1, 0, tzinfo=edt)),
        ev("2", "Late", datetime(2026, 11, 1, 23, 30, tzinfo=est), datetime(2026, 11, 2, 0, 0, tzinfo=est)),
        ev("3", "Next", datetime(2026, 11, 2, 0, 30, tzinfo=est), datetime(2026, 11, 2, 1, 0, tzinfo=est))],
        cals=[CalendarInfo("primary", "Me", "#111", True)])
    got = agenda.today(cache, st, datetime(2026, 11, 1, 12, 0, tzinfo=NY))
    assert [e.title for e in got] == ["Early", "Late"]
    assert [e.start.astimezone(NY).strftime("%H:%M") for e in got] == ["00:30", "23:30"]
    # spring forward (23-hour day): Mar 8 2026
    e4 = timezone(timedelta(hours=-4))
    (tmp_path / "x").mkdir()
    cache2, st2 = make(tmp_path / "x", [
        ev("1", "Eve", datetime(2026, 3, 8, 23, 30, tzinfo=e4), datetime(2026, 3, 9, 0, 0, tzinfo=e4)),
        ev("2", "Mon", datetime(2026, 3, 9, 0, 30, tzinfo=e4), datetime(2026, 3, 9, 1, 0, tzinfo=e4))])
    got = agenda.today(cache2, st2, datetime(2026, 3, 8, 12, 0, tzinfo=NY))
    assert [e.title for e in got] == ["Eve"]


def test_dst_day_boundaries_with_fixed_offset_now(tmp_path, monkeypatch):
    # Production passes datetime.now().astimezone(): a fixed-offset tzinfo.
    monkeypatch.setattr(agenda, "_local_zone", lambda: NY)
    edt, est = timezone(timedelta(hours=-4)), timezone(timedelta(hours=-5))
    # Fall back (25-hour day), now is EST late in the day.
    cache, st = make(tmp_path, [
        ev("0", "Prev", datetime(2026, 10, 31, 23, 30, tzinfo=edt), datetime(2026, 11, 1, 0, 0, tzinfo=edt)),
        ev("1", "Early", datetime(2026, 11, 1, 0, 30, tzinfo=edt), datetime(2026, 11, 1, 1, 0, tzinfo=edt)),
        ev("2", "Late", datetime(2026, 11, 1, 23, 30, tzinfo=est), datetime(2026, 11, 2, 0, 0, tzinfo=est)),
        ev("3", "Next", datetime(2026, 11, 2, 0, 30, tzinfo=est), datetime(2026, 11, 2, 1, 0, tzinfo=est))],
        cals=[CalendarInfo("primary", "Me", "#111", True)])
    now = datetime(2026, 11, 1, 23, 45, tzinfo=est)
    assert [e.title for e in agenda.today(cache, st, now)] == ["Early", "Late"]
    # Spring forward (23-hour day), now is EDT.
    (tmp_path / "x").mkdir()
    cache2, st2 = make(tmp_path / "x", [
        ev("1", "Eve", datetime(2026, 3, 8, 23, 30, tzinfo=edt), datetime(2026, 3, 9, 0, 0, tzinfo=edt)),
        ev("2", "Mon", datetime(2026, 3, 9, 0, 30, tzinfo=edt), datetime(2026, 3, 9, 1, 0, tzinfo=edt)),
        ev("3", "Sat", datetime(2026, 3, 7, 23, 30, tzinfo=est), datetime(2026, 3, 8, 0, 0, tzinfo=est))])
    now2 = datetime(2026, 3, 8, 12, 0, tzinfo=edt)
    assert [e.title for e in agenda.today(cache2, st2, now2)] == ["Eve"]
