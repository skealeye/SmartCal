"""v0.8.5: the v0.8.4 popup crashed the app as it opened (QtCore.pyd+0x38130, 0xc0000005
then 0xc000041d). Its nativeEvent override returned super().nativeEvent(), which with
PyQt6 6.10 is (False, None); sip then copies the result from a NULL pointer inside the
window procedure. Offscreen tests never route native messages, so this file checks the
contract (always (bool, int)) everywhere and, with SMARTCAL_TESTS_REAL_DISPLAY=1, stresses
the real Windows platform in a child process."""
import ctypes
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6 import sip


from smartcal.ui import nativebg, theme
from test_meeting_actions_ui import click, rows
from test_popup_animations import MANY, settled_popup

GENERIC = b"windows_generic_MSG"


def _pair_ok(result) -> bool:
    return (isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool)
            and isinstance(result[1], int) and not isinstance(result[1], bool))


def _msg(message: int):
    from ctypes import wintypes
    m = wintypes.MSG()
    m.message = message
    m.wParam = 0                        # no DC: the erase is not answered
    m.hWnd = None
    return m


@pytest.mark.parametrize("event_type, message", [
    (GENERIC, "mousemove"), (GENERIC, "erase_no_dc"), (GENERIC, None),
    (b"other", "mousemove"), (GENERIC, "bogus"),
])
def test_native_event_always_returns_bool_int(qtbot, tmp_path, event_type, message):
    keep = []
    if message in ("mousemove", "erase_no_dc") and sys.platform == "win32":
        m = _msg(0x0200 if message == "mousemove" else nativebg.WM_ERASEBKGND)
        keep.append(m)
        ptr = sip.voidptr(ctypes.addressof(m))
    elif message == "bogus":
        ptr = object()                  # not a pointer at all: must not raise
    else:
        ptr = None
    p = settled_popup(qtbot, tmp_path, MANY)
    click(qtbot, rows(p)[0])
    for w in (p, p.flyout()):
        r = w.nativeEvent(event_type, ptr)
        assert _pair_ok(r), r
        assert r == nativebg.NOT_HANDLED


def test_native_event_swallows_errors(monkeypatch):
    def boom(*_a):
        raise RuntimeError("x")
    monkeypatch.setattr(nativebg, "handle_erase", boom)
    assert nativebg.native_event(GENERIC, 1, theme.BG) == nativebg.NOT_HANDLED


def test_native_event_overrides_never_call_super():
    root = Path(__file__).resolve().parents[1] / "smartcal"
    for path in root.rglob("*.py"):
        assert "super().nativeEvent" not in path.read_text(encoding="utf-8"), path


def test_win32_prototypes_do_not_touch_shared_windll():
    if sys.platform != "win32":
        pytest.skip("Win32")
    before = ctypes.windll.user32.FillRect.argtypes
    nativebg._win32()
    assert ctypes.windll.user32.FillRect.argtypes is before
    user32, gdi32 = nativebg._win32()
    assert gdi32.CreateSolidBrush.argtypes is not None


# -- the real Windows platform (opt in: it opens real windows) ---------------------------
STRESS = textwrap.dedent(r"""
    import faulthandler, os, sys
    faulthandler.enable()
    from pathlib import Path
    tmp = Path(sys.argv[1])
    sys.path[:0] = [sys.argv[2], sys.argv[3]]
    from PyQt6.QtCore import QElapsedTimer, QRect
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    from smartcal.ui import motion
    motion.animations_enabled = lambda: True
    from smartcal.ui.popup import AgendaPopup
    from test_meeting_actions_ui import NOW, store
    from test_popup_animations import MANY
    cache, settings = store(tmp, MANY)
    p = AgendaPopup(cache, settings)
    p.now = lambda: NOW
    p.refresh()
    scr = app.primaryScreen().availableGeometry()
    tray = QRect(scr.right() - 40, scr.bottom() + 5, 20, 20)

    def pump(ms):
        t = QElapsedTimer(); t.start()
        while t.elapsed() < ms:
            app.processEvents()

    def agenda_rows():
        from smartcal.ui.popup import AgendaRow
        return [r for r in p.row_widgets() if isinstance(r, AgendaRow)]

    for i in range(6):                  # rapid open/close, some mid-animation
        p.show_near_tray(tray); pump(20 if i % 2 else 300); p.hide(); pump(30)
    p.show_near_tray(tray); pump(300)
    rs = agenda_rows()
    p.toggle_row(rs[0]); pump(60)       # details panel, switched, closed mid-slide
    p.toggle_row(rs[1]); pump(60)
    p.toggle_row(rs[1]); pump(40)
    p.toggle_row(rs[0]); pump(250)
    p.hide(); pump(50)                  # closed with the panel open
    p.show_near_tray(tray); pump(300)
    rs = agenda_rows()
    p.toggle_row(rs[0]); pump(80)
    p.hide_meeting(rs[0].event, "event"); pump(30)
    p.hide(); pump(30)
    p.show_near_tray(tray); pump(100)
    p.toggle_chat(); pump(100); p.toggle_chat(); pump(30)
    p.hide(); pump(100)
    print("survived", flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1",
                    reason="opens real windows: SMARTCAL_TESTS_REAL_DISPLAY=1")
def test_real_windows_platform_popup_stress(tmp_path):
    tests = Path(__file__).resolve().parent
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SMARTCAL_NO_SYSTEM_WRITES="1",
               SMARTCAL_DATA_DIR=str(tmp_path / "data"))
    env.pop("SMARTCAL_NO_ANIMATIONS", None)
    script = tmp_path / "stress.py"
    script.write_text(STRESS, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(tmp_path), str(tests.parent), str(tests)],
                       env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "survived" in r.stdout, (r.returncode, r.stdout, r.stderr[-3000:])


# -- crash visibility ------------------------------------------------------------------
def test_native_crash_leaves_a_python_stack_in_crash_log(tmp_path):
    log = tmp_path / "crash.log"
    root = Path(__file__).resolve().parents[1]
    code = ("import ctypes, sys; sys.path.insert(0, sys.argv[2]);"
            "from smartcal.logsetup import enable_crash_log;"
            "assert enable_crash_log(sys.argv[1], '9.9.9');"
            "ctypes.string_at(0)")
    r = subprocess.run([sys.executable, "-c", code, str(log), str(root)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "--- SmartCal 9.9.9 started" in text
    assert "access violation" in text.lower() or "segmentation fault" in text.lower(), text


def test_crash_log_starts_over_past_its_limit(tmp_path):
    import faulthandler
    from smartcal import logsetup
    log = tmp_path / "crash.log"
    log.write_text("x" * (logsetup.CRASH_LOG_MAX_BYTES + 10), encoding="utf-8")
    try:
        assert logsetup.enable_crash_log(log, "1.0")
        assert log.stat().st_size < 200
        assert (tmp_path / "crash.log.1").exists()
    finally:
        faulthandler.enable(file=sys.__stderr__, all_threads=True)
        logsetup._crash_file.close()
        logsetup._crash_file = None


def test_qt_messages_go_to_the_log(qapp, tmp_path):
    import logging
    from PyQt6.QtCore import qInstallMessageHandler, qWarning
    from smartcal.logsetup import install_qt_message_handler, setup_logging
    path = tmp_path / "smartcal.log"
    handler = setup_logging(path)
    try:
        install_qt_message_handler()
        qWarning("qt-warning-probe")
        handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "WARNING smartcal.qt: qt-warning-probe" in text
    finally:
        qInstallMessageHandler(None)
        logging.getLogger().removeHandler(handler)
        handler.close()
