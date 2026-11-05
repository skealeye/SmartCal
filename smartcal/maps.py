"""Google Maps directions links (no API key, no origin: Maps uses the current location)."""
from __future__ import annotations

import re
from urllib.parse import quote

_BASE = "https://www.google.com/maps/dir/?api=1&destination="
_SPACE = re.compile(r"[\x00-\x1f\x7f-\x9f  \s]+")


def directions_url(place: str) -> str:
    """Directions URL to `place` (a business name or address), fully percent-encoded."""
    text = _SPACE.sub(" ", str(place or "")).strip()
    if not text:
        raise ValueError("place is empty")
    return _BASE + quote(text, safe="")
