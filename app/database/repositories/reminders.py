from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Reminder, User
from app.timeutil import utcnow


def create_reminder(s: Session, user: User, text: str, due_at: datetime, recurrence: str = "none",
                    local_time: str = "", weekday: int | None = None) -> Reminder:
    r = Reminder(user_id=user.id, text=text.strip()[:500], due_at=due_at, recurrence=recurrence,
                 local_time=local_time, weekday=weekday)
    s.add(r)
    s.flush()
    return r


def list_reminders(s: Session, user: User, include_inactive: bool = False) -> list[Reminder]:
    q = select(Reminder).where(Reminder.user_id == user.id)
    if not include_inactive:
        q = q.where(Reminder.status == "active")
    return list(s.scalars(q.order_by(Reminder.due_at)).all())


def get_reminder(s: Session, user: User, reminder_id: int) -> Reminder | None:
    r = s.get(Reminder, reminder_id)
    return r if r and r.user_id == user.id else None


def due_reminders(s: Session, now: datetime | None = None) -> list[Reminder]:
    now = now or utcnow()
    return list(s.scalars(select(Reminder).where(Reminder.status == "active", Reminder.due_at <= now)
                          .order_by(Reminder.due_at)).all())


def upcoming_reminders(s: Session, user: User, within_hours: float) -> list[Reminder]:
    from datetime import timedelta
    now = utcnow()
    return list(s.scalars(select(Reminder).where(
        Reminder.user_id == user.id, Reminder.status == "active",
        Reminder.due_at <= now + timedelta(hours=within_hours)).order_by(Reminder.due_at)).all())
