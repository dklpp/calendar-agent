"""Transcript -> typed :class:`Intent`, via one Claude call.

One request both classifies the utterance and extracts its fields. The SDK's
``messages.parse`` helper validates the response against the Pydantic model, so
downstream code receives a real object rather than hand-parsed JSON.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import anthropic

from app.config import Preferences, get_preferences, get_settings
from app.models import EventSpec, Intent, ResolvedEvent
from app.timezones import UnknownTimezone, combine, resolve_timezone

# Stable across every request, so it sits before the cache breakpoint.
# (Prompt caching only engages above the model's minimum cacheable prefix; the
# ordering costs nothing either way and pays off as this prompt grows.)
STABLE_RULES = """\
You turn a spoken request into a structured calendar intent for a user who \
works across two timezones: America/Toronto (Canadian Eastern) and \
Europe/Vilnius (Lithuanian).

Rules:

1. NEVER convert times. Record the wall-clock time and the timezone exactly as \
the speaker stated them. If they say "3pm Lithuanian time", emit \
time=15:00 and timezone="Europe/Vilnius" -- do not translate that into \
Toronto time. Downstream code does all conversion.

2. Map spoken zones to IANA names: "Lithuanian"/"Lithuania"/"Vilnius"/"LT"/ \
"EET"/"EEST" -> Europe/Vilnius. "Eastern"/"Toronto"/"Canadian"/"ET"/"EST"/ \
"EDT" -> America/Toronto. If no zone is stated, leave timezone null.

3. Relative dates ("this Thursday", "tomorrow", "next week") resolve against \
the current date given below, in the user's default timezone.

4. Recurrence: set the recurrence object only when the meeting genuinely \
repeats. "every other Thursday" -> freq=weekly, interval=2, byday=["TH"]. \
"last Friday of the month" -> freq=monthly, byday=["FR"], bysetpos=-1. Never \
write a raw RRULE string.

5. Choose kind carefully:
   - create_event: booking something new.
   - query_agenda: asking what is already scheduled ("what do I have today", \
"what's in an hour").
   - find_slots: asking when they are free ("what slots do I have Thursday").
   - reschedule / cancel: changing or removing an existing meeting; put the \
speaker's description of it in target_description.
   - unknown: anything ambiguous or unrelated. Set clarification_needed to the \
single question that would resolve it.

6. Prefer asking over guessing. A transcript may contain speech-recognition \
errors; if a critical field is garbled, use kind="unknown" and ask.

7. Titles are short and human. If a company is named, put it in the company \
field as well as in the title where it reads naturally.\
"""


@lru_cache
def _client() -> anthropic.Anthropic:
    settings = get_settings()
    kwargs = {}
    if settings.anthropic_api_key:
        kwargs["api_key"] = settings.anthropic_api_key
    return anthropic.Anthropic(**kwargs)


def _context_block(prefs: Preferences, now: dt.datetime) -> str:
    """Volatile context. Deliberately placed AFTER the cache breakpoint -- a
    date inside the cached prefix would invalidate the cache every day."""
    local = now.astimezone()
    return (
        f"Current context:\n"
        f"- Now: {local:%A %d %B %Y, %H:%M %Z}\n"
        f"- User's default timezone: {prefs.primary_tz}\n"
        f"- User's other timezone: {prefs.secondary_tz}\n"
        f"- Default meeting length: {prefs.default_duration_minutes} minutes"
    )


def parse_utterance(
    text: str, *, now: dt.datetime | None = None, prefs: Preferences | None = None
) -> Intent:
    """Parse a transcript (or typed message) into an :class:`Intent`."""
    prefs = prefs or get_preferences()
    now = now or dt.datetime.now(dt.timezone.utc)
    settings = get_settings()

    response = _client().messages.parse(
        model=settings.anthropic_model,
        max_tokens=2000,
        # Extraction is not reasoning-heavy; low effort keeps latency and cost
        # down without measurably hurting accuracy on this task.
        output_config={"effort": "low"},
        system=[
            {
                "type": "text",
                "text": STABLE_RULES,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": _context_block(prefs, now)},
        ],
        messages=[{"role": "user", "content": text}],
        output_format=Intent,
    )
    return response.parsed_output


def resolve_event(
    spec: EventSpec, *, prefs: Preferences | None = None
) -> ResolvedEvent:
    """Turn a spoken :class:`EventSpec` into concrete aware datetimes.

    This is where all timezone work happens -- never in the model.
    """
    prefs = prefs or get_preferences()
    warnings: list[str] = []

    if spec.date is None or spec.time is None:
        raise ValueError("cannot resolve an event without both a date and a time")

    try:
        tz_name = resolve_timezone(spec.timezone, default=prefs.primary_tz)
    except UnknownTimezone:
        tz_name = prefs.primary_tz
        warnings.append(
            f"Could not understand the timezone {spec.timezone!r}; "
            f"assuming {prefs.primary_tz}."
        )
    if spec.timezone is None:
        warnings.append(f"No timezone stated; assuming {tz_name}.")

    built = combine(spec.date, spec.time, tz_name)
    if built.warning:
        warnings.append(built.warning)

    duration = spec.duration_minutes or prefs.default_duration_minutes
    return ResolvedEvent(
        title=spec.title,
        company=spec.company,
        start=built.when,
        end=built.when + dt.timedelta(minutes=duration),
        timezone=tz_name,
        recurrence=spec.recurrence,
        attendees=spec.attendees,
        notes=spec.notes,
        warnings=warnings,
    )
