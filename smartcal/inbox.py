"""The inbox: how other programs (the MCP server, v0.16.0) hand SmartCal a command.

A writer drops one small JSON file into %APPDATA%\\SmartCal\\inbox (written under a .tmp
name, then renamed, so a half-written file is never read). The running SmartCal picks
files up every few seconds (and at start-up), applies them and deletes them. Qt-free.
SmartCal alone owns reminders.json, so two processes never write it at once.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from .config import data_dir

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 10_000
MAX_PER_PASS = 50


def inbox_dir() -> Path:
    path = data_dir() / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def post(command: dict, directory: Path | None = None) -> Path:
    """Queue one command (a JSON-able dict with a "type")."""
    d = Path(directory) if directory else inbox_dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    tmp = d / f"{name}.tmp"
    tmp.write_text(json.dumps(command), encoding="utf-8")
    final = d / f"{name}.json"
    os.replace(tmp, final)
    return final


def _apply(command, reminders) -> str:
    if not isinstance(command, dict):
        raise ValueError("not an object")
    kind = command.get("type")
    if kind == "add_reminder":
        fire_at = datetime.fromisoformat(str(command.get("fire_at")))
        if fire_at.tzinfo is None:
            fire_at = fire_at.astimezone()
        rid = reminders.add(str(command.get("text") or ""), fire_at)
        return f"reminder #{rid} added"
    if kind == "cancel_reminder":
        reminders.cancel(int(command.get("id")))
        return "reminder cancelled"
    raise ValueError(f"unknown command type {kind!r}")


def process(reminders, directory: Path | None = None) -> list[str]:
    """Apply and delete every queued command; return a line per command. A file that
    cannot be applied is renamed to .bad (kept for a look, never retried)."""
    d = Path(directory) if directory else inbox_dir()
    done: list[str] = []
    try:
        files = sorted(d.glob("*.json"))[:MAX_PER_PASS]
    except OSError:
        return done
    for f in files:
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("file too large")
            command = json.loads(f.read_text(encoding="utf-8"))
            done.append(_apply(command, reminders))
            f.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            log.warning("inbox: %s skipped: %s", f.name, exc)
            try:
                os.replace(f, f.with_suffix(".bad"))
            except OSError:
                pass
    return done
