"""v0.11.1: the Day/Week/Month window and its details dialog appear only where they end
up, and only once painted.

Offscreen: the order of things on open (placed, framed and transparent before show();
any later move only while still transparent). Real display (opt in): a WinEvent monitor
logs every show / move of every window of the process, and screen grabs of every screen
look for the accent edge anywhere but the final window rects, for any window at the
primary screen's center (where a stray parentless widget appears), and for white or
restarting frames in the dialog."""
import os
import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QObject

from smartcal.ui import motion, winframe
from smartcal.ui.window import CalendarWindow, EventDetails
from test_window import NOW, ev, make


class OpenOrder(QObject):
    """For one window: its opacity, geometry and frame state at show and at every move."""

    def __init__(self, w, framed):
        super().__init__()
        self.w, self.framed = w, framed
        self.at_show = None
        self.moves_after_show: list[tuple[float, object]] = []
        w.installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:
        if obj is self.w and e.type() == QEvent.Type.Show and self.at_show is None:
            self.at_show = (self.w.windowOpacity(), self.w.geometry(), list(self.framed))
        elif obj is self.w and e.type() == QEvent.Type.Move and self.at_show is not None:
            self.moves_after_show.append((self.w.windowOpacity(), self.w.geometry()))
        return False


@pytest.fixture
def native(monkeypatch):
    """Offscreen, but take the real-display code paths; record frame calls."""
    framed = []
    monkeypatch.setattr(winframe, "native_windows", lambda: True)
    monkeypatch.setattr(winframe, "apply_dwm_frame",
                        lambda w: framed.append((type(w).__name__, w.isVisible())) or True)
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    return framed


def test_details_placed_framed_and_transparent_before_show(qtbot, tmp_path, native,
                                                         monkeypatch):
    from smartcal.ui import placement
    e = ev("1", "Standup", NOW, NOW + timedelta(minutes=30), location="Room 4")
    w = make(qtbot, tmp_path, [e], "week")
    native.clear()
    placed, seen = [], []
    center_over = placement.center_over

    def place(widget, parent):
        center_over(widget, parent)
        placed.append(widget.geometry())
        seen.append(OpenOrder(widget, native))      # watched from before its show
    monkeypatch.setattr(placement, "center_over", place)
    for _ in range(2):                                  # open, then re-open
        w.show_details(e)
        opacity, geom, framed = seen[-1].at_show
        assert opacity == 0.0                           # transparent until painted
        assert geom == placed[-1]                       # shown where it was placed
        assert ("EventDetails", False) in framed        # edge set while still hidden
        # (offscreen Qt nudges a new window by its fake frame: unseen, at opacity 0)
        assert all(op == 0.0 for op, _g in seen[-1].moves_after_show)
        qtbot.waitUntil(lambda: not w.details.revealing() and not w.details.building(),
                        timeout=3000)
        assert w.details.windowOpacity() == 1.0
    w.details.close()
    assert w.details.windowOpacity() == 1.0 and not w.details.revealing()


def test_details_without_animations_open_at_full_opacity(qtbot, tmp_path, native,
                                                         monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    e = ev("1", "Standup", NOW, NOW + timedelta(minutes=30))
    w = make(qtbot, tmp_path, [e], "week")
    w.show_details(e)
    assert w.details.windowOpacity() == 1.0 and not w.details.revealing()


def test_window_open_and_reopen_transparent_until_painted(qtbot, tmp_path, native):
    from PyQt6.QtCore import QSettings

    from smartcal.db import Cache
    from smartcal.settings import Settings
    cache = Cache(tmp_path / "c.db")
    qs = QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat)
    # first open (placed near the tray), re-open, then a restart (saved geometry)
    for opens in (2, 1):
        w = CalendarWindow(cache, Settings(tmp_path / "s.json"), qs)
        qtbot.addWidget(w)
        w.now = lambda: NOW
        for _ in range(opens):
            order = OpenOrder(w, native)
            w.show_view("week")
            assert order.at_show[0] == 0.0
            # the first open is placed again once the real frame is known: unseen
            assert all(op == 0.0 for op, _g in order.moves_after_show)
            qtbot.waitUntil(lambda: not w.revealing(), timeout=3000)
            assert w.windowOpacity() == 1.0
            w.removeEventFilter(order)
            w.hide()


# -- the real Windows display (opt in: it opens real windows) ---------------------------
MONITOR = textwrap.dedent(r"""
    # monitor.py pid seconds out.json: every show / hide / move of a top-level window of
    # process `pid` (out-of-context WinEvent hook) with its rect and layered alpha.
    import ctypes, json, sys, time
    from ctypes import wintypes
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    pid, secs, out = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    u = ctypes.WinDLL("user32"); vp = ctypes.c_void_p
    PROC = ctypes.WINFUNCTYPE(None, vp, wintypes.DWORD, vp, wintypes.LONG, wintypes.LONG,
                              wintypes.DWORD, wintypes.DWORD)
    u.SetWinEventHook.restype = vp
    u.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, vp, PROC, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD]
    u.GetWindowRect.argtypes = [vp, ctypes.POINTER(wintypes.RECT)]
    u.IsWindowVisible.argtypes = [vp]
    u.GetAncestor.argtypes = [vp, ctypes.c_uint]; u.GetAncestor.restype = vp
    u.GetWindowLongW.argtypes = [vp, ctypes.c_int]; u.GetWindowLongW.restype = wintypes.LONG
    u.GetClassNameW.argtypes = [vp, wintypes.LPWSTR, ctypes.c_int]
    u.GetLayeredWindowAttributes.argtypes = [vp, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]
    u.PeekMessageW.argtypes = [vp, vp, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    log = []
    @PROC
    def cb(hook, event, hwnd, obj, child, thread, ms):
        if obj != 0 or not hwnd or u.GetAncestor(hwnd, 2) != hwnd:
            return
        r = wintypes.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r))
        ex = u.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
        alpha = 255
        if ex & 0x80000:
            a = ctypes.c_ubyte(); f = wintypes.DWORD()
            if u.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(a), ctypes.byref(f)) \
                    and f.value & 2:
                alpha = a.value
        cn = ctypes.create_unicode_buffer(128); u.GetClassNameW(hwnd, cn, 128)
        log.append(dict(t=time.perf_counter(), ev=event, hwnd=hwnd, cls=cn.value,
                        rect=[r.left, r.top, r.right, r.bottom],
                        vis=bool(u.IsWindowVisible(hwnd)), alpha=alpha))
    hook = u.SetWinEventHook(0x8000, 0x800B, None, cb, pid, 0, 0)
    msg = wintypes.MSG(); t0 = time.perf_counter()
    print("ready", flush=True)
    while time.perf_counter() - t0 < secs:
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.001)
    json.dump(log, open(out, "w"))
""")

# grab.py x y w h seconds out.npz mode [allowed]: GDI grabs of one rectangle in a loop.
# mode "accent": per frame, the pixels of theme.ACCENT's color that were not so in the
# first frame and lie outside the `allowed` rects (JSON, global): their count and the most
# of them in one row / one column (a window edge is a long straight run of them; another
# app's text or icons are not), so whole screens can be watched; "raw": the frames.
GRAB = textwrap.dedent(r"""
    import ctypes, json, sys, time
    import numpy as np
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    x, y, w, h = map(int, sys.argv[1:5]); secs = float(sys.argv[5]); out = sys.argv[6]
    mode = sys.argv[7]
    u, g = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    vp, i = ctypes.c_void_p, ctypes.c_int
    u.GetDC.restype = vp; u.GetDC.argtypes = [vp]
    g.CreateCompatibleDC.restype = vp; g.CreateCompatibleDC.argtypes = [vp]
    g.CreateCompatibleBitmap.restype = vp; g.CreateCompatibleBitmap.argtypes = [vp, i, i]
    g.SelectObject.argtypes = [vp, vp]; g.SelectObject.restype = vp
    g.BitBlt.argtypes = [vp, i, i, i, i, vp, i, i, ctypes.c_uint]
    g.GetDIBits.argtypes = [vp, vp, ctypes.c_uint, ctypes.c_uint, vp, vp, ctypes.c_uint]
    class BIH(ctypes.Structure):
        _fields_ = [("s", ctypes.c_uint32), ("w", ctypes.c_int32), ("h", ctypes.c_int32),
                    ("p", ctypes.c_uint16), ("b", ctypes.c_uint16), ("c", ctypes.c_uint32),
                    ("z", ctypes.c_uint32), ("x1", ctypes.c_int32), ("y1", ctypes.c_int32),
                    ("u", ctypes.c_uint32), ("i", ctypes.c_uint32)]
    sdc = u.GetDC(None); mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h); g.SelectObject(mdc, bmp)
    bih = BIH(); bih.s = 40; bih.w = w; bih.h = -h; bih.p = 1; bih.b = 32
    buf = (ctypes.c_ubyte * (w * h * 4))()
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
    keep = np.ones((h, w), bool)
    for ax, ay, aw, ah in (json.loads(sys.argv[8]) if len(sys.argv) > 8 else []):
        keep[max(0, ay - y):max(0, ay + ah - y), max(0, ax - x):max(0, ax + aw - x)] = False
    times, frames, stats = [], [], []
    base = None
    print("ready", flush=True)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < secs:
        g.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020)
        g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
        times.append(time.perf_counter())
        if mode == "raw":
            frames.append(arr[:, :, :3].copy()); continue
        b, gg, r = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        m = (b >= 215) & (gg >= 115) & (gg <= 165) & (r >= 98) & (r <= 148)   # ~#7b8cf0
        if base is None:
            base = m.copy()
        m &= keep & ~base
        rows, cols = m.sum(axis=1), m.sum(axis=0)
        stats.append([int(rows.sum()), int(rows.max()), int(rows.argmax()) + y,
                      int(cols.max()), int(cols.argmax()) + x])
    if mode == "raw":
        np.savez(out, times=np.array(times), frames=np.stack(frames))
    else:
        np.savez(out, times=np.array(times), stats=np.array(stats).reshape(-1, 5))
""")

RUN = textwrap.dedent(r"""
    import faulthandler, json, os, subprocess, sys, time
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QEvent, QRect, QSettings, Qt
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    from smartcal.ui import motion
    motion.animations_enabled = lambda: True
    from smartcal.db import Cache
    from smartcal.models import CalendarInfo
    from smartcal.settings import Settings
    from smartcal.ui.window import CalendarWindow
    from test_window import NOW
    from test_window_motion import busy
    cache = Cache(tmp / "c.db")
    cache.upsert_calendars("a", [CalendarInfo("primary", "Primary", "#4285f4", True)])
    cache.upsert_events("a", busy())
    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents(); app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    scr = app.primaryScreen().availableGeometry()
    tray = QRect(scr.right() - 300, scr.bottom() + 5, 24, 40)
    def new_window():
        w = CalendarWindow(cache, Settings(tmp / "s.json"),
                           QSettings(str(tmp / "q.ini"), QSettings.Format.IniFormat))
        w.now = lambda: NOW; w.anchor = NOW.date(); w.tray_geometry = lambda: tray
        return w
    def tile(w):
        return next(t for t in w.time_view.tiles() if t.event.id == "p")
    def rect(r):
        return [r.x(), r.y(), r.width(), r.height()]
    # warm-up: learn where the window and the dialog end up (and save the geometry)
    w = new_window(); w.show_view("week"); pump(1200)
    w.show_details(tile(w).event); pump(600)
    dlg = w.details.frameGeometry(); w.details.close(); pump(200)
    win = w.frameGeometry(); w.hide(); pump(300); w.deleteLater(); pump(200)
    secs = 9.5
    c = scr.center()                                 # a stray window's default spot
    center = QRect(c.x() - 420, c.y() - 320, 840, 640)
    back = QWidget()            # a black backdrop at the center: any change is a window
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(center.adjusted(-20, -20, 20, 20))
    back.show(); pump(400)
    screens = [("screen%d" % k, s.geometry(), "accent") for k, s in enumerate(app.screens())]
    fast = [("center", center, "raw"), ("dialog", dlg.adjusted(-24, -24, 24, 24), "raw")]
    marks = []
    def mark(name):
        marks.append((name, time.perf_counter()))
    # Two passes of the same opens: every screen at once (slow grabs, GDI is shared),
    # then the screen center and the dialog alone (fast enough for single frames).
    for k, grabs in enumerate((screens, fast)):
        procs = [subprocess.Popen([sys.executable, str(tmp / "monitor.py"), str(os.getpid()),
                                   str(secs + 0.5), str(tmp / ("mon%d.json" % k))],
                                  stdout=subprocess.PIPE, text=True)]
        for name, g, mode in grabs:
            procs.append(subprocess.Popen(
                [sys.executable, str(tmp / "grab.py"), str(g.x()), str(g.y()), str(g.width()),
                 str(g.height()), str(secs), str(tmp / (name + ".npz")), mode,
                 json.dumps([rect(r.adjusted(-2, -2, 2, 2)) for r in (win, dlg)])],
                stdout=subprocess.PIPE, text=True))
        for p in procs:
            p.stdout.readline()                      # "ready"
        pump(700)
        w = new_window()                             # a restart: the saved geometry
        mark("open"); w.show_view("week"); pump(1300)
        for _ in range(2):
            mark("details"); w.show_details(tile(w).event); pump(1100)
            mark("details_close"); w.details.close(); pump(300)
        mark("hide"); w.hide(); pump(500)
        mark("open"); w.show_view("week"); pump(1300)    # re-open
        mark("details"); w.show_details(tile(w).event); pump(1100)
        mark("hide"); w.hide(); pump(400)            # closes the dialog too
        for p in procs:
            p.wait()
        frame_now = w.frameGeometry()
        w.deleteLater(); pump(200)
    grabs = screens + fast
    info = {"marks": marks, "win": rect(win), "dlg": rect(dlg), "pid": os.getpid(),
            "grabs": [(n, rect(g), m) for n, g, m in grabs], "frame_now": rect(frame_now)}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


def _inside(x, y, rects, pad):
    return any(r[0] - pad <= x < r[0] + r[2] + pad and r[1] - pad <= y < r[1] + r[3] + pad
               for r in rects)


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_window_and_details_only_where_they_end_up(tmp_path):
    import json
    import numpy as np
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(motion.ENV_LEVEL, None)
    (tmp_path / "monitor.py").write_text(MONITOR, encoding="utf-8")
    (tmp_path / "grab.py").write_text(GRAB, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=240)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    info = json.loads((tmp_path / "info.json").read_text())
    win, dlg = info["win"], info["dlg"]
    assert info["frame_now"] == win                  # opened where it was saved

    # 1. Native truth, every screen, every moment: a window of ours is visible only at
    # its final rect, and the big window and the dialog are shown fully transparent.
    log = sum((json.loads((tmp_path / f"mon{k}.json").read_text()) for k in (0, 1)), [])
    ours = [e for e in log if e["cls"].startswith("Qt") and "titlebar" not in e["cls"]]
    shown = [e for e in ours if e["vis"]]
    assert shown, "the monitor saw nothing"
    allowed = [win, dlg]
    for e in shown:
        x0, y0, x1, y1 = e["rect"]
        # (Windows' rect includes the invisible resize borders: a few px wider)
        assert _inside(x0, y0, allowed, 16) and _inside(x1 - 1, y1 - 1, allowed, 16), e
    for e in ours:
        if e["ev"] == 0x8002:                        # EVENT_OBJECT_SHOW
            assert e["alpha"] == 0, e                # shown before painted: transparent

    # 2. The accent edge, on every screen: never outside the two final rects.
    for name, g, mode in info["grabs"]:
        z = np.load(tmp_path / (name + ".npz"))
        n = len(z["times"])
        assert n > 20, (name, n)
        if mode != "accent":
            continue
        st = z["stats"]
        # a window edge: 60+ accent pixels in one row, or 40+ in one column
        edges = np.nonzero((st[:, 1] >= 60) | (st[:, 3] >= 40))[0]
        assert len(edges) == 0, (name, [st[k].tolist() for k in edges[:5]])

    # 3. Nothing ever appears at the primary screen's center (outside our rects).
    z = np.load(tmp_path / "center.npz")
    frames, gx, gy = z["frames"].astype(np.int16), info["grabs"][-2][1][0], info["grabs"][-2][1][1]
    hh, ww = frames.shape[1:3]
    mask = np.ones((hh, ww), bool)
    for rr in allowed:
        x0, y0 = max(0, rr[0] - gx - 40), max(0, rr[1] - gy - 40)
        mask[y0:max(0, rr[1] + rr[3] - gy + 40), x0:max(0, rr[0] + rr[2] - gx + 40)] = False
    changed = ((np.abs(frames - frames[0]).max(axis=3) > 40) & mask).sum(axis=(1, 2))
    assert changed.max() < 30, f"something at the screen center: frames {np.nonzero(changed >= 30)[0][:5]}"

    # 4. The dialog: never white, and its content never shows then restarts.
    z = np.load(tmp_path / "dialog.npz")
    frames, times = z["frames"], z["times"]
    assert len(frames) / (times[-1] - times[0]) > 60     # fast enough to see one frame
    white = frames.min(axis=3) > 200
    bad = np.nonzero((white.mean(axis=1) > 0.3).sum(axis=1) > 2)[0]
    assert len(bad) == 0, f"{len(bad)} dialog frames with white, first at {bad[:5]}"
    inner = frames[:, 24 + 40:-24 - 8, 24 + 8:-24 - 8].astype(np.int16)  # the client area
    checked = 0
    for name, t0 in info["marks"]:
        if name != "details" or not times[0] < t0 < times[-1] - 1.0:
            continue                                     # (the first pass: not grabbed)
        checked += 1
        i0, i1 = int(np.searchsorted(times, t0)), int(np.searchsorted(times, t0 + 0.9))
        final = inner[i1]
        far = np.abs(inner[i0:i1] - final).mean(axis=(1, 2, 3))   # how far from the end
        assert far[0] > 2, "the dialog was already there"
        near = int(np.argmax(far < far[0] * 0.3))
        assert near > 0, far.tolist()
        # once close to its final look it never jumps back (no restart, no blank frame)
        assert far[near:].max() < far[0] * 0.6, ("content restarted", far.round(1).tolist())
    assert checked == 3, checked
