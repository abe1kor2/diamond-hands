"""Yahoo Finance chart and calendar endpoints (public, unofficial)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from ..models import Bar, Series
from ..timeutil import from_unix, now_utc
from .http import DataError, get, session

SOURCE = "Yahoo Finance"
CAL_SOURCE = "Yahoo Finance calendar"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
DAILY_INTERVALS = {"1d", "5d", "1wk", "1mo", "3mo"}


def fetch_chart(symbol: str, range_: str = "2y", interval: str = "1d", prepost: bool = False) -> Series:
    r = get(
        CHART_URL.format(symbol=quote(symbol)),
        SOURCE,
        params={"range": range_, "interval": interval, "includePrePost": str(prepost).lower(), "events": "div,splits"},
    )
    try:
        payload = r.json()
    except ValueError as e:
        raise DataError(SOURCE, f"{symbol}: response was not JSON") from e

    chart = payload.get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise DataError(SOURCE, f"{symbol}: {err.get('description') if isinstance(err, dict) else err}")
    results = chart.get("result") or []
    if not results:
        raise DataError(SOURCE, f"{symbol}: empty result")

    res = results[0]
    meta = res.get("meta") or {}
    tzname = meta.get("exchangeTimezoneName") or "America/New_York"
    tz = ZoneInfo(tzname)
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    daily = interval in DAILY_INTERVALS
    periods = [] if daily else _regular_periods(meta)

    bars: list[Bar] = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c, v = (_at(col, i) for col in cols)
        if o is None or h is None or l is None or c is None:
            continue
        ts = from_unix(t)
        sess = "regular" if daily else _classify(ts, tz, periods)
        bars.append(Bar(ts, ts.astimezone(tz).date(), float(o), float(h), float(l), float(c), float(v or 0), sess))
    if daily:
        bars = _dedupe_days(bars)
    if not bars:
        raise DataError(SOURCE, f"{symbol}: no bars returned")
    return Series(symbol.upper(), SOURCE, now_utc(), meta.get("currency"), tzname, bars, meta)


def _at(col: list, i: int):
    return col[i] if i < len(col) else None


def _dedupe_days(bars: list[Bar]) -> list[Bar]:
    """Yahoo sometimes repeats the live bar; keep the last bar per session date."""
    by_day: dict[date, Bar] = {}
    for b in bars:
        by_day[b.day] = b
    return sorted(by_day.values(), key=lambda b: b.day)


def _regular_periods(meta: dict) -> list[tuple[int, int]]:
    tp = meta.get("tradingPeriods")
    regs = tp.get("regular") if isinstance(tp, dict) else tp if isinstance(tp, list) else []
    out = []
    for day in regs or []:
        for p in day if isinstance(day, list) else [day]:
            if isinstance(p, dict) and "start" in p and "end" in p:
                out.append((int(p["start"]), int(p["end"])))
    return out


def _classify(ts: datetime, tz: ZoneInfo, periods: list[tuple[int, int]]) -> str:
    t = int(ts.timestamp())
    if any(s <= t < e for s, e in periods):
        return "regular"
    lt = ts.astimezone(tz).time()
    if periods:
        return "pre" if lt < time(12) else "post"
    if lt < time(9, 30):
        return "pre"
    if lt >= time(16):
        return "post"
    return "regular"


@dataclass
class Calendar:
    symbol: str
    earnings: list[date]
    ex_dividend: date | None
    source: str
    fetched_at: datetime


_crumb: str | None = None


def _get_crumb() -> str:
    global _crumb
    if _crumb:
        return _crumb
    try:
        session().get("https://fc.yahoo.com", timeout=10)  # sets the session cookie; a 404 here is normal
    except requests.RequestException:
        pass
    crumb = get(CRUMB_URL, CAL_SOURCE).text.strip()
    if not crumb or "<" in crumb or " " in crumb:
        raise DataError(CAL_SOURCE, "could not obtain a session crumb")
    _crumb = crumb
    return crumb


def fetch_calendar(symbol: str) -> Calendar:
    r = get(SUMMARY_URL.format(symbol=quote(symbol)), CAL_SOURCE, params={"modules": "calendarEvents", "crumb": _get_crumb()})
    try:
        result = ((r.json().get("quoteSummary") or {}).get("result") or [None])[0]
    except ValueError as e:
        raise DataError(CAL_SOURCE, f"{symbol}: response was not JSON") from e
    if not result:
        raise DataError(CAL_SOURCE, f"{symbol}: no calendar data")
    ce = result.get("calendarEvents") or {}
    ny = ZoneInfo("America/New_York")
    earnings = sorted(
        {
            from_unix(x["raw"]).astimezone(ny).date()
            for x in (ce.get("earnings") or {}).get("earningsDate") or []
            if isinstance(x, dict) and x.get("raw")
        }
    )
    exd = ce.get("exDividendDate")
    ex_div = from_unix(exd["raw"]).astimezone(ny).date() if isinstance(exd, dict) and exd.get("raw") else None
    return Calendar(symbol.upper(), earnings, ex_div, CAL_SOURCE, now_utc())
