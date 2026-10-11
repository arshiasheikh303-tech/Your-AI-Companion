"""APScheduler wiring. Every job is wrapped so one failure never kills the scheduler."""
from __future__ import annotations

import logging
import random
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database.database import check_database, session_scope
from app.database.models import AgentEvent, Message
from app.scheduler.proactive import escalation_tick, proactive_tick
from app.scheduler.reminders import process_due_reminders
from app.timeutil import utcnow

log = logging.getLogger(__name__)


def _guarded(name, fn, services):
    async def runner():
        try:
            await fn(services)
        except Exception:
            log.exception("scheduled job crashed", extra={"job": name})
    runner.__name__ = name
    return runner


async def cleanup_job(services) -> None:
    cutoff = utcnow() - timedelta(days=90)
    with session_scope() as s:
        n = s.query(AgentEvent).filter(AgentEvent.created_at < cutoff).delete()
    if n:
        log.info("cleanup removed old events", extra={"count": n})
    from app.config import VOICE_DIR
    if VOICE_DIR.exists():
        for f in VOICE_DIR.iterdir():
            if f.is_file() and utcnow().timestamp() - f.stat().st_mtime > 3600:
                f.unlink(missing_ok=True)


async def memory_maintenance_job(services) -> None:
    services.agent.memory.maintain()


async def health_job(services) -> None:
    ai = await services.provider.health()
    db = check_database()
    tg = await services.messenger.healthy() if services.messenger else False
    if not (ai and db):
        log.warning("health check", extra={"ai": ai, "db": db, "telegram": tg})


def build_scheduler(services) -> AsyncIOScheduler:
    s = services.settings
    sched = AsyncIOScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120})
    interval = max(1, s.proactive_check_interval_minutes)
    sched.add_job(_guarded("reminders", process_due_reminders, services), "interval", seconds=30, id="reminders")
    # jitter -> the check-in doesn't land at the same minute every day
    sched.add_job(_guarded("proactive", proactive_tick, services), "interval", minutes=interval,
                  jitter=min(300, interval * 20), id="proactive")
    sched.add_job(_guarded("escalation", escalation_tick, services), "interval", minutes=max(5, interval), id="escalation")
    sched.add_job(_guarded("cleanup", cleanup_job, services), "interval", hours=6, id="cleanup")
    sched.add_job(_guarded("memory_maintenance", memory_maintenance_job, services), "interval", hours=12, id="memory")
    sched.add_job(_guarded("health", health_job, services), "interval", minutes=10, id="health")
    return sched
