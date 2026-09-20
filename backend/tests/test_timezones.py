"""The DST-divergence tests. These are the point of the whole module.

Every constant below was verified against `zoneinfo` rather than reasoned
about, because reasoning about DST is exactly how these bugs get written.

2026 transitions:
    America/Toronto  spring forward Mar  8 02:00 -> 03:00
                     fall back      Nov  1 02:00 -> 01:00
    Europe/Vilnius   spring forward Mar 29 03:00 -> 04:00
                     fall back      Oct 25 04:00 -> 03:00
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.timezones import (
    UnknownTimezone,
    combine,
    format_dual,
    offset_gap_hours,
    resolve_timezone,
    to_zone,
    zone_label,
)

TOR = "America/Toronto"
VIL = "Europe/Vilnius"


def _noon_utc(y: int, m: int, d: int) -> dt.datetime:
    return dt.datetime(y, m, d, 12, 0, tzinfo=dt.timezone.utc)


class TestDivergence:
    """Toronto <-> Vilnius is 7h apart most of the year, 6h twice a year."""

    @pytest.mark.parametrize(
        ("date", "expected_gap", "why"),
        [
            ((2026, 1, 15), 7.0, "both on standard time"),
            ((2026, 3, 7), 7.0, "day before North America springs forward"),
            ((2026, 3, 15), 6.0, "DIVERGENT: Toronto on DST, Vilnius not yet"),
            ((2026, 3, 28), 6.0, "last full day of the March divergence"),
            ((2026, 3, 30), 7.0, "Europe has caught up"),
            ((2026, 7, 15), 7.0, "both on summer time"),
            ((2026, 10, 24), 7.0, "day before Europe falls back"),
            ((2026, 10, 28), 6.0, "DIVERGENT: Vilnius back to EET, Toronto still EDT"),
            ((2026, 10, 31), 6.0, "last full day of the October divergence"),
            ((2026, 11, 2), 7.0, "North America has caught up"),
        ],
    )
    def test_gap(self, date, expected_gap, why):
        assert offset_gap_hours(_noon_utc(*date), TOR, VIL) == expected_gap, why

    def test_gap_is_never_eight_hours(self):
        """An 8h gap would need Toronto on winter time while Vilnius is on
        summer time. Current rules never produce that -- if this ever fails,
        a government changed the rules and the synonym table needs review."""
        day = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)
        seen = set()
        while day.year == 2026:
            seen.add(offset_gap_hours(day, TOR, VIL))
            day += dt.timedelta(days=1)
        assert seen == {6.0, 7.0}

    def test_same_wall_clock_maps_to_different_instants_across_the_boundary(self):
        """15:00 Vilnius is 08:00 in Toronto normally, but 09:00 mid-March."""
        normal = combine(dt.date(2026, 7, 16), dt.time(15, 0), VIL).when
        assert to_zone(normal, TOR).hour == 8

        divergent = combine(dt.date(2026, 3, 19), dt.time(15, 0), VIL).when
        assert to_zone(divergent, TOR).hour == 9


class TestResolveTimezone:
    @pytest.mark.parametrize(
        "phrase",
        ["Lithuanian time", "lithuania", "Vilnius", "3pm LT", "EEST", "baltic time"],
    )
    def test_lithuanian_synonyms(self, phrase):
        assert resolve_timezone(phrase) == VIL

    @pytest.mark.parametrize(
        "phrase", ["eastern time", "Toronto", "Canadian time", "EDT", "ET"]
    )
    def test_eastern_synonyms(self, phrase):
        assert resolve_timezone(phrase) == TOR

    def test_iana_name_passes_through(self):
        assert resolve_timezone("Europe/Vilnius") == VIL

    def test_bad_iana_name_rejected(self):
        with pytest.raises(UnknownTimezone):
            resolve_timezone("Europe/Atlantis")

    def test_falls_back_to_default_when_empty(self):
        assert resolve_timezone(None, default=TOR) == TOR
        assert resolve_timezone("", default=TOR) == TOR

    def test_unrecognised_phrase_raises_rather_than_guessing(self):
        """Booking in a guessed zone is worse than asking a question."""
        with pytest.raises(UnknownTimezone):
            resolve_timezone("somewhere sunny", default=TOR)

    def test_label(self):
        assert zone_label(VIL) == "Vilnius"
        assert zone_label("America/New_York") == "New York"


class TestDstAnomalies:
    def test_nonexistent_time_is_shifted_and_flagged(self):
        """02:30 on 2026-03-08 never happens in Toronto."""
        result = combine(dt.date(2026, 3, 8), dt.time(2, 30), TOR)
        assert result.warning is not None
        assert "does not exist" in result.warning
        assert to_zone(result.when, TOR).hour == 3

    def test_nonexistent_time_in_vilnius(self):
        """03:30 on 2026-03-29 never happens in Vilnius."""
        result = combine(dt.date(2026, 3, 29), dt.time(3, 30), VIL)
        assert result.warning is not None
        assert to_zone(result.when, VIL).hour == 4

    def test_ambiguous_time_picks_first_occurrence_and_flags(self):
        """01:30 on 2026-11-01 happens twice in Toronto."""
        result = combine(dt.date(2026, 11, 1), dt.time(1, 30), TOR)
        assert result.warning is not None
        assert "occurs twice" in result.warning
        assert result.when.utcoffset() == dt.timedelta(hours=-4)  # EDT, the earlier one

    def test_ambiguous_time_in_vilnius(self):
        """03:30 on 2026-10-25 happens twice in Vilnius."""
        result = combine(dt.date(2026, 10, 25), dt.time(3, 30), VIL)
        assert result.warning is not None
        assert result.when.utcoffset() == dt.timedelta(hours=3)  # EEST

    def test_ordinary_time_carries_no_warning(self):
        assert combine(dt.date(2026, 9, 24), dt.time(15, 0), VIL).warning is None


class TestFormatting:
    def test_dual_rendering_shows_both_zones(self):
        when = combine(dt.date(2026, 9, 24), dt.time(15, 0), VIL).when
        out = format_dual(when, VIL, TOR)
        assert "15:00 EEST Vilnius" in out
        assert "08:00 EDT Toronto" in out
        assert "Thu 24 Sep" in out

    def test_second_zone_gets_its_own_date_when_the_day_differs(self):
        """A 09:00 Toronto meeting is 16:00 Vilnius -- same day. But 20:00
        Toronto is 03:00 the *next* day in Vilnius, and hiding that would be
        actively misleading."""
        when = combine(dt.date(2026, 9, 24), dt.time(20, 0), TOR).when
        out = format_dual(when, TOR, VIL)
        assert "Thu 24 Sep" in out
        assert "Fri 25 Sep" in out
        assert "03:00" in out

    def test_naive_datetimes_are_refused(self):
        with pytest.raises(ValueError):
            to_zone(dt.datetime(2026, 9, 24, 15, 0), VIL)
        with pytest.raises(ValueError):
            offset_gap_hours(dt.datetime(2026, 9, 24, 15, 0), TOR, VIL)
