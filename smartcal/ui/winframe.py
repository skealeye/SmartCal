"""v0.8.6: the rounded, accent-edged frame of the popup and the details panel, and the
native stacking that lets the panel slide out from behind the popup.

Frame. On Windows 11 the window manager (DWM) rounds the window and draws a 1 px edge
in theme.ACCENT, anti-aliased (DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND and
DWMWA_BORDER_COLOR); the window paints only a faint inner line (theme.ACCENT_SOFT) just
inside it. Where that is not available (Windows 10, a failed call, tests offscreen) the
window paints the edge itself, as a rounded line; on the real Windows platform it is
also clipped to the rounded shape with a window mask (shape_region).

Stacking. A window owned by another always stays above it, so v0.8.5's panel (owned by
the popup) could only look "behind" the popup through a window mask that grew as it slid
out; the newly unmasked strip had never been painted and showed white for a frame
(Recording 2026-11-05: frames 67, 131, 191). The panel now has no native owner and is
stacked right below the popup (stack_below): it slides out from under the opaque popup
fully painted, with no mask at all.

Every call here is defensive: typed 64-bit-safe prototypes on private WinDLL instances,
never an exception out, False/None on any failure (the caller then keeps the old way)."""
from __future__ import annotations

import sys

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion

from . import theme

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2
WIN11_BUILD = 22000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x1, 0x2, 0x10, 0x200
GW_OWNER = 4
S_OK = 0

_api = None


def _win32():
    """(user32, dwmapi) with full prototypes; private instances, never the shared
    ctypes.windll function objects."""
    global _api
    if _api is None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmSetWindowAttribute.argtypes = (ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.c_void_p, wintypes.DWORD)
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long     # HRESULT
        user32.SetWindowPos.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint)
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.GetWindow.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.GetWindow.restype = ctypes.c_void_p
        _api = (user32, dwmapi)
    return _api


def native_windows() -> bool:
    """The real Windows platform (not offscreen tests, not another system)."""
    if sys.platform != "win32":
        return False
    try:
        from PyQt6.QtGui import QGuiApplication
        return (QGuiApplication.instance() is not None
                and QGuiApplication.platformName() == "windows")
    except Exception:  # noqa: BLE001
        return False


def windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001
        return 0


def _hwnd(widget) -> int:
    try:
        return int(widget.winId())
    except Exception:  # noqa: BLE001
        return 0


def apply_dwm_frame(widget) -> bool:
    """Ask Windows 11 to round `widget`'s window and draw its edge in theme.ACCENT.
    True only if the corner preference took (the edge color is a bonus: if only that
    failed, the window is still rounded and the painted inner line still shows)."""
    if not native_windows() or windows_build() < WIN11_BUILD:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        from .nativebg import colorref
        _user32, dwmapi = _win32()
        hwnd = _hwnd(widget)
        if not hwnd:
            return False
        pref = ctypes.c_int(DWMWCP_ROUND)
        ok = dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd),
                                          DWMWA_WINDOW_CORNER_PREFERENCE,
                                          ctypes.byref(pref), ctypes.sizeof(pref)) == S_OK
        if ok:
            color = wintypes.DWORD(colorref(theme.ACCENT))
            dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_BORDER_COLOR,
                                         ctypes.byref(color), ctypes.sizeof(color))
        return ok
    except Exception:  # noqa: BLE001 - a frame must never break the window
        return False


def stack_below(widget, above) -> bool:
    """Put `widget`'s window directly below `above`'s in the z-order, without moving,
    sizing or activating it. False on any failure or off the real Windows platform."""
    if not native_windows():
        return False
    try:
        import ctypes
        user32, _dwmapi = _win32()
        hwnd, ref = _hwnd(widget), _hwnd(above)
        if not hwnd or not ref:
            return False
        return bool(user32.SetWindowPos(
            ctypes.c_void_p(hwnd), ctypes.c_void_p(ref), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER))
    except Exception:  # noqa: BLE001
        return False


def has_native_owner(widget) -> bool | None:
    """Does `widget`'s window have an owner window? None when unknown."""
    if not native_windows():
        return None
    try:
        user32, _dwmapi = _win32()
        hwnd = _hwnd(widget)
        if not hwnd:
            return None
        return bool(user32.GetWindow(hwnd, GW_OWNER))
    except Exception:  # noqa: BLE001
        return None


DESKTOP_SWITCHDESKTOP = 0x0100


def input_desktop_locked() -> bool:
    """v0.9.1: is the input desktop out of reach (the workstation is locked, or a secure
    desktop such as a UAC prompt is up)? False off the real Windows platform or when the
    check itself fails (then nothing changes)."""
    if not native_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = (ctypes.c_void_p,)
        user32.CloseDesktop.restype = wintypes.BOOL
        user32.SwitchDesktop.argtypes = (ctypes.c_void_p,)
        user32.SwitchDesktop.restype = wintypes.BOOL
        desk = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not desk:
            return True
        try:
            # Locked: the input desktop opens, but switching to it is refused.
            return not user32.SwitchDesktop(desk)
        finally:
            user32.CloseDesktop(desk)
    except Exception:  # noqa: BLE001
        return False


def drop_native_owner(widget) -> None:
    """Keep the Qt parent (lifetime, focus group) but give the window no native owner:
    Qt applies a cleared transient parent when the window is next shown."""
    try:
        handle = widget.windowHandle()
        if handle is None:
            widget.winId()              # creates the native window (not shown)
            handle = widget.windowHandle()
        if handle is not None and handle.transientParent() is not None:
            handle.setTransientParent(None)
    except Exception:  # noqa: BLE001
        pass


def rounded_path(w: float, h: float, inset: float, radius: float) -> QPainterPath:
    path = QPainterPath()
    r = max(0.0, radius - inset)
    path.addRoundedRect(QRectF(inset, inset, max(0.0, w - 2 * inset),
                               max(0.0, h - 2 * inset)), r, r)
    return path


def shape_region(w: int, h: int, radius: int = theme.CORNER_RADIUS) -> QRegion:
    """The rounded window shape as a (pixel) region, for the fallback mask."""
    if w <= 0 or h <= 0:
        return QRegion()
    r = min(radius, w // 2, h // 2)
    region = QRegion(0, r, w, max(0, h - 2 * r)).united(QRegion(r, 0, max(0, w - 2 * r), h))
    d = 2 * r
    for x, y in ((0, 0), (w - d, 0), (0, h - d), (w - d, h - d)):
        region = region.united(QRegion(x, y, d, d, QRegion.RegionType.Ellipse))
    return region


def paint_frame(widget, by_dwm: bool) -> None:
    """The card edge, over the window's background. `by_dwm`: Windows already draws the
    outer ACCENT edge, so only the soft inner line is painted just inside it; else
    the ACCENT edge is painted too, as a rounded line on the window's outline."""
    w, h = widget.width(), widget.height()
    if w < 4 or h < 4:
        return
    p = QPainter(widget)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        radius = float(theme.CORNER_RADIUS)
        if not by_dwm:
            p.setPen(QPen(QColor(theme.ACCENT), 1.0))
            p.drawPath(rounded_path(w, h, 0.5, radius))
        p.setPen(QPen(QColor(theme.ACCENT_SOFT), 1.0))
        p.drawPath(rounded_path(w, h, 1.5, radius))
    finally:
        p.end()


class FramedWindow:
    """Mixin for the popup and the panel: the frame is set up when the window is shown
    (DWM first; else the painted edge, plus the rounded mask on real Windows)."""

    _frame_hwnd = 0
    frame_by_dwm = False
    frame_masked = False                # fallback: clipped to shape_region

    def setup_frame(self) -> None:
        hwnd = _hwnd(self) if native_windows() else 0
        if hwnd and hwnd == self._frame_hwnd:
            return                      # same native window: already set up
        self._frame_hwnd = hwnd
        self.frame_by_dwm = apply_dwm_frame(self)
        self.frame_masked = native_windows() and not self.frame_by_dwm

    def frame_shape(self) -> QRegion | None:
        """The fallback shape mask for the current size, or None (no shape mask)."""
        if not self.frame_masked:
            return None
        return shape_region(self.width(), self.height())
