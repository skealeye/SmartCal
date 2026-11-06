"""v0.8.4: never a white frame from the native window behind a Qt window.

Windows erases a window's background (WM_ERASEBKGND) before Qt paints it: when it is
first shown, when parts of it are uncovered, and when it stops being a layered window
(Qt drops WS_EX_LAYERED when the window opacity goes back to 1.0 and repaints the whole
window). Qt itself leaves that erase to the native window, which can show the system's
white for a frame before Qt's paint arrives. The popup and the details panel answer the
erase themselves, filling with their own dark background (theme.BG).

v0.8.5: a nativeEvent override must ALWAYS return (bool, int). With PyQt6 6.10,
the base class's nativeEvent returns (False, None), and handing that None back to Qt makes
sip copy the result from a NULL pointer: an access violation inside the window
procedure (QtCore.pyd+0x38130, 0xc0000005 then 0xc000041d) on the very first message,
so the popup crashed the app as it opened. native_event() is the only entry point: it
never calls the base class (QWidget's own nativeEvent does nothing and returns false) and
never lets an exception or a None escape."""
from __future__ import annotations

import sys

WM_ERASEBKGND = 0x0014
NOT_HANDLED = (False, 0)        # what QWidget.nativeEvent itself answers
HANDLED = (True, 1)             # erase answered: "the background is erased"
_brushes: dict[int, int] = {}
_api = None                     # (user32, gdi32): private, typed WinDLL instances


def colorref(hex_color: str) -> int:
    """'#rrggbb' as a Win32 COLORREF (0x00bbggrr)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def _win32():
    """user32/gdi32 with full 64-bit-safe prototypes. Private WinDLL instances: the
    prototypes never change ctypes.windll's shared function objects used elsewhere."""
    global _api
    if _api is None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        gdi32 = ctypes.WinDLL("gdi32")
        gdi32.CreateSolidBrush.argtypes = (wintypes.COLORREF,)
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        user32.FillRect.argtypes = (wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                    wintypes.HBRUSH)
        user32.FillRect.restype = ctypes.c_int
        user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user32.GetClientRect.restype = wintypes.BOOL
        _api = (user32, gdi32)
    return _api


def fill_dc(hdc: int, hwnd: int, hex_color: str) -> bool:
    """Fill the client area of `hwnd` on `hdc` with `hex_color`. False on any failure
    (Qt's own handling then runs as before)."""
    if sys.platform != "win32" or not hdc:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32, gdi32 = _win32()
        color = colorref(hex_color)
        brush = _brushes.get(color)
        if not brush:
            brush = gdi32.CreateSolidBrush(color)   # kept for the app's lifetime
            if not brush:
                return False
            _brushes[color] = brush
        rect = wintypes.RECT()
        if hwnd:
            if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
                return False
        else:                           # (tests: a memory DC has no window)
            rect.right = rect.bottom = 32767
        return bool(user32.FillRect(hdc, ctypes.byref(rect), brush))
    except Exception:  # noqa: BLE001 - painting help must never break the window
        return False


def handle_erase(event_type, message, hex_color: str):
    """HANDLED when `message` is a WM_ERASEBKGND that was answered by filling with
    `hex_color`; else None (not handled)."""
    if sys.platform != "win32":
        return None
    try:
        if bytes(event_type) != b"windows_generic_MSG" or not message:
            return None
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != WM_ERASEBKGND:
            return None
        if fill_dc(int(msg.wParam or 0), int(msg.hWnd or 0), hex_color):
            return HANDLED
    except Exception:  # noqa: BLE001
        return None
    return None


WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004


def message_info(event_type, message) -> tuple[int, int] | None:
    """(message id, wParam) of a native Windows message; None for anything else or on
    any failure. Read-only: never changes what nativeEvent answers (v0.9.1: the popup
    notes suspend and end-of-session messages, so it hides at once then)."""
    if sys.platform != "win32":
        return None
    try:
        if bytes(event_type) != b"windows_generic_MSG" or not message:
            return None
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        return int(msg.message), int(msg.wParam or 0)
    except BaseException:  # noqa: BLE001
        return None


def native_event(event_type, message, hex_color: str) -> tuple[bool, int]:
    """The whole body of a QWidget.nativeEvent override: always a (bool, int) pair,
    never None inside it, never an exception (see the module docstring)."""
    try:
        handled = handle_erase(event_type, message, hex_color)
    except BaseException:  # noqa: BLE001 - nothing may escape into the window procedure
        handled = None
    return HANDLED if handled == HANDLED else NOT_HANDLED
