"""FastAPI service: HTTP endpoints for the macOS app, plus the Telegram bot
and the reminder scheduler running alongside in the same event loop.
"""

from __future__ import annotations

import datetime as dt
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from app import db
from app.agent import Agent
from app.calendar_client import CalendarClient
from app.config import get_preferences, get_settings
from app.reminders import ReminderService
from app.telegram_bot import TelegramFrontend
from app.timezones import combine, format_dual, to_zone
from app.transcribe import TranscriptionError, transcribe

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger(__name__)


class AskRequest(BaseModel):
    text: str


class ChoiceOut(BaseModel):
    label: str
    token: str


class ReplyOut(BaseModel):
    text: str
    kind: str
    choices: list[ChoiceOut] = []


class EventOut(BaseModel):
    id: str
    summary: str
    start: dt.datetime
    end: dt.datetime
    primary: str
    secondary: str
    both: str


class AppState:
    agent: Agent
    telegram: TelegramFrontend | None = None
    reminders: ReminderService | None = None


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    conn = db.connect()
    calendar = CalendarClient(settings)
    state.agent = Agent(calendar=calendar, conn=conn)
    db.purge_expired(conn)

    if settings.telegram_bot_token:
        state.telegram = TelegramFrontend(state.agent, settings)
        application = state.telegram.build()
        await application.initialize()
        await application.start()
        # Long polling: outbound only, so the Pi needs no inbound ports and no
        # port forwarding.
        await application.updater.start_polling(drop_pending_updates=True)
        log.info("telegram bot polling")

        state.reminders = ReminderService(
            calendar, conn, state.telegram.send, get_preferences()
        )
        state.reminders.start()
    else:
        log.warning("TELEGRAM_BOT_TOKEN unset: bot and reminders disabled")

    try:
        yield
    finally:
        if state.reminders:
            state.reminders.shutdown()
        if state.telegram and state.telegram.app:
            await state.telegram.app.updater.stop()
            await state.telegram.app.stop()
            await state.telegram.app.shutdown()
        conn.close()


app = FastAPI(title="Calendar Agent", version="0.1.0", lifespan=lifespan)


def _reply_out(reply) -> ReplyOut:
    return ReplyOut(
        text=reply.text,
        kind=reply.kind,
        choices=[ChoiceOut(label=c.label, token=c.token) for c in reply.choices],
    )


@app.get("/health")
def health() -> dict:
    prefs = get_preferences()
    return {
        "status": "ok",
        "timezones": [prefs.primary_tz, prefs.secondary_tz],
        "telegram": state.telegram is not None,
        "reminders": state.reminders is not None,
    }


@app.post("/ask", response_model=ReplyOut)
def ask(request: AskRequest) -> ReplyOut:
    """Text in, reply out. Writes nothing; may return a confirmation token."""
    return _reply_out(state.agent.handle(request.text, source="mac"))


@app.post("/voice", response_model=ReplyOut)
async def voice(file: UploadFile = File(...)) -> ReplyOut:
    """Audio in, reply out. Transcribes locally, then behaves like /ask."""
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / f"upload{suffix}"
        source.write_bytes(await file.read())
        try:
            text = transcribe(source)
        except TranscriptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    reply = state.agent.handle(text, source="mac-voice")
    return _reply_out(reply).model_copy(
        update={"text": f'Heard: "{text}"\n\n{reply.text}'}
    )


@app.post("/confirm/{token}", response_model=ReplyOut)
def confirm(token: str) -> ReplyOut:
    """Execute a pending action. The only path that writes to the calendar."""
    return _reply_out(state.agent.confirm(token))


@app.get("/agenda", response_model=list[EventOut])
def agenda(days: int = 1) -> list[EventOut]:
    """Upcoming events, pre-rendered in both timezones for the menu bar."""
    prefs = get_preferences()
    now = dt.datetime.now(dt.timezone.utc)
    today = to_zone(now, prefs.primary_tz).date()
    start = combine(today, dt.time(0, 0), prefs.primary_tz).when
    end = start + dt.timedelta(days=days)

    out: list[EventOut] = []
    for item in state.agent.calendar.list_events(start, end):
        start_field = item.get("start", {})
        if "dateTime" not in start_field:
            continue
        begins = dt.datetime.fromisoformat(start_field["dateTime"])
        ends = dt.datetime.fromisoformat(item["end"]["dateTime"])
        out.append(
            EventOut(
                id=item["id"],
                summary=item.get("summary", "(no title)"),
                start=begins,
                end=ends,
                primary=f"{to_zone(begins, prefs.primary_tz):%H:%M}",
                secondary=f"{to_zone(begins, prefs.secondary_tz):%H:%M}",
                both=format_dual(begins, prefs.primary_tz, prefs.secondary_tz),
            )
        )
    return out


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
