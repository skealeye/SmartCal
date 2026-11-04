"""Locations for SmartCal's per-user data."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Return %APPDATA%\SmartCal, creating it if needed."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / "SmartCal"
    path.mkdir(parents=True, exist_ok=True)
    return path
