"""Telegram front end: text, voice notes, and inline confirmation buttons."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.agent import Agent, AgentReply
from app.config import Settings, get_settings
from app.transcribe import TranscriptionError, transcribe

log = logging.getLogger(__name__)


def _keyboard(reply: AgentReply) -> InlineKeyboardMarkup | None:
    if not reply.choices:
        return None
    rows = [
        [InlineKeyboardButton(choice.label, callback_data=f"ok:{choice.token}")]
        for choice in reply.choices
    ]
    rows.append([InlineKeyboardButton("Cancel", callback_data="no:")])
    return InlineKeyboardMarkup(rows)


class TelegramFrontend:
    def __init__(self, agent: Agent, settings: Settings | None = None) -> None:
        self.agent = agent
        self.settings = settings or get_settings()
        self.app: Application | None = None

    # ---- access control -------------------------------------------------

    def _allowed(self, update: Update) -> bool:
        """Whitelist check.

        An empty whitelist denies everyone. Without this, anyone who finds the
        bot's username could read and rewrite the calendar.
        """
        chat = update.effective_chat
        if chat is None:
            return False
        allowed = self.settings.allowed_chat_ids
        if not allowed:
            log.warning(
                "rejecting chat %s: TELEGRAM_ALLOWED_CHAT_IDS is empty", chat.id
            )
            return False
        return chat.id in allowed

    async def _deny(self, update: Update) -> None:
        chat = update.effective_chat
        log.warning("unauthorised access attempt from chat_id=%s", chat.id if chat else "?")
        if update.effective_message:
            await update.effective_message.reply_text(
                "This assistant is private. "
                f"If it is yours, add chat id {chat.id if chat else '?'} to "
                "TELEGRAM_ALLOWED_CHAT_IDS."
            )

    # ---- handlers -------------------------------------------------------

    async def _respond(self, update: Update, reply: AgentReply) -> None:
        await update.effective_message.reply_text(
            reply.text, reply_markup=_keyboard(reply), parse_mode="Markdown"
        )

    async def on_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not self._allowed(update):
            return await self._deny(update)
        await update.effective_message.reply_text(
            "Calendar agent ready.\n\n"
            "Send a voice note or type, for example:\n"
            "- meeting Thursday 3pm Lithuanian time for Acme Corp\n"
            "- what do I have today?\n"
            "- what is in the next hour?\n"
            "- what slots are free Thursday?\n"
            "- every other Thursday 3pm Vilnius time with Acme\n\n"
            f"(this chat id is {chat_id})"
        )

    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        await update.effective_chat.send_action(ChatAction.TYPING)
        reply = self.agent.handle(
            update.effective_message.text,
            source="telegram",
            chat_id=update.effective_chat.id,
        )
        await self._respond(update, reply)

    async def on_voice(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self._deny(update)

        message = update.effective_message
        # Transcription takes ~10-20s on a Pi; say so immediately rather than
        # looking hung.
        status = await message.reply_text("Transcribing...")

        voice = message.voice or message.audio
        telegram_file = await voice.get_file()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "voice.ogg"
            await telegram_file.download_to_drive(str(source))
            try:
                text = transcribe(source)
            except TranscriptionError as exc:
                log.exception("transcription failed")
                await status.edit_text(f"Could not transcribe that: {exc}")
                return

        await status.edit_text(f'Heard: "{text}"')
        await update.effective_chat.send_action(ChatAction.TYPING)
        reply = self.agent.handle(
            text, source="telegram-voice", chat_id=update.effective_chat.id
        )
        await self._respond(update, reply)

    async def on_button(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not self._allowed(update):
            return await self._deny(update)

        action, _, token = query.data.partition(":")
        if action == "no":
            await query.edit_message_text("Cancelled. Nothing was changed.")
            return

        reply = self.agent.confirm(token)
        await query.edit_message_text(reply.text, parse_mode="Markdown")

    # ---- lifecycle ------------------------------------------------------

    def build(self) -> Application:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        app = Application.builder().token(self.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self.on_start))
        app.add_handler(CommandHandler("help", self.on_start))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app = app
        return app

    async def send(self, text: str) -> None:
        """Push a message to every whitelisted chat (used by reminders)."""
        if self.app is None:
            return
        for chat_id in self.settings.allowed_chat_ids:
            await self.app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="Markdown"
            )
