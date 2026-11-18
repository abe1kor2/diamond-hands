"""Nasdaq public quote API (no key): US-listed stocks and ETFs.

Endpoints used:
  quote/<SYM>/info              last price, change, day and 52-week range, market status
  quote/<SYM>/historical        daily OHLCV (stops at the last published session)
  quote/<SYM>/chart             1-minute prices for the latest session, 4:00 AM to 8:00 PM ET
  quote/<SYM>/extended-trading  pre-market / after-hours last trade and volume
  analyst/<SYM>/earnings-date   next announced earnings date
The API is public but unofficial; every failure surfaces as DataError.
"""

from __future__ import annotations

import re
import time as time_mod
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote as urlquote
from zoneinfo import ZoneInfo

from ..models import Bar, Quote, Series
from ..timeutil import now_utc
from .http import DataError, get

SOURCE = "Nasdaq"
BASE = "https://api.nasdaq.com/api"
HEADERS = {"Accept": "application/json, text/plain, */*"}
ET = ZoneInfo("America/New_York")
TZ_NAME = "America/New_York"
ASSET_CLASSES = ("stocks", "etf")
NON_US_SUFFIXES = (".TO", ".V", ".NE", ".CN")
REGULAR_OPEN, REGULAR_CLOSE = time(9, 30), time(16, 0)

INFO_TTL = 60  # seconds

_class_cache: dict[str, str] = {}
_info_cache: dict[str, tuple[float, dict, str]] = {}
_NUM = re.compile(r"-?\d[\d,]*\.?\d*|-?\.\d+")
_DATE = re.compile(r"[A-Z][a-z]{2} \d{1,2}, \d{4}")


def covers(symbol: str) -> bool:
    s = symbol.upper()
    return not s.startswith("^") and not s.endswith(NON_US_SUFFIXES)


def num(value) -> float | None:
    """'$1,234.56' / '-1.06%' / '235,730,052.01' -> float; None when absent."""
    if value is None:
        return None
    m = _NUM.search(str(value).replace("$", ""))
    return float(m.group(0).replace(",", "")) if m else None


def span(value) -> tuple[float | None, float | None]:
    """'150.33 - 157.46' or '$157.46/$150.33' -> (low, high)."""
    text = str(value or "")
    parts = text.split(" - ") if " - " in text else text.split("/")
    if len(parts) != 2:
        return None, None
    lo, hi = num(parts[0]), num(parts[1])
    if lo is None or hi is None:
        return None, None
    return min(lo, hi), max(lo, hi)


def _json(path: str, params: dict | None, what: str) -> dict:
    r = get(f"{BASE}/{path}", SOURCE, params=params, headers=HEADERS)
    try:
        return r.json()
    except ValueError as e:
        raise DataError(SOURCE, f"{what}: response was not JSON") from e


def _ok(payload: dict) -> bool:
    return bool(payload.get("data")) and (payload.get("status") or {}).get("rCode") == 200


def info(symbol: str) -> tuple[dict, str]:
    """Quote info plus the asset class ('stocks' or 'etf') that worked. Reused for INFO_TTL seconds."""
    s = symbol.upper()
    if not covers(s):
        raise DataError(SOURCE, f"{s}: not a US listing")
    hit = _info_cache.get(s)
    if hit and time_mod.monotonic() - hit[0] < INFO_TTL:
        return hit[1], hit[2]
    for ac in [_class_cache[s]] if s in _class_cache else ASSET_CLASSES:
        payload = _json(f"quote/{urlquote(s)}/info", {"assetclass": ac}, s)
        if _ok(payload):
            _class_cache[s] = ac
            _info_cache[s] = (time_mod.monotonic(), payload["data"], ac)
            return payload["data"], ac
    raise DataError(SOURCE, f"{s}: symbol not found")


def parse_quote(symbol: str, d: dict) -> Quote:
    p = d.get("primaryData") or {}
    price = num(p.get("lastSalePrice"))
    if price is None:
        raise DataError(SOURCE, f"{symbol}: no last sale price")
    ks = d.get("keyStats") or {}
    day_lo, day_hi = span((ks.get("dayrange") or {}).get("value"))
    lo52, hi52 = span((ks.get("fiftyTwoWeekHighLow") or {}).get("value"))
    extra = []
    sd = d.get("secondaryData")
    if isinstance(sd, dict) and sd.get("lastSalePrice"):
        change = " ".join(x for x in (sd.get("netChange"), f"({sd['percentageChange']})" if sd.get("percentageChange") else None) if x)
        extra.append(f"{sd['lastSalePrice']} {change} as of {sd.get('lastTradeTimestamp') or 'n/a'}".replace("  ", " "))
    return Quote(
        symbol=symbol.upper(),
        name=d.get("companyName"),
        price=price,
        change=num(p.get("netChange")),
        change_pct=num(p.get("percentageChange")),
        prev_close=None,
        day_low=day_lo,
        day_high=day_hi,
        volume=num(p.get("volume")),
        low_52w=lo52,
        high_52w=hi52,
        market_status=d.get("marketStatus"),
        last_trade=p.get("lastTradeTimestamp"),
        extra=extra,
        source=SOURCE,
        fetched_at=now_utc(),
        real_time=p.get("isRealTime"),
    )


def fetch_quote(symbol: str) -> Quote:
    d, _ = info(symbol)
    return parse_quote(symbol, d)


def parse_historical(rows: list[dict]) -> list[Bar]:
    bars = []
    for r in reversed(rows or []):  # the API returns newest first
        try:
            day = datetime.strptime(r["date"], "%m/%d/%Y").date()
        except (KeyError, ValueError):
            continue
        o, h, l, c = (num(r.get(k)) for k in ("open", "high", "low", "close"))
        if o is None or h is None or l is None or c is None:
            continue
        ts = datetime.combine(day, REGULAR_OPEN, ET).astimezone(timezone.utc)
        bars.append(Bar(ts, day, o, h, l, c, num(r.get("volume")) or 0.0))
    return bars


def chart_points(chart: dict) -> list[tuple[datetime, float]]:
    """1-minute (time, price) points. `x` is Eastern wall-clock time encoded as epoch ms."""
    out = []
    for p in chart.get("chart") or []:
        try:
            wall = datetime.fromtimestamp(p["x"] / 1000, timezone.utc).replace(tzinfo=None)
            out.append((wall.replace(tzinfo=ET).astimezone(timezone.utc), float(p["y"])))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return out


def _session(t: datetime) -> str:
    lt = t.astimezone(ET).time()
    if lt < REGULAR_OPEN:
        return "pre"
    if lt >= REGULAR_CLOSE:
        return "post"
    return "regular"


def _chart(symbol: str, ac: str) -> dict:
    payload = _json(f"quote/{urlquote(symbol)}/chart", {"assetclass": ac}, symbol)
    if not _ok(payload):
        raise DataError(SOURCE, f"{symbol}: no intraday chart")
    return payload["data"]


def live_bar(info_data: dict, chart: dict, day: date) -> Bar | None:
    """Today's bar from live data, marked provisional until the official close is published."""
    pts = [(t, v) for t, v in chart_points(chart) if t.astimezone(ET).date() == day and _session(t) == "regular"]
    if not pts:
        return None
    lo, hi = span(((info_data.get("keyStats") or {}).get("dayrange") or {}).get("value"))
    prices = [v for _, v in pts]
    high = max([v for v in (hi, max(prices)) if v is not None])
    low = min([v for v in (lo, min(prices)) if v is not None])
    vol = num(chart.get("volume")) or num((info_data.get("primaryData") or {}).get("volume")) or 0.0
    return Bar(pts[0][0], day, pts[0][1], high, low, pts[-1][1], vol, "regular", provisional=True)


def fetch_daily(symbol: str, years: int = 2, now: datetime | None = None) -> Series:
    now = now or now_utc()
    s = symbol.upper()
    d, ac = info(s)
    today = now.astimezone(ET).date()
    payload = _json(
        f"quote/{urlquote(s)}/historical",
        {"assetclass": ac, "fromdate": (today - timedelta(days=365 * years + 7)).isoformat(), "todate": today.isoformat(), "limit": 9999},
        s,
    )
    rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
    bars = parse_historical(rows)
    if not bars:
        raise DataError(SOURCE, f"{s}: no daily bars")
    as_of = None
    if bars[-1].day < today:
        try:
            chart = _chart(s, ac)
        except DataError:
            chart = {}
        live = live_bar(d, chart, today)
        if live:
            bars.append(live)
            pts = chart_points(chart)
            as_of = pts[-1][0] if pts else None
    return Series(s, SOURCE, now_utc(), "USD", TZ_NAME, bars, {"assetclass": ac, "marketStatus": d.get("marketStatus")}, as_of)


def fetch_intraday(symbol: str) -> Series:
    """Latest session's 1-minute prices as point bars (open = high = low = close)."""
    s = symbol.upper()
    _, ac = info(s)
    pts = chart_points(_chart(s, ac))
    if not pts:
        raise DataError(SOURCE, f"{s}: no intraday points")
    bars = [Bar(t, t.astimezone(ET).date(), v, v, v, v, 0.0, _session(t)) for t, v in pts]
    return Series(s, SOURCE + " 1-minute prices", now_utc(), "USD", TZ_NAME, bars, {}, pts[-1][0])


def extended_hours(symbol: str) -> str:
    """Pre-market and after-hours last trade and volume, as the source reports them."""
    s = symbol.upper()
    _, ac = info(s)
    out = []
    for market, label in (("pre", "pre-market"), ("post", "after-hours")):
        payload = _json(f"quote/{urlquote(s)}/extended-trading", {"assetclass": ac, "markettype": market}, s)
        data = payload.get("data") or {}
        rows = ((data.get("infoTable") or {}).get("rows")) or []
        if not rows or not rows[0].get("consolidated"):
            continue
        r = rows[0]
        line = f"{label} last {r.get('consolidated')}, volume {r.get('volume')}, high {r.get('highPrice')}, low {r.get('lowPrice')}"
        updated = [u for u in data.get("lastUpdateInfo") or [] if u.startswith("Data last updated")]
        if updated:
            line += f" ({updated[0].removeprefix('Data last updated ').rstrip('.')})"
        out.append(line)
    return "; ".join(out) if out else "no extended-hours trading reported"


def fetch_earnings(symbol: str) -> tuple[list[date] | None, str]:
    """(dates, note). [] for ETFs; None when no date has been announced."""
    s = symbol.upper()
    _, ac = info(s)
    if ac == "etf":
        return [], f"{SOURCE}: ETF, no earnings reports"
    payload = _json(f"analyst/{urlquote(s)}/earnings-date", None, s)
    data = payload.get("data") or {}
    m = _DATE.search(data.get("announcement") or "")
    if not m:
        return None, f"{SOURCE}: no upcoming earnings date announced"
    day = datetime.strptime(m.group(0), "%b %d, %Y").date()
    text = data.get("reportText") or ""
    timing = "before market open" if "before market open" in text else "after market close" if "after market close" in text else "time not given"
    return [day], f"{SOURCE}: {day.strftime('%b %-d')} {timing}"
