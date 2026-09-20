"""Front-end-agnostic orchestration: utterance in, reply out.

Telegram and the macOS app are both thin clients over this module, so the
behaviour they offer stays identical.

The central safety property lives here: **nothing is written to the calendar
until the user confirms.** Voice -> transcription -> LLM is three fallible
steps chained together, and a misheard transcript that silently creates a
meeting is worse than one extra button tap.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app import db
from app.calendar_client import CalendarClient
from app.config import Preferences, get_preferences
from app.models import Intent, ResolvedEvent
from app.parser import parse_utterance, resolve_event
from app.recurrence import expand_occurrences
from app.slots import Interval, conflicts_with, find_slots_across_days
from app.timezones import UnknownTimezone, format_dual, resolve_timezone, to_zone

log = logging.getLogger(__name__)


@dataclass
class Choice:
    """One tappable option: an alternative time, or a confirmation."""

    label: str
    token: str


@dataclass
class AgentReply:
    text: str
    choices: list[Choice] = field(default_factory=list)
    kind: str = "info"  # info | confirm | conflict | question | error | done


class Agent:
    def __init__(
        self,
        calendar: CalendarClient | None = None,
        conn: sqlite3.Connection | None = None,
        prefs: Preferences | None = None,
    ) -> None:
        self.calendar = calendar or CalendarClient()
        self.conn = conn or db.connect()
        self.prefs = prefs or get_preferences()

    # ---- rendering ------------------------------------------------------

    def _dual(self, when: dt.datetime) -> str:
        return format_dual(when, self.prefs.primary_tz, self.prefs.secondary_tz)

    def _render_event_line(self, item: dict[str, Any]) -> str:
        start = item.get("start", {})
        if "dateTime" in start:
            when = dt.datetime.fromisoformat(start["dateTime"])
            return f"- {self._dual(when)} - {item.get('summary', '(no title)')}"
        return f"- {start.get('date')} (all day) - {item.get('summary', '(no title)')}"

    def _describe_proposal(self, event: ResolvedEvent) -> str:
        lines = [f"*{event.summary}*", self._dual(event.start)]
        minutes = int((event.end - event.start).total_seconds() // 60)
        lines.append(f"{minutes} minutes")
        if event.recurrence:
            lines.append(f"Repeats: {event.recurrence.describe()}")
        if event.attendees:
            lines.append("With: " + ", ".join(event.attendees))
        for warning in event.warnings:
            lines.append(f"Note: {warning}")
        return "\n".join(lines)

    # ---- entry points ---------------------------------------------------

    def handle(
        self, text: str, *, source: str = "telegram", chat_id: int | None = None
    ) -> AgentReply:
        """Parse an utterance and act on it (without writing anything yet)."""
        try:
            intent = parse_utterance(text, prefs=self.prefs)
        except Exception:
            log.exception("parse failed")
            db.record(self.conn, source=source, transcript=text, outcome="parse_error")
            return AgentReply(
                "I could not understand that. Could you say it again?", kind="error"
            )

        db.record(
            self.conn, source=source, transcript=text, intent=intent.kind
        )

        handlers = {
            "create_event": self._create,
            "query_agenda": self._agenda,
            "find_slots": self._slots,
            "cancel": self._cancel,
            "reschedule": self._reschedule,
        }
        handler = handlers.get(intent.kind)
        if handler is None:
            question = (
                intent.clarification_needed
                or "I did not catch that. What would you like me to do?"
            )
            return AgentReply(question, kind="question")
        try:
            return handler(intent, chat_id)
        except Exception:
            log.exception("handler for %s failed", intent.kind)
            return AgentReply("Something went wrong handling that.", kind="error")

    def confirm(self, token: str) -> AgentReply:
        """Execute a previously proposed action. This is the only writer."""
        payload = db.take_pending(self.conn, token)
        if payload is None:
            return AgentReply(
                "That confirmation has expired. Please ask again.", kind="error"
            )

        action = payload.get("action")
        if action == "create":
            event = ResolvedEvent.model_validate(payload["event"])
            created = self.calendar.create_event(event)
            db.record(
                self.conn,
                source="confirm",
                intent="create_event",
                outcome=created.get("id"),
            )
            return AgentReply(
                f"Booked.\n\n{self._describe_proposal(event)}", kind="done"
            )
        if action == "delete":
            self.calendar.delete_event(payload["event_id"])
            db.record(
                self.conn, source="confirm", intent="cancel", outcome=payload["event_id"]
            )
            return AgentReply(f"Cancelled *{payload['summary']}*.", kind="done")
        if action == "move":
            start = dt.datetime.fromisoformat(payload["start"])
            end = dt.datetime.fromisoformat(payload["end"])
            self.calendar.move_event(
                payload["event_id"], start, end, payload["timezone"]
            )
            return AgentReply(
                f"Moved *{payload['summary']}* to\n{self._dual(start)}", kind="done"
            )
        return AgentReply("I no longer know what that referred to.", kind="error")

    # ---- intent handlers -------------------------------------------------

    def _create(self, intent: Intent, chat_id: int | None) -> AgentReply:
        if intent.event is None:
            return AgentReply("What meeting would you like me to add?", kind="question")
        if intent.event.date is None or intent.event.time is None:
            return AgentReply(
                "What day and time should I book that for?", kind="question"
            )

        event = resolve_event(intent.event, prefs=self.prefs)

        # Check the whole series, not just the first meeting.
        occurrences: list[Interval] = [(event.start, event.end)]
        if event.recurrence:
            length = event.end - event.start
            occurrences = [
                (start, start + length)
                for start in expand_occurrences(
                    event.recurrence,
                    to_zone(event.start, event.timezone).date(),
                    to_zone(event.start, event.timezone).time(),
                    event.timezone,
                    limit=8,
                )
            ]

        window_start = min(o[0] for o in occurrences) - dt.timedelta(hours=1)
        window_end = max(o[1] for o in occurrences) + dt.timedelta(hours=1)
        busy = self.calendar.busy_intervals(window_start, window_end)

        clashing = [o for o in occurrences if conflicts_with(o, busy)]
        if clashing:
            return self._offer_alternatives(event, clashing, busy, chat_id)

        token = db.store_pending(
            self.conn,
            {"action": "create", "event": event.model_dump(mode="json")},
            chat_id=chat_id,
            ttl_minutes=self.prefs.confirm_ttl_minutes,
        )
        return AgentReply(
            f"{self._describe_proposal(event)}\n\nShall I book it?",
            choices=[Choice("Confirm", token)],
            kind="confirm",
        )

    def _offer_alternatives(
        self,
        event: ResolvedEvent,
        clashing: list[Interval],
        busy: list[Interval],
        chat_id: int | None,
    ) -> AgentReply:
        """Refuse the double-booking and propose real alternatives."""
        duration = int((event.end - event.start).total_seconds() // 60)
        first_clash = clashing[0]
        lines = [
            f"*{first_clash[0].astimezone(first_clash[0].tzinfo):%a %-d %b}* is "
            f"already booked at that time:",
            self._dual(first_clash[0]),
        ]
        if len(clashing) > 1:
            lines.append(f"({len(clashing)} occurrences of the series clash.)")

        day = to_zone(event.start, event.timezone).date()
        alternatives = find_slots_across_days(
            day,
            event.timezone,
            duration,
            busy,
            self.prefs.working_hours,
            days=5,
            limit=3,
        )
        if not alternatives:
            lines.append("\nI could not find a free slot in the next 5 working days.")
            return AgentReply("\n".join(lines), kind="conflict")

        choices: list[Choice] = []
        lines.append("\nFree instead:")
        for start, end in alternatives:
            moved = event.model_copy(update={"start": start, "end": end})
            token = db.store_pending(
                self.conn,
                {"action": "create", "event": moved.model_dump(mode="json")},
                chat_id=chat_id,
                ttl_minutes=self.prefs.confirm_ttl_minutes,
            )
            label = format_dual(start, event.timezone, self.prefs.primary_tz)
            lines.append(f"- {label}")
            choices.append(Choice(label, token))
        return AgentReply("\n".join(lines), choices=choices, kind="conflict")

    def _agenda(self, intent: Intent, chat_id: int | None) -> AgentReply:
        query = intent.agenda
        now = dt.datetime.now(dt.timezone.utc)

        if query and query.within_minutes:
            start, end = now, now + dt.timedelta(minutes=query.within_minutes)
            heading = f"Next {query.within_minutes} minutes"
        elif query and query.date:
            tz = self.prefs.primary_tz
            from app.timezones import combine

            start = combine(query.date, dt.time(0, 0), tz).when
            end = start + dt.timedelta(days=1)
            heading = f"{query.date:%A %-d %B}"
        elif query and query.range_days:
            start, end = now, now + dt.timedelta(days=query.range_days)
            heading = f"Next {query.range_days} days"
        else:
            tz = self.prefs.primary_tz
            from app.timezones import combine

            today = now.astimezone(to_zone(now, tz).tzinfo).date()
            start = combine(today, dt.time(0, 0), tz).when
            end = start + dt.timedelta(days=1)
            heading = "Today"

        events = self.calendar.list_events(start, end)
        if not events:
            return AgentReply(f"*{heading}*\n\nNothing scheduled.")
        lines = [f"*{heading}*", ""]
        lines += [self._render_event_line(item) for item in events]
        return AgentReply("\n".join(lines))

    def _slots(self, intent: Intent, chat_id: int | None) -> AgentReply:
        query = intent.slots
        now = dt.datetime.now(dt.timezone.utc)
        duration = (
            query.duration_minutes if query and query.duration_minutes else None
        ) or self.prefs.default_duration_minutes

        try:
            tz = resolve_timezone(
                query.timezone if query else None, default=self.prefs.primary_tz
            )
        except UnknownTimezone:
            tz = self.prefs.primary_tz

        day = (query.date if query and query.date else None) or to_zone(now, tz).date()
        span = (query.range_days if query and query.range_days else None) or (
            1 if query and query.date else 5
        )

        search_end = dt.datetime.combine(
            day + dt.timedelta(days=span + 1), dt.time(0, 0), dt.timezone.utc
        )
        busy = self.calendar.busy_intervals(now - dt.timedelta(days=1), search_end)
        slots = find_slots_across_days(
            day, tz, duration, busy, self.prefs.working_hours,
            days=span, now=now, limit=5, per_day=2,
        )
        if not slots:
            return AgentReply(
                f"No free {duration}-minute slots in that window.", kind="info"
            )
        lines = [f"*Free {duration}-minute slots*", ""]
        lines += [f"- {self._dual(start)}" for start, _ in slots]
        return AgentReply("\n".join(lines))

    def _find_matching_event(self, description: str | None) -> dict[str, Any] | None:
        """Locate an existing event from a spoken description.

        Intentionally simple substring matching over the next 30 days; the
        confirmation step is what makes this safe enough.
        """
        now = dt.datetime.now(dt.timezone.utc)
        events = self.calendar.list_events(now, now + dt.timedelta(days=30))
        if not description:
            return events[0] if events else None
        words = {w for w in description.lower().split() if len(w) > 3}
        best, best_score = None, 0
        for item in events:
            summary = item.get("summary", "").lower()
            score = sum(1 for w in words if w in summary)
            if score > best_score:
                best, best_score = item, score
        return best

    def _cancel(self, intent: Intent, chat_id: int | None) -> AgentReply:
        match = self._find_matching_event(intent.target_description)
        if match is None:
            return AgentReply("I could not find that meeting.", kind="question")
        when = dt.datetime.fromisoformat(match["start"]["dateTime"])
        token = db.store_pending(
            self.conn,
            {
                "action": "delete",
                "event_id": match["id"],
                "summary": match.get("summary", "(no title)"),
            },
            chat_id=chat_id,
            ttl_minutes=self.prefs.confirm_ttl_minutes,
        )
        return AgentReply(
            f"Cancel *{match.get('summary')}*?\n{self._dual(when)}",
            choices=[Choice("Cancel it", token)],
            kind="confirm",
        )

    def _reschedule(self, intent: Intent, chat_id: int | None) -> AgentReply:
        match = self._find_matching_event(intent.target_description)
        if match is None:
            return AgentReply("I could not find that meeting.", kind="question")
        if intent.event is None or intent.event.date is None or intent.event.time is None:
            return AgentReply("What should I move it to?", kind="question")

        spec = intent.event.model_copy(
            update={"title": intent.event.title or match.get("summary", "Meeting")}
        )
        moved = resolve_event(spec, prefs=self.prefs)
        old_start = dt.datetime.fromisoformat(match["start"]["dateTime"])
        old_end = dt.datetime.fromisoformat(match["end"]["dateTime"])
        length = old_end - old_start

        token = db.store_pending(
            self.conn,
            {
                "action": "move",
                "event_id": match["id"],
                "summary": match.get("summary", "(no title)"),
                "start": moved.start.isoformat(),
                "end": (moved.start + length).isoformat(),
                "timezone": moved.timezone,
            },
            chat_id=chat_id,
            ttl_minutes=self.prefs.confirm_ttl_minutes,
        )
        return AgentReply(
            f"Move *{match.get('summary')}*\nfrom {self._dual(old_start)}\n"
            f"to {self._dual(moved.start)}?",
            choices=[Choice("Move it", token)],
            kind="confirm",
        )
