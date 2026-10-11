from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Task, User
from app.timeutil import utcnow


def create_task(s: Session, user: User, title: str, notes: str = "", priority: int = 2,
                due_at: datetime | None = None) -> Task:
    t = Task(user_id=user.id, title=title.strip()[:300], notes=notes, priority=max(1, min(3, priority)), due_at=due_at)
    s.add(t)
    s.flush()
    return t


def list_tasks(s: Session, user: User, status: str | None = "open") -> list[Task]:
    q = select(Task).where(Task.user_id == user.id)
    if status in ("open", "done"):
        q = q.where(Task.status == status)
    return list(s.scalars(q.order_by(Task.priority, Task.id)).all())


def get_task(s: Session, user: User, task_id: int) -> Task | None:
    t = s.get(Task, task_id)
    return t if t and t.user_id == user.id else None


def complete_task(s: Session, user: User, task_id: int) -> Task | None:
    t = get_task(s, user, task_id)
    if t:
        t.status = "done"
        t.completed_at = utcnow()
    return t


def delete_task(s: Session, user: User, task_id: int) -> bool:
    t = get_task(s, user, task_id)
    if t:
        s.delete(t)
        return True
    return False
