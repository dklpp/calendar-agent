"""Golden utterances -> expected intent. Hits the real Claude API.

Run with:  uv run pytest -m costs_money
Skip with: uv run pytest -m 'not costs_money'   (the default in a fast loop)

These are the regression net for prompt changes. Each case asserts only the
fields that matter, so harmless wording differences in the title do not fail
the suite.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.config import Preferences
from app.models import Intent
from app.parser import parse_utterance

pytestmark = pytest.mark.costs_money

TOR = "America/Toronto"
VIL = "Europe/Vilnius"

# A fixed "now" so relative dates ("this Thursday") are deterministic.
NOW = dt.datetime(2026, 9, 21, 14, 0, tzinfo=dt.timezone.utc)  # a Monday
PREFS = Preferences(primary_tz=TOR, secondary_tz=VIL)


def parse(text: str) -> Intent:
    return parse_utterance(text, now=NOW, prefs=PREFS)


class TestIntentClassification:
    @pytest.mark.parametrize(
        ("utterance", "expected_kind"),
        [
            ("I have a meeting this Thursday 3pm Lithuanian time for Acme Corp",
             "create_event"),
            ("what meetings do I have today?", "query_agenda"),
            ("what do I have in an hour?", "query_agenda"),
            ("what available time slots do I have on Thursday?", "find_slots"),
            ("cancel the Acme call on Thursday", "cancel"),
            ("move the Acme call to Friday at 4pm eastern", "reschedule"),
        ],
    )
    def test_kind(self, utterance, expected_kind):
        assert parse(utterance).kind == expected_kind


class TestTimezoneExtraction:
    def test_lithuanian_time_is_not_converted(self):
        """The critical one. The model must report 15:00 Vilnius, NOT the
        Toronto equivalent. Converting here would double-convert downstream."""
        intent = parse("meeting this Thursday at 3pm Lithuanian time for Acme")
        assert intent.event.time == dt.time(15, 0)
        assert intent.event.timezone == VIL

    def test_eastern_time_maps_to_toronto(self):
        intent = parse("call Friday 10am eastern time")
        assert intent.event.time == dt.time(10, 0)
        assert intent.event.timezone == TOR

    def test_unstated_timezone_is_left_null(self):
        """Null means 'unstated', which lets our code apply the default and
        tell the user it did so. A guess here would be silent."""
        assert parse("meeting Thursday at 3pm").event.timezone is None

    def test_relative_date_resolves(self):
        """NOW is Monday 21 Sep 2026, so 'this Thursday' is the 24th."""
        assert parse("meeting this Thursday 3pm Vilnius time").event.date == (
            dt.date(2026, 9, 24)
        )


class TestRecurrence:
    def test_every_other_thursday(self):
        rec = parse("every other Thursday at 3pm Lithuanian time with Acme").event.recurrence
        assert rec is not None
        assert rec.freq == "weekly"
        assert rec.interval == 2
        assert rec.byday == ["TH"]

    def test_weekly(self):
        rec = parse("weekly standup every Monday 9am eastern").event.recurrence
        assert rec.freq == "weekly"
        assert rec.interval == 1
        assert rec.byday == ["MO"]

    def test_last_friday_of_the_month(self):
        rec = parse("board review the last Friday of every month at 2pm eastern").event.recurrence
        assert rec.freq == "monthly"
        assert rec.byday == ["FR"]
        assert rec.bysetpos == -1

    def test_one_off_meeting_has_no_recurrence(self):
        assert parse("meeting Thursday 3pm eastern").event.recurrence is None


class TestDetails:
    def test_company_is_captured(self):
        intent = parse("meeting Thursday 3pm Lithuanian time for Acme Corporation")
        assert "acme" in (intent.event.company or "").lower()

    def test_duration_is_captured(self):
        assert parse("30 minute call Thursday 3pm eastern").event.duration_minutes == 30

    def test_within_minutes_for_next_hour_queries(self):
        assert parse("what do I have in the next hour?").agenda.within_minutes == 60


class TestAmbiguity:
    def test_garbled_input_asks_rather_than_guessing(self):
        intent = parse("uh so the thing with the uh")
        assert intent.kind == "unknown"
        assert intent.clarification_needed
