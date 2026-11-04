"""Dark palette and color helpers for SmartCal's UI."""
from __future__ import annotations

import re

from PyQt6.QtGui import QColor

BG = "#1f2023"
BG_NEXT = "#2b3a55"
TEXT = "#f1f3f4"
MUTED = "#9aa0a6"
BORDER = "#3c4043"
LINK = "#8ab4f8"
WARN = "#fdd663"
DEFAULT_STRIPE = "#4285f4"


_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def stripe_color(color: str | None) -> str:
    """A valid CSS hex color for an event's calendar, falling back to blue."""
    if color and _HEX.fullmatch(color) and QColor(color).isValid():
        return QColor(color).name()
    return DEFAULT_STRIPE
