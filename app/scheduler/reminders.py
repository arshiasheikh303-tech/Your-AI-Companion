"""Reminder processing. Reminders live in SQLite and a polling job fires what's due, so nothing is lost on restart."""
from __future__ import annotations

import logging
from datetime import timedelta

from app.database.database import session_scope
from app.database.models import Reminder
from app.database.repositories import events
from app.database.repositories import reminders as repo
from app.timeparse import next_occurrence
from app.timeutil import local_now, local_to_utc, to_local, utcnow

log = logging.getLogger(__name__)
CATCH_UP_LIMIT = timedelta(hours=36)  # one-time reminders missed for longer than this are marked done quietly


def _format(r: Reminder, late: timedelta) -> str:
    base = f"⏰ Reminder: {r.text}"
    if late > timedelta(minutes=10):
        base += f" (this was due {to_local(r.due_at):%I:%M %p}, sorry for the delay)".replace(" 0", " ")
    return base


async def process_due_reminders(services) -> int:
    """Send every due reminder. Advances recurring ones. Returns how many were sent."""
    with session_scope() as s:
        due = [(r.id, r.text, r.due_at, r.recurrence, r.local_time, r.weekday) for r in repo.due_reminders(s)]
    sent = 0
    for rid, text, due_at, recurrence, local_time, weekday in due:
        late = utcnow() - due_at
        class _R:  # lightweight view for formatting
            pass
        r = _R()
        r.text, r.due_at = text, due_at
        if recurrence == "none" and late > CATCH_UP_LIMIT:
            _finish(rid, advance=False, skipped=True)
            continue
        ok = await services.deliver(_format(r, late), kind="reminder")
        if not ok:
            log.warning("reminder delivery failed; will retry next tick", extra={"reminder_id": rid})
            continue
        _finish(rid, advance=recurrence != "none")
        sent += 1
        log.info("reminder sent", extra={"reminder_id": rid})
    return sent


def _finish(rid: int, advance: bool, skipped: bool = False) -> None:
    with session_scope() as s:
        r = s.get(Reminder, rid)
        if r is None or r.status != "active":
            return
        if not skipped:
            r.last_sent_at = utcnow()
            r.times_sent += 1
        if advance:
            nxt = next_occurrence(r.recurrence, r.local_time, r.weekday, local_now().replace(tzinfo=None))
            if nxt is not None:
                r.due_at = local_to_utc(nxt)
                return
        r.status = "done"
        events.log_event(s, "reminder_done", {"id": rid, "skipped": skipped}, r.user_id)
