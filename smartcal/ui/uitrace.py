"""v0.10.0: a cheap debug trace of what the popup does to its own look: rebuilds,
relayouts, height changes, effects installed, frames set up, the chat drawer, activation.
Only with SMARTCAL_DEBUG_UI=1 (read once, at import); then each line goes to the log
(smartcal.log) with a millisecond timestamp. Off, trace() is one boolean test.

Used to find what changes on screen with nobody touching the popup (the owner's
"every so often the UI glitches and flickers"). Never logs titles or other personal
data: counts, sizes and names of code paths only."""
from __future__ import annotations

import logging
import os
import time

ENV = "SMARTCAL_DEBUG_UI"
log = logging.getLogger("smartcal.uitrace")
_T0 = time.monotonic()
_enabled = os.environ.get(ENV, "").strip().lower() in ("1", "true", "yes", "on")
# Every line also goes here when set (tests and the soak run read it back).
sink: list | None = None


def enabled() -> bool:
    return _enabled or sink is not None


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = bool(value)


def trace(what: str, **fields) -> None:
    """One line: "<ms> <what> k=v ...". Never raises."""
    if not (_enabled or sink is not None):
        return
    try:
        ms = int((time.monotonic() - _T0) * 1000)
        parts = " ".join(f"{k}={v}" for k, v in fields.items())
        line = f"{ms:>9} {what} {parts}".rstrip()
        if sink is not None:
            sink.append((what, dict(fields)))
        if _enabled:
            log.info("ui %s", line)
    except Exception:  # noqa: BLE001 - a debug trace never breaks the UI
        pass
