"""Twelve Data daily time series: keyed backup source for daily bars.

Free Basic plan: 8 requests per minute, 800 per day, US listings. Non-US exchanges
(TSX) and indexes usually need a paid plan; the API's own error message is passed
through when a symbol isn't covered.

Set TWELVEDATA_API_KEY in the environment or in a git-ignored `.env` file at the repo root.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

from ..config import ROOT
from ..models import Bar, Series
from ..timeutil import now_utc
from .http import DataError, get

SOURCE = "Twelve Data"
URL = "https://api.twelvedata.com/time_series"
ENV_KEY = "TWELVEDATA_API_KEY"
MIN_INTERVAL = 60 / 8  # seconds between requests on the free plan
OUTPUTSIZE = 600
EXCHANGES = {".TO": "TSX", ".V": "TSXV", ".NE": "NEO", ".CN": "CSE"}

_last_call = 0.0


def api_key() -> str | None:
    key = os.environ.get(ENV_KEY, "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == ENV_KEY:
                return value.strip().strip("\"'") or None
    return None


def td_params(symbol: str) -> dict | None:
    """Map a Yahoo-style ticker to Twelve Data parameters; None for indexes."""
    s = symbol.upper()
    if s.startswith("^"):
        return None
    for suffix, exchange in EXCHANGES.items():
        if s.endswith(suffix):
            return {"symbol": s[: -len(suffix)], "exchange": exchange}
    return {"symbol": s}


def covers(symbol: str) -> bool:
    return td_params(symbol) is not None


def _throttle() -> None:
    global _last_call
    wait = _last_call + MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def parse(symbol: str, payload: dict) -> Series:
    if payload.get("status") == "error" or "values" not in payload:
        raise DataError(SOURCE, f"{symbol}: {payload.get('message') or 'no values returned'}")
    meta = payload.get("meta") or {}
    tzname = meta.get("exchange_timezone") or "America/New_York"
    tz = ZoneInfo(tzname)
    bars = []
    for v in reversed(payload["values"]):  # the API returns newest first
        try:
            d = date.fromisoformat(str(v["datetime"])[:10])
            o, h, l, c = (float(v[k]) for k in ("open", "high", "low", "close"))
            vol = float(v.get("volume") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        ts = datetime.combine(d, dtime(9, 30), tz).astimezone(timezone.utc)
        bars.append(Bar(ts, d, o, h, l, c, vol))
    if not bars:
        raise DataError(SOURCE, f"{symbol}: no bars")
    return Series(symbol.upper(), SOURCE, now_utc(), meta.get("currency"), tzname, bars, {})


def fetch_daily(symbol: str) -> Series:
    key = api_key()
    if not key:
        raise DataError(SOURCE, f"{ENV_KEY} is not set")
    params = td_params(symbol)
    if params is None:
        raise DataError(SOURCE, f"{symbol}: indexes are not covered")
    _throttle()
    r = get(URL, SOURCE, params={**params, "interval": "1day", "outputsize": OUTPUTSIZE, "apikey": key})
    try:
        payload = r.json()
    except ValueError as e:
        raise DataError(SOURCE, f"{symbol}: response was not JSON") from e
    return parse(symbol, payload)
