"""Start with Windows via the HKCU Run registry key."""
from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SmartCal"


def default_command() -> str:
    """Command line that launches SmartCal at login."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{exe}" "{run_py}"'


def enable(exe_cmd: str | None = None, key_path: str = RUN_KEY) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, exe_cmd or default_command())


def disable(key_path: str = RUN_KEY) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


def is_enabled(key_path: str = RUN_KEY) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    return bool(value)


def apply(enabled: bool) -> None:
    """Make the registry match the setting; never raise (registry may be locked down)."""
    import logging
    try:
        enable() if enabled else disable()
    except OSError:
        logging.getLogger(__name__).exception("could not update autostart")


def apply_at_startup(settings, apply_fn=apply) -> None:
    """Default-on autostart exactly once (first run); afterwards obey the user's setting.

    Corrupt settings leave the registry alone.
    """
    import logging
    if settings.load_failed:
        logging.getLogger(__name__).warning("settings unreadable; autostart left unchanged")
        return
    if not settings.first_run_done:
        settings.autostart = True
        settings.first_run_done = True
        try:
            settings.save()
        except OSError:
            logging.getLogger(__name__).exception("could not save settings")
    apply_fn(settings.autostart)
