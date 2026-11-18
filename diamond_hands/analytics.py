"""Volume, candle, trend and level analytics from daily (and 5-minute) bars."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from .candles import CandleRead, read_candle, streak
from .config import Config
from .indicators import pct, relative_strength, relative_volume, sma, swing_points, up_down_volume, weekly, wilder_atr, wilder_rsi
from .models import Series, is_final
from .timeutil import fmt_day, fmt_time, now_utc


def _direction(now_v: float | None, prev_v: float | None) -> str:
    if now_v is None or prev_v is None:
        return "n/a"
    return "rising" if now_v > prev_v else "falling" if now_v < prev_v else "flat"


def _ratio(up: float, down: float) -> str:
    if down == 0:
        return "all up-volume" if up > 0 else "n/a"
    return f"{up / down:.2f}:1"


@dataclass
class Analytics:
    symbol: str
    as_of: date
    final: bool
    close: float
    change_pct: float | None
    rel_vol_20: float | None
    rel_vol_50: float | None
    updown_5: tuple[float, float] | None
    updown_20: tuple[float, float] | None
    range_note: str | None
    candles: list[CandleRead]
    streak: tuple[str, int]
    mas: dict[int, dict]
    range_label: str
    high_52w: float
    low_52w: float
    swing_highs: list[tuple[date, float]]
    swing_lows: list[tuple[date, float]]
    weekly_trend: str | None
    atr14: float | None
    range_vs_atr: float | None
    rsi14: float | None
    rel_strength: list[dict]
    ext_hours: str | None
    flags: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def lines(self) -> list[str]:
        out = [
            f"{self.symbol} analytics, {fmt_day(self.as_of)} {'close' if self.final else '(session open)'}: {self.close:.2f}"
            + (f" ({self.change_pct:+.2f}%)" if self.change_pct is not None else "")
        ]
        vol = []
        if self.rel_vol_20 is not None:
            vol.append(f"{self.rel_vol_20:.1f}x 20-day avg")
        if self.rel_vol_50 is not None:
            vol.append(f"{self.rel_vol_50:.1f}x 50-day avg")
        for n, ud in ((5, self.updown_5), (20, self.updown_20)):
            if ud:
                vol.append(f"up/down volume {n}d {_ratio(*ud)}")
        if vol:
            out.append("Volume: " + " · ".join(vol))
        if self.range_note:
            out.append("Range: " + self.range_note)
        out.append("Candles: " + " ".join(c.text() + "." for c in self.candles))
        direction, n = self.streak
        if n:
            out.append(f"Streak: {n} {direction} close{'s' if n != 1 else ''} in a row")
        trend = []
        for n, m in self.mas.items():
            if m["value"] is None:
                trend.append(f"{n}d MA n/a (not enough bars)")
            else:
                trend.append(f"{n}d MA {m['value']:.2f} {m['direction']} (price {m['price_vs_pct']:+.1f}%)")
        out.append("Trend: " + " · ".join(trend))
        out.append(
            f"{self.range_label}: high {self.high_52w:.2f} ({pct(self.high_52w, self.close):+.1f}%), "
            f"low {self.low_52w:.2f} ({pct(self.low_52w, self.close):+.1f}%)"
        )
        if self.swing_highs or self.swing_lows:
            sh = ", ".join(f"{p:.2f} ({fmt_day(d)})" for d, p in self.swing_highs) or "none"
            sl = ", ".join(f"{p:.2f} ({fmt_day(d)})" for d, p in self.swing_lows) or "none"
            out.append(f"Swing highs: {sh} | Swing lows: {sl}")
        if self.weekly_trend:
            out.append("Weekly: " + self.weekly_trend)
        vola = []
        if self.atr14 is not None:
            vola.append(f"ATR(14) {self.atr14:.2f}")
        if self.range_vs_atr is not None:
            vola.append(f"today's range {self.range_vs_atr:.1f}x prior ATR")
        if self.rsi14 is not None:
            vola.append(f"RSI(14) {self.rsi14:.1f}")
        if vola:
            out.append(" · ".join(vola))
        if self.rel_strength:
            out.append(
                "Relative strength: "
                + " · ".join(
                    f"vs {r['vs']} {r['days']}d {r['diff']:+.1f} pts ({r['ticker_pct']:+.1f}% vs {r['bench_pct']:+.1f}%)" for r in self.rel_strength
                )
            )
        if self.ext_hours:
            out.append("Extended hours: " + self.ext_hours)
        out += self.notes
        out.append("Source: " + "; ".join(self.sources))
        return out


def ext_hours_summary(intra: Series) -> str:
    vol: dict[tuple[date, str], float] = {}
    for b in intra.bars:
        if b.session in ("pre", "post"):
            vol[(b.day, b.session)] = vol.get((b.day, b.session), 0.0) + b.volume
    out = []
    for sess, label in (("pre", "pre-market"), ("post", "after-hours")):
        days = sorted(d for d, s in vol if s == sess and vol[(d, s)] > 0)
        if not days:
            continue
        latest = days[-1]
        v = vol[(latest, sess)]
        prior = [vol[(d, sess)] for d in days[:-1]]
        s = f"{label} {fmt_day(latest)} {v:,.0f} shares"
        if prior:
            s += f" ({v / (sum(prior) / len(prior)):.1f}x prior {len(prior)}-session avg)"
        out.append(s)
    return "; ".join(out) if out else "no extended-hours volume reported"


def compute(
    cfg: Config,
    daily: Series,
    now: datetime | None = None,
    benches: dict[str, Series] | None = None,
    ext_hours: str | None = None,
    ext_source: str | None = None,
) -> Analytics:
    now = now or now_utc()
    bars = daily.bars
    if len(bars) < 2:
        raise ValueError(f"{daily.symbol}: not enough daily bars")
    last, prev = bars[-1], bars[-2]
    final = is_final(daily, last, now)
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    notes: list[str] = []
    flags: list[dict] = []
    if not final:
        notes.append(f"{fmt_day(last.day)} session still open: the last bar and its volume are partial.")

    rv20, rv50 = relative_volume(vols, 20), relative_volume(vols, 50)

    range_note = None
    if len(bars) > 21:
        window = bars[-21:-1]
        hi, lo = max(b.high for b in window), min(b.low for b in window)
        vol_txt = f" on {rv20:.1f}x 20-day volume" if rv20 is not None else ""
        verb = "Closed" if final else "Trading"
        if last.close > hi:
            range_note = f"{verb} {last.close:.2f}, above the prior 20-day high {hi:.2f}{vol_txt}."
            if final:
                flags.append({"rule": "breakout_20d", "level": f"{hi:.2f}", "text": range_note})
        elif last.close < lo:
            range_note = f"{verb} {last.close:.2f}, below the prior 20-day low {lo:.2f}{vol_txt}."
            if final:
                flags.append({"rule": "breakdown_20d", "level": f"{lo:.2f}", "text": range_note})

    candles = [read_candle(bars, i, cfg.alerts.big_candle_body_pct, relative_volume(vols, 20, i)) for i in range(max(1, len(bars) - 5), len(bars))]
    if final and candles[-1].big:
        flags.append({"rule": "big_candle", "level": candles[-1].color, "text": candles[-1].text() + "."})
    if final and rv20 is not None and rv20 >= cfg.alerts.relative_volume_flag:
        flags.append(
            {
                "rule": "rel_volume",
                "level": f"{cfg.alerts.relative_volume_flag:g}x",
                "text": f"Volume {rv20:.1f}x its 20-day average on {fmt_day(last.day)} ({last.volume:,.0f} shares).",
            }
        )

    mas = {}
    for n in (20, 50, 200):
        v = sma(closes, n)
        mas[n] = {"value": v, "direction": _direction(v, sma(closes, n, len(closes) - 6)), "price_vs_pct": pct(v, last.close) if v else None}

    yr = bars[-252:]
    range_label = "52-week" if len(yr) >= 252 else f"{len(yr)}-session"

    swing_src = bars if final else bars[:-1]
    hs, ls = swing_points(swing_src, cfg.rules.swing_lookback)

    weekly_trend = None
    wk = weekly(bars)
    if len(wk) >= 12:
        wc = [w.close for w in wk]
        m, mp = sma(wc, 10), sma(wc, 10, len(wc) - 2)
        weekly_trend = f"week ending {fmt_day(wk[-1].day)}: close {wc[-1]:.2f} {'above' if wc[-1] > m else 'below'} the 10-week MA {m:.2f} ({_direction(m, mp)})"
        h3, l3 = [w.high for w in wk[-3:]], [w.low for w in wk[-3:]]
        if h3[0] < h3[1] < h3[2] and l3[0] < l3[1] < l3[2]:
            weekly_trend += "; 3 weeks of higher highs and higher lows"
        elif h3[0] > h3[1] > h3[2] and l3[0] > l3[1] > l3[2]:
            weekly_trend += "; 3 weeks of lower highs and lower lows"

    atr_prev = wilder_atr(bars[:-1], 14)

    rs = []
    for name, bs in (benches or {}).items():
        for n in (5, 20):
            r = relative_strength(bars, bs.bars, n)
            if r:
                rs.append({"vs": name, "days": n, "ticker_pct": r[0], "bench_pct": r[1], "diff": r[0] - r[1]})

    sources = [f"{daily.source} daily bars, fetched {fmt_time(daily.fetched_at, cfg.timezone)}"]
    if last.provisional:
        sources[0] += f" (today's bar is live and provisional until {daily.source} publishes the official close)"
    sources += [f"{bs.source} {name} daily bars" for name, bs in (benches or {}).items()]
    if ext_hours and ext_source:
        sources.append(f"{ext_source} extended-hours data")

    return Analytics(
        symbol=daily.symbol,
        as_of=last.day,
        final=final,
        close=last.close,
        change_pct=pct(prev.close, last.close),
        rel_vol_20=rv20,
        rel_vol_50=rv50,
        updown_5=up_down_volume(bars, 5),
        updown_20=up_down_volume(bars, 20),
        range_note=range_note,
        candles=candles,
        streak=streak(bars),
        mas=mas,
        range_label=range_label,
        high_52w=max(b.high for b in yr),
        low_52w=min(b.low for b in yr),
        swing_highs=[(swing_src[i].day, p) for i, p in hs[-3:]],
        swing_lows=[(swing_src[i].day, p) for i, p in ls[-3:]],
        weekly_trend=weekly_trend,
        atr14=wilder_atr(bars, 14),
        range_vs_atr=(last.high - last.low) / atr_prev if atr_prev else None,
        rsi14=wilder_rsi(closes, 14),
        rel_strength=rs,
        ext_hours=ext_hours,
        flags=flags,
        sources=sources,
        notes=notes,
    )
