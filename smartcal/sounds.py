"""Short UI sounds, synthesized in memory (no audio files to ship), played with winsound.

Three sounds: "chime" (a reminder toast arrived), "open" (the calendar popup opens) and
"details" (a meeting's details open). Off with the tray menu's "Play sounds", or with
SMARTCAL_NO_SOUND=1 (the tests set it). Playing never blocks the caller and never raises.
"""
from __future__ import annotations

import io
import logging
import math
import os
import struct
import threading
import wave

log = logging.getLogger(__name__)

RATE = 44100
DEFAULT_VOLUME = 40      # percent of the v0.14 level: 60% quieter (v0.15.1)
_enabled = True
_volume = DEFAULT_VOLUME
_cache: dict[tuple[str, int], bytes] = {}


def _tone(notes, volume: float) -> bytes:
    """`notes`: (frequency Hz, seconds) pairs played one after another, each with a quick
    attack and an exponential decay (a soft bell, no clicks)."""
    frames = bytearray()
    for freq, secs in notes:
        n = int(RATE * secs)
        for i in range(n):
            t = i / RATE
            attack = min(1.0, i / (RATE * 0.004))
            env = attack * math.exp(-5.0 * t / secs)
            sample = math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t)
            frames += struct.pack("<h", int(32767 * volume * env * sample / 1.3))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return buf.getvalue()


SOUNDS = {      # each takes the volume factor 0..1 (1 = the v0.14 level)
    "chime": lambda v: _tone([(1318.5, 0.16), (1760.0, 0.34)], 0.55 * v),   # E6 then A6
    "open": lambda v: _tone([(880.0, 0.09)], 0.28 * v),
    "details": lambda v: _tone([(660.0, 0.07)], 0.25 * v),
}


def clamp_volume(value) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_VOLUME


def set_volume(percent) -> None:
    global _volume
    _volume = clamp_volume(percent)


def volume() -> int:
    return _volume


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


def is_enabled() -> bool:
    return _enabled and os.environ.get("SMARTCAL_NO_SOUND") != "1"


def wav_bytes(name: str, percent: int | None = None) -> bytes:
    """The sound at `percent` of its full level (default: the current volume)."""
    pct = _volume if percent is None else clamp_volume(percent)
    key = (name, pct)
    if key not in _cache:
        _cache[key] = SOUNDS[name](pct / 100)
    return _cache[key]


def _play_now(name: str, percent: int | None = None) -> None:
    try:
        import winsound
        # Windows refuses SND_MEMORY together with SND_ASYNC, so this call blocks for the
        # sound's length (under a second) and runs on its own thread.
        winsound.PlaySound(wav_bytes(name, percent), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
    except Exception as exc:  # noqa: BLE001 - a sound must never break the app
        log.info("sound %s failed: %s", name, exc)


def play(name: str, percent: int | None = None) -> threading.Thread | None:
    """Start `name` and return at once (the thread, or None when sounds are off or the
    volume is 0). `percent` overrides the volume (the Settings preview)."""
    if not is_enabled() or (_volume if percent is None else percent) <= 0:
        return None
    t = threading.Thread(target=_play_now, args=(name, percent), daemon=True, name="sound")
    t.start()
    return t
