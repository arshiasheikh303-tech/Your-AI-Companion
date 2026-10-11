"""Ollama provider (default, fully local and free)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.ai.base import LLMError, LLMProvider, LLMResponse, LLMUnavailable, ToolCall
from app.config import Settings, get_settings

log = logging.getLogger(__name__)
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # unterminated opening tag stripped by server
        text = text.split("</think>", 1)[1]
    return text.strip()


class OllamaProvider(LLMProvider):
    name = "ollama"
    supports_native_tools = True

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.s = settings or get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=self.s.ollama_host.rstrip("/"), timeout=httpx.Timeout(self.s.ollama_timeout_seconds, connect=5)
        )
        self._tools_unsupported = False
        self._think_unsupported = False

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post("/api/chat", json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise LLMUnavailable("Ollama is not reachable") from e
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {type(e).__name__}") from e
        if resp.status_code >= 400:
            body = resp.text[:300]
            if resp.status_code == 404:
                raise LLMUnavailable(f"Model '{self.s.ollama_model}' not found. Run: ollama pull {self.s.ollama_model}")
            raise LLMError(f"Ollama HTTP {resp.status_code}: {body}")
        try:
            return resp.json()
        except ValueError as e:
            raise LLMError("Ollama returned invalid JSON") from e

    async def chat(self, messages, tools=None, json_mode=False, temperature=0.7) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.s.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        if tools and not self._tools_unsupported:
            payload["tools"] = tools
        if not self._think_unsupported:
            payload["think"] = False

        for _ in range(3):
            try:
                data = await self._post_chat(payload)
                break
            except LLMError as e:
                msg = str(e).lower()
                if "does not support thinking" in msg or "think" in msg and "support" in msg:
                    self._think_unsupported = True
                    payload.pop("think", None)
                    continue
                if "does not support tools" in msg or ("tools" in msg and "support" in msg):
                    self._tools_unsupported = True
                    payload.pop("tools", None)
                    raise LLMError("tools_unsupported") from e
                raise
        else:
            raise LLMError("Ollama request failed after retries")

        message = data.get("message") or {}
        calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if fn.get("name"):
                calls.append(ToolCall(name=fn["name"], arguments=args if isinstance(args, dict) else {}))
        return LLMResponse(content=strip_thinking(message.get("content") or ""), tool_calls=calls)

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/api/tags", timeout=4)
            resp.raise_for_status()
            names = [m.get("name", "") for m in resp.json().get("models", [])]
            wanted = self.s.ollama_model
            return any(n == wanted or n.split(":")[0] == wanted.split(":")[0] and ":" not in wanted for n in names)
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
