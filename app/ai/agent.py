"""The agent: deterministic fast-paths + LLM tool-calling loop (native tools with a JSON fallback)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from app.ai.base import LLMError, LLMProvider, LLMUnavailable
from app.ai.memory_context import build_context
from app.ai.prompts import CANARY, build_system_prompt, detect_mode
from app.config import Settings, get_settings
from app.database.database import session_scope
from app.database.models import User
from app.database.repositories import events, messages, users
from app.memory.extractor import MemoryExtractor
from app.memory.manager import MemoryManager
from app.timeutil import local_now, utcnow
from app.tools.base import ToolContext, ToolRegistry

log = logging.getLogger(__name__)

OFFLINE_REPLY = "Sorry, my AI brain is temporarily unavailable. Try again in a moment."
GENERIC_FAIL = "Hmm, something went wrong on my side. Mind trying that again?"
MAX_TOOL_ROUNDS = 4


@dataclass
class AgentReply:
    text: str
    files: list[Path] = field(default_factory=list)
    ok: bool = True


# ----------------------------------------------------------------------------------------------
# Deterministic fast paths: the things that must work 100% of the time, even on a weak local model.
# ----------------------------------------------------------------------------------------------
_REMEMBER = re.compile(r"^\s*(?:please\s+)?remember(?:\s+this)?(?:\s+that)?\s*[:,\-]?\s*(?P<what>.{3,})$", re.I | re.S)
_LIST_MEM = re.compile(r"\b(what do you (remember|know) about me|show (me )?(my )?memories|list (my )?memories|"
                       r"what have you (remembered|saved) about me)\b", re.I)
_FORGET_THAT = re.compile(r"^\s*(?:please\s+)?forget (?:that|it|this)\s*[.!]?\s*$", re.I)
_STOP = re.compile(r"\b(stop|disable|turn off)\b.*\b(proactive|texting me|messaging me|checking (up )?on me|"
                   r"contacting me)\b|\bdon'?t (message|text|contact) me (anymore|again|first)\b", re.I)
_START = re.compile(r"\b(start|resume|enable|turn on)\b.*\bproactive\b|\byou can (message|text|contact) me (first )?again\b", re.I)
_PAUSE = re.compile(r"\bdon'?t (?:message|text|contact|disturb|bother|ping)(?: me)?(?: for)?\s+(?:(?:the next|about)\s+)?"
                    r"(?P<n>\d+(?:\.\d+)?|an?|one|two|three|four|five|six|a couple of)\s*"
                    r"(?P<u>minutes?|mins?|hours?|hrs?|days?)\b", re.I)
_TONIGHT = re.compile(r"\bdon'?t (?:disturb|message|text|bother|contact)(?: me)?\s+(?:tonight|for the night)\b", re.I)
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "a couple of": 2}


def fast_path(text: str) -> tuple[str, dict] | None:
    """Return (tool_name, args) for requests we can fulfil without the LLM, else None."""
    t = text.strip()
    if _FORGET_THAT.match(t):
        return "__forget_last__", {}
    if _LIST_MEM.search(t):
        return "search_memory", {"query": ""}
    m = _REMEMBER.match(t)
    if m and not re.match(r"\s*(to|me\b)", m.group("what"), re.I):
        return "save_memory", {"content": m.group("what").strip().rstrip("."), "category": "explicit", "importance": 0.8}
    if _TONIGHT.search(t):
        ln = local_now()
        wake = ln.replace(hour=get_settings().quiet_end.hour, minute=get_settings().quiet_end.minute, second=0, microsecond=0)
        if wake <= ln or ln.hour >= 5:  # "tonight" asked in the daytime means the coming night
            wake = wake + timedelta(days=1) if wake.date() == ln.date() else wake
        return "set_contact_preference", {"action": "pause", "hours": round((wake - ln).total_seconds() / 3600, 2)}
    m = _PAUSE.search(t)
    if m:
        raw = m.group("n").lower()
        n = float(raw) if raw.replace(".", "").isdigit() else float(_WORDS.get(raw, 1))
        u = m.group("u").lower()
        hours = n / 60 if u.startswith("min") else n * 24 if u.startswith("day") else n
        return "set_contact_preference", {"action": "pause", "hours": hours}
    if _START.search(t):
        return "set_contact_preference", {"action": "start"}
    if _STOP.search(t):
        return "set_contact_preference", {"action": "stop"}
    return None


def parse_json_object(raw: str) -> dict | None:
    """Tolerantly pull a JSON object out of model output (fences, prose, trailing commas)."""
    if not raw:
        return None
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        c = raw[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                chunk = raw[start:i + 1]
                for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
                    try:
                        obj = json.loads(candidate)
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        continue
                return None
    return None


def sanitize_reply(text: str) -> str:
    text = text.strip()
    if CANARY in text or "Hard rules:" in text:
        return "I can't share my internal instructions, but I'm happy to help with something else!"
    return text


def store_outgoing(user_id: int, text: str, kind: str = "chat", proactive: bool = False) -> None:
    """Persist anything the agent sent (replies, reminders, proactive messages) and update activity stats."""
    with session_scope() as s:
        user = s.get(User, user_id)
        messages.add_message(s, user, "assistant", text, kind)
        users.record_agent_message(s, user, proactive=proactive)


class Agent:
    def __init__(self, provider: LLMProvider, registry: ToolRegistry, memory: MemoryManager | None = None,
                 settings: Settings | None = None):
        self.provider = provider
        self.registry = registry
        self.memory = memory or MemoryManager()
        self.extractor = MemoryExtractor(provider, self.memory)
        self.s = settings or get_settings()
        self._json_mode = False
        self._lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ public
    async def handle_message(self, user_id: int, text: str, kind: str = "chat") -> AgentReply:
        text = text.strip()
        if not text:
            return AgentReply("I didn't catch anything there — say that again?", ok=False)
        log.info("incoming message", extra={"chars": len(text), "kind": kind})
        try:
            with session_scope() as s:
                user = s.get(User, user_id)
                users.record_user_activity(s, user)
                messages.add_message(s, user, "user", text, kind)
        except Exception:
            log.exception("database failure while recording message")
            return AgentReply(GENERIC_FAIL, ok=False)

        async with self._lock:
            reply = await self._respond(user_id, text)

        if reply.ok:
            try:
                store_outgoing(user_id, reply.text)
            except Exception:
                log.exception("database failure while storing reply")
            self._spawn(self.extractor.extract_and_save(user_id, text))
        log.info("agent response", extra={"chars": len(reply.text), "ok": reply.ok})
        return reply

    async def drain(self) -> None:
        """Wait for background work (used by tests and shutdown)."""
        if self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)

    # ------------------------------------------------------------------ internals
    def _spawn(self, coro) -> None:
        task = asyncio.create_task(self._safe(coro))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    @staticmethod
    async def _safe(coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("background task failed")

    async def _respond(self, user_id: int, text: str) -> AgentReply:
        ctx = ToolContext(user_id=user_id)
        fp = fast_path(text)
        if fp:
            name, args = fp
            if name == "__forget_last__":
                gone = self.memory.forget_last(user_id)
                return AgentReply(f"Done — I've forgotten: “{gone}”." if gone else "There's nothing saved to forget.")
            result = await self.registry.execute(name, args, ctx)
            return AgentReply(self._friendly_fast(name, result), ctx.files_to_send)
        try:
            if self._json_mode:
                return await self._json_loop(user_id, text, ctx)
            try:
                return await self._native_loop(user_id, text, ctx)
            except LLMError as e:
                if str(e) == "tools_unsupported":
                    log.warning("model lacks native tool calling; switching to JSON tool mode")
                    self._json_mode = True
                    return await self._json_loop(user_id, text, ctx)
                raise
        except LLMUnavailable:
            log.warning("ollama unavailable")
            return AgentReply(OFFLINE_REPLY, ok=False)
        except LLMError:
            log.exception("llm error")
            return AgentReply(OFFLINE_REPLY, ok=False)
        except Exception:
            log.exception("agent failure")
            return AgentReply(GENERIC_FAIL, ok=False)

    @staticmethod
    def _friendly_fast(name: str, result: str) -> str:
        if name == "save_memory":
            return "Got it, I'll remember that. 🧠" if result.startswith("Saved") else "Already had that one — updated it."
        if name == "search_memory":
            return "Here's what I remember about you:\n\n" + result if not result.startswith("No ") else \
                "I don't have anything saved about you yet — tell me stuff!"
        return result

    def _system(self, user_id: int, text: str, tools_text: str | None = None):
        c = build_context(user_id, text)
        system = build_system_prompt(
            name=self.s.companion_name, user_name=c.user_name, now_local=local_now(), memories=c.memories,
            preferences=c.preferences, mode=detect_mode(text), tools_text=tools_text,
        )
        # history already includes the current user message (stored before this call)
        return system, c.history

    async def _native_loop(self, user_id: int, text: str, ctx: ToolContext) -> AgentReply:
        system, history = self._system(user_id, text)
        msgs: list[dict] = [{"role": "system", "content": system}] + history
        schemas = self.registry.schemas()
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.provider.chat(msgs, tools=schemas)
            if not resp.tool_calls:
                final = sanitize_reply(resp.content) or GENERIC_FAIL
                return AgentReply(final, ctx.files_to_send)
            msgs.append({"role": "assistant", "content": resp.content,
                         "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in resp.tool_calls]})
            for call in resp.tool_calls:
                result = await self._run_tool(user_id, call.name, call.arguments, ctx)
                msgs.append({"role": "tool", "tool_name": call.name, "content": result})
        # too many rounds: ask for a wrap-up without tools
        resp = await self.provider.chat(msgs + [{"role": "user", "content": "(Summarise what you did for the user.)"}])
        return AgentReply(sanitize_reply(resp.content) or GENERIC_FAIL, ctx.files_to_send)

    async def _json_loop(self, user_id: int, text: str, ctx: ToolContext) -> AgentReply:
        system, history = self._system(user_id, text, tools_text=self.registry.describe())
        msgs: list[dict] = [{"role": "system", "content": system}] + history
        retried = False
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.provider.chat(msgs, json_mode=True, temperature=0.4)
            obj = parse_json_object(resp.content)
            if obj is None:
                if not retried:
                    retried = True
                    msgs += [{"role": "assistant", "content": resp.content[:500]},
                             {"role": "user", "content": 'That was not valid JSON. Reply ONLY with {"tool":..., "args":..., "reply":...}.'}]
                    continue
                plain = resp.content.strip()
                if plain and not plain.startswith(("{", "[")):
                    return AgentReply(sanitize_reply(plain), ctx.files_to_send)
                return AgentReply(GENERIC_FAIL, ok=False)
            tool = obj.get("tool")
            reply = obj.get("reply") if isinstance(obj.get("reply"), str) else ""
            if not tool or str(tool).lower() in ("null", "none"):
                return AgentReply(sanitize_reply(reply) or GENERIC_FAIL, ctx.files_to_send)
            args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
            result = await self._run_tool(user_id, str(tool), args, ctx)
            msgs += [{"role": "assistant", "content": json.dumps(obj)[:1500]},
                     {"role": "user", "content": f"TOOL RESULT for {tool}: {result}\nNow reply to the user in JSON "
                                                 f'with tool null: {{"tool": null, "args": {{}}, "reply": "..."}}'}]
        return AgentReply(GENERIC_FAIL, ok=False)

    async def _run_tool(self, user_id: int, name: str, args: dict, ctx: ToolContext) -> str:
        log.info("tool call", extra={"tool": name})
        result = await self.registry.execute(name, args, ctx)
        try:
            with session_scope() as s:
                events.log_event(s, "tool_call", {"tool": name, "result": result[:300]}, user_id)
        except Exception:
            log.exception("failed to log tool event")
        return result
