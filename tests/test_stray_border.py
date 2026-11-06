"""v0.11.2: no stray accent outline when the Day/Week/Month window (or any framed window)
opens.

Recorded 2026-11-06 on the owner's three-monitor desktop: opening the big window from the
popup made DWM draw an empty outline in theme.ACCENT, exactly the window's size, at the
top-left corner of the primary screen ((0, 0), "the other side of my screen" from the
tray) for one composed frame, ~20 ms after the window was shown, on about half of the
opens. No window of any process was visible there (WinEvent hook of every process,
EnumWindows poll at ~400 Hz): it was DWM's edge of our own window, colored while the
native window was still hidden. Window opacity 0 did not hide it; the corner preference
alone never drew it; the same color set once the native window is visible never did
(0 of 25 opens each way, against 8-12 of 25 before).

Offscreen: the edge color is only ever set on a visible native window (the corner
preference may come before). Real display (opt in): the owner's flow, popup -> Day/Week/
Month window, with the details panel or the chat drawer open, re-opened again and again,
while a fast screen grab watches the primary screen's top-left corner and a WinEvent
monitor logs every window of ours."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QWidget

from smartcal.ui import winframe


class FakeDwm:
    """dwmapi / user32 stand-ins: every DwmSetWindowAttribute call with whether the
    widget's native window was visible at that moment."""

    def __init__(self, widget):
        self.widget = widget
        self.calls = []
        self.DwmSetWindowAttribute = self._set

    def _set(self, hwnd, attr, _ref, _size):
        handle = self.widget.windowHandle()
        self.calls.append((attr, handle is not None and handle.isVisible()))
        return winframe.S_OK


@pytest.fixture
def fake_native(monkeypatch, qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    w.resize(200, 120)
    dwm = FakeDwm(w)
    monkeypatch.setattr(winframe, "native_windows", lambda: True)
    monkeypatch.setattr(winframe, "windows_build", lambda: 26100)
    monkeypatch.setattr(winframe, "_win32", lambda: (None, dwm))
    monkeypatch.setattr(winframe, "native_visible",
                        lambda _hwnd: dwm.widget.windowHandle() is not None
                        and dwm.widget.windowHandle().isVisible())
    return w, dwm


def test_edge_color_waits_until_the_window_is_shown(fake_native):
    w, dwm = fake_native
    w.winId()                                       # native, still hidden
    assert winframe.apply_dwm_frame(w) is True
    assert dwm.calls == [(winframe.DWMWA_WINDOW_CORNER_PREFERENCE, False)]
    w.show()
    assert dwm.calls[-1] == (winframe.DWMWA_BORDER_COLOR, True)   # right after the show
    w.hide()
    w.show()                                        # shown again: set once, not again
    assert [a for a, _v in dwm.calls].count(winframe.DWMWA_BORDER_COLOR) == 1


def test_edge_color_set_from_show_event_lands_after_the_native_show(fake_native):
    w, dwm = fake_native

    class Framed(QWidget):
        def showEvent(self, e):                     # as the popup / big window do
            super().showEvent(e)
            winframe.apply_dwm_frame(self)
    f = Framed()
    dwm.widget = f
    try:
        f.resize(100, 80)
        f.show()
        border = [v for a, v in dwm.calls if a == winframe.DWMWA_BORDER_COLOR]
        assert border == [True], dwm.calls          # never on a hidden window
    finally:
        f.close()
        f.deleteLater()


def test_edge_color_at_once_on_a_visible_window(fake_native):
    w, dwm = fake_native
    w.show()
    dwm.calls.clear()
    assert winframe.apply_dwm_frame(w) is True
    assert dwm.calls == [(winframe.DWMWA_WINDOW_CORNER_PREFERENCE, True),
                         (winframe.DWMWA_BORDER_COLOR, True)]


def test_pending_edge_follows_a_recreated_native_window(fake_native):
    w, dwm = fake_native
    w.winId()
    winframe.apply_dwm_frame(w)
    w.show()
    w.close()                                       # Qt 6: the native window is gone
    dwm.calls.clear()
    w.winId()
    winframe.apply_dwm_frame(w)                     # the next open frames the new one
    assert (winframe.DWMWA_BORDER_COLOR, False) not in dwm.calls
    w.show()
    assert dwm.calls[-1] == (winframe.DWMWA_BORDER_COLOR, True)


def test_never_raises(monkeypatch, qtbot):
    monkeypatch.setattr(winframe, "native_windows", lambda: True)
    monkeypatch.setattr(winframe, "windows_build", lambda: 26100)

    def boom():
        raise OSError("no dwm")
    monkeypatch.setattr(winframe, "_win32", boom)
    w = QWidget()
    qtbot.addWidget(w)
    assert winframe.apply_dwm_frame(w) is False
    winframe.border_color_when_shown(w)             # swallowed
    winframe.border_color_when_shown(object())
    assert winframe.set_border_color(1) is False
    assert winframe.native_visible(1) is False



# -- the real Windows display (opt in: it opens real windows) ---------------------------
# grab_dx.py x y w h seconds out.npz allowed [output]: every frame DWM composes (DXGI desktop
# duplication, dxcam) of one rect of the primary screen: per frame, the theme.ACCENT
# pixels that were not so in the first frame, outside `allowed`: their count, the most in
# one row and one column (as test_window_flash.GRAB). The stray edge lasted one composed
# frame (a few ms at a high refresh rate), too short for GDI polling to see reliably, so
# without dxcam (pip install dxcam; or its folder in SMARTCAL_TESTS_PYPATH) the grab falls
# back to GDI and says so ("mode").
GRAB_DX = textwrap.dedent(r"""
    import ctypes, json, os, sys, time
    extra = os.environ.get("SMARTCAL_TESTS_PYPATH")
    if extra:
        sys.path.append(extra)
    import numpy as np
    import dxcam
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    x, y, w, h = map(int, sys.argv[1:5]); secs = float(sys.argv[5]); out = sys.argv[6]
    keep = np.ones((h, w), bool)
    for ax, ay, aw, ah in json.loads(sys.argv[7]):
        keep[max(0, ay - y):max(0, ay + ah - y), max(0, ax - x):max(0, ax + aw - x)] = False
    idx = int(sys.argv[8]) if len(sys.argv) > 8 else None    # (None: the primary)
    cam = dxcam.create(output_idx=idx, output_color="BGR")
    base = None
    times, stats = [], []
    print("ready", flush=True)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < secs:
        fr = cam.grab(region=(x, y, x + w, y + h))
        if fr is None:
            if base is not None:
                time.sleep(0.0003)
            continue
        b, g, r = fr[:, :, 0], fr[:, :, 1], fr[:, :, 2]
        m = (b >= 215) & (g >= 115) & (g <= 165) & (r >= 98) & (r <= 148)   # ~#7b8cf0
        if base is None:
            base = m.copy()
        m &= keep & ~base
        rows, cols = m.sum(axis=1), m.sum(axis=0)
        times.append(time.perf_counter())
        stats.append([int(rows.sum()), int(rows.max()), int(rows.argmax()) + y,
                      int(cols.max()), int(cols.argmax()) + x])
    np.savez(out, times=np.array(times), stats=np.array(stats).reshape(-1, 5),
             mode=np.array("dxgi"))
""")

RUN = textwrap.dedent(r"""
    import faulthandler, json, os, subprocess, sys, time
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QEvent, QRect, QSettings, Qt
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from smartcal.ui import motion, uitrace
    motion.animations_enabled = lambda: True
    uitrace.sink = []
    from smartcal.ui.popup import AgendaPopup, AgendaRow
    from smartcal.ui.window import CalendarWindow
    from test_meeting_actions_ui import NOW, ev, store
    from test_popup_animations import MANY
    evs = MANY[:6] + [ev("d", "Design review", 18, description="Walk through it.",
                         meeting_url="https://zoom.us/j/1")]
    cache, settings = store(tmp, evs)
    qs = QSettings(str(tmp / "window.ini"), QSettings.Format.IniFormat)
    p = AgendaPopup(cache, settings); p.now = lambda: NOW; p.refresh()
    p.attach_chat(lambda: None)
    w = CalendarWindow(cache, settings, qs); w.now = lambda: NOW; w.anchor = NOW.date()
    scr = app.primaryScreen().availableGeometry()
    tray = QRect(scr.right() - 300, scr.bottom() + 5, 24, 40)
    w.tray_geometry = lambda: tray
    def open_window(view):                          # as run.py
        p.dismiss(); w.show_view(view)
    p.view_requested.connect(open_window)
    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents(); app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    def rows():
        return [r for r in p.row_widgets() if isinstance(r, AgendaRow)]
    def rect(r):
        return [r.x(), r.y(), r.width(), r.height()]
    # warm-up: the final rects (and the saved geometry)
    p.show_near_tray(tray); pump(1000); popup_r = p.frameGeometry()
    p.select_row(rows()[0]); pump(700); fly_r = p.flyout().frameGeometry()
    p.request_view("week"); pump(1500); win_r = w.frameGeometry()
    w.close(); pump(600)
    # the corner where DWM drew the stray edge: the primary screen's top-left
    g = app.primaryScreen().geometry()
    corner = QRect(g.x(), g.y(), min(win_r.width() + 60, g.width()),
                   min(win_r.height() + 60, g.height()))
    allowed = [r.adjusted(-2, -2, 2, 2) for r in (popup_r, fly_r, win_r)]
    back = QWidget()                    # a black backdrop: anything new there shows
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(corner.adjusted(0, 0, 20, 20))
    back.show(); pump(400)
    secs = 30.0
    procs = [subprocess.Popen([sys.executable, str(tmp / "monitor.py"), str(os.getpid()),
                               str(secs + 0.5), str(tmp / "mon.json")],
                              stdout=subprocess.PIPE, text=True),
             subprocess.Popen([sys.executable, str(tmp / "grab_dx.py"), str(corner.x()),
                               str(corner.y()), str(corner.width()), str(corner.height()),
                               str(secs), str(tmp / "corner.npz"),
                               json.dumps([rect(r) for r in allowed])],
                              stdout=subprocess.PIPE, text=True)]
    for pr in procs:
        pr.stdout.readline()                             # "ready"
    pump(500)
    opens = 0
    t = QElapsedTimer(); t.start(); k = 0
    while t.elapsed() < (secs - 4) * 1000:
        p.refresh(); p.show_near_tray(tray); pump(900)
        if k % 3 == 1:
            p.select_row(rows()[k % 4]); pump(600)       # the details panel open
        elif k % 3 == 2:
            p.toggle_chat(); pump(600)                   # the chat drawer open
        p.request_view(("week", "day", "month")[k % 3]); opens += 1; pump(1300)
        if p.chat_expanded():
            p.toggle_chat()
        w.close(); pump(500)
        k += 1
    for pr in procs:
        pr.wait()
    border = [f for what, f in uitrace.sink if what == "frame.border"]
    info = {"win": rect(win_r), "popup": rect(popup_r), "fly": rect(fly_r),
            "allowed": [rect(r) for r in allowed], "opens": opens,
            "border_ok": [f.get("ok") for f in border],
            "border_visible": [f.get("visible") for f in border], "pid": os.getpid()}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


def _inside(x, y, rects, pad):
    return any(r[0] - pad <= x < r[0] + r[2] + pad and r[1] - pad <= y < r[1] + r[3] + pad
               for r in rects)


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_no_stray_outline_when_the_window_opens_from_the_popup(tmp_path):
    import json
    import numpy as np
    from smartcal.ui import motion
    from test_window_flash import GRAB, MONITOR
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(motion.ENV_LEVEL, None)
    (tmp_path / "monitor.py").write_text(MONITOR, encoding="utf-8")
    try:
        import importlib.util
        has_dx = importlib.util.find_spec("dxcam") is not None or bool(
            os.environ.get("SMARTCAL_TESTS_PYPATH"))
    except Exception:  # noqa: BLE001
        has_dx = False
    if has_dx:
        (tmp_path / "grab_dx.py").write_text(GRAB_DX, encoding="utf-8")
    else:                                   # GDI polling: same output, may miss a frame
        gdi = GRAB.replace('mode = sys.argv[7]', 'mode = "accent"').replace(
            'json.loads(sys.argv[8]) if len(sys.argv) > 8', 'json.loads(sys.argv[7]) if len(sys.argv) > 7')
        (tmp_path / "grab_dx.py").write_text(gdi, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=240)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    info = json.loads((tmp_path / "info.json").read_text())
    assert info["opens"] >= 8, info["opens"]
    # every open framed the new window (edge color set once it was shown)
    assert len(info["border_ok"]) >= info["opens"] and all(info["border_ok"]), info["border_ok"]
    assert all(info["border_visible"]), info["border_visible"]     # never on a hidden one

    # 1. Native truth: a window of ours is visible only at its final rects.
    log = json.loads((tmp_path / "mon.json").read_text())
    ours = [e for e in log if e["cls"].startswith("Qt") and "titlebar" not in e["cls"]]
    shown = [e for e in ours if e["vis"]]
    assert shown, "the monitor saw nothing"
    # (the popup's opening slide / outro sink, and the panel tucking under it, stay
    # within a few dozen px of their final rects)
    allowed = info["allowed"]
    for e in shown:
        x0, y0, x1, y1 = e["rect"]
        assert _inside(x0, y0, allowed, 48) and _inside(x1 - 1, y1 - 1, allowed, 48), e

    # 2. No accent outline at the primary screen's top-left corner, at any moment.
    z = np.load(tmp_path / "corner.npz")
    times, st = z["times"], z["stats"]
    assert len(times) > 20, len(times)
    if not has_dx:
        assert len(times) / (times[-1] - times[0]) > 60, "grabs too slow to see one frame"
    # an edge: 60+ accent pixels in one row, or 40+ in one column
    edges = np.nonzero((st[:, 1] >= 60) | (st[:, 3] >= 40))[0]
    assert len(edges) == 0, [st[k].tolist() for k in edges[:5]]
