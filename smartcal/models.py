"""Plain data types shared across SmartCal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Event:
    """A calendar event. start/end are always tz-aware datetimes.

    All-day events have all_day=True and start/end as tz-aware local midnight
    datetimes (end is exclusive, i.e. midnight after the last day).
    """

    id: str
    account: str
    calendar_id: str
    calendar_name: str
    color: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str = ""
    description: str = ""
    meeting_url: str | None = None
    recurring_id: str | None = None
    # Fields below come from Google at fetch time; only `busy` is cached.
    updated: str | None = None  # Google updated timestamp
    status: str = "confirmed"  # Google status; "cancelled" = deleted
    recurrence: list[str] | None = None  # RRULE lines; set only on a series master
    is_series_master: bool = False
    attendee_count: int = 0  # attendees other than the owner
    organizer_self: bool | None = None
    raw_title: str | None = None  # Google summary as stored ("" if absent); None = not from Google
    busy: bool = True  # False when Google transparency is "transparent" (shown as Free)


@dataclass
class CalendarInfo:
    id: str
    name: str
    color: str
    primary: bool
