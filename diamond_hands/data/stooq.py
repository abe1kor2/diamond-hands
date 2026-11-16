"""Stooq daily CSV: backup source for US tickers and the S&P 500."""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from ..models import Bar, Series
from ..timeutil import now_utc
from .http import DataError, get

SOURCE = "Stooq"
URL = "https://stooq.com/q/d/l/"
KEEP_BARS = 600


def stooq_symbol(symbol: str) -> str | None:
    s = symbol.upper()
    if s == "^GSPC":
        return "^spx"
    if "." in s or s.startswith("^"):
        return None  # TSX listings and other indexes are not covered reliably
    return f"{s.lower()}.us"


def covers(symbol: str) -> bool:
    return stooq_symbol(symbol) is not None


def fetch_daily(symbol: str) -> Series:
    ss = stooq_symbol(symbol)
    if not ss:
        raise DataError(SOURCE, f"{symbol}: not covered")
    text = get(URL, SOURCE, params={"s": ss, "i": "d"}).text.strip()
    lines = text.splitlines()
    if not lines or not lines[0].lower().startswith("date"):
        raise DataError(SOURCE, f"{symbol}: {(lines[0][:80] if lines else 'empty response')}")
    ny = ZoneInfo("America/New_York")
    bars = []
    for row in csv.DictReader(lines):
        try:
            d = date.fromisoformat(row["Date"])
            o, h, l, c = (float(row[k]) for k in ("Open", "High", "Low", "Close"))
            v = float(row.get("Volume") or 0)
        except (KeyError, ValueError):
            continue
        ts = datetime.combine(d, time(9, 30), ny).astimezone(timezone.utc)
        bars.append(Bar(ts, d, o, h, l, c, v))
    if not bars:
        raise DataError(SOURCE, f"{symbol}: no bars")
    return Series(symbol.upper(), SOURCE, now_utc(), "USD", "America/New_York", bars[-KEEP_BARS:], {})
