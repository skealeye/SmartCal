import json
from datetime import datetime

import pytest

from fakes import FakeClient, cal, ev
from smartcal.actions import Actions
from smartcal.db import Cache
from smartcal.undo import Change, UndoStore


def _dt(d, h, m=0):
    return datetime(2026, 11, d, h, m).astimezone()


def make(tmp_path, events=(), limit=20):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a@x.com", [cal("a@x.com")])
    cache.upsert_events("a@x.com", list(events))
    client = FakeClient("a@x.com", events)
    store = UndoStore(tmp_path / "undo.json", limit=limit)
    return Actions(lambda a: client, cache, store), client, cache, store


def test_move_records_undo_and_undo_restores(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    moved = act.move("e1", _dt(6, 9), _dt(6, 10))
    assert moved.start == _dt(6, 9) and len(store) == 1
    assert store.peek().kind == "move" and store.peek().before["title"] == "Dentist"
    line = store.undo_last(act)
    assert line == "Undone: moved Dentist back to Thu 6:00 PM"
    assert client.store[("a@x.com", "e1")].start == _dt(5, 18)
    assert cache.events_by_id("e1")[0].start == _dt(5, 18)
    assert len(store) == 0


def test_undo_create_deletes_event(tmp_path):
    act, client, cache, store = make(tmp_path)
    new = act.create("a@x.com", title="Lunch", start=_dt(6, 12), end=_dt(6, 13))
    assert cache.events_by_id(new.id)
    assert store.undo_last(act) == "Undone: removed Lunch"
    assert ("a@x.com", new.id) not in client.store and not cache.events_by_id(new.id)


def test_undo_delete_recreates_event(tmp_path):
    act, client, cache, store = make(tmp_path, [ev(location="Main St")])
    act.delete("e1")
    assert not client.store and not cache.events_by_id("e1")
    assert store.undo_last(act) == "Undone: restored Dentist"
    back = client.store[("a@x.com", "e1")]
    assert (back.title, back.location, back.start) == ("Dentist", "Main St", _dt(5, 18))
    assert cache.events_by_id("e1")


def test_undo_persists_across_restart(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act.move("e1", _dt(6, 9), _dt(6, 10))
    store2 = UndoStore(tmp_path / "undo.json")
    assert len(store2) == 1 and store2.peek().event_id == "e1"
    assert store2.undo_last(act).startswith("Undone: moved Dentist")
    assert len(UndoStore(tmp_path / "undo.json")) == 0


def _chg(i):
    return Change("create", "a", "c", f"e{i}", None, None, f"t{i}", "now")


def test_undo_limit_20(tmp_path):
    store = UndoStore(tmp_path / "u.json")
    for i in range(25):
        store.record(_chg(i))
    assert len(store) == 20 and store.peek().event_id == "e24"
    again = UndoStore(tmp_path / "u.json")
    assert len(again) == 20 and again._items[0].event_id == "e5"


def test_undo_warns_if_event_changed_elsewhere(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act.move("e1", _dt(6, 9), _dt(6, 10))
    client.external_edit("a@x.com", "e1", title="Dentist (moved by spouse)")
    line = store.undo_last(act)
    assert line.startswith("Not undone: Dentist was changed elsewhere")
    assert client.store[("a@x.com", "e1")].start == _dt(6, 9)   # untouched
    assert len(store) == 1                                        # entry kept


def test_undo_force_overwrites_after_warning(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act.move("e1", _dt(6, 9), _dt(6, 10))
    client.external_edit("a@x.com", "e1", location="Elsewhere")
    assert store.undo_last(act).startswith("Not undone")
    assert store.undo_last(act, force=True).startswith("Undone: moved Dentist")
    assert client.store[("a@x.com", "e1")].start == _dt(5, 18) and len(store) == 0


def test_corrupt_undo_file_backed_up(tmp_path):
    p = tmp_path / "undo.json"
    p.write_text("{not json", encoding="utf-8")
    store = UndoStore(p)
    assert len(store) == 0 and (tmp_path / "undo.json.corrupt").exists()
    p.write_text(json.dumps({"items": [{"kind": "bogus"}]}), encoding="utf-8")
    assert len(UndoStore(p)) == 0


def test_undo_empty_store(tmp_path):
    act, *_ = make(tmp_path)
    assert act.undo.undo_last(act) == "Nothing to undo."


def test_failed_undo_keeps_entry(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act.move("e1", _dt(6, 9), _dt(6, 10))
    client.fail_next = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        store.undo_last(act)
    assert len(store) == 1
