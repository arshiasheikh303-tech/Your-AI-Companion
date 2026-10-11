"""Telegram adapter built on python-telegram-bot (long polling: no public URL / webhook needed)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Bot
from telegram.error import NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.bot.messenger import Messenger
from app.config import Settings, get_settings

log = logging.getLogger(__name__)
MAX_LEN = 4000


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on paragraph/line boundaries so long replies fit Telegram's 4096 limit."""
    text = text.strip() or "…"
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit), text.rfind(". ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    parts.append(text)
    return [p for p in parts if p]


class TelegramMessenger(Messenger):
    """Sends to the single whitelisted user. Never raises: returns False on failure."""

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def _retry(self, fn, *args, **kwargs) -> bool:
        for attempt in range(3):
            try:
                await fn(*args, **kwargs)
                return True
            except RetryAfter as e:
                await asyncio.sleep(min(float(e.retry_after), 30))
            except NetworkError:
                await asyncio.sleep(1.5 * (attempt + 1))
            except TelegramError as e:
                log.error("telegram send failed", extra={"error": type(e).__name__})
                return False
        log.error("telegram send failed after retries")
        return False

    async def send_text(self, text: str) -> bool:
        ok = True
        for chunk in split_message(text):
            ok = await self._retry(self.bot.send_message, chat_id=self.chat_id, text=chunk) and ok
        return ok

    async def send_voice(self, audio_path: Path) -> bool:
        async def _send():
            with open(audio_path, "rb") as f:
                if audio_path.suffix == ".ogg":
                    await self.bot.send_voice(chat_id=self.chat_id, voice=f)
                else:
                    await self.bot.send_audio(chat_id=self.chat_id, audio=f)
        return await self._retry(_send)

    async def send_file(self, path: Path, caption: str = "") -> bool:
        async def _send():
            with open(path, "rb") as f:
                await self.bot.send_document(chat_id=self.chat_id, document=f, filename=path.name, caption=caption[:900] or None)
        return await self._retry(_send)

    async def healthy(self) -> bool:
        try:
            await self.bot.get_me()
            return True
        except Exception:
            return False


class TelegramApp:
    """Owns the PTB Application lifecycle so it can run inside FastAPI's event loop."""

    def __init__(self, services, settings: Settings | None = None):
        from app.bot import handlers

        self.s = settings or get_settings()
        self.app = Application.builder().token(self.s.telegram_bot_token).build()
        self.messenger = TelegramMessenger(self.app.bot, self.s.telegram_user_id)
        h = handlers.Handlers(services, self.s)
        self.app.add_handler(CommandHandler("start", h.cmd_start))
        self.app.add_handler(CommandHandler("help", h.cmd_help))
        self.app.add_handler(CommandHandler("status", h.cmd_status))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.on_text))
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, h.on_voice))
        self.app.add_handler(MessageHandler(~filters.COMMAND, h.on_unsupported))
        self.app.add_error_handler(h.on_error)
        self.connected = False

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=False)
        self.connected = True

    async def stop(self) -> None:
        self.connected = False
        try:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()
        except Exception:
            log.exception("error while stopping telegram")
