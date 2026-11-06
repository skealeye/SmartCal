"""Short UI animations (the tray popup sliding in, the details panel sliding out and
back, v0.9.0's card build and press feedback, see cards.py) and whether to play them.
Every timing, offset, scale and curve lives here.

Whether they play is the owner's setting (Settings > General > Animations, or the tray
menu): "on" (the default; plays even when Windows "Animation effects" are off), "auto"
(follows that Windows setting) or "off". SMARTCAL_NO_ANIMATIONS=1 forces them off. Off:
everything appears and docks instantly, through the same code path with a duration of
0."""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QPoint, QRect, QSize

ENV_OFF = "SMARTCAL_NO_ANIMATIONS"
SPI_GETCLIENTAREAANIMATION = 0x1042

POPUP_MS = 160              # the popup sliding in from the tray side while fading in
POPUP_SLIDE_PX = 18         # v0.9.0 ("card build"): a short rise; the cards do the rest
# v0.8.4: the popup's opening animation starts once its first frame is painted (the
# very first open after start paints slowly: started before, the slide was over before
# anything was on screen). If no paint is seen within this time, it starts anyway.
SHOW_START_FALLBACK_MS = 150
FLYOUT_OPEN_MS = 180        # the details panel sliding out from behind the popup
FLYOUT_CLOSE_MS = 140       # ...and back
# v0.8.6: the details panel shows under the popup and starts sliding once its first frame
# is painted (nothing unpainted ever comes out); if no paint is seen by then, it starts.
FLYOUT_PAINT_WAIT_MS = 80


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


# The owner's choice (Settings > General > Animations, or the tray menu): "on" plays
# animations even when Windows "Animation effects" are off; "auto" follows that Windows
# setting; "off" never animates. SMARTCAL_NO_ANIMATIONS=1 forces off whatever is chosen.
ANIMATION_MODES = ("on", "auto", "off")
DEFAULT_MODE = "on"
_user_preference = DEFAULT_MODE


def set_user_preference(value) -> None:
    """Set at startup (run.py) and whenever the setting changes; anything invalid is
    the default ("on")."""
    global _user_preference
    _user_preference = value if value in ANIMATION_MODES else DEFAULT_MODE


def user_preference() -> str:
    return _user_preference


def env_forces_off() -> bool:
    return os.environ.get(ENV_OFF, "").strip().lower() in ("1", "true", "yes", "on")


def animations_enabled() -> bool:
    """Play animations? The env var forces off; else the owner's setting ("auto": the
    Windows setting). Injectable: the popup and the details panel both ask this
    (module attribute, so tests can replace it)."""
    if env_forces_off():
        return False
    if _user_preference == "off":
        return False
    if _user_preference == "on":
        return True
    return system_animations_enabled()


def describe_mode() -> str:
    """One line for the log: the effective mode and why (no titles, no personal data)."""
    system = "on" if system_animations_enabled() else "off"
    if env_forces_off():
        text = f"animations: off ({ENV_OFF}=1 is set)"
    elif _user_preference == "on":
        text = f"animations: on (user setting; Windows animation effects are {system})"
    elif _user_preference == "off":
        text = f"animations: off (user setting; Windows animation effects are {system})"
    else:
        text = f"animations: {system} (user setting: follow Windows animation effects)"
    if os.environ.get(ENV_LEVEL, "").strip().lower() == "basic" and animations_enabled():
        text += f"; micro-animations off ({ENV_LEVEL}=basic)"
    return text


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
# One set of durations and curves for every small animation (see curve()). They follow
# animations_enabled() exactly like the popup/panel slides; SMARTCAL_ANIMATIONS=basic
# keeps only those two slides.
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


# v0.8.4: every motion eases out with a little weight: it passes its end by a few
# percent and settles back (OutBack, this amplitude: about 2.3% of the distance at 0.8).
# Opacity and color fades never overshoot (plain ease-out).
OVERSHOOT = 0.8
# The details panel's slide is bounded on both sides (past its dock it would open a gap
# beside the popup; past "tucked" it would poke out behind the popup's far edge), so it
# uses a weighted ease-out that settles into place without passing it: cubic Bezier
# (0.2, 1) (0.3, 1), whose values never leave 0..1.
GLIDE_BEZIER = ((0.2, 1.0), (0.3, 1.0))

# v0.9.0 ("card build"): the springy curve of the cards building into the popup and the
# panel's lines, and of a press springing back: cubic Bezier (0.34, 1.45) (0.5, 1), the
# owner's chosen mockup (option B). It passes its end by about 8% and settles back.
SPRING_BEZIER = ((0.34, 1.45), (0.5, 1.0))

SETTLE = "settle"           # positions and sizes: OutBack(OVERSHOOT)
GLIDE = "glide"             # bounded positions (the panel slide): no overshoot
SPRING = "spring"           # the card build and press release: SPRING_BEZIER
FADE = "fade"               # opacity and colors: OutCubic, never past the end
LINEAR = "linear"           # raw progress (callers apply curves themselves, see ease())


def curve(kind: str = FADE):
    """The easing curve for `kind`: SETTLE, GLIDE, SPRING, LINEAR, ACCEL, or anything
    else (FADE)."""
    from PyQt6.QtCore import QEasingCurve, QPointF
    if kind == SETTLE:
        c = QEasingCurve(QEasingCurve.Type.OutBack)
        c.setOvershoot(OVERSHOOT)
        return c
    if kind in (GLIDE, SPRING):
        c = QEasingCurve(QEasingCurve.Type.BezierSpline)
        (x1, y1), (x2, y2) = GLIDE_BEZIER if kind == GLIDE else SPRING_BEZIER
        c.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
        return c
    if kind == LINEAR:
        return QEasingCurve(QEasingCurve.Type.Linear)
    if kind == ACCEL:
        return QEasingCurve(QEasingCurve.Type.InQuad)
    return QEasingCurve(QEasingCurve.Type.OutCubic)


_CURVES: dict = {}


def ease(kind: str, progress: float) -> float:
    """curve(kind) at `progress` (0..1, clamped). Exactly 0.0 at 0 and 1.0 at 1, so an
    animation driven by it always lands on its end value."""
    p = min(1.0, max(0.0, float(progress)))
    if p >= 1.0:
        return 1.0
    if p <= 0.0:
        return 0.0
    c = _CURVES.get(kind)
    if c is None:
        c = _CURVES[kind] = curve(kind)
    return float(c.valueForProgress(p))


def clamp(value, lo, hi):
    """`value` kept inside [lo, hi] (either order)."""
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, value))


def clamp_between(pos: QPoint, a: QPoint, b: QPoint) -> QPoint:
    """`pos` kept inside the box spanned by `a` and `b`: a motion from a to b that
    overshoots never goes past either end."""
    return QPoint(clamp(pos.x(), a.x(), b.x()), clamp(pos.y(), a.y(), b.y()))


def alive(anim) -> bool:
    """A QVariantAnimation that still exists and is running (or paused mid-way)."""
    if anim is None:
        return False
    try:
        from PyQt6.QtCore import QAbstractAnimation
        return anim.state() != QAbstractAnimation.State.Stopped
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


# -- v0.9.0: the "card build" ------------------------------------------------------------
# Opening the popup: the header fades in, then every agenda row (a card) builds into
# place: from BUILD_OFFSET_PX lower, scaled to BUILD_SCALE_START and transparent, it
# springs (SPRING) to its place, BUILD_STAGGER_MS after the one above. Opening the
# details panel (or switching it to another meeting): its lines rise PANEL_BUILD_OFFSET_PX
# the same way. Both are micro-animations (micro_enabled(): off with animations off and
# with SMARTCAL_ANIMATIONS=basic). Opacity follows the same curve clamped to 0..1, so it
# never passes 1.
HEADER_FADE_DELAY_MS = 40   # after the popup's slide starts
HEADER_FADE_MS = 200
BUILD_START_MS = 50         # the first row, after the popup's slide starts
BUILD_STAGGER_MS = 45       # between rows
BUILD_ROW_MS = 420          # each row's own build
BUILD_OFFSET_PX = 26
BUILD_SCALE_START = 0.94
BUILD_MAX_STAGGER = 8       # rows past this many start together with the 8th
PANEL_BUILD_START_MS = 140  # the first line, after the panel's slide starts
PANEL_SWITCH_START_MS = 40  # ...or after another meeting's content swapped in
PANEL_BUILD_STAGGER_MS = 50
PANEL_BUILD_MS = 380
PANEL_BUILD_OFFSET_PX = 14
# Press feedback (a meeting row, a Join button): shrinks to PRESS_SCALE while held, then
# springs back (SPRING) on release.
PRESS_SCALE = 0.97
PRESS_IN_MS = 90
PRESS_OUT_MS = 260


# -- v0.9.1: the outro (the card build in reverse) ------------------------------------
# Dismissing the popup (focus lost, the tray icon clicked again, Esc, Join...): the rows,
# then the next line and the header, fade out and drop OUTRO_DROP_PX (bottom row first,
# OUTRO_STAGGER_MS apart, each OUTRO_ITEM_MS, ACCEL), while the window itself fades and
# sinks OUTRO_SINK_PX toward the tray edge (ACCEL; clamped to the work area like the
# opening slide) from OUTRO_WINDOW_START_MS to OUTRO_MS; then it really hides. An open
# details panel first slides back under the popup in OUTRO_FLYOUT_MS (the window's fade
# waits for it, so nothing ever shows through the fading popup) and hides with it.
# SMARTCAL_ANIMATIONS=basic: only the window's fade and sink (OUTRO_BASIC_MS). Animations
# off: the popup hides at once. Reopened (or activated again) mid-outro: everything goes
# back into place in OUTRO_RESTORE_MS. OUTRO_SAFETY_MS: the popup is hidden by then
# whatever happens to the animation.
OUTRO_MS = 180
OUTRO_BASIC_MS = 130
OUTRO_WINDOW_START_MS = 50
OUTRO_WINDOW_MIN_MS = 95    # the window's own fade, at least (after a panel slid back)
OUTRO_SINK_PX = 14
OUTRO_DROP_PX = 8
OUTRO_STAGGER_MS = 15
OUTRO_ITEM_MS = 100
OUTRO_MAX_STAGGER = 6       # items past this many leave together with the 6th
OUTRO_FLYOUT_MS = 100
OUTRO_RESTORE_MS = 120
OUTRO_SAFETY_MS = 400
ACCEL = "accel"             # the outro: ease-in (InQuad), never past its end


# -- v0.10.0: the Ask Claude drawer ------------------------------------------------------
# The chat slides DOWN from the popup's top edge (a child widget inside the opaque popup,
# covering the agenda down to the "Hide chat" row) in DRAWER_OPEN_MS with the SETTLE
# curve (its slight overshoot is absorbed by DRAWER_PAD_PX of drawer hidden above the
# popup's top, so no gap ever opens above it); its header, last messages and input row
# build in (CardBuild: DRAWER_BUILD_*). Closing retracts it upward in DRAWER_CLOSE_MS
# (ACCEL, no overshoot). The slide follows animations_enabled() (basic mode keeps it);
# the build is a micro-animation. A popup too short for DRAWER_MIN_PX of drawer grows
# (its usual height tween) first.
DRAWER_OPEN_MS = 420
DRAWER_CLOSE_MS = 180
DRAWER_PAD_PX = 16
DRAWER_MIN_PX = 260
DRAWER_SHADOW_PX = 6
DRAWER_BUILD_START_MS = 90
DRAWER_BUILD_STAGGER_MS = 45
DRAWER_BUILD_MS = 380
DRAWER_BUILD_OFFSET_PX = 14
DRAWER_BUILD_SCALE = 0.96


def outro_window_span(micro: bool, flyout_ms: int = 0) -> tuple[int, int]:
    """When the window's fade and sink run (ms after the outro starts): (start, end).
    It starts after an open details panel slid back (`flyout_ms`), and never runs
    shorter than OUTRO_WINDOW_MIN_MS."""
    start = OUTRO_WINDOW_START_MS if micro else 0
    start = max(start, int(flyout_ms))
    end = max(OUTRO_MS if micro else OUTRO_BASIC_MS, start + OUTRO_WINDOW_MIN_MS)
    return start, end


def build_delays(count: int, start: int = BUILD_START_MS, stagger: int = BUILD_STAGGER_MS,
                 cap: int = BUILD_MAX_STAGGER) -> list[int]:
    """When each of `count` items starts building (ms after the build starts): `start`,
    then `stagger` apart; items past `cap` start together with the last staggered one."""
    cap = max(1, cap)
    return [start + min(i, cap - 1) * stagger for i in range(max(0, count))]


def build_total_ms(count: int, start: int = BUILD_START_MS, stagger: int = BUILD_STAGGER_MS,
                   item_ms: int = BUILD_ROW_MS, cap: int = BUILD_MAX_STAGGER) -> int:
    """How long a build of `count` items runs (0 for none)."""
    delays = build_delays(count, start, stagger, cap)
    return (delays[-1] + item_ms) if delays else 0


def card_state(elapsed_ms: float, delay: int, item_ms: int, offset_px: float,
               scale_start: float = 1.0) -> tuple[float, float, float, bool]:
    """One item of a build at `elapsed_ms`: (opacity, y offset in px, scale, done).
    Before its delay: transparent, `offset_px` lower, at `scale_start`. Then SPRING
    eases it into place (the offset and the scale may pass their end slightly and settle
    back; the opacity is clamped to 0..1). Done (exactly 1.0, 0.0, 1.0) at the end."""
    p = (elapsed_ms - delay) / item_ms if item_ms > 0 else 1.0
    if p >= 1.0:
        return 1.0, 0.0, 1.0, True
    e = ease(SPRING, p)
    return (clamp(e, 0.0, 1.0), offset_px * (1.0 - e),
            scale_start + (1.0 - scale_start) * e, False)


def tween(parent, start, end, ms: int, on_value, on_done=None, kind: str = FADE):
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
