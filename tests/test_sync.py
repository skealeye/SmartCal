from datetime import datetime, timedelta

import pytest
from googleapiclient.errors import HttpError

from smartcal.db import Cache
from smartcal.google_auth import ReauthRequired
from smartcal.models import CalendarInfo, Event
from smartcal.settings import Settings
from smartcal.sync import SyncWorker


def _ev(id, cal="p"):
    s = datetime.now().astimezone() + timedelta(hours=1)
    return Event(id=id, account="a", calendar_id=cal, calendar_name=cal, color="#1",
                 title=id, start=s, end=s + timedelta(hours=1))


class FakeClient:
    def __init__(self, events=(), error=None):
        self.events, self.error = list(events), error

    def list_calendars(self):
        if self.error:
            raise self.error
        return [CalendarInfo("p", "Me", "#1", True), CalendarInfo("ooo", "OOO", "#2", False)]

    def list_events(self, start, end):
        if self.error:
            raise self.error
        return self.events


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "c.db")


def worker(cache, client, tmp_path):
    return SyncWorker(cache, {"a": client}, Settings(tmp_path / "s.json"))


def _cached(cache):
    now = datetime.now().astimezone()
    return [e.id for e in cache.events_between(now, now + timedelta(days=1))]


def test_sync_ok_stores_events_and_calendars(cache, tmp_path):
    w = worker(cache, FakeClient([_ev("1"), _ev("2", "ooo")]), tmp_path)
    assert w.sync_account("a") == "ok"
    assert sorted(c.id for c in cache.calendars()) == ["ooo", "p"]
    assert _cached(cache) == ["1", "2"]  # hidden calendars are stored too


def test_sync_offline_keeps_cached_events_and_reports_status(cache, tmp_path):
    cache.upsert_events("a", [_ev("old")])
    resp = type("R", (), {"status": 503, "reason": "x"})()
    for err in (OSError("down"), HttpError(resp, b"")):
        w = worker(cache, FakeClient(error=err), tmp_path)
        assert w.sync_account("a") == "offline"
    assert _cached(cache) == ["old"]


def test_sync_expired_token_reports_reauth(cache, tmp_path):
    cache.upsert_events("a", [_ev("old")])
    w = worker(cache, FakeClient(error=ReauthRequired("x")), tmp_path)
    assert w.sync_account("a") == "reauth"
    assert _cached(cache) == ["old"]


def test_worker_emits_signals(qtbot, cache, tmp_path):
    w = worker(cache, FakeClient([_ev("1")]), tmp_path)
    seen = []
    w.status.connect(lambda a, s: seen.append((a, s)))
    with qtbot.waitSignal(w.synced, timeout=5000):
        w.start()
    w.stop()
    w.wait(5000)
    assert seen[0] == ("a", "ok")


def test_unhiding_calendar_shows_cached_events_without_resync(cache, tmp_path):
    from smartcal import agenda
    st = Settings(tmp_path / "s.json")
    w = SyncWorker(cache, {"a": FakeClient([_ev("1"), _ev("2", "ooo")])}, st)
    assert w.sync_account("a") == "ok"
    now = datetime.now().astimezone()
    assert agenda.next_event(cache, st, now).id == "1"
    st.set_hidden("p", True)
    st.set_hidden("ooo", False)
    assert agenda.next_event(cache, st, now).id == "2"


def test_dns_failure_is_offline(cache, tmp_path):
    import httplib2
    for err in (httplib2.ServerNotFoundError("dns"), TimeoutError("t")):
        assert worker(cache, FakeClient(error=err), tmp_path).sync_account("a") == "offline"


def test_http_401_is_reauth(cache, tmp_path):
    resp = type("R", (), {"status": 401, "reason": "x"})()
    assert worker(cache, FakeClient(error=HttpError(resp, b"")), tmp_path).sync_account("a") == "reauth"


def test_unexpected_exception_is_error_status_and_logged(cache, tmp_path, caplog):
    cache.upsert_events("a", [_ev("old")])
    resp = type("R", (), {"status": 403, "reason": "x"})()
    with caplog.at_level("WARNING"):
        assert worker(cache, FakeClient(error=ValueError("boom")), tmp_path).sync_account("a") == "error"
        assert worker(cache, FakeClient(error=HttpError(resp, b"")), tmp_path).sync_account("a") == "error"
    assert "boom" in caplog.text
    assert _cached(cache) == ["old"]


def test_worker_can_restart_after_stop(qtbot, cache, tmp_path):
    w = worker(cache, FakeClient([_ev("1")]), tmp_path)
    with qtbot.waitSignal(w.synced, timeout=5000):
        w.start()
    w.stop()
    w.wait(5000)
    with qtbot.waitSignal(w.synced, timeout=5000):
        w.start()
    w.stop()
    w.wait(5000)
