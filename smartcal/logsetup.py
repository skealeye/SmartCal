"""File logging and exception hooks so a stray exception never aborts the tray app."""
from __future__ import annotations

import functools
import logging
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger("smartcal")


def _file_handler(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")


def setup_logging(path: str | Path) -> logging.Handler:
    """Log to a rotating file (512 KB x 3). Returns the handler (so tests can remove it).

    Never raises: if the log folder is unwritable it falls back to smartcal.log in
    the temp folder, and failing that to no logging at all."""
    handler: logging.Handler
    try:
        handler = _file_handler(Path(path))
    except OSError:
        try:
            handler = _file_handler(Path(tempfile.gettempdir()) / "smartcal.log")
        except OSError:
            handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    quiet_noisy_loggers()
    return handler


# These log full URLs at INFO: the OAuth URL carries the state and PKCE challenge.
NOISY_LOGGERS = ("google_auth_oauthlib.flow", "googleapiclient.discovery", "googleapiclient.http")


def quiet_noisy_loggers() -> None:
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _sys_hook(exc_type, exc, tb) -> None:
    log.error("Unhandled exception", exc_info=(exc_type, exc, tb))


def _thread_hook(args) -> None:
    log.error("Unhandled exception in thread %s",
              getattr(args.thread, "name", "?"),
              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def install_excepthooks() -> None:
    """Route unhandled exceptions (main and worker threads) to the log; app keeps running."""
    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


CRASH_LOG_MAX_BYTES = 256 * 1024
_crash_file = None          # kept open for the app's lifetime (faulthandler writes to it)
_qt_handler = None          # a reference keeps the Qt message handler alive


def enable_crash_log(path: str | Path, version: str = "") -> bool:
    """v0.8.5: a native crash (access violation in Qt, ...) kills the process before
    any Python logging runs; faulthandler then writes the Python stack of every thread
    to `path` (crash.log next to smartcal.log). One short line per start says which
    run a dump belongs to; past 256 KB the file starts over. Never raises."""
    global _crash_file
    import faulthandler
    from datetime import datetime
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > CRASH_LOG_MAX_BYTES:
            p.replace(p.with_suffix(p.suffix + ".1"))
        f = open(p, "a", encoding="utf-8", buffering=1)
        f.write(f"--- SmartCal {version} started {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.flush()
        faulthandler.enable(file=f, all_threads=True)
    except (OSError, ValueError, RuntimeError):
        return False
    old, _crash_file = _crash_file, f
    if old is not None:
        try:
            old.close()
        except OSError:
            pass
    return True


def install_qt_message_handler() -> None:
    """Qt's own warnings and errors (qWarning, qCritical, qFatal) go to smartcal.log."""
    global _qt_handler
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    qt_log = logging.getLogger("smartcal.qt")
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING,
              QtMsgType.QtCriticalMsg: logging.ERROR, QtMsgType.QtFatalMsg: logging.CRITICAL}

    def handler(mode, context, message) -> None:
        try:
            where = ""
            if context is not None and context.file:
                where = f" ({context.file}:{context.line})"
            qt_log.log(levels.get(mode, logging.WARNING), "%s%s", message, where)
            if mode == QtMsgType.QtFatalMsg:
                for h in logging.getLogger().handlers:
                    h.flush()
        except Exception:  # noqa: BLE001 - a log call must never raise into Qt
            pass

    _qt_handler = handler
    qInstallMessageHandler(handler)


def guarded(fn):
    """Wrap a Qt slot so an exception is logged instead of propagating into Qt."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("error in %s", getattr(fn, "__name__", fn))
            return None
    return wrapper
