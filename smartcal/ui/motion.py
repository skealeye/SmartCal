"""Short UI animations (the tray popup sliding in, the details panel sliding out and
back) and whether to play them.

Animations are off when Windows "Animation effects" are off (Settings > Accessibility >
Visual effects) or when SMARTCAL_NO_ANIMATIONS=1 is set; everything then appears and
docks instantly, through the same code path with a duration of 0."""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QPoint, QRect, QSize

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


def clamp_to_area(pos: QPoint, size: QSize, area: QRect) -> QPoint:
    """`pos` moved just enough that a window of `size` there lies inside `area` (no
    margin): the opening slide never covers the taskbar or reaches another monitor."""
    x = max(area.left(), min(pos.x(), area.right() + 1 - size.width()))
    y = max(area.top(), min(pos.y(), area.bottom() + 1 - size.height()))
    return QPoint(x, y)


def slide_in_offset(edge: str, px: int = POPUP_SLIDE_PX) -> QPoint:
    """Where the popup starts, relative to its docked position: on the tray side, so it
    moves away from the taskbar (bottom taskbar: starts lower and slides up)."""
    return {"bottom": QPoint(0, px), "top": QPoint(0, -px),
            "left": QPoint(-px, 0), "right": QPoint(px, 0)}.get(edge, QPoint(0, px))


# -- micro-interactions (v0.8.0) ---------------------------------------------------------
# One set of durations and curves for every small animation: entering (OutCubic),
# leaving (InCubic), resizing (InOutCubic). They follow animations_enabled() exactly like
# the popup/panel slides; SMARTCAL_ANIMATIONS=basic keeps only those two slides.
ENV_LEVEL = "SMARTCAL_ANIMATIONS"
FAST = 120                  # hover, press, notice fade
NORMAL = 180                # resizes, row collapse, chat expand
SLOW = 240
CROSSFADE_MS = 140          # the details panel swapping content


def micro_enabled() -> bool:
    """Hover fades, resizes, collapses, cross-fades... Off with animations off, or with
    SMARTCAL_ANIMATIONS=basic (keeps just the popup slide-in and the panel slide)."""
    if os.environ.get(ENV_LEVEL, "").strip().lower() == "basic":
        return False
    return animations_enabled()


def micro_duration(ms: int) -> int:
    return ms if micro_enabled() else 0


def curve(kind: str):
    """"enter" (OutCubic), "leave" (InCubic) or "resize" (InOutCubic)."""
    from PyQt6.QtCore import QEasingCurve
    t = {"enter": QEasingCurve.Type.OutCubic, "leave": QEasingCurve.Type.InCubic}.get(
        kind, QEasingCurve.Type.InOutCubic)
    return QEasingCurve(t)


def alive(anim) -> bool:
    """A QVariantAnimation that still exists and is running."""
    if anim is None:
        return False
    try:
        from PyQt6.QtCore import QAbstractAnimation
        return anim.state() == QAbstractAnimation.State.Running
    except RuntimeError:
        return False


def stop(anim) -> None:
    """Stop an animation if it is still there (safe on a deleted one)."""
    if anim is None:
        return
    try:
        anim.stop()
    except RuntimeError:
        pass


def tween(parent, start, end, ms: int, on_value, on_done=None, kind: str = "resize"):
    """Animate from `start` to `end` over `ms`, calling on_value(v) per frame and
    on_done() at the end. `ms` 0: on_value(end) and on_done() at once, returns None.
    The animation belongs to `parent` (deleted with it) and is deleted when it stops."""
    if ms <= 0:
        on_value(end)
        if on_done is not None:
            on_done()
        return None
    from PyQt6.QtCore import QAbstractAnimation, QVariantAnimation
    anim = QVariantAnimation(parent)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(curve(kind))
    anim.valueChanged.connect(on_value)
    if on_done is not None:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim
