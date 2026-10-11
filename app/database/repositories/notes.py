from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.models import Note, User


def create_note(s: Session, user: User, title: str, content: str) -> Note:
    n = Note(user_id=user.id, title=title.strip()[:200] or "Untitled", content=content)
    s.add(n)
    s.flush()
    return n


def list_notes(s: Session, user: User) -> list[Note]:
    return list(s.scalars(select(Note).where(Note.user_id == user.id).order_by(Note.updated_at.desc())).all())


def get_note(s: Session, user: User, note_id: int) -> Note | None:
    n = s.get(Note, note_id)
    return n if n and n.user_id == user.id else None


def search_notes(s: Session, user: User, query: str) -> list[Note]:
    like = f"%{query.strip()}%"
    return list(s.scalars(select(Note).where(Note.user_id == user.id,
                or_(Note.title.ilike(like), Note.content.ilike(like))).order_by(Note.updated_at.desc())).all())


def delete_note(s: Session, user: User, note_id: int) -> bool:
    n = get_note(s, user, note_id)
    if n:
        s.delete(n)
        return True
    return False
