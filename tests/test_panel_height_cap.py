"""v0.9.2: the details panel is never taller than the popup and its top is the popup's
top, at every moment: opening, switching meetings, and while the popup's height changes
under an open panel (a status line, a notice, the chat, a hide, a refresh), tweened or in
one step, with the tray at the bottom or the top. Offscreen Qt: an event filter checks
every move and resize of either window; with SMARTCAL_TESTS_REAL_DISPLAY=1 a real-window
run checks the windows' on-screen bounds (DWM) the same way."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QObject, QSize
from PyQt6.QtWidgets import QApplication

from smartcal.ui import motion
from test_meeting_actions_ui import ev, rows
from test_popup_animations import MANY, settled_popup
from test_popup_flyout import BOTTOM_RIGHT, TOP_TRAY

LONG = "\n".join(f"line {i} of a long description that wraps in the panel" for i in range(60))
MEDIUM = "\n".join(f"line {i}" for i in range(8))


def few():
    return [ev("l", "Long notes", 9, 30, description=LONG, meeting_url="https://zoom.us/j/1"),
            ev("m", "Medium", 9, 40, description=MEDIUM), ev("s", "Short", 10)]


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


class Invariant(QObject):
    """Checks panel-inside-popup (vertically) after every move/resize of either window."""

    def __init__(self, popup):
        super().__init__()
        self.p = popup
        self.checks = 0
        self.bad = []
        self.tag = ""
        for w in (popup, popup.flyout()):
            w.installEventFilter(self)

    def check(self):
        p, fly = self.p, self.p.flyout()
        if (fly is None or not fly.isVisible() or not p.isVisible()
                or p.flyout_side == "over" or p.dismissing()):
            return
        self.checks += 1
        pg, fg = p.geometry(), fly.geometry()
        if fg.top() < pg.top() or fg.bottom() > pg.bottom() or fly.height() > p.height():
            self.bad.append((self.tag, (pg.top(), pg.bottom()), (fg.top(), fg.bottom())))

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.check()
        return False


def run(qtbot, inv, tag, ms):
    inv.tag = tag
    qtbot.wait(ms)
    inv.check()


def scenario(qtbot, p, inv):
    """Everything that changes the popup's height under an open panel, cut mid-way too."""
    for mid in (40, 400):
        click_row(p, 0)
        run(qtbot, inv, "open", mid)
        click_row(p, 1)
        run(qtbot, inv, "switch", mid)
        click_row(p, 0)
        run(qtbot, inv, "switch back", mid)
        p.set_account_status("a", "offline")
        run(qtbot, inv, "status line on", mid)
        p.set_account_status("a", "ok")                 # shrinks in one step
        run(qtbot, inv, "status line off", mid)
        p.show_notice("A notice")
        run(qtbot, inv, "notice", mid)
        p.show_notice("Another, longer notice that wraps onto a second line here")
        run(qtbot, inv, "notice replaced", mid)
        p.show_notice("")
        run(qtbot, inv, "notice cleared", mid)
        p.toggle_chat()
        run(qtbot, inv, "chat open", mid)
        p.set_account_status("a", "error")
        run(qtbot, inv, "status during chat", mid)
        p.set_account_status("a", "ok")
        p.toggle_chat()
        run(qtbot, inv, "chat closed", mid)
        p.refresh()
        run(qtbot, inv, "refresh", mid)
        if len(rows(p)) > 2:
            p.hide_meeting(rows(p)[-1].event, "event")
            run(qtbot, inv, "hide a meeting", mid)
        p.close_flyout(restore_focus=False)
        run(qtbot, inv, "close", 400)


def click_row(p, i):
    rs = rows(p)
    p.select_row(rs[min(i, len(rs) - 1)])


@pytest.mark.parametrize("tray", [BOTTOM_RIGHT, TOP_TRAY], ids=["bottom", "top"])
@pytest.mark.parametrize("events", ["few", "many"])
def test_panel_never_taller_than_the_popup(qtbot, tmp_path, animated, tray, events):
    evs = few() if events == "few" else few() + MANY
    p = settled_popup(qtbot, tmp_path, evs, tray=tray)
    p.attach_chat(lambda: None)
    click_row(p, 0)
    inv = Invariant(p)
    qtbot.waitUntil(lambda: not p.flyout().sliding())
    scenario(qtbot, p, inv)
    assert inv.checks > 50
    assert not inv.bad, inv.bad[:5]


def test_long_description_with_few_meetings_is_capped_and_scrolls(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, few()[:1], tray=BOTTOM_RIGHT)
    click_row(p, 0)
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding())
    assert fly.content_height() > p.height()
    assert fly.height() == p.height() and fly.y() == p.y()
    box = fly.description_box()
    bars = [fly._scroll.verticalScrollBar(), box.verticalScrollBar()]
    assert any(b.maximum() > 0 for b in bars)            # the rest scrolls into view


def test_shrink_tween_never_starts_above_the_popup(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, few(), tray=BOTTOM_RIGHT)
    click_row(p, 0)
    fly = p.flyout()
    qtbot.waitUntil(lambda: not fly.sliding())
    h = p.height()
    fly.cap_height = h - 30                                # the popup is about to shrink
    now = fly._apply_size(QSize(fly.width(), h - 30))
    assert now is not None and now.height() == h - 30      # cut at once, with the move
    fly.cap_height = h - 30
    fly._resize_height(h)                                  # an overshooting frame
    assert fly.height() <= h - 30


def test_slide_follows_the_popup_top_at_once(qtbot, tmp_path, animated):
    p = settled_popup(qtbot, tmp_path, few(), tray=BOTTOM_RIGHT)
    p.set_account_status("a", "offline")
    qtbot.wait(400)
    click_row(p, 0)
    fly = p.flyout()
    inv = Invariant(p)
    qtbot.waitUntil(lambda: fly._anim.state() == fly._anim.State.Running, timeout=1000)
    p.set_account_status("a", "ok")                        # shrinks mid-slide
    assert fly.y() == p.y() and fly.height() <= p.height()
    qtbot.waitUntil(lambda: not fly.sliding())
    assert not inv.bad, inv.bad[:5]


# -- the real Windows platform (opt in: it opens real windows) ---------------------------
RUN = textwrap.dedent(r"""
    import ctypes, faulthandler, json, sys
    from ctypes import wintypes
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QRect, QTimer
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    from smartcal.ui import motion
    motion.animations_enabled = lambda: True
    from smartcal.ui.popup import AgendaPopup, AgendaRow
    from test_meeting_actions_ui import NOW, store
    from test_panel_height_cap import few
    from test_popup_animations import MANY
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.DWORD)
    def bounds(w):                      # on screen, as DWM shows it (extended frame)
        r = wintypes.RECT()
        dwm.DwmGetWindowAttribute(ctypes.c_void_p(int(w.winId())), 9, ctypes.byref(r),
                                  ctypes.sizeof(r))
        return r.top, r.bottom
    out = {}
    scr = app.primaryScreen().availableGeometry()
    trays = {"bottom": QRect(scr.right() - 40, scr.bottom() + 5, 20, 20),
             "top": QRect(scr.right() - 40, scr.top() - 25, 20, 20)}
    for name, evs in (("few", few()), ("many", few() + MANY)):
        for side, tray in trays.items():
            sub = tmp / f"{name}-{side}"; sub.mkdir()
            cache, settings = store(sub, evs)
            p = AgendaPopup(cache, settings); p.now = lambda: NOW; p.refresh()
            p.attach_chat(lambda: None)
            stats = {"checks": 0, "bad": []}
            def sample(tag=""):
                fly = p.flyout()
                if fly is None or not fly.isVisible() or not p.isVisible() \
                        or p.flyout_side == "over" or p.dismissing():
                    return
                stats["checks"] += 1
                (pt, pb), (ft, fb) = bounds(p), bounds(fly)
                if ft < pt or fb > pb:
                    stats["bad"].append([tag, pt, pb, ft, fb])
            t = QTimer(); t.timeout.connect(sample); t.start(0)
            def pump(ms, tag=""):
                e = QElapsedTimer(); e.start()
                while e.elapsed() < ms:
                    app.processEvents(); sample(tag)
            def row(i):
                rs = [r for r in p.row_widgets() if isinstance(r, AgendaRow)]
                return rs[min(i, len(rs) - 1)]
            p.show_near_tray(tray); pump(500)
            for mid in (40, 400):
                p.select_row(row(0)); pump(mid, "open")
                p.select_row(row(1)); pump(mid, "switch")
                p.select_row(row(0)); pump(mid, "switch back")
                p.set_account_status("a", "offline"); pump(mid, "status on")
                p.set_account_status("a", "ok"); pump(mid, "status off")
                p.show_notice("A notice"); pump(mid, "notice")
                p.show_notice("Another, longer notice that wraps onto a second line");
                pump(mid, "notice replaced")
                p.show_notice(""); pump(mid, "notice cleared")
                p.toggle_chat(); pump(mid, "chat open")
                p.set_account_status("a", "error"); pump(mid, "status in chat")
                p.set_account_status("a", "ok"); p.toggle_chat(); pump(mid, "chat closed")
                p.refresh(); pump(mid, "refresh")
                p.close_flyout(restore_focus=False); pump(400, "close")
            fly = p.flyout()
            p.select_row(row(0)); pump(600)
            stats["final"] = [list(bounds(p)), list(bounds(fly)), fly.height(), p.height()]
            p.hide(); pump(200); t.stop()
            stats["bad"] = stats["bad"][:5] + [len(stats["bad"])]
            out[f"{name}-{side}"] = stats
            p.deleteLater(); pump(100)
    (tmp / "out.json").write_text(json.dumps(out))
    print("survived", flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_panel_never_taller_than_the_popup(tmp_path):
    import json
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop("SMARTCAL_SLIDE_MODE", None)
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    out = json.loads((tmp_path / "out.json").read_text())
    assert len(out) == 4
    for key, stats in out.items():
        assert stats["checks"] > 1000, (key, stats["checks"])
        assert stats["bad"] == [0], (key, stats["bad"])
        (pt, pb), (ft, fb), fh, ph = stats["final"]
        assert ft == pt and fb <= pb and fh <= ph, (key, stats["final"])
