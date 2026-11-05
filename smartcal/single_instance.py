"""One SmartCal per user and data folder.

A QLockFile in the data folder decides who is first (Qt treats a lock left by a
dead process as stale). The first instance also listens on a QLocalServer; a
second launch connects to it, which makes the first show its popup, and exits.

Both are keyed by the data folder, so a test run with SMARTCAL_DATA_DIR set to a
temp folder never collides with the owner's real instance.
"""
from __future__ import annotations

import getpass
import hashlib
import logging
import os
import re
from pathlib import Path

from PyQt6.QtCore import QLockFile, QObject, QThread, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

LOCK_NAME = "smartcal.lock"
CONNECT_TIMEOUT_MS = 1000
CONNECT_TRIES = 5


def _user() -> str:
    try:
        name = os.environ.get("USERNAME") or getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name: still give a stable name
        name = "user"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "user"


def server_name(data_dir: str | Path) -> str:
    """'SmartCal-<user>-<hash of the data folder>'."""
    key = str(Path(data_dir).resolve()).casefold().encode("utf-8")
    return f"SmartCal-{_user()}-{hashlib.sha1(key).hexdigest()[:12]}"


class SingleInstance(QObject):
    activated = pyqtSignal()   # another launch asked this instance to show itself

    def __init__(self, data_dir: str | Path, parent=None):
        super().__init__(parent)
        self.name = server_name(data_dir)
        self._lock = QLockFile(str(Path(data_dir) / LOCK_NAME))
        self._lock.setStaleLockTime(0)   # stale only when the owning process is gone
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """True if this is the first instance; False after asking the first to show."""
        if not self._lock.tryLock(0):
            self._notify_first()
            return False
        server = QLocalServer(self)
        QLocalServer.removeServer(self.name)   # clears a stale socket (non-Windows)
        if server.listen(self.name):
            server.newConnection.connect(self._on_connection)
            self._server = server
        else:
            log.warning("single-instance server unavailable: %s", server.errorString())
        return True

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()

    def _notify_first(self) -> None:
        for _ in range(CONNECT_TRIES):   # the first instance may still be starting up
            sock = QLocalSocket()
            sock.connectToServer(self.name)
            if sock.waitForConnected(CONNECT_TIMEOUT_MS):
                sock.write(b"show\n")
                sock.waitForBytesWritten(CONNECT_TIMEOUT_MS)
                sock.disconnectFromServer()
                return
            QThread.msleep(200)
        log.warning("another SmartCal is running but did not answer")

    def _on_connection(self) -> None:
        server = self._server
        while server is not None and server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is not None:
                conn.disconnected.connect(conn.deleteLater)
                conn.disconnectFromServer()
            self.activated.emit()
