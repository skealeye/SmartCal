"""Pure layout math for the calendar window (no widgets, unit-testable).

Every function that touches datetimes takes `ref`, a tz-aware datetime used only
to pick the time zone; day boundaries come from agenda's DST-safe helpers.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .. import agenda
from ..models import Event

# Python weekday number of the first day of the week (6 = Sunday, US convention).
WEEK_START = 6
MAX_CHIPS = 3        # events shown per month cell before "+N more"
# Shortest block drawn on a timeline, in minutes (v0.11.0: 24, with the window's 60 px
# hour a 15-minute meeting is still tall enough for one readable line of title).
MIN_BLOCK_MIN = 24
DAY_MIN = 24 * 60


# -- dates ------------------------------------------------------------------
def week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() - WEEK_START) % 7)


def week_dates(d: date) -> list[date]:
    first = week_start(d)
    return [first + timedelta(days=i) for i in range(7)]


def month_grid(year: int, month: int) -> list[date]:
    """42 consecutive dates (6 weeks) starting on the week-start before the 1st."""
    first = week_start(date(year, month, 1))
    return [first + timedelta(days=i) for i in range(42)]


def shift_month(d: date, delta: int) -> date:
    idx = d.year * 12 + (d.month - 1) + delta
    y, m = divmod(idx, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def day_bounds(d: date, ref: datetime) -> tuple[datetime, datetime]:
    return agenda.midnight(d, ref), agenda.midnight(d + timedelta(days=1), ref)


def to_local(dt: datetime, ref: datetime) -> datetime:
    zone = agenda.zone(ref)
    return dt.astimezone(zone) if zone else dt.astimezone()


# -- titles / text ------------------------------------------------------------
def month_short(d: date) -> str:
    return d.strftime("%b")


def title_for(view: str, anchor: date) -> str:
    if view == "month":
        return f"{month_short(anchor)} {anchor.year}"
    if view == "week":
        a, b = week_dates(anchor)[0], week_dates(anchor)[-1]
        if a.year != b.year:
            return f"{month_short(a)} {a.day}, {a.year} – {month_short(b)} {b.day}, {b.year}"
        if a.month != b.month:
            return f"{month_short(a)} {a.day} – {month_short(b)} {b.day}, {b.year}"
        return f"{month_short(a)} {a.day} – {b.day}, {b.year}"
    return f"{anchor:%a}, {month_short(anchor)} {anchor.day}, {anchor.year}"


clock_text = agenda.clock_text   # one 12-hour format for the UI and the toasts


def short_time(dt: datetime) -> str:
    suffix = "a" if dt.hour < 12 else "p"
    t = f"{dt.hour % 12 or 12}" + (f":{dt.minute:02d}" if dt.minute else "")
    return t + suffix


def _day_text(d: date, with_year: bool = True) -> str:
    return f"{d:%a}, {month_short(d)} {d.day}" + (f", {d.year}" if with_year else "")


def format_range(ev: Event, ref: datetime) -> str:
    if ev.all_day:
        first = ev.start.date()
        last = max(first, ev.end.date() - timedelta(days=1))
        if last == first:
            return f"{_day_text(first)} · All day"
        return f"{_day_text(first, False)} – {_day_text(last)} · All day"
    s, e = to_local(ev.start, ref), to_local(ev.end, ref)
    if s.date() == e.date() or (e.time() == datetime.min.time() and
                                e.date() == s.date() + timedelta(days=1)):
        return f"{_day_text(s.date())} · {clock_text(s)} – {clock_text(e)}"
    return f"{_day_text(s.date(), False)}, {clock_text(s)} – {_day_text(e.date())}, {clock_text(e)}"


# -- events per day -----------------------------------------------------------
def event_days(ev: Event, ref: datetime) -> list[date]:
    """Local dates an event covers. All-day end and timed end are exclusive, so a
    timed event ending exactly at midnight does not appear on the next day."""
    if ev.all_day:
        first, last = ev.start.date(), ev.end.date() - timedelta(days=1)
    else:
        first = to_local(ev.start, ref).date()
        last = to_local(ev.end - timedelta(microseconds=1), ref).date() \
            if ev.end > ev.start else first
    last = max(first, last)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def events_by_day(events: list[Event], dates: list[date], ref: datetime) -> dict[date, list[Event]]:
    """Group events onto the given dates; all-day first, then by start, then title."""
    out: dict[date, list[Event]] = {d: [] for d in dates}
    for ev in events:
        for d in event_days(ev, ref):
            if d in out:
                out[d].append(ev)
    for lst in out.values():
        lst.sort(key=lambda e: (not e.all_day, e.start, e.title))
    return out


def more_count(total: int, limit: int = MAX_CHIPS) -> int:
    return max(0, total - limit)


def split_for_month(day_events: list[Event], limit: int = MAX_CHIPS) -> tuple[list[Event], int]:
    return day_events[:limit], more_count(len(day_events), limit)


# -- timeline blocks ----------------------------------------------------------
@dataclass
class Block:
    event: Event
    start_min: int   # wall-clock minutes from the day's midnight
    end_min: int
    col: int = 0
    ncols: int = 1


def pack_columns(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Assign side-by-side columns to (start, end) spans. Returns (col, ncols) per
    input span; ncols is shared by every span in the same overlap cluster."""
    order = sorted(range(len(spans)), key=lambda i: (spans[i][0], spans[i][1], i))
    result: list[tuple[int, int]] = [(0, 1)] * len(spans)
    cluster: list[int] = []
    col_ends: list[int] = []
    cluster_end = -1

    def close():
        n = len(col_ends)
        for i in cluster:
            result[i] = (result[i][0], n)

    for i in order:
        s, e = spans[i]
        if cluster and s >= cluster_end:
            close()
            cluster, col_ends, cluster_end = [], [], -1
        for c, end in enumerate(col_ends):
            if end <= s:
                col_ends[c] = e
                col = c
                break
        else:
            col_ends.append(e)
            col = len(col_ends) - 1
        result[i] = (col, 1)
        cluster.append(i)
        cluster_end = max(cluster_end, e)
    if cluster:
        close()
    return result


def day_blocks(events: list[Event], day: date, ref: datetime) -> list[Block]:
    """Timed events clipped to `day`, in wall-clock minutes, with column packing."""
    blocks: list[Block] = []
    for ev in events:
        if ev.all_day:
            continue
        s, e = to_local(ev.start, ref), to_local(ev.end, ref)
        start = 0 if s.date() < day else s.hour * 60 + s.minute
        end = DAY_MIN if e.date() > day else e.hour * 60 + e.minute
        if e.date() < day or (e.date() == day and end == 0 and e > s):
            continue  # ended at or before this midnight
        end = max(end, start + MIN_BLOCK_MIN)
        if end > DAY_MIN:
            start, end = max(0, DAY_MIN - max(MIN_BLOCK_MIN, end - start)), DAY_MIN
        blocks.append(Block(ev, start, end))
    for b, (col, n) in zip(blocks, pack_columns([(b.start_min, b.end_min) for b in blocks])):
        b.col, b.ncols = col, n
    return blocks
