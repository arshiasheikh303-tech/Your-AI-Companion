from datetime import datetime

import pytest

from app.timeparse import TimeParseError, next_occurrence, parse_when
from app.timeutil import in_quiet_hours
from datetime import time

NOW = datetime(2026, 10, 8, 6, 5)  # Thursday


@pytest.mark.parametrize("phrase,expected", [
    ("in 30 minutes", datetime(2026, 10, 8, 6, 35)),
    ("in an hour", datetime(2026, 10, 8, 7, 5)),
    ("tomorrow at 7 PM", datetime(2026, 10, 9, 19, 0)),
    ("tomorrow at 8", datetime(2026, 10, 9, 8, 0)),
    ("tonight at 9", datetime(2026, 10, 8, 21, 0)),
    ("November 12 at 5pm", datetime(2026, 11, 12, 17, 0)),
    ("2026-10-09T19:00", datetime(2026, 10, 9, 19, 0)),
    ("friday at 3pm", datetime(2026, 10, 9, 15, 0)),
])
def test_one_time(phrase, expected):
    assert parse_when(phrase, NOW).due_local == expected


def test_recurring_weekly_and_daily():
    w = parse_when("every Monday at 6 PM", NOW)
    assert (w.recurrence, w.weekday, w.local_time) == ("weekly", 0, "18:00")
    assert w.due_local == datetime(2026, 10, 12, 18, 0)
    d = parse_when("every day at 8am", NOW)
    assert d.recurrence == "daily" and d.due_local == datetime(2026, 10, 8, 8, 0)


def test_assumption_is_reported_for_bare_hour():
    assert parse_when("tomorrow at 8", NOW).assumptions


@pytest.mark.parametrize("bad", ["blah", "", "every 3 tasks"])
def test_unparseable(bad):
    with pytest.raises(TimeParseError):
        parse_when(bad, NOW)


def test_past_date_rejected():
    with pytest.raises(TimeParseError):
        parse_when("2020-01-01 10:00", NOW)


def test_next_occurrence():
    assert next_occurrence("daily", "08:00", None, datetime(2026, 10, 8, 8, 0)) == datetime(2026, 10, 9, 8, 0)
    assert next_occurrence("weekly", "18:00", 0, datetime(2026, 10, 12, 18, 0)) == datetime(2026, 10, 19, 18, 0)
    assert next_occurrence("monthly", "09:00", None, datetime(2026, 1, 31, 9, 0)) == datetime(2026, 2, 28, 9, 0)
    assert next_occurrence("none", "", None, NOW) is None


def test_quiet_hours_wraps_midnight():
    s, e = time(23), time(8)
    assert in_quiet_hours(time(23, 30), s, e) and in_quiet_hours(time(3), s, e) and in_quiet_hours(time(7, 59), s, e)
    assert not in_quiet_hours(time(8), s, e) and not in_quiet_hours(time(14), s, e)
    assert not in_quiet_hours(time(1), time(0), time(0))  # empty window
