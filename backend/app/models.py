"""The typed shapes Claude fills in, and the results the agent hands back.

Design rule: the model reports *what the speaker said*, never a computed
result. It writes down the date, the wall-clock time and the zone it heard;
`timezones.py` does every conversion afterwards.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from app.recurrence import Recurrence

IntentKind = Literal[
    "create_event",
    "query_agenda",
    "find_slots",
    "reschedule",
    "cancel",
    "unknown",
]


class EventSpec(BaseModel):
    """A meeting as described out loud."""

    title: str = Field(description="Short meeting title, e.g. 'Acme Corp sync'.")
    company: str | None = Field(
        default=None,
        description="Company or client the meeting is for, if one was named.",
    )
    date: dt.date | None = Field(
        default=None, description="Calendar date of the (first) occurrence."
    )
    time: dt.time | None = Field(
        default=None,
        description="Wall-clock start time in the stated timezone. Do NOT convert.",
    )
    duration_minutes: int | None = Field(
        default=None, description="Length in minutes if stated; null to use the default."
    )
    timezone: str | None = Field(
        default=None,
        description=(
            "IANA zone the speaker stated, e.g. 'Europe/Vilnius' for "
            "'Lithuanian time', 'America/Toronto' for 'eastern'. Null if unstated."
        ),
    )
    recurrence: Recurrence | None = Field(
        default=None, description="Set only if the meeting repeats."
    )
    attendees: list[str] = Field(
        default_factory=list, description="Email addresses, if any were given."
    )
    notes: str | None = None


class AgendaQuery(BaseModel):
    """A question about what is already scheduled."""

    date: dt.date | None = Field(
        default=None, description="Specific day asked about, if any."
    )
    within_minutes: int | None = Field(
        default=None,
        description="For 'what's in the next hour' style questions: 60.",
    )
    range_days: int | None = Field(
        default=None, description="For 'this week' style questions: 7."
    )


class SlotQuery(BaseModel):
    """A request for free time."""

    date: dt.date | None = None
    range_days: int | None = Field(
        default=None, description="Search window if no single day was named."
    )
    duration_minutes: int | None = None
    timezone: str | None = Field(
        default=None, description="IANA zone the slots should be judged in."
    )


class Intent(BaseModel):
    """The single object Claude returns for any utterance."""

    kind: IntentKind
    event: EventSpec | None = None
    agenda: AgendaQuery | None = None
    slots: SlotQuery | None = None
    target_description: str | None = Field(
        default=None,
        description=(
            "For reschedule/cancel: how the speaker referred to the existing "
            "meeting, e.g. 'the Acme call on Thursday'."
        ),
    )
    clarification_needed: str | None = Field(
        default=None,
        description=(
            "For kind='unknown', the one question that would resolve the "
            "ambiguity. Ask rather than guess."
        ),
    )


class ResolvedEvent(BaseModel):
    """An EventSpec after our own code has done the timezone work."""

    title: str
    company: str | None
    start: dt.datetime
    end: dt.datetime
    timezone: str
    recurrence: Recurrence | None = None
    attendees: list[str] = Field(default_factory=list)
    notes: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.company and self.company.lower() not in self.title.lower():
            return f"{self.title} ({self.company})"
        return self.title
