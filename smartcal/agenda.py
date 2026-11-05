"""Agenda logic: today's merged events, next event, and its description."""
from __future__ import annotations

import math
import os
from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Event


def local_zone() -> tzinfo | None:
    """The system zone as a ZoneInfo when it can be determined (TZ env var),
    else None. None means: resolve each boundary with naive.astimezone(), which
    uses the OS rules for that specific date and so stays DST-correct."""
    name = os.environ.get("TZ")
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return None


def zone(now: datetime) -> tzinfo | None:
    return now.tzinfo if isinstance(now.tzinfo, ZoneInfo) else local_zone()


def midnight(d: date, now: datetime) -> datetime:
    """Local midnight of `d`, resolved per boundary so 23/25-hour days work."""
    naive = datetime.combine(d, time())
    z = zone(now)
    return naive.replace(tzinfo=z) if z else naive.astimezone()


def local_date(now: datetime) -> date:
    z = zone(now)
    return now.astimezone(z).date() if z else now.astimezone().date()


def clock_text(dt: datetime) -> str:
    """'9:05 AM': the 12-hour clock used everywhere in the UI and in toasts."""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


# -- which events are shown -----------------------------------------------------
def primary_ids(cache) -> set[tuple[str, str]]:
    """(account, calendar id) of each account's primary calendar."""
    return {(account, c.id) for account, cals in cache.calendars_by_account().items()
            for c in cals if c.primary}


def calendar_owners(cache) -> dict[str, str]:
    """Calendar id -> the account whose copy is used when several accounts have it.

    A calendar shared into a second account (e.g. the personal calendar subscribed
    in the work account) is one calendar: the account that owns it (its primary
    calendar, or the calendar id is the account's email) wins, else the first
    account alphabetically."""
    seen: dict[str, list[tuple[str, bool]]] = {}
    for account, cals in cache.calendars_by_account().items():
        for c in cals:
            seen.setdefault(c.id, []).append((account, c.primary))
    return {cid: min(copies, key=lambda p: (
                not (p[1] or p[0].casefold() == cid.casefold()), p[0]))[0]
            for cid, copies in seen.items()}


def is_hidden(ev: Event, settings, primary: set[tuple[str, str]],
              owners: dict[str, str] | None = None) -> bool:
    """Hidden in Settings (keyed by calendar id). The default (primary calendars
    show) is taken from the owning account's copy of the calendar."""
    account = (owners or {}).get(ev.calendar_id, ev.account)
    return settings.is_hidden(ev.calendar_id, (account, ev.calendar_id) in primary)


def dedupe(events: list[Event], owners: dict[str, str]) -> list[Event]:
    """One copy per (calendar id, event id): the owning account's copy, else the
    first account alphabetically. Order of first appearance is kept."""
    best: dict[tuple[str, str], Event] = {}

    def rank(e: Event):
        return (e.account != owners.get(e.calendar_id, e.account), e.account)

    for e in events:
        key = (e.calendar_id, e.id)
        cur = best.get(key)
        if cur is None or rank(e) < rank(cur):
            best[key] = e
    keep = {id(e) for e in best.values()}
    return [e for e in events if id(e) in keep]


def visible_events(cache, settings, events: list[Event],
                   include_declined: bool = False) -> list[Event]:
    """The events the owner sees: shared calendars deduped, hidden calendars
    dropped (settings=None: no hiding), declined meetings dropped unless asked for."""
    owners = calendar_owners(cache)
    out = dedupe(events, owners)
    if not include_declined:
        out = [e for e in out if not e.declined]
    if settings is not None:
        primary = primary_ids(cache)
        out = [e for e in out if not is_hidden(e, settings, primary, owners)]
    return out


def today(cache, settings, now: datetime) -> list[Event]:
    """Events on the local calendar day of `now`: all-day first, then by start."""
    day = local_date(now)
    day_start, day_end = midnight(day, now), midnight(day + timedelta(days=1), now)
    out = []
    for ev in visible_events(cache, settings, cache.events_between(
            day_start - timedelta(days=1), day_end + timedelta(days=1))):
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
    best = None
    for ev in visible_events(cache, settings, cache.events_between(now, now + timedelta(days=14))):
        if ev.all_day or ev.end <= now:
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
