"""Loads config.yaml (personal settings) into typed objects."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .timeutil import parse_date

ROOT = Path(__file__).resolve().parent.parent
CA_SUFFIXES = (".TO", ".V", ".NE", ".CN")


class ConfigError(Exception):
    pass


def is_canadian(ticker: str) -> bool:
    return ticker.upper().endswith(CA_SUFFIXES)


def infer_currency(ticker: str) -> str:
    return "CAD" if is_canadian(ticker) else "USD"


@dataclass
class SwingHolding:
    ticker: str
    shares: float
    avg_cost: float
    stop_close_below: float
    target_1: float | None = None
    out_by: date | None = None
    entry_date: date | None = None
    account: str = "real"


@dataclass
class Rules:
    hold_weeks: tuple[float, float] = (4, 8)
    target_1_pct: tuple[float, float] = (10, 12)
    t1_sell_fraction: float = 0.5
    after_target_1: str = "breakeven_then_trail_higher_swing_lows"
    stop_type: str = "close_based"
    stop_distance_pct: tuple[float, float] = (7, 10)
    trailing_lock_in_after_gain_pct: float = 10
    swing_lookback: int = 2
    risk_per_trade_pct: float = 1
    max_position_pct: float = 10
    max_open_ideas: int = 8


@dataclass
class PaperCfg:
    enabled: bool = True
    starting_capital: float = 100_000
    benchmark: str = "VFV.TO"


@dataclass
class AlertCfg:
    near_stop_band_pct: float = 1.5
    relative_volume_flag: float = 1.5
    big_candle_body_pct: float = 3.0
    sentiment_shift_flag: float = 20


@dataclass
class Config:
    timezone: str
    markets: list[str]
    base_currency: str
    fx_source: str
    account_equity: float | None
    core_holdings: list[str]
    swing_holdings: list[SwingHolding]
    watchlist: list[str]
    rules: Rules
    paper_book: PaperCfg
    alerts: AlertCfg
    us_index: str = "^GSPC"
    ca_index: str = "^GSPTSE"
    sector_index: dict[str, str] = field(default_factory=dict)
    root: Path = ROOT

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def index_for(self, ticker: str) -> str:
        return self.ca_index if is_canadian(ticker) else self.us_index


def _pair(value: Any, name: str) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    raise ConfigError(f"rules.{name} must be a two-item list like [7, 10]")


def _rules(raw: dict) -> Rules:
    r = Rules()
    for f in fields(Rules):
        if f.name not in raw:
            continue
        current = getattr(r, f.name)
        v = raw[f.name]
        if isinstance(current, tuple):
            setattr(r, f.name, _pair(v, f.name))
        elif isinstance(current, int) and not isinstance(current, bool) and f.name in ("swing_lookback", "max_open_ideas"):
            setattr(r, f.name, int(v))
        elif isinstance(current, (int, float)):
            setattr(r, f.name, float(v))
        else:
            setattr(r, f.name, str(v))
    return r


def _holding(raw: dict, i: int) -> SwingHolding:
    if not isinstance(raw, dict):
        raise ConfigError(f"swing_holdings[{i}] must be a mapping")
    missing = [k for k in ("ticker", "shares", "avg_cost", "stop_close_below") if raw.get(k) is None]
    if missing:
        raise ConfigError(f"swing_holdings[{i}] is missing: {', '.join(missing)}")
    account = str(raw.get("account") or "real")
    if account == "paper":
        raise ConfigError(f"swing_holdings[{i}]: account 'paper' is reserved for the paper book")
    return SwingHolding(
        ticker=str(raw["ticker"]).upper(),
        shares=float(raw["shares"]),
        avg_cost=float(raw["avg_cost"]),
        stop_close_below=float(raw["stop_close_below"]),
        target_1=float(raw["target_1"]) if raw.get("target_1") is not None else None,
        out_by=parse_date(raw.get("out_by")),
        entry_date=parse_date(raw.get("entry_date")),
        account=account,
    )


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    if not path.exists():
        raise ConfigError(f"{path} not found. Copy config.example.yaml to config.yaml and fill in your settings.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a YAML mapping")

    tz = str(raw.get("timezone") or "America/St_Johns")
    try:
        ZoneInfo(tz)
    except Exception as e:  # noqa: BLE001 - zoneinfo raises several types
        raise ConfigError(f"unknown timezone {tz!r}") from e

    base = str(raw.get("base_currency") or "CAD").upper()
    if base not in ("CAD", "USD"):
        raise ConfigError("base_currency must be CAD or USD (FX comes from the Bank of Canada USD/CAD rate)")

    bm = raw.get("benchmarks") or {}
    paper = raw.get("paper_book") or {}
    alerts = raw.get("alerts") or {}
    eq = raw.get("account_equity")
    alert_names = {f.name for f in fields(AlertCfg)}

    return Config(
        timezone=tz,
        markets=[str(m) for m in raw.get("markets") or []],
        base_currency=base,
        fx_source=str(raw.get("fx_source") or "bank_of_canada"),
        account_equity=float(eq) if eq not in (None, "") else None,
        core_holdings=[str(t).upper() for t in raw.get("core_holdings") or []],
        swing_holdings=[_holding(h, i) for i, h in enumerate(raw.get("swing_holdings") or [])],
        watchlist=[str(t).upper() for t in raw.get("watchlist") or []],
        rules=_rules(raw.get("rules") or {}),
        paper_book=PaperCfg(
            enabled=bool(paper.get("enabled", True)),
            starting_capital=float(paper.get("starting_capital", 100_000)),
            benchmark=str(paper.get("benchmark") or "VFV.TO").upper(),
        ),
        alerts=AlertCfg(**{k: float(v) for k, v in alerts.items() if k in alert_names}),
        us_index=str(bm.get("us_index") or "^GSPC"),
        ca_index=str(bm.get("ca_index") or "^GSPTSE"),
        sector_index={str(k).upper(): str(v) for k, v in (bm.get("sector") or {}).items()},
        root=path.resolve().parent,
    )
