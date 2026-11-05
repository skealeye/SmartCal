from datetime import date, datetime, timedelta

import pytest

from fakes import FakeClient, cal, ev
from smartcal.actions import (Actions, AttendeesRequireConfirmationError, EventNotFoundError,
                              SeriesRefusedError)
from smartcal.db import Cache
from smartcal.google_client import GoogleClient
from smartcal.undo import UndoStore


def _dt(d, h, m=0):
    return datetime(2026, 11, d, h, m).astimezone()


# A fixed present well before the scenario day (Nov 5): never the real clock.
BEFORE = _dt(3, 9)


def make(tmp_path, events=(), accounts=("a@x.com",)):
    cache = Cache(tmp_path / "c.db")
    clients = {a: FakeClient(a, [e for e in events if e.account == a]) for a in accounts}
    for a in accounts:
        cache.upsert_calendars(a, [cal(a)])
    for e in events:
        cache.upsert_events(e.account, [e])
    store = UndoStore(tmp_path / "undo.json")
    return Actions(lambda a: clients[a], cache, store), clients, cache, store


def test_move_missing_event_raises_not_found_and_drops_cache_row(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev()])
    del clients["a@x.com"].store[("a@x.com", "e1")]          # gone from Google
    with pytest.raises(EventNotFoundError):
        act.move("e1", _dt(6, 9), _dt(6, 10))
    assert cache.events_by_id("e1") == [] and len(store) == 0


def test_cancelled_event_not_writable(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev()])
    clients["a@x.com"].external_edit("a@x.com", "e1", status="cancelled")
    for call in (lambda: act.move("e1", _dt(6, 9), _dt(6, 10)),
                 lambda: act.update("e1", title="x"), lambda: act.delete("e1")):
        with pytest.raises(EventNotFoundError):
            call()
    assert cache.events_by_id("e1") == [] and len(store) == 0
    assert not [e for e in clients["a@x.com"].log if e[0] in ("update", "delete")]


def _master():
    return ev(id="s1", is_series_master=True, recurrence=["RRULE:FREQ=WEEKLY"])


def test_delete_series_master_refused(tmp_path):
    act, clients, cache, store = make(tmp_path, [_master()])
    with pytest.raises(SeriesRefusedError):
        act.delete("s1")
    with pytest.raises(SeriesRefusedError):
        act.move("s1", _dt(6, 9), _dt(6, 10))
    with pytest.raises(SeriesRefusedError):
        act.update("s1", title="x")
    assert clients["a@x.com"].store and len(store) == 0
    act.delete("s1", allow_series=True)
    assert not clients["a@x.com"].store


def test_delete_series_master_refused_when_account_and_calendar_given(tmp_path):
    act, clients, cache, store = make(tmp_path, [_master()])
    cache.delete_event("a@x.com", "a@x.com", "s1")           # not in cache at all
    with pytest.raises(SeriesRefusedError):
        act.delete("s1", account="a@x.com", calendar_id="a@x.com")
    assert clients["a@x.com"].store and len(store) == 0


def test_series_instance_still_allowed(tmp_path):
    inst = ev(id="s1_20261105", recurring_id="s1")
    act, clients, cache, store = make(tmp_path, [inst])
    act.move("s1_20261105", _dt(5, 10), _dt(5, 11))
    act.delete("s1_20261105")
    assert len(store) == 2


def _svc_with_attendees():
    from test_google_client import FakeService, _Req, _ev
    svc = FakeService([])
    svc.get = lambda **kw: _Req(_ev(id=kw["eventId"], attendees=[
        {"email": "me@x.com", "self": True}, {"email": "boss@y.com"}]))
    return svc


def _real_actions(tmp_path, svc):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("me@x.com", [cal("me@x.com")])
    cache.upsert_events("me@x.com", [ev("me@x.com", "e1", cal="me@x.com")])
    client = GoogleClient("me@x.com", service=svc)
    return Actions(lambda a: client, cache, UndoStore(tmp_path / "u.json"))


def test_move_event_with_attendees_requires_confirmation(tmp_path):
    svc = _svc_with_attendees()
    act = _real_actions(tmp_path, svc)
    with pytest.raises(AttendeesRequireConfirmationError) as ei:
        act.move("e1", _dt(6, 9), _dt(6, 10))
    assert ei.value.event.attendee_count == 1
    with pytest.raises(AttendeesRequireConfirmationError):
        act.delete("e1")
    assert not [c for c in svc.calls if isinstance(c, tuple)] and len(act.undo) == 0


def test_allow_attendees_flag_proceeds_and_sends_updates_none(tmp_path):
    svc = _svc_with_attendees()
    act = _real_actions(tmp_path, svc)
    act.move("e1", _dt(6, 9), _dt(6, 10), allow_attendees=True)
    act.delete("e1", allow_attendees=True)
    writes = [c for c in svc.calls if isinstance(c, tuple)]
    assert [w[0] for w in writes] == ["patch", "delete"]
    assert all(w[1]["sendUpdates"] == "none" for w in writes)


def test_update_rejects_none_and_empty_title(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev()])
    for bad in ({"title": ""}, {"title": "  "}, {"title": None}, {"location": None},
                {"description": None}):
        with pytest.raises(ValueError):
            act.update("e1", **bad)
    assert len(store) == 0 and not clients["a@x.com"].log
    assert act.update("e1", location="").location == ""


def test_placeholder_title_is_not_written_back(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev(title="(no title)")])
    act.move("e1", _dt(6, 9), _dt(6, 10))
    assert store.peek().before["title"] == ""
    store.undo_last(act)
    assert clients["a@x.com"].store[("a@x.com", "e1")].title == ""


def test_free_slots_ignores_free_events(tmp_path):
    act, *_ = make(tmp_path, [ev(id="1", start=_dt(5, 10), end=_dt(5, 11), busy=False)])
    assert act.free_slots(date(2026, 11, 5), timedelta(hours=1), now=BEFORE) == [(_dt(5, 9), _dt(5, 17))]


def test_busy_flag_survives_cache_roundtrip(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev(busy=False)])
    assert cache.events_by_id("e1")[0].busy is False


def test_free_slots_ignores_zero_length_event(tmp_path):
    act, *_ = make(tmp_path, [ev(id="1", start=_dt(5, 12), end=_dt(5, 12))])
    assert act.free_slots(date(2026, 11, 5), timedelta(hours=1), now=BEFORE) == [(_dt(5, 9), _dt(5, 17))]


def test_free_slots_today_excludes_past(tmp_path):
    act, *_ = make(tmp_path, [ev(id="1", start=_dt(5, 14), end=_dt(5, 15))])
    now = _dt(5, 11, 20)
    assert act.free_slots(date(2026, 11, 5), timedelta(hours=1), now=now) == [
        (now, _dt(5, 14)), (_dt(5, 15), _dt(5, 17))]
    assert act.free_slots(date(2026, 11, 5), timedelta(hours=1), now=_dt(5, 18)) == []


def test_suite_has_no_wall_clock_dependence_for_free_slots(tmp_path):
    """The free_slots scenarios with the injected clock the day BEFORE the scenario day
    (it is still ahead: every slot offered) and the day AFTER (it is past: nothing
    offered), whatever the real date is."""
    day = date(2026, 11, 5)
    scenarios = [  # (events, duration, slots while the day is still ahead)
        ([ev(id="1", start=_dt(5, 10), end=_dt(5, 11)),
          ev(id="2", start=_dt(5, 13), end=_dt(5, 14))], timedelta(hours=1),
         [(_dt(5, 9), _dt(5, 10)), (_dt(5, 11), _dt(5, 13)), (_dt(5, 14), _dt(5, 17))]),
        ([ev(id="1", start=_dt(5, 8), end=_dt(5, 10)),
          ev(id="2", start=_dt(5, 9, 30), end=_dt(5, 11)),
          ev(id="3", start=_dt(5, 16), end=_dt(5, 18))], timedelta(minutes=30),
         [(_dt(5, 11), _dt(5, 16))]),
        ([ev(id="t", all_day=True, start=_dt(5, 0), end=_dt(6, 0))], timedelta(hours=1),
         [(_dt(5, 9), _dt(5, 17))]),
        ([ev(id="1", start=_dt(5, 10), end=_dt(5, 11), busy=False)], timedelta(hours=1),
         [(_dt(5, 9), _dt(5, 17))]),
        ([ev(id="1", start=_dt(5, 12), end=_dt(5, 12))], timedelta(hours=1),
         [(_dt(5, 9), _dt(5, 17))]),
    ]
    day_before = [_dt(4, 0, 1), _dt(4, 12), _dt(4, 23, 59)]
    day_after = [_dt(6, 0, 1), _dt(6, 12), _dt(6, 23, 59)]
    for i, (events, duration, ahead) in enumerate(scenarios):
        sub = tmp_path / str(i)
        sub.mkdir()
        act, *_ = make(sub, events)
        for now in day_before:
            assert act.free_slots(day, duration, now=now) == ahead, (i, now)
        for now in day_after:
            assert act.free_slots(day, duration, now=now) == [], (i, now)
