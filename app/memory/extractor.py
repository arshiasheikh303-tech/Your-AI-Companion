"""Automatic memory extraction: decides what's worth saving from a user message."""
from __future__ import annotations

import json
import logging
import re

from app.ai.base import LLMError, LLMProvider
from app.ai.prompts import EXTRACT_PROMPT
from app.memory.manager import MemoryManager

log = logging.getLogger(__name__)

_FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'll|my|me|we|our|mine)\b", re.I)
_SIGNAL = re.compile(
    r"\b(my|i am|i'm|i hate|i love|i like|i prefer|i don't like|i work|i study|i live|birthday|exam|deadline|"
    r"goal|always|never|usually|call me|name is|friend|mom|dad|sister|brother|girlfriend|boyfriend|wife|husband|"
    r"project|learning|allergic|favorite|favourite)\b", re.I)


def worth_extracting(text: str) -> bool:
    """Cheap pre-filter to avoid an LLM call for chit-chat."""
    t = text.strip()
    if len(t) < 12 or t.endswith("?") and not _SIGNAL.search(t):
        return False
    return bool(_FIRST_PERSON.search(t) and _SIGNAL.search(t))


def parse_extraction(raw: str) -> list[dict]:
    """Tolerant parse of the model's JSON (handles code fences, trailing prose, malformed output)."""
    if not raw:
        return []
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return []
    items = data.get("memories") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("content"), str) and it["content"].strip():
            out.append(it)
    return out[:5]


class MemoryExtractor:
    MIN_CONFIDENCE = 0.6
    MIN_IMPORTANCE = 0.4

    def __init__(self, provider: LLMProvider, manager: MemoryManager):
        self.provider = provider
        self.manager = manager

    async def extract_and_save(self, user_id: int, text: str) -> list[str]:
        if not worth_extracting(text):
            return []
        try:
            resp = await self.provider.chat(
                [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": text}],
                json_mode=True, temperature=0.1,
            )
        except LLMError:
            log.info("memory extraction skipped: model unavailable")
            return []
        saved = []
        for it in parse_extraction(resp.content):
            try:
                conf = float(it.get("confidence", 0.7))
                imp = float(it.get("importance", 0.5))
            except (TypeError, ValueError):
                continue
            if conf < self.MIN_CONFIDENCE or imp < self.MIN_IMPORTANCE:
                continue
            created = self.manager.remember(user_id, it["content"], str(it.get("category", "fact")), conf, imp, "auto")
            if created:
                saved.append(it["content"])
        if saved:
            log.info("memories auto-saved", extra={"count": len(saved)})
        return saved
