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
    if color and isinstance(color, str) and _HEX.fullmatch(color) and QColor(color).isValid():
        return QColor(color).name()
    return DEFAULT_STRIPE


def effective_color(event, settings) -> str:
    """The meeting's own color (set in SmartCal) if any, else its calendar's color."""
    lookup = getattr(settings, "event_color", None) if settings is not None else None
    override = lookup(event) if lookup is not None else None
    return stripe_color(override or getattr(event, "color", None))


# Meeting color palette: (name, hex). Every swatch has >= 3:1 contrast against BG,
# so a colored stripe or chip stays visible on the dark popup.
PALETTE = (
    ("Tomato", "#e8594f"),
    ("Orange", "#f4893d"),
    ("Banana", "#f6c445"),
    ("Sage", "#5fbf8c"),
    ("Basil", "#3fa364"),
    ("Peacock", "#2aa3d8"),
    ("Blueberry", "#7b8cf0"),
    ("Lavender", "#a7b0f2"),
    ("Grape", "#b36ad6"),
    ("Graphite", "#9aa0a6"),
)
