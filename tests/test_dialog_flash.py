"""v0.12.0: Settings and Reminders (opened from the tray menu) never flash white.

Recorded on v0.11.3 (real display, DXGI capture of every composed frame + a WinEvent
monitor): Settings was shown opaque before Qt painted it, so for 2-4 composed frames
(35-65 ms after the click) the whole dialog was the system's white erase, 8 px off its
spot; Reminders flashed the same way, and kept growing for four frames after it was
shown (its rows were rebuilt in showEvent, after it was placed), down under the taskbar.

Offscreen: the order of things on open (placed with the real frame, framed and
transparent before show(), revealed after the first paint, at once with animations off),
no stray top-level window, the Reminders dialog staying inside the work area. Real
display (opt in, SMARTCAL_TESTS_REAL_DISPLAY=1; DXGI capture with dxcam, or its folder in
SMARTCAL_TESTS_PYPATH): every composed frame of the dialog area, the screen center and
the primary screen's top-left while both dialogs open, reopen and switch tabs, in the
Windows color scheme and with Qt started in the Light scheme."""
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from smartcal.db import Cache
from smartcal.models import CalendarInfo
from smartcal.reminders import Reminders
from smartcal.settings import Settings
from smartcal.ui import dialogs, motion, placement, winframe
from smartcal.ui.reminders_dialog import RemindersDialog
from smartcal.ui.settings_dialog import SettingsDialog
from test_window_flash import MONITOR, OpenOrder

TRAY = QRect(700, 590, 20, 20)


def make_settings(qtbot, tmp_path):
    cache = Cache(tmp_path / "c.db")
    cache.upsert_calendars("a@x.com", [CalendarInfo("p", "Main", "#111", True),
                                       CalendarInfo("w", "Work", "#222", False)])
    s = Settings(tmp_path / "s.json")
    s.hidden_events = {"e:p:x": {"title": "Lunch", "scope": "event", "when": "2026-11-09"}}
    dlg = SettingsDialog(s, cache, lambda: ["a@x.com"], lambda: None, lambda a: None)
    qtbot.addWidget(dlg)
    return dlg


def make_reminders(qtbot, tmp_path, n=2):
    r = Reminders(tmp_path / "r.json")
    now = datetime.now().astimezone()
    for k in range(n):
        r.add(f"Reminder {k}", now + timedelta(hours=k + 1))
    dlg = RemindersDialog(r)
    qtbot.addWidget(dlg)
    return dlg, r


@pytest.fixture
def native(monkeypatch):
    """Offscreen, but take the real-display code paths; record frame calls."""
    framed = []
    monkeypatch.setattr(winframe, "native_windows", lambda: True)
    monkeypatch.setattr(winframe, "apply_dwm_frame",
                        lambda w: framed.append((type(w).__name__, w.isVisible())) or True)
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    return framed


@pytest.mark.parametrize("which", ["settings", "reminders"])
def test_placed_framed_and_transparent_before_show(qtbot, tmp_path, native, monkeypatch,
                                                  which):
    placed = []
    place = placement.place_near_tray

    def spy(widget, tray):
        place(widget, tray)
        if not widget.isVisible():
            placed.append(widget.geometry())
    monkeypatch.setattr(placement, "place_near_tray", spy)
    for k in range(2):                                  # first open, then a new one
        dlg = make_settings(qtbot, tmp_path / str(k)) if which == "settings" \
            else make_reminders(qtbot, tmp_path / str(k))[0]
        native.clear()
        order = OpenOrder(dlg, native)
        dlg.open_near_tray(TRAY)
        opacity, geom, framed = order.at_show
        assert opacity == 0.0                           # transparent until painted
        assert geom == placed[-1]                       # shown where it was placed
        assert (type(dlg).__name__, False) in framed    # corners set while still hidden
        assert all(op == 0.0 for op, _g in order.moves_after_show)
        assert dlg.content_build() is not None          # its content builds in
        qtbot.waitUntil(lambda: not dlg.revealing() and not dlg.building(), timeout=3000)
        assert dlg.windowOpacity() == 1.0
        dlg.close()
        assert dlg.windowOpacity() == 1.0 and not dlg.revealing()


@pytest.mark.parametrize("which", ["settings", "reminders"])
def test_animations_off_still_hidden_until_painted_then_at_once(qtbot, tmp_path, native,
                                                               monkeypatch, which):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    dlg = make_settings(qtbot, tmp_path) if which == "settings" \
        else make_reminders(qtbot, tmp_path)[0]
    order = OpenOrder(dlg, native)
    dlg.open_near_tray(TRAY)
    assert order.at_show[0] == 0.0 and dlg.content_build() is None   # no build
    qtbot.waitUntil(lambda: not dlg.revealing(), timeout=1000)
    assert dlg.windowOpacity() == 1.0 and dlg._reveal_anim is None   # no fade
    dlg.close()


@pytest.mark.parametrize("animated", [True, False])
def test_shown_cloaked_then_uncloaked_once_painted(qtbot, tmp_path, native, monkeypatch,
                                                  animated):
    """DWM drew a captioned window's frame outline once at the primary screen's top-left
    as it was shown (any toolkit): every window of ours is shown cloaked and uncloaked
    only once painted (the dialogs, the Day/Week/Month window, its details dialog)."""
    from test_window import NOW, ev, make
    monkeypatch.setattr(motion, "animations_enabled", lambda: animated)
    calls = []
    monkeypatch.setattr(winframe, "set_cloak", lambda w, on: calls.append(
        (type(w).__name__, on, w.isVisible())) or True)
    dlg = make_settings(qtbot, tmp_path)
    rdlg, _r = make_reminders(qtbot, tmp_path / "r")
    w = make(qtbot, tmp_path / "w", [ev("1", "Standup", NOW, NOW + timedelta(minutes=30))],
             "week")
    w.hide()
    calls.clear()
    opens = [(dlg, lambda: dlg.open_near_tray(TRAY)),
             (rdlg, lambda: rdlg.open_near_tray(TRAY)),
             (w, lambda: w.show_view("week"))]
    for widget, do in opens:
        calls.clear()
        do()
        name = type(widget).__name__
        assert calls[0] == (name, True, False)          # cloaked before it was shown
        qtbot.waitUntil(lambda: not widget.revealing(), timeout=3000)
        assert calls[1:] == [(name, False, True)]       # uncloaked once, after painting
    calls.clear()
    w.show_details(w.time_view.tiles()[0].event)
    assert calls[0] == ("EventDetails", True, False)
    qtbot.waitUntil(lambda: not w.details.revealing(), timeout=3000)
    assert calls[1:] == [("EventDetails", False, True)]
    calls.clear()
    dlg.close()
    assert calls == []                                  # nothing left cloaked


@pytest.mark.skipif(os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") == "1",
                    reason="about the offscreen platform")
def test_offscreen_platform_opens_opaque(qtbot, tmp_path):
    dlg = make_settings(qtbot, tmp_path)
    dlg.open_near_tray(TRAY)
    assert dlg.isVisible() and dlg.windowOpacity() == 1.0 and not dlg.revealing()
    dlg.open_near_tray(TRAY)                            # already open: raised, not moved
    dlg.close()


def test_native_event_always_answers_a_pair(qtbot, tmp_path):
    dlg, _r = make_reminders(qtbot, tmp_path)
    for args in ((b"windows_generic_MSG", 0), (b"other", 1234), (None, None)):
        out = dlg.nativeEvent(*args)
        assert isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], int)


def test_reminders_stays_inside_work_area_when_rows_change(qtbot, tmp_path):
    dlg, r = make_reminders(qtbot, tmp_path, n=1)
    area = placement.area_for(TRAY)
    dlg.open_near_tray(TRAY)
    qtbot.waitExposed(dlg)
    now = datetime.now().astimezone()
    for k in range(6):
        r.add(f"More {k}", now + timedelta(minutes=30 + k))
    dlg.refresh()
    frame = QRect(dlg.pos(), dlg.size() + placement._frame_extra(dlg))
    assert area.contains(frame), (frame, area)
    assert len(dlg.rows()) == 7
    before = [w for w in dlg.rows()]
    dlg.refresh()                                       # nothing changed: no rebuild
    assert dlg.rows() == before
    dlg.close()


def test_buttons_shrink_when_pressed(qtbot, tmp_path):
    dlg = make_settings(qtbot, tmp_path)
    buttons = dlg.findChildren(QPushButton)
    assert buttons and all(b.findChild(dialogs._PressScale) is not None for b in buttons)
    rdlg, _r = make_reminders(qtbot, tmp_path / "r")
    assert all(b.findChild(dialogs._PressScale) is not None
               for b in rdlg.findChildren(QPushButton))


def test_no_stray_windows_opening_both_dialogs(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QTabWidget
    from test_no_stray_windows import TopLevelShows
    from test_meeting_actions_ui import settle_qt
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    settle_qt()
    rec = TopLevelShows()
    QApplication.instance().installEventFilter(rec)
    try:
        for k in range(2):
            dlg = make_settings(qtbot, tmp_path / f"s{k}")
            dlg.open_near_tray(TRAY)
            tabs = dlg.findChild(QTabWidget)
            for t in range(tabs.count()):
                tabs.setCurrentIndex(t)
                qtbot.wait(20)
            dlg.default_lead.setValue(20)
            dlg._rows["w"][0].setChecked(True)
            qtbot.waitUntil(lambda: not dlg.building(), timeout=3000)
            dlg.close()
            rdlg, r = make_reminders(qtbot, tmp_path / f"r{k}")
            rdlg.open_near_tray(TRAY)
            r.add("new", datetime.now().astimezone() + timedelta(hours=3))
            rdlg.refresh()
            rdlg.rows()[0].button.click()
            qtbot.waitUntil(lambda: not rdlg.building(), timeout=3000)
            rdlg.close()
    finally:
        QApplication.instance().removeEventFilter(rec)
    assert rec.strays((SettingsDialog, RemindersDialog)) == []


def test_force_dark_scheme():
    """run.py asks Qt for the dark color scheme (offscreen Qt ignores it: a fake app)."""
    from smartcal.ui import theme
    asked = []

    class Hints:
        def setColorScheme(self, scheme):
            asked.append(scheme)

    class App:
        def styleHints(self):
            return Hints()
    assert theme.force_dark_scheme(App()) is True and asked == [Qt.ColorScheme.Dark]
    assert theme.force_dark_scheme(object()) is False   # never raises


# -- the real Windows display (opt in: it opens real windows) ---------------------------
# grab_dx.py output secs outdir spec...: one DXGI duplication per output (dxcam), every
# composed frame cut into regions (spec name:x:y:w:h:mode:step, x/y relative to the
# output). "frames": the frames (downsampled by step); "diff": per frame, pixels
# differing from the first frame, and new near-white pixels.
GRAB_MULTI = textwrap.dedent(r"""
    import ctypes, os, sys, time
    extra = os.environ.get("SMARTCAL_TESTS_PYPATH")
    if extra:
        sys.path.append(extra)
    import numpy as np
    import dxcam
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    idx = int(sys.argv[1]); secs = float(sys.argv[2]); outdir = sys.argv[3]
    specs = []
    for sp in sys.argv[4:]:
        name, x, y, w, h, mode, step = sp.split(":")
        specs.append(dict(name=name, x=int(x), y=int(y), w=int(w), h=int(h), mode=mode,
                          step=int(step), times=[], frames=[], stats=[], base=None, bw=None))
    cam = None
    for attempt in range(40):
        try:
            cam = dxcam.create(output_idx=idx, output_color="BGR"); break
        except Exception:
            time.sleep(0.25)
    print("ready", flush=True)
    if cam is None:
        sys.exit(3)
    x0 = min(s["x"] for s in specs); y0 = min(s["y"] for s in specs)
    x1 = max(s["x"] + s["w"] for s in specs); y1 = max(s["y"] + s["h"] for s in specs)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < secs:
        fr = cam.grab(region=(x0, y0, x1, y1))
        if fr is None:
            time.sleep(0.0003); continue
        t = time.perf_counter()
        for s in specs:
            c = fr[s["y"] - y0:s["y"] - y0 + s["h"], s["x"] - x0:s["x"] - x0 + s["w"]]
            s["times"].append(t)
            if s["mode"] == "frames":
                s["frames"].append(c[::s["step"], ::s["step"]].copy()); continue
            f = c.astype(np.int16)
            if s["base"] is None:
                s["base"] = f.copy(); s["bw"] = c.min(axis=2) > 200
            s["stats"].append([int((np.abs(f - s["base"]).max(axis=2) > 40).sum()),
                               int(((c.min(axis=2) > 200) & ~s["bw"]).sum())])
    for s in specs:
        out = os.path.join(outdir, s["name"] + ".npz")
        if s["mode"] == "frames":
            np.savez(out, times=np.array(s["times"]), frames=np.stack(s["frames"]))
        else:
            np.savez(out, times=np.array(s["times"]),
                     stats=np.array(s["stats"]).reshape(-1, 2))
""")

RUN = textwrap.dedent(r"""
    import faulthandler, json, os, subprocess, sys, time
    faulthandler.enable()
    from datetime import datetime, timedelta
    from pathlib import Path
    tmp = Path(sys.argv[1]); sys.path[:0] = [sys.argv[2], sys.argv[3]]
    scheme, animated = sys.argv[4], sys.argv[5] == "1"
    from PyQt6.QtCore import QElapsedTimer, QEvent, QRect, Qt
    from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from smartcal.ui import motion, theme
    if scheme == "light":           # Windows set to Light: Qt starts in it ...
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    theme.force_dark_scheme(app)    # ... and run.py makes it dark
    motion.animations_enabled = (lambda: True) if animated else (lambda: False)
    from smartcal.db import Cache
    from smartcal.models import CalendarInfo
    from smartcal.reminders import Reminders
    from smartcal.settings import Settings
    from smartcal.ui import placement
    from smartcal.ui.reminders_dialog import RemindersDialog
    from smartcal.ui.settings_dialog import SettingsDialog
    cache = Cache(tmp / "c.db")
    cache.upsert_calendars("me@gmail.com", [
        CalendarInfo("p1", "me@gmail.com", "#4285f4", True),
        CalendarInfo("h1", "Holidays in United States", "#0b8043", False),
        CalendarInfo("f1", "Family", "#e67c73", False)])
    cache.upsert_calendars("me@work.com", [CalendarInfo("p2", "me@work.com", "#7986cb", True),
                                           CalendarInfo("t2", "Team", "#f6bf26", False)])
    settings = Settings(tmp / "s.json")
    settings.hidden_events = {"e:p1:x": {"title": "Lunch", "scope": "event",
                                         "when": "2026-11-09"}}
    accounts = ["me@gmail.com", "me@work.com"]
    rem = Reminders(tmp / "r.json")
    now = datetime.now().astimezone()
    for k, text in enumerate(["Call the dentist", "Leave for the airport", "Water the plants"]):
        rem.add(text, now + timedelta(minutes=20 + 60 * k))
    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents(); app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    scr = app.primaryScreen(); sg = scr.geometry(); area = scr.availableGeometry()
    tray = QRect(area.right() - 300, area.bottom() + 5, 24, 40)
    def new_settings():
        return SettingsDialog(settings, cache, lambda: list(accounts), lambda: None,
                              lambda a: None)
    # where they end up (built and placed, never shown: the first open is still first)
    final = []
    for make in (new_settings, lambda: RemindersDialog(rem)):
        d = make(); placement.place_near_tray(d, tray)
        g = d.geometry(); final.append([g.x() - 8, g.y() - 40, g.width() + 16, g.height() + 48])
        d.deleteLater()
    pump(100)
    x0 = min(r[0] for r in final) - 40; y0 = min(r[1] for r in final) - 40
    x1 = min(max(r[0] + r[2] for r in final) + 40, sg.right() + 1)
    y1 = min(max(r[1] + r[3] for r in final) + 40, sg.bottom() + 1)
    region = [x0, y0, x1 - x0, y1 - y0]
    back = QWidget()                # a black backdrop: anything white there is ours
    back.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    back.setStyleSheet("background:#000;"); back.setGeometry(QRect(*region))
    back.show(); pump(500)
    c = sg.center()
    center = [c.x() - 300, c.y() - 200, 600, 400]
    corner = [sg.x(), sg.y(), 600, 400]
    secs = 15.0
    def spec(name, r, mode, step):
        return f"{name}:{r[0] - sg.x()}:{r[1] - sg.y()}:{r[2]}:{r[3]}:{mode}:{step}"
    procs = [subprocess.Popen([sys.executable, str(tmp / "monitor.py"), str(os.getpid()),
                               str(secs + 0.5), str(tmp / "mon.json")],
                              stdout=subprocess.PIPE, text=True),
             subprocess.Popen([sys.executable, str(tmp / "grab_multi.py"), "0", str(secs),
                               str(tmp), spec("dialog", region, "frames", 2),
                               spec("center", center, "diff", 1),
                               spec("corner", corner, "diff", 1)],
                              stdout=subprocess.PIPE, text=True)]
    for p in procs:
        p.stdout.readline()                             # "ready"
    marks = []
    def mark(n):
        marks.append((n, time.perf_counter()))
    pump(900)
    mark("baseline_end")
    rects = {}
    def rect(d):
        g = d.frameGeometry(); return [g.x(), g.y(), g.width(), g.height()]
    for rep in range(2):
        d = new_settings(); d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        mark("settings_open"); d.open_near_tray(tray); pump(1200)
        rects["settings"] = rect(d)
        if rep == 0:
            tabs = d.findChild(QTabWidget)
            for t in list(range(1, tabs.count())) + [0]:
                mark("tab%d" % t); tabs.setCurrentIndex(t); pump(450)
        mark("close"); d.close(); pump(500)
    for rep in range(2):
        d = RemindersDialog(rem); d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        mark("reminders_open"); d.open_near_tray(tray); pump(1200)
        rects["reminders"] = rect(d)
        mark("close"); d.close(); pump(500)
    for p in procs:
        p.wait()
    info = {"marks": marks, "rects": rects, "region": region, "center": center,
            "corner": corner, "area": [area.x(), area.y(), area.width(), area.height()]}
    (tmp / "info.json").write_text(json.dumps(info))
    print("survived", flush=True)
""")


def _inside(x, y, rects, pad):
    return any(r[0] - pad <= x < r[0] + r[2] + pad and r[1] - pad <= y < r[1] + r[3] + pad
               for r in rects)


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
@pytest.mark.parametrize("scheme,animated", [("system", True), ("light", True),
                                             ("system", False)])
def test_real_display_settings_and_reminders_never_flash_white(tmp_path, scheme, animated):
    import importlib.util
    import json
    import numpy as np
    if importlib.util.find_spec("dxcam") is None and not os.environ.get("SMARTCAL_TESTS_PYPATH"):
        pytest.skip("needs dxcam (pip install dxcam, or its folder in SMARTCAL_TESTS_PYPATH): "
                    "a white frame lasts one composed frame")
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    env.pop(motion.ENV_LEVEL, None)
    (tmp_path / "monitor.py").write_text(MONITOR, encoding="utf-8")
    (tmp_path / "grab_multi.py").write_text(GRAB_MULTI, encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent),
                        str(tests), scheme, "1" if animated else "0"],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=240)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stderr[-3000:])
    info = json.loads((tmp_path / "info.json").read_text())
    rects, area = info["rects"], info["area"]
    marks = info["marks"]
    opens = [t for n, t in marks if n.endswith("_open")]
    assert len(opens) == 4

    # 1. Native truth: shown fully transparent, and only where it ends up, inside the
    # work area (Windows' rect includes the invisible resize borders: a few px wider).
    log = json.loads((tmp_path / "mon.json").read_text())
    ours = [e for e in log if e["cls"].startswith("Qt") and "titlebar" not in e["cls"]]
    assert any(e["ev"] == 0x8002 for e in ours), "the monitor saw nothing"
    for e in ours:
        if e["ev"] == 0x8002:                           # EVENT_OBJECT_SHOW
            assert e["alpha"] == 0, e
        if e["vis"]:
            x0, y0, x1, y1 = e["rect"]
            assert _inside(x0, y0, list(rects.values()), 10) and \
                _inside(x1 - 1, y1 - 1, list(rects.values()), 10), e
    for name, (x, y, w, h) in rects.items():
        assert area[1] <= y and y + h <= area[1] + area[3], (name, rects[name], area)

    # 2. The dialog area (on a black backdrop): no frame whiter than the dialog itself.
    z = np.load(tmp_path / "dialog.npz")
    frames, times = z["frames"], z["times"]
    assert len(frames) > 30, len(frames)
    white = (frames.min(axis=3) > 200).reshape(len(frames), -1).sum(axis=1)
    settled = []                                        # each dialog, 1 s after it opened
    for t in opens:
        k = int(np.searchsorted(times, t + 1.0))
        settled.append(int(white[min(k, len(white) - 1)]))
    limit = max(settled) * 2 + 600                      # (light text only: a few hundred)
    bad = np.nonzero(white > limit)[0]
    assert len(bad) == 0, ("white frames", [(int(k), int(white[k]), round(
        (times[k] - max([t for t in opens if t <= times[k]] or [times[0]])) * 1000))
        for k in bad[:8]], "limit", limit)
    assert max(settled) < 0.02 * frames.shape[1] * frames.shape[2], settled   # dark

    # 3. Nothing appears at the primary screen's center or its top-left corner.
    for name in ("center", "corner"):
        z = np.load(tmp_path / (name + ".npz"))
        st, tt = z["stats"], z["times"]
        base_end = next(t for n, t in marks if n == "baseline_end")
        if (st[tt < base_end, 0] > 300).any():          # something else animating there
            continue
        hits = np.nonzero(st[:, 0] > 300)[0]
        assert len(hits) == 0, (name, [st[k].tolist() for k in hits[:5]])
