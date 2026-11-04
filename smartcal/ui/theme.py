"""Dark palette and color helpers for SmartCal's UI."""
from __future__ import annotations

from PyQt6.QtGui import QColor

BG = "#1f2023"
BG_NEXT = "#2b3a55"
TEXT = "#f1f3f4"
MUTED = "#9aa0a6"
BORDER = "#3c4043"
LINK = "#8ab4f8"
WARN = "#fdd663"
DEFAULT_STRIPE = "#4285f4"


def stripe_color(color: str | None) -> str:
    """A valid CSS hex color for an event's calendar, falling back to blue."""
    if color and QColor(color).isValid():
        return QColor(color).name()
    return DEFAULT_STRIPE
