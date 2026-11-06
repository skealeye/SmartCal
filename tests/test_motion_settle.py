"""v0.8.4: easing with a slight settle (clamped), the very first open slides up like every
later one (also through the second-launch path), and the details panel never shows a
white frame (it never goes through a transparent layered window; erases are dark).
Offscreen Qt; animations turned on per test through motion.animations_enabled."""
import ctypes
import sys
import time

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPoint, QRect
from PyQt6.QtGui import QColor

from smartcal.ui import motion, nativebg, theme
from smartcal.ui.flyout import NOTHING_SHOWN
from smartcal.ui.popup import MAX_HEIGHT, AgendaPopup
from smartcal.ui.tray import TrayController
from test_meeting_actions_ui import NOW, click, ev, rows, settle_qt, store
from test_popup_animations import MANY, docked_pos, settled_popup
from test_popup_flyout import AREA, BOTTOM_RIGHT

TOP_EDGE_TRAY = QRect(980, 10, 20, 20)      # a taskbar at the top: slides down
TOP_EDGE_AREA = QRect(0, 40, 1000, 800)


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


def samples(kind, n=400):
    return [motion.ease(kind, i / n) for i in range(n + 1)]


# -- the curves ------------------------------------------------------------------------
def test_settle_curve_overshoots_slightly_and_lands_exactly():
    c = motion.curve(motion.SETTLE)
    assert c.type().name == "OutBack" and 0.6 <= c.overshoot() == motion.OVERSHOOT <= 1.0
    vals = samples(motion.SETTLE)
    assert vals[0] == 0.0 and vals[-1] == 1.0
    assert 1.005 < max(vals) < 1.05                     # slight: passes the end, settles
    peak = vals.index(max(vals))
    assert all(a >= b for a, b in zip(vals[peak:], vals[peak + 1:]))   # then back down


def test_fade_and_glide_curves_never_leave_0_to_1():
    for kind in (motion.FADE, motion.GLIDE, "enter", "anything"):
        vals = samples(kind)
        assert vals[0] == 0.0 and vals[-1] == 1.0
        assert all(0.0 <= v <= 1.0 for v in vals), kind
        assert all(a <= b + 1e-9 for a, b in zip(vals, vals[1:])), kind   # monotonic
    assert motion.curve(motion.FADE).type().name == "OutCubic"
    assert motion.curve(motion.GLIDE).type().name == "BezierSpline"
    assert motion.ease(motion.GLIDE, 0.5) > 0.8          # an ease-out with weight
    assert motion.ease(motion.SETTLE, -1) == 0.0 and motion.ease(motion.SETTLE, 2) == 1.0


def test_clamp_helpers():
    assert motion.clamp(5, 0, 3) == 3 and motion.clamp(-1, 3, 0) == 0
    a, b = QPoint(10, 50), QPoint(100, 50)
    assert motion.clamp_between(QPoint(-20, 55), a, b) == QPoint(10, 50)
    assert motion.clamp_between(QPoint(130, 40), b, a) == QPoint(100, 50)
    assert motion.clamp_between(QPoint(40, 50), a, b) == QPoint(40, 50)


def test_fades_use_plain_ease_out(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    assert p._show_fade.easingCurve().type().name == "OutCubic"
    row = rows(p)[0]
    anim = motion.tween(row, 0.0, 1.0, 100, lambda v: None)     # the default: a fade
    assert anim.easingCurve().type().name == "OutCubic"
    anim.stop()


# -- clamping: the overshoot never goes out of bounds ---------------------------------------
def test_popup_slide_overshoot_stays_inside_work_area(qtbot, tmp_path, animated):
    cache, settings = store(tmp_path, MANY)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    for tray, area in ((BOTTOM_RIGHT, AREA), (TOP_EDGE_TRAY, TOP_EDGE_AREA)):
        settle_qt()
        p._area = lambda point=None, a=area: a
        seen = []
        p._show_slide.valueChanged.connect(lambda _v: seen.append(QRect(p.geometry())))
        p.show_near_tray(tray)
        docked = p.docked_geometry()
        qtbot.waitUntil(lambda: not p.showing_animated())
        p._show_slide.valueChanged.disconnect()
        assert seen and all(area.contains(g) for g in seen), (tray, seen)
        assert p.pos() == docked.topLeft()               # lands exactly
        # Way past the docked spot (any overshoot): kept inside the work area.
        p._set_show_pos(QPoint(docked.x(), area.bottom() + 200))
        assert area.contains(p.geometry())
        p._set_show_pos(QPoint(docked.x(), area.top() - 200))
        assert area.contains(p.geometry())
        p.hide()


def test_panel_slide_never_passes_dock_or_tucked(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    docked, tucked = docked_pos(p), QPoint(p.x(), p.y())
    seen = []
    fly._anim.valueChanged.connect(lambda _v: seen.append(fly.pos()))
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.pos() == docked and all(docked.x() <= q.x() <= tucked.x() for q in seen)
    fly._set_slide_pos(docked - QPoint(40, 0))          # an overshoot past the dock
    assert fly.pos() == docked                          # no gap beside the popup
    fly._tucked = QPoint(tucked)
    fly._set_slide_pos(tucked + QPoint(40, 0))          # past tucked: never pokes out
    assert fly.pos() == tucked


def test_panel_height_overshoot_never_taller_than_popup(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding() and not fly.height_animating())
    fly._height_target = p.height()
    fly._resize_height(p.height() + 30)                 # an overshoot frame
    assert fly.height() == p.height()
    fly._resize_height(-5)
    assert fly.height() == 1


def test_popup_height_tween_settles_clamped_and_exact(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)], tray=BOTTOM_RIGHT)
    p.attach_chat(lambda: None)
    heights = []
    p.toggle_chat()
    anim = p.height_animation()
    assert anim is not None and anim.easingCurve().type().name == "Linear"
    anim.valueChanged.connect(lambda _v: heights.append(p.height()))
    qtbot.waitUntil(lambda: not p.height_animating())
    final = p.final_height()
    assert p.height() == final and heights
    room = min(MAX_HEIGHT, AREA.height() - 24)
    assert all(1 <= h <= max(room, final) for h in heights)
    assert p.chat().graphicsEffect() is None or p.chat().graphicsEffect().opacity() <= 1.0


# -- the first open slides up -------------------------------------------------------------
def _fresh_popup(qtbot, tmp_path, events=MANY):
    settle_qt()
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    p._area = lambda point=None: AREA
    qtbot.addWidget(p)
    p.refresh()
    return p, cache, settings


def test_first_open_waits_for_first_paint_then_slides(qtbot, tmp_path, animated):
    p, *_ = _fresh_popup(qtbot, tmp_path)
    assert not p.isVisible()                            # never shown yet: the first open
    p.show_near_tray(BOTTOM_RIGHT)
    docked = p.docked_geometry().topLeft()
    # Set up but not started yet: hidden behind opacity 0 at the start position.
    assert p.showing_animated() and p._show_pending
    assert not p._show_anim.state().name == "Running"
    assert p.pos() != docked and p.windowOpacity() < 0.05
    qtbot.waitUntil(lambda: p._show_anim.state().name == "Running")
    assert not p._show_pending and p._show_slide.startValue() != docked
    qtbot.waitUntil(lambda: not p.showing_animated())
    assert p.pos() == docked and p.windowOpacity() == 1.0


def test_slow_first_paint_still_shows_the_whole_slide(qtbot, tmp_path, animated):
    # The bug: started before the (slow) first paint, the slide was over before the
    # popup first appeared. Now it starts after that frame, so every step is seen.
    p, *_ = _fresh_popup(qtbot, tmp_path)
    painted = []

    class SlowFirstPaint(QObject):
        def eventFilter(self, obj, e):
            if obj is p and e.type() == QEvent.Type.Paint and not painted:
                # The opening has not started its clock yet (it would run during this
                # slow paint, and be over before the popup first appears).
                painted.append(p._show_anim.state().name)
                time.sleep(motion.POPUP_MS * 2 / 1000)   # longer than the whole slide
            return False
    spy = SlowFirstPaint(p)
    p.installEventFilter(spy)
    steps = []
    p._show_slide.valueChanged.connect(lambda v: steps.append(QPoint(v)))
    p.show_near_tray(BOTTOM_RIGHT)
    docked = p.docked_geometry().topLeft()
    qtbot.waitUntil(lambda: not p.showing_animated())
    assert painted == ["Stopped"]
    distinct = {q.y() for q in steps}
    assert len(distinct) >= 3, steps                  # it moved, frame by frame
    assert steps[0].y() > docked.y() + 10             # from below (bottom taskbar)
    assert p.pos() == docked and p.windowOpacity() == 1.0


def test_first_open_starts_even_without_a_paint(qtbot, tmp_path, animated):
    p, *_ = _fresh_popup(qtbot, tmp_path)
    p._first_frame_painted = lambda: None             # no paint ever reported
    p.show_near_tray(BOTTOM_RIGHT)
    assert p._show_pending and p.show_start_fallback.isActive()
    qtbot.waitUntil(lambda: not p.showing_animated(), timeout=2000)
    assert p.pos() == p.docked_geometry().topLeft() and p.windowOpacity() == 1.0


def test_hidden_while_waiting_for_first_paint_ends_cleanly(qtbot, tmp_path, animated):
    p, *_ = _fresh_popup(qtbot, tmp_path)
    p.show_near_tray(BOTTOM_RIGHT)
    assert p._show_pending
    p.hide()
    assert not p.showing_animated() and not p.show_start_fallback.isActive()
    assert p.windowOpacity() == 1.0
    settle_qt()
    assert p._show_anim.state().name == "Stopped"


class _Worker:
    def sync_now(self):
        pass

    def stop(self):
        pass


@pytest.mark.parametrize("path", ["tray click", "second launch"])
def test_first_open_slides_through_every_path(qtbot, tmp_path, animated, path):
    p, cache, settings = _fresh_popup(qtbot, tmp_path)
    tray = TrayController(p, _Worker(), cache, settings)
    tray.showMessage = lambda *a, **k: None
    tray._tray_rect = lambda: BOTTOM_RIGHT
    steps = []
    p._show_slide.valueChanged.connect(lambda v: steps.append(QPoint(v)))
    if path == "tray click":
        tray.toggle_popup()
    else:
        tray.show_popup()                   # SingleInstance.activated: a second launch
    assert p.isVisible() and p.showing_animated() and p.windowOpacity() < 0.05
    qtbot.waitUntil(lambda: not p.showing_animated())
    assert len({q.y() for q in steps}) >= 3
    assert p.pos() == p.docked_geometry().topLeft()


# -- no white frame -------------------------------------------------------------------------
def _whitish(rgb) -> bool:
    c = QColor(rgb)
    return c.red() > 200 and c.green() > 200 and c.blue() > 200


def test_panel_stays_opaque_and_unlayered_through_its_slide(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    opacities, masks = [], []
    real_set = None

    def watch(fly):
        nonlocal real_set
        real_set = fly.setWindowOpacity
        fly.setWindowOpacity = lambda v: (opacities.append(v), real_set(v))
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    watch(fly)
    fly._anim.valueChanged.connect(lambda _v: masks.append(QRect(fly.mask().boundingRect())))
    assert fly.windowOpacity() == 1.0 and fly.mask() == NOTHING_SHOWN
    qtbot.waitUntil(lambda: not fly.sliding())
    click(qtbot, rows(p)[0])                            # and back
    qtbot.waitUntil(lambda: not fly.isVisible())
    click(qtbot, rows(p)[1])                            # a second open
    qtbot.waitUntil(lambda: not fly.sliding())
    assert all(v == 1.0 for v in opacities), opacities  # never transparent (layered)
    assert fly.windowOpacity() == 1.0 and masks


def test_panel_first_frame_has_no_white_pixels(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    first = fly.grab().toImage()                        # the frame it is shown with
    w, h = first.width(), first.height()
    pts = [(x, y) for y in range(0, h, 3) for x in range(0, w, 3)]
    white = sum(_whitish(first.pixel(x, y)) for x, y in pts)
    assert white / len(pts) < 0.05, white              # (only the light text)
    bg = QColor(theme.BG).rgb()
    # (v0.8.6: inside the rounded edge, clear of its corners)
    assert [first.pixel(x, y) for x, y in ((12, h - 4), (w - 13, h - 4), (w // 2, h - 4))] \
        == [bg] * 3
    qtbot.waitUntil(lambda: fly.x() < p.x() - 20)
    mid = fly.grab().toImage()
    assert mid.pixel(12, h - 4) == QColor(theme.BG).rgb()


def test_colorref_is_bgr():
    assert nativebg.colorref("#112233") == 0x332211
    assert nativebg.handle_erase(b"other", 0, theme.BG) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 GDI")
def test_erase_background_message_fills_dark(qtbot, tmp_path):
    from ctypes import wintypes
    gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.GetPixel.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.GetPixel.restype = wintypes.DWORD
    gdi32.SetPixel.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, 8, 8)
    user32.ReleaseDC(None, screen)
    gdi32.SelectObject(dc, bmp)
    try:
        gdi32.SetPixel(dc, 3, 3, 0xFFFFFF)              # white, as a fresh surface may be
        msg = wintypes.MSG()
        msg.message = nativebg.WM_ERASEBKGND
        msg.wParam = dc
        msg.hWnd = None
        p = settled_popup(qtbot, tmp_path, MANY)
        ptr = sip.voidptr(ctypes.addressof(msg))
        assert p.nativeEvent(b"windows_generic_MSG", ptr) == (True, 1)
        assert gdi32.GetPixel(dc, 3, 3) == nativebg.colorref(theme.BG)
        click(qtbot, rows(p)[0])
        gdi32.SetPixel(dc, 3, 3, 0xFFFFFF)
        assert p.flyout().nativeEvent(b"windows_generic_MSG", ptr) == (True, 1)
        assert gdi32.GetPixel(dc, 3, 3) == nativebg.colorref(theme.BG)
        msg.message = 0x0200                            # WM_MOUSEMOVE: Qt handles it
        handled, _ = p.nativeEvent(b"windows_generic_MSG", ptr)
        assert handled is False
    finally:
        gdi32.DeleteDC(dc)
        gdi32.DeleteObject(bmp)
