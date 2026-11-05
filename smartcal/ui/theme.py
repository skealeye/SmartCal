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
BORDER_PX = 1


def window_style(name: str) -> str:
    """Style sheet of the tray popup and its details panel (one look for both): the
    dark background, a BORDER_PX border in BORDER, and the label text."""
    return (f"#{name}{{background:{BG};border:{BORDER_PX}px solid {BORDER};}}"
            f"QLabel{{color:{TEXT};font-size:12px;}}")


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


# -- tile style "fill": text on a colored tile ------------------------------------------
TILE_STYLES = ("stripe", "fill")
ON_DARK = "#000000"
ON_LIGHT = "#ffffff"


def _channel(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    """WCAG relative luminance of a hex color."""
    q = QColor(color)
    return 0.2126 * _channel(q.red()) + 0.7152 * _channel(q.green()) \
        + 0.0722 * _channel(q.blue())


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_on(fill: str) -> str:
    """Black or white, whichever contrasts more with `fill` (always >= 4.5:1: the
    lowest possible best-of-two is about 4.58:1)."""
    return ON_DARK if contrast_ratio(fill, ON_DARK) >= contrast_ratio(fill, ON_LIGHT) \
        else ON_LIGHT


def muted_on(fill: str) -> str:
    """Secondary text on `fill`: the text color, slightly toned toward the fill but
    kept at >= 4.5:1."""
    main = QColor(text_on(fill))
    base = QColor(fill)
    for t in (0.25, 0.15, 0.08, 0.0):
        mixed = QColor(round(main.red() + (base.red() - main.red()) * t),
                       round(main.green() + (base.green() - main.green()) * t),
                       round(main.blue() + (base.blue() - main.blue()) * t)).name()
        if contrast_ratio(mixed, fill) >= 4.5:
            return mixed
    return main.name()


def mix(a: str, b: str, t: float) -> str:
    """`a` blended toward `b` by t (0..1)."""
    qa, qb = QColor(a), QColor(b)
    return QColor(round(qa.red() + (qb.red() - qa.red()) * t),
                  round(qa.green() + (qb.green() - qa.green()) * t),
                  round(qa.blue() + (qb.blue() - qa.blue()) * t)).name()


def shade_keeping_contrast(base: str, toward: str, against: str,
                           amounts=(0.15, 0.10, 0.05), min_ratio: float = 4.5) -> str:
    """`base` blended toward `toward` by the largest of `amounts` that keeps it at
    least `min_ratio` against `against` (a hover/press shade that stays legible);
    `base` itself when none does."""
    for t in amounts:
        c = mix(base, toward, t)
        if contrast_ratio(c, against) >= min_ratio:
            return c
    return QColor(base).name()
