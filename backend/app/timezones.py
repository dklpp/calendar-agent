"""Timezone resolution, DST-safe construction, and dual-zone rendering.

This module is the backbone of the whole agent, so two rules are absolute:

1. The LLM never does time arithmetic. It reports ``{date, time, timezone}``
   exactly as the speaker said it; everything here converts deterministically.
2. Nothing is ever stored as a naive datetime or a fixed UTC offset. Only IANA
   zone names survive, because only they know about future DST transitions.

The reason this matters: North America and the EU switch DST on different
dates. In 2026 the America/Toronto <-> Europe/Vilnius gap is 7h for most of the
year, but narrows to 6h between Mar 8-29 and again between Oct 25 - Nov 1.
Arithmetic that assumes a constant 7h silently books meetings an hour off
twice a year. See tests/test_timezones.py.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc

# Spoken forms -> IANA zone. Keys are matched against normalized word tokens,
# so keep them lowercase and free of punctuation. Abbreviations like "eet" are
# deliberately mapped to a zone rather than a fixed offset: an abbreviation
# names a *place's* current rule, not an offset we should freeze.
TZ_SYNONYMS: dict[str, str] = {
    # Lithuania / Europe
    "lithuania": "Europe/Vilnius",
    "lithuanian": "Europe/Vilnius",
    "vilnius": "Europe/Vilnius",
    "kaunas": "Europe/Vilnius",
    "lt": "Europe/Vilnius",
    "eet": "Europe/Vilnius",
    "eest": "Europe/Vilnius",
    "baltic": "Europe/Vilnius",
    # Canada / US Eastern
    "toronto": "America/Toronto",
    "canada": "America/Toronto",
    "canadian": "America/Toronto",
    "eastern": "America/Toronto",
    "ontario": "America/Toronto",
    "et": "America/Toronto",
    "est": "America/Toronto",
    "edt": "America/Toronto",
    "montreal": "America/Toronto",
    "ottawa": "America/Toronto",
    "nyc": "America/New_York",
    # occasionally useful extras
    "utc": "UTC",
    "gmt": "UTC",
    "london": "Europe/London",
    "uk": "Europe/London",
    "warsaw": "Europe/Warsaw",
    "poland": "Europe/Warsaw",
    "berlin": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "riga": "Europe/Riga",
    "latvia": "Europe/Riga",
    "tallinn": "Europe/Tallinn",
    "estonia": "Europe/Tallinn",
}

_WORD_RE = re.compile(r"[a-z]+")


class UnknownTimezone(ValueError):
    """Raised when a spoken timezone cannot be resolved to an IANA zone."""


def resolve_timezone(text: str | None, default: str | None = None) -> str:
    """Resolve a spoken timezone phrase to an IANA zone name.

    Accepts an IANA name verbatim ("Europe/Vilnius"), or a spoken phrase
    ("Lithuanian time", "eastern"). Falls back to ``default`` when ``text`` is
    empty, and raises :class:`UnknownTimezone` when it is present but
    unrecognised -- guessing a zone is worse than asking.
    """
    if text:
        candidate = text.strip()
        if "/" in candidate:
            try:
                ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise UnknownTimezone(f"not a known IANA zone: {candidate!r}") from exc
            return candidate
        for word in _WORD_RE.findall(candidate.lower()):
            if word in TZ_SYNONYMS:
                return TZ_SYNONYMS[word]
        raise UnknownTimezone(f"could not resolve timezone from {text!r}")
    if default:
        return default
    raise UnknownTimezone("no timezone given and no default configured")


def zone_label(tz_name: str) -> str:
    """Human label for a zone: 'Europe/Vilnius' -> 'Vilnius'."""
    return tz_name.rsplit("/", 1)[-1].replace("_", " ")


@dataclass(frozen=True)
class LocalTimeResult:
    """An aware datetime plus any DST anomaly encountered while building it."""

    when: dt.datetime
    warning: str | None = None


def combine(date: dt.date, time: dt.time, tz_name: str) -> LocalTimeResult:
    """Build an aware datetime from a wall-clock date/time in ``tz_name``.

    Handles the two DST anomalies explicitly rather than letting them pass
    silently, because a calendar agent that books a meeting at a wall-clock
    time that does not exist is worse than one that says so:

    * **Nonexistent** (spring forward, e.g. 02:30 on the switch day) -- shifted
      forward past the gap, with a warning.
    * **Ambiguous** (fall back, e.g. 01:30 occurring twice) -- resolved to the
      first (earlier) occurrence, with a warning.
    """
    tz = ZoneInfo(tz_name)
    naive = dt.datetime.combine(date, time)
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)

    # Nonexistent local time: round-tripping through UTC does not return the
    # same wall clock reading.
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        gap = second.utcoffset() - first.utcoffset()
        shifted = (first + abs(gap)).astimezone(tz)
        return LocalTimeResult(
            shifted,
            f"{time:%H:%M} does not exist on {date} in {zone_label(tz_name)} "
            f"(clocks spring forward); using {shifted:%H:%M} instead",
        )

    # Ambiguous local time: the same wall clock reading has two valid offsets.
    if first.utcoffset() != second.utcoffset():
        return LocalTimeResult(
            first,
            f"{time:%H:%M} on {date} occurs twice in {zone_label(tz_name)} "
            f"(clocks fall back); using the first occurrence",
        )

    return LocalTimeResult(first)


def to_zone(when: dt.datetime, tz_name: str) -> dt.datetime:
    """Convert an aware datetime into ``tz_name``. Rejects naive input."""
    if when.tzinfo is None:
        raise ValueError("refusing to convert a naive datetime; attach a zone first")
    return when.astimezone(ZoneInfo(tz_name))


def offset_gap_hours(instant: dt.datetime, tz_a: str, tz_b: str) -> float:
    """Hours that ``tz_b`` is ahead of ``tz_a`` at a given instant.

    Must be evaluated at an instant, never cached -- that is the entire point.
    """
    if instant.tzinfo is None:
        raise ValueError("offset gap requires an aware datetime")
    a = instant.astimezone(ZoneInfo(tz_a)).utcoffset()
    b = instant.astimezone(ZoneInfo(tz_b)).utcoffset()
    assert a is not None and b is not None
    return (b - a).total_seconds() / 3600


def format_in_zone(when: dt.datetime, tz_name: str, *, with_date: bool = False) -> str:
    """Render as '15:00 EEST Vilnius', optionally prefixed with the date."""
    local = to_zone(when, tz_name)
    stamp = f"{local:%H:%M} {local:%Z} {zone_label(tz_name)}"
    return f"{local:%a %-d %b} {stamp}" if with_date else stamp


def format_dual(when: dt.datetime, primary: str, secondary: str) -> str:
    """Render an instant in both of the user's zones.

    Example: ``Thu 24 Sep - 15:00 EEST Vilnius / 08:00 EDT Toronto``. When the
    two zones fall on different calendar days (which the 7h gap makes routine
    for evening meetings) the second zone carries its own date so the reading
    is never ambiguous.
    """
    a = to_zone(when, primary)
    b = to_zone(when, secondary)
    left = f"{a:%a %-d %b} - {a:%H:%M} {a:%Z} {zone_label(primary)}"
    if a.date() == b.date():
        right = f"{b:%H:%M} {b:%Z} {zone_label(secondary)}"
    else:
        right = f"{b:%a %-d %b} {b:%H:%M} {b:%Z} {zone_label(secondary)}"
    return f"{left} / {right}"
