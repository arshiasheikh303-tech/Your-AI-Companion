"""Provider-agnostic LLM interface. Implement LLMProvider to add OpenAI/Gemini/Anthropic later."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    """Any failure talking to the model."""


class LLMUnavailable(LLMError):
    """The model server is unreachable or the model is missing."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    name: str = "base"
    supports_native_tools: bool = False

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send chat messages (role/content dicts). `tools` uses the OpenAI-style function schema."""

    @abstractmethod
    async def health(self) -> bool:
        """True if the provider is reachable and the configured model is usable."""

    async def close(self) -> None:  # optional
        return None
