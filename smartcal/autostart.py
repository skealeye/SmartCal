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
    from smartcal.config import no_system_writes
    if no_system_writes():
        return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, exe_cmd or default_command())


def disable(key_path: str = RUN_KEY) -> None:
    import winreg
    from smartcal.config import no_system_writes
    if no_system_writes():
        return
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
    from smartcal.config import no_system_writes
    if no_system_writes():
        return
    try:
        enable() if enabled else disable()
    except OSError:
        logging.getLogger(__name__).exception("could not update autostart")


def current_value(key_path: str = RUN_KEY) -> str | None:
    """The Run-key command now registered for SmartCal, or None."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None
    return str(value) if value else None


def startup_action(enabled: bool, current: str | None, wanted: str, frozen: bool) -> str | None:
    """What to do to the Run key at startup: "enable", "disable" or None.

    A missing value is added when autostart is on. An existing value is only
    rewritten (or removed when autostart is off) by the packaged exe: a run from
    source (e.g. a dev checkout on the Z: share) never repoints login at itself.
    Toggling the setting in Settings always applies (see `apply`)."""
    if enabled:
        if current is None:
            return "enable"
        return "enable" if frozen and current != wanted else None
    return "disable" if frozen and current is not None else None


def apply_at_startup(settings, read=current_value, enable_fn=enable, disable_fn=disable,
                     frozen: bool | None = None) -> None:
    """Default-on autostart exactly once (first run); afterwards keep the Run key in
    line with the setting without rewriting it on every start (see startup_action).

    Corrupt settings leave the registry alone.
    """
    import logging
    from smartcal.config import no_system_writes
    log = logging.getLogger(__name__)
    if settings.load_failed:
        log.warning("settings unreadable; autostart left unchanged")
        return
    if not settings.first_run_done:
        settings.autostart = True
        settings.first_run_done = True
        try:
            settings.save()
        except OSError:
            log.exception("could not save settings")
    if no_system_writes():
        return
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    try:
        action = startup_action(settings.autostart, read(), default_command(), frozen)
        if action == "enable":
            enable_fn()
        elif action == "disable":
            disable_fn()
    except OSError:
        log.exception("could not update autostart")
