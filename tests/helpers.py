from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from diamond_hands.models import Bar, Series

TZ = "America/Toronto"
ET = ZoneInfo(TZ)


def mkbar(d: date, o: float, h: float, l: float, c: float, v: float = 1_000_000) -> Bar:
    ts = datetime.combine(d, time(9, 30), ET).astimezone(timezone.utc)
    return Bar(ts, d, o, h, l, c, v)


def mkseries(symbol: str, bars: list[Bar], currency: str = "CAD") -> Series:
    return Series(symbol, "Yahoo Finance", datetime.now(timezone.utc), currency, TZ, bars, {})
