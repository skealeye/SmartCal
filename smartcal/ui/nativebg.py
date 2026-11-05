"""v0.8.4: never a white frame from the native window behind a Qt window.

Windows erases a window's background (WM_ERASEBKGND) before Qt paints it: when it is
first shown, when parts of it are uncovered, and when it stops being a layered window
(Qt drops WS_EX_LAYERED when the window opacity goes back to 1.0 and repaints the whole
window). Qt itself leaves that erase to the native window, which can show the system's
white for a frame before Qt's paint arrives. The popup and the details panel answer the
erase themselves, filling with their own dark background (theme.BG)."""
from __future__ import annotations

import sys

WM_ERASEBKGND = 0x0014
_brushes: dict[int, int] = {}


def colorref(hex_color: str) -> int:
    """'#rrggbb' as a Win32 COLORREF (0x00bbggrr)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def fill_dc(hdc: int, hwnd: int, hex_color: str) -> bool:
    """Fill the client area of `hwnd` on `hdc` with `hex_color`. False on any failure
    (Qt's own handling then runs as before)."""
    if sys.platform != "win32" or not hdc:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        user32.FillRect.argtypes = (wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                    wintypes.HBRUSH)
        user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
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
    """For QWidget.nativeEvent: (True, 1) when `message` is a WM_ERASEBKGND that was
    answered by filling with `hex_color`; else None (let Qt handle it)."""
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
            return True, 1
    except Exception:  # noqa: BLE001
        return None
    return None
