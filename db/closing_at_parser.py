"""Parse tender deadline strings into timezone-aware closing_at (P2-06)."""

from __future__ import annotations

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

VANCOUVER_TZ = ZoneInfo("America/Vancouver")

SENTINEL_VALUES: frozenset[str] = frozenset(
    {
        "not available",
        "n/a",
        "na",
        "tbd",
        "tba",
        "none",
        "-",
        "--",
    }
)

ISO_TZ_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2}:\d{2}|Z)$"
)
BOBS_NOISY_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+at\s+(\d{2}:\d{2})\s+pt\b",
    re.I,
)
DATETIME_MINUTE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})$")
SLASH_DATE_PATTERN = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
ISO_DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
PROSE_DATETIME_PATTERN = re.compile(
    r"^([A-Za-z]+ \d{1,2}, \d{4}), (\d{1,2}:\d{2} (?:am|pm))$",
    re.I,
)


def _localize_vancouver(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=VANCOUVER_TZ)
    return dt.astimezone(VANCOUVER_TZ)


def _end_of_day_vancouver(year: int, month: int, day: int) -> datetime:
    return datetime.combine(
        datetime(year, month, day).date(),
        time(23, 59, 59),
        tzinfo=VANCOUVER_TZ,
    )


def parse_closing_at(raw: str | None) -> datetime | None:
    """Return timezone-aware closing_at or None when input is empty/unparseable."""
    if raw is None:
        return None

    text = raw.strip()
    if not text:
        return None
    if text.lower() in SENTINEL_VALUES:
        return None

    iso_tz = ISO_TZ_PATTERN.match(text)
    if iso_tz:
        iso_text = iso_tz.group(1)
        offset = iso_tz.group(2)
        if offset == "Z":
            offset = "+00:00"
        parsed = datetime.fromisoformat(f"{iso_text}{offset}")
        return parsed.astimezone(VANCOUVER_TZ)

    bobs = BOBS_NOISY_PATTERN.match(text)
    if bobs:
        parsed = datetime.strptime(f"{bobs.group(1)} {bobs.group(2)}", "%Y-%m-%d %H:%M")
        return _localize_vancouver(parsed)

    minute = DATETIME_MINUTE_PATTERN.match(text)
    if minute:
        parsed = datetime.strptime(f"{minute.group(1)} {minute.group(2)}", "%Y-%m-%d %H:%M")
        return _localize_vancouver(parsed)

    slash = SLASH_DATE_PATTERN.match(text)
    if slash:
        try:
            return _end_of_day_vancouver(int(slash.group(1)), int(slash.group(2)), int(slash.group(3)))
        except ValueError:
            return None

    iso_date = ISO_DATE_PATTERN.match(text)
    if iso_date:
        try:
            parsed = datetime.strptime(iso_date.group(1), "%Y-%m-%d")
            return _end_of_day_vancouver(parsed.year, parsed.month, parsed.day)
        except ValueError:
            return None

    prose = PROSE_DATETIME_PATTERN.match(text)
    if prose:
        try:
            parsed = datetime.strptime(text, "%B %d, %Y, %I:%M %p")
            return _localize_vancouver(parsed)
        except ValueError:
            return None

    return None
