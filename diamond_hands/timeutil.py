"""Time helpers. Every user-facing time is shown in the trader's zone."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def from_unix(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def local(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz))


def fmt_time(dt: datetime, tz: str) -> str:
    """e.g. 'Nov 14 2:41 PM NST'."""
    return local(dt, tz).strftime("%b %-d %-I:%M %p %Z")


def fmt_day(d: date) -> str:
    return d.strftime("%b %-d")


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_dt(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
