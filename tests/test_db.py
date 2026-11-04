from datetime import datetime, timedelta, timezone

from smartcal.db import Cache
from smartcal.models import Event

TZ = timezone(timedelta(hours=-5))


def ev(id, start, hours=1, **kw):
    base = dict(
        id=id, account="a@x.com", calendar_id="cal1", calendar_name="Main",
        color="#112233", title=f"Event {id}", start=start,
        end=start + timedelta(hours=hours), all_day=False, location="",
        description="", meeting_url=None, recurring_id=None,
    )
    base.update(kw)
    return Event(**base)


def test_upsert_then_query_returns_events_sorted_by_start(tmp_path):
    c = Cache(tmp_path / "c.db")
    t = datetime(2026, 11, 4, 9, tzinfo=TZ)
    c.upsert_events("a@x.com", [ev("2", t + timedelta(hours=3)), ev("1", t)])
    got = c.events_between(t - timedelta(days=1), t + timedelta(days=1))
    assert [e.id for e in got] == ["1", "2"]
    assert got[0].start == t and got[0].start.utcoffset() == timedelta(hours=-5)
    # upsert updates in place
    c.upsert_events("a@x.com", [ev("1", t, title="Changed")])
    got = c.events_between(t - timedelta(days=1), t + timedelta(days=1))
    assert len(got) == 2 and got[0].title == "Changed"


def test_replace_range_removes_deleted_events(tmp_path):
    c = Cache(tmp_path / "c.db")
    t = datetime(2026, 11, 4, 9, tzinfo=TZ)
    c.upsert_events("a@x.com", [ev("1", t), ev("2", t + timedelta(hours=2))])
    c.upsert_events("b@x.com", [ev("9", t, account="b@x.com")])
    c.replace_range("a@x.com", t - timedelta(hours=1), t + timedelta(days=1), [ev("2", t + timedelta(hours=2))])
    got = c.events_between(t - timedelta(days=1), t + timedelta(days=1))
    assert sorted(e.id for e in got) == ["2", "9"]


def test_all_day_event_roundtrips(tmp_path):
    c = Cache(tmp_path / "c.db")
    s = datetime(2026, 11, 5, tzinfo=TZ)
    e = ev("d", s, hours=24, all_day=True, meeting_url="https://meet.example/x", recurring_id="r1")
    c.upsert_events("a@x.com", [e])
    got = c.events_between(s - timedelta(days=1), s + timedelta(days=2))
    assert got == [e]
    assert got[0].all_day and got[0].start.tzinfo is not None
