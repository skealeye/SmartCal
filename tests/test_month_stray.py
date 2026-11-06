"""v0.11.3: the Month view paths never put an outline (or any window of ours) where it
does not belong, and SMARTCAL_DEBUG_UI=1 records every window of ours (smartcal.ui.
wintrace) so the next stray flash on the owner's screen can be traced.

Offscreen: the diagnostics install only when asked, log top-level shows with the code that
showed them, never raise. Real display (opt in): the Month flows (opened from the popup
straight into Month, Day/Week/Month switches, month steps, hover, tooltips, "+N more",
a day, meeting details, close and reopen in Month) with wintrace's native hook as the
window monitor and DXGI grabs (dxcam, see test_stray_border) of every monitor's
top-left corner."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import QRect, QSettings
from PyQt6.QtWidgets import QApplication, QWidget

from smartcal.ui import uitrace, wintrace


@pytest.fixture
def sink(monkeypatch):
    lines = []
    monkeypatch.setattr(uitrace, "sink", lines)
    yield lines
    wintrace.uninstall()


def test_not_installed_unless_debugging(monkeypatch):
    monkeypatch.setattr(uitrace, "sink", None)
    monkeypatch.setattr(uitrace, "_enabled", False)
    assert wintrace.install(QApplication.instance()) is False
    assert not wintrace._installed


def test_logs_top_level_shows_with_the_code_that_showed_them(qtbot, tmp_path, sink):
    from smartcal.db import Cache
    from smartcal.settings import Settings
    from smartcal.ui.window import CalendarWindow
    from test_window import NOW
    assert wintrace.install(QApplication.instance()) is True
    assert wintrace.install(QApplication.instance()) is False      # once
    w = CalendarWindow(Cache(tmp_path / "c.db"), Settings(tmp_path / "s.json"),
                       QSettings(str(tmp_path / "q.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(w)
    w.now = lambda: NOW
    w.tray_geometry = lambda: QRect(900, 700, 20, 20)
    child = QWidget(w)                          # a child: never logged
    child.show()
    w.show_view("month")
    shows = [f for what, f in sink if what == "win.show"]
    assert [f["cls"] for f in shows] == ["CalendarWindow"]
    assert "show_view" in shows[0]["caller"]
    assert shows[0]["frame"].count(",") == 2 and "x" in shows[0]["frame"]
    w.hide()
    assert any(what == "win.hide" and f["cls"] == "CalendarWindow" for what, f in sink)
    wintrace.uninstall()
    assert not wintrace._installed
    sink.clear()
    w.show_view("week")
    assert not [1 for what, _f in sink if what.startswith("win.")]


def test_never_raises(sink):
    f = wintrace.WindowEvents()
    assert f.eventFilter(object(), object()) is False
    assert wintrace.near_corner(10, -20, [(0, 0)]) and not wintrace.near_corner(100, 0, [(0, 0)])
    assert wintrace.near_corner(-1440 + 3, -165, [(-1440, -165)])
    assert isinstance(wintrace.caller(), str)
    if sys.platform == "win32":
        hook = wintrace.NativeHook()
        hook._callback(None, 0x8002, 0x12345678, 0, 0, 0, 0)   # no such window
        hook._callback(None, 0x8002, None, 0, 0, 0, 0)
        hook.on_event(0x8001, 1, 0)
        hook.stop()


# -- the real Windows display (opt in: it opens real windows) ---------------------------
RUN = textwrap.dedent(r"""
    import faulthandler, json, os, subprocess, sys, time
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, QRect, QSettings, Qt
    from PyQt6.QtGui import QEnterEvent, QHelpEvent
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from smartcal.ui import motion, uitrace, wintrace
    motion.animations_enabled = lambda: True
    uitrace.sink = []
    from smartcal.ui.popup import AgendaPopup
    from smartcal.ui.window import CalendarWindow
    from test_meeting_actions_ui import NOW, ev, store
    from test_popup_animations import MANY
    evs = MANY + [ev("d", "Design review with a long title that will not fit", 18, day=5,
                     description="Walk through it.", meeting_url="https://zoom.us/j/1"),
                  ev("e", "Planning", 10, day=12)]
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
    def rect(r):
        return [r.x(), r.y(), r.width(), r.height()]
    def cells():
        return w.month_view.tiles()
    def hover(x):
        c = QPointF(x.width() / 2, x.height() / 2)
        app.sendEvent(x, QEnterEvent(c, c, QPointF(x.mapToGlobal(c.toPoint()))))
    def tip(x):
        c = QPoint(x.width() // 2, x.height() // 2)
        app.sendEvent(x, QHelpEvent(QEvent.Type.ToolTip, c, x.mapToGlobal(c)))
    # warm-up: the final rects
    p.show_near_tray(tray); pump(900); p.request_view("month"); pump(1300)
    chip = next(ch for c in cells() for ch in c.chips)
    w.show_details(chip.event); pump(800); dlg_r = w.details.frameGeometry()
    w.details.close(); win_r = w.frameGeometry(); popup_r = p.frameGeometry()
    w.close(); pump(500)
    assert wintrace.install(app)                   # the diagnostics are the monitor
    secs = 34.0
    corners = [(k, s.geometry()) for k, s in enumerate(app.screens())]
    procs = []
    for k, g in corners:
        procs.append(subprocess.Popen(
            [sys.executable, str(tmp / "grab_dx.py"), "0", "0",
             str(min(g.width(), win_r.width() + 60)), str(min(g.height(), win_r.height() + 60)),
             str(secs), str(tmp / f"corner{k}.npz"), "[]", str(k)],
            stdout=subprocess.PIPE, text=True))
    for pr in procs:
        pr.stdout.readline()                             # "ready"
    pump(500)
    t0 = time.perf_counter()
    opens = steps = 0
    t = QElapsedTimer(); t.start()
    while t.elapsed() < (secs - 6) * 1000:
        p.refresh(); p.show_near_tray(tray); pump(800)
        p.request_view("month"); opens += 1; pump(1200)
        for v in ("day", "week", "month"):
            w.set_view(v); pump(500)
        w.step(1); pump(500); w.step(-1); pump(500); steps += 2
        for c in cells()[8:14]:
            hover(c); pump(80); app.sendEvent(c, QEvent(QEvent.Type.Leave)); pump(40)
        more = [c.more_label for c in cells() if c.more_label is not None]
        if more:
            hover(more[0]); tip(more[0]); pump(700)
        chip = next(ch for c in cells() for ch in c.chips)
        tip(chip); pump(700)
        w.show_details(chip.event); pump(900); w.details.close(); pump(300)
        if more:
            more[0].clicked.emit(); pump(700); w.set_view("month"); pump(600)
        cells()[12].clicked.emit(cells()[12].day); pump(600); w.set_view("month"); pump(600)
        w.close(); pump(500)
        w.show_view("month"); opens += 1; pump(1200); w.close(); pump(500)
    for pr in procs:
        pr.wait()
    wintrace.uninstall()
    info = {"win": rect(win_r), "dlg": rect(dlg_r), "popup": rect(popup_r),
            "opens": opens, "steps": steps, "t0": t0,
            "corners": [(k, rect(g)) for k, g in corners],
            "trace": [(what, f) for what, f in uitrace.sink
                      if what.startswith(("native.", "win.show", "frame.border"))]}
    (tmp / "info.json").write_text(json.dumps(info, default=str))
    print("survived", flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_display_month_flows_leave_no_stray_outline(tmp_path):
    import importlib.util
    import json
    import numpy as np
    from smartcal.ui import motion
    from test_stray_border import GRAB_DX
    if importlib.util.find_spec("dxcam") is None and not os.environ.get("SMARTCAL_TESTS_PYPATH"):
        pytest.skip("needs dxcam (pip install dxcam, or its folder in SMARTCAL_TESTS_PYPATH): "
                    "the stray edge lasts one composed frame")
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(motion.ENV_LEVEL, None)
    (tmp_path / "grab_dx.py").write_text(GRAB_DX, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=240)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    info = json.loads((tmp_path / "info.json").read_text())
    assert info["opens"] >= 6, info["opens"]
    trace = info["trace"]

    # 1. The diagnostics saw the windows (native hook on the GUI thread) ...
    shows = [f for what, f in trace if what == "native.show"]
    assert len([f for f in shows if f["cls"].endswith("QWindowIcon")]) >= info["opens"], shows[:3]
    # ... and every window of ours shown was where it ends up (tooltips: at the chip).
    allowed = [info["win"], info["dlg"], info["popup"]]
    for f in shows:
        if "ToolTip" in f["cls"]:
            continue
        x, y = (int(v) for v in f["rect"].split(",")[:2])
        assert any(a[0] - 60 <= x <= a[0] + 60 and a[1] - 60 <= y <= a[1] + 60
                   for a in allowed), f
    # every edge color was set on a visible window
    borders = [f for what, f in trace if what == "frame.border"]
    assert borders and all(f["visible"] for f in borders), borders[:3]

    # 2. No accent outline at any monitor's top-left corner, at any composed frame.
    for k, _g in info["corners"]:
        z = np.load(tmp_path / f"corner{k}.npz")
        st = z["stats"]
        edges = np.nonzero((st[:, 1] >= 60) | (st[:, 3] >= 40))[0]
        assert len(edges) == 0, (k, [st[j].tolist() for j in edges[:5]])
