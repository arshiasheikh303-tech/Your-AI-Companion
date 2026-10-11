"""Proactive messaging. Deterministic gates decide IF contact is allowed; the LLM decides WHETHER/WHAT."""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from app.ai.agent import parse_json_object, sanitize_reply
from app.ai.base import LLMError
from app.ai.prompts import PROACTIVE_PROMPT
from app.config import Settings
from app.database.database import session_scope
from app.database.models import User
from app.database.repositories import events, messages, users
from app.database.repositories import memories as mem_repo
from app.database.repositories import reminders as rem_repo
from app.database.repositories import tasks as task_repo
from app.timeutil import human_delta, in_quiet_hours, local_now, to_local, utcnow

log = logging.getLogger(__name__)

MESSAGE_TYPES = {"none", "checkin", "reminder", "joke", "question", "motivation", "task_followup", "notification"}
SIMILARITY_LIMIT = 0.72
MAX_MESSAGE_LEN = 400


@dataclass
class Gate:
    allowed: bool
    reason: str = ""


@dataclass
class Decision:
    should_contact: bool
    message_type: str = "none"
    reason: str = ""
    message: str = ""


# ----------------------------------------------------------------------------------- deterministic gates
def check_gates(user: User, settings: Settings, now_utc: datetime, sent_today: int) -> Gate:
    """Hard rules. The LLM is never consulted if any of these fail."""
    if not settings.proactive_enabled:
        return Gate(False, "proactive disabled in config")
    if not user.proactive_enabled:
        return Gate(False, "user turned proactive messages off")
    if users.is_paused(user, now_utc):
        return Gate(False, "user pause active")
    local = to_local(now_utc)
    if in_quiet_hours(local.time(), settings.quiet_start, settings.quiet_end):
        return Gate(False, "quiet hours")
    if sent_today >= settings.max_proactive_messages_per_day:
        return Gate(False, "daily limit reached")
    min_gap = timedelta(hours=settings.min_time_between_proactive_messages_hours)
    if user.last_proactive_at and now_utc - user.last_proactive_at < min_gap:
        return Gate(False, "too soon after last proactive message")
    if user.last_user_message_at and now_utc - user.last_user_message_at < timedelta(minutes=20):
        return Gate(False, "user is actively chatting")
    # don't pile on: if the last proactive message is still unanswered, back off progressively
    if user.unanswered_proactive >= 1 and user.last_proactive_at:
        backoff = min_gap * (2 ** min(user.unanswered_proactive, 4))
        if now_utc - user.last_proactive_at < backoff:
            return Gate(False, "waiting for a reply to the last proactive message")
    if user.unanswered_proactive >= 3:
        return Gate(False, "several unanswered messages; giving space")
    return Gate(True)


def is_duplicate(message: str, recent: list[str]) -> bool:
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()
    m = norm(message)
    return any(SequenceMatcher(None, m, norm(r)).ratio() >= SIMILARITY_LIMIT for r in recent)


def validate_decision(obj: dict | None, recent: list[str]) -> Decision:
    """Never trust model output: coerce, bound and de-duplicate it."""
    if not isinstance(obj, dict):
        return Decision(False, reason="malformed model output")
    should = obj.get("should_contact")
    if isinstance(should, str):
        should = should.strip().lower() in ("true", "yes")
    mtype = str(obj.get("message_type", "none")).strip().lower()
    if mtype not in MESSAGE_TYPES:
        mtype = "checkin" if should else "none"
    msg = obj.get("message")
    msg = sanitize_reply(msg) if isinstance(msg, str) else ""
    reason = str(obj.get("reason", ""))[:200]
    if not should or mtype == "none":
        return Decision(False, "none", reason or "model chose not to contact")
    if not msg or len(msg) < 3:
        return Decision(False, mtype, "empty message")
    if len(msg) > MAX_MESSAGE_LEN:
        msg = msg[:MAX_MESSAGE_LEN].rsplit(" ", 1)[0] + "…"
    if is_duplicate(msg, recent):
        return Decision(False, mtype, "too similar to a recent message")
    return Decision(True, mtype, reason, msg)


# ----------------------------------------------------------------------------------- situation analysis
def activity_pattern(hours_utc: list[int], tz_offset_hours: float) -> str:
    """Describe when the user is usually active (learned from their message history)."""
    if len(hours_utc) < 15:
        return "not enough history to know their usual schedule yet"
    local_hours = [int((h + tz_offset_hours) % 24) for h in hours_utc]
    counts = {h: local_hours.count(h) for h in set(local_hours)}
    top = sorted(counts, key=counts.get, reverse=True)[:3]
    return "usually chats around " + ", ".join(f"{h % 12 or 12}{'am' if h < 12 else 'pm'}" for h in sorted(top))


def suggest_type(user: User, now_utc: datetime, has_due_task: bool, settings: Settings, rng: random.Random) -> str:
    quiet_for = now_utc - (user.last_user_message_at or user.created_at)
    if quiet_for > timedelta(hours=settings.inactivity_threshold_hours):
        return rng.choice(["checkin", "checkin", "question", "joke"])
    if has_due_task:
        return "task_followup"
    return rng.choice(["joke", "question", "motivation", "checkin", "none", "none"])


def build_situation(user_id: int, settings: Settings, now_utc: datetime) -> tuple[str, str, list[str], bool, str]:
    """Returns (situation text, memories text, recent proactive texts, has_due_task, suggested)."""
    with session_scope() as s:
        user = s.get(User, user_id)
        recent = messages.recent_proactive_texts(s, user, 8)
        hours = messages.user_message_hours(s, user)
        local = to_local(now_utc)
        offset = local.utcoffset().total_seconds() / 3600 if local.utcoffset() else 0
        tasks = task_repo.list_tasks(s, user, "open")
        upcoming = rem_repo.upcoming_reminders(s, user, 6)
        mems = mem_repo.list_memories(s, user)[:10]
        quiet_for = now_utc - user.last_user_message_at if user.last_user_message_at else None
        lines = [
            f"Local time now: {local.strftime('%A %I:%M %p')}",
            f"Last heard from the user: {human_delta(quiet_for) + ' ago' if quiet_for else 'never'}",
            f"Unanswered unprompted messages in a row: {user.unanswered_proactive}",
            f"Their pattern: {activity_pattern(hours, offset)}",
            f"Open tasks: {', '.join(t.title for t in tasks[:5]) or 'none'}",
            f"Reminders in next 6h: {', '.join(r.text for r in upcoming[:3]) or 'none'}",
        ]
        has_due = bool(tasks)
        suggested = suggest_type(user, now_utc, has_due, settings, random.Random())
        return ("\n".join(lines), "\n".join(f"- {m.content}" for m in mems) or "(nothing yet)", recent, has_due, suggested)


# ----------------------------------------------------------------------------------- the loop body
async def proactive_tick(services, now_utc: datetime | None = None) -> Decision | None:
    """Run one proactive check. Safe to call often; returns the decision if the LLM was consulted."""
    s_ = services.settings
    now_utc = now_utc or utcnow()
    uid = services.user_id()
    try:
        with session_scope() as s:
            user = s.get(User, uid)
            sent_today = messages.count_proactive_since(s, user, now_utc - timedelta(hours=24))
            gate = check_gates(user, s_, now_utc, sent_today)
            if not gate.allowed:
                log.info("proactive skipped", extra={"reason": gate.reason})
                return None
            snapshot = {
                "last_user": user.last_user_message_at, "last_proactive": user.last_proactive_at,
                "unanswered": user.unanswered_proactive,
            }
        situation, mem_text, recent, _, suggested = build_situation(uid, s_, now_utc)
        if suggested == "none" and random.random() < 0.7:  # randomness: often just stay quiet
            _log_decision(uid, Decision(False, "none", "random restraint"))
            return Decision(False, "none", "random restraint")
        prompt = PROACTIVE_PROMPT.format(
            name=s_.companion_name, user_name=s_.user_name or "the user", situation=situation,
            memories=mem_text, recent="\n".join(f"- {r}" for r in recent) or "(none yet)", suggested=suggested)
        try:
            resp = await services.provider.chat([{"role": "user", "content": prompt}], json_mode=True, temperature=0.9)
        except LLMError:
            log.info("proactive skipped: model unavailable")
            return None
        decision = validate_decision(parse_json_object(resp.content), recent)
        _log_decision(uid, decision)
        if decision.should_contact:
            sent = await services.deliver(decision.message, kind="proactive", proactive=True)
            log.info("proactive message", extra={"sent": sent, "type": decision.message_type})
            if not sent:
                decision.should_contact = False
                decision.reason = "telegram send failed"
        return decision
    except Exception:
        log.exception("proactive tick failed")
        return None


def _log_decision(uid: int, d: Decision) -> None:
    try:
        with session_scope() as s:
            events.log_event(s, "proactive_decision", {"send": d.should_contact, "type": d.message_type,
                                                       "reason": d.reason}, uid)
    except Exception:
        log.exception("failed to log proactive decision")
        return
    log.info("proactive decision", extra={"send": d.should_contact, "type": d.message_type, "reason": d.reason})


# ----------------------------------------------------------------------------------- escalation
async def escalation_tick(services, now_utc: datetime | None = None) -> str | None:
    """Optional follow-up after an unanswered proactive message.
    Stage 1: wait. Stage 2: one gentle follow-up. Stage 3: voice note (if enabled). Stage 4: telephony adapter."""
    s_ = services.settings
    if not s_.escalation_enabled:
        return None
    now_utc = now_utc or utcnow()
    uid = services.user_id()
    with session_scope() as s:
        user = s.get(User, uid)
        sent_today = messages.count_proactive_since(s, user, now_utc - timedelta(hours=24))
        if (user.unanswered_proactive < 1 or not user.last_proactive_at or not user.proactive_enabled
                or users.is_paused(user, now_utc)
                or in_quiet_hours(to_local(now_utc).time(), s_.quiet_start, s_.quiet_end)
                or sent_today >= s_.max_proactive_messages_per_day):
            return None
        stage = user.escalation_stage
        waited = now_utc - user.last_proactive_at
    needed = timedelta(hours=s_.escalation_wait_hours) * (stage + 1)
    if waited < needed or stage >= 3:
        return None
    if stage == 0:
        text = random.choice(["No pressure at all — just here if you want to chat 🙂",
                              "Hey, whenever you surface, I'm around. No rush!",
                              "Still here if you need anything. Take your time."])
        if not await services.deliver(text, kind="proactive", proactive=True):
            return None
        action = "gentle_followup"
    elif stage == 1 and s_.escalation_voice_enabled:
        if not await services.send_voice_safely("Hey, just checking in. No rush, message me whenever."):
            return None
        action = "voice_note"
    else:
        from app.integrations.telephony import get_telephony  # optional adapter (not configured by default)
        provider = get_telephony()
        if provider is None:
            action = "telephony_not_configured"
        else:
            action = "call_requested"
            await provider.place_call("Just checking in on you.")
    with session_scope() as s:
        user = s.get(User, uid)
        user.escalation_stage = stage + 1
        events.log_event(s, "escalation", {"stage": stage + 1, "action": action}, uid)
    return action
