"""v0.8.6: no white strip in the details panel (it slides out from UNDER the popup,
stacked below it, fully painted, never through a growing window mask) and the rounded,
accent-edged frame of the popup and the panel. Offscreen Qt (the native calls are
replaced by fakes); with SMARTCAL_TESTS_REAL_DISPLAY=1 a real-window run captures the
screen during slides and asserts no white block ever shows."""
import ctypes
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QColor, QRegion

from smartcal.ui import flyout as flymod
from smartcal.ui import motion, theme, winframe
from smartcal.ui.flyout import NOTHING_SHOWN, slide_mode
from test_meeting_actions_ui import click, ev, rows
from test_popup_animations import MANY, settled_popup

NOTES = "\n".join(f"line {i} of the notes" for i in range(3))   # (no scrollbar)


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


@pytest.fixture
def stacking(monkeypatch):
    """Stack mode with fake native calls: stacking works, the panel has no owner."""
    calls = []
    monkeypatch.setenv(flymod.SLIDE_MODE_ENV, "stack")
    monkeypatch.setattr(winframe, "stack_below",
                        lambda w, above: calls.append((w, above)) or True)
    monkeypatch.setattr(winframe, "has_native_owner", lambda w: False)
    return calls


def white_blocks(img, size=5) -> int:
    """5x5 blocks of near-white pixels (text strokes are thinner than that)."""
    w, h = img.width(), img.height()
    found = 0
    for y in range(0, h - size, 2):
        for x in range(0, w - size, 2):
            if all(min(QColor(img.pixel(x + i, y + j)).getRgb()[:3]) > 225
                   for i in range(size) for j in range(size)):
                found += 1
    return found


# -- palette and frame -------------------------------------------------------------------
def test_accent_is_one_constant_and_soft_line_is_derived():
    assert QColor(theme.ACCENT).isValid()
    assert theme.ACCENT_SOFT == theme.mix(theme.ACCENT, theme.BG, 0.6)
    assert "border:none" in theme.window_style("popup")
    assert theme.contrast_ratio(theme.ACCENT, theme.BG) >= 3.0     # a visible edge


def test_shape_region_is_rounded():
    r = winframe.shape_region(250, 120, 8)
    assert not r.contains(QPoint(0, 0)) and not r.contains(QPoint(249, 119))
    assert r.contains(QPoint(125, 0)) and r.contains(QPoint(0, 60))
    assert r.contains(QPoint(4, 4)) and r.boundingRect() == QRect(0, 0, 250, 120)
    assert winframe.shape_region(0, 10).isEmpty()


def test_native_calls_are_inert_offscreen_and_never_raise():
    assert not winframe.native_windows()
    assert winframe.apply_dwm_frame(object()) is False
    assert winframe.stack_below(object(), object()) is False
    assert winframe.has_native_owner(object()) is None
    winframe.drop_native_owner(object())                  # swallowed


def test_win32_prototypes_are_private():
    if sys.platform != "win32":
        pytest.skip("Win32")
    before = ctypes.windll.user32.SetWindowPos.argtypes
    user32, dwmapi = winframe._win32()
    assert ctypes.windll.user32.SetWindowPos.argtypes is before
    assert user32.SetWindowPos.argtypes[0] is ctypes.c_void_p      # 64-bit HWND
    assert dwmapi.DwmSetWindowAttribute.restype is ctypes.c_long


def test_slide_mode_choices(monkeypatch):
    monkeypatch.delenv(flymod.SLIDE_MODE_ENV, raising=False)
    assert slide_mode() == "slide"                        # offscreen: the masked slide
    monkeypatch.setattr(winframe, "native_windows", lambda: True)
    assert slide_mode() == "stack"                        # the real Windows platform
    for mode in ("fade", "slide", "stack"):
        monkeypatch.setenv(flymod.SLIDE_MODE_ENV, mode)
        assert slide_mode() == mode


def test_fallback_frame_mask_clips_the_slide_mask_to_the_rounded_shape(qtbot, tmp_path):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    fly.frame_masked = True                               # as on Windows 10
    fly._apply_mask(QRegion(0, 0, 40, fly.height()))
    assert not fly.mask().contains(QPoint(0, 0))          # rounded corner cut
    assert fly.mask().contains(QPoint(20, fly.height() // 2))
    assert not fly.mask().contains(QPoint(60, 10))        # still the slide's part only
    fly._apply_mask(None)
    assert fly.mask() == winframe.shape_region(fly.width(), fly.height())
    fly._apply_mask(QRegion())
    assert fly.mask() == NOTHING_SHOWN
    p.frame_masked = True
    p.resize(p.width(), p.height() - 10)
    assert p.mask() == winframe.shape_region(p.width(), p.height())


def test_popup_and_panel_paint_no_white_blocks(qtbot, tmp_path):
    p = settled_popup(qtbot, tmp_path, MANY[:4] + [ev("l", "Long", 18, description=NOTES,
                                                     meeting_url="https://zoom.us/j/1")])
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    assert white_blocks(p.grab().toImage()) == 0
    assert white_blocks(fly.grab().toImage()) == 0


# -- stack mode (fake native calls) ----------------------------------------------------
def test_stacked_panel_waits_for_paint_then_slides_out_unmasked(qtbot, tmp_path, animated,
                                                                 stacking):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert fly.mode() == "stack" and fly.isVisible()
    assert fly.windowHandle().transientParent() is None   # no native owner
    assert fly.parentWidget() is p                        # same Qt group and lifetime
    assert (fly, p) in stacking                           # right below the popup
    assert fly.pos() == QPoint(p.x(), fly.y())            # tucked under it
    masks = []
    fly._anim.valueChanged.connect(lambda _v: masks.append(fly.mask()))
    qtbot.waitUntil(lambda: not fly.slide_pending(), timeout=1000)
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.pos() == fly.docked_pos()
    assert masks and all(m.isEmpty() for m in masks)     # never a window mask
    assert fly.mask().isEmpty()
    n = len(stacking)
    click(qtbot, rows(p)[0])                              # and back under it
    assert len(stacking) > n
    qtbot.waitUntil(lambda: not fly.isVisible())
    assert fly.mask().isEmpty()


def test_stack_failure_falls_back_to_the_masked_slide(qtbot, tmp_path, animated,
                                                      monkeypatch):
    monkeypatch.setenv(flymod.SLIDE_MODE_ENV, "stack")
    monkeypatch.setattr(winframe, "stack_below", lambda w, above: False)
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert fly.mode() == "slide" and fly.isVisible()
    assert fly.mask() == NOTHING_SHOWN                    # v0.8.5's way, fully behind
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.pos() == fly.docked_pos()


def test_stack_mode_with_animations_off_docks_at_once(qtbot, tmp_path, stacking):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert fly.mode() == "stack" and not fly.sliding() and not fly.slide_pending()
    assert fly.pos() == fly.docked_pos() and fly.mask().isEmpty()
    click(qtbot, rows(p)[0])
    assert not fly.isVisible()


def test_close_or_hide_while_waiting_for_paint(qtbot, tmp_path, animated, stacking):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    fly._paint_wait.stop()                                # hold it in the waiting state
    fly._pending_ms = motion.FLYOUT_OPEN_MS
    p.close_flyout(restore_focus=False)
    assert not fly.isVisible() and not fly.slide_pending()
    click(qtbot, rows(p)[1])
    fly._paint_wait.stop()
    fly._pending_ms = motion.FLYOUT_OPEN_MS
    p.hide()
    assert not fly.isVisible() and not fly.slide_pending()
    fly._start_pending_slide()                            # a late timer: nothing happens
    assert not fly.isVisible()


def test_switching_meetings_mid_wait_keeps_it_tucked(qtbot, tmp_path, animated, stacking):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    fly._paint_wait.stop()
    fly._pending_ms = motion.FLYOUT_OPEN_MS
    click(qtbot, rows(p)[1])                              # placed again: stays tucked
    assert fly.slide_pending() and fly.pos().x() == p.x()
    fly._start_pending_slide()
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.pos() == fly.docked_pos() and fly.event_shown.title == "Event 1"


# -- the real Windows platform (opt in: it opens real windows) ---------------------------
CAPTURE = textwrap.dedent(r"""
    # capture.py x y w h seconds out.npy: GDI screen grabs of a rectangle, in a loop
    import ctypes, sys, time
    import numpy as np
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    x, y, w, h = map(int, sys.argv[1:5]); secs = float(sys.argv[5]); out = sys.argv[6]
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
    frames = []; t0 = time.perf_counter()
    while time.perf_counter() - t0 < secs:
        g.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020)
        g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
        frames.append(np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy())
    np.save(out, np.stack(frames))
""")

RUN = textwrap.dedent(r"""
    import faulthandler, json, subprocess, sys
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QRect, Qt
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    from smartcal.ui import motion
    motion.animations_enabled = lambda: True
    from smartcal.ui.popup import AgendaPopup, AgendaRow
    from test_meeting_actions_ui import NOW, store
    from test_popup_animations import MANY
    cache, settings = store(tmp, MANY)
    p = AgendaPopup(cache, settings); p.now = lambda: NOW; p.refresh()
    scr = app.primaryScreen().availableGeometry()
    tray = QRect(scr.right() - 40, scr.bottom() + 5, 20, 20)
    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents()
    def rows():
        return [r for r in p.row_widgets() if isinstance(r, AgendaRow)]
    p.show_near_tray(tray); pump(400); p.select_row(rows()[0]); pump(400)
    fly = p.flyout()
    info = {"mode": fly.mode(), "popup_dwm": p.frame_by_dwm, "panel_dwm": fly.frame_by_dwm,
            "owner": fly.has_native_owner(fly)}
    r = fly.geometry().united(p.geometry())
    edge = p.x() - r.x()
    p.hide(); pump(200)
    back = QWidget()                    # a black backdrop: any white is ours
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(r.adjusted(-20, -20, 20, 20))
    back.show(); pump(300)
    cap = subprocess.Popen([sys.executable, str(tmp / "capture.py"), str(r.x()), str(r.y()),
                            str(r.width()), str(r.height()), "7", str(tmp / "cap.npy")])
    pump(400)
    t = QElapsedTimer(); t.start(); k = 0
    while t.elapsed() < 5500:           # open, switch, close mid-slide, chat, hide
        p.show_near_tray(tray); pump(200 if k % 2 else 60)
        rs = rows(); p.toggle_row(rs[k % 4]); pump(40 if k % 3 == 0 else 250)
        p.toggle_row(rs[(k + 1) % 4]); pump(250)
        p.toggle_row(rs[(k + 1) % 4]); pump(60 if k % 2 else 250)
        p.toggle_row(rs[k % 4]); pump(120)
        p.hide(); pump(120); k += 1
    cap.wait()
    info["edge"] = edge
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_panel_never_shows_white(tmp_path):
    import json
    import numpy as np
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(flymod.SLIDE_MODE_ENV, None)
    (tmp_path / "capture.py").write_text(CAPTURE, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    info = json.loads((tmp_path / "info.json").read_text())
    assert info["mode"] == "stack" and info["owner"] is False, info
    assert info["popup_dwm"] and info["panel_dwm"], info     # Windows 11 rounding
    frames = np.load(tmp_path / "cap.npy")
    assert len(frames) > 200
    panel = frames[:, :, :info["edge"] + 8]                 # the panel's side
    white = panel.min(axis=3) > 200
    # a white block: a column more than 30% white (text never fills that much)
    bad = np.nonzero((white.mean(axis=1) > 0.3).sum(axis=1) > 2)[0]
    assert len(bad) == 0, f"{len(bad)} frames with white, first at {bad[:5]}"
