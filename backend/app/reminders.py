"""Proactive reminders, swept on a schedule and deduped through SQLite."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db
from app.calendar_client import CalendarClient
from app.config import Preferences, get_preferences
from app.timezones import format_dual

log = logging.getLogger(__name__)

Notifier = Callable[[str], Awaitable[None]]


class ReminderService:
    def __init__(
        self,
        calendar: CalendarClient,
        conn: sqlite3.Connection,
        notify: Notifier,
        prefs: Preferences | None = None,
    ) -> None:
        self.calendar = calendar
        self.conn = conn
        self.notify = notify
        self.prefs = prefs or get_preferences()
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self.scheduler.add_job(self.sweep, "interval", seconds=60, id="reminder-sweep")
        self.scheduler.add_job(self.prune, "interval", hours=24, id="reminder-prune")
        self.scheduler.start()
        log.info("reminder scheduler started (offsets: %s)", self.prefs.reminder_minutes)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def sweep(self) -> None:
        """Fire any reminder whose moment has arrived.

        The window is anchored on each configured offset: a 60-minute reminder
        fires for events starting 59-60 minutes out. Sweeping every 60 seconds
        against a 60-second window means an event is seen at least once, and
        `claim_reminder` guarantees it is sent at most once.
        """
        now = dt.datetime.now(dt.timezone.utc)
        try:
            events = self.calendar.list_events(now, now + dt.timedelta(days=2))
        except Exception:
            log.exception("reminder sweep could not read the calendar")
            return

        for offset in self.prefs.reminder_minutes:
            target_start = now + dt.timedelta(minutes=offset)
            target_end = target_start + dt.timedelta(seconds=90)
            for item in events:
                start_field = item.get("start", {})
                if "dateTime" not in start_field:
                    continue  # all-day events get no timed reminder
                start = dt.datetime.fromisoformat(start_field["dateTime"])
                if not (target_start <= start < target_end):
                    continue
                if not db.claim_reminder(self.conn, item["id"], start, offset):
                    continue
                await self._send(item, start, offset)

    async def _send(self, item: dict, start: dt.datetime, offset: int) -> None:
        when = format_dual(start, self.prefs.primary_tz, self.prefs.secondary_tz)
        lead = f"{offset} minutes" if offset < 60 else f"{offset // 60}h"
        text = (
            f"Reminder: *{item.get('summary', '(no title)')}* in {lead}\n{when}"
        )
        if item.get("location"):
            text += f"\n{item['location']}"
        try:
            await self.notify(text)
        except Exception:
            log.exception("failed to deliver reminder for %s", item.get("id"))

    def prune(self) -> None:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
        removed = db.forget_reminders_before(self.conn, cutoff)
        if removed:
            log.info("pruned %d old reminder records", removed)
