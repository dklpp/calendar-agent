"""Free-slot search. Deliberately pure so it can be tested without Google.

Everything here takes busy intervals as input and returns free intervals; the
Google round-trip lives in calendar_client.py.
"""

from __future__ import annotations

import datetime as dt

from app.config import WorkingHours
from app.timezones import combine, to_zone

Interval = tuple[dt.datetime, dt.datetime]


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Sort and coalesce overlapping or touching intervals."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract(window: Interval, busy: list[Interval]) -> list[Interval]:
    """The parts of ``window`` not covered by any interval in ``busy``."""
    free: list[Interval] = []
    cursor, window_end = window
    for busy_start, busy_end in merge_intervals(busy):
        if busy_end <= cursor or busy_start >= window_end:
            continue
        if busy_start > cursor:
            free.append((cursor, min(busy_start, window_end)))
        cursor = max(cursor, busy_end)
        if cursor >= window_end:
            break
    if cursor < window_end:
        free.append((cursor, window_end))
    return free


def working_window(
    day: dt.date, tz_name: str, hours: WorkingHours
) -> Interval | None:
    """The acceptable meeting window for ``day``, as aware datetimes.

    Returns None for a weekend when ``weekdays_only`` is set, which is how the
    caller skips days rather than proposing a Saturday morning.
    """
    if hours.weekdays_only and day.weekday() >= 5:
        return None
    window = hours.window_for(tz_name)
    start = combine(day, window.start, tz_name).when
    end = combine(day, window.end, tz_name).when
    return (start, end)


def find_free_slots(
    day: dt.date,
    tz_name: str,
    duration_minutes: int,
    busy: list[Interval],
    hours: WorkingHours,
    *,
    now: dt.datetime | None = None,
    limit: int = 3,
) -> list[Interval]:
    """Candidate start/end pairs on ``day`` that fit ``duration_minutes``.

    A buffer is applied around existing meetings so the agent does not propose
    a slot butting directly against another call.
    """
    window = working_window(day, tz_name, hours)
    if window is None:
        return []

    # Never propose a slot in the past.
    if now is not None and now > window[0]:
        window = (max(window[0], now), window[1])
        if window[0] >= window[1]:
            return []

    buffer = dt.timedelta(minutes=hours.buffer_minutes)
    padded = [(start - buffer, end + buffer) for start, end in busy]

    needed = dt.timedelta(minutes=duration_minutes)
    slots: list[Interval] = []
    for free_start, free_end in subtract(window, padded):
        cursor = free_start
        while cursor + needed <= free_end:
            slots.append((cursor, cursor + needed))
            if len(slots) >= limit:
                return slots
            # Step by the meeting length so suggestions are distinct rather
            # than three variations a minute apart.
            cursor += needed
    return slots


def find_slots_across_days(
    start_day: dt.date,
    tz_name: str,
    duration_minutes: int,
    busy: list[Interval],
    hours: WorkingHours,
    *,
    days: int = 5,
    now: dt.datetime | None = None,
    limit: int = 3,
    per_day: int = 2,
) -> list[Interval]:
    """Search forward over several days, spreading suggestions out."""
    found: list[Interval] = []
    for offset in range(days):
        day = start_day + dt.timedelta(days=offset)
        for slot in find_free_slots(
            day, tz_name, duration_minutes, busy, hours, now=now, limit=per_day
        ):
            found.append(slot)
            if len(found) >= limit:
                return found
    return found


def conflicts_with(proposed: Interval, busy: list[Interval]) -> list[Interval]:
    """Busy intervals that genuinely overlap ``proposed``.

    No buffer here -- a buffer is a preference when *suggesting* a time, but a
    meeting that merely abuts another is not a double-booking and refusing it
    would be wrong.
    """
    start, end = proposed
    return [b for b in busy if b[0] < end and b[1] > start]
