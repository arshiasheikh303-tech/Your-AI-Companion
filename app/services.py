"""Service container wiring the components together (the single place that knows about all of them)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.agent import Agent, store_outgoing
from app.ai.base import LLMProvider
from app.bot.messenger import Messenger
from app.config import Settings
from app.database.database import session_scope
from app.database.models import User
from app.database.repositories import users
from app.tools.base import ToolRegistry
from app.voice.transcription import Transcriber
from app.voice.tts import TextToSpeech

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    provider: LLMProvider
    registry: ToolRegistry
    agent: Agent
    messenger: Messenger | None = None
    transcriber: Transcriber | None = None
    tts: TextToSpeech | None = None
    scheduler: Any = None
    telegram: Any = None
    started_at: Any = None
    _user_id: int | None = field(default=None, repr=False)

    def user_id(self) -> int:
        """DB id of the (single) whitelisted user, created on first use."""
        if self._user_id is None:
            with session_scope() as s:
                u = users.get_or_create_user(s, self.settings.telegram_user_id, self.settings.user_name)
                self._user_id = u.id
        return self._user_id

    def wants_voice(self) -> bool:
        with session_scope() as s:
            u = s.get(User, self.user_id())
            pref = u.voice_responses if u and u.voice_responses is not None else self.settings.voice_responses
        return bool(pref and self.tts and self.tts.available())

    async def deliver(self, text: str, kind: str = "chat", proactive: bool = False, files: list[Path] | None = None,
                      voice: bool | None = None) -> bool:
        """Send a message to the user via the messenger and record it. Returns True if it was sent."""
        if self.messenger is None:
            return False
        ok = await self.messenger.send_text(text)
        if not ok:
            return False
        try:
            store_outgoing(self.user_id(), text, kind=kind, proactive=proactive)
        except Exception:
            log.exception("failed to store outgoing message")
        for f in files or []:
            await self.messenger.send_file(f)
        want_voice = self.wants_voice() if voice is None else voice
        if want_voice and self.tts:
            await self.send_voice_safely(text)
        return True

    async def send_voice_safely(self, text: str) -> bool:
        if not (self.tts and self.messenger and self.tts.available()):
            return False
        try:
            audio = await self.tts.synthesize(text)
        except Exception:
            log.warning("tts failed; text-only fallback")
            return False
        try:
            return await self.messenger.send_voice(audio)
        finally:
            audio.unlink(missing_ok=True)
