from __future__ import annotations

from app.ai.base import LLMProvider
from app.config import get_settings


def get_provider() -> LLMProvider:
    name = get_settings().ai_provider.lower()
    if name == "ollama":
        from app.ai.ollama import OllamaProvider

        return OllamaProvider()
    raise ValueError(
        f"AI_PROVIDER '{name}' is not implemented. Subclass app.ai.base.LLMProvider and register it here."
    )
