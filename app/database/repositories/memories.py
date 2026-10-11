from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Memory, User, UserPreference
from app.timeutil import utcnow

CATEGORIES = {
    "personal_fact", "preference", "person", "goal", "project", "important_date",
    "habit", "communication", "instruction", "explicit", "fact",
}
_WORD = re.compile(r"[a-z0-9']+")


_ORDINAL = re.compile(r"(\d+)(st|nd|rd|th)\b")


def _tokens(text: str) -> set[str]:
    text = _ORDINAL.sub(r"\1", text.lower())
    return {w for w in _WORD.findall(text) if len(w) > 2 or w.isdigit()}


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def add_memory(
    s: Session, user: User, content: str, category: str = "fact",
    confidence: float = 0.8, importance: float = 0.5, source: str = "auto",
) -> tuple[Memory, bool]:
    """Insert a memory, merging near-duplicates. Returns (memory, created)."""
    content = content.strip()
    category = category if category in CATEGORIES else "fact"
    confidence = max(0.0, min(1.0, confidence))
    importance = max(0.0, min(1.0, importance))
    for m in list_memories(s, user):
        if _similar(m.content, content) >= 0.6:
            m.content = content if len(content) >= len(m.content) else m.content
            m.confidence = max(m.confidence, confidence)
            m.importance = max(m.importance, importance)
            if source == "explicit":
                m.source = "explicit"
            m.updated_at = utcnow()
            return m, False
    mem = Memory(user_id=user.id, content=content, category=category, confidence=confidence,
                 importance=importance, source=source)
    s.add(mem)
    s.flush()
    return mem, True


def list_memories(s: Session, user: User, category: str | None = None) -> list[Memory]:
    q = select(Memory).where(Memory.user_id == user.id, Memory.active.is_(True))
    if category:
        q = q.where(Memory.category == category)
    return list(s.scalars(q.order_by(Memory.importance.desc(), Memory.id.desc())).all())


def search_memories(s: Session, user: User, query: str, limit: int = 10) -> list[Memory]:
    """Keyword relevance, boosted by importance/confidence/recency."""
    qt = _tokens(query)
    scored: list[tuple[float, Memory]] = []
    now = utcnow()
    for m in list_memories(s, user):
        overlap = len(qt & _tokens(m.content)) if qt else 0
        if qt and overlap == 0:
            continue
        age_days = max((now - m.updated_at).days, 0)
        recency = 1.0 / (1.0 + age_days / 30)
        score = overlap * 2 + m.importance + m.confidence * 0.5 + recency * 0.5
        scored.append((score, m))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [m for _, m in scored[:limit]]


def context_memories(s: Session, user: User, query: str, limit: int) -> list[Memory]:
    """Most relevant to the query, topped up with the most important overall."""
    picked = search_memories(s, user, query, limit=limit) if query.strip() else []
    seen = {m.id for m in picked}
    for m in list_memories(s, user):
        if len(picked) >= limit:
            break
        if m.id not in seen and (m.importance >= 0.6 or m.source == "explicit"):
            picked.append(m)
            seen.add(m.id)
    now = utcnow()
    for m in picked:
        m.last_accessed_at = now
        m.access_count += 1
    return picked


def forget(s: Session, user: User, memory_id: int | None = None, query: str | None = None) -> list[Memory]:
    removed: list[Memory] = []
    if memory_id is not None:
        m = s.get(Memory, memory_id)
        if m and m.user_id == user.id and m.active:
            m.active = False
            removed.append(m)
    elif query:
        for m in search_memories(s, user, query, limit=3):
            if _similar(m.content, query) >= 0.25 or len(_tokens(query)) <= 2:
                m.active = False
                removed.append(m)
    return removed


def set_preference(s: Session, user: User, key: str, value: str) -> None:
    pref = s.scalar(select(UserPreference).where(UserPreference.user_id == user.id, UserPreference.key == key))
    if pref:
        pref.value = value
    else:
        s.add(UserPreference(user_id=user.id, key=key, value=value))


def get_preferences(s: Session, user: User) -> dict[str, str]:
    return {p.key: p.value for p in s.scalars(select(UserPreference).where(UserPreference.user_id == user.id))}
