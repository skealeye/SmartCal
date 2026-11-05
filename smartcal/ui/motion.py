"""Short UI animations (the tray popup sliding in, the details panel sliding out and
back) and whether to play them.

Animations are off when Windows "Animation effects" are off (Settings > Accessibility >
Visual effects) or when SMARTCAL_NO_ANIMATIONS=1 is set; everything then appears and
docks instantly, through the same code path with a duration of 0."""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QPoint, QRect

ENV_OFF = "SMARTCAL_NO_ANIMATIONS"
SPI_GETCLIENTAREAANIMATION = 0x1042

POPUP_MS = 160              # the popup sliding in from the tray side while fading in
POPUP_SLIDE_PX = 24
FLYOUT_OPEN_MS = 180        # the details panel sliding out from behind the popup
FLYOUT_CLOSE_MS = 140       # ...and back


def system_animations_enabled() -> bool:
    """Windows "Animation effects" setting. Any failure (or another system): True."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        value = wintypes.BOOL(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
        if not ok:
            return True
        return bool(value.value)
    except Exception:  # noqa: BLE001 - a missing setting never breaks the UI
        return True


def animations_enabled() -> bool:
    """Play animations? Injectable: the popup and the details panel both ask this
    (module attribute, so tests can replace it)."""
    if os.environ.get(ENV_OFF, "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return system_animations_enabled()


def duration(ms: int) -> int:
    """`ms`, or 0 when animations are off."""
    return ms if animations_enabled() else 0


def tray_edge(tray: QRect | None, area: QRect) -> str:
    """Which screen edge the taskbar holding the tray icon is on: "bottom", "top",
    "left" or "right". No tray geometry (icon in the overflow): "bottom". Same
    top/bottom rule as placement.compute_position; left/right when the icon lies beside
    the work area (a vertical taskbar is outside it)."""
    if tray is None or tray.isEmpty():
        return "bottom"
    if tray.right() < area.left():
        return "left"
    if tray.left() > area.right():
        return "right"
    return "top" if tray.top() < area.center().y() else "bottom"


def slide_in_offset(edge: str, px: int = POPUP_SLIDE_PX) -> QPoint:
    """Where the popup starts, relative to its docked position: on the tray side, so it
    moves away from the taskbar (bottom taskbar: starts lower and slides up)."""
    return {"bottom": QPoint(0, px), "top": QPoint(0, -px),
            "left": QPoint(-px, 0), "right": QPoint(px, 0)}.get(edge, QPoint(0, px))
