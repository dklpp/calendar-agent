"""Recurrence rendering and DST-stable expansion."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.recurrence import Recurrence, expand_occurrences
from app.timezones import to_zone

VIL = "Europe/Vilnius"
TOR = "America/Toronto"


class TestRruleRendering:
    def test_simple_weekly(self):
        assert Recurrence(freq="weekly", byday=["TH"]).to_rrule() == (
            "RRULE:FREQ=WEEKLY;BYDAY=TH"
        )

    def test_every_other_thursday(self):
        assert Recurrence(freq="weekly", interval=2, byday=["TH"]).to_rrule() == (
            "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TH"
        )

    def test_last_friday_of_the_month(self):
        assert Recurrence(
            freq="monthly", byday=["FR"], bysetpos=-1
        ).to_rrule() == "RRULE:FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1"

    def test_multiple_weekdays(self):
        assert Recurrence(freq="weekly", byday=["MO", "WE"]).to_rrule() == (
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE"
        )

    def test_until_is_utc_with_z_suffix(self):
        """RFC 5545 requires UNTIL on a timed event to be UTC. Google rejects
        a bare date, and does so with an unhelpful error."""
        rule = Recurrence(freq="weekly", byday=["TH"], until=dt.date(2026, 12, 31))
        assert rule.to_rrule().endswith("UNTIL=20261231T235959Z")

    def test_count(self):
        assert "COUNT=10" in Recurrence(freq="weekly", byday=["TH"], count=10).to_rrule()

    def test_interval_one_is_omitted(self):
        """INTERVAL=1 is the default; emitting it is noise."""
        assert "INTERVAL" not in Recurrence(freq="weekly", byday=["TH"]).to_rrule()


class TestValidation:
    def test_count_and_until_together_rejected(self):
        with pytest.raises(ValidationError):
            Recurrence(
                freq="weekly", byday=["TH"], count=5, until=dt.date(2026, 12, 31)
            )

    def test_bad_weekday_code_rejected(self):
        with pytest.raises(ValidationError):
            Recurrence(freq="weekly", byday=["THURSDAY"])

    def test_bysetpos_requires_monthly(self):
        with pytest.raises(ValidationError):
            Recurrence(freq="weekly", byday=["FR"], bysetpos=-1)

    def test_bysetpos_requires_byday(self):
        with pytest.raises(ValidationError):
            Recurrence(freq="monthly", bysetpos=-1)


class TestDescribe:
    @pytest.mark.parametrize(
        ("rule", "expected"),
        [
            (Recurrence(freq="weekly", byday=["TH"]), "Thursday every week"),
            (
                Recurrence(freq="weekly", interval=2, byday=["TH"]),
                "Thursday every 2 weeks",
            ),
            (
                Recurrence(freq="monthly", byday=["FR"], bysetpos=-1),
                "the last Friday of each month",
            ),
        ],
    )
    def test_plain_english(self, rule, expected):
        assert rule.describe() == expected

    def test_until_appears_in_description(self):
        rule = Recurrence(freq="weekly", byday=["TH"], until=dt.date(2026, 12, 31))
        assert "until 31 Dec 2026" in rule.describe()


class TestExpansion:
    def test_every_other_thursday_lands_on_thursdays_two_weeks_apart(self):
        occurrences = expand_occurrences(
            Recurrence(freq="weekly", interval=2, byday=["TH"]),
            dt.date(2026, 9, 24),
            dt.time(15, 0),
            VIL,
            limit=3,
        )
        assert [o.date() for o in occurrences] == [
            dt.date(2026, 9, 24),
            dt.date(2026, 10, 8),
            dt.date(2026, 10, 22),
        ]

    def test_wall_clock_time_survives_a_dst_transition(self):
        """The core guarantee: a 15:00 Vilnius series stays at 15:00 Vilnius
        after the clocks change. The UTC offset moves instead."""
        occurrences = expand_occurrences(
            Recurrence(freq="weekly", byday=["TH"]),
            dt.date(2026, 10, 15),  # spans the Oct 25 Vilnius fall-back
            dt.time(15, 0),
            VIL,
            limit=4,
        )
        assert all(to_zone(o, VIL).hour == 15 for o in occurrences)
        offsets = {o.utcoffset() for o in occurrences}
        assert offsets == {dt.timedelta(hours=3), dt.timedelta(hours=2)}

    def test_the_toronto_view_of_that_series_shifts_by_an_hour(self):
        """Consequence of divergence, and exactly what the agent must show the
        user: a fixed 15:00 Vilnius slot is 08:00 in Toronto before Oct 25 and
        09:00 after it, because Vilnius fell back a week earlier."""
        occurrences = expand_occurrences(
            Recurrence(freq="weekly", byday=["TH"]),
            dt.date(2026, 10, 15),
            dt.time(15, 0),
            VIL,
            limit=3,
        )
        toronto_hours = [to_zone(o, TOR).hour for o in occurrences]
        assert toronto_hours == [8, 8, 9]

    def test_last_friday_expansion(self):
        occurrences = expand_occurrences(
            Recurrence(freq="monthly", byday=["FR"], bysetpos=-1),
            dt.date(2026, 9, 25),
            dt.time(10, 0),
            TOR,
            limit=3,
        )
        assert [o.date() for o in occurrences] == [
            dt.date(2026, 9, 25),
            dt.date(2026, 10, 30),
            dt.date(2026, 11, 27),
        ]

    def test_count_limits_the_series(self):
        occurrences = expand_occurrences(
            Recurrence(freq="weekly", byday=["TH"], count=2),
            dt.date(2026, 9, 24),
            dt.time(15, 0),
            VIL,
            limit=10,
        )
        assert len(occurrences) == 2
