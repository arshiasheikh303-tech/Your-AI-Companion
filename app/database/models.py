"""SQLAlchemy models. All datetimes are stored as naive UTC."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Proactive-contact controls
    proactive_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    voice_responses: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Activity tracking
    last_user_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_agent_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_user_response_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_proactive_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unanswered_proactive: Mapped[int] = mapped_column(Integer, default=0)
    escalation_stage: Mapped[int] = mapped_column(Integer, default=0)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    summary: Mapped[str] = mapped_column(Text, default="")

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="chat")  # chat | proactive | reminder | voice
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_user_active", "user_id", "active"),
        Index("ix_memories_category", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(40), default="fact")
    content: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    source: Mapped[str] = mapped_column(String(20), default="auto")  # auto | explicit
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)


class UserPreference(Base):
    __tablename__ = "preferences"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_pref_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(300))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | done
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 high .. 3 low
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_status_due", "status", "due_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(String(500))
    due_at: Mapped[datetime] = mapped_column(DateTime)  # next fire time, UTC
    recurrence: Mapped[str] = mapped_column(String(20), default="none")  # none|daily|weekly|monthly
    local_time: Mapped[str] = mapped_column(String(5), default="")  # HH:MM in user's tz (recurring)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Mon (weekly)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|done|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    times_sent: Mapped[int] = mapped_column(Integer, default=0)


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_user", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AgentEvent(Base):
    """Audit trail: tool calls, proactive decisions, errors."""

    __tablename__ = "agent_events"
    __table_args__ = (Index("ix_events_type_created", "event_type", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
