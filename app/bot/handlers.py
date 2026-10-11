"""Telegram handlers. Thin: authenticate, translate to agent calls, send replies."""
from __future__ import annotations

import logging
import uuid

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from app.bot.keyboards import QUICK_TEXT, main_keyboard
from app.config import VOICE_DIR, Settings
from app.database.database import check_database
from app.services import Services
from app.voice.transcription import TranscriptionUnavailable

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Just talk to me like a friend. A few things I can do:\n"
    "• “Remind me tomorrow at 7 PM to study” / “Remind me every Monday at 6 PM…”\n"
    "• “I need to finish my portfolio and study PyTorch” → I'll make tasks\n"
    "• “Remember that my exam is on Nov 12” / “What do you remember about me?” / “Forget that”\n"
    "• “Save this as my project idea: …” (notes)\n"
    "• “Make me a study plan” (I can send files)\n"
    "• “Don't message me for 3 hours” / “Stop proactive messages” / “Start proactive messages”\n"
    "• Send a voice message and I'll transcribe it."
)


def is_authorized(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return bool(user and settings.telegram_user_id and user.id == settings.telegram_user_id)


class Handlers:
    def __init__(self, services: Services, settings: Settings):
        self.sv = services
        self.s = settings

    async def _guard(self, update: Update) -> bool:
        if is_authorized(update, self.s):
            return True
        uid = update.effective_user.id if update.effective_user else None
        log.warning("unauthorized telegram access blocked", extra={"telegram_id": uid})
        return False  # silent: don't reveal the bot is a private agent

    async def _typing(self, update: Update) -> None:
        try:
            await update.effective_chat.send_action(ChatAction.TYPING)
        except Exception:
            pass

    async def _reply(self, reply, *, as_voice: bool = False) -> None:
        sent = await self.sv.messenger.send_text(reply.text)
        for f in reply.files:
            await self.sv.messenger.send_file(f)
        if sent and reply.ok and (as_voice or self.sv.wants_voice()):
            await self.sv.send_voice_safely(reply.text)

    # ------------------------------------------------------------------ commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        self.sv.user_id()
        name = self.s.companion_name
        await update.message.reply_text(
            f"Hey! I'm {name} 👋 — your AI friend and assistant. I run on your own computer, and I might text you first "
            f"now and then. Tell me to stop any time.\n\n" + HELP_TEXT, reply_markup=main_keyboard())

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await update.message.reply_text(HELP_TEXT)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        ai_ok = await self.sv.provider.health()
        await update.message.reply_text(
            f"AI model ({self.s.ollama_model}): {'online' if ai_ok else 'OFFLINE'}\n"
            f"Database: {'ready' if check_database() else 'ERROR'}\n"
            f"Scheduler: {'running' if self.sv.scheduler and self.sv.scheduler.running else 'stopped'}")

    # ------------------------------------------------------------------ messages
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update) or not update.message or not update.message.text:
            return
        text = QUICK_TEXT.get(update.message.text, update.message.text)
        await self._typing(update)
        reply = await self.sv.agent.handle_message(self.sv.user_id(), text)
        await self._reply(reply)

    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update) or not update.message:
            return
        if not self.s.voice_input_enabled or self.sv.transcriber is None:
            await update.message.reply_text("Voice messages are turned off right now — text me instead?")
            return
        media = update.message.voice or update.message.audio
        if media is None:
            return
        await self._typing(update)
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        path = VOICE_DIR / f"in_{uuid.uuid4().hex}.ogg"
        try:
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(custom_path=path)
            text = await self.sv.transcriber.transcribe(path)
        except TranscriptionUnavailable as e:
            log.warning("transcription unavailable: %s", e)
            await update.message.reply_text("I can't transcribe voice yet — the speech engine isn't installed. "
                                            "Text me for now!")
            return
        except Exception:
            log.exception("voice handling failed")
            await update.message.reply_text("I couldn't make out that voice message. Could you try again or type it?")
            return
        finally:
            path.unlink(missing_ok=True)
        if not text:
            await update.message.reply_text("That voice message looked empty to me — try again?")
            return
        await update.message.reply_text(f"🎙 {text}")
        reply = await self.sv.agent.handle_message(self.sv.user_id(), text, kind="voice")
        await self._reply(reply, as_voice=True)

    async def on_unsupported(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update) or not update.message:
            return
        await update.message.reply_text("I can only handle text and voice messages for now 🙂")

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # never include the exception's string (it can contain the bot token in URLs)
        log.error("telegram handler error", extra={"error_type": type(context.error).__name__})
