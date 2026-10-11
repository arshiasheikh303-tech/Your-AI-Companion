"""High-level memory operations used by the agent, scheduler and API."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from app.database.database import session_scope
from app.database.models import Memory, User
from app.database.repositories import events
from app.database.repositories import memories as repo
from app.timeutil import utcnow

log = logging.getLogger(__name__)


class MemoryManager:
    def remember(self, user_id: int, content: str, category: str = "fact", confidence: float = 0.8,
                 importance: float = 0.5, source: str = "auto") -> bool:
        with session_scope() as s:
            user = s.get(User, user_id)
            mem, created = repo.add_memory(s, user, content, category, confidence, importance, source)
            events.log_event(s, "memory_saved" if created else "memory_merged", content, user_id)
            return created

    def list_text(self, user_id: int) -> str:
        with session_scope() as s:
            user = s.get(User, user_id)
            items = repo.list_memories(s, user)
            if not items:
                return "I don't have anything saved about you yet."
            by_cat: dict[str, list[str]] = {}
            for m in items:
                by_cat.setdefault(m.category.replace("_", " "), []).append(f"#{m.id} {m.content}")
            return "\n\n".join(f"{cat.title()}:\n" + "\n".join(f"  • {l}" for l in lines) for cat, lines in by_cat.items())

    def forget_last(self, user_id: int) -> str | None:
        with session_scope() as s:
            user = s.get(User, user_id)
            latest = s.scalar(select(Memory).where(Memory.user_id == user_id, Memory.active.is_(True))
                              .order_by(Memory.updated_at.desc()).limit(1))
            if latest:
                latest.active = False
                return latest.content
            return None

    def maintain(self) -> dict[str, int]:
        """Periodic housekeeping: drop low-value stale auto memories."""
        cutoff = utcnow() - timedelta(days=60)
        removed = 0
        with session_scope() as s:
            stale = s.scalars(select(Memory).where(
                Memory.active.is_(True), Memory.source == "auto", Memory.importance < 0.5,
                Memory.access_count == 0, Memory.created_at < cutoff)).all()
            for m in stale:
                m.active = False
                removed += 1
            if removed:
                events.log_event(s, "memory_maintenance", {"deactivated": removed})
        return {"deactivated": removed}
