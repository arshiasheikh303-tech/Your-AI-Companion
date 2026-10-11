import httpx
import pytest
from fastapi.testclient import TestClient
from telegram.error import NetworkError, TelegramError

from app.ai.base import LLMError, LLMUnavailable
from app.ai.ollama import OllamaProvider, strip_thinking
from app.bot.handlers import Handlers, is_authorized
from app.bot.telegram import TelegramMessenger, split_message
from app.config import get_settings
from app.logging_setup import RedactingJsonFormatter
from tests.conftest import TEST_USER_TG


def provider_with(handler):
    client = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    return OllamaProvider(get_settings(), client)


async def test_ollama_parses_tool_calls_and_strips_think():
    def handler(req):
        return httpx.Response(200, json={"message": {"content": "<think>hmm</think>Hi!", "tool_calls": [
            {"function": {"name": "create_task", "arguments": {"title": "x"}}}]}})
    r = await provider_with(handler).chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert r.content == "Hi!" and r.tool_calls[0].name == "create_task"


async def test_ollama_connection_error_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(LLMUnavailable):
        await provider_with(handler).chat([{"role": "user", "content": "hi"}])


async def test_ollama_missing_model_and_bad_json():
    with pytest.raises(LLMUnavailable, match="ollama pull"):
        await provider_with(lambda r: httpx.Response(404, text="model not found")).chat([{"role": "user", "content": "x"}])
    with pytest.raises(LLMError):
        await provider_with(lambda r: httpx.Response(200, text="<html>")).chat([{"role": "user", "content": "x"}])


async def test_ollama_retries_without_think_and_flags_no_tools():
    seen = []
    def handler(req):
        import json
        body = json.loads(req.content)
        seen.append(body)
        if "think" in body:
            return httpx.Response(400, text='{"error":"\\"model\\" does not support thinking"}')
        return httpx.Response(200, json={"message": {"content": "ok"}})
    p = provider_with(handler)
    assert (await p.chat([{"role": "user", "content": "x"}])).content == "ok"
    assert "think" not in seen[-1]

    def no_tools(req):
        return httpx.Response(400, text='{"error":"registry.ollama.ai/x does not support tools"}')
    p2 = provider_with(no_tools)
    with pytest.raises(LLMError, match="tools_unsupported"):
        await p2.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])


def test_strip_thinking():
    assert strip_thinking("<think>a\nb</think>\n\nanswer") == "answer"
    assert strip_thinking("thoughts</think>answer") == "answer"


# ---------------------------------------------------------------- telegram
class FakeUser:
    def __init__(self, id): self.id = id
class FakeUpdate:
    def __init__(self, id, text="hi"):
        self.effective_user = FakeUser(id) if id is not None else None
        self.message = type("M", (), {"text": text})()


def test_whitelist_authentication():
    s = get_settings()
    assert is_authorized(FakeUpdate(TEST_USER_TG), s)
    assert not is_authorized(FakeUpdate(999), s)
    assert not is_authorized(FakeUpdate(None), s)


async def test_unauthorized_user_never_reaches_agent(make_services):
    sv = make_services()
    h = Handlers(sv, get_settings())
    await h.on_text(FakeUpdate(999, "hack me"), None)
    assert not sv.provider.calls and not sv.messenger.texts


async def test_authorized_text_roundtrip(make_services):
    sv = make_services(["Hey there!"])
    h = Handlers(sv, get_settings())
    class Chat:
        async def send_action(self, a): pass
    u = FakeUpdate(TEST_USER_TG, "hello")
    u.effective_chat = Chat()
    await h.on_text(u, None)
    assert sv.messenger.texts == ["Hey there!"]


def test_split_message():
    parts = split_message("para\n\n" * 1500)
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
    assert split_message("short") == ["short"]


class FlakyBot:
    def __init__(self, exc): self.exc, self.calls = exc, 0
    async def send_message(self, **kw):
        self.calls += 1
        raise self.exc
    async def get_me(self): raise self.exc


async def test_telegram_failures_never_raise(monkeypatch):
    async def nosleep(*a): pass
    monkeypatch.setattr("app.bot.telegram.asyncio.sleep", nosleep)
    m = TelegramMessenger(FlakyBot(NetworkError("down")), 1)
    assert await m.send_text("hi") is False and m.bot.calls == 3
    m = TelegramMessenger(FlakyBot(TelegramError("bad")), 1)
    assert await m.send_text("hi") is False and m.bot.calls == 1
    assert await m.healthy() is False


def test_logs_never_contain_secrets():
    import logging
    f = RedactingJsonFormatter(["123456:SECRET-TOKEN"])
    rec = logging.LogRecord("x", logging.ERROR, "", 0, "url https://api.telegram.org/bot123456:SECRET-TOKEN/send", (), None)
    assert "SECRET-TOKEN" not in f.format(rec)


# ---------------------------------------------------------------- API
@pytest.fixture
def client():
    from app.main import create_app
    with TestClient(create_app(start_background=False)) as c:
        yield c


def test_api_requires_token(client):
    assert client.get("/health").status_code == 200
    for path in ("/status", "/memories", "/tasks", "/reminders", "/conversations"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-API-Token": "wrong"}).status_code == 401
        assert client.get(path, headers={"X-API-Token": "test-token"}).status_code == 200
