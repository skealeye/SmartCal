"""Background sync of Google calendars into the local cache."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from PyQt6.QtCore import QThread, pyqtSignal

from .google_auth import ReauthRequired

log = logging.getLogger(__name__)


def _classify(exc: BaseException) -> str:
    """Map an exception to "offline", "reauth" or "error"."""
    import httplib2
    from googleapiclient.errors import HttpError
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        if status == 401:
            return "reauth"
        return "offline" if status >= 500 else "error"
    if isinstance(exc, (OSError, TimeoutError, httplib2.HttpLib2Error)):
        return "offline"
    return "error"


class SyncWorker(QThread):
    synced = pyqtSignal(str)
    status = pyqtSignal(str, str)

    def __init__(self, cache, clients, settings=None, interval_seconds: int = 300, parent=None):
        super().__init__(parent)
        self.cache = cache
        self._clients = clients
        self.settings = settings
        self.interval_seconds = interval_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()

    def _accounts(self) -> list[str]:
        return list(self._clients) if isinstance(self._clients, dict) else []

    def _client(self, account: str):
        return self._clients[account] if isinstance(self._clients, dict) else self._clients(account)

    def sync_account(self, account: str) -> str:
        """One sync of one account; returns "ok", "offline", "reauth" or "error".

        Stores every calendar's events (hidden ones too); hiding is a read-time filter.
        """
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end = now + timedelta(days=14)
        try:
            client = self._client(account)
            calendars = client.list_calendars()
            events = client.list_events(start, end)
        except ReauthRequired:
            return "reauth"
        except Exception as exc:  # noqa: BLE001 - classified below
            result = _classify(exc)
            if result == "error":
                log.exception("sync of %s failed unexpectedly", account)
            else:
                log.warning("sync of %s: %s (%s)", account, result, exc)
            return result
        self.cache.upsert_calendars(account, calendars)
        self.cache.replace_range(account, start, end, events)
        return "ok"

    def sync_all(self) -> None:
        for account in self._accounts():
            if self._stop.is_set():
                return
            try:
                result = self.sync_account(account)
            except Exception:  # noqa: BLE001 - never kill the worker thread
                log.exception("sync of %s crashed", account)
                result = "error"
            self.status.emit(account, result)
            if result == "ok":
                self.synced.emit(account)

    def start(self, *args, **kwargs) -> None:
        self._stop.clear()
        super().start(*args, **kwargs)

    def sync_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self.sync_all()
            self._wake.wait(self.interval_seconds)
