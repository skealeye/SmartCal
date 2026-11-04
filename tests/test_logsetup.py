import logging
import sys
import threading

from smartcal.db import Cache
from smartcal.google_auth import ReauthRequired
from smartcal.live_sync import LiveSyncWorker
from smartcal.logsetup import guarded, install_excepthooks, setup_logging
from smartcal.models import CalendarInfo


def test_excepthook_logs_and_does_not_raise(tmp_path):
    old_sys, old_thr = sys.excepthook, threading.excepthook
    path = tmp_path / "smartcal.log"
    handler = setup_logging(path)
    try:
        install_excepthooks()
        try:
            raise ValueError("kaboom-main")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        def boom():
            raise KeyError("kaboom-thread")
        t = threading.Thread(target=boom, name="t1")
        t.start()
        t.join()

        @guarded
        def slot():
            raise RuntimeError("kaboom-slot")
        assert slot() is None
        handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "kaboom-main" in text and "kaboom-thread" in text and "kaboom-slot" in text
    finally:
        sys.excepthook, threading.excepthook = old_sys, old_thr
        logging.getLogger().removeHandler(handler)
        handler.close()


class OkClient:
    def list_calendars(self):
        return [CalendarInfo("primary", "Me", "#111", True)]

    def list_events(self, start, end):
        return []


def test_live_worker_isolates_bad_accounts(tmp_path):
    cache = Cache(tmp_path / "c.db")

    def client_for(account):
        if account == "reauth@x.com":
            raise ReauthRequired(account)
        if account == "weird@x.com":
            raise ZeroDivisionError
        return OkClient()

    w = LiveSyncWorker(cache, client_for,
                       accounts_fn=lambda: ["reauth@x.com", "weird@x.com", "ok@x.com"])
    seen = []
    w.status.connect(lambda a, s: seen.append((a, s)))
    w.sync_all()
    assert seen == [("reauth@x.com", "reauth"), ("weird@x.com", "error"), ("ok@x.com", "ok")]


def test_live_worker_survives_account_listing_failure(tmp_path):
    def bad():
        raise OSError("disk")
    w = LiveSyncWorker(Cache(tmp_path / "c.db"), lambda a: OkClient(), accounts_fn=bad)
    w.sync_all()  # must not raise
