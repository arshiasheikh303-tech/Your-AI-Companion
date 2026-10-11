import pytest

from app.tools.base import ToolContext, ToolError, build_registry, validate_args
from app.tools.utility_tools import safe_eval, safe_filename


@pytest.mark.parametrize("expr,val", [("2+3*4", 14), ("sqrt(16)^2", 16.0), ("-2**3", -8), ("10 % 4", 2)])
def test_calculator(expr, val):
    assert safe_eval(expr) == val


@pytest.mark.parametrize("bad", ["__import__('os').system('ls')", "open('x')", "9**9**9", "1/0", "a+1", "x" * 300])
def test_calculator_rejects_unsafe(bad):
    with pytest.raises(ToolError):
        safe_eval(bad)


@pytest.mark.parametrize("name", ["../../etc/passwd", "evil.exe", ".env", "", "noext"])
def test_filename_rejected(name):
    with pytest.raises(ToolError):
        safe_filename(name)


def test_filename_strips_directories():
    assert safe_filename("sub/dir/plan.md") == "plan.md"


def test_validate_args_coerces_and_checks():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}, "s": {"type": "string", "maxLength": 3},
                                               "e": {"type": "string", "enum": ["a", "b"]}}, "required": ["n"]}
    assert validate_args(schema, {"n": "5"}) == {"n": 5}
    for bad in ({}, {"n": "x"}, {"n": 1, "s": "toolong"}, {"n": 1, "e": "z"}):
        with pytest.raises(ToolError):
            validate_args(schema, bad)
    with pytest.raises(ToolError):
        validate_args(schema, "not a dict")


async def test_generate_file_is_sandboxed(uid, env):
    reg = build_registry()
    ctx = ToolContext(user_id=uid)
    out = await reg.execute("generate_file", {"filename": "../../x/plan.md", "content": "# Plan"}, ctx)
    assert "Created" in out
    path = ctx.files_to_send[0]
    assert path.parent == (env / "generated").resolve() and path.read_text() == "# Plan"
    assert (await reg.execute("generate_file", {"filename": "a.exe", "content": "x"}, ctx)).startswith("Error")


async def test_unknown_tool_and_crash_are_contained(uid):
    reg = build_registry()
    assert (await reg.execute("rm_rf", {}, ToolContext(uid))).startswith("Error")
    assert (await reg.execute("create_task", {}, ToolContext(uid))).startswith("Error")  # missing title


async def test_task_management(uid):
    reg, ctx = build_registry(), ToolContext(uid)
    for t in ("Finish portfolio", "Apply to 3 internships", "Study PyTorch"):
        await reg.execute("create_task", {"title": t}, ctx)
    listing = await reg.execute("list_tasks", {}, ctx)
    assert listing.count("[ ]") == 3
    assert "Completed" in await reg.execute("complete_task", {"task_id": 1}, ctx)
    assert (await reg.execute("list_tasks", {}, ctx)).count("[ ]") == 2
    assert "Updated" in await reg.execute("update_task", {"task_id": 2, "title": "Apply to 5 internships"}, ctx)
    assert "Deleted" in await reg.execute("delete_task", {"task_id": 3}, ctx)
    assert (await reg.execute("delete_task", {"task_id": 99}, ctx)).startswith("Error")


async def test_notes_crud_and_search(uid):
    reg, ctx = build_registry(), ToolContext(uid)
    await reg.execute("create_note", {"title": "Project idea", "content": "A friendly AI companion"}, ctx)
    assert "companion" in await reg.execute("search_notes", {"query": "companion"}, ctx)
    assert "friendly" in await reg.execute("read_note", {"note_id": 1}, ctx)
    await reg.execute("update_note", {"note_id": 1, "content": "Updated text"}, ctx)
    assert "Updated text" in await reg.execute("read_note", {"note_id": 1}, ctx)
    assert "Deleted" in await reg.execute("delete_note", {"note_id": 1}, ctx)
    assert "No notes" in await reg.execute("list_notes", {}, ctx)
