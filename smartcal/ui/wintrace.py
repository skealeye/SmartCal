"""v0.11.3: with SMARTCAL_DEBUG_UI=1, a log of every top-level window of ours appearing,
moving or going away, so a stray flash on the owner's screen can be traced to a window.

The owner still saw "a faint and quick blip of a blue square outline on the top left side
of my monitor" with the Month view on v0.11.2. A probe on the real display (system-wide
WinEvent hook, EnumWindows polling, DXGI capture of every monitor's top-left corner) did
not see it: not across Month opens from the popup, view switches, month steps, hover,
tooltips, "+N more", meeting details, nor 14 fresh first opens straight into Month. So
the next occurrence is recorded where it happens:

- Qt side (an application event filter): every Show / Hide / Move / Resize of a top-level
  widget and every native window created / destroyed for one, with class, object name,
  frame rect, window opacity and the SmartCal code that showed it (the Python stack).
- Windows side (an out-of-context WinEvent hook, delivered on the GUI thread): every
  create / show / hide / move / cloak of a top-level window of this process, with its
  rect, DWM frame bounds, layered alpha and cloaked state; and any window of ANOTHER
  process shown at a monitor's top-left corner (its exe and class, never its title).

Lines go through uitrace (smartcal.log, "ui ..."). Never meeting titles: window titles of
our own windows are not logged at all. Off (the default), nothing is installed. Nothing
here may raise into Qt or the window procedure."""
from __future__ import annotations

import os
import sys
import traceback

from PyQt6.QtCore import QEvent, QObject

from . import uitrace

_TYPES = {QEvent.Type.Show: "show", QEvent.Type.Hide: "hide", QEvent.Type.Move: "move",
          QEvent.Type.Resize: "resize", QEvent.Type.PlatformSurface: "surface"}
EVENT_NAMES = {0x8000: "create", 0x8001: "destroy", 0x8002: "show", 0x8003: "hide",
               0x800B: "move", 0x8017: "cloak", 0x8018: "uncloak"}
CORNER_PX = 64          # another process's window this close to a monitor's top-left
_installed: dict = {}


def caller(skip: str = "wintrace") -> str:
    """The innermost SmartCal frames that led here ("file:line func", up to three)."""
    try:
        out = []
        for f in reversed(traceback.extract_stack()[:-1]):
            path = f.filename.replace("\\", "/")
            if "/smartcal/" in path and skip not in path:
                out.append(f"{path.rsplit('/', 1)[-1]}:{f.lineno} {f.name}")
            if len(out) == 3:
                break
        return " < ".join(out) or "-"
    except Exception:  # noqa: BLE001
        return "?"


def _rect(r) -> str:
    return f"{r.x()},{r.y()},{r.width()}x{r.height()}"


class WindowEvents(QObject):
    """Application event filter: top-level widgets only (never their titles)."""

    def eventFilter(self, obj, e) -> bool:
        try:
            kind = _TYPES.get(e.type())
            if kind is None or not obj.isWidgetType() or not obj.isWindow():
                return False
            fields = {"cls": type(obj).__name__, "name": obj.objectName() or "-",
                      "frame": _rect(obj.frameGeometry()),
                      "opacity": round(obj.windowOpacity(), 3),
                      "visible": obj.isVisible()}
            if kind == "surface":
                fields["surface"] = e.surfaceEventType().name
            if kind in ("show", "hide", "surface"):
                fields["caller"] = caller()
            uitrace.trace("win." + kind, **fields)
        except Exception:  # noqa: BLE001 - a debug log never breaks the UI
            pass
        return False


class NativeHook:
    """Out-of-context WinEvent hook for this process's top-level windows (and other
    processes' windows shown at a monitor's top-left). Typed, private WinDLLs."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        vp = ctypes.c_void_p
        self.PROC = ctypes.WINFUNCTYPE(None, vp, wintypes.DWORD, vp, wintypes.LONG,
                                       wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        u = self.user32 = ctypes.WinDLL("user32")
        d = self.dwmapi = ctypes.WinDLL("dwmapi")
        k = self.kernel32 = ctypes.WinDLL("kernel32")
        u.SetWinEventHook.restype = vp
        u.SetWinEventHook.argtypes = (wintypes.DWORD, wintypes.DWORD, vp, self.PROC,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
        u.UnhookWinEvent.argtypes = (vp,)
        u.UnhookWinEvent.restype = wintypes.BOOL
        u.GetAncestor.argtypes = (vp, ctypes.c_uint)
        u.GetAncestor.restype = vp
        u.GetWindowRect.argtypes = (vp, ctypes.POINTER(wintypes.RECT))
        u.GetWindowRect.restype = wintypes.BOOL
        u.IsWindowVisible.argtypes = (vp,)
        u.IsWindowVisible.restype = wintypes.BOOL
        u.GetWindowLongW.argtypes = (vp, ctypes.c_int)
        u.GetWindowLongW.restype = wintypes.LONG
        u.GetClassNameW.argtypes = (vp, wintypes.LPWSTR, ctypes.c_int)
        u.GetClassNameW.restype = ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = (vp, ctypes.POINTER(wintypes.DWORD))
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetLayeredWindowAttributes.argtypes = (vp, ctypes.POINTER(wintypes.DWORD),
                                                 ctypes.POINTER(ctypes.c_ubyte),
                                                 ctypes.POINTER(wintypes.DWORD))
        u.GetLayeredWindowAttributes.restype = wintypes.BOOL
        d.DwmGetWindowAttribute.argtypes = (vp, wintypes.DWORD, vp, wintypes.DWORD)
        d.DwmGetWindowAttribute.restype = ctypes.c_long
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.OpenProcess.restype = vp
        k.QueryFullProcessImageNameW.argtypes = (vp, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = (vp,)
        k.CloseHandle.restype = wintypes.BOOL
        self.pid = os.getpid()
        self._exes: dict[int, str] = {}
        self._cb = self.PROC(self._callback)       # kept alive as long as the hook
        self._hooks: list = []
        self.corners: list[tuple[int, int]] = []

    def start(self) -> bool:
        self.corners = screen_corners()
        for lo, hi in ((0x8000, 0x8003), (0x800B, 0x800B), (0x8017, 0x8018)):
            h = self.user32.SetWinEventHook(lo, hi, None, self._cb, 0, 0, 0)
            if h:
                self._hooks.append(h)
        return bool(self._hooks)

    def stop(self) -> None:
        hooks, self._hooks = self._hooks, []
        for h in hooks:
            try:
                self.user32.UnhookWinEvent(h)
            except Exception:  # noqa: BLE001
                pass

    def _exe(self, pid: int) -> str:
        if pid not in self._exes:
            name = "?"
            try:
                h = self.kernel32.OpenProcess(0x1000, False, pid)
                if h:
                    buf = self.ctypes.create_unicode_buffer(260)
                    n = self.wintypes.DWORD(260)
                    if self.kernel32.QueryFullProcessImageNameW(h, 0, buf, self.ctypes.byref(n)):
                        name = buf.value.replace("\\", "/").rsplit("/", 1)[-1]
                    self.kernel32.CloseHandle(h)
            except Exception:  # noqa: BLE001
                pass
            self._exes[pid] = name
        return self._exes[pid]

    def _callback(self, _hook, event, hwnd, obj, _child, _thread, _ms) -> None:
        try:
            self.on_event(int(event), int(hwnd or 0), int(obj))
        except BaseException:  # noqa: BLE001 - nothing escapes into Windows
            pass

    def on_event(self, event: int, hwnd: int, obj: int) -> None:
        ctypes, wintypes, u = self.ctypes, self.wintypes, self.user32
        if obj != 0 or not hwnd:
            return
        if event != 0x8001 and u.GetAncestor(hwnd, 2) != hwnd:      # GA_ROOT: top-level
            return
        pid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        ours = pid.value == self.pid
        if not ours and event not in (0x8002, 0x8018):
            return
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        if not ours and not near_corner(r.left, r.top, self.corners):
            return
        cn = ctypes.create_unicode_buffer(96)
        u.GetClassNameW(hwnd, cn, 96)
        name = EVENT_NAMES.get(event, hex(event))
        if not ours:
            uitrace.trace("native.other." + name, exe=self._exe(pid.value), cls=cn.value,
                          rect=f"{r.left},{r.top},{r.right - r.left}x{r.bottom - r.top}")
            return
        fb = wintypes.RECT()
        self.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(fb), ctypes.sizeof(fb))
        cloaked = wintypes.DWORD(0)
        self.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
        ex = u.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
        alpha = 255
        if ex & 0x80000:
            a, f = ctypes.c_ubyte(0), wintypes.DWORD(0)
            if u.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(a), ctypes.byref(f)):
                alpha = a.value if f.value & 2 else -1
        uitrace.trace("native." + name, hwnd=hex(hwnd), cls=cn.value,
                      rect=f"{r.left},{r.top},{r.right - r.left}x{r.bottom - r.top}",
                      dwm=f"{fb.left},{fb.top},{fb.right - fb.left}x{fb.bottom - fb.top}",
                      visible=bool(u.IsWindowVisible(hwnd)), alpha=alpha,
                      cloaked=cloaked.value)


def screen_corners() -> list[tuple[int, int]]:
    try:
        from PyQt6.QtGui import QGuiApplication
        return [(s.geometry().x(), s.geometry().y()) for s in QGuiApplication.screens()]
    except Exception:  # noqa: BLE001
        return []


def near_corner(x: int, y: int, corners) -> bool:
    return any(abs(x - cx) <= CORNER_PX and abs(y - cy) <= CORNER_PX for cx, cy in corners)


def install(app) -> bool:
    """With SMARTCAL_DEBUG_UI=1 (uitrace on): the event filter, and on Windows the
    native hook. False (nothing installed) otherwise. Never raises."""
    if _installed or not uitrace.enabled():
        return False
    try:
        f = WindowEvents(app)
        app.installEventFilter(f)
        _installed["filter"] = f
        if sys.platform == "win32" and app.platformName() == "windows":
            hook = NativeHook()
            if hook.start():
                _installed["hook"] = hook
                app.aboutToQuit.connect(uninstall)
        uitrace.trace("wintrace.installed", native="hook" in _installed)
        return True
    except Exception:  # noqa: BLE001
        return False


def uninstall() -> None:
    try:
        hook = _installed.pop("hook", None)
        if hook is not None:
            hook.stop()
        f = _installed.pop("filter", None)
        if f is not None:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(f)
    except Exception:  # noqa: BLE001
        pass
