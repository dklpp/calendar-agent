"""Agent orchestration, with a fake calendar and a stubbed parser.

These cover the property that matters most: nothing reaches the calendar
without an explicit confirmation.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app import db
from app.agent import Agent
from app.config import Preferences
from app.models import EventSpec, Intent
from app.slots import Interval
from app.timezones import combine

TOR = "America/Toronto"
VIL = "Europe/Vilnius"
THURSDAY = dt.date(2026, 9, 24)


class FakeCalendar:
    """Records writes instead of performing them."""

    def __init__(self, busy: list[Interval] | None = None, events=None):
        self._busy = busy or []
        self._events = events or []
        self.created: list = []
        self.deleted: list[str] = []
        self.moved: list[tuple] = []

    def busy_intervals(self, start, end):
        return [b for b in self._busy if b[0] < end and b[1] > start]

    def list_events(self, start, end, max_results=100):
        return self._events

    def create_event(self, event):
        self.created.append(event)
        return {"id": f"evt{len(self.created)}"}

    def delete_event(self, event_id):
        self.deleted.append(event_id)

    def move_event(self, event_id, start, end, tz_name):
        self.moved.append((event_id, start, end, tz_name))
        return {"id": event_id}


@pytest.fixture
def prefs():
    return Preferences(primary_tz=TOR, secondary_tz=VIL)


@pytest.fixture
def make_agent(tmp_path, prefs):
    def _make(calendar):
        return Agent(
            calendar=calendar, conn=db.connect(tmp_path / "a.db"), prefs=prefs
        )

    return _make


def stub_parser(monkeypatch, intent: Intent):
    monkeypatch.setattr("app.agent.parse_utterance", lambda *a, **k: intent)


def at(hour, minute=0, tz=TOR, day=THURSDAY):
    return combine(day, dt.time(hour, minute), tz).when


def create_intent(**kw):
    defaults = dict(
        title="Acme Corp sync",
        company="Acme Corp",
        date=THURSDAY,
        time=dt.time(15, 0),
        timezone=VIL,
    )
    defaults.update(kw)
    return Intent(kind="create_event", event=EventSpec(**defaults))


class TestNothingIsWrittenWithoutConfirmation:
    def test_create_proposes_but_does_not_write(self, make_agent, monkeypatch):
        calendar = FakeCalendar()
        agent = make_agent(calendar)
        stub_parser(monkeypatch, create_intent())

        reply = agent.handle("meeting Thursday 3pm Lithuanian time for Acme")

        assert reply.kind == "confirm"
        assert reply.choices, "a confirm button should be offered"
        assert calendar.created == [], "nothing may reach the calendar yet"

    def test_confirming_writes_exactly_once(self, make_agent, monkeypatch):
        calendar = FakeCalendar()
        agent = make_agent(calendar)
        stub_parser(monkeypatch, create_intent())

        token = agent.handle("...").choices[0].token
        done = agent.confirm(token)

        assert done.kind == "done"
        assert len(calendar.created) == 1

    def test_double_tapping_confirm_does_not_double_book(
        self, make_agent, monkeypatch
    ):
        calendar = FakeCalendar()
        agent = make_agent(calendar)
        stub_parser(monkeypatch, create_intent())

        token = agent.handle("...").choices[0].token
        agent.confirm(token)
        second = agent.confirm(token)

        assert len(calendar.created) == 1
        assert second.kind == "error"

    def test_expired_confirmation_is_refused(self, make_agent, monkeypatch, prefs):
        calendar = FakeCalendar()
        agent = make_agent(calendar)
        agent.prefs = Preferences(
            primary_tz=TOR, secondary_tz=VIL, confirm_ttl_minutes=-1
        )
        stub_parser(monkeypatch, create_intent())

        token = agent.handle("...").choices[0].token
        assert agent.confirm(token).kind == "error"
        assert calendar.created == []


class TestConflicts:
    def test_busy_slot_is_refused_and_alternatives_offered(
        self, make_agent, monkeypatch
    ):
        """15:00 Vilnius on 2026-09-24 is 08:00 Toronto."""
        busy = [(at(8), at(9))]
        calendar = FakeCalendar(busy=busy)
        agent = make_agent(calendar)
        stub_parser(monkeypatch, create_intent())

        reply = agent.handle("book Thursday 3pm Lithuanian time")

        assert reply.kind == "conflict"
        assert calendar.created == []
        assert reply.choices, "alternatives should be offered"

    def test_an_offered_alternative_is_actually_free(self, make_agent, monkeypatch):
        busy = [(at(8), at(9))]
        calendar = FakeCalendar(busy=busy)
        agent = make_agent(calendar)
        stub_parser(monkeypatch, create_intent())

        reply = agent.handle("...")
        agent.confirm(reply.choices[0].token)

        created = calendar.created[0]
        assert not any(b[0] < created.end and b[1] > created.start for b in busy)

    def test_free_slot_goes_straight_to_confirmation(self, make_agent, monkeypatch):
        calendar = FakeCalendar(busy=[(at(13), at(14))])
        agent = make_agent(calendar)
        stub_parser(monkeypatch, create_intent())

        assert agent.handle("...").kind == "confirm"

    def test_recurring_series_checks_later_occurrences_too(
        self, make_agent, monkeypatch
    ):
        """The first Thursday is free but the third is not. A series check that
        only looked at occurrence one would happily double-book."""
        third = THURSDAY + dt.timedelta(days=14)
        busy = [(at(8, 0, TOR, third), at(9, 0, TOR, third))]
        calendar = FakeCalendar(busy=busy)
        agent = make_agent(calendar)
        stub_parser(
            monkeypatch,
            create_intent(recurrence={"freq": "weekly", "byday": ["TH"]}),
        )

        reply = agent.handle("every Thursday 3pm Lithuanian time")

        assert reply.kind == "conflict"
        assert calendar.created == []


class TestOtherIntents:
    def test_unknown_asks_rather_than_guessing(self, make_agent, monkeypatch):
        calendar = FakeCalendar()
        agent = make_agent(calendar)
        stub_parser(
            monkeypatch,
            Intent(kind="unknown", clarification_needed="Which Thursday did you mean?"),
        )

        reply = agent.handle("uh, thursday something")

        assert reply.kind == "question"
        assert "Thursday" in reply.text
        assert calendar.created == []

    def test_empty_agenda_says_so(self, make_agent, monkeypatch):
        agent = make_agent(FakeCalendar())
        stub_parser(monkeypatch, Intent(kind="query_agenda"))
        assert "Nothing scheduled" in agent.handle("what do I have today").text

    def test_agenda_renders_both_timezones(self, make_agent, monkeypatch):
        events = [
            {
                "id": "e1",
                "summary": "Acme Corp sync",
                "start": {"dateTime": at(8).isoformat()},
                "end": {"dateTime": at(9).isoformat()},
            }
        ]
        agent = make_agent(FakeCalendar(events=events))
        stub_parser(monkeypatch, Intent(kind="query_agenda"))

        text = agent.handle("what do I have today").text

        assert "08:00 EDT Toronto" in text
        assert "15:00 EEST Vilnius" in text

    def test_cancel_requires_confirmation(self, make_agent, monkeypatch):
        events = [
            {
                "id": "e1",
                "summary": "Acme Corp sync",
                "start": {"dateTime": at(8).isoformat()},
                "end": {"dateTime": at(9).isoformat()},
            }
        ]
        calendar = FakeCalendar(events=events)
        agent = make_agent(calendar)
        stub_parser(
            monkeypatch, Intent(kind="cancel", target_description="the Acme call")
        )

        reply = agent.handle("cancel the Acme call")
        assert reply.kind == "confirm"
        assert calendar.deleted == []

        agent.confirm(reply.choices[0].token)
        assert calendar.deleted == ["e1"]

    def test_parse_failure_degrades_gracefully(self, make_agent, monkeypatch):
        agent = make_agent(FakeCalendar())

        def boom(*a, **k):
            raise RuntimeError("API down")

        monkeypatch.setattr("app.agent.parse_utterance", boom)
        reply = agent.handle("anything")
        assert reply.kind == "error"
