"""Google Calendar client: maps API dicts to SmartCal models."""
from __future__ import annotations

import functools
import html
import re
import threading
from datetime import date, datetime, time

from smartcal.google_auth import ReauthRequired
from smartcal.models import CalendarInfo, Event
from smartcal.textutil import html_to_text, meeting_label  # noqa: F401  (re-exported)

_ZOOM_RE = re.compile(r"https?://(?:[\w-]+\.)?zoom\.us/[^\s<>\"')]+")
_TEAMS_RE = re.compile(r"https?://teams\.(?:microsoft|live)\.com/[^\s<>\"')]+")


_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)


def _meeting_url(raw: dict) -> str | None:
    """Best join link: conference video entry point, hangoutLink, then Zoom/Teams in text."""
    cd = raw.get("conferenceData")
    entry_points = cd.get("entryPoints") if isinstance(cd, dict) else None
    for ep in entry_points if isinstance(entry_points, list) else []:
        uri = ep.get("uri") if isinstance(ep, dict) else None
        if (isinstance(ep, dict) and ep.get("entryPointType") == "video"
                and isinstance(uri, str) and uri.lower().startswith(("http://", "https://"))):
            return uri
    if raw.get("hangoutLink"):
        return raw["hangoutLink"]
    location = raw.get("location") or ""
    desc = raw.get("description") or ""
    unescaped = html.unescape(desc)
    sources = [location, unescaped, html_to_text(desc)]
    sources += [m.group(1) for m in _HREF_RE.finditer(unescaped)]
    for rx in (_ZOOM_RE, _TEAMS_RE):
        for text in sources:
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


# The assistant never emails attendees on the owner's behalf: every write passes
# sendUpdates="none" explicitly (the API default would notify guests).
NO_NOTIFY = "none"


def http_status(exc: Exception) -> int | None:
    return getattr(getattr(exc, "resp", None), "status", None)


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


HTTP_TIMEOUT = 20  # seconds; a stuck request must not hang the sync thread forever


def _locked(method):
    """Run the method under the client's lock: one request at a time per client."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class GoogleClient:
    """Calendar operations for one account.

    `service` is injectable for tests; when None the real API service is built
    lazily (raising ReauthRequired if the stored token is unusable).

    The underlying httplib2 connection is not thread-safe: callers keep one client
    per thread (see clients.ClientPool), and every public method also holds an
    RLock so a shared client can never interleave two requests.
    """

    def __init__(self, account: str, service=None):
        self.account = account
        self._service = service
        self._cals: dict[str, CalendarInfo] | None = None
        self._lock = threading.RLock()

    @property
    def service(self):
        with self._lock:
            if self._service is None:
                import google_auth_httplib2
                import httplib2
                from googleapiclient import discovery
                from smartcal import google_auth
                http = google_auth_httplib2.AuthorizedHttp(
                    google_auth.load_credentials(self.account),
                    http=httplib2.Http(timeout=HTTP_TIMEOUT))
                self._service = discovery.build("calendar", "v3", http=http,
                                                cache_discovery=False)
            return self._service

    @_locked
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

    @_locked
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
            updated=raw.get("updated"), status=raw.get("status", "confirmed"),
            recurrence=raw.get("recurrence") or None,
            is_series_master=bool(raw.get("recurrence")),
            attendee_count=sum(1 for a in raw.get("attendees", []) if not a.get("self")),
            organizer_self=(bool(raw["organizer"].get("self"))
                            if isinstance(raw.get("organizer"), dict) else None),
            raw_title=raw.get("summary") or "",
            busy=raw.get("transparency") != "transparent",
            declined=any(a.get("self") and a.get("responseStatus") == "declined"
                         for a in raw.get("attendees", []) if isinstance(a, dict)))

    @_locked
    def list_events(self, start: datetime, end: datetime,
                    calendars: list[CalendarInfo] | None = None) -> list[Event]:
        """Events from every calendar in [start, end), recurrences expanded. Pass
        calendars (from list_calendars) to skip fetching the list again."""
        events: list[Event] = []
        for cal in (self.list_calendars() if calendars is None else calendars):
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

    @_locked
    def get_event(self, event_id: str, *, calendar_id: str) -> Event:
        raw = _exec(self.service.events().get(calendarId=calendar_id, eventId=event_id))
        if raw.get("status") == "cancelled" and "start" not in raw:
            # Deleted events may come back without times; callers only need the status.
            now = datetime.now().astimezone()
            raw = {**raw, "start": {"dateTime": now.isoformat()},
                   "end": {"dateTime": now.isoformat()}}
        return self._to_event(raw, calendar_id)

    @_locked
    def create_event(self, calendar_id: str, title: str, start: datetime, end: datetime,
                     all_day: bool = False, location: str = "", description: str = "") -> Event:
        """Create an event; for all_day, `end` is exclusive (midnight after last day)."""
        body = {"summary": title, "start": _time_body(start, all_day),
                "end": _time_body(end, all_day), "location": location,
                "description": description}
        raw = _exec(self.service.events().insert(
            calendarId=calendar_id, body=body, sendUpdates=NO_NOTIFY))
        return self._to_event(raw, calendar_id)

    @_locked
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
            calendarId=calendar_id, eventId=event_id, body=body, sendUpdates=NO_NOTIFY))
        return self._to_event(raw, calendar_id)

    @_locked
    def delete_event(self, event_id: str, *, calendar_id: str) -> None:
        _exec(self.service.events().delete(
            calendarId=calendar_id, eventId=event_id, sendUpdates=NO_NOTIFY))

    @_locked
    def restore_event(self, before: Event) -> Event:
        """Bring back a deleted event with the fields of `before`.

        First un-deletes it (patch status "confirmed" on the same id; keeps the id
        and a recurring instance's place in its series). Only if Google answers
        404/410 (the event is really gone) is a new event inserted, and never for
        a series instance (that would create a one-off). Any other error (403, 5xx,
        timeout, ...) propagates: the outcome is unknown, so nothing more is sent.
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
                body={**body, "status": "confirmed"}, sendUpdates=NO_NOTIFY))
        except HttpError as exc:
            if http_status(exc) not in (404, 410) or before.recurring_id:
                raise
            if before.recurrence:
                body["recurrence"] = before.recurrence
            raw = _exec(self.service.events().insert(
                calendarId=before.calendar_id, body=body, sendUpdates=NO_NOTIFY))
        return self._to_event(raw, before.calendar_id)
