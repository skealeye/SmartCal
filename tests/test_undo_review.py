import json
from datetime import datetime

import pytest

from fakes import FakeClient, cal, ev, http_err
from smartcal.actions import Actions
from smartcal.db import Cache
from smartcal.google_client import GoogleClient
from smartcal.undo import Change, UndoStore, snapshot


def _dt(d, h, m=0):
    return datetime(2026, 11, d, h, m).astimezone()


def make(tmp_path, events=()):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a@x.com", [cal("a@x.com")])
    cache.upsert_events("a@x.com", list(events))
    client = FakeClient("a@x.com", events)
    store = UndoStore(tmp_path / "undo.json")
    return Actions(lambda a: client, cache, store), client, cache, store


def test_undo_create_when_event_already_deleted(tmp_path):
    act, client, cache, store = make(tmp_path)
    new = act.create("a@x.com", title="Lunch", start=_dt(6, 12), end=_dt(6, 13))
    del client.store[("a@x.com", new.id)]
    client.log.clear()
    assert store.undo_last(act) == "Can't undo: the event no longer exists."
    assert len(store) == 0 and not cache.events_by_id(new.id) and client.log == []


def test_undo_cancelled_event_is_not_force_deleted(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act.update("e1", title="X")
    client.external_edit("a@x.com", "e1", status="cancelled")
    client.log.clear()
    assert store.undo_last(act, force=True).startswith("Can't undo")
    assert client.log == [] and len(store) == 0


def _real_delete_undo(tmp_path, status):
    from test_google_client import FakeService, _Boom
    svc = FakeService([])
    svc.get = lambda **kw: _Boom(http_err(404))
    svc.patch = lambda **kw: _Boom(http_err(status))
    store = UndoStore(tmp_path / "u.json")
    e = ev("me@x.com", "e1", cal="me@x.com")
    store.record(Change("delete", "me@x.com", "me@x.com", "e1", snapshot(e), None, "Dentist", "t"))
    act = Actions(lambda a: GoogleClient("me@x.com", service=svc),
                  Cache(tmp_path / "c.db"), store)
    return act, svc, store


def _inserts(svc):
    return [c for c in svc.calls if isinstance(c, tuple) and c[0] == "insert"]


def test_restore_403_does_not_insert_and_keeps_entry(tmp_path):
    act, svc, store = _real_delete_undo(tmp_path, 403)
    with pytest.raises(Exception):
        store.undo_last(act)
    assert not _inserts(svc) and len(store) == 1


def test_restore_500_does_not_insert(tmp_path):
    act, svc, store = _real_delete_undo(tmp_path, 500)
    with pytest.raises(Exception):
        store.undo_last(act)
    assert not _inserts(svc) and len(store) == 1


def test_restore_404_inserts_for_plain_event(tmp_path):
    act, svc, store = _real_delete_undo(tmp_path, 404)
    assert store.undo_last(act) == "Undone: restored Dentist"
    assert len(_inserts(svc)) == 1 and len(store) == 0


def test_undo_during_pending_write_does_not_report_undone(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act._journal("move", client.get_event("e1", calendar_id="a@x.com"))  # write not applied
    assert store.peek().pending
    line = store.undo_last(act)
    assert line.startswith("Nothing to undo yet") and "Undone" not in line
    assert len(store) == 0 and client.log == []


def test_pending_entry_after_crash_warns_instead_of_overwriting(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act._journal("move", client.get_event("e1", calendar_id="a@x.com"))
    client.update_event("e1", calendar_id="a@x.com", start=_dt(6, 9), end=_dt(6, 10),
                        all_day=False)           # write landed, finalize never ran
    store2 = UndoStore(tmp_path / "undo.json")   # "restart"
    assert store2.peek().pending and store2.peek().after_updated is None
    client.log.clear()
    assert store2.undo_last(act).startswith("Not undone")
    assert client.log == [] and len(store2) == 1
    assert store2.undo_last(act, force=True).startswith("Undone: moved Dentist")


def test_two_changes_two_undos_in_a_row(tmp_path):
    act, client, cache, store = make(tmp_path, [ev()])
    act.move("e1", _dt(6, 9), _dt(6, 10))
    act.update("e1", location="Office")
    assert store.undo_last(act) == "Undone: reverted changes to Dentist"
    line = store.undo_last(act)
    assert line == "Undone: moved Dentist back to Thu 6:00 PM", line
    got = client.store[("a@x.com", "e1")]
    assert got.start == _dt(5, 18) and got.location == ""


def _chg(i):
    return Change("create", "a", "c", f"e{i}", None, None, f"t{i}", "now")


def test_one_bad_entry_does_not_lose_the_rest(tmp_path):
    p = tmp_path / "u.json"
    store = UndoStore(p)
    store.record(_chg(1))
    store.record(_chg(2))
    data = json.loads(p.read_text(encoding="utf-8"))
    data["items"].insert(1, {"kind": "bogus"})
    p.write_text(json.dumps(data), encoding="utf-8")
    assert len(UndoStore(p)) == 2 and not (tmp_path / "u.json.corrupt").exists()
    p.write_text('{"items": "nope"}', encoding="utf-8")
    assert len(UndoStore(p)) == 0 and (tmp_path / "u.json.corrupt").exists()
