"""v0.9.0 "card build": the popup's rows build into place on opening (staggered, from
lower, smaller and transparent, springing up), the details panel's lines build in when
it opens or switches meeting, and rows / Join buttons shrink a little while pressed.
Offscreen Qt: the schedule, the gating by the animation setting, no build on a refresh,
cleanup on hide/close/collapse, and that everything lands exactly in its final state.
(The real-display run, screen grabs included, is test_real_display_card_build.)"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from smartcal.ui import cards, motion
from smartcal.ui.cards import CardBuild, CardEffect
from test_meeting_actions_ui import click, ev, rows, settle_qt
from test_popup_animations import MANY, settled_popup
from test_popup_flyout import popup_on

LINK = "https://zoom.us/j/1"


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    monkeypatch.delenv(motion.ENV_LEVEL, raising=False)


def card(w):
    fx = w.graphicsEffect()
    return fx if isinstance(fx, CardEffect) else None


def wait_built(qtbot, p, timeout=3000):
    qtbot.waitUntil(lambda: not p.building() and not p.showing_animated(), timeout=timeout)


# -- the schedule (pure) -----------------------------------------------------------------
def test_stagger_schedule_and_total():
    assert motion.build_delays(4) == [50, 95, 140, 185]
    assert motion.build_total_ms(4) == 185 + motion.BUILD_ROW_MS
    assert motion.build_total_ms(0) == 0 and motion.build_delays(0) == []
    d = motion.build_delays(12)
    cap = motion.BUILD_MAX_STAGGER
    assert d[cap - 1] == d[-1] == 50 + (cap - 1) * 45       # the rest start together
    assert motion.build_delays(5, 140, 50, 8) == [140, 190, 240, 290, 340]


def test_card_state_starts_out_of_place_and_lands_exactly():
    o, dy, s, done = motion.card_state(0, 50, 420, 26, 0.94)
    assert (o, dy, s, done) == (0.0, 26.0, 0.94, False)
    assert motion.card_state(470, 50, 420, 26, 0.94) == (1.0, 0.0, 1.0, True)
    assert motion.card_state(10_000, 50, 420, 26, 0.94) == (1.0, 0.0, 1.0, True)
    seen = [motion.card_state(t, 50, 420, 26, 0.94) for t in range(0, 480, 2)]
    assert all(0.0 <= o <= 1.0 for o, *_ in seen)              # opacity never past 1
    assert min(dy for _o, dy, _s, _d in seen) < 0               # springs past its place
    assert max(s for _o, _dy, s, _d in seen) > 1.0
    assert min(dy for _o, dy, _s, _d in seen) > -0.15 * 26      # ...only a little


def test_spring_curve_overshoots_and_lands():
    assert motion.ease(motion.SPRING, 1.0) == 1.0 and motion.ease(motion.SPRING, 0.0) == 0.0
    peak = max(motion.ease(motion.SPRING, i / 200) for i in range(201))
    assert 1.02 < peak < 1.15
    assert motion.curve(motion.SPRING).type().name == "BezierSpline"


def test_constants_are_the_chosen_option_b():
    assert motion.POPUP_SLIDE_PX == 18
    assert (motion.BUILD_OFFSET_PX, motion.BUILD_SCALE_START) == (26, 0.94)
    assert (motion.BUILD_START_MS, motion.BUILD_STAGGER_MS, motion.BUILD_ROW_MS) == (50, 45, 420)
    assert (motion.PANEL_BUILD_START_MS, motion.PANEL_BUILD_STAGGER_MS,
            motion.PANEL_BUILD_MS, motion.PANEL_BUILD_OFFSET_PX) == (140, 50, 380, 14)
    assert motion.PRESS_SCALE == 0.97


# -- the popup's rows --------------------------------------------------------------------
def test_rows_start_out_of_place_before_first_paint_then_land(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:5])
    rs = rows(p)
    build = p.rows_build()
    assert build is not None and build.widgets() == rs
    assert build.delays() == motion.build_delays(5)
    # Set up in show_near_tray (before the first frame): all out of place.
    if not build.started():
        assert all(card(r) is not None and card(r).state() == (0.0, 26.0, 0.94) for r in rs)
    assert card(p._header) is not None
    wait_built(qtbot, p)
    assert all(r.graphicsEffect() is None for r in rs)        # effects removed
    assert p._header.graphicsEffect() is None
    assert p.rows_build() is None or not p.rows_build().running()


def test_build_is_staggered_top_to_bottom(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:4])
    rs = rows(p)
    build = p.rows_build()
    qtbot.waitUntil(build.started, timeout=1000)
    build._frame(120.0)                                       # 120 ms in
    states = [card(r).state() for r in rs]
    opacities = [o for o, _dy, _s in states]
    assert opacities[0] > opacities[1] > opacities[2] == opacities[3] == 0.0
    build._frame(50 + 420 + 1.0)                              # the first one is done
    assert rs[0].graphicsEffect() is None and card(rs[1]) is not None
    wait_built(qtbot, p)


def test_final_state_is_exactly_the_unanimated_one(qtbot, tmp_path, animated, monkeypatch):
    p = popup_on(qtbot, tmp_path, MANY[:5])
    wait_built(qtbot, p)
    settle_qt()
    built = [r.geometry() for r in rows(p)], p.geometry()
    img = p.grab().toImage()
    p.hide()
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    p.refresh()
    p.show_near_tray(p._tray_geom)
    settle_qt()
    assert ([r.geometry() for r in rows(p)], p.geometry()) == built
    assert p.grab().toImage() == img


def test_no_build_on_refresh_while_open(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:5])
    wait_built(qtbot, p)
    p.refresh()                                               # a sync / the 30 s tick
    assert not p.building() and all(r.graphicsEffect() is None for r in rows(p))
    p.refresh(fade_in_new=True)                               # nothing new: no fade either
    assert all(r.graphicsEffect() is None for r in rows(p))


def test_refresh_mid_build_carries_on_and_adds_nothing(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:5])
    build = p.rows_build()
    qtbot.waitUntil(build.started, timeout=1000)
    build._frame(100.0)
    old = rows(p)
    p.refresh()
    new = rows(p)
    assert new[0] is not old[0]
    assert build.widgets() == new                             # rebound to the new rows
    assert all(card(r) is not None for r in new[1:])          # still on their way in
    assert build.elapsed() == 100.0
    wait_built(qtbot, p)
    assert all(r.graphicsEffect() is None for r in rows(p))


@pytest.mark.parametrize("mode", ["off", "basic"])
def test_gated_by_animation_setting(qtbot, tmp_path, monkeypatch, mode):
    if mode == "basic":
        monkeypatch.setattr(motion, "animations_enabled", lambda: True)
        monkeypatch.setenv(motion.ENV_LEVEL, "basic")
    p = popup_on(qtbot, tmp_path, MANY[:4] + [ev("j", "J", 18, meeting_url=LINK)])
    assert p.rows_build() is None and p.header_build() is None
    assert all(r.graphicsEffect() is None for r in rows(p))
    r = rows(p)[0]
    qtbot.mousePress(r, Qt.MouseButton.LeftButton)
    assert r.graphicsEffect() is None                         # no press scale
    qtbot.mouseRelease(r, Qt.MouseButton.LeftButton)
    click(qtbot, rows(p)[-1])
    fly = p.flyout()
    assert fly.content_build() is None
    assert all(w.graphicsEffect() is None for w in fly.build_lines())


def test_hide_mid_build_ends_everything(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:6])
    build = p.rows_build()
    qtbot.waitUntil(build.started, timeout=1000)
    build._frame(80.0)
    p.hide()
    assert p.rows_build() is None and p.header_build() is None
    assert all(r.graphicsEffect() is None for r in rows(p))
    assert p._header.graphicsEffect() is None
    assert not motion.alive(build._anim)


def test_rapid_toggling_restarts_cleanly(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:6])
    tray = p._tray_geom
    for i in range(8):
        p.hide()
        assert all(r.graphicsEffect() is None for r in rows(p))
        p.refresh()
        p.show_near_tray(tray)
        build = p.rows_build()
        assert build is not None and build.widgets() == rows(p)
        if i % 2:
            qtbot.wait(30)
    wait_built(qtbot, p)
    assert all(r.graphicsEffect() is None for r in rows(p))
    builds = p.findChildren(CardBuild)
    settle_qt()
    assert all(sip.isdeleted(b) or not b.running() for b in builds)


def test_pressed_row_lands_now_others_keep_building(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:5])
    build = p.rows_build()
    qtbot.waitUntil(build.started, timeout=1000)
    build._frame(60.0)
    rs = rows(p)
    qtbot.mousePress(rs[2], Qt.MouseButton.LeftButton)
    assert not build.building(rs[2]) and build.building(rs[3])
    assert rs[2].press_feedback.active()                      # its press scale instead
    qtbot.mouseRelease(rs[2], Qt.MouseButton.LeftButton)
    assert p.flyout_open()                                    # the click still selects
    wait_built(qtbot, p)
    qtbot.waitUntil(lambda: not rs[2].press_feedback.active(), timeout=1000)
    assert all(r.graphicsEffect() is None for r in rows(p))


def test_hide_meeting_mid_build(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:5])
    build = p.rows_build()
    qtbot.waitUntil(build.started, timeout=1000)
    build._frame(90.0)
    p.hide_meeting(rows(p)[1].event, "event")
    assert p.rows_build() is None                             # everything in place first
    qtbot.waitUntil(lambda: not p.collapsing(), timeout=2000)
    assert len(rows(p)) == 4
    assert all(r.graphicsEffect() is None for r in rows(p))


def test_opening_slide_never_passes_the_dock(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY[:3])
    start, end = p._show_slide.startValue(), p._show_slide.endValue()
    seen = []
    p._show_slide.valueChanged.connect(lambda _v: seen.append(p.pos()))
    qtbot.waitUntil(lambda: not p.showing_animated(), timeout=1000)
    assert p.pos() == end
    assert all(min(start.y(), end.y()) <= q.y() <= max(start.y(), end.y()) for q in seen)


# -- press feedback ----------------------------------------------------------------------
def test_row_and_join_press_scale_and_spring_back(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, [ev("1", "A", 10, meeting_url=LINK), ev("2", "B", 11)])
    wait_built(qtbot, p)
    r = rows(p)[1]
    qtbot.mousePress(r, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: r.press_feedback.scale() == pytest.approx(motion.PRESS_SCALE),
                    timeout=1000)
    assert card(r).state()[2] == pytest.approx(motion.PRESS_SCALE)
    qtbot.mouseRelease(r, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: r.graphicsEffect() is None, timeout=1000)
    assert r.press_feedback.scale() == 1.0
    p.close_flyout(restore_focus=False)
    join = rows(p)[0].join_button
    assert join.press_feedback is not None
    join.press_feedback.press()
    assert card(join) is not None
    join.press_feedback.release()
    qtbot.waitUntil(lambda: join.graphicsEffect() is None, timeout=1000)


# -- the details panel -------------------------------------------------------------------
def test_panel_lines_build_in_on_open_and_switch_not_on_refresh(qtbot, tmp_path, animated):
    evs = [ev("1", "A", 10, description="Notes", meeting_url=LINK), ev("2", "B", 11)]
    p = settled_popup(qtbot, tmp_path, evs)
    wait_built(qtbot, p)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    build = fly.content_build()
    lines = fly.build_lines()
    names = [w.objectName() for w in lines]
    assert names[:2] == ["title", "when"] and "description" in names and "bigjoin" in names
    assert build is not None and build.widgets() == lines
    assert build.delays() == motion.build_delays(len(lines), 140, 50)
    assert all(card(w) is not None for w in lines)
    qtbot.waitUntil(lambda: not fly.building(), timeout=3000)
    assert all(w.graphicsEffect() is None for w in fly.build_lines())
    p.refresh()                                               # nothing changed: no build
    assert not fly.building()
    click(qtbot, rows(p)[1])                                  # another meeting
    assert fly.content_build() is not None and fly.content_build().started()
    assert fly.content_build().delays()[0] == motion.PANEL_SWITCH_START_MS
    qtbot.waitUntil(lambda: not fly.building() and not fly.crossfading(), timeout=3000)
    assert all(w.graphicsEffect() is None for w in fly.build_lines())
    assert fly.body().graphicsEffect() is None


def test_panel_close_and_popup_hide_mid_build(qtbot, tmp_path, animated):
    evs = [ev("1", "A", 10, description="Notes", meeting_url=LINK), ev("2", "B", 11)]
    p = settled_popup(qtbot, tmp_path, evs)
    wait_built(qtbot, p)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    p.close_flyout(restore_focus=False, animate=False)
    assert fly.content_build() is None
    assert all(w.graphicsEffect() is None for w in fly.build_lines())
    click(qtbot, rows(p)[1])
    assert fly.content_build() is not None
    p.hide()
    assert fly.content_build() is None and not fly.isVisible()
    assert all(w.graphicsEffect() is None for w in fly.build_lines())


def test_big_join_press_lands_the_build(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, [ev("1", "A", 10, meeting_url=LINK)])
    wait_built(qtbot, p)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    join = next(w for w in fly.build_lines() if w.objectName() == "bigjoin")
    assert fly.building()
    join.pressed.emit()
    assert not fly.building()


# -- CardBuild on its own ----------------------------------------------------------------
def test_cardbuild_skips_deleted_and_foreign_widgets(qtbot):
    from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget
    host = QWidget()
    qtbot.addWidget(host)
    a, b, c = QLabel("a", host), QLabel("b", host), QLabel("c", host)
    b.setGraphicsEffect(QGraphicsOpacityEffect(b))           # someone else's effect
    build = CardBuild(host, 10, 0.9, 0, 10, 100)
    build.prepare([a, b, c])
    assert card(a) is not None and card(b) is None and card(c) is not None
    c.setGraphicsEffect(None)                                 # removed behind its back
    sip.delete(a)                                             # deleted mid-build
    build.start()
    build._frame(50.0)
    build.finish()
    assert isinstance(b.graphicsEffect(), QGraphicsOpacityEffect)
    assert c.graphicsEffect() is None


# -- the real Windows display (opt in: it opens real windows) ---------------------------
RUN = textwrap.dedent(r"""
    import faulthandler, json, subprocess, sys
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QRect, Qt, qInstallMessageHandler
    from PyQt6.QtWidgets import QApplication, QWidget
    msgs = []
    qInstallMessageHandler(lambda t, c, m: msgs.append(m))
    app = QApplication([])
    from smartcal.ui import motion
    motion.animations_enabled = lambda: True
    from smartcal.ui.cards import CardEffect
    from smartcal.ui.popup import AgendaPopup, AgendaRow
    from test_meeting_actions_ui import NOW, ev, store
    from test_popup_animations import MANY
    evs = MANY[:5] + [ev("d", "Design review", 18, description="Walk through it.\nAgree.",
                         meeting_url="https://zoom.us/j/1")]
    cache, settings = store(tmp, evs)
    p = AgendaPopup(cache, settings); p.now = lambda: NOW; p.refresh()
    p.attach_chat(lambda: None)
    scr = app.primaryScreen().availableGeometry()
    tray = QRect(scr.right() - 40, scr.bottom() + 5, 20, 20)
    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents()
    def rows():
        return [r for r in p.row_widgets() if isinstance(r, AgendaRow)]
    p.show_near_tray(tray); pump(900); p.select_row(rows()[0]); pump(700)
    fly = p.flyout()
    r = fly.geometry().united(p.geometry())
    p.hide(); pump(200)
    back = QWidget()                    # a black backdrop: any white is ours
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(r.adjusted(-20, -40, 20, 40))
    back.show(); pump(300)
    cap = subprocess.Popen([sys.executable, str(tmp / "capture.py"), str(r.x()), str(r.y()),
                            str(r.width()), str(r.height()), "9", str(tmp / "cap.npy")])
    pump(400)
    t = QElapsedTimer(); t.start(); k = 0; checks = []
    while t.elapsed() < 7500:
        p.refresh(); p.show_near_tray(tray); pump([30, 120, 250, 600][k % 4])
        rs = rows()
        if k % 5 == 1:
            p.refresh()                                     # a sync mid-build
        p.toggle_row(rs[k % 4]); pump(60 if k % 3 == 0 else 200)   # open mid-build
        p.toggle_row(rs[(k + 1) % 4]); pump(90)              # switch mid-build
        if k % 4 == 2:
            p.toggle_chat(); pump(80); p.toggle_chat(); pump(80)
        if k % 3 == 1:
            gone = rows()[-2].event
            p.hide_meeting(gone, "event"); pump(60)              # hide mid-collapse...
            settings.unhide_all()                                # ...and undo it
            p.refresh(fade_in_new=True); pump(60)
        p.toggle_row(rows()[0]); pump(40)                   # close mid-build
        p.hide(); pump(15 if k % 2 else 120)                # rapid toggles
        checks.append(all(not isinstance(w.graphicsEffect(), CardEffect)
                          for w in rows() + [p._header]))
        k += 1
    cap.wait()
    info = {"clean": all(checks), "loops": k,
            "geometry_warnings": [m for m in msgs if "geometry" in m.lower()][:5]}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_card_build(tmp_path):
    import json
    import numpy as np
    from test_rounded_stack import CAPTURE
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(motion.ENV_LEVEL, None)
    (tmp_path / "capture.py").write_text(CAPTURE, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=180)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    info = json.loads((tmp_path / "info.json").read_text())
    assert info["clean"] and info["loops"] > 5, info
    assert info["geometry_warnings"] == [], info
    frames = np.load(tmp_path / "cap.npy")
    assert len(frames) > 200
    white = frames.min(axis=3) > 200
    # a white block: a column more than 30% white (text never fills that much)
    bad = np.nonzero((white.mean(axis=1) > 0.3).sum(axis=1) > 2)[0]
    assert len(bad) == 0, f"{len(bad)} frames with white, first at {bad[:5]}"
