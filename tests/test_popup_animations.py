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
from smartcal.ui.flyout import HEADER_TEXT, DetailsFlyout
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
def test_flyout_same_size_as_popup_and_top_aligned(qtbot, tmp_path):
    for i, tray in enumerate((TOP_TRAY, BOTTOM_RIGHT)):
        sub = tmp_path / str(i)
        sub.mkdir()
        p = popup_on(qtbot, sub, MANY + [ev("x", "Short", 18)], tray=tray)
        click(qtbot, rows(p)[-1])                     # short content, tall popup
        fly = p.flyout()
        assert fly.size() == p.size() and fly.width() == WIDTH
        assert fly.y() == p.y() and fly.geometry().bottom() == p.geometry().bottom()
        assert fly.pos() == docked_pos(p) and p.flyout_side == "left"
        assert AREA.contains(fly.geometry())


def test_flyout_resizes_with_popup_when_chat_expands(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10), ev("2", "B", 11)], tray=BOTTOM_RIGHT)
    p.attach_chat(lambda: None)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    h0 = p.height()
    assert fly.size() == p.size()
    p.toggle_chat()                                   # the popup grows (and moves up)
    assert p.height() > h0 and fly.isVisible()
    assert fly.size() == p.size() and fly.pos() == docked_pos(p)
    p.toggle_chat()                                   # and shrinks back
    assert p.height() == h0
    assert fly.size() == p.size() and fly.pos() == docked_pos(p)
    p.show_notice("A notice that makes the popup taller than before")
    assert fly.size() == p.size() and fly.y() == p.y()


def test_flyout_background_fully_painted_no_black_gap(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, MANY + [ev("x", "Short", 18)])
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    assert not fly.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert fly.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert fly.height() > 300                          # much taller than its content
    img = fly.grab().toImage()
    w, h = img.width(), img.height()
    bg, border = QColor(theme.BG).rgb(), QColor(theme.BORDER).rgb()
    assert img.pixel(0, 0) == border and img.pixel(w - 1, h - 1) == border
    empty = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3), (w // 2, h - 3),
             (w // 2, h - 40), (w // 2, h // 2 + 60), (w - 15, h // 2)]
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
    assert [w.objectName() for w in fly.tab_chain()] == ["bigjoin", "hide", "color"]


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
    for name in ("hide", "color"):
        b = body.findChild(QPushButton, name)
        assert b.width() >= b.minimumSizeHint().width()  # nothing squeezed
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
    assert fly._anim.easingCurve().type().name == "OutCubic"
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
    assert fly._anim.easingCurve().type().name == "InCubic"
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
            qtbot.waitUntil(lambda: not p.isVisible())
        assert not p.isVisible()
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
    qtbot.waitUntil(lambda: QApplication.focusWidget() is details(p).findChild(QPushButton,
                                                                               "hide"))
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert fly.closing() and fly.sliding() and fly.isVisible() and p.isVisible()
    qtbot.waitUntil(lambda: not fly.isVisible())
    assert p.isVisible()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is row)
    qtbot.keyClick(row, Qt.Key.Key_Escape)               # then Esc closes the popup
    assert not p.isVisible()


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
        assert p.pos() == docked.topLeft() + offset, (tray, p.pos(), docked)
        assert p.windowOpacity() < 0.05
        assert p._show_slide.duration() == motion.POPUP_MS
        assert p._show_slide.easingCurve().type().name == "OutCubic"
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
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.pos() == docked_pos(p) and fly.size() == p.size()


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
    assert not motion.animations_enabled()
    # Off (here: the system setting): everything is instant, same final state.
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
