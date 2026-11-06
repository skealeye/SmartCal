"""v0.9.1: the outro. Dismissing the tray popup (focus lost, the tray icon, Esc, Join...)
plays the card build in reverse (rows, then the next line and the header, fade out and
drop; the window fades and sinks toward the tray edge), then really hides. Offscreen Qt,
real widgets; animations are turned on per test (the suite default is off)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QRect
from PyQt6.QtWidgets import QApplication

from smartcal.ui import cards, motion
from smartcal.ui.popup import AgendaRow
from smartcal.ui.tray import TrayController
from test_meeting_actions_ui import click, ev, rows, settle_qt, wait_active
from test_popup_animations import MANY, move_focus_away, settled_popup
from test_popup_flyout import BOTTOM_RIGHT, TOP_TRAY, popup_on
from test_tray import FakeWorker


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


def deactivate(w):
    QApplication.sendEvent(w, QEvent(QEvent.Type.WindowDeactivate))


def activate(w):
    QApplication.sendEvent(w, QEvent(QEvent.Type.WindowActivate))


def effects_left(p) -> list:
    return [w for w in [p._header, p._next] + p.row_widgets()
            if w.graphicsEffect() is not None]


def assert_clean(p) -> None:
    """Hidden, opaque for the next opening, nothing left running."""
    assert not p.isVisible() and not p.dismissing() and not p.is_open()
    assert p.windowOpacity() == 1.0
    assert not effects_left(p)
    assert not p.outro_safety.isActive() and not p.watchdog.isActive()
    assert not p.reclaim_check.isActive() and not p.showing_animated()
    fly = p.flyout()
    if fly is not None:
        assert not fly.isVisible() and not fly.sliding() and not fly.closing()


# -- off / on ---------------------------------------------------------------------------
def test_animations_off_dismiss_hides_at_once(qtbot, tmp_path):
    p = popup_on(qtbot, tmp_path, MANY)
    p.dismiss()
    assert not p.isVisible() and p.outro() is None
    assert_clean(p)


def test_hide_is_always_immediate(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.hide()
    assert_clean(p)


def test_outro_plays_then_hides_clean(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY, tray=BOTTOM_RIGHT)
    timer = QElapsedTimer()
    timer.start()
    p.dismiss()
    o = p.outro()
    assert o is not None and p.dismissing() and not p.is_open() and p.isVisible()
    assert o["total"] <= 200 and p.outro_safety.isActive()
    out = o["cards"]
    # Bottom row first (in view), header last; 15 ms apart, capped.
    leaving = out.widgets()
    assert leaving[-1] is p._header and isinstance(leaving[0], AgendaRow)
    shown = [w for w in leaving if isinstance(w, AgendaRow)]
    assert shown == sorted(shown, key=lambda r: -r.y())
    assert out.delays()[:3] == [0, motion.OUTRO_STAGGER_MS, 2 * motion.OUTRO_STAGGER_MS]
    assert max(out.delays()) == (motion.OUTRO_MAX_STAGGER - 1) * motion.OUTRO_STAGGER_MS
    p.dismiss()                                      # twice: still the same outro
    assert p.outro() is o
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert timer.elapsed() < motion.OUTRO_SAFETY_MS
    assert_clean(p)


@pytest.mark.parametrize("tray, down", [(BOTTOM_RIGHT, True), (TOP_TRAY, False)])
def test_window_fades_and_sinks_toward_the_tray(qtbot, tmp_path, animated, tray, down):
    p = settled_popup(qtbot, tmp_path, MANY, tray=tray)
    qtbot.waitUntil(lambda: not p.building(), timeout=3000)      # rows in place
    docked = p.pos()
    seen = []
    p.dismiss()
    o = p.outro()
    o["anim"].valueChanged.connect(
        lambda v: seen.append((float(v), p.windowOpacity(), p.pos().y(),
                               [w.graphicsEffect().state() for w in o["cards"].widgets()
                                if isinstance(w.graphicsEffect(), cards.CardEffect)])))
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert len(seen) > 3
    ops = [s[1] for s in seen]
    assert all(b <= a + 1e-6 for a, b in zip(ops, ops[1:]))       # never brightens
    target = o["to_pos"].y()
    assert target != docked.y() and (target > docked.y()) == down
    assert abs(target - docked.y()) <= motion.OUTRO_SINK_PX
    for _t, _op, y, states in seen:
        assert min(docked.y(), target) <= y <= max(docked.y(), target)
        for op, dy, _s in states:
            assert 0.0 <= op <= 1.0 and 0.0 <= dy <= motion.OUTRO_DROP_PX + 1e-6
    # The window waits OUTRO_WINDOW_START_MS before fading.
    assert all(op == 1.0 for t, op, *_ in seen if t < motion.OUTRO_WINDOW_START_MS)


def test_basic_mode_fades_the_window_only(qtbot, tmp_path, animated, monkeypatch):
    monkeypatch.setenv(motion.ENV_LEVEL, "basic")
    p = settled_popup(qtbot, tmp_path, MANY)
    p.dismiss()
    o = p.outro()
    assert o["cards"] is None and o["span"] == (0, motion.OUTRO_BASIC_MS)
    assert not effects_left(p)
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


# -- never on quit, lock, suspend -------------------------------------------------------
def test_no_outro_on_quit(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.dismiss()
    assert p.dismissing()
    p.shutdown()                                     # mid-outro: hidden now
    assert_clean(p)
    p.show_near_tray(TOP_TRAY)
    p.dismiss()                                      # after quit began: never again
    assert not p.isVisible() and p.outro() is None


def test_no_outro_while_windows_locks_or_suspends(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.session_interrupted = lambda: True
    p.dismiss()
    assert not p.isVisible() and p.outro() is None
    p.session_interrupted = lambda: (_ for _ in ()).throw(OSError("x"))
    p.show_near_tray(TOP_TRAY)
    p.dismiss()                                      # a failing check: no outro either
    assert not p.isVisible() and p.outro() is None


def test_suspend_message_noted(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    assert not p._os_session_interrupted()
    p._interrupt_seen_at = p.clock()                 # (what WM_POWERBROADCAST notes)
    assert p._os_session_interrupted()
    p.dismiss()
    assert not p.isVisible()


# -- interruption -----------------------------------------------------------------------
def test_reopen_mid_outro_restores_without_jump(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY, tray=BOTTOM_RIGHT)
    docked = p.pos()
    p.dismiss()
    qtbot.waitUntil(lambda: p.windowOpacity() < 0.9, timeout=2000)
    op, pos = p.windowOpacity(), p.pos()
    p.show_near_tray(BOTTOM_RIGHT)
    assert p.is_open() and not p.dismissing() and p.isVisible()
    assert abs(p.windowOpacity() - op) < 0.05 and abs(p.pos().y() - pos.y()) <= 1
    assert p.restoring()
    qtbot.waitUntil(lambda: not p.restoring(), timeout=2000)
    assert p.windowOpacity() == 1.0 and p.pos() == docked and not effects_left(p)
    qtbot.wait(motion.OUTRO_SAFETY_MS + 100)         # the safety timer was stopped too
    assert p.isVisible() and p.is_open()


def test_dismiss_again_mid_restore_continues_from_there(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.dismiss()
    qtbot.wait(90)
    p.show_near_tray(TOP_TRAY)
    qtbot.wait(30)
    leaving = [w for w in p.row_widgets() + [p._header]
               if isinstance(w.graphicsEffect(), cards.CardEffect)]
    fx_before = {id(w): w.graphicsEffect() for w in leaving}
    p.dismiss()
    o = p.outro()
    assert o is not None and o["cards"] is not None
    for w in o["cards"].widgets():
        if id(w) in fx_before:
            assert w.graphicsEffect() is fx_before[id(w)]          # taken over, no jump
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


def test_close_mid_intro_leaves_from_where_it_is(qtbot, tmp_path, animated):
    p = popup_on(qtbot, tmp_path, MANY)
    qtbot.waitUntil(lambda: p.rows_build() is not None and p.rows_build().started()
                    and p.windowOpacity() > 0.2, timeout=2000)
    building = [r for r in rows(p) if isinstance(r.graphicsEffect(), cards.CardEffect)]
    fx = {id(r): r.graphicsEffect() for r in building}
    op = p.windowOpacity()
    p.dismiss()
    o = p.outro()
    assert not p.showing_animated() and p.rows_build() is None
    assert abs(o["from_opacity"] - op) < 0.05
    for r in o["cards"].widgets():
        if id(r) in fx:
            assert r.graphicsEffect() is fx[id(r)]
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


def test_activated_again_mid_outro_restores(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.active_window = lambda: None                   # focus left the group
    deactivate(p)
    assert p.dismissing() and not p.outro()["was_active"]
    activate(p)                                      # clicked back into it
    assert p.is_open()
    qtbot.waitUntil(lambda: not p.restoring(), timeout=2000)
    assert p.isVisible() and p.windowOpacity() == 1.0


def test_late_activation_after_esc_does_not_restore(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.active_window = lambda: p
    p.handle_escape()
    assert p.dismissing() and p.outro()["was_active"]
    activate(p)                                      # (e.g. a late activation event)
    assert p.dismissing()
    p.handle_escape()                                # Esc again: nothing new
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


def test_safety_timer_forces_the_hide(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    timer = QElapsedTimer()
    timer.start()
    p.dismiss()
    motion.stop(p.outro()["anim"])                   # its callbacks never fire (stop()
    assert p.isVisible()                             # emits no finished)
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert timer.elapsed() >= motion.OUTRO_SAFETY_MS - 50
    assert_clean(p)


def test_hide_mid_outro_is_immediate_and_clean(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.dismiss()
    qtbot.wait(80)
    p.hide()
    assert_clean(p)
    qtbot.wait(motion.OUTRO_SAFETY_MS + 50)
    assert not p.isVisible()


def test_refresh_during_outro_runs_after_it(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    before = rows(p)
    p.dismiss()
    p.refresh()                                      # e.g. a sync
    assert rows(p) == before                         # the leaving rows stay
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    settle_qt()
    # Ran after the hide (v0.10.0: nothing changed, so the same widgets stay).
    assert rows(p) == before and not p._refresh_deferred
    assert_clean(p)


def test_hide_meeting_and_chat_toggle_during_outro(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    p.attach_chat(lambda: None)
    p.dismiss()
    p.hide_meeting(rows(p)[0].event, "event")          # no collapse tween, no crash
    p.toggle_chat()
    assert p.dismissing() and not p.collapsing() and not p.revealing()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    settle_qt()
    assert_clean(p)
    assert "Event 0" not in [r.title for r in p.rows()]


# -- the details panel ------------------------------------------------------------------
def test_flyout_slides_back_before_the_window_fades(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    wait_active(qtbot, fly)
    qtbot.waitUntil(lambda: not fly.sliding())
    docked = p.pos()
    frames = []
    p.dismiss()
    o = p.outro()
    assert fly.closing() and fly.sliding()
    assert o["span"][0] == motion.OUTRO_FLYOUT_MS and o["total"] <= 200
    o["anim"].valueChanged.connect(
        lambda _v: frames.append((fly.isVisible(), p.windowOpacity(), p.pos())))
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    # While the panel is on screen the popup stays fully opaque and docked (nothing
    # shows through it, no corner of the panel peeks out).
    assert frames and all(op == 1.0 and pos == docked for vis, op, pos in frames if vis)
    assert_clean(p)


def test_flyout_already_sliding_back_retimes_into_the_outro(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    wait_active(qtbot, fly)
    qtbot.waitUntil(lambda: not fly.sliding())
    click(qtbot, rows(p)[0])                         # sliding back
    assert fly.closing()
    p.dismiss()
    assert fly._anim.duration() <= motion.OUTRO_FLYOUT_MS
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


def test_panel_over_the_popup_closes_at_once_without_taking_focus(qtbot, tmp_path,
                                                                 animated):
    narrow = QRect(0, 0, 560, 800)
    p = settled_popup(qtbot, tmp_path, MANY, tray=QRect(270, 0, 20, 20), area=narrow)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    assert p.flyout_side == "over" and fly.isVisible()
    calls = []
    p.activateWindow = lambda: calls.append(1)
    p.active_window = lambda: fly
    p.dismiss()
    assert p.dismissing() and not fly.isVisible() and p.outro()["flyout_ms"] == 0
    assert not calls and not p.reclaim_check.isActive()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


def test_outro_never_takes_focus_back_from_the_panel(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    fly = p.flyout()
    wait_active(qtbot, fly)
    qtbot.waitUntil(lambda: not fly.sliding())
    calls = []
    p.activateWindow = lambda: calls.append(1)
    p.active_window = lambda: None
    deactivate(fly)                                  # focus left the group
    qtbot.waitUntil(lambda: p.dismissing(), timeout=2000)
    p._settle()                                      # settle during the outro: nothing
    p._watchdog_tick()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert not calls and not p.reclaim_check.isActive()
    assert_clean(p)


def test_rapid_open_close_leaves_nothing_behind(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, MANY)
    for i in range(6):
        p.dismiss()
        qtbot.wait(20 + 25 * (i % 3))
        p.show_near_tray(TOP_TRAY)
        qtbot.wait(15)
    p.dismiss()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


# -- the tray icon ----------------------------------------------------------------------
TRAY = QRect(100, 100, 20, 20)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def tray_and_popup(qtbot, tmp_path):
    settle_qt()
    move_focus_away(qtbot)
    p = popup_on(qtbot, tmp_path, MANY, tray=TRAY)
    qtbot.waitUntil(lambda: not p.showing_animated())
    p.clock = Clock()
    tray = TrayController(p, FakeWorker(), p.cache, p.settings)
    tray._tray_rect = lambda: TRAY
    return tray, p


def test_tray_click_while_open_closes_with_outro_and_does_not_reopen(qtbot, tmp_path,
                                                                     animated):
    tray, p = tray_and_popup(qtbot, tmp_path)
    p.cursor_pos = lambda: QPoint(105, 105)          # on the tray icon
    # Windows: the press deactivates the popup first, then the click reaches the icon.
    p.active_window = lambda: None
    deactivate(p)
    assert p.dismissing()
    p.clock.t += 0.1
    tray.toggle_popup()                              # the same click: no reopen
    assert p.dismissing()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    p.clock.t += 0.2
    tray.toggle_popup()                              # still that click's window
    assert not p.isVisible()
    p.clock.t += 5
    tray.toggle_popup()                              # a later click opens it
    assert p.isVisible() and p.is_open()


def test_tray_toggle_without_deactivation_dismisses(qtbot, tmp_path, animated):
    tray, p = tray_and_popup(qtbot, tmp_path)
    tray.toggle_popup()
    assert p.dismissing() and not p.is_open()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    assert_clean(p)


def test_tray_click_mid_outro_from_elsewhere_reopens(qtbot, tmp_path, animated):
    tray, p = tray_and_popup(qtbot, tmp_path)
    p.cursor_pos = lambda: QPoint(900, 700)          # Esc / a click elsewhere
    p.active_window = lambda: p
    p.handle_escape()
    assert p.dismissing()
    p.clock.t += 0.1
    tray.toggle_popup()
    assert p.is_open() and not p.dismissing()
    qtbot.waitUntil(lambda: not p.restoring(), timeout=2000)
    assert p.isVisible() and p.windowOpacity() == 1.0


def test_second_launch_mid_outro_brings_it_back(qtbot, tmp_path, animated):
    tray, p = tray_and_popup(qtbot, tmp_path)
    p.dismiss()
    tray.show_popup()
    assert p.is_open()
    qtbot.waitUntil(lambda: not p.restoring(), timeout=2000)
    assert p.isVisible()


def test_tray_quit_hides_without_outro(qtbot, tmp_path, animated):
    tray, p = tray_and_popup(qtbot, tmp_path)
    tray._quit_app = lambda: None
    tray.worker.wait = lambda _ms: True
    p.dismiss()
    tray.quit()
    assert_clean(p)


def test_outro_window_span():
    assert motion.outro_window_span(True) == (motion.OUTRO_WINDOW_START_MS, motion.OUTRO_MS)
    s, e = motion.outro_window_span(True, motion.OUTRO_FLYOUT_MS)
    assert s == motion.OUTRO_FLYOUT_MS and e - s >= motion.OUTRO_WINDOW_MIN_MS and e <= 200
    assert motion.outro_window_span(False) == (0, motion.OUTRO_BASIC_MS)
    assert motion.ease(motion.ACCEL, 0.5) < 0.5                # an ease-in
    assert motion.ease(motion.ACCEL, 1.0) == 1.0


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
    from smartcal.ui import motion, winframe
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
    r = fly.geometry().united(p.geometry()).adjusted(0, -10, 0, 20)
    locked_check = winframe.input_desktop_locked()
    p.hide(); pump(200)
    back = QWidget()                    # a black backdrop: any white is ours
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(r.adjusted(-20, -40, 20, 40))
    back.show(); pump(300)
    bad_frames = []                     # a translucent popup with the panel still up
    outros = [0]
    def watch():
        o = p.outro()
        if o is None or o.get("watched"):
            return
        o["watched"] = True; outros[0] += 1
        o["anim"].valueChanged.connect(
            lambda _v: bad_frames.append(1)
            if p.windowOpacity() < 1.0 and not fly.isHidden() else None)
    cap = subprocess.Popen([sys.executable, str(tmp / "capture.py"), str(r.x()), str(r.y()),
                            str(r.width()), str(r.height()), "16", str(tmp / "cap.npy")])
    pump(400)
    t = QElapsedTimer(); t.start(); k = 0; checks = []; restored = 0
    while t.elapsed() < 14000:
        p.refresh(); p.show_near_tray(tray)
        step = k % 6
        if step == 0:                                       # close mid-intro
            pump(60); p.dismiss(); watch(); pump(260)
        elif step == 1:                                     # panel open, then dismiss
            pump(500); p.select_row(rows()[k % 4]); pump(300)
            p.handle_escape(); pump(20); p.handle_escape(); watch(); pump(260)
        elif step == 2:                                     # reopen mid-outro
            pump(450); p.dismiss(); watch(); pump(70)
            p.show_near_tray(tray); restored += int(p.is_open()); pump(250)
            p.dismiss(); watch(); pump(260)
        elif step == 3:                                     # focus lost (real)
            pump(450); back.activateWindow(); pump(30); watch(); pump(300)
        elif step == 4:                                     # hide a meeting, chat, mid-outro
            pump(450); p.dismiss(); watch(); pump(30)
            p.hide_meeting(rows()[-2].event, "event"); p.toggle_chat(); pump(40)
            settings.unhide_all(); pump(260)
            if p.chat_expanded():
                p.show_near_tray(tray); pump(150); p.toggle_chat(); pump(60)
                p.dismiss(); watch(); pump(260)
        else:                                               # rapid toggles
            for j in range(4):
                pump(40 + 30 * j); p.dismiss(); watch(); pump(25 * j)
                p.show_near_tray(tray)
            pump(200); p.dismiss(); watch(); pump(260)
        pump(80)
        checks.append(not p.isVisible() and p.windowOpacity() == 1.0 and p.outro() is None
                      and all(not isinstance(w.graphicsEffect(), CardEffect)
                              for w in rows() + [p._header, p._next])
                      and not p.outro_safety.isActive() and fly.isHidden())
        k += 1
    cap.wait()
    info = {"clean": all(checks), "loops": k, "outros": outros[0], "restored": restored,
            "bad_frames": len(bad_frames), "locked_check": locked_check,
            "geometry_warnings": [m for m in msgs if "geometry" in m.lower()][:5]}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_outro(tmp_path):
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
    assert info["clean"] and info["loops"] >= 8 and info["outros"] > 15, info
    assert info["restored"] > 0 and info["bad_frames"] == 0, info
    assert info["locked_check"] is False, info              # unlocked: outro allowed
    assert info["geometry_warnings"] == [], info
    frames = np.load(tmp_path / "cap.npy")
    assert len(frames) > 200
    white = frames.min(axis=3) > 200
    # a white block: a column more than 30% white (text never fills that much)
    bad = np.nonzero((white.mean(axis=1) > 0.3).sum(axis=1) > 2)[0]
    assert len(bad) == 0, f"{len(bad)} frames with white, first at {bad[:5]}"
