from datetime import timedelta

from app.config import reset_settings_cache
from app.database import database
from app.database.database import session_scope
from app.database.models import Reminder
from app.scheduler.reminders import process_due_reminders
from app.timeutil import utcnow
from app.tools.base import ToolContext


async def test_create_list_cancel(make_services, uid):
    sv = make_services()
    ctx = ToolContext(uid)
    out = await sv.registry.execute("create_reminder", {"text": "study", "when": "tomorrow at 7 PM"}, ctx)
    assert out.startswith("Reminder set") and "7:00 PM" in out
    assert "study" in await sv.registry.execute("list_reminders", {}, ctx)
    assert "Cancelled" in await sv.registry.execute("cancel_reminder", {"reminder_id": 1}, ctx)
    assert "No active" in await sv.registry.execute("list_reminders", {}, ctx)


async def test_invalid_time_is_graceful(make_services, uid):
    sv = make_services()
    out = await sv.registry.execute("create_reminder", {"text": "x", "when": "whenever"}, ToolContext(uid))
    assert out.startswith("Error")
    with session_scope() as s:
        assert s.query(Reminder).count() == 0


async def test_due_reminder_is_sent_once(make_services, uid):
    sv = make_services()
    await sv.registry.execute("create_reminder", {"text": "call mom", "when": "in 1 minute"}, ToolContext(uid))
    with session_scope() as s:  # make it due now
        s.query(Reminder).update({"due_at": utcnow() - timedelta(seconds=5)})
    assert await process_due_reminders(sv) == 1
    assert "call mom" in sv.messenger.texts[0]
    assert await process_due_reminders(sv) == 0  # not re-sent


async def test_recurring_reminder_reschedules(make_services, uid):
    sv = make_services()
    await sv.registry.execute("create_reminder", {"text": "standup", "when": "every day at 9am"}, ToolContext(uid))
    with session_scope() as s:
        s.query(Reminder).update({"due_at": utcnow() - timedelta(minutes=1)})
    await process_due_reminders(sv)
    with session_scope() as s:
        r = s.query(Reminder).one()
        assert r.status == "active" and r.due_at > utcnow() and r.times_sent == 1


async def test_failed_telegram_send_is_retried_next_tick(make_services, uid):
    sv = make_services(messenger_ok=False)
    await sv.registry.execute("create_reminder", {"text": "pay rent", "when": "in 1 minute"}, ToolContext(uid))
    with session_scope() as s:
        s.query(Reminder).update({"due_at": utcnow() - timedelta(seconds=5)})
    assert await process_due_reminders(sv) == 0
    with session_scope() as s:
        assert s.query(Reminder).one().status == "active"
    sv.messenger.ok = True
    assert await process_due_reminders(sv) == 1


async def test_reminders_survive_application_restart(make_services, uid):
    sv = make_services()
    await sv.registry.execute("create_reminder", {"text": "persist me", "when": "in 1 minute"}, ToolContext(uid))
    with session_scope() as s:
        s.query(Reminder).update({"due_at": utcnow() - timedelta(seconds=5)})
    # simulate restart: brand new engine/services on the same DB file
    reset_settings_cache()
    database.init_engine()
    sv2 = make_services()
    assert await process_due_reminders(sv2) == 1
    assert "persist me" in sv2.messenger.texts[0]


async def test_very_stale_one_time_reminder_is_dropped_quietly(make_services, uid):
    sv = make_services()
    await sv.registry.execute("create_reminder", {"text": "old", "when": "in 1 minute"}, ToolContext(uid))
    with session_scope() as s:
        s.query(Reminder).update({"due_at": utcnow() - timedelta(days=5)})
    assert await process_due_reminders(sv) == 0 and not sv.messenger.texts
