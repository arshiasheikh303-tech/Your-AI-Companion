from datetime import datetime, timedelta

import pytest

from app.ai.base import LLMUnavailable
from app.config import get_settings
from app.database.database import session_scope
from app.database.models import AgentEvent, Message, User
from app.scheduler.proactive import (check_gates, escalation_tick, is_duplicate, proactive_tick, validate_decision)
from tests.conftest import FakeProvider

DAY = datetime(2026, 10, 8, 15, 0)  # 3 PM UTC (timezone fixture = UTC)


def mk_user(**kw) -> User:
    u = User(telegram_id=1, proactive_enabled=True, unanswered_proactive=0)
    for k, v in kw.items():
        setattr(u, k, v)
    return u


def test_gates_allow_normal_afternoon():
    assert check_gates(mk_user(last_user_message_at=DAY - timedelta(hours=8)), get_settings(), DAY, 0).allowed


@pytest.mark.parametrize("kwargs,now,sent,reason", [
    ({}, datetime(2026, 10, 8, 23, 30), 0, "quiet"),
    ({}, datetime(2026, 10, 8, 3, 0), 0, "quiet"),
    ({}, DAY, 4, "daily"),
    ({"proactive_enabled": False}, DAY, 0, "off"),
    ({"paused_until": DAY + timedelta(hours=1)}, DAY, 0, "pause"),
    ({"last_proactive_at": DAY - timedelta(hours=1)}, DAY, 0, "soon"),
    ({"last_user_message_at": DAY - timedelta(minutes=5)}, DAY, 0, "actively"),
    ({"unanswered_proactive": 3, "last_proactive_at": DAY - timedelta(days=2)}, DAY, 0, "unanswered"),
    ({"unanswered_proactive": 1, "last_proactive_at": DAY - timedelta(hours=4)}, DAY, 0, "waiting"),
])
def test_gates_block(kwargs, now, sent, reason):
    g = check_gates(mk_user(**kwargs), get_settings(), now, sent)
    assert not g.allowed and reason in g.reason


def test_pause_expiry_allows_again():
    u = mk_user(paused_until=DAY - timedelta(minutes=1))
    assert check_gates(u, get_settings(), DAY, 0).allowed


def test_validate_decision_handles_garbage():
    assert not validate_decision(None, []).should_contact
    assert not validate_decision({"should_contact": False}, []).should_contact
    assert not validate_decision({"should_contact": True, "message_type": "joke", "message": ""}, []).should_contact
    d = validate_decision({"should_contact": "true", "message_type": "weird", "message": "Hey you 👀"}, [])
    assert d.should_contact and d.message_type == "checkin"
    long = validate_decision({"should_contact": True, "message_type": "joke", "message": "word " * 300}, [])
    assert len(long.message) <= 401


def test_duplicate_detection():
    recent = ["Hey, you've been quiet today 👀 Everything okay?"]
    assert is_duplicate("Hey, you've been quiet today 👀 everything okay??", recent)
    assert not is_duplicate("Want to hear a terrible pun about databases?", recent)
    assert not validate_decision({"should_contact": True, "message_type": "checkin",
                                  "message": "Hey, you've been quiet today! Everything okay?"}, recent).should_contact


GOOD = '{"should_contact": true, "message_type": "joke", "reason": "light moment", "message": "Why did the dev quit? No arrays."}'


def _stale_user(uid, hours=8):
    with session_scope() as s:
        u = s.get(User, uid)
        u.last_user_message_at = DAY - timedelta(hours=hours)
        u.created_at = DAY - timedelta(days=3)


async def test_tick_sends_and_records(make_services, uid, monkeypatch):
    monkeypatch.setattr("app.scheduler.proactive.random.random", lambda: 1.0)
    sv = make_services([GOOD])
    _stale_user(uid)
    d = await proactive_tick(sv, now_utc=DAY)
    assert d and d.should_contact and sv.messenger.texts == ["Why did the dev quit? No arrays."]
    with session_scope() as s:
        u = s.get(User, uid)
        assert u.unanswered_proactive == 1 and u.last_proactive_at is not None
        assert s.query(Message).filter_by(kind="proactive").count() == 1
        assert s.query(AgentEvent).filter_by(event_type="proactive_decision").count() == 1
    # immediately again -> blocked by min-gap gate, LLM not even consulted
    calls = len(sv.provider.calls)
    await proactive_tick(sv, now_utc=DAY + timedelta(minutes=20))
    assert len(sv.provider.calls) == calls


async def test_tick_respects_quiet_hours_without_calling_llm(make_services, uid):
    sv = make_services([GOOD])
    _stale_user(uid)
    assert await proactive_tick(sv, now_utc=datetime(2026, 10, 8, 2, 0)) is None
    assert not sv.provider.calls and not sv.messenger.texts


async def test_tick_survives_ollama_failure_and_malformed_json(make_services, uid, monkeypatch):
    monkeypatch.setattr("app.scheduler.proactive.random.random", lambda: 1.0)
    _stale_user(uid)
    sv = make_services([LLMUnavailable("down")])
    assert await proactive_tick(sv, now_utc=DAY) is None and not sv.messenger.texts
    sv = make_services(["totally not json"])
    d = await proactive_tick(sv, now_utc=DAY)
    assert d and not d.should_contact and not sv.messenger.texts


async def test_tick_telegram_failure_does_not_count_as_sent(make_services, uid, monkeypatch):
    monkeypatch.setattr("app.scheduler.proactive.random.random", lambda: 1.0)
    sv = make_services([GOOD], messenger_ok=False)
    _stale_user(uid)
    d = await proactive_tick(sv, now_utc=DAY)
    assert d and not d.should_contact
    with session_scope() as s:
        assert s.get(User, uid).unanswered_proactive == 0


async def test_user_pause_via_agent_blocks_proactive(make_services, uid):
    sv = make_services()
    _stale_user(uid)
    reply = await sv.agent.handle_message(uid, "Don't message me for 3 hours")
    assert "quiet until" in reply.text
    sv.provider.script = [GOOD]
    with session_scope() as s:  # pretend the user's message was long ago so only the pause matters
        s.get(User, uid).last_user_message_at = DAY - timedelta(hours=8)
    from app.timeutil import utcnow
    assert await proactive_tick(sv, now_utc=utcnow() + timedelta(hours=1)) is None


async def test_escalation_is_off_by_default_and_gentle_when_on(make_services, uid, monkeypatch):
    sv = make_services()
    assert await escalation_tick(sv, now_utc=DAY) is None
    monkeypatch.setenv("ESCALATION_ENABLED", "true")
    from app.config import reset_settings_cache
    reset_settings_cache()
    sv = make_services()
    with session_scope() as s:
        u = s.get(User, uid)
        u.unanswered_proactive, u.last_proactive_at = 1, DAY - timedelta(hours=5)
    assert await escalation_tick(sv, now_utc=DAY) == "gentle_followup"
    assert len(sv.messenger.texts) == 1
