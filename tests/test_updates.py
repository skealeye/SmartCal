from smartcal import updates
from smartcal.settings import Settings
from smartcal.ui.tray import TrayController
from tests.test_tray import FakeWorker, build


def test_version_parsing_and_comparison():
    assert updates.parse_version("v0.12.3") == (0, 12, 3)
    assert updates.parse_version("1.0") == (1, 0)
    assert updates.parse_version("beta") is None and updates.parse_version(None) is None
    assert updates.is_newer("0.13.0", "0.12.3") and updates.is_newer("v0.12.10", "0.12.9")
    assert not updates.is_newer("0.12.3", "0.12.3") and not updates.is_newer("0.12.2", "0.12.3")
    assert not updates.is_newer("garbage", "0.12.3")


def test_check_returns_only_newer():
    assert updates.check(lambda: "0.14.0", "0.13.0") == "0.14.0"
    assert updates.check(lambda: "0.13.0", "0.13.0") is None
    assert updates.check(lambda: None, "0.13.0") is None


def test_fetch_latest_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert updates.fetch_latest() is None


def test_settings_roundtrip(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.check_updates is True and s.notified_update == ""
    s.set_check_updates(False)
    s.set_notified_update("0.14.0")
    s2 = Settings(tmp_path / "s.json")
    assert s2.check_updates is False and s2.notified_update == "0.14.0"


def test_tray_shows_update_once_and_can_be_switched_off(qtbot, tmp_path):
    tray, _popup = build(qtbot, tmp_path, update_check_fn=lambda: "9.9.9")
    messages = []
    tray.showMessage = lambda *a, **k: messages.append(a)
    assert not tray._update_action.isVisible()
    tray._show_update("9.9.9")
    assert tray._update_action.isVisible() and "9.9.9" in tray._update_action.text()
    tray._show_update("9.9.9")
    assert len(messages) == 1            # the balloon appears once per version
    tray.set_check_updates(False)
    assert tray.settings.check_updates is False and not tray._update_action.isVisible()


def test_tray_without_check_fn_never_checks(qtbot, tmp_path):
    tray, _popup = build(qtbot, tmp_path)
    tray.check_for_update()              # no update_check_fn: a no-op, no thread, no network
    assert not tray._update_action.isVisible()
