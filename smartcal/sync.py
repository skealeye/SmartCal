"""Background sync of Google calendars into the local cache."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QThread, pyqtSignal

from . import agenda
from .google_auth import ReauthRequired

log = logging.getLogger(__name__)

# The synced range: local days [past edge, today + SYNC_FUTURE_DAYS], where the past
# edge is the earlier of today - SYNC_PAST_DAYS and the first day of the month grid
# that contains today (so the current month view never has unsynced days).
# Everything that reads or prunes the cache (window, assistant, sync) uses sync_window.
SYNC_PAST_DAYS = 7
SYNC_FUTURE_DAYS = 60
# An account whose last successful sync is older than this is not trusted for
# availability: its token may be revoked or the PC was offline.
STALE_AFTER = timedelta(hours=2)


def ago_text(delta: timedelta) -> str:
    """'just now', '5 minutes', '3 hours', '2 days' (no 'ago')."""
    secs = max(0, int(delta.total_seconds()))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size * (2 if unit == "day" else 1):
            n = secs // size
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return "less than a minute"


def synced_days(now: datetime) -> tuple[date, date]:
    """First and last local day (both inclusive) that a sync at `now` covers."""
    from .ui.layout import month_grid   # pure date math, no widgets
    today = agenda.local_date(now)
    first = min(today - timedelta(days=SYNC_PAST_DAYS), month_grid(today.year, today.month)[0])
    return first, today + timedelta(days=SYNC_FUTURE_DAYS)


def sync_window(now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the synced range: local midnights, DST-safe. The one window
    used by sync, prune, the window's banner and the recorded sync state."""
    first, last = synced_days(now)
    return agenda.midnight(first, now), agenda.midnight(last + timedelta(days=1), now)


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

    def __init__(self, cache, client_for, interval_seconds: int = 300, parent=None):
        super().__init__(parent)
        self.cache = cache
        self._client_for = client_for   # account -> GoogleClient
        self.interval_seconds = interval_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.clock = lambda: datetime.now().astimezone()

    def _accounts(self) -> list[str]:
        return []   # LiveSyncWorker reads the stored accounts

    def _client(self, account: str):
        return self._client_for(account)

    def sync_account(self, account: str) -> str:
        """One sync of one account; returns "ok", "offline", "reauth" or "error".

        Stores every calendar's events (hidden ones too); hiding is a read-time filter.
        """
        now = self.clock()
        start, end = sync_window(now)
        try:
            client = self._client(account)
            calendars = client.list_calendars()
            events = client.list_events(start, end, calendars=calendars)
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
        self.cache.prune_outside(account, start, end)   # days that left the window
        self.cache.record_sync(account, start, end, now)   # only after a full success
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
