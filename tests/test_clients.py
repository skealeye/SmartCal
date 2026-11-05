"""C1: Google clients are never shared between threads; one client serializes its calls."""
import threading
import time
from datetime import datetime, timedelta, timezone

from smartcal.clients import ClientPool
from smartcal.google_client import GoogleClient


class _Probe:
    """A fake API service whose requests record overlapping execution."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def _req(self, result):
        probe = self

        class Req:
            def execute(self_inner):
                with probe.lock:
                    probe.active += 1
                    probe.max_active = max(probe.max_active, probe.active)
                time.sleep(0.01)
                with probe.lock:
                    probe.active -= 1
                return result
        return Req()

    def calendarList(self):
        return self

    def events(self):
        return self

    def list(self, **kw):
        if "calendarId" in kw:
            return self._req({"items": [{"id": "e1", "summary": "A",
                                         "start": {"dateTime": "2026-11-04T09:00:00+00:00"},
                                         "end": {"dateTime": "2026-11-04T10:00:00+00:00"}}]})
        return self._req({"items": [{"id": "me@x.com", "summary": "Me", "primary": True}]})

    def patch(self, **kw):
        return self._req({"id": kw["eventId"], "summary": "B",
                          "start": {"dateTime": "2026-11-04T11:00:00+00:00"},
                          "end": {"dateTime": "2026-11-04T12:00:00+00:00"}})


def _in_thread(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join(5)
    return out[0]


def test_two_threads_get_different_clients_and_services():
    built = []

    class Svc:
        pass

    def factory(account):
        c = GoogleClient(account, service=Svc())
        built.append(c)
        return c

    pool = ClientPool(factory)
    main = pool.get("a@x.com")
    assert pool.get("a@x.com") is main                 # same thread: cached
    other = _in_thread(lambda: pool.get("a@x.com"))
    assert other is not main and other.service is not main.service
    assert pool.get("b@x.com") is not main             # per account too


def test_clear_drops_every_thread_client():
    pool = ClientPool(lambda a: object())
    first = pool.get("a@x.com")
    _in_thread(lambda: pool.get("a@x.com"))
    pool.clear()
    assert len(pool) == 0
    assert pool.get("a@x.com") is not first


def test_clients_of_finished_threads_are_pruned():
    pool = ClientPool(lambda a: object())
    for _ in range(5):
        _in_thread(lambda: pool.get("a@x.com"))
    pool.get("a@x.com")
    assert len(pool) == 1   # only the live (main) thread's client is kept


def test_concurrent_calls_on_one_client_do_not_interleave():
    probe = _Probe()
    client = GoogleClient("me@x.com", service=probe)
    start = datetime(2026, 11, 4, tzinfo=timezone.utc)
    errors = []

    def lister():
        try:
            for _ in range(10):
                client.list_events(start, start + timedelta(days=1))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def patcher():
        try:
            for _ in range(10):
                client.update_event("e1", calendar_id="me@x.com", title="B")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=lister), threading.Thread(target=patcher)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert probe.max_active == 1


def test_real_service_is_built_with_a_20_second_http_timeout(monkeypatch):
    import googleapiclient.discovery
    from smartcal import google_auth
    seen = {}

    class Creds:
        pass

    def fake_build(name, version, **kw):
        seen.update(kw)
        return object()

    monkeypatch.setattr(google_auth, "load_credentials", lambda account: Creds())
    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    GoogleClient("me@x.com").service
    http = seen["http"]
    assert "credentials" not in seen
    assert http.http.timeout == 20
    assert isinstance(http.credentials, Creds)
