"""Calendar actions used by the assistant: create/move/update/delete/find/free_slots.

Safety rules for every write:
  1. fetch the CURRENT event from Google (never trust the cache) as the "before" state;
  2. journal an undo entry, write, then store the returned event in the cache;
  3. if the write raises, the journal entry is dropped, so Undo never reverses
     something that did not happen.
Google event ids are only unique per calendar, so methods taking an event_id accept
`account=` / `calendar_id=`; an id matching several calendars raises AmbiguousEventError.
Only the event's own account client is ever used. Logs carry event ids, never titles.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from . import agenda
from .google_client import http_status
from .models import Event
from .undo import Change, UndoStore, snapshot

log = logging.getLogger(__name__)

_UPDATE_FIELDS = {"title", "start", "end", "all_day", "location", "description"}


class EventNotFoundError(LookupError):
    pass


class AmbiguousEventError(LookupError):
    def __init__(self, candidates: list[Event]):
        self.candidates = candidates
        super().__init__(f"event id matches {len(candidates)} calendars")


class SeriesRefusedError(PermissionError):
    """The event is a recurring-series master; changing it would change every occurrence."""

    def __init__(self, event: Event):
        self.event = event
        super().__init__("event is a recurring series master")


class AttendeesRequireConfirmationError(PermissionError):
    """The event has other attendees; changing it affects other people."""

    def __init__(self, event: Event):
        self.event = event
        super().__init__(f"event has {event.attendee_count} other attendee(s)")


def _as_datetime(value, all_day: bool) -> datetime:
    """All-day events accept a date (converted to local midnight) or a datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            if not all_day:
                raise ValueError("times must be timezone-aware")
            return value.astimezone()
        return value
    if isinstance(value, date) and all_day:
        return datetime.combine(value, time()).astimezone()
    raise ValueError("times must be timezone-aware datetimes")


def _check_range(start: datetime, end: datetime) -> None:
    if not end > start:
        raise ValueError("end must be after start")


class Actions:
    def __init__(self, clients, cache, undo: UndoStore, settings=None):
        self._clients = clients          # callable: account -> GoogleClient
        self.cache = cache
        self.undo = undo
        self.settings = settings

    def client(self, account: str):
        return self._clients(account)

    # -- lookup ------------------------------------------------------------
    def _locate(self, event_id: str, account, calendar_id) -> tuple[str, str]:
        if account is not None and calendar_id is not None:
            return account, calendar_id
        found = [e for e in self.cache.events_by_id(event_id)
                 if (account is None or e.account == account)
                 and (calendar_id is None or e.calendar_id == calendar_id)]
        if not found:
            raise EventNotFoundError(event_id)
        if len({(e.account, e.calendar_id) for e in found}) > 1:
            raise AmbiguousEventError(found)
        return found[0].account, found[0].calendar_id

    def _begin(self, event_id: str, account, calendar_id, allow_series: bool,
               allow_attendees: bool):
        """Locate the event and fetch its CURRENT Google state; refuse when unsafe."""
        account, calendar_id = self._locate(event_id, account, calendar_id)
        client = self.client(account)
        try:
            before = client.get_event(event_id, calendar_id=calendar_id)
        except Exception as exc:  # noqa: BLE001
            if http_status(exc) in (404, 410):
                self.cache.delete_event(account, calendar_id, event_id)
                raise EventNotFoundError(event_id) from exc
            raise
        if before.status == "cancelled":
            self.cache.delete_event(account, calendar_id, event_id)
            raise EventNotFoundError(event_id)
        if before.is_series_master and not allow_series:
            raise SeriesRefusedError(before)
        if before.attendee_count > 0 and not allow_attendees:
            raise AttendeesRequireConfirmationError(before)
        return client, before

    def _journal(self, kind: str, before: Event) -> Change:
        return self.undo.record(Change(
            kind=kind, account=before.account, calendar_id=before.calendar_id,
            event_id=before.id, before=snapshot(before), after_updated=None,
            summary=before.title, at=datetime.now(timezone.utc).isoformat(),
            before_updated=before.updated, pending=True))

    def _write(self, kind: str, before: Event, do):
        """Journal as pending, write, and drop the entry if the write raised.

        The undo lock is held throughout, so an Undo from another thread cannot
        run between "journaled" and "finalized"."""
        with self.undo.lock:
            change = self._journal(kind, before)
            try:
                result = do()
            except BaseException:
                self.undo.discard(change)
                raise
            self.undo.finalize(change, getattr(result, "updated", None))
            return change, result

    # -- writes ------------------------------------------------------------
    def create(self, account: str, *, calendar_id: str | None = None, title: str,
               start: datetime, end: datetime, all_day: bool = False,
               location: str = "", description: str = "") -> Event:
        if not isinstance(start, datetime) or not isinstance(end, datetime) \
                or start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware datetimes")
        _check_range(start, end)
        if not title.strip():
            raise ValueError("title is required")
        if calendar_id is None:
            cals = self.cache.calendars_by_account().get(account, [])
            primary = next((c for c in cals if c.primary), None)
            if primary is None:
                raise ValueError(f"no primary calendar known for {account}")
            calendar_id = primary.id
        ev = self.client(account).create_event(
            calendar_id, title, start, end, all_day=all_day,
            location=location, description=description)
        self.undo.record(Change(
            kind="create", account=account, calendar_id=calendar_id, event_id=ev.id,
            before=None, after_updated=ev.updated, summary=ev.title,
            at=datetime.now(timezone.utc).isoformat()))
        self.cache.upsert_events(account, [ev])
        log.info("created event %s", ev.id)
        return ev

    def move(self, event_id: str, new_start, new_end, *, account: str | None = None,
             calendar_id: str | None = None, allow_series: bool = False,
             allow_attendees: bool = False) -> Event:
        """Move an event. Timed events need tz-aware datetimes. For all-day events
        pass dates (or datetimes); `new_end` is exclusive (the day after the last
        day), the same convention the events use. A recurring instance (its id is
        the instance id) moves alone; the rest of the series is untouched. A series master is refused
        unless allow_series; an event with other attendees unless allow_attendees."""
        client, before = self._begin(event_id, account, calendar_id, allow_series, allow_attendees)
        s, e = _as_datetime(new_start, before.all_day), _as_datetime(new_end, before.all_day)
        _check_range(s, e)
        change, ev = self._write("move", before, lambda: client.update_event(
            event_id, calendar_id=before.calendar_id, start=s, end=e, all_day=before.all_day))
        return self._after(change, ev)

    def update(self, event_id: str, *, account: str | None = None,
               calendar_id: str | None = None, allow_series: bool = False,
               allow_attendees: bool = False, **fields) -> Event:
        bad = set(fields) - _UPDATE_FIELDS
        if bad:
            raise ValueError(f"unknown fields: {sorted(bad)}")
        if not fields:
            raise ValueError("nothing to update")
        if any(v is None for v in fields.values()):
            raise ValueError("field values must not be None")
        if "title" in fields and not str(fields["title"]).strip():
            raise ValueError("title must not be empty")
        client, before = self._begin(event_id, account, calendar_id, allow_series, allow_attendees)
        if "all_day" in fields and bool(fields["all_day"]) != before.all_day \
                and not ("start" in fields and "end" in fields):
            raise ValueError("changing all_day requires start and end")
        all_day = bool(fields.get("all_day", before.all_day))
        if "start" in fields or "end" in fields:
            fields["all_day"] = all_day   # the client requires it alongside times
        else:
            fields.pop("all_day", None)   # unchanged flag, no times: nothing to send
        if "start" in fields or "end" in fields:
            s = _as_datetime(fields.get("start", before.start), all_day)
            e = _as_datetime(fields.get("end", before.end), all_day)
            _check_range(s, e)
            if "start" in fields:
                fields["start"] = s
            if "end" in fields:
                fields["end"] = e
        change, ev = self._write("update", before, lambda: client.update_event(
            event_id, calendar_id=before.calendar_id, **fields))
        return self._after(change, ev)

    def delete(self, event_id: str, *, account: str | None = None,
               calendar_id: str | None = None, allow_series: bool = False,
               allow_attendees: bool = False) -> None:
        """Delete one event (a recurring instance removes only that occurrence)."""
        client, before = self._begin(event_id, account, calendar_id, allow_series, allow_attendees)
        change, _ = self._write("delete", before, lambda: client.delete_event(
            event_id, calendar_id=before.calendar_id))
        self.cache.delete_event(before.account, before.calendar_id, event_id)
        log.info("deleted event %s", event_id)

    def _after(self, change: Change, ev: Event) -> Event:
        self.cache.upsert_events(ev.account, [ev])
        log.info("%s event %s", change.kind, ev.id)
        return ev

    # -- reads -------------------------------------------------------------
    def _visible(self, events: list[Event]) -> list[Event]:
        if self.settings is None:
            return events
        primary = agenda._primary_ids(self.cache)
        return [e for e in events if not agenda._is_hidden(e, self.settings, primary)]

    def find(self, query: str, day: date | None = None) -> list[Event]:
        """Case-insensitive substring over title/location/calendar name."""
        q = query.strip().casefold()
        now = datetime.now().astimezone()
        if day is None:
            lo, hi = datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc)
        else:
            lo, hi = agenda._midnight(day, now), agenda._midnight(day + timedelta(days=1), now)
        out = []
        for e in self._visible(self.cache.events_between(lo, hi)):
            if day is not None and e.all_day and not (e.start.date() <= day < e.end.date()):
                continue
            if q in f"{e.title}\n{e.location}\n{e.calendar_name}".casefold():
                out.append(e)
        out.sort(key=lambda e: (e.start, e.title, e.account, e.id))
        return out

    def free_slots(self, day: date, duration: timedelta,
                   working_hours: tuple[int, int] = (9, 17),
                   now: datetime | None = None) -> list[tuple[datetime, datetime]]:
        """Gaps >= duration within local working hours on `day`, across every visible
        calendar of every account. Busy = timed events (partial overlap counts);
        all-day events, zero-length events and events Google shows as Free
        (transparent) are ignored. The model has no declined-status field, so
        declined events cannot be told apart and count as busy. `now` only supplies
        the time zone (default: the system zone, resolved per boundary for DST)."""
        h0, h1 = working_hours
        if not (0 <= h0 < h1 <= 24) or duration <= timedelta(0):
            raise ValueError("bad working hours or duration")
        now = now or datetime.now().astimezone()

        def at(hour: int) -> datetime:
            d = day + timedelta(days=1) if hour == 24 else day
            return agenda._midnight(d, now) if hour in (0, 24) else _at(d, hour, now)

        w0, w1 = at(h0), at(h1)
        if day == agenda._local_date(now):
            w0 = max(w0, now)   # never offer time that has already passed
        if w0 >= w1:
            return []
        busy = sorted((max(e.start, w0), min(e.end, w1))
                      for e in self._visible(self.cache.events_between(w0, w1))
                      if not e.all_day and e.busy and e.end > e.start
                      and e.end > w0 and e.start < w1)
        merged: list[list[datetime]] = []
        for s, e in busy:
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        slots, cursor = [], w0
        for s, e in merged + [[w1, w1]]:
            if s - cursor >= duration:
                slots.append((cursor, s))
            cursor = max(cursor, e)
        return slots


def _at(d: date, hour: int, now: datetime) -> datetime:
    naive = datetime.combine(d, time(hour))
    zone = agenda._zone(now)
    return naive.replace(tzinfo=zone) if zone else naive.astimezone()
