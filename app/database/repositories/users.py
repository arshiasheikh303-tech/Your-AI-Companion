from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import User
from app.timeutil import utcnow


def get_or_create_user(s: Session, telegram_id: int, name: str = "") -> User:
    user = s.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, name=name)
        s.add(user)
        s.flush()
    elif name and not user.name:
        user.name = name
    return user


def get_primary_user(s: Session) -> User | None:
    return s.scalar(select(User).order_by(User.id).limit(1))


def record_user_activity(s: Session, user: User) -> None:
    """Called when the user sends anything."""
    now = utcnow()
    user.last_user_message_at = now
    user.last_active_at = now
    if user.last_agent_message_at and (
        user.last_user_response_at is None or user.last_user_response_at < user.last_agent_message_at
    ):
        user.last_user_response_at = now
    user.unanswered_proactive = 0
    user.escalation_stage = 0


def record_agent_message(s: Session, user: User, proactive: bool = False) -> None:
    now = utcnow()
    user.last_agent_message_at = now
    if proactive:
        user.last_proactive_at = now
        user.unanswered_proactive += 1


def is_paused(user: User, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return bool(user.paused_until and user.paused_until > now)


def pause_for(user: User, hours: float) -> datetime:
    user.paused_until = utcnow() + timedelta(hours=hours)
    return user.paused_until
