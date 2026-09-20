"""Typed recurrence -> RFC 5545 RRULE, and DST-correct occurrence expansion.

The LLM never emits a raw RRULE string. It emits the small typed
:class:`Recurrence` below and this module renders it, because a model asked for
an RRULE directly will happily produce a plausible-looking wrong one
(``BYDAY=TH;BYSETPOS=-1`` without ``FREQ=MONTHLY``, ``COUNT`` and ``UNTIL``
together, and so on) and Google will accept it silently.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from dateutil.rrule import rrulestr
from pydantic import BaseModel, Field, model_validator

from app.timezones import combine

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

_FREQ_MAP = {
    "daily": "DAILY",
    "weekly": "WEEKLY",
    "monthly": "MONTHLY",
    "yearly": "YEARLY",
}


class Recurrence(BaseModel):
    """A recurrence rule in the narrow shape the agent actually supports."""

    freq: Literal["daily", "weekly", "monthly", "yearly"]
    interval: int = Field(default=1, ge=1, le=52)
    byday: list[str] | None = Field(
        default=None,
        description="Weekday codes, e.g. ['TH'] for Thursday, ['MO','WE'] for Mon+Wed.",
    )
    bysetpos: int | None = Field(
        default=None,
        description="Ordinal within the month: 1 = first, -1 = last. Monthly only.",
    )
    count: int | None = Field(default=None, ge=1, le=730)
    until: dt.date | None = None

    @model_validator(mode="after")
    def _check(self) -> "Recurrence":
        if self.count is not None and self.until is not None:
            raise ValueError("a recurrence may end with COUNT or UNTIL, not both")
        if self.byday:
            bad = [d for d in self.byday if d.upper() not in WEEKDAY_CODES]
            if bad:
                raise ValueError(f"not weekday codes: {bad}")
        if self.bysetpos is not None:
            if self.freq != "monthly":
                raise ValueError("bysetpos only makes sense with freq='monthly'")
            if not self.byday:
                raise ValueError("bysetpos needs byday, e.g. last FRIDAY of the month")
        return self

    def to_rrule(self) -> str:
        """Render the ``RRULE:...`` line Google Calendar expects."""
        parts = [f"FREQ={_FREQ_MAP[self.freq]}"]
        if self.interval != 1:
            parts.append(f"INTERVAL={self.interval}")
        if self.byday:
            parts.append(f"BYDAY={','.join(d.upper() for d in self.byday)}")
        if self.bysetpos is not None:
            parts.append(f"BYSETPOS={self.bysetpos}")
        if self.count is not None:
            parts.append(f"COUNT={self.count}")
        if self.until is not None:
            # RFC 5545: UNTIL for a timed event must be UTC with a Z suffix.
            # End of the given day so "until December 31" includes the 31st.
            parts.append(f"UNTIL={self.until:%Y%m%d}T235959Z")
        return "RRULE:" + ";".join(parts)

    def describe(self) -> str:
        """Plain-English echo, for the confirmation card."""
        names = {
            "MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday",
            "FR": "Friday", "SA": "Saturday", "SU": "Sunday",
        }
        days = ", ".join(names[d.upper()] for d in self.byday) if self.byday else ""
        if self.freq == "weekly":
            every = "every week" if self.interval == 1 else f"every {self.interval} weeks"
            base = f"{days} {every}" if days else every
        elif self.freq == "monthly":
            ordinals = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}
            if self.bysetpos is not None:
                base = f"the {ordinals.get(self.bysetpos, self.bysetpos)} {days} of each month"
            else:
                base = "monthly"
        elif self.freq == "daily":
            base = "every day" if self.interval == 1 else f"every {self.interval} days"
        else:
            base = "every year"
        if self.count:
            base += f", {self.count} times"
        elif self.until:
            base += f", until {self.until:%-d %b %Y}"
        return base


def expand_occurrences(
    recurrence: Recurrence,
    first_date: dt.date,
    at: dt.time,
    tz_name: str,
    limit: int = 8,
) -> list[dt.datetime]:
    """The first ``limit`` occurrences, as aware datetimes in ``tz_name``.

    Used to conflict-check a series rather than just its first meeting.

    The expansion runs over **naive wall-clock** datetimes and re-localizes
    each result afterwards. That is deliberate: a weekly 15:00 Vilnius meeting
    must stay at 15:00 Vilnius after the DST switch, not drift to 14:00 because
    something added a fixed 7*24h. Expanding in aware time would bake the
    original UTC offset into every later occurrence.
    """
    start = dt.datetime.combine(first_date, at)
    rule = rrulestr(recurrence.to_rrule(), dtstart=start)
    out: list[dt.datetime] = []
    for naive in rule:
        if len(out) >= limit:
            break
        out.append(combine(naive.date(), naive.time(), tz_name).when)
    return out
