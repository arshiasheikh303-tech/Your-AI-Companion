from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import AgentEvent

log = logging.getLogger(__name__)


def log_event(s: Session, event_type: str, detail: object = "", user_id: int | None = None) -> None:
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str, ensure_ascii=False)
    s.add(AgentEvent(user_id=user_id, event_type=event_type, detail=text[:4000]))


def recent_events(s: Session, event_type: str | None = None, limit: int = 30) -> list[AgentEvent]:
    q = select(AgentEvent)
    if event_type:
        q = q.where(AgentEvent.event_type == event_type)
    return list(s.scalars(q.order_by(AgentEvent.id.desc()).limit(limit)).all())
