"""Google Calendar client: maps API dicts to SmartCal models."""
from __future__ import annotations

import re
from datetime import date, datetime, time

from smartcal.google_auth import ReauthRequired
from smartcal.models import CalendarInfo, Event

_ZOOM_RE = re.compile(r"https?://(?:[\w-]+\.)?zoom\.us/[^\s<>\"')]+")
_TEAMS_RE = re.compile(r"https?://teams\.(?:microsoft|live)\.com/[^\s<>\"')]+")


def _meeting_url(raw: dict) -> str | None:
    if raw.get("hangoutLink"):
        return raw["hangoutLink"]
    text = f"{raw.get('location', '')}\n{raw.get('description', '')}"
    for rx in (_ZOOM_RE, _TEAMS_RE):
        m = rx.search(text)
        if m:
            return m.group(0).rstrip(".,;")
    return None


def _local_midnight(d: str) -> datetime:
    return datetime.combine(date.fromisoformat(d), time()).astimezone()


def _parse_time(t: dict) -> tuple[datetime, bool]:
    if "date" in t:
        return _local_midnight(t["date"]), True
    dt = datetime.fromisoformat(t["dateTime"].replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.astimezone()), False


def _time_body(value: datetime, all_day: bool) -> dict:
    if all_day:
        return {"date": value.date().isoformat()}
    if value.tzinfo is None:
        value = value.astimezone()
    return {"dateTime": value.isoformat()}


def _exec(request):
    """Execute an API request, mapping auth failures to ReauthRequired."""
    from google.auth.exceptions import RefreshError
    from googleapiclient.errors import HttpError
    try:
        return request.execute()
    except RefreshError as exc:
        raise ReauthRequired(str(exc)) from exc
    except HttpError as exc:
        if getattr(exc.resp, "status", None) == 401:
            raise ReauthRequired(str(exc)) from exc
        raise


class GoogleClient:
    """Calendar operations for one account.

    `service` is injectable for tests; when None the real API service is built
    lazily (raising ReauthRequired if the stored token is unusable).
    """

    def __init__(self, account: str, service=None):
        self.account = account
        self._service = service
        self._cals: dict[str, CalendarInfo] | None = None

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build
            from smartcal.google_auth import load_credentials
            self._service = build("calendar", "v3", credentials=load_credentials(self.account),
                                  cache_discovery=False)
        return self._service

    def list_calendars(self) -> list[CalendarInfo]:
        out, page = [], None
        while True:
            resp = _exec(self.service.calendarList().list(pageToken=page))
            for c in resp.get("items", []):
                out.append(CalendarInfo(
                    id=c["id"],
                    name=c.get("summaryOverride") or c.get("summary") or c["id"],
                    color=c.get("backgroundColor", "#4285f4"),
                    primary=bool(c.get("primary", False))))
            page = resp.get("nextPageToken")
            if not page:
                break
        self._cals = {c.id: c for c in out}
        return out

    def _cal(self, calendar_id: str) -> CalendarInfo:
        if self._cals is None:
            self.list_calendars()
        return self._cals.get(calendar_id) or CalendarInfo(calendar_id, calendar_id, "#4285f4", False)

    def _to_event(self, raw: dict, calendar_id: str) -> Event:
        cal = self._cal(calendar_id)
        start, all_day = _parse_time(raw["start"])
        end, _ = _parse_time(raw["end"])
        return Event(
            id=raw["id"], account=self.account, calendar_id=calendar_id,
            calendar_name=cal.name, color=cal.color,
            title=raw.get("summary") or "(no title)",
            start=start, end=end, all_day=all_day,
            location=raw.get("location", ""), description=raw.get("description", ""),
            meeting_url=_meeting_url(raw), recurring_id=raw.get("recurringEventId"),
            updated=raw.get("updated"))

    def list_events(self, start: datetime, end: datetime) -> list[Event]:
        """Events from every calendar in [start, end), recurrences expanded."""
        events: list[Event] = []
        for cal in self.list_calendars():
            page = None
            while True:
                resp = _exec(self.service.events().list(
                    calendarId=cal.id, timeMin=start.isoformat(), timeMax=end.isoformat(),
                    singleEvents=True, orderBy="startTime", pageToken=page))
                for raw in resp.get("items", []):
                    if raw.get("status") == "cancelled":
                        continue
                    events.append(self._to_event(raw, cal.id))
                page = resp.get("nextPageToken")
                if not page:
                    break
        return events

    def get_event(self, event_id: str, *, calendar_id: str) -> Event:
        raw = _exec(self.service.events().get(calendarId=calendar_id, eventId=event_id))
        return self._to_event(raw, calendar_id)

    def create_event(self, calendar_id: str, title: str, start: datetime, end: datetime,
                     all_day: bool = False, location: str = "", description: str = "") -> Event:
        """Create an event; for all_day, `end` is exclusive (midnight after last day)."""
        body = {"summary": title, "start": _time_body(start, all_day),
                "end": _time_body(end, all_day), "location": location,
                "description": description}
        raw = _exec(self.service.events().insert(calendarId=calendar_id, body=body))
        return self._to_event(raw, calendar_id)

    def update_event(self, event_id: str, *, calendar_id: str, **fields) -> Event:
        """Patch an event. Fields: title, start, end, all_day, location, description.

        If start or end is given, all_day must be given too (ValueError otherwise).
        Pass the instance id (not the recurring id) to change a single occurrence.
        """
        if ("start" in fields or "end" in fields) and "all_day" not in fields:
            raise ValueError("all_day is required when updating start or end")
        all_day = fields.get("all_day", False)
        body: dict = {}
        if "title" in fields:
            body["summary"] = fields["title"]
        for k in ("location", "description"):
            if k in fields:
                body[k] = fields[k]
        for k in ("start", "end"):
            if k in fields:
                body[k] = _time_body(fields[k], all_day)
        raw = _exec(self.service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=body))
        return self._to_event(raw, calendar_id)

    def delete_event(self, event_id: str, *, calendar_id: str) -> None:
        _exec(self.service.events().delete(calendarId=calendar_id, eventId=event_id))

    def restore_event(self, before: Event) -> Event:
        """Bring back a deleted event with the fields of `before`.

        First tries to un-delete it (patch status "confirmed" on the same id, which
        keeps the id and, for a recurring instance, its place in the series). If
        Google refuses (HttpError, e.g. already purged), inserts a new event.
        """
        from googleapiclient.errors import HttpError
        all_day = before.all_day
        body = {"summary": before.title, "location": before.location,
                "description": before.description,
                "start": _time_body(before.start, all_day),
                "end": _time_body(before.end, all_day)}
        try:
            raw = _exec(self.service.events().patch(
                calendarId=before.calendar_id, eventId=before.id,
                body={**body, "status": "confirmed"}))
        except HttpError:
            raw = _exec(self.service.events().insert(calendarId=before.calendar_id, body=body))
        return self._to_event(raw, before.calendar_id)
