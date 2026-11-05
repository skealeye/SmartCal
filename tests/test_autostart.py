import uuid
import winreg

import pytest

from smartcal import autostart

BASE = r"Software\SmartCalTest"
KEY = BASE + "\\" + uuid.uuid4().hex


@pytest.fixture
def temp_key():
    yield KEY
    for path in (KEY, BASE):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


def test_autostart_roundtrip_on_temp_key(temp_key):
    assert not autostart.is_enabled(temp_key)
    cmd = '"C:\\x\\pythonw.exe" "C:\\x\\run.py"'
    autostart.enable(cmd, key_path=temp_key)
    assert autostart.is_enabled(temp_key)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, temp_key) as k:
        assert winreg.QueryValueEx(k, "SmartCal")[0] == cmd
    autostart.disable(temp_key)
    assert not autostart.is_enabled(temp_key)
    autostart.disable(temp_key)  # idempotent


def test_default_command_points_at_run_py():
    cmd = autostart.default_command()
    assert cmd.startswith('"') and cmd.rstrip('"').endswith("run.py")


def test_no_system_writes_env_skips_registry_and_appid(monkeypatch):
    import winreg
    from unittest import mock
    from smartcal import toast
    monkeypatch.setenv("SMARTCAL_NO_SYSTEM_WRITES", "1")
    with mock.patch.object(winreg, "CreateKeyEx") as create, \
            mock.patch.object(winreg, "OpenKey") as open_key:
        autostart.apply(True)
        autostart.apply(False)
        toast.register_app_id()
    create.assert_not_called()
    open_key.assert_not_called()


def test_data_dir_override_env(monkeypatch, tmp_path):
    from smartcal.config import data_dir
    target = tmp_path / "custom" / "data"
    monkeypatch.setenv("SMARTCAL_DATA_DIR", str(target))
    assert data_dir() == target and target.is_dir()


def test_frozen_default_command_is_quoted_exe(monkeypatch):
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", r"C:\Apps\SmartCal\SmartCal.exe")
    assert autostart.default_command() == r'"C:\Apps\SmartCal\SmartCal.exe"'


def test_enable_disable_noop_when_no_system_writes(monkeypatch):
    import winreg
    from unittest import mock
    monkeypatch.setenv("SMARTCAL_NO_SYSTEM_WRITES", "1")
    with mock.patch.object(winreg, "CreateKeyEx") as create, \
            mock.patch.object(winreg, "OpenKey") as open_key:
        autostart.enable()
        autostart.disable()
    create.assert_not_called()
    open_key.assert_not_called()


def test_settings_ok_touches_run_key_only_when_autostart_toggled():
    class S:
        autostart = True
    calls = []
    s = S()
    w = autostart.SettingsWatcher(s, apply_fn=calls.append)
    assert w.settings_applied() is False and w.settings_applied() is False   # other edits
    assert calls == []                  # an existing (installed exe) Run value is left alone
    s.autostart = False
    assert w.settings_applied() is True and calls == [False]
    assert w.settings_applied() is False and calls == [False]
    s.autostart = True
    assert w.settings_applied() is True and calls == [False, True]


def test_run_py_applies_autostart_through_the_watcher():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    assert "autostart.apply(" not in src and "SettingsWatcher(settings)" in src
