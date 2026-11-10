"""Short UI sounds, synthesized in memory (no audio files to ship), played with winsound.

Three sounds: "chime" (a reminder toast arrived), "open" (the calendar popup opens) and
"details" (a meeting's details open). Off with the tray menu's "Play sounds", or with
SMARTCAL_NO_SOUND=1 (the tests set it). Playing never blocks and never raises.
"""
from __future__ import annotations

import io
import logging
import math
import os
import struct
import wave

log = logging.getLogger(__name__)

RATE = 44100
_enabled = True
_cache: dict[str, bytes] = {}


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


SOUNDS = {
    "chime": lambda: _tone([(1318.5, 0.16), (1760.0, 0.34)], 0.55),   # E6 then A6
    "open": lambda: _tone([(880.0, 0.09)], 0.28),
    "details": lambda: _tone([(660.0, 0.07)], 0.25),
}


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


def is_enabled() -> bool:
    return _enabled and os.environ.get("SMARTCAL_NO_SOUND") != "1"


def wav_bytes(name: str) -> bytes:
    if name not in _cache:
        _cache[name] = SOUNDS[name]()
    return _cache[name]


def play(name: str) -> None:
    """Start `name` and return at once. Silent when sounds are off or winsound fails."""
    if not is_enabled():
        return
    try:
        import winsound
        winsound.PlaySound(wav_bytes(name),
                           winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception as exc:  # noqa: BLE001 - a sound must never break the app
        log.info("sound %s failed: %s", name, exc)
