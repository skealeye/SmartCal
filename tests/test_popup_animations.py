"""v0.6.0: the tray popup slides in from the tray side; the details panel has the popup's
size, slides out from behind its edge and back, and has no ✕. Offscreen Qt, real widgets
and geometry. The suite runs with animations off (conftest); these tests turn them on
through motion.animations_enabled and wait with qtbot.waitUntil."""
import ctypes
import sys
import time

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QElapsedTimer, QEvent, QObject, QPoint, QRect, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from smartcal.ui import motion, placement, theme
from smartcal.ui.flyout import HEADER_TEXT, NOTHING_SHOWN, DetailsFlyout
from smartcal.ui.popup import WIDTH, AgendaPopup
from test_meeting_actions_ui import (DESC, NOW, click, details, ev, rows, settle_qt, store,
                                     wait_active)
from test_popup_flyout import AREA, BOTTOM_RIGHT, TOP_TRAY, popup_on

MANY = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20) for i in range(12)]


@pytest.fixture
def animated(monkeypatch):
    """Animations on (the suite default is off)."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


def deactivate(w):
    QApplication.sendEvent(w, QEvent(QEvent.Type.WindowDeactivate))


def move_focus_away(qtbot) -> None:
    """Offscreen Qt keeps a hidden window as its focus window (real Windows activates
    another one): showing that window again then never reports it active. v0.9.1: the
    outro hides the popup after it took focus back, so tests that reopen it hand focus
    to a scratch window first."""
    scratch = QWidget()
    qtbot.addWidget(scratch)
    scratch.show()
    scratch.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is scratch)
    scratch.hide()
    settle_qt()


def docked_pos(p) -> QPoint:
    g = p.docked_geometry()
    return QPoint(g.left() - placement.GAP - WIDTH, g.top())


def record_slide(fly) -> list[QPoint]:
    """Where the panel really is at each step of its slide (positions on screen)."""
    seen = []

    def step(_v):
        if fly.sliding():
            seen.append(fly.pos())
    fly._anim.valueChanged.connect(step)
    fly._anim.finished.connect(lambda: seen.append(fly.pos()))
    return seen


def settled_popup(qtbot, tmp_path, events, **kw):
    """A shown popup whose own opening animation (if any) has finished."""
    p = popup_on(qtbot, tmp_path, events, **kw)
    qtbot.waitUntil(lambda: not p.showing_animated())
    return p


# -- size and paint ----------------------------------------------------------------------
def test_flyout_popup_width_top_aligned_and_fits_content(qtbot, tmp_path):
    # v0.8.0: as wide as the popup and top aligned, but only as tall as its content.
    for i, tray in enumerate((TOP_TRAY, BOTTOM_RIGHT)):
        sub = tmp_path / str(i)
        sub.mkdir()
        p = popup_on(qtbot, sub, MANY + [ev("x", "Short", 18)], tray=tray)
        click(qtbot, rows(p)[-1])                     # short content, tall popup
        fly = p.flyout()
        assert fly.width() == WIDTH and fly.height() == fly.content_height() < p.height()
        assert fly.y() == p.y() and fly.geometry().bottom() < p.geometry().bottom()
        assert fly.pos() == docked_pos(p) and p.flyout_side == "left"
        assert AREA.contains(fly.geometry())


def test_flyout_resizes_with_popup_when_chat_expands(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)], tray=BOTTOM_RIGHT)
    p.attach_chat(lambda: None)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    h0 = p.height()

    def fitted():
        return fly.width() == WIDTH and fly.height() == min(p.height(), fly.content_height())
    assert fitted()
    p.toggle_chat()                                   # the popup grows (and moves up)
    assert p.height() > h0 and fly.isVisible()
    assert fitted() and fly.pos() == docked_pos(p)    # follows the popup's top
    p.toggle_chat()                                   # and shrinks back
    assert p.height() == h0
    assert fitted() and fly.pos() == docked_pos(p)
    p.show_notice("A notice that makes the popup taller than before")
    assert fitted() and fly.y() == p.y()


def test_flyout_background_fully_painted_no_black_gap(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, MANY + [ev("x", "Short", 18)])
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    assert not fly.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert fly.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert fly.height() < p.height()                   # v0.8.0: fits its content
    img = fly.grab().toImage()
    w, h = img.width(), img.height()
    bg, edge = QColor(theme.BG).rgb(), QColor(theme.ACCENT).rgb()
    # v0.8.6: a rounded ACCENT edge (painted here: no DWM offscreen)
    assert img.pixel(w // 2, 0) == edge and img.pixel(w - 1, h // 2) == edge
    empty = [(12, 3), (w - 13, 3), (12, h - 4), (w - 13, h - 4), (w // 2, h - 4),
             (w - 15, h // 2)]
    assert [img.pixel(x, y) for x, y in empty] == [bg] * len(empty)
    black = QColor("#000000").rgb()
    assert not any(img.pixel(x, y) == black for y in range(0, h, 2) for x in range(0, w, 2))


def test_flyout_has_no_close_button(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, meeting_url="https://zoom.us/j/1")])
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert not hasattr(fly, "close_button")
    assert fly.findChild(QPushButton, "close") is None
    assert [b.text() for b in fly.findChildren(QPushButton)
            if b.text() in ("✕", "×", "X")] == []
    assert fly.findChild(QLabel, "header").text() == HEADER_TEXT == "Meeting details"
    assert [w.objectName() for w in fly.tab_chain()] == ["bigjoin"]   # v0.8.0


def test_flyout_content_layout_fits_250px_width(qtbot, tmp_path):
    e = ev("long", "A very long meeting title " * 6 + "Supercalifragilisticexpialidocious", 10,
           location="Building 7, Room 4.2 " * 6, description=DESC * 20,
           meeting_url="https://us02web.zoom.us/j/123", rid="s1")
    e.calendar_name = "A calendar with a rather long name indeed"
    p = popup_on(qtbot, tmp_path, MANY + [e])
    click(qtbot, next(r for r in rows(p) if r.event.id == "long"))
    fly = p.flyout()
    QApplication.processEvents()
    body = details(p)
    view = fly._scroll.viewport()
    assert fly.width() == WIDTH == 250
    assert body.width() <= view.width() <= WIDTH
    for w in body.findChildren(QWidget):
        if not w.isVisibleTo(body) or w.parentWidget() is not body:
            continue
        g = QRect(w.mapTo(body, QPoint(0, 0)), w.size())
        assert g.left() >= 0 and g.right() <= body.width() - 1, (w.objectName(), g)
    assert body.findChild(QLabel, "title").wordWrap()
    big = body.findChild(QPushButton, "bigjoin")
    assert big.width() == body.width()                 # full width
    assert body.findChild(QPushButton, "hide") is None  # v0.8.0: right-click only
    desc = fly.description_box()
    assert desc.verticalScrollBar().maximum() > 0      # the rest scrolls


# -- the panel slides --------------------------------------------------------------------
def test_flyout_slides_out_from_popup_edge(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[1])
    fly = p.flyout()
    seen = record_slide(fly)
    end = docked_pos(p)
    tucked = QPoint(p.x(), p.y())
    assert fly.isVisible() and fly.sliding() and p.flyout_open()
    assert fly.pos() == tucked                          # start: right behind the popup
    assert fly._anim.duration() == motion.FLYOUT_OPEN_MS
    assert fly._anim.easingCurve() == motion.curve(motion.GLIDE)   # v0.8.4: never past
    assert fly.mask().boundingRect().width() <= 1       # nothing shows over the popup yet
    qtbot.waitUntil(lambda: end.x() < fly.x() < tucked.x())
    mid = fly.pos()                                      # mid: out on the left side only
    vis = fly.mask().boundingRect().translated(mid)
    assert vis.right() < p.x() and vis.left() == mid.x() and mid.y() == p.y()
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.pos() == end and fly.mask().isEmpty()    # end: docked, unmasked
    xs = [q.x() for q in seen]
    assert xs == sorted(xs, reverse=True) and len(set(xs)) > 2
    assert all(q.y() == p.y() for q in seen)


def test_flyout_slides_back_then_hides(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    row = rows(p)[2]
    click(qtbot, row)
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding())
    seen = record_slide(fly)
    click(qtbot, row)                                    # the selected meeting again
    assert not p.flyout_open() and not row.is_selected() and p.selected_row() is None
    assert fly.isVisible() and fly.closing() and fly.sliding()
    assert fly._anim.duration() == motion.FLYOUT_CLOSE_MS
    assert fly._anim.easingCurve() == motion.curve(motion.GLIDE)
    qtbot.waitUntil(lambda: not fly.isVisible())
    assert seen and seen[-1] == QPoint(p.x(), p.y())     # all the way behind the popup
    xs = [q.x() for q in seen]
    assert xs == sorted(xs)
    assert p.isVisible() and not fly.closing() and fly.mask().isEmpty()


def test_click_same_row_during_open_animation_reverses(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    row = rows(p)[0]
    click(qtbot, row)
    fly = p.flyout()
    tucked, end = QPoint(p.x(), p.y()), docked_pos(p)
    qtbot.waitUntil(lambda: fly.x() < tucked.x() - 40)
    at = fly.pos()
    click(qtbot, row)                                    # toggled closed mid-slide
    assert fly.closing() and fly.sliding() and fly.isVisible()
    assert fly._anim.startValue() == at and fly._anim.endValue() == tucked   # no jump
    assert fly._anim.duration() < motion.FLYOUT_CLOSE_MS                     # part of it
    qtbot.waitUntil(lambda: fly.x() > at.x())            # heading back
    click(qtbot, row)                                    # re-opened mid-way back
    assert fly.sliding() and not fly.closing() and p.flyout_open() and row.is_selected()
    back_at = fly._anim.startValue()
    assert at.x() < back_at.x() < tucked.x() and fly._anim.endValue() == end
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.isVisible() and fly.pos() == end


def test_popup_hide_hides_flyout_immediately_even_mid_animation(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    wait_active(qtbot, fly)               # offscreen activates it on show: let that settle
    assert fly.sliding()
    p.hide()                                             # mid slide-out
    assert not fly.isVisible() and not fly.sliding() and not fly.closing()
    for close in ("slide-back", "focus"):
        settle_qt()
        move_focus_away(qtbot)
        p.active_window = QApplication.activeWindow
        p.show_near_tray(TOP_TRAY)
        wait_active(qtbot, p)
        qtbot.waitUntil(lambda: not p.showing_animated())
        click(qtbot, rows(p)[0])
        wait_active(qtbot, fly)
        qtbot.waitUntil(lambda: not fly.sliding())
        if close == "slide-back":
            click(qtbot, rows(p)[0])                     # sliding back...
            assert fly.closing() and fly.sliding()
            p.handle_escape()                            # ...Esc closes the popup now
        else:
            click(qtbot, rows(p)[1])
            p.active_window = lambda: None               # focus left the group
            deactivate(p)
        # v0.9.1: both play the outro (closed for everyone right away; the focus loss
        # after one settle pass), then hide.
        qtbot.waitUntil(lambda: p.dismissing())
        assert not p.is_open()
        qtbot.waitUntil(lambda: not p.isVisible())
        assert not fly.isVisible() and not fly.sliding() and not fly.closing()
    settle_qt()
    assert not fly.isVisible()                           # no orphan comes back


def test_switching_rows_does_not_slide(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    a, b, c = rows(p)[:3]
    click(qtbot, a)
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding())
    seen = record_slide(fly)
    click(qtbot, b)
    assert not fly.sliding() and fly.pos() == docked_pos(p) and seen == []
    assert details(p).findChild(QLabel, "title").text() == "Event 1"
    # While sliding out: the content swaps, the slide just carries on.
    click(qtbot, b)
    qtbot.waitUntil(lambda: not fly.isVisible())
    click(qtbot, a)
    qtbot.waitUntil(lambda: fly.x() < p.x() - 20)
    anim_start = fly._anim.startValue()
    click(qtbot, c)
    assert fly.sliding() and fly._anim.startValue() == anim_start
    assert details(p).findChild(QLabel, "title").text() == "Event 2"
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.pos() == docked_pos(p)


def test_flyout_stays_in_focus_group_during_animation(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.active_window = lambda: p
    p.app_active = lambda: True
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert fly.sliding()
    p.active_window = lambda: fly                        # clicked into it mid-slide
    deactivate(p)
    settle_qt()
    assert p.isVisible() and fly.isVisible()
    p._watchdog_tick()
    settle_qt()
    assert p.isVisible() and fly.isVisible()
    qtbot.waitUntil(lambda: not fly.sliding())
    # Clicked into it while it slides back: still the group; focus returns after.
    p.active_window = lambda: p
    click(qtbot, rows(p)[0])
    assert fly.closing() and fly.sliding()
    p.active_window = lambda: fly if fly.isVisible() else (None if p.reclaim_check.isActive()
                                                          else p)
    deactivate(p)
    settle_qt()
    assert p.isVisible() and fly.isVisible()
    activated = []
    real_activate = p.activateWindow
    p.activateWindow = lambda: activated.append(1) or real_activate()
    qtbot.waitUntil(lambda: not fly.isVisible())
    assert activated and p.reclaim_check.isActive()      # the popup takes focus back
    qtbot.waitUntil(lambda: not p.reclaim_check.isActive())
    settle_qt()
    assert p.isVisible()


def test_escape_closes_flyout_with_slide_back(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    row = rows(p)[1]
    qtbot.keyClick(row, Qt.Key.Key_Return)
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding())
    qtbot.waitUntil(lambda: QApplication.focusWidget() is fly)   # no link: the panel
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert fly.closing() and fly.sliding() and fly.isVisible() and p.isVisible()
    qtbot.waitUntil(lambda: not fly.isVisible())
    assert p.isVisible()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is row)
    qtbot.keyClick(row, Qt.Key.Key_Escape)               # then Esc closes the popup
    assert not p.is_open() and p.dismissing()            # v0.9.1: with the outro
    qtbot.waitUntil(lambda: not p.isVisible())


def test_overlay_on_narrow_screen_appears_without_sliding(qtbot, tmp_path, animated):
    narrow = QRect(0, 0, 560, 800)
    p = settled_popup(qtbot, tmp_path, MANY, tray=QRect(270, 0, 20, 20), area=narrow)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert p.flyout_side == "over" and not fly.sliding() and fly.isVisible()
    click(qtbot, rows(p)[0])
    assert not fly.isVisible()


# -- the popup slides in -------------------------------------------------------------------
EDGES = [  # (tray, work area, expected start offset from the docked position)
    (QRect(980, 820, 20, 20), QRect(0, 0, 1000, 800), QPoint(0, 24)),     # bottom: up
    (QRect(980, 10, 20, 20), QRect(0, 40, 1000, 800), QPoint(0, -24)),    # top: down
    (QRect(5, 700, 20, 20), QRect(40, 0, 960, 800), QPoint(-24, 0)),      # left: right
    (QRect(975, 700, 20, 20), QRect(0, 0, 960, 800), QPoint(24, 0)),      # right: left
    (QRect(), QRect(0, 0, 1000, 800), QPoint(0, 24)),                     # unknown: up
]


def test_popup_slides_in_from_tray_side_for_each_taskbar_edge(qtbot, tmp_path, animated):
    cache, settings = store(tmp_path, MANY)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    p.refresh()
    for tray, area, offset in EDGES:
        settle_qt()
        p._area = lambda point=None, a=area: a
        p.show_near_tray(tray)
        docked = p.docked_geometry()
        assert p.showing_animated()
        # From the tray side, but clamped inside the work area (v0.6.2): never over the
        # taskbar or onto another monitor.
        start = p.pos()
        assert start == motion.clamp_to_area(docked.topLeft() + offset, p.size(), area)
        assert area.contains(QRect(start, p.size())), (tray, start, area)
        moved = start - docked.topLeft()
        assert moved != QPoint(0, 0), (tray, start, docked)
        assert (moved.x() * offset.x() >= 0 and moved.y() * offset.y() >= 0
                and (moved.x() == 0) == (offset.x() == 0)), (tray, moved, offset)
        assert p.windowOpacity() < 0.05
        assert p._show_slide.duration() == motion.POPUP_MS
        assert p._show_slide.easingCurve() == motion.curve(motion.SETTLE)   # v0.8.4
        assert p._show_fade.easingCurve().type().name == "OutCubic"
        qtbot.waitUntil(lambda: not p.showing_animated())
        assert p.pos() == docked.topLeft() and p.windowOpacity() == 1.0
        assert area.contains(p.geometry())
        p.hide()


def test_popup_final_geometry_equals_docked_geometry_after_animation(qtbot, tmp_path,
                                                                     animated):
    cache, settings = store(tmp_path, MANY)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    p._area = lambda point=None: AREA
    qtbot.addWidget(p)
    p.refresh()
    p.show_near_tray(BOTTOM_RIGHT)
    assert p.showing_animated() and p.geometry() != p.docked_geometry()
    assert p.docked_geometry().topLeft() == p.compute_position(BOTTOM_RIGHT, AREA)
    p.show_notice("A notice that makes the popup taller mid-animation")   # re-docks
    docked = p.docked_geometry()
    assert p._show_slide.endValue() == docked.topLeft()
    click(qtbot, rows(p)[0])                             # interactive mid-animation
    fly = p.flyout()
    assert fly.isVisible() and fly.docked_pos() == docked_pos(p)
    qtbot.waitUntil(lambda: not p.showing_animated())
    assert p.geometry() == p.docked_geometry() == docked
    qtbot.waitUntil(lambda: not fly.sliding() and not fly.height_animating())
    assert fly.pos() == docked_pos(p) and fly.width() == p.width()
    assert fly.height() == min(p.height(), fly.content_height())


def test_popup_first_paint_within_budget_with_animation(qtbot, tmp_path, animated):
    events = [ev(str(i), f"Event {i}", 8 + i // 3, (i % 3) * 20, description=DESC)
              for i in range(30)]
    cache, settings = store(tmp_path, events)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    qtbot.addWidget(p)
    painted = []

    class FirstPaint(QObject):
        def eventFilter(self, obj, e):
            if e.type() == QEvent.Type.Paint and not painted:
                painted.append((time.perf_counter(), p.showing_animated()))
            return False
    spy = FirstPaint(p)
    p.installEventFilter(spy)
    t0 = time.perf_counter()
    p.refresh()
    p.show_near_tray(QRect(700, 570, 20, 20))
    qtbot.waitUntil(lambda: bool(painted))
    first, animating = painted[0]
    assert (first - t0) * 1000 < 200                    # first paint within the budget...
    assert animating                                    # ...while the slide still runs
    qtbot.waitUntil(lambda: not p.showing_animated())
    assert p.geometry() == p.docked_geometry() and p.windowOpacity() == 1.0


# -- switching animations off ----------------------------------------------------------------
def test_animations_disabled_by_env_and_system_setting_are_instant(qtbot, tmp_path,
                                                                   monkeypatch):
    monkeypatch.delenv(motion.ENV_OFF, raising=False)
    monkeypatch.setattr(motion, "system_animations_enabled", lambda: True)
    assert motion.animations_enabled() and motion.duration(160) == 160
    monkeypatch.setenv(motion.ENV_OFF, "1")
    assert not motion.animations_enabled() and motion.duration(160) == 0
    monkeypatch.delenv(motion.ENV_OFF)
    monkeypatch.setattr(motion, "system_animations_enabled", lambda: False)
    assert motion.animations_enabled()                  # v0.8.3: "on" overrides Windows
    motion.set_user_preference("auto")                  # "Follow Windows setting"
    assert not motion.animations_enabled()
    # Off (here: the system setting, followed): everything is instant, same final state.
    p = popup_on(qtbot, tmp_path, MANY, tray=BOTTOM_RIGHT)
    assert not p.showing_animated() and p.windowOpacity() == 1.0
    assert p.geometry() == p.docked_geometry()
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert not fly.sliding() and fly.isVisible() and fly.pos() == docked_pos(p)
    assert fly.mask().isEmpty()
    click(qtbot, rows(p)[0])
    assert not fly.isVisible() and not fly.closing()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows setting")
def test_system_animation_setting_read_through_spi(monkeypatch):
    user32 = ctypes.windll.user32

    def answer(value, ok=1):
        def spi(action, _ui, pv, _flags):
            assert action == motion.SPI_GETCLIENTAREAANIMATION
            pv._obj.value = value
            return ok
        return spi
    monkeypatch.setattr(user32, "SystemParametersInfoW", answer(0))
    assert motion.system_animations_enabled() is False
    monkeypatch.setattr(user32, "SystemParametersInfoW", answer(1))
    assert motion.system_animations_enabled() is True
    monkeypatch.setattr(user32, "SystemParametersInfoW", answer(0, ok=0))
    assert motion.system_animations_enabled() is True      # the call failed: on

    def boom(*_a):
        raise OSError("no")
    monkeypatch.setattr(user32, "SystemParametersInfoW", boom)
    assert motion.system_animations_enabled() is True      # any failure: on


# -- lifetime ----------------------------------------------------------------------------------
def test_no_runtimeerror_when_popup_destroyed_mid_animation(qtbot, tmp_path, animated):
    for stage in ("popup-opening", "panel-opening", "panel-closing"):
        settle_qt()
        sub = tmp_path / stage
        sub.mkdir()
        cache, settings = store(sub, MANY)
        p = AgendaPopup(cache, settings)
        p.now = lambda: NOW
        p._area = lambda point=None: AREA
        p.refresh()
        p.show_near_tray(TOP_TRAY)
        fly = None
        if stage != "popup-opening":
            qtbot.waitUntil(lambda: not p.showing_animated())
            click(qtbot, rows(p)[0])
            fly = p.flyout()
            if stage == "panel-closing":
                qtbot.waitUntil(lambda: not fly.sliding())
                click(qtbot, rows(p)[0])
                assert fly.closing()
            assert fly.sliding()
        else:
            assert p.showing_animated()
        gone = []
        p.destroyed.connect(lambda *_: gone.append(1))
        p.deleteLater()
        qtbot.waitUntil(lambda: gone == [1])
        assert sip.isdeleted(p) and (fly is None or sip.isdeleted(fly))
        clock = QElapsedTimer()
        clock.start()                                   # let any leftover timer fire
        qtbot.waitUntil(lambda: clock.elapsed() > motion.FLYOUT_OPEN_MS + 60)
        assert not [w for w in QApplication.topLevelWidgets()
                    if isinstance(w, DetailsFlyout) and not sip.isdeleted(w)
                    and w.isVisible()]


# -- v0.6.2: review fixes ------------------------------------------------------------------
SHORT_NOTES = ("One session only. Pick one and decline the rest.\n"
               "Do not forward this invite or share the Zoom link. Need someone added? "
               "Have their producer request it with L7 approval.")


def _open_with(qtbot, tmp_path, description, **kw):
    e = ev("notes", "SwaggerAttire Read-In Session - DO NOT FWD INVITE", 18,
           description=description, meeting_url="https://zoom.us/j/1", **kw)
    p = popup_on(qtbot, tmp_path, MANY + [e])
    click(qtbot, next(r for r in rows(p) if r.event.id == "notes"))
    QApplication.processEvents()
    return p, p.flyout()


def _gap_above_buttons(fly) -> int:
    body = details_of(fly)
    desc = fly.description_box()
    join = body.findChild(QPushButton, "bigjoin")
    below = join
    return below.mapTo(body, QPoint(0, 0)).y() - (desc.mapTo(body, QPoint(0, 0)).y()
                                                 + desc.height())


def details_of(fly):
    return fly.body()


def test_short_description_fully_visible_no_scrollbar(qtbot, tmp_path):
    p, fly = _open_with(qtbot, tmp_path, SHORT_NOTES)
    desc = fly.description_box()
    assert desc.verticalScrollBar().maximum() == 0          # nothing to scroll...
    assert not desc.verticalScrollBar().isVisible()
    doc_h = desc.document().size().height()
    assert doc_h <= desc.viewport().height()                 # ...every line shows in full
    assert "L7 approval" in desc.toPlainText()
    assert fly._scroll.verticalScrollBar().maximum() == 0    # the panel fits too


def test_long_description_fills_remaining_height_then_scrolls(qtbot, tmp_path):
    long = "\n".join(f"line {i} of the notes" for i in range(120))
    p, fly = _open_with(qtbot, tmp_path, long)
    desc = fly.description_box()
    assert desc.verticalScrollBar().maximum() > 0            # the rest scrolls
    assert fly._scroll.verticalScrollBar().maximum() == 0    # inside the panel, which fits
    assert desc.height() > fly.height() // 3                 # it got the spare height
    assert 0 <= _gap_above_buttons(fly) < 12
    assert fly.height() == p.height()                        # capped at the popup's height
    join = details_of(fly).findChild(QPushButton, "bigjoin")
    bottom = join.mapTo(fly, QPoint(0, join.height())).y()
    assert fly.height() - bottom < 24                        # Join at the bottom


def test_description_fills_space_above_buttons_no_blank_band(qtbot, tmp_path):
    for i, notes in enumerate((SHORT_NOTES, "Short.", DESC)):
        sub = tmp_path / str(i)
        sub.mkdir()
        p, fly = _open_with(qtbot, sub, notes)
        gap = _gap_above_buttons(fly)
        assert 0 <= gap < 12, (i, gap)
        join = details_of(fly).findChild(QPushButton, "bigjoin")
        bottom = join.mapTo(fly, QPoint(0, join.height())).y()
        assert fly.height() - bottom < 24, (i, fly.height(), bottom)
        p.hide()


def test_fade_only_fallback_has_no_mask_and_ends_docked(qtbot, tmp_path, animated,
                                                        monkeypatch):
    from smartcal.ui import flyout as flyout_mod
    for how in ("env", "constant"):
        if how == "env":
            monkeypatch.setenv(flyout_mod.SLIDE_MODE_ENV, "fade")
        else:
            monkeypatch.delenv(flyout_mod.SLIDE_MODE_ENV)
            monkeypatch.setattr(flyout_mod, "SLIDE_USES_MASK", False)
        sub = tmp_path / how
        sub.mkdir()
        p = settled_popup(qtbot, sub, MANY)
        click(qtbot, rows(p)[0])
        fly = p.flyout()
        assert fly.mode() == "fade"
        assert fly.isVisible() and fly.sliding()
        assert fly.pos() == docked_pos(p) and fly.mask().isEmpty()   # docked from the start
        assert fly.windowOpacity() < 0.05
        assert fly._fade.duration() == motion.FLYOUT_OPEN_MS
        qtbot.waitUntil(lambda: not fly.sliding())
        assert fly.windowOpacity() == 1.0 and fly.pos() == docked_pos(p)
        assert fly.mask().isEmpty()
        click(qtbot, rows(p)[0])                             # fades out, then hides
        assert fly.closing() and fly.isVisible() and fly.pos() == docked_pos(p)
        assert fly._fade.duration() == motion.FLYOUT_CLOSE_MS
        qtbot.waitUntil(lambda: not fly.isVisible())
        assert fly.windowOpacity() == 1.0 and fly.mask().isEmpty()
        assert p.isVisible()
        p.hide()
    monkeypatch.setattr(flyout_mod, "SLIDE_USES_MASK", True)
    assert flyout_mod.slide_mode() == "slide"
    monkeypatch.setenv(flyout_mod.SLIDE_MODE_ENV, "fade")
    assert flyout_mod.slide_mode() == "fade"


def test_slid_back_panel_with_focus_hands_it_to_popup_even_during_reclaim(qtbot, tmp_path,
                                                                         animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    row = rows(p)[0]
    qtbot.keyClick(row, Qt.Key.Key_Return)
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding())
    settle_qt()
    activated = []
    p.activateWindow = lambda: activated.append(1)          # Windows ignores the request...
    p.active_window = lambda: fly if fly.isVisible() else None   # ...the panel keeps focus
    p.app_active = lambda: True
    qtbot.keyClick(fly, Qt.Key.Key_Escape)                  # Esc: slide back + reclaim
    assert fly.closing() and p.reclaim_check.isActive() and activated == [1]
    qtbot.waitUntil(lambda: not fly.isVisible())            # 140 ms < RECLAIM_CHECK_MS
    assert activated == [1, 1]                               # asked again at the end...
    assert p.reclaim_check.isActive()
    assert p.reclaim_check.remainingTime() > 150             # ...and the check restarted
    p.active_window = lambda: p
    qtbot.waitUntil(lambda: not p.reclaim_check.isActive())
    settle_qt()
    assert p.isVisible()


def test_popup_and_panel_share_one_border_and_background(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert p.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert p.styleSheet() == theme.window_style("popup")
    assert fly.styleSheet() == theme.window_style("flyout")
    a, b = p.grab().toImage(), fly.grab().toImage()
    edge, soft = QColor(theme.ACCENT).rgb(), QColor(theme.ACCENT_SOFT).rgb()
    bg = QColor(theme.BG).rgb()
    for img in (a, b):
        w, h = img.width(), img.height()
        # v0.8.6: the rounded ACCENT edge (painted offscreen; DWM draws it on Windows
        # 11) with the soft inner line just inside it
        assert [img.pixel(w // 2, 0), img.pixel(w - 1, h // 2), img.pixel(w // 2, h - 1),
                img.pixel(0, h // 2)] == [edge] * 4
        assert [img.pixel(w // 2, 1), img.pixel(1, h // 2)] == [soft] * 2
        assert img.pixel(0, 0) == bg              # outside the rounded corner
        assert img.pixel(12, h - 4) == bg


def test_fully_tucked_panel_shows_zero_pixels(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert fly.pos() == QPoint(p.x(), p.y()) and fly.sliding()
    # v0.8.4: fully behind, a mask outside the window shows 0 px; the window stays
    # opaque (never a layered window turning opaque on screen: a white frame).
    assert fly.mask() == NOTHING_SHOWN and not fly.mask().intersects(fly.rect())
    assert fly.windowOpacity() == 1.0
    qtbot.waitUntil(lambda: fly.x() < p.x() - 10)
    assert fly.windowOpacity() == 1.0 and fly.mask().intersects(fly.rect())
    qtbot.waitUntil(lambda: not fly.sliding())
    click(qtbot, rows(p)[0])
    qtbot.waitUntil(lambda: not fly.isVisible())
    assert fly.windowOpacity() == 1.0 and fly.mask().isEmpty()


def test_mask_edges_never_cross_the_popup_edge_at_fractional_scaling():
    from smartcal.ui.flyout import px_before_edge, px_until_edge
    for dpr in (1.0, 1.25, 1.5, 1.75, 2.0):
        for edge in range(700, 760, 7):
            for x in range(edge - 260, edge + 1, 3):
                shown = px_before_edge(x, edge, dpr)
                assert round(x * dpr) + round(shown * dpr) <= round(edge * dpr)
                assert shown >= edge - x - 1                 # at most 1 px short
                hidden = px_until_edge(x, edge, dpr)
                assert round(x * dpr) + round(hidden * dpr) >= round(edge * dpr)
                assert hidden <= edge - x + 1


def test_hidden_panel_place_sets_no_mask(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding())
    click(qtbot, rows(p)[0])
    qtbot.waitUntil(lambda: not fly.isVisible())
    calls = []
    fly.setMask = lambda *a: calls.append(a)
    fly.place(fly.size(), QPoint(p.x() - 20, p.y()), QPoint(p.x(), p.y()), p.geometry())
    p.show_notice("taller")
    assert calls == [] and fly.mask().isEmpty()


def test_popup_hidden_during_show_is_not_animated(qtbot, tmp_path, animated):
    cache, settings = store(tmp_path, MANY)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    p._area = lambda point=None: AREA
    qtbot.addWidget(p)
    p.refresh()
    p.activateWindow = lambda: p.hide()                     # e.g. a focus loss right away
    p.show_near_tray(BOTTOM_RIGHT)
    assert not p.isVisible() and not p.showing_animated()
    assert p.windowOpacity() == 1.0
