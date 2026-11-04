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
