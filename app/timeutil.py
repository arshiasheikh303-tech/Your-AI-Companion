"""Time helpers. DB stores naive UTC; users think in their local timezone."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from app.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt_utc: datetime) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(get_settings().tz)


def local_now() -> datetime:
    return to_local(utcnow())


def local_to_utc(dt_local: datetime) -> datetime:
    """Naive local datetime -> naive UTC."""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=get_settings().tz)
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def fmt_local(dt_utc: datetime | None) -> str:
    if dt_utc is None:
        return "never"
    return to_local(dt_utc).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def in_quiet_hours(now_local: time, start: time, end: time) -> bool:
    """Handles windows that wrap midnight (e.g. 23:00-08:00)."""
    if start == end:
        return False
    if start < end:
        return start <= now_local < end
    return now_local >= start or now_local < end


def human_delta(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 90:
        return "a moment"
    mins = secs // 60
    if mins < 90:
        return f"{mins} minutes"
    hours = mins / 60
    if hours < 36:
        return f"{hours:.0f} hours"
    return f"{hours / 24:.0f} days"
