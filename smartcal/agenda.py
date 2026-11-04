"""Agenda logic: today's merged events, next event, and its description."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Event


def _midnight(d: date, now: datetime) -> datetime:
    """Local midnight of `d`. DST-safe: uses now's zone if it is a real zone,
    else the system local zone."""
    naive = datetime.combine(d, time())
    if isinstance(now.tzinfo, ZoneInfo):
        return naive.replace(tzinfo=now.tzinfo)
    return naive.astimezone()


def _local_date(now: datetime) -> date:
    return now.date() if isinstance(now.tzinfo, ZoneInfo) else now.astimezone().date()


def _primary_ids(cache) -> set[str]:
    return {c.id for c in cache.calendars() if c.primary}


def _is_hidden(ev: Event, settings, primary_ids: set[str]) -> bool:
    return settings.is_hidden(ev.calendar_id, ev.calendar_id in primary_ids)


def today(cache, settings, now: datetime) -> list[Event]:
    """Events on the local calendar day of `now`: all-day first, then by start."""
    day = _local_date(now)
    day_start, day_end = _midnight(day, now), _midnight(day + timedelta(days=1), now)
    primary = _primary_ids(cache)
    out = []
    for ev in cache.events_between(day_start - timedelta(days=1), day_end + timedelta(days=1)):
        if _is_hidden(ev, settings, primary):
            continue
        if ev.all_day:
            # Stored as local midnights; end is exclusive.
            if not (ev.start.date() <= day < ev.end.date()):
                continue
        elif not (ev.start < day_end and ev.end > day_start):
            continue
        out.append(ev)
    out.sort(key=lambda e: (not e.all_day, e.start, e.title))
    return out


def next_event(cache, settings, now: datetime) -> Event | None:
    """First timed, visible event that has not ended yet (in progress counts)."""
    primary = _primary_ids(cache)
    best = None
    for ev in cache.events_between(now, now + timedelta(days=14)):
        if ev.all_day or ev.end <= now or _is_hidden(ev, settings, primary):
            continue
        if best is None or ev.start < best.start:
            best = ev
    return best


def describe_next(event: Event, now: datetime) -> str:
    if event.start <= now:
        return f"{event.title} now"
    minutes = math.ceil((event.start - now).total_seconds() / 60)
    if minutes < 60:
        return f"{event.title} in {minutes} min"
    h, m = divmod(minutes, 60)
    return f"{event.title} in {h} h" + (f" {m} min" if m else "")
