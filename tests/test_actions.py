from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fakes import FakeClient, cal, ev
from smartcal.actions import Actions, AmbiguousEventError, EventNotFoundError
from smartcal.db import Cache
from smartcal.settings import Settings
from smartcal.undo import UndoStore

NY = ZoneInfo("America/New_York")


def _dt(d, h, m=0):
    return datetime(2026, 11, d, h, m).astimezone()


# A fixed present well before the scenario day (Nov 5): never the real clock.
BEFORE = _dt(3, 9)


def make(tmp_path, events=(), settings=None, accounts=("a@x.com",)):
    cache = Cache(tmp_path / "c.db")
    clients = {a: FakeClient(a, [e for e in events if e.account == a]) for a in accounts}
    for a in accounts:
        cache.upsert_calendars(a, [cal(a)])
    for e in events:
        cache.upsert_events(e.account, [e])
    store = UndoStore(tmp_path / "undo.json")
    return Actions(lambda a: clients[a], cache, store, settings), clients, cache, store


def test_move_recurring_instance_leaves_series_alone(tmp_path):
    series = ev(id="s1", title="Standup")
    inst = ev(id="s1_20261105", title="Standup", recurring_id="s1")
    act, clients, cache, store = make(tmp_path, [series, inst])
    act.move("s1_20261105", _dt(5, 10), _dt(5, 11))
    c = clients["a@x.com"]
    assert c.store[("a@x.com", "s1")].start == _dt(5, 18)
    assert c.store[("a@x.com", "s1_20261105")].start == _dt(5, 10)
    assert [entry[1] for entry in c.log if entry[0] == "update"] == ["s1_20261105"]


def test_delete_instance_removes_only_that_occurrence(tmp_path):
    series = ev(id="s1")
    inst = ev(id="s1_20261105", recurring_id="s1")
    act, clients, cache, store = make(tmp_path, [series, inst])
    act.delete("s1_20261105")
    assert ("a@x.com", "s1") in clients["a@x.com"].store
    assert cache.events_by_id("s1") and not cache.events_by_id("s1_20261105")


def test_failed_write_records_no_undo_entry(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev()])
    c = clients["a@x.com"]
    for call in (lambda: act.move("e1", _dt(6, 9), _dt(6, 10)),
                 lambda: act.update("e1", title="X"),
                 lambda: act.delete("e1")):
        c.fail_next = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            call()
        assert len(store) == 0
    c.fail_next = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        act.create("a@x.com", title="N", start=_dt(6, 9), end=_dt(6, 10))
    assert len(store) == 0 and cache.events_by_id("e1")[0].title == "Dentist"


def test_ambiguous_event_id_raises_with_candidates(tmp_path):
    a, b = ev("a@x.com", "same"), ev("b@x.com", "same", cal="b@x.com")
    act, clients, cache, store = make(tmp_path, [a, b], accounts=("a@x.com", "b@x.com"))
    with pytest.raises(AmbiguousEventError) as ei:
        act.delete("same")
    assert {c.account for c in ei.value.candidates} == {"a@x.com", "b@x.com"}
    assert len(store) == 0 and clients["a@x.com"].store and clients["b@x.com"].store
    act.delete("same", account="b@x.com")
    assert clients["a@x.com"].store and not clients["b@x.com"].store


def test_event_not_found(tmp_path):
    act, *_ = make(tmp_path)
    with pytest.raises(EventNotFoundError):
        act.delete("nope")
    with pytest.raises(EventNotFoundError):
        act.update("nope", title="x")


def test_create_rejects_naive_or_inverted_times(tmp_path):
    act, clients, *_ = make(tmp_path)
    with pytest.raises(ValueError):
        act.create("a@x.com", title="x", start=datetime(2026, 11, 6, 9), end=_dt(6, 10))
    with pytest.raises(ValueError):
        act.create("a@x.com", title="x", start=_dt(6, 10), end=_dt(6, 9))
    with pytest.raises(ValueError):
        act.create("a@x.com", title="x", start=_dt(6, 10), end=_dt(6, 10))
    assert not clients["a@x.com"].store


def test_create_defaults_to_primary_calendar(tmp_path):
    act, clients, cache, store = make(tmp_path)
    cache.upsert_calendars("a@x.com", [cal("other", False), cal("a@x.com", True)])
    e = act.create("a@x.com", title="Lunch", start=_dt(6, 12), end=_dt(6, 13))
    assert e.calendar_id == "a@x.com"
    e2 = act.create("a@x.com", calendar_id="other", title="L2", start=_dt(6, 12), end=_dt(6, 13))
    assert e2.calendar_id == "other"


def test_delete_removes_from_cache(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev()])
    act.delete("e1")
    assert cache.events_by_id("e1") == [] and store.peek().kind == "delete"


def test_find_excludes_hidden_calendars(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.set_hidden("b@x.com", True)
    a, b = ev("a@x.com", "1", "Dentist"), ev("b@x.com", "2", "Dentist two", cal="b@x.com")
    act, *_ = make(tmp_path, [a, b], settings=s, accounts=("a@x.com", "b@x.com"))
    assert [e.id for e in act.find("DENTIST")] == ["1"]
    assert [e.id for e in act.find("dentist", day=date(2026, 11, 5))] == ["1"]
    assert act.find("dentist", day=date(2026, 11, 6)) == []
    assert [e.id for e in act.find("cal a@x")] == ["1"]   # calendar name


def test_write_uses_current_google_state_not_cache(tmp_path):
    act, clients, cache, store = make(tmp_path, [ev()])   # cache says "Dentist"
    clients["a@x.com"].external_edit("a@x.com", "e1", title="Dentist (new office)")
    act.move("e1", _dt(6, 9), _dt(6, 10))
    assert store.peek().before["title"] == "Dentist (new office)"
    store.undo_last(act)
    assert clients["a@x.com"].store[("a@x.com", "e1")].title == "Dentist (new office)"


def test_actions_never_write_to_other_account(tmp_path):
    a, b = ev("a@x.com", "e1"), ev("b@x.com", "e2", cal="b@x.com")
    act, clients, cache, store = make(tmp_path, [a, b], accounts=("a@x.com", "b@x.com"))
    act.move("e1", _dt(6, 9), _dt(6, 10))
    act.update("e1", title="T")
    act.create("a@x.com", title="N", start=_dt(6, 9), end=_dt(6, 10))
    store.undo_last(act)
    assert clients["b@x.com"].log == []
    assert clients["b@x.com"].store[("b@x.com", "e2")].title == "Dentist"


def test_update_validates_fields(tmp_path):
    act, *_ = make(tmp_path, [ev()])
    with pytest.raises(ValueError):
        act.update("e1", color="red")
    with pytest.raises(ValueError):
        act.update("e1", start=_dt(5, 20))      # end 19:00 would precede start
    assert act.update("e1", location="Here").location == "Here"


def test_all_day_move_uses_dates(tmp_path):
    trip = ev(id="t", title="Trip", all_day=True, start=_dt(5, 0), end=_dt(6, 0))
    act, clients, *_ = make(tmp_path, [trip])
    got = act.move("t", date(2026, 11, 10), date(2026, 11, 11))
    assert got.all_day and got.start.date() == date(2026, 11, 10)


# -- free slots ---------------------------------------------------------------
def test_free_slots_respects_all_accounts_and_working_hours(tmp_path):
    evs = [ev("a@x.com", "1", start=_dt(5, 10), end=_dt(5, 11)),
           ev("b@x.com", "2", start=_dt(5, 13), end=_dt(5, 14), cal="b@x.com"),
           ev("a@x.com", "3", start=_dt(5, 7), end=_dt(5, 8))]   # before hours
    act, *_ = make(tmp_path, evs, accounts=("a@x.com", "b@x.com"))
    slots = act.free_slots(date(2026, 11, 5), timedelta(hours=1), now=BEFORE)
    assert slots == [(_dt(5, 9), _dt(5, 10)), (_dt(5, 11), _dt(5, 13)), (_dt(5, 14), _dt(5, 17))]
    assert act.free_slots(date(2026, 11, 5), timedelta(hours=3), now=BEFORE) == [(_dt(5, 14), _dt(5, 17))]


def test_free_slots_partial_overlap_and_merge(tmp_path):
    evs = [ev(id="1", start=_dt(5, 8), end=_dt(5, 10)),            # spills into window
           ev(id="2", start=_dt(5, 9, 30), end=_dt(5, 11)),        # overlaps previous
           ev(id="3", start=_dt(5, 16), end=_dt(5, 18))]           # spills out
    act, *_ = make(tmp_path, evs)
    assert act.free_slots(date(2026, 11, 5), timedelta(minutes=30), now=BEFORE) == [(_dt(5, 11), _dt(5, 16))]


def test_free_slots_ignores_all_day_events(tmp_path):
    trip = ev(id="t", all_day=True, start=_dt(5, 0), end=_dt(6, 0))
    act, *_ = make(tmp_path, [trip])
    assert act.free_slots(date(2026, 11, 5), timedelta(hours=1), now=BEFORE) == [(_dt(5, 9), _dt(5, 17))]


def test_free_slots_dst_day(tmp_path):
    # 2026-11-01 is the 25-hour fall-back day in New York.
    busy = ev(id="1", start=datetime(2026, 11, 1, 12, tzinfo=NY),
              end=datetime(2026, 11, 1, 13, tzinfo=NY))
    act, *_ = make(tmp_path, [busy])
    now = datetime(2026, 10, 1, tzinfo=NY)
    slots = act.free_slots(date(2026, 11, 1), timedelta(hours=1), (0, 24), now=now)
    total = sum((e - s for s, e in slots), timedelta())
    assert total == timedelta(hours=24)                      # 25h day minus 1h busy
    assert slots[0][0] == datetime(2026, 11, 1, 0, tzinfo=NY)
    assert slots[-1][1] == datetime(2026, 11, 2, 0, tzinfo=NY)
