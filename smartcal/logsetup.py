"""File logging and exception hooks so a stray exception never aborts the tray app."""
from __future__ import annotations

import functools
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger("smartcal")


def setup_logging(path: str | Path) -> RotatingFileHandler:
    """Log to a rotating file (512 KB x 3). Returns the handler (so tests can remove it)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    quiet_noisy_loggers()
    return handler


# These log full URLs at INFO: the OAuth URL carries the state and PKCE challenge.
NOISY_LOGGERS = ("google_auth_oauthlib.flow",)


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
