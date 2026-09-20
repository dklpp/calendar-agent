"""Slot search: busy subtraction, working hours, buffers, conflicts."""

from __future__ import annotations

import datetime as dt

from app.config import WorkingHours, WorkingWindow
from app.slots import (
    conflicts_with,
    find_free_slots,
    merge_intervals,
    subtract,
    working_window,
)
from app.timezones import combine

TOR = "America/Toronto"
VIL = "Europe/Vilnius"
DAY = dt.date(2026, 9, 24)  # a Thursday


def at(hour: int, minute: int = 0, tz: str = TOR, day: dt.date = DAY) -> dt.datetime:
    return combine(day, dt.time(hour, minute), tz).when


def hours(**kw) -> WorkingHours:
    return WorkingHours(**kw)


class TestIntervalMath:
    def test_merge_overlapping(self):
        merged = merge_intervals([(at(9), at(11)), (at(10), at(12))])
        assert merged == [(at(9), at(12))]

    def test_merge_leaves_disjoint_alone(self):
        assert len(merge_intervals([(at(9), at(10)), (at(11), at(12))])) == 2

    def test_subtract_carves_a_hole(self):
        assert subtract((at(9), at(17)), [(at(12), at(13))]) == [
            (at(9), at(12)),
            (at(13), at(17)),
        ]

    def test_subtract_with_no_busy_returns_whole_window(self):
        assert subtract((at(9), at(17)), []) == [(at(9), at(17))]

    def test_fully_booked_window_yields_nothing(self):
        assert subtract((at(9), at(17)), [(at(8), at(18))]) == []

    def test_busy_outside_window_is_ignored(self):
        assert subtract((at(9), at(12)), [(at(14), at(15))]) == [(at(9), at(12))]


class TestWorkingWindow:
    def test_weekend_is_skipped(self):
        saturday = dt.date(2026, 9, 26)
        assert working_window(saturday, TOR, hours()) is None

    def test_weekend_allowed_when_configured(self):
        saturday = dt.date(2026, 9, 26)
        assert working_window(saturday, TOR, hours(weekdays_only=False)) is not None

    def test_per_zone_override_applies(self):
        """A Vilnius meeting gets the Vilnius window, not the default one."""
        h = hours(
            default=WorkingWindow(start=dt.time(9, 0), end=dt.time(18, 0)),
            by_zone={VIL: WorkingWindow(start=dt.time(9, 0), end=dt.time(19, 0))},
        )
        _, end = working_window(DAY, VIL, h)
        assert end.astimezone(end.tzinfo).hour == 19


class TestFindFreeSlots:
    def test_empty_day_offers_slots_from_the_start_of_the_window(self):
        slots = find_free_slots(DAY, TOR, 60, [], hours(), limit=2)
        assert slots[0] == (at(9), at(10))
        assert slots[1] == (at(10), at(11))

    def test_buffer_is_respected_around_existing_meetings(self):
        """With a 15-minute buffer, a 10:00-11:00 meeting blocks 09:45-11:15,
        so a 60-minute slot cannot start at 09:00 (it would run to 10:00 and
        touch the buffer). The first offer should be after the buffer."""
        busy = [(at(10), at(11))]
        slots = find_free_slots(DAY, TOR, 60, busy, hours(buffer_minutes=15), limit=3)
        for start, end in slots:
            assert end <= at(9, 45) or start >= at(11, 15)

    def test_duration_is_honoured(self):
        slots = find_free_slots(DAY, TOR, 30, [], hours(), limit=1)
        assert slots[0][1] - slots[0][0] == dt.timedelta(minutes=30)

    def test_fully_booked_day_offers_nothing(self):
        busy = [(at(8), at(19))]
        assert find_free_slots(DAY, TOR, 60, busy, hours()) == []

    def test_no_slot_shorter_than_requested_is_returned(self):
        """A 30-minute gap must not be offered for a 60-minute meeting."""
        busy = [(at(9), at(12)), (at(12, 30), at(18))]
        assert find_free_slots(DAY, TOR, 60, busy, hours(buffer_minutes=0)) == []

    def test_past_slots_are_not_proposed(self):
        now = at(14, 0)
        slots = find_free_slots(DAY, TOR, 60, [], hours(), now=now, limit=3)
        assert all(start >= now for start, _ in slots)

    def test_slots_are_distinct_not_a_minute_apart(self):
        slots = find_free_slots(DAY, TOR, 60, [], hours(), limit=3)
        starts = [s for s, _ in slots]
        assert len(set(starts)) == 3
        assert starts[1] - starts[0] >= dt.timedelta(minutes=60)


class TestConflicts:
    def test_overlap_is_a_conflict(self):
        busy = [(at(15), at(16))]
        assert conflicts_with((at(15, 30), at(16, 30)), busy) == busy

    def test_exact_double_booking_is_a_conflict(self):
        busy = [(at(15), at(16))]
        assert conflicts_with((at(15), at(16)), busy) == busy

    def test_abutting_meetings_are_not_a_conflict(self):
        """Back-to-back is legitimate. Refusing it would be wrong, even though
        slot *suggestions* apply a buffer."""
        busy = [(at(15), at(16))]
        assert conflicts_with((at(16), at(17)), busy) == []
        assert conflicts_with((at(14), at(15)), busy) == []

    def test_free_time_is_not_a_conflict(self):
        assert conflicts_with((at(9), at(10)), [(at(15), at(16))]) == []

    def test_conflict_detection_works_across_zones(self):
        """15:00 Vilnius on 2026-03-19 is 09:00 Toronto (6h divergence window),
        so it collides with a 09:00 Toronto meeting -- a collision that naive
        7h arithmetic would miss entirely."""
        march = dt.date(2026, 3, 19)
        toronto_meeting = (
            at(9, 0, TOR, march),
            at(10, 0, TOR, march),
        )
        vilnius_proposal = (
            at(15, 0, VIL, march),
            at(16, 0, VIL, march),
        )
        assert conflicts_with(vilnius_proposal, [toronto_meeting]) == [toronto_meeting]
