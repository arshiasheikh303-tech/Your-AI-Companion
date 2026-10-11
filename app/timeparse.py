"""Deterministic natural-language time parsing for reminders.

The LLM may pass ISO datetimes directly; for everything else it passes the user's phrase and this
module resolves it. Keeping this out of the LLM makes reminders reliable on small local models.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.timeutil import local_to_utc

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"])}
MONTHS.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
               "oct": 10, "nov": 11, "dec": 12})
DEFAULT_HOUR = 9

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
              "forty-five": 45, "sixty": 60, "half": 0.5}


class TimeParseError(ValueError):
    """The phrase could not be turned into a time."""


@dataclass
class ParsedWhen:
    due_local: datetime  # naive local time of the first fire
    recurrence: str = "none"  # none | daily | weekly | monthly
    weekday: int | None = None
    local_time: str = ""  # "HH:MM"
    assumptions: list[str] = field(default_factory=list)

    @property
    def due_utc(self) -> datetime:
        return local_to_utc(self.due_local)


_TIME_RE = re.compile(r"(?:at\s+|@\s*)?\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?!\d)", re.I)
_REL_RE = re.compile(
    r"\bin\s+(?P<n>\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|"
    r"forty-five|forty|sixty|half)\s*(?:and a half\s*)?(?P<u>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\b"
    r"(?:\s+(?:and\s+)?(?P<n2>\d+)\s*(?P<u2>minutes?|mins?))?",
    re.I,
)


def _num(token: str) -> float:
    token = token.lower()
    return float(token) if token.replace(".", "").isdigit() else float(_NUM_WORDS[token])


def _extract_time(text: str) -> tuple[int, int, bool, bool] | None:
    """Return (hour24, minute, had_meridiem, matched_explicit_marker) for the clock time in text."""
    # strip date fragments so "november 12" / "12th" are not read as clock times
    cleaned = re.sub(r"\b\d{1,2}(st|nd|rd|th)\b", " ", text)
    cleaned = re.sub(r"\b(" + "|".join(MONTHS) + r")\.?\s+\d{1,2}\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d{1,2}\s+(" + "|".join(MONTHS) + r")\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}", " ", cleaned)
    if re.search(r"\bnoon\b", cleaned, re.I):
        return 12, 0, True, True
    if re.search(r"\bmidnight\b", cleaned, re.I):
        return 0, 0, True, True
    for m in _TIME_RE.finditer(cleaned):
        hour, minute, mer = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
        marker = bool(re.match(r"(at\s+|@)", m.group(0), re.I))
        if not (mer or m.group(2) or marker):
            continue  # a bare number like "3 tasks" is not a time
        if hour > 24 or minute > 59:
            continue
        if mer:
            if hour == 12:
                hour = 0 if mer.startswith("a") else 12
            elif mer.startswith("p") and hour < 12:
                hour += 12
        return hour % 24, minute, bool(mer), True
    return None


def _resolve_clock(hour: int, minute: int, had_mer: bool, same_day: bool, now: datetime,
                   day_hint: str, assumptions: list[str]) -> tuple[int, int]:
    """Guess am/pm for a bare hour like 'at 8'."""
    if had_mer or hour >= 13 or hour == 0:
        return hour, minute
    if "tonight" in day_hint or "evening" in day_hint or "afternoon" in day_hint:
        h = hour + 12 if hour < 12 else hour
        return h, minute
    if "morning" in day_hint:
        return hour, minute
    if same_day:
        # choose the next upcoming occurrence today
        for cand in (hour, hour + 12 if hour < 12 else hour):
            if (cand, minute) > (now.hour, now.minute):
                if hour < 12:
                    assumptions.append(f"assumed {'AM' if cand < 12 else 'PM'}")
                return cand, minute
        return hour, minute
    if hour == 12:
        return 12, minute
    guess = hour + 12 if hour <= 6 else hour
    assumptions.append(f"assumed {'PM' if guess >= 12 else 'AM'}")
    return guess, minute


def parse_when(text: str, now: datetime) -> ParsedWhen:
    """Parse a phrase into a first-fire time (+ recurrence). `now` is naive local time."""
    raw = text.strip()
    t = raw.lower().strip(" .!?")
    if not t:
        raise TimeParseError("empty time")
    assumptions: list[str] = []

    # --- ISO datetime -------------------------------------------------
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2}))?", t, re.I)
    if iso:
        y, mo, d = int(iso[1]), int(iso[2]), int(iso[3])
        if iso[4]:
            hh, mm = int(iso[4]), int(iso[5])
        else:
            hh, mm = DEFAULT_HOUR, 0
            assumptions.append("no time given, used 9:00 AM")
        try:
            due = datetime(y, mo, d, hh, mm)
        except ValueError as e:
            raise TimeParseError(str(e)) from e
        if due <= now:
            raise TimeParseError("that time is in the past")
        return ParsedWhen(due, assumptions=assumptions)

    # --- relative ("in 30 minutes") ----------------------------------
    rel = _REL_RE.search(t)
    if rel:
        n = _num(rel["n"])
        unit = rel["u"].lower()
        if "half" in t[rel.start():rel.end() + 12] and rel["n"].lower() not in ("half",):
            n += 0.5
        if unit.startswith("sec"):
            delta = timedelta(seconds=n)
        elif unit.startswith(("min",)):
            delta = timedelta(minutes=n)
        elif unit.startswith(("hour", "hr")):
            delta = timedelta(hours=n)
        elif unit.startswith("day"):
            delta = timedelta(days=n)
        else:
            delta = timedelta(weeks=n)
        if rel["n2"]:
            delta += timedelta(minutes=int(rel["n2"]))
        return ParsedWhen(now + delta)

    # --- recurrence ---------------------------------------------------
    recurrence = "none"
    weekday: int | None = None
    if re.search(r"\b(every\s+day|daily|each day|every\s+(morning|evening|night))\b", t):
        recurrence = "daily"
    elif re.search(r"\b(weekly|every\s+week)\b", t):
        recurrence = "weekly"
    elif re.search(r"\b(monthly|every\s+month)\b", t):
        recurrence = "monthly"
    m = re.search(r"\bevery\s+(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")s?\b", t)
    if m:
        recurrence, weekday = "weekly", WEEKDAYS[m.group(1)]

    clock = _extract_time(t)
    day_hint = t

    # --- date ---------------------------------------------------------
    base_date = None
    if "day after tomorrow" in t:
        base_date = (now + timedelta(days=2)).date()
    elif "tomorrow" in t:
        base_date = (now + timedelta(days=1)).date()
    elif re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", t):
        base_date = now.date()
    else:
        md = re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
        dm = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b", t)
        mo = day = None
        if md:
            mo, day = MONTHS[md.group(1)], int(md.group(2))
        elif dm:
            day, mo = int(dm.group(1)), MONTHS[dm.group(2)]
        if mo and day:
            try:
                cand = datetime(now.year, mo, day).date()
            except ValueError as e:
                raise TimeParseError(str(e)) from e
            if cand < now.date():
                cand = datetime(now.year + 1, mo, day).date()
            base_date = cand
        else:
            wd = None
            for name in sorted(WEEKDAYS, key=len, reverse=True):
                if re.search(rf"\b{name}\b", t):
                    wd = WEEKDAYS[name]
                    break
            if wd is not None:
                weekday = wd if recurrence in ("weekly", "none") else weekday
                days_ahead = (wd - now.weekday()) % 7
                if days_ahead == 0 and clock is None:
                    days_ahead = 7
                if re.search(r"\bnext\s+" + "|".join(WEEKDAYS), t) and days_ahead < 7 and days_ahead != 0:
                    days_ahead += 0
                base_date = (now + timedelta(days=days_ahead)).date()

    if base_date is None and recurrence == "none" and clock is None:
        # maybe a part-of-day only
        if re.search(r"\b(morning|afternoon|evening|tonight|night)\b", t):
            base_date = now.date()
        else:
            raise TimeParseError(f"I couldn't work out when '{raw}' is")

    if clock is None:
        part = re.search(r"\b(morning|afternoon|evening|tonight|night)\b", t)
        defaults = {"morning": 9, "afternoon": 15, "evening": 19, "tonight": 20, "night": 21}
        if part:
            hour, minute, had_mer = defaults[part.group(1)], 0, True
            assumptions.append(f"used {hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'} for the {part.group(1)}")
        else:
            hour, minute, had_mer = DEFAULT_HOUR, 0, True
            assumptions.append("no time given, used 9:00 AM")
    else:
        hour, minute, had_mer, _ = clock

    same_day = base_date is None or base_date == now.date()
    hour, minute = _resolve_clock(hour, minute, had_mer, same_day, now, day_hint, assumptions)

    if base_date is None:
        # recurring (or time-only): first occurrence
        cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if recurrence == "weekly" and weekday is not None:
            cand += timedelta(days=(weekday - now.weekday()) % 7)
        if cand <= now:
            cand += timedelta(days=7 if recurrence == "weekly" else 1)
        if recurrence == "monthly":
            cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if cand <= now:
                cand = _add_month(cand)
        if recurrence == "weekly" and weekday is None:
            weekday = cand.weekday()
    else:
        cand = datetime.combine(base_date, datetime.min.time()).replace(hour=hour, minute=minute)
        if cand <= now and recurrence == "none":
            if base_date == now.date() and clock is not None:
                cand += timedelta(days=1)
                assumptions.append("that time already passed today, so I set it for tomorrow")
            else:
                raise TimeParseError("that time is in the past")
        if recurrence == "weekly" and weekday is None:
            weekday = cand.weekday()

    return ParsedWhen(
        due_local=cand, recurrence=recurrence, weekday=weekday,
        local_time=f"{hour:02d}:{minute:02d}" if recurrence != "none" else "", assumptions=assumptions,
    )


def _add_month(dt: datetime) -> datetime:
    y, mth = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    day = dt.day
    while True:
        try:
            return dt.replace(year=y, month=mth, day=day)
        except ValueError:
            day -= 1


def next_occurrence(recurrence: str, local_time: str, weekday: int | None, after_local: datetime) -> datetime | None:
    """Next fire time strictly after `after_local` for a recurring reminder (naive local)."""
    if recurrence == "none" or not local_time:
        return None
    hh, mm = (int(x) for x in local_time.split(":"))
    cand = after_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if recurrence == "daily":
        while cand <= after_local:
            cand += timedelta(days=1)
        return cand
    if recurrence == "weekly":
        wd = after_local.weekday() if weekday is None else weekday
        cand += timedelta(days=(wd - cand.weekday()) % 7)
        while cand <= after_local:
            cand += timedelta(days=7)
        return cand
    if recurrence == "monthly":
        while cand <= after_local:
            cand = _add_month(cand)
        return cand
    return None
