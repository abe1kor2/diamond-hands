"""Price bars and series shared by every module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .timeutil import from_unix

# TSX, NYSE and NASDAQ all close at 16:00 Eastern. Half-days are treated as full days.
SESSION_CLOSE = time(16, 0)
# Wait a few minutes after the bell before treating a daily bar as the official close.
FINAL_GRACE = timedelta(minutes=10)


@dataclass
class Bar:
    ts: datetime  # bar start, UTC
    day: date  # session date in the exchange's zone
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str = "regular"  # pre | regular | post


@dataclass
class Series:
    symbol: str
    source: str
    fetched_at: datetime
    currency: str | None
    exchange_tz: str
    bars: list[Bar]
    meta: dict = field(default_factory=dict)

    def market_price(self) -> float | None:
        v = self.meta.get("regularMarketPrice")
        return float(v) if v is not None else None

    def market_time(self) -> datetime | None:
        v = self.meta.get("regularMarketTime")
        return from_unix(v) if v else None


def session_close(day: date, tz: str) -> datetime:
    return datetime.combine(day, SESSION_CLOSE, ZoneInfo(tz))


def is_final(series: Series, bar: Bar, now: datetime) -> bool:
    """True once the bar's session has closed, so its close is the official close."""
    return now >= session_close(bar.day, series.exchange_tz) + FINAL_GRACE
