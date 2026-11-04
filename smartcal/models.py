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


@dataclass
class CalendarInfo:
    id: str
    name: str
    color: str
    primary: bool
