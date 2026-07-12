"""Shared date/time utilities used across pipeline and scraper modules."""

from __future__ import annotations

from datetime import date, datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def parse_iso_date(raw: str | None) -> date | None:
    """Parse a YYYY-MM-DD (or slash-separated) date string to a date object."""
    if not raw:
        return None
    text = str(raw).strip().replace("/", "-")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_dt(value: datetime | None) -> datetime | None:
    """Normalize a datetime to UTC. Returns None if input is None."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
