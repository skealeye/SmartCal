"""Locations for SmartCal's per-user data."""
from __future__ import annotations

import os
from pathlib import Path


def no_system_writes() -> bool:
    """True when SMARTCAL_NO_SYSTEM_WRITES=1: skip the Run key and toast app-id registry writes."""
    return os.environ.get("SMARTCAL_NO_SYSTEM_WRITES") == "1"


def data_dir() -> Path:
    r"""Return %APPDATA%\SmartCal (or $SMARTCAL_DATA_DIR if set), creating it if needed."""
    override = os.environ.get("SMARTCAL_DATA_DIR")
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / "SmartCal"
    path.mkdir(parents=True, exist_ok=True)
    return path
