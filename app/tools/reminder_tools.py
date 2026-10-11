from __future__ import annotations

from typing import Any

from app.database.database import session_scope
from app.database.repositories import reminders as repo
from app.timeparse import TimeParseError, parse_when
from app.timeutil import fmt_local, local_now, to_local
from app.tools.base import Tool, ToolContext, ToolError, get_user


def _describe(r) -> str:
    rec = {"none": "", "daily": " (every day)", "weekly": " (every week)", "monthly": " (every month)"}[r.recurrence]
    return f"#{r.id} {r.text} — {fmt_local(r.due_at)}{rec}"


async def create_reminder(ctx: ToolContext, a: dict[str, Any]) -> str:
    try:
        parsed = parse_when(a["when"], local_now().replace(tzinfo=None))
    except TimeParseError as e:
        raise ToolError(f"{e}. Ask the user to say the time again (e.g. 'tomorrow at 7 PM').") from e
    with session_scope() as s:
        user = get_user(s, ctx)
        r = repo.create_reminder(s, user, a["text"], parsed.due_utc, parsed.recurrence, parsed.local_time, parsed.weekday)
        note = f" Note: {', '.join(parsed.assumptions)}." if parsed.assumptions else ""
        return f"Reminder set: {_describe(r)}.{note}"


async def list_reminders(ctx: ToolContext, a: dict[str, Any]) -> str:
    with session_scope() as s:
        user = get_user(s, ctx)
        items = repo.list_reminders(s, user)
        return "\n".join(_describe(r) for r in items) if items else "No active reminders."


async def cancel_reminder(ctx: ToolContext, a: dict[str, Any]) -> str:
    with session_scope() as s:
        user = get_user(s, ctx)
        r = repo.get_reminder(s, user, a["reminder_id"])
        if not r or r.status != "active":
            raise ToolError("no active reminder with that id")
        r.status = "cancelled"
        return f"Cancelled reminder #{r.id}: {r.text}"


async def update_reminder(ctx: ToolContext, a: dict[str, Any]) -> str:
    with session_scope() as s:
        user = get_user(s, ctx)
        r = repo.get_reminder(s, user, a["reminder_id"])
        if not r or r.status != "active":
            raise ToolError("no active reminder with that id")
        if a.get("text"):
            r.text = a["text"][:500]
        if a.get("when"):
            try:
                p = parse_when(a["when"], local_now().replace(tzinfo=None))
            except TimeParseError as e:
                raise ToolError(str(e)) from e
            r.due_at, r.recurrence, r.weekday, r.local_time = p.due_utc, p.recurrence, p.weekday, p.local_time
        return f"Updated: {_describe(r)}"


TOOLS = [
    Tool("create_reminder",
         "Create a one-time or recurring reminder. 'when' is the user's own phrase or an ISO datetime, e.g. "
         "'in 30 minutes', 'tomorrow at 7 PM', 'every Monday at 6 PM', '2026-11-12 09:00'.",
         {"type": "object", "properties": {
             "text": {"type": "string", "description": "What to remind about", "maxLength": 500},
             "when": {"type": "string", "description": "When to fire, in the user's words"}},
          "required": ["text", "when"]}, create_reminder),
    Tool("list_reminders", "List upcoming active reminders.", {"type": "object", "properties": {}}, list_reminders),
    Tool("cancel_reminder", "Cancel a reminder by id.",
         {"type": "object", "properties": {"reminder_id": {"type": "integer"}}, "required": ["reminder_id"]},
         cancel_reminder),
    Tool("update_reminder", "Change a reminder's text and/or time.",
         {"type": "object", "properties": {"reminder_id": {"type": "integer"}, "text": {"type": "string"},
                                           "when": {"type": "string"}}, "required": ["reminder_id"]},
         update_reminder),
]
