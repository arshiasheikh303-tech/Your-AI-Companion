"""Tool architecture: independent Tool objects with JSON-schema params, validated before execution."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.database.database import session_scope
from app.database.models import User

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Raised by a tool for a user-presentable failure."""


@dataclass
class ToolContext:
    user_id: int
    files_to_send: list[Path] = field(default_factory=list)  # filled by generate_file


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema object
    handler: Handler

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        clean = validate_args(self.parameters, args)
        return await self.handler(ctx, clean)


_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict,
}


def validate_args(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    """Validate/coerce tool arguments; raises ToolError on problems. Small LLMs often send '5' for 5."""
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    props = schema.get("properties", {})
    out: dict[str, Any] = {}
    for key in schema.get("required", []):
        if key not in args or args[key] in (None, ""):
            raise ToolError(f"missing required argument '{key}'")
    for key, val in args.items():
        spec = props.get(key)
        if spec is None:
            continue  # ignore unknown keys rather than failing
        if val is None:
            continue
        typ = spec.get("type", "string")
        if typ == "integer" and isinstance(val, (str, float)) and not isinstance(val, bool):
            try:
                val = int(float(val))
            except ValueError as e:
                raise ToolError(f"'{key}' must be an integer") from e
        elif typ == "number" and isinstance(val, str):
            try:
                val = float(val)
            except ValueError as e:
                raise ToolError(f"'{key}' must be a number") from e
        elif typ == "boolean" and isinstance(val, str):
            val = val.strip().lower() in ("true", "yes", "1")
        elif typ == "string" and isinstance(val, (int, float)) and not isinstance(val, bool):
            val = str(val)
        expected = _TYPES.get(typ, object)
        if typ == "boolean":
            ok = isinstance(val, bool)
        elif typ in ("integer", "number"):
            ok = isinstance(val, expected) and not isinstance(val, bool)
        else:
            ok = isinstance(val, expected)
        if not ok:
            raise ToolError(f"'{key}' must be of type {typ}")
        if typ == "string" and len(val) > spec.get("maxLength", 20000):
            raise ToolError(f"'{key}' is too long")
        if "enum" in spec and val not in spec["enum"]:
            raise ToolError(f"'{key}' must be one of {spec['enum']}")
        out[key] = val
    return out


def get_user(session, ctx: ToolContext) -> User:
    user = session.get(User, ctx.user_id)
    if user is None:
        raise ToolError("user not found")
    return user


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def describe(self) -> str:
        """Compact text catalogue, used by the JSON-fallback prompt."""
        lines = []
        for t in self._tools.values():
            props = t.parameters.get("properties", {})
            req = set(t.parameters.get("required", []))
            args = ", ".join(f"{k}{'*' if k in req else ''}: {v.get('type', 'string')}" for k, v in props.items())
            lines.append(f"- {t.name}({args}) — {t.description}")
        return "\n".join(lines)

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'."
        try:
            result = await tool.run(ctx, args)
            log.info("tool executed", extra={"tool": name, "ok": True})
            return result
        except ToolError as e:
            log.info("tool rejected", extra={"tool": name, "error": str(e)})
            return f"Error: {e}"
        except Exception:
            log.exception("tool crashed", extra={"tool": name})
            return "Error: that tool failed unexpectedly."


def build_registry() -> ToolRegistry:
    from app.tools import memory_tools, note_tools, reminder_tools, task_tools, utility_tools

    reg = ToolRegistry()
    for module in (memory_tools, reminder_tools, task_tools, note_tools, utility_tools):
        for tool in module.TOOLS:
            reg.register(tool)
    return reg
