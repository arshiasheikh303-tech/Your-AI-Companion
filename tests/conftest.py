from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from app.ai.base import LLMProvider, LLMResponse, ToolCall
from app.bot.messenger import Messenger

TEST_USER_TG = 42


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Isolated settings + temp SQLite DB for every test."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN")
    monkeypatch.setenv("TELEGRAM_USER_ID", str(TEST_USER_TG))
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setenv("USER_NAME", "Iram")
    monkeypatch.setenv("QUIET_HOURS_START", "23:00")
    monkeypatch.setenv("QUIET_HOURS_END", "08:00")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setattr("app.config.GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr("app.tools.utility_tools.GENERATED_DIR", tmp_path / "generated")
    from app.config import reset_settings_cache
    from app.database import database
    from app.database.models import Base
    reset_settings_cache()
    database.init_engine()
    Base.metadata.create_all(database.get_engine())
    yield tmp_path
    reset_settings_cache()


class FakeProvider(LLMProvider):
    """Scripted LLM. Items are LLMResponse, str, or an Exception to raise."""
    name = "fake"

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[dict] = []
        self.healthy = True

    async def chat(self, messages, tools=None, json_mode=False, temperature=0.7):
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode})
        if not self.script:
            return LLMResponse(content="ok")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item) if isinstance(item, str) else item

    async def health(self):
        return self.healthy


class FakeMessenger(Messenger):
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.texts: list[str] = []
        self.files: list[Path] = []
        self.voices: list[Path] = []

    async def send_text(self, text):
        if self.ok:
            self.texts.append(text)
        return self.ok

    async def send_voice(self, audio_path):
        self.voices.append(audio_path)
        return self.ok

    async def send_file(self, path, caption=""):
        self.files.append(path)
        return self.ok

    async def healthy(self):
        return self.ok


def call(name, **args):
    return LLMResponse(tool_calls=[ToolCall(name, args)])


@pytest.fixture
def make_services():
    from app.ai.agent import Agent
    from app.services import Services
    from app.tools.base import build_registry
    from app.config import get_settings

    def _make(script=None, messenger_ok=True):
        provider = FakeProvider(script)
        registry = build_registry()
        sv = Services(settings=get_settings(), provider=provider, registry=registry,
                      agent=Agent(provider, registry), messenger=FakeMessenger(messenger_ok))
        sv.user_id()
        return sv
    return _make


@pytest.fixture
def uid(make_services):
    from app.config import get_settings
    from app.database.database import session_scope
    from app.database.repositories import users
    with session_scope() as s:
        return users.get_or_create_user(s, TEST_USER_TG, "Iram").id
