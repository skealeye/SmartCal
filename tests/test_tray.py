import threading

from PyQt6.QtCore import QEvent, QPoint, QRect
from PyQt6.QtWidgets import QApplication

from smartcal.db import Cache
from smartcal.settings import Settings
from smartcal.ui.popup import AgendaPopup
from smartcal.ui.tray import TrayController

TRAY = QRect(100, 100, 20, 20)


class FakeWorker:
    def __init__(self, fail=False):
        self.syncs, self.fail = 0, fail

    def sync_now(self):
        self.syncs += 1
        if self.fail:
            raise RuntimeError("boom")

    def stop(self):
        pass


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def build(qtbot, tmp_path, worker=None, **kw):
    cache = Cache(tmp_path / "c.db")
    settings = Settings(tmp_path / "s.json")
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    popup.clock = Clock()
    popup.cursor_pos = lambda: QPoint(105, 105)
    tray = TrayController(popup, worker or FakeWorker(), cache, settings, **kw)
    tray._tray_rect = lambda: TRAY
    return tray, popup


def show_then_deactivate(qtbot, popup):
    popup.show()
    qtbot.waitExposed(popup)
    QApplication.sendEvent(popup, QEvent(QEvent.Type.WindowDeactivate))
    assert not popup.isVisible()


def test_tray_click_after_deactivate_is_ignored_when_cursor_on_tray(qtbot, tmp_path):
    tray, popup = build(qtbot, tmp_path)
    show_then_deactivate(qtbot, popup)
    popup.clock.t += 0.2
    tray.toggle_popup()
    assert not popup.isVisible()


def test_tray_click_with_cursor_elsewhere_toggles_even_if_recent(qtbot, tmp_path):
    tray, popup = build(qtbot, tmp_path)
    popup.cursor_pos = lambda: QPoint(900, 900)
    show_then_deactivate(qtbot, popup)
    popup.clock.t += 0.2
    tray.toggle_popup()
    assert popup.isVisible()


def test_tray_click_toggles_when_popup_hidden_long_ago(qtbot, tmp_path):
    tray, popup = build(qtbot, tmp_path)
    show_then_deactivate(qtbot, popup)
    popup.clock.t += 5
    tray.toggle_popup()
    assert popup.isVisible()
    tray.toggle_popup()
    assert not popup.isVisible()


def test_empty_geometry_uses_time_window_alone(qtbot, tmp_path):
    tray, popup = build(qtbot, tmp_path)
    tray._tray_rect = lambda: QRect()
    popup.cursor_pos = lambda: QPoint(900, 900)
    show_then_deactivate(qtbot, popup)
    popup.clock.t += 0.2
    tray.toggle_popup()
    assert not popup.isVisible()


def test_add_account_reenables_menu_item_after_failure(qtbot, tmp_path):
    def failing(label):
        raise RuntimeError("")

    messages = []
    worker = FakeWorker(fail=True)
    tray, _ = build(qtbot, tmp_path, worker, add_account_fn=failing)
    tray.showMessage = lambda *a, **k: messages.append(a[1])
    tray.add_account()
    assert tray._add_action.text() == "Cancel Google sign-in" and tray.signin_pending()
    qtbot.waitUntil(lambda: not tray.signin_pending(), timeout=3000)
    assert tray._add_action.text() == "Add Google account…" and tray._add_action.isEnabled()
    assert "RuntimeError" in messages[0]
    assert worker.syncs == 0


def test_add_account_reenables_even_if_sync_now_raises(qtbot, tmp_path):
    worker = FakeWorker(fail=True)
    dropped = []
    tray, _ = build(qtbot, tmp_path, worker, add_account_fn=lambda label: "a@x.com",
                    on_accounts_changed=lambda: dropped.append(1))
    tray.add_account()
    qtbot.waitUntil(lambda: not tray.signin_pending(), timeout=3000)
    assert dropped == [1] and worker.syncs == 1


def test_add_account_double_trigger_starts_one_flow(qtbot, tmp_path):
    gate, calls = threading.Event(), []

    def slow(label):
        calls.append(label)
        gate.wait(5)
        return "a@x.com"

    tray, _ = build(qtbot, tmp_path, add_account_fn=slow)
    tray.add_account()
    tray.add_account()
    qtbot.waitUntil(lambda: len(calls) == 1)
    gate.set()
    qtbot.waitUntil(lambda: not tray.signin_pending(), timeout=3000)
    assert len(calls) == 1


def test_sync_now_action_passes_no_args(qtbot, tmp_path):
    worker = FakeWorker()
    tray, _ = build(qtbot, tmp_path, worker)
    tray._sync_action.trigger()
    assert worker.syncs == 1


class StuckWorker(FakeWorker):
    """A sync thread that never finishes (e.g. blocked in a network call)."""

    def __init__(self):
        super().__init__()
        self.stopped = False

    def stop(self):
        self.stopped = True

    def wait(self, ms):
        return False

    def terminate(self):
        raise AssertionError("terminate() must never be called")


def test_quit_with_stuck_worker_flushes_and_exits_without_terminate(qtbot, tmp_path):
    exits, flushed, quits = [], [], []
    worker = StuckWorker()
    tray, _ = build(qtbot, tmp_path, worker, on_before_exit=lambda: flushed.append(1),
                    exit_fn=exits.append)
    tray._quit_app = lambda: quits.append(1)
    tray.quit()
    assert worker.stopped and flushed == [1] and exits == [0] and quits == []


def test_quit_with_stopping_worker_quits_normally(qtbot, tmp_path):
    exits, quits = [], []

    class Worker(FakeWorker):
        def wait(self, ms):
            return True

    tray, _ = build(qtbot, tmp_path, Worker(), exit_fn=exits.append)
    tray._quit_app = lambda: quits.append(1)
    tray.quit()
    assert exits == [] and quits == [1]


def test_show_popup_shows_and_never_hides(qtbot, tmp_path):
    tray, popup = build(qtbot, tmp_path)
    tray.show_popup()
    assert popup.isVisible()
    tray.show_popup()
    assert popup.isVisible()


def test_settings_menu_action_opens_settings(qtbot, tmp_path):
    opened = []
    cache = Cache(tmp_path / "c.db")
    settings = Settings(tmp_path / "s.json")
    popup = AgendaPopup(cache, settings)
    qtbot.addWidget(popup)
    tray = TrayController(popup, FakeWorker(), cache, settings,
                          on_open_settings=lambda: opened.append(1))
    action = next(a for a in tray.contextMenu().actions() if a.text() == "Settings…")
    action.trigger()
    assert opened == [1]
