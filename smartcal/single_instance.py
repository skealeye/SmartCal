"""One SmartCal per user and data folder.

A QLockFile in the data folder decides who is first (Qt treats a lock left by a
dead process as stale). The first instance also listens on a QLocalServer; a
second launch connects to it, which makes the first show its popup, and exits.

Both are keyed by the data folder, so a test run with SMARTCAL_DATA_DIR set to a
temp folder never collides with the owner's real instance.

The guard never leaves the owner with no app and no message: a lock that cannot
be used (permissions, unknown error) is skipped with a warning, and a lock whose
holder does not answer within about 1.5 s is cleared if stale and otherwise
ignored (`took_over` is set so the app can say so).
"""
from __future__ import annotations

import getpass
import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QLockFile, QObject, QThread, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

LOCK_NAME = "smartcal.lock"
ANSWER_WITHIN_S = 1.5      # how long the first instance gets to answer
CONNECT_TIMEOUT_MS = 500
RETRY_SLEEP_MS = 150
ASFW_ANY = 0xFFFFFFFF      # AllowSetForegroundWindow: any process may take focus


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


def allow_foreground() -> None:
    """Let the first instance bring its popup to the front (Windows only, best effort)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(ASFW_ANY))
    except Exception:  # noqa: BLE001 - focus is a nicety, never a reason to fail
        log.debug("AllowSetForegroundWindow failed", exc_info=True)


def process_name(pid: int) -> str | None:
    """Executable file name of a running process, or None if unknown/not running."""
    if sys.platform != "win32" or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return Path(buf.value).name
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def _could_be_smartcal(name: str) -> bool:
    """A process name that may be another SmartCal: the packaged exe, this executable,
    or any Python (a source run may use python.exe or pythonw.exe)."""
    n = name.casefold()
    return n in ("smartcal.exe", Path(sys.executable).name.casefold()) or n.startswith("python")


class SingleInstance(QObject):
    activated = pyqtSignal()   # another launch asked this instance to show itself

    def __init__(self, data_dir: str | Path, parent=None, lock=None, socket_factory=None,
                 process_name_fn=None, own_pid=None):
        super().__init__(parent)
        self.name = server_name(data_dir)
        self._lock = lock if lock is not None else QLockFile(str(Path(data_dir) / LOCK_NAME))
        self._lock.setStaleLockTime(0)   # stale only when the owning process is gone
        self._socket_factory = socket_factory or QLocalSocket
        self._process_name = process_name_fn or process_name
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._server: QLocalServer | None = None
        self._locked = False
        self.took_over = False    # started although another copy holds the lock

    def acquire(self) -> bool:
        """True: start this instance. False: the first one answered and shows its popup."""
        if self._try_lock():
            self._listen()
            return True
        allow_foreground()
        if self._notify_first():
            return False
        # Held, but nobody answers: a stale lock (dead or reused PID) or a hung copy.
        if self._clear_stale() and self._try_lock():
            log.warning("removed a stale single-instance lock")
            self._listen()
            return True
        log.warning("another SmartCal holds the lock but is not responding; starting anyway")
        self.took_over = True
        self._listen()
        return True

    def _try_lock(self) -> bool:
        """True if we hold the lock, or must start without it (lock unusable)."""
        if self._lock.tryLock(0):
            self._locked = True
            return True
        err = self._lock.error()
        if err == QLockFile.LockError.LockFailedError:
            return False                 # really held by someone
        log.warning("single-instance lock unusable (%s); starting without it", err)
        return True

    def _clear_stale(self) -> bool:
        """Remove the lock if its owner is gone, is us, or is not a SmartCal."""
        if self._lock.removeStaleLockFile():
            return True
        try:
            ok, pid, _host, _app = self._lock.getLockInfo()
        except Exception:  # noqa: BLE001
            return False
        if not ok:
            return False
        name = None if pid == self._own_pid else self._process_name(pid)
        if pid == self._own_pid or (name is not None and not _could_be_smartcal(name)):
            log.warning("single-instance lock names pid %s (%s); treating it as stale",
                        pid, "this process" if pid == self._own_pid else name)
            try:
                os.remove(self._lock.fileName())
            except OSError:
                log.warning("could not remove the stale lock file", exc_info=True)
                return False
            return True
        return False

    def _listen(self) -> None:
        server = QLocalServer(self)
        QLocalServer.removeServer(self.name)   # clears a stale socket (non-Windows)
        if server.listen(self.name):
            server.newConnection.connect(self._on_connection)
            self._server = server
        else:
            log.warning("single-instance server unavailable: %s", server.errorString())

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._locked:
            self._lock.unlock()
            self._locked = False

    def _notify_first(self) -> bool:
        """Ask the first instance to show itself; True if it answered in time."""
        deadline = time.monotonic() + ANSWER_WITHIN_S
        while True:   # the first instance may still be starting up
            sock = self._socket_factory()
            sock.connectToServer(self.name)
            if sock.waitForConnected(CONNECT_TIMEOUT_MS):
                sock.write(b"show\n")
                sock.waitForBytesWritten(CONNECT_TIMEOUT_MS)
                sock.disconnectFromServer()
                return True
            if time.monotonic() >= deadline:
                log.warning("another SmartCal is running but did not answer")
                return False
            QThread.msleep(RETRY_SLEEP_MS)

    def _on_connection(self) -> None:
        server = self._server
        while server is not None and server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is not None:
                conn.disconnected.connect(conn.deleteLater)
                conn.disconnectFromServer()
            self.activated.emit()
