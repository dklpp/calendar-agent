"""Google Calendar access: OAuth, free/busy, and event CRUD.

Why OAuth rather than a service account: a service account has no access to a
personal Gmail calendar without domain-wide delegation, which a personal
account cannot grant. Run the consent flow once on a machine with a browser,
then copy token.json to the Pi.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config import Settings, get_preferences, get_settings
from app.models import ResolvedEvent
from app.slots import Interval

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def load_credentials(settings: Settings | None = None) -> Credentials:
    """Load cached credentials, refreshing or running consent as needed."""
    settings = settings or get_settings()
    creds: Credentials | None = None

    if settings.google_token_path.exists():
        creds = Credentials.from_authorized_user_file(
            str(settings.google_token_path), SCOPES
        )

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not settings.google_credentials_path.exists():
            raise FileNotFoundError(
                f"Google OAuth client secrets not found at "
                f"{settings.google_credentials_path}. Download them from the "
                f"Google Cloud console (OAuth client ID, type 'Desktop app')."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(settings.google_credentials_path), SCOPES
        )
        # Needs a browser. Do this on the Mac, then copy token.json to the Pi.
        creds = flow.run_local_server(port=0)

    settings.google_token_path.write_text(creds.to_json())
    return creds


class CalendarClient:
    """Thin wrapper over the Google Calendar v3 API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._service: Any | None = None

    @property
    def service(self) -> Any:
        if self._service is None:
            self._service = build(
                "calendar",
                "v3",
                credentials=load_credentials(self._settings),
                cache_discovery=False,
            )
        return self._service

    @property
    def calendar_id(self) -> str:
        return get_preferences().calendar_id

    # ---- reading -------------------------------------------------------

    def busy_intervals(self, start: dt.datetime, end: dt.datetime) -> list[Interval]:
        """Busy blocks between two instants, via the free/busy endpoint.

        This is the call that makes "that's taken, here's another time" cheap:
        it returns opaque busy ranges without fetching every event body.
        """
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": self.calendar_id}],
        }
        response = self.service.freebusy().query(body=body).execute()
        calendars = response.get("calendars", {})
        entry = calendars.get(self.calendar_id, {})
        if entry.get("errors"):
            raise RuntimeError(f"free/busy query failed: {entry['errors']}")
        return [
            (
                dt.datetime.fromisoformat(block["start"]),
                dt.datetime.fromisoformat(block["end"]),
            )
            for block in entry.get("busy", [])
        ]

    def list_events(
        self, start: dt.datetime, end: dt.datetime, *, max_results: int = 100
    ) -> list[dict[str, Any]]:
        """Events in a window, recurring series already expanded.

        ``singleEvents=True`` is what turns a recurring series into individual
        occurrences; without it a weekly meeting appears once, at its original
        start date, and the agenda is silently wrong.
        """
        response = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
            )
            .execute()
        )
        return response.get("items", [])

    # ---- writing -------------------------------------------------------

    def create_event(self, event: ResolvedEvent) -> dict[str, Any]:
        """Create an event, timed in its own zone.

        The ``timeZone`` field carries the IANA name rather than a UTC instant.
        For a recurring series this is what makes later occurrences follow that
        zone's DST rules instead of drifting by an hour.
        """
        body: dict[str, Any] = {
            "summary": event.summary,
            "start": {
                "dateTime": event.start.isoformat(),
                "timeZone": event.timezone,
            },
            "end": {"dateTime": event.end.isoformat(), "timeZone": event.timezone},
        }
        if event.recurrence:
            body["recurrence"] = [event.recurrence.to_rrule()]
        if event.attendees:
            body["attendees"] = [{"email": a} for a in event.attendees]

        description_parts = []
        if event.company:
            description_parts.append(f"Company: {event.company}")
        if event.notes:
            description_parts.append(event.notes)
        description_parts.append("Created by calendar-agent.")
        body["description"] = "\n\n".join(description_parts)

        return (
            self.service.events()
            .insert(calendarId=self.calendar_id, body=body)
            .execute()
        )

    def delete_event(self, event_id: str) -> None:
        self.service.events().delete(
            calendarId=self.calendar_id, eventId=event_id
        ).execute()

    def move_event(
        self, event_id: str, start: dt.datetime, end: dt.datetime, tz_name: str
    ) -> dict[str, Any]:
        body = {
            "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        }
        return (
            self.service.events()
            .patch(calendarId=self.calendar_id, eventId=event_id, body=body)
            .execute()
        )
