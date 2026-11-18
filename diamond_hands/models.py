"""Price bars, series and quotes shared by every module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .timeutil import from_unix

# NYSE, NASDAQ and TSX all close at 16:00 Eastern. Half-days are treated as full days.
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
    provisional: bool = False  # built from live quotes; never treated as an official close


@dataclass
class Series:
    symbol: str
    source: str
    fetched_at: datetime
    currency: str | None
    exchange_tz: str
    bars: list[Bar]
    meta: dict = field(default_factory=dict)
    as_of: datetime | None = None  # time of the latest price, when the source gives one

    def market_time(self) -> datetime | None:
        if self.as_of:
            return self.as_of
        v = self.meta.get("regularMarketTime")
        return from_unix(v) if v else None


@dataclass
class Quote:
    symbol: str
    name: str | None
    price: float
    change: float | None
    change_pct: float | None
    prev_close: float | None
    day_low: float | None
    day_high: float | None
    volume: float | None
    low_52w: float | None
    high_52w: float | None
    market_status: str | None
    last_trade: str | None  # as the source states it, e.g. "Nov 17, 2026 2:18 PM ET"
    extra: list[str]  # other-session prices (pre-market / after-hours) as the source states them
    source: str
    fetched_at: datetime
    real_time: bool | None = None


def session_close(day: date, tz: str) -> datetime:
    return datetime.combine(day, SESSION_CLOSE, ZoneInfo(tz))


def is_final(series: Series, bar: Bar, now: datetime) -> bool:
    """True once the bar's session has closed and its close is the official close."""
    if bar.provisional:
        return False
    return now >= session_close(bar.day, series.exchange_tz) + FINAL_GRACE
