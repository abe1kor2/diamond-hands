"""Bank of Canada Valet API: official daily USD/CAD rate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..timeutil import now_utc
from .http import DataError, get

SOURCE = "Bank of Canada Valet"
URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"


@dataclass
class FxRate:
    rate: float  # CAD per 1 USD
    day: date
    source: str
    fetched_at: datetime


def usdcad(on: date | None = None) -> FxRate:
    """Latest official rate, or the latest one on or before `on`."""
    params = {"recent": 1} if on is None else {"start_date": (on - timedelta(days=10)).isoformat(), "end_date": on.isoformat()}
    r = get(URL, SOURCE, params=params)
    try:
        obs = (r.json() or {}).get("observations") or []
    except ValueError as e:
        raise DataError(SOURCE, "response was not JSON") from e
    obs = [o for o in obs if (o.get("FXUSDCAD") or {}).get("v")]
    if not obs:
        raise DataError(SOURCE, f"no USD/CAD observation{'' if on is None else f' on or before {on}'}")
    last = obs[-1]
    return FxRate(float(last["FXUSDCAD"]["v"]), date.fromisoformat(last["d"]), SOURCE, now_utc())


class FxConverter:
    """Converts to the base currency, caching one fetch per requested date."""

    def __init__(self, base: str):
        self.base = base.upper()
        self._cache: dict[date | None, FxRate] = {}

    def rate(self, currency: str, on: date | None = None) -> tuple[float, FxRate | None]:
        cur = currency.upper()
        if cur == self.base:
            return 1.0, None
        if {cur, self.base} != {"USD", "CAD"}:
            raise DataError(SOURCE, f"no {cur}->{self.base} rate (only USD/CAD is supported)")
        if on not in self._cache:
            self._cache[on] = usdcad(on)
        fx = self._cache[on]
        return (fx.rate if cur == "USD" else 1 / fx.rate), fx
