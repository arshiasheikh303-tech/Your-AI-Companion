from __future__ import annotations

from typing import Any

from app.database.database import session_scope
from app.database.repositories import memories as repo
from app.tools.base import Tool, ToolContext, get_user


async def save_memory(ctx: ToolContext, a: dict[str, Any]) -> str:
    with session_scope() as s:
        user = get_user(s, ctx)
        mem, created = repo.add_memory(
            s, user, a["content"], a.get("category", "fact"),
            confidence=0.95, importance=float(a.get("importance", 0.7)), source="explicit",
        )
        return f"Saved memory #{mem.id}: {mem.content}" if created else f"Already knew something like that (#{mem.id}); updated it."


async def search_memory(ctx: ToolContext, a: dict[str, Any]) -> str:
    with session_scope() as s:
        user = get_user(s, ctx)
        q = a.get("query", "")
        found = repo.search_memories(s, user, q, 10) if q else repo.list_memories(s, user)[:20]
        if not found:
            return "No matching memories."
        return "\n".join(f"#{m.id} [{m.category}] {m.content}" for m in found)


async def forget_memory(ctx: ToolContext, a: dict[str, Any]) -> str:
    with session_scope() as s:
        user = get_user(s, ctx)
        removed = repo.forget(s, user, memory_id=a.get("memory_id"), query=a.get("query"))
        if not removed:
            return "I couldn't find a matching memory to forget."
        return "Forgot: " + "; ".join(m.content for m in removed)


TOOLS = [
    Tool("save_memory",
         "Remember something about the user long-term (facts, preferences, people, goals, dates, instructions). "
         "Use when the user says 'remember this' or shares something important.",
         {"type": "object", "properties": {
             "content": {"type": "string", "description": "The fact, written as a short clear statement", "maxLength": 500},
             "category": {"type": "string", "enum": ["personal_fact", "preference", "person", "goal", "project",
                                                     "important_date", "habit", "communication", "instruction",
                                                     "explicit", "fact"]},
             "importance": {"type": "number", "description": "0 to 1"}},
          "required": ["content"]}, save_memory),
    Tool("search_memory", "Look up what is remembered about the user. Empty query lists everything.",
         {"type": "object", "properties": {"query": {"type": "string"}}}, search_memory),
    Tool("forget_memory", "Forget a stored memory by id or by describing it.",
         {"type": "object", "properties": {"memory_id": {"type": "integer"}, "query": {"type": "string"}}},
         forget_memory),
]
