from __future__ import annotations

from app.database.database import session_scope
from app.database.repositories import notes as repo
from app.tools.base import Tool, ToolContext, ToolError, get_user


def _brief(n) -> str:
    snippet = n.content.replace("\n", " ")
    return f"#{n.id} {n.title}: {snippet[:80]}{'…' if len(snippet) > 80 else ''}"


async def create_note(ctx, a):
    with session_scope() as s:
        n = repo.create_note(s, get_user(s, ctx), a.get("title") or a["content"][:40], a["content"])
        return f"Saved note #{n.id} “{n.title}”."


async def list_notes(ctx, a):
    with session_scope() as s:
        items = repo.list_notes(s, get_user(s, ctx))
        return "\n".join(_brief(n) for n in items[:30]) if items else "No notes yet."


async def read_note(ctx, a):
    with session_scope() as s:
        n = repo.get_note(s, get_user(s, ctx), a["note_id"])
        if not n:
            raise ToolError("no note with that id")
        return f"#{n.id} {n.title}\n{n.content}"


async def update_note(ctx, a):
    with session_scope() as s:
        n = repo.get_note(s, get_user(s, ctx), a["note_id"])
        if not n:
            raise ToolError("no note with that id")
        if a.get("title"):
            n.title = a["title"][:200]
        if a.get("content"):
            n.content = a["content"]
        return f"Updated note #{n.id}."


async def delete_note(ctx, a):
    with session_scope() as s:
        if not repo.delete_note(s, get_user(s, ctx), a["note_id"]):
            raise ToolError("no note with that id")
        return "Deleted note."


async def search_notes(ctx, a):
    with session_scope() as s:
        items = repo.search_notes(s, get_user(s, ctx), a["query"])
        return "\n".join(_brief(n) for n in items[:15]) if items else "No notes match."


_NID = {"type": "object", "properties": {"note_id": {"type": "integer"}}, "required": ["note_id"]}
TOOLS = [
    Tool("create_note", "Save a note (ideas, snippets, anything to keep). Separate from long-term memories.",
         {"type": "object", "properties": {"title": {"type": "string", "maxLength": 200},
                                           "content": {"type": "string"}}, "required": ["content"]}, create_note),
    Tool("list_notes", "List saved notes.", {"type": "object", "properties": {}}, list_notes),
    Tool("read_note", "Show a note's full text.", _NID, read_note),
    Tool("update_note", "Edit a note.", {"type": "object", "properties": {
        "note_id": {"type": "integer"}, "title": {"type": "string"}, "content": {"type": "string"}},
        "required": ["note_id"]}, update_note),
    Tool("delete_note", "Delete a note.", _NID, delete_note),
    Tool("search_notes", "Search notes by keyword.",
         {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, search_notes),
]
