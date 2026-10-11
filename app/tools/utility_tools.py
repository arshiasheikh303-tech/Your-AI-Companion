from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any

from app.config import GENERATED_DIR
from app.database.database import session_scope
from app.database.repositories import events, users
from app.search.base import get_search_provider
from app.timeutil import local_now, utcnow
from app.tools.base import Tool, ToolContext, ToolError, get_user

# ---------- calculator (safe AST evaluator, no eval) ----------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log,
          "log10": math.log10, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil}
_CONST = {"pi": math.pi, "e": math.e}


def safe_eval(expr: str) -> float:
    if len(expr) > 200:
        raise ToolError("expression too long")

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            l, r = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(r) > 1000:
                raise ToolError("exponent too large")
            return _BIN[type(n.op)](l, r)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONST:
            return _CONST[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ToolError("unsupported expression")

    try:
        tree = ast.parse(expr.replace("^", "**").replace("×", "*").replace("÷", "/"), mode="eval")
        return ev(tree)
    except ToolError:
        raise
    except ZeroDivisionError as e:
        raise ToolError("division by zero") from e
    except (SyntaxError, ValueError, OverflowError, TypeError) as e:
        raise ToolError("couldn't evaluate that expression") from e


async def calculate(ctx, a):
    val = safe_eval(a["expression"])
    return f"{a['expression']} = {round(val, 10):g}" if isinstance(val, float) else f"{a['expression']} = {val}"


async def get_current_time(ctx, a):
    n = local_now()
    return n.strftime("%A, %B %d, %Y, %I:%M %p ") + str(n.tzinfo)


# ---------- file generation ----------
ALLOWED_EXT = {".txt", ".md", ".py", ".csv", ".json", ".html", ".js", ".css", ".yaml", ".yml"}
MAX_FILE_BYTES = 500_000


def safe_filename(name: str) -> str:
    name = name.strip().replace("\\", "/").split("/")[-1]  # drop any directory part
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name or name.startswith("."):
        raise ToolError("invalid file name")
    stem, dot, ext = name.rpartition(".")
    if not dot or f".{ext.lower()}" not in ALLOWED_EXT:
        raise ToolError(f"file type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    return name[:100]


async def generate_file(ctx: ToolContext, a: dict[str, Any]) -> str:
    name = safe_filename(a["filename"])
    content = a["content"]
    if len(content.encode()) > MAX_FILE_BYTES:
        raise ToolError("file too large")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = (GENERATED_DIR / name).resolve()
    if GENERATED_DIR.resolve() not in path.parents:
        raise ToolError("invalid path")
    if path.exists():  # never overwrite silently
        path = path.with_name(f"{path.stem}_{utcnow():%H%M%S}{path.suffix}")
    path.write_text(content, encoding="utf-8")
    ctx.files_to_send.append(path)
    return f"Created file {path.name} (it will be sent to the user)."


# ---------- web search ----------
async def web_search(ctx, a):
    provider = get_search_provider()
    if provider is None:
        raise ToolError("web search isn't configured")
    try:
        results = await provider.search(a["query"], 5)
    except Exception:
        raise ToolError("web search failed right now")
    if not results:
        return "No results."
    return "\n".join(f"- {r.title}: {r.snippet} ({r.url})" for r in results)


# ---------- contact controls ----------
async def set_contact_preference(ctx: ToolContext, a: dict[str, Any]) -> str:
    action = a["action"]
    with session_scope() as s:
        user = get_user(s, ctx)
        if action == "stop":
            user.proactive_enabled = False
            result = "Okay, I'll stop messaging first until you say otherwise."
        elif action == "start":
            user.proactive_enabled = True
            user.paused_until = None
            result = "Proactive messages are back on."
        elif action == "pause":
            hours = float(a.get("hours", 3))
            if not 0 < hours <= 24 * 14:
                raise ToolError("hours must be between 0 and 336")
            until = users.pause_for(user, hours)
            from app.timeutil import fmt_local
            result = f"Got it — quiet until {fmt_local(until)}."
        else:
            raise ToolError("unknown action")
        events.log_event(s, "contact_preference", {"action": action, "hours": a.get("hours")}, user.id)
        return result


TOOLS = [
    Tool("get_current_time", "Get the current local date and time.", {"type": "object", "properties": {}}, get_current_time),
    Tool("calculate", "Evaluate a math expression exactly (+ - * / ** % sqrt sin cos log ...).",
         {"type": "object", "properties": {"expression": {"type": "string", "maxLength": 200}},
          "required": ["expression"]}, calculate),
    Tool("generate_file", "Create a downloadable text-based file (study plan, python file, notes, csv...) and send it.",
         {"type": "object", "properties": {"filename": {"type": "string", "description": "e.g. study_plan.md"},
                                           "content": {"type": "string"}}, "required": ["filename", "content"]},
         generate_file),
    Tool("web_search", "Search the web for current information (only if configured).",
         {"type": "object", "properties": {"query": {"type": "string", "maxLength": 300}}, "required": ["query"]},
         web_search),
    Tool("set_contact_preference",
         "Control proactive messaging: 'stop' (no unprompted messages), 'start' (resume), or 'pause' for N hours "
         "(e.g. 'don't text me for 3 hours', 'don't disturb me tonight').",
         {"type": "object", "properties": {"action": {"type": "string", "enum": ["stop", "start", "pause"]},
                                           "hours": {"type": "number", "description": "for pause"}},
          "required": ["action"]}, set_contact_preference),
]
