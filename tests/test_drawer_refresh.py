"""v0.10.0: (1) a refresh that changes nothing changes nothing on screen (no rebuilt row,
no relayout, no geometry change, no effect; scroll, focus and selection survive), and a
refresh with one changed meeting rebuilds only that row; (2) the Ask Claude drawer
(drawer.py): state machine, gating by the animation setting, interruption, coexistence
with the details panel, keyboard. Offscreen Qt. The real-display runs (screen grabs)
are at the end (SMARTCAL_TESTS_REAL_DISPLAY=1)."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from smartcal.ui import cards, motion, theme, uitrace
from smartcal.ui.cards import CardEffect
from test_chat import FakeAssistant
from test_meeting_actions_ui import click, ev, open_details, rows, settle_qt
from test_polish_ui import settled, shown_popup
from test_popup_animations import MANY
from test_popup_flyout import BOTTOM_RIGHT
from smartcal.ui import meeting_menus

LINK = "https://zoom.us/j/1"


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    monkeypatch.delenv(motion.ENV_LEVEL, raising=False)


@pytest.fixture
def trace():
    uitrace.sink = []
    yield uitrace.sink
    uitrace.sink = None


def snapshot(p):
    return ([id(r) for r in rows(p)], [r.geometry() for r in rows(p)], p.geometry(),
            p._scroll.height(), p._scroll.verticalScrollBar().value())


# -- (1) refresh idempotence -------------------------------------------------------------
def test_identical_refresh_touches_nothing(qtbot, tmp_path, animated, trace):
    p = shown_popup(qtbot, tmp_path, MANY + [ev("j", "Join me", 18, meeting_url=LINK)])
    rs = rows(p)
    rs[6].setFocus()
    p._scroll.verticalScrollBar().setValue(30)
    settle_qt()
    before = snapshot(p)
    trace.clear()
    heights = []
    orig = p._scroll.setFixedHeight
    p._scroll.setFixedHeight = lambda h: (heights.append(h), orig(h))
    for _ in range(5):
        p.refresh()                                  # the 30 s tick
        p.set_account_status("a", "ok")             # a sync's status...
        p.prune_accounts(["a"])
        p.refresh()                                  # ...and its completion
    settle_qt()
    assert snapshot(p) == before
    assert QApplication.focusWidget() is rs[6]
    assert heights == []                             # no relayout at all
    assert {w for w, _f in trace} == {"refresh"}
    assert all(f["result"] == "unchanged" for _w, f in trace)
    assert not p.animations_running()
    assert all(r.graphicsEffect() is None for r in rows(p))


def test_identical_refresh_with_details_open_keeps_selection_and_panel(qtbot, tmp_path,
                                                                     animated):
    p = shown_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[3])
    fly = p.flyout()
    settled(qtbot, p)
    builds, sel = fly.builds, rows(p)[3]
    p.cache.upsert_events("a", MANY)                 # a sync storing the same events
    p.refresh()
    assert rows(p)[3] is sel and sel.is_selected() and p.flyout_open()
    assert fly.builds == builds and not fly.crossfading() and not p.animations_running()


def test_one_changed_event_rebuilds_only_its_row(qtbot, tmp_path, animated, trace):
    p = shown_popup(qtbot, tmp_path, MANY[:6])
    old = rows(p)
    geoms = [r.geometry() for r in old]
    changed = MANY[2]
    from dataclasses import replace
    p.cache.upsert_events("a", [replace(changed, title="Renamed")])
    trace.clear()
    p.refresh()
    new = rows(p)
    assert [new[i] is old[i] for i in range(6)] == [True, True, False, True, True, True]
    assert new[2].event.title == "Renamed" and p.rows()[2].title == "Renamed"
    assert [r.geometry() for r in new] == geoms      # same height: nothing moved
    hits = [f for w, f in trace if w == "refresh"]
    assert hits[-1]["created"] == 1 and hits[-1]["deleted"] == 1 and hits[-1]["kept"] == 5


def test_new_and_removed_events_keep_the_other_rows(qtbot, tmp_path, animated):
    p = shown_popup(qtbot, tmp_path, MANY[:4])
    old = rows(p)
    p.cache.upsert_events("a", [ev("n", "New one", 8, 50)])
    p.cache.delete_event("a", "primary", MANY[0].id)
    p.refresh()
    new = rows(p)
    assert old[1] in new and old[2] in new and old[3] in new and old[0] not in new
    assert len(new) == 4 and [r.event.title for r in new].count("New one") == 1
    settled(qtbot, p)
    assert all(r.graphicsEffect() is None for r in rows(p))


def test_non_visual_change_updates_the_row_event_without_rebuilding(qtbot, tmp_path):
    p = shown_popup(qtbot, tmp_path, MANY[:3])
    old = rows(p)
    from dataclasses import replace
    p.cache.upsert_events("a", [replace(MANY[1], description="New notes")])
    p.refresh()
    assert rows(p) == old and rows(p)[1].event.description == "New notes"


def test_status_line_unchanged_does_nothing_and_change_slides(qtbot, tmp_path, animated,
                                                              trace):
    p = shown_popup(qtbot, tmp_path, MANY[:3])
    h0 = p.height()
    trace.clear()
    for _ in range(3):
        p.set_account_status("a", "ok")
    assert trace == [] and p.height() == h0
    p.set_account_status("a", "offline")
    assert p.revealing(p._status) and p.status_text().startswith("Offline")
    settled(qtbot, p)
    assert p.height() > h0
    p.set_account_status("a", "ok")
    assert p.revealing(p._status) and p.status_text() == ""
    settled(qtbot, p)
    assert p.height() == h0 and p._status.isHidden()


def test_changed_refresh_lays_the_list_out_before_returning(qtbot, tmp_path, animated):
    # Nothing is left to a posted layout request: right after refresh() every row sits
    # where the layout puts it (a paint in between showed an empty list on Windows).
    p = shown_popup(qtbot, tmp_path, MANY[:5])
    p.settings.set_event_color(MANY[0], "#e8594f")
    p.refresh()
    rs = rows(p)
    assert all(r.height() > 5 for r in rs)
    assert [r.y() for r in rs] == sorted(r.y() for r in rs) and rs[0].y() == 0
    assert len({r.y() for r in rs}) == len(rs)


# -- (2) the drawer ----------------------------------------------------------------------
def popup_with_chat(qtbot, tmp_path, events=None, **kw):
    p = shown_popup(qtbot, tmp_path, events if events is not None else MANY[:2],
                    tray=BOTTOM_RIGHT, **kw)
    fake = FakeAssistant()
    p.attach_chat(lambda: fake)
    return p, fake


def no_card_effects(p):
    d = p.drawer()
    ws = [d.header, d.chat.input_row, d.chat._new_btn, d.chat._send_btn] + d.chat.items()
    return all(not isinstance(w.graphicsEffect(), CardEffect) for w in ws)


def test_drawer_opens_from_the_top_and_builds_in(qtbot, tmp_path, animated):
    p, _ = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    d = p.drawer()
    assert d.parent() is p and d.isWindow() is False          # a child, not a window
    assert p.mask().isEmpty()                                  # no mask on the popup
    assert d.state() == "opening" and d.building()
    assert isinstance(d.header.graphicsEffect(), CardEffect)
    ys = []
    d._anim.valueChanged.connect(lambda _v: ys.append(d.y()))
    assert QApplication.focusWidget() is d.chat._input          # typing goes there
    settled(qtbot, p)
    assert ys and ys[0] < ys[-1] and max(ys) <= 0               # downward, never a gap
    assert d.state() == "open" and d.y() == -motion.DRAWER_PAD_PX and no_card_effects(p)
    p.toggle_chat()
    assert d.state() == "closing"
    settled(qtbot, p)
    assert d.state() == "closed" and d.isHidden() and no_card_effects(p)


@pytest.mark.parametrize("mode", ["off", "basic"])
def test_drawer_gated_by_animation_setting(qtbot, tmp_path, monkeypatch, mode):
    if mode == "basic":
        monkeypatch.setattr(motion, "animations_enabled", lambda: True)
        monkeypatch.setenv(motion.ENV_LEVEL, "basic")
    else:
        monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    p, fake = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    d = p.drawer()
    assert not d.building() and no_card_effects(p)              # never a stagger
    if mode == "off":
        assert d.state() == "open" and d.y() == -motion.DRAWER_PAD_PX   # at once
    else:
        assert d.state() == "opening"                           # the slide only
    qtbot.waitUntil(lambda: d.state() == "open", timeout=2000)
    d.chat.send("hi")
    assert not d.chat.arrivals_running()
    p.toggle_chat()
    qtbot.waitUntil(lambda: d.state() == "closed", timeout=2000)
    assert no_card_effects(p)


def test_rapid_toggles_never_leave_it_half_open(qtbot, tmp_path, animated):
    p, _ = popup_with_chat(qtbot, tmp_path)
    for i in range(15):
        p.toggle_chat()
        qtbot.wait([5, 30, 70][i % 3])
    d = p.drawer()
    assert d.state() == "opening"                               # 15 toggles: open
    settled(qtbot, p)
    assert d.state() == "open" and d.openness() == 1.0 and no_card_effects(p)
    p.toggle_chat()
    qtbot.wait(40)
    p.toggle_chat()
    qtbot.wait(20)
    p.toggle_chat()
    settled(qtbot, p)
    assert d.state() == "closed" and d.openness() == 0.0 and d.isHidden()
    assert not d.findChildren(cards.CardBuild) or all(
        not b.running() for b in d.findChildren(cards.CardBuild))


def test_hide_popup_mid_slide_lands_and_reopens_clean(qtbot, tmp_path, animated):
    p, _ = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    d = p.drawer()
    qtbot.wait(60)
    p.hide()
    assert d.state() == "open" and not d.animating() and no_card_effects(p)
    assert not p.animations_running()
    p.show_near_tray(p._tray_geom)
    settled(qtbot, p)
    assert d.isVisible() and d.y() == -motion.DRAWER_PAD_PX and p.chat_expanded()
    p.toggle_chat()
    qtbot.wait(40)
    p.hide()                                                    # mid-retract
    assert d.state() == "closed" and d.isHidden() and not p.animations_running()


def test_chat_and_details_never_open_together(qtbot, tmp_path, animated):
    p, _ = popup_with_chat(qtbot, tmp_path, MANY[:4])
    open_details(qtbot, p, rows(p)[1])          # (offscreen activates the panel: wait)
    assert p.flyout_open()
    p.toggle_chat()
    assert p.chat_expanded() and not p.flyout_open()
    settled(qtbot, p)
    p.select_row(rows(p)[2])
    assert p.flyout_open() and not p.chat_expanded()
    settled(qtbot, p)
    assert p.drawer().isHidden()


def test_esc_closes_drawer_first_then_popup(qtbot, tmp_path):
    p, _ = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    qtbot.keyClick(p.chat()._input, Qt.Key.Key_Escape)
    assert not p.chat_expanded() and p.isVisible()
    p.toggle_chat()
    qtbot.keyClick(p.drawer().close_button, Qt.Key.Key_Escape)
    assert not p.chat_expanded() and p.isVisible()
    qtbot.keyClick(p, Qt.Key.Key_Escape)
    assert not p.isVisible()


def test_tab_stays_inside_the_drawer(qtbot, tmp_path):
    p, _ = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    d = p.drawer()
    seen = set()
    for _ in range(8):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        w = QApplication.focusWidget()
        assert d.isAncestorOf(w), type(w).__name__
        seen.add(w)
    assert d.chat._input in seen and d.close_button in seen


def test_short_popup_grows_for_the_drawer_tall_one_does_not(qtbot, tmp_path, animated):
    p, _ = popup_with_chat(qtbot, tmp_path)                     # 2 meetings: short
    h0 = p.height()
    p.toggle_chat()
    assert p.height_animating()                                  # the tween, not a snap
    settled(qtbot, p)
    d = p.drawer()
    vis = d.height() - motion.DRAWER_PAD_PX - motion.DRAWER_SHADOW_PX
    assert p.height() > h0 and vis >= motion.DRAWER_MIN_PX
    p.toggle_chat()
    settled(qtbot, p)
    assert p.height() == h0
    (tmp_path / "t").mkdir(exist_ok=True)
    tall, _ = popup_with_chat(qtbot, tmp_path / "t", MANY)
    h1 = tall.height()
    tall.toggle_chat()
    assert not tall.height_animating()
    settled(qtbot, tall)
    assert tall.height() == h1


def test_message_arrives_sliding_up_and_thinking_dots_pulse(qtbot, tmp_path, animated):
    p, fake = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    settled(qtbot, p)
    chat = p.chat()
    import threading
    from smartcal.assistant import AssistantResult
    gate = threading.Event()
    fake.script = [lambda: (gate.wait(3), AssistantResult("Hello there", False))[1]]
    chat._input.setText("hi")
    chat.send()
    user = chat.items()[0]
    assert isinstance(user.graphicsEffect(), CardEffect)         # arriving
    thinking = chat._thinking
    assert thinking is not None
    qtbot.waitUntil(thinking.dots.running, timeout=1000)        # (shown on the next pass)
    o = [thinking.dots.dot_opacity(i) for i in range(3)]
    qtbot.wait(200)
    assert [thinking.dots.dot_opacity(i) for i in range(3)] != o   # pulsing
    gate.set()
    qtbot.waitUntil(lambda: not chat.is_busy(), timeout=3000)
    settle_qt()
    from PyQt6 import sip
    assert sip.isdeleted(thinking.dots) or not thinking.dots.running()   # gone with it
    assert chat._thinking is None
    settled(qtbot, p)
    assert all(i.graphicsEffect() is None for i in chat.items())
    assert "Hello there" in "\n".join(chat.texts())


def test_send_button_press_scale(qtbot, tmp_path, animated):
    p, _ = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    settled(qtbot, p)
    send = p.chat()._send_btn
    fb = p.chat().press_feedback["send"]
    send.pressed.emit()
    qtbot.waitUntil(lambda: fb.scale() == pytest.approx(motion.PRESS_SCALE), timeout=1000)
    send.released.emit()
    qtbot.waitUntil(lambda: send.graphicsEffect() is None, timeout=1000)


def test_hide_meeting_during_drawer_animation_keeps_its_room(qtbot, tmp_path, animated):
    p, _ = popup_with_chat(qtbot, tmp_path, MANY[:3])
    p.toggle_chat()
    qtbot.wait(50)
    p.hide_meeting(rows(p)[0].event, "event")
    settled(qtbot, p)
    d = p.drawer()
    vis = d.height() - motion.DRAWER_PAD_PX - motion.DRAWER_SHADOW_PX
    assert d.state() == "open" and vis >= motion.DRAWER_MIN_PX
    assert d.geometry().bottom() < p._ask.geometry().top()
    assert len(rows(p)) == 2 and no_card_effects(p)


def test_drawer_paints_opaque_dark(qtbot, tmp_path):
    p, _ = popup_with_chat(qtbot, tmp_path)
    p.toggle_chat()
    img = p.grab().toImage()
    from PyQt6.QtGui import QColor
    pts = [(x, y) for y in range(5, p.drawer().geometry().bottom() - 10, 3)
           for x in range(4, p.width() - 4, 3)]
    light = [pt for pt in pts if QColor(img.pixel(*pt)).lightness() > 200]
    assert len(light) < 0.04 * len(pts)                # text only, never a light area
    assert QColor(img.pixel(p.width() // 2, 60)).name() == theme.BG   # (empty chat)


# -- the real Windows display (opt in) ------------------------------------------------------
REAL = pytest.mark.skipif(sys.platform != "win32"
                          or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                          reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")

SOAK = textwrap.dedent(r"""
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
    from smartcal.ui.popup import AgendaPopup, AgendaRow
    from test_meeting_actions_ui import NOW, ev, store
    from test_popup_animations import MANY
    import numpy as np
    evs = MANY + [ev(f"x{i}", f"Extra {i}", 13 + i // 2, (i % 2) * 30) for i in range(10)] \
        + [ev("d", "Design review", 18, description="Walk through it.",
              meeting_url="https://zoom.us/j/1")]
    cache, settings = store(tmp, evs)
    p = AgendaPopup(cache, settings); p.now = lambda: NOW; p.set_accounts(["a"]); p.refresh()
    p.attach_chat(lambda: None)
    scr = app.primaryScreen().availableGeometry()
    tray = QRect(scr.right() - 40, scr.bottom() + 5, 20, 20)
    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents()
    def rows():
        return [r for r in p.row_widgets() if isinstance(r, AgendaRow)]
    def tick():
        # what run.py does while the popup sits open: the 30 s tick, a sync's status and
        # completion (the same events stored again)
        p.prune_accounts(["a"]); p.refresh(); p.set_account_status("a", "ok")
        cache.upsert_events("a", evs); p.set_accounts(["a"]); p.refresh()
    back = QWidget()
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;")
    results = {}
    def phase(name, setup, secs=3.5):
        p.show_near_tray(tray); pump(1200)
        setup(); pump(1000)
        r = p.geometry()
        fly = p.flyout()
        if fly is not None and fly.isVisible():
            r = r.united(fly.geometry())
        back.setGeometry(r.adjusted(-20, -40, 20, 40)); back.show(); back.lower(); pump(200)
        p.raise_(); pump(200)
        out = tmp / f"{name}.npy"
        cap = subprocess.Popen([sys.executable, str(tmp / "capture.py"), str(r.x()), str(r.y()),
                                str(r.width()), str(r.height()), str(secs), str(out)])
        pump(300)
        t = QElapsedTimer(); t.start(); n = 0
        focus0 = QApplication.focusWidget(); scroll0 = p._scroll.verticalScrollBar().value()
        geo0 = p.geometry()
        stable = True
        while t.elapsed() < (secs - 0.6) * 1000:
            tick(); n += 1; pump(150)
            stable &= (QApplication.focusWidget() is focus0
                       and p._scroll.verticalScrollBar().value() == scroll0
                       and p.geometry() == geo0)
        cap.wait()
        frames = np.load(out).astype(np.int16)
        d = np.abs(np.diff(frames, axis=0)).max(axis=3) > 24
        # A text cursor blinking (a 1-2 px wide column) is the only change allowed.
        cols = d.any(axis=1).sum(axis=1)
        changed = int(((d.sum(axis=(1, 2)) > 6) & (cols > 2)).sum())
        results[name] = {"frames": int(len(frames)), "ticks": n, "changed": changed,
                         "stable": bool(stable)}
        back.hide(); p.hide(); pump(300)
    phase("idle", lambda: None)
    phase("focused_scrolled", lambda: (rows()[5].setFocus(),
                                       p._scroll.verticalScrollBar().setValue(40)))
    phase("details_open", lambda: p.select_row(rows()[2]))
    phase("chat_open", lambda: p.chat_expanded() or p.toggle_chat())
    info = {"phases": results,
            "geometry_warnings": [m for m in msgs if "geometry" in m.lower()][:5]}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


STRESS = textwrap.dedent(r"""
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
    evs = MANY[:6] + [ev("d", "Design review", 18, description="Walk through it.",
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
    p.show_near_tray(tray); pump(900); p.toggle_chat(); pump(900)
    r = p.geometry()
    p.select_row(rows()[0]); pump(700)
    r = r.united(p.flyout().geometry()).united(p.geometry())
    p.hide(); pump(200)
    back = QWidget()                    # a black backdrop: any white is ours
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(r.adjusted(-20, -60, 20, 40))
    back.show(); pump(300)
    cap = subprocess.Popen([sys.executable, str(tmp / "capture.py"), str(r.x() - 0),
                            str(r.y() - 40), str(r.width()), str(r.height() + 40), "9",
                            str(tmp / "cap.npy")])
    pump(400)
    t = QElapsedTimer(); t.start(); k = 0; checks = []
    while t.elapsed() < 7500:
        p.show_near_tray(tray); pump([40, 150, 400][k % 3])
        for _ in range(3 + k % 4):                          # rapid toggles
            p.toggle_chat(); pump([15, 60, 120, 250][k % 4])
        if k % 3 == 0:
            p.select_row(rows()[k % 3]); pump(80)           # panel while chat is open
            p.toggle_chat(); pump(100)                      # and chat while panel is open
        if k % 3 == 1:
            if not p.chat_expanded():
                p.toggle_chat()
            pump(60)
            p.hide_meeting(rows()[-2].event, "event"); pump(70)   # mid drawer animation
            settings.unhide_all(); p.refresh(fade_in_new=True); pump(60)
        if k % 4 == 3:
            p.refresh(); p.set_account_status("a", "ok"); pump(30)
        p.hide(); pump(20 if k % 2 else 150)                # close with drawer open
        d = p.drawer()
        checks.append(d.state() in ("open", "closed") and d.openness() in (0.0, 1.0)
                      and all(not isinstance(w.graphicsEffect(), CardEffect)
                              for w in rows() + [d.header, d.chat.input_row]))
        k += 1
    cap.wait()
    info = {"clean": all(checks), "loops": k,
            "geometry_warnings": [m for m in msgs if "geometry" in m.lower()][:5]}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


def _run_real(tmp_path, script_text):
    import json
    from test_rounded_stack import CAPTURE
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(motion.ENV_LEVEL, None)
    (tmp_path / "capture.py").write_text(CAPTURE, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(script_text, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent),
                        str(tests)], env=env, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=240)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    return json.loads((tmp_path / "info.json").read_text())


@REAL
def test_real_display_open_popup_soak_shows_no_change(tmp_path):
    info = _run_real(tmp_path, SOAK)
    assert info["geometry_warnings"] == [], info
    for name, ph in info["phases"].items():
        assert ph["frames"] > 150 and ph["ticks"] > 8, (name, ph)
        assert ph["changed"] == 0, (name, ph)            # no frame-to-frame change
        assert ph["stable"], (name, ph)                  # focus, scroll, geometry kept


@REAL
def test_real_display_drawer_stress_no_white(tmp_path):
    import numpy as np
    info = _run_real(tmp_path, STRESS)
    assert info["clean"] and info["loops"] > 5, info
    assert info["geometry_warnings"] == [], info
    frames = np.load(tmp_path / "cap.npy")
    assert len(frames) > 200
    white = frames.min(axis=3) > 200
    bad = np.nonzero((white.mean(axis=1) > 0.3).sum(axis=1) > 2)[0]
    assert len(bad) == 0, f"{len(bad)} frames with white, first at {bad[:5]}"
