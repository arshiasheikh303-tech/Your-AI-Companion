"""FastAPI application: lifecycle (DB, scheduler, Telegram) + protected JSON API + tiny dashboard."""
from __future__ import annotations

import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from app.ai.agent import Agent
from app.ai.factory import get_provider
from app.config import Settings, get_settings
from app.database.database import check_database, init_engine, run_migrations, session_scope
from app.database.models import User
from app.database.repositories import events as ev_repo
from app.database.repositories import memories as mem_repo
from app.database.repositories import messages as msg_repo
from app.database.repositories import reminders as rem_repo
from app.database.repositories import tasks as task_repo
from app.logging_setup import setup_logging
from app.services import Services
from app.timeutil import to_local, utcnow
from app.tools.base import build_registry
from app.voice.transcription import get_transcriber
from app.voice.tts import get_tts

log = logging.getLogger(__name__)
DASHBOARD = Path(__file__).parent / "dashboard" / "index.html"


def build_services(settings: Settings | None = None) -> Services:
    s = settings or get_settings()
    provider = get_provider()
    registry = build_registry()
    agent = Agent(provider, registry, settings=s)
    return Services(settings=s, provider=provider, registry=registry, agent=agent,
                    transcriber=get_transcriber(), tts=get_tts(), started_at=utcnow())


async def print_banner(sv: Services, settings: Settings, generated_token: str | None) -> None:
    ai_ok = await sv.provider.health()
    tg = sv.telegram.connected if sv.telegram else False
    line = "=" * 37
    model = settings.ollama_model + ("" if ai_ok else f"  (NOT READY: start Ollama / run `ollama pull {settings.ollama_model}`)")
    rows = [
        ("Status", "ONLINE"), ("Model", model),
        ("Telegram", "CONNECTED" if tg else "DISABLED (check .env / logs)"),
        ("Database", "READY" if check_database() else "ERROR"),
        ("Scheduler", "RUNNING" if sv.scheduler and sv.scheduler.running else "STOPPED"),
        ("Memory", "READY"),
    ]
    print(f"\n{line}\n{settings.companion_name.upper()} - AI COMPANION\n{line}")
    for k, v in rows:
        print(f"{k + ':':<11}{v}")
    print(line)
    print(f"Dashboard: http://{settings.api_host}:{settings.api_port}/dashboard")
    if generated_token:
        print(f"API token (this run only; set API_TOKEN in .env to keep one): {generated_token}")
    print(f"{line}\n", flush=True)


def iso(dt: datetime | None) -> str | None:
    return to_local(dt).isoformat(timespec="seconds") if dt else None


def create_app(services: Services | None = None, start_background: bool = True) -> FastAPI:
    settings = services.settings if services else get_settings()
    state: dict = {"services": services, "status": {}}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        if not settings.api_token:
            settings.api_token = secrets.token_urlsafe(24)
            state["generated_token"] = settings.api_token
        init_engine()
        run_migrations()
        sv = state["services"] or build_services(settings)
        state["services"] = sv
        sv.user_id()  # make sure the user row exists
        if start_background:
            from app.scheduler.scheduler import build_scheduler
            sv.scheduler = build_scheduler(sv)
            if settings.telegram_bot_token and settings.telegram_user_id:
                from app.bot.telegram import TelegramApp
                try:
                    sv.telegram = TelegramApp(sv, settings)
                    sv.messenger = sv.telegram.messenger
                    await sv.telegram.start()
                except Exception:
                    log.exception("telegram failed to start; API/scheduler continue without it")
            else:
                log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID not set; Telegram disabled")
            sv.scheduler.start()
        await print_banner(sv, settings, state.get("generated_token"))
        yield
        if sv.scheduler:
            sv.scheduler.shutdown(wait=False)
        if sv.telegram:
            await sv.telegram.stop()
        await sv.agent.drain()
        await sv.provider.close()

    app = FastAPI(title="AI Companion", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.shared = state

    def auth(x_api_token: str | None = Header(default=None), token: str | None = Query(default=None)) -> None:
        supplied = x_api_token or token or ""
        if not settings.api_token or not hmac.compare_digest(supplied.encode(), settings.api_token.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing API token")

    @app.get("/health")
    async def health():
        return {"status": "ok", "database": check_database()}

    @app.get("/status", dependencies=[Depends(auth)])
    async def status():
        sv: Services = state["services"]
        ai_ok = await sv.provider.health()
        with session_scope() as s:
            u = s.get(User, sv.user_id())
            data = {
                "companion": settings.companion_name,
                "model": settings.ollama_model,
                "ai_online": ai_ok,
                "database": check_database(),
                "telegram_connected": bool(sv.telegram and sv.telegram.connected),
                "scheduler_running": bool(sv.scheduler and sv.scheduler.running),
                "uptime_seconds": int((utcnow() - sv.started_at).total_seconds()) if sv.started_at else 0,
                "last_user_message": iso(u.last_user_message_at),
                "last_agent_message": iso(u.last_agent_message_at),
                "last_proactive": iso(u.last_proactive_at),
                "proactive_enabled": u.proactive_enabled,
                "paused_until": iso(u.paused_until),
                "unanswered_proactive": u.unanswered_proactive,
                "voice_input": settings.voice_input_enabled,
                "voice_responses": settings.voice_responses,
                "search_provider": settings.search_provider,
            }
            events = ev_repo.recent_events(s, "proactive_decision", 15)
            data["proactive_activity"] = [{"at": iso(e.created_at), "detail": e.detail} for e in events]
        return data

    def _user(s) -> User:
        return s.get(User, state["services"].user_id())

    @app.get("/memories", dependencies=[Depends(auth)])
    async def memories():
        with session_scope() as s:
            return [{"id": m.id, "category": m.category, "content": m.content, "confidence": m.confidence,
                     "importance": m.importance, "source": m.source, "updated": iso(m.updated_at)}
                    for m in mem_repo.list_memories(s, _user(s))]

    @app.delete("/memories/{memory_id}", dependencies=[Depends(auth)])
    async def delete_memory(memory_id: int):
        with session_scope() as s:
            if not mem_repo.forget(s, _user(s), memory_id=memory_id):
                raise HTTPException(404, "memory not found")
        return {"deleted": memory_id}

    @app.get("/tasks", dependencies=[Depends(auth)])
    async def tasks(status: str = "all"):
        with session_scope() as s:
            return [{"id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "due": iso(t.due_at)}
                    for t in task_repo.list_tasks(s, _user(s), status)]

    @app.get("/reminders", dependencies=[Depends(auth)])
    async def reminders():
        with session_scope() as s:
            return [{"id": r.id, "text": r.text, "due": iso(r.due_at), "recurrence": r.recurrence}
                    for r in rem_repo.list_reminders(s, _user(s))]

    @app.get("/conversations", dependencies=[Depends(auth)])
    async def conversations(limit: int = Query(40, ge=1, le=200)):
        with session_scope() as s:
            return [{"role": m.role, "kind": m.kind, "content": m.content, "at": iso(m.created_at)}
                    for m in msg_repo.recent_messages(s, _user(s), limit)]

    @app.get("/events", dependencies=[Depends(auth)])
    async def events(limit: int = Query(40, ge=1, le=200)):
        with session_scope() as s:
            return [{"type": e.event_type, "detail": e.detail, "at": iso(e.created_at)} for e in ev_repo.recent_events(s, None, limit)]

    @app.get("/dashboard")
    async def dashboard():
        return FileResponse(DASHBOARD)

    @app.exception_handler(Exception)
    async def unhandled(_, exc: Exception):
        log.exception("unhandled API error")
        return JSONResponse({"detail": "internal error"}, status_code=500)

    return app
