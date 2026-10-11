from __future__ import annotations

from typing import Any

from app.database.database import session_scope
from app.database.repositories import tasks as repo
from app.timeparse import TimeParseError, parse_when
from app.timeutil import fmt_local, local_now
from app.tools.base import Tool, ToolContext, ToolError, get_user

_PRI = {1: "high", 2: "normal", 3: "low"}


def _line(t) -> str:
    due = f" (due {fmt_local(t.due_at)})" if t.due_at else ""
    mark = "x" if t.status == "done" else " "
    return f"#{t.id} [{mark}] {t.title} — {_PRI.get(t.priority, 'normal')}{due}"


def _due(a: dict[str, Any]):
    if not a.get("due"):
        return None
    try:
        return parse_when(a["due"], local_now().replace(tzinfo=None)).due_utc
    except TimeParseError as e:
        raise ToolError(str(e)) from e


async def create_task(ctx, a):
    with session_scope() as s:
        user = get_user(s, ctx)
        t = repo.create_task(s, user, a["title"], a.get("notes", ""), int(a.get("priority", 2)), _due(a))
        return "Added task " + _line(t)


async def list_tasks(ctx, a):
    with session_scope() as s:
        user = get_user(s, ctx)
        items = repo.list_tasks(s, user, a.get("status", "open"))
        return "\n".join(_line(t) for t in items) if items else "No tasks."


async def complete_task(ctx, a):
    with session_scope() as s:
        t = repo.complete_task(s, get_user(s, ctx), a["task_id"])
        if not t:
            raise ToolError("no task with that id")
        return "Completed: " + t.title


async def delete_task(ctx, a):
    with session_scope() as s:
        if not repo.delete_task(s, get_user(s, ctx), a["task_id"]):
            raise ToolError("no task with that id")
        return "Deleted task."


async def update_task(ctx, a):
    with session_scope() as s:
        t = repo.get_task(s, get_user(s, ctx), a["task_id"])
        if not t:
            raise ToolError("no task with that id")
        if a.get("title"):
            t.title = a["title"][:300]
        if "notes" in a:
            t.notes = a["notes"]
        if "priority" in a:
            t.priority = max(1, min(3, int(a["priority"])))
        if a.get("due"):
            t.due_at = _due(a)
        return "Updated " + _line(t)


_ID = {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}
TOOLS = [
    Tool("create_task", "Add a to-do item. Call once per separate task.",
         {"type": "object", "properties": {"title": {"type": "string", "maxLength": 300}, "notes": {"type": "string"},
                                           "priority": {"type": "integer", "description": "1 high, 2 normal, 3 low"},
                                           "due": {"type": "string", "description": "optional due date phrase"}},
          "required": ["title"]}, create_task),
    Tool("list_tasks", "List tasks (status: open, done, or all).",
         {"type": "object", "properties": {"status": {"type": "string", "enum": ["open", "done", "all"]}}}, list_tasks),
    Tool("complete_task", "Mark a task done.", _ID, complete_task),
    Tool("delete_task", "Delete a task.", _ID, delete_task),
    Tool("update_task", "Edit a task's title, notes, priority or due date.",
         {"type": "object", "properties": {"task_id": {"type": "integer"}, "title": {"type": "string"},
                                           "notes": {"type": "string"}, "priority": {"type": "integer"},
                                           "due": {"type": "string"}}, "required": ["task_id"]}, update_task),
]
