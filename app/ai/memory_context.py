"""Assemble the agent's context: recent history + relevant long-term memories + preferences."""
from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.database.database import session_scope
from app.database.models import User
from app.database.repositories import memories as mem_repo
from app.database.repositories import messages as msg_repo


@dataclass
class AgentContext:
    user_name: str
    history: list[dict[str, str]]
    memories: list[str]
    preferences: dict[str, str]


def build_context(user_id: int, query: str) -> AgentContext:
    s = get_settings()
    with session_scope() as session:
        user = session.get(User, user_id)
        recent = msg_repo.recent_messages(session, user, s.recent_messages_limit)
        mems = mem_repo.context_memories(session, user, query, s.memory_context_limit)
        prefs = mem_repo.get_preferences(session, user)
        return AgentContext(
            user_name=user.name or s.user_name,
            history=[{"role": m.role, "content": m.content} for m in recent],
            memories=[m.content for m in mems],
            preferences=prefs,
        )
