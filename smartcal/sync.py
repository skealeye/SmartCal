"""Background sync of Google calendars into the local cache."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from PyQt6.QtCore import QThread, pyqtSignal

from .google_auth import ReauthRequired


def _is_offline_error(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", 0) >= 500
    return isinstance(exc, OSError)


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
        """One sync of one account; returns "ok", "offline" or "reauth"."""
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end = now + timedelta(days=14)
        try:
            client = self._client(account)
            calendars = client.list_calendars()
            events = client.list_events(start, end)
        except ReauthRequired:
            return "reauth"
        except Exception as exc:  # noqa: BLE001 - classify, re-raise unknowns
            if _is_offline_error(exc):
                return "offline"
            raise
        if self.settings is not None:
            primary = {c.id: c.primary for c in calendars}
            events = [e for e in events
                      if not self.settings.is_hidden(e.calendar_id, primary.get(e.calendar_id, False))]
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
                result = "offline"
            self.status.emit(account, result)
            if result == "ok":
                self.synced.emit(account)

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
