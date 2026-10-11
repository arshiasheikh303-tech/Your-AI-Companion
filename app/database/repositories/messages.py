from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Conversation, Message, User
from app.timeutil import utcnow


def get_active_conversation(s: Session, user: User) -> Conversation:
    conv = s.scalar(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.id.desc()).limit(1))
    if conv is None:
        conv = Conversation(user_id=user.id)
        s.add(conv)
        s.flush()
    return conv


def add_message(s: Session, user: User, role: str, content: str, kind: str = "chat") -> Message:
    conv = get_active_conversation(s, user)
    msg = Message(conversation_id=conv.id, user_id=user.id, role=role, content=content, kind=kind)
    s.add(msg)
    s.flush()
    return msg


def recent_messages(s: Session, user: User, limit: int) -> list[Message]:
    rows = s.scalars(select(Message).where(Message.user_id == user.id).order_by(Message.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def recent_proactive_texts(s: Session, user: User, limit: int = 10) -> list[str]:
    rows = s.scalars(
        select(Message.content)
        .where(Message.user_id == user.id, Message.kind == "proactive")
        .order_by(Message.id.desc())
        .limit(limit)
    ).all()
    return list(rows)


def count_proactive_since(s: Session, user: User, since: datetime) -> int:
    return s.scalar(
        select(func.count(Message.id)).where(
            Message.user_id == user.id, Message.kind == "proactive", Message.created_at >= since
        )
    ) or 0


def user_message_hours(s: Session, user: User, limit: int = 300) -> list[int]:
    """UTC hour-of-day of recent user messages (for activity pattern learning)."""
    rows = s.scalars(
        select(Message.created_at)
        .where(Message.user_id == user.id, Message.role == "user")
        .order_by(Message.id.desc())
        .limit(limit)
    ).all()
    return [r.hour for r in rows]
