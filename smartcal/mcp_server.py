"""SmartCal's MCP server (v0.16.0): lets Claude Code / Claude Desktop talk to SmartCal.

A stdio server (one JSON-RPC message per line), no third-party packages. Claude starts it
as `SmartCal-mcp.exe` (or `python smartcal_mcp.py`). Tools:
  smartcal_now, smartcal_add_reminder, smartcal_list_reminders, smartcal_cancel_reminder,
  smartcal_agenda.
Writes go through the inbox (the running SmartCal applies them); reads open the files
SmartCal keeps (reminders.json, cache.db, settings.json) without changing them.
stdout carries only protocol messages; everything else goes to stderr.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta

from . import __version__, inbox
from .config import data_dir

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_AGENDA_DAYS = 14

TOOLS = [
    {"name": "smartcal_now",
     "description": "The current local date, time and time zone on the user's PC. Call this "
                    "first when the user gives a relative time such as 'tomorrow at 10am'.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "smartcal_add_reminder",
     "description": "Make a SmartCal reminder: a popup card with a chime at the given time. "
                    "SmartCal (the tray app) must be running; it adds the reminder within "
                    "a few seconds.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "What to remind about (max 200 characters)."},
         "when": {"type": "string", "description": "Local date and time, ISO 8601, e.g. "
                  "2026-11-11T10:00:00 (an offset such as -05:00 is allowed)."}},
         "required": ["text", "when"]}},
    {"name": "smartcal_list_reminders",
     "description": "The reminders that have not fired yet (id, time, text).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "smartcal_cancel_reminder",
     "description": "Cancel a reminder by its id (see smartcal_list_reminders).",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "smartcal_agenda",
     "description": "The user's meetings (as SmartCal shows them) for one or more days.",
     "inputSchema": {"type": "object", "properties": {
         "start_date": {"type": "string", "description": "YYYY-MM-DD; default today."},
         "days": {"type": "integer", "description": f"1 to {MAX_AGENDA_DAYS}; default 1."}}}},
]


class ToolError(Exception):
    """A problem to report to Claude as the tool's result (not a protocol error)."""


def _now() -> datetime:
    return datetime.now().astimezone()


def _clock(dt: datetime) -> str:
    return f"{dt:%a %b} {dt.day}, {dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def tool_now(_args) -> str:
    n = _now()
    return f"{_clock(n)} {n:%Y} ({n.isoformat(timespec='minutes')}, {n.tzname()})"


def tool_add_reminder(args) -> str:
    text = str(args.get("text") or "").strip()
    if not text:
        raise ToolError("text is empty")
    try:
        when = datetime.fromisoformat(str(args.get("when")))
    except ValueError:
        raise ToolError("when must be an ISO 8601 local time like 2026-11-11T10:00:00") from None
    if when.tzinfo is None:
        when = when.astimezone()
    if when < _now() - timedelta(minutes=1):
        raise ToolError(f"{_clock(when)} has already passed; call smartcal_now and try again")
    inbox.post({"type": "add_reminder", "text": text[:200], "fire_at": when.isoformat()})
    return (f"Queued: SmartCal will remind you on {_clock(when)}: {text[:200]}. "
            "SmartCal has to be running to add it (it is picked up at its next start otherwise).")


def _read_reminders() -> list[dict]:
    try:
        data = json.loads((data_dir() / "reminders.json").read_text(encoding="utf-8"))
        return [i for i in data.get("items", []) if not i.get("fired")]
    except (OSError, ValueError, AttributeError):
        return []


def tool_list_reminders(_args) -> str:
    items = sorted(_read_reminders(), key=lambda i: str(i.get("fire_at")))
    if not items:
        return "No reminders are waiting."
    lines = []
    for i in items:
        try:
            when = _clock(datetime.fromisoformat(i["fire_at"]).astimezone())
        except (KeyError, ValueError):
            when = "?"
        lines.append(f"#{i.get('id')}  {when}  {i.get('text')}")
    return "\n".join(lines)


def tool_cancel_reminder(args) -> str:
    try:
        rid = int(args.get("id"))
    except (TypeError, ValueError):
        raise ToolError("id must be a number") from None
    inbox.post({"type": "cancel_reminder", "id": rid})
    return f"Queued: reminder #{rid} will be cancelled (if it exists and SmartCal is running)."


def tool_agenda(args) -> str:
    from . import agenda
    from .db import Cache
    from .settings import Settings
    try:
        start = date.fromisoformat(args["start_date"]) if args.get("start_date") else _now().date()
        days = max(1, min(MAX_AGENDA_DAYS, int(args.get("days") or 1)))
    except (ValueError, TypeError):
        raise ToolError("start_date must be YYYY-MM-DD and days a number") from None
    now = _now()
    begin = agenda.midnight(start, now)
    end = agenda.midnight(start + timedelta(days=days), now)
    cache = Cache(data_dir() / "cache.db")
    events = agenda.visible_events(cache, Settings(), cache.events_between(begin, end),
                                   hide_user_hidden=True)
    if not events:
        return "No meetings."
    lines, day = [], None
    for ev in sorted(events, key=lambda e: (e.start, e.title)):
        if ev.start.date() != day:
            day = ev.start.date()
            lines.append(f"{day:%a %b} {day.day}:")
        when = "all day" if ev.all_day else f"{agenda.clock_text(ev.start)}-{agenda.clock_text(ev.end)}"
        extra = f"  [{ev.meeting_url}]" if getattr(ev, "meeting_url", None) else ""
        lines.append(f"  {when}  {ev.title}{extra}")
    return "\n".join(lines)


HANDLERS = {"smartcal_now": tool_now, "smartcal_add_reminder": tool_add_reminder,
            "smartcal_list_reminders": tool_list_reminders,
            "smartcal_cancel_reminder": tool_cancel_reminder, "smartcal_agenda": tool_agenda}


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, the reply out (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:        # a notification (e.g. notifications/initialized): no reply
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in SUPPORTED else SUPPORTED[0],
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "smartcal", "version": __version__}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        fn = HANDLERS.get(params.get("name"))
        if fn is None:
            return err(-32602, f"unknown tool {params.get('name')!r}")
        try:
            return ok({"content": [{"type": "text", "text": fn(params.get("arguments") or {})}]})
        except ToolError as exc:
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # noqa: BLE001
            print(f"smartcal-mcp: {params.get('name')} failed: {exc!r}", file=sys.stderr)
            return ok({"content": [{"type": "text", "text": "SmartCal could not do that."}],
                       "isError": True})
    return err(-32601, f"method not found: {method}")


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
            reply = handle(msg) if isinstance(msg, dict) else None
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0
