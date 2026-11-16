"""Price and level watch for one plan against the latest daily bar."""

from __future__ import annotations

from datetime import datetime

from .alerts import Alert
from .config import Config
from .indicators import pct, relative_volume
from .models import Series, is_final
from .plans import Plan, propose_trail
from .timeutil import fmt_day, fmt_time, local


def _dist(price: float, level: float) -> str:
    return f"{pct(price, level):+.1f}%, {level - price:+.2f}"


def check_plan(cfg: Config, plan: Plan, series: Series, now: datetime) -> tuple[str, list[Alert]]:
    """Return (status line, alerts). Close-based rules only fire on a final bar."""
    bars = series.bars
    last = bars[-1]
    final = is_final(series, last, now)
    prev = bars[-2].close if len(bars) >= 2 else None
    price = last.close
    src = f"{series.source} {fmt_time(series.market_time() or series.fetched_at, cfg.timezone)}"
    rv = relative_volume([b.volume for b in bars], 20)

    head = f"{plan.ticker} {price:.2f} ({fmt_day(last.day)} {'close' if final else 'live'}"
    if prev:
        head += f", {pct(prev, price):+.2f}% vs prior close {prev:.2f}"
    parts = [head + ")", f"day {last.low:.2f}–{last.high:.2f}"]

    ctx = [f"Day range {last.low:.2f}–{last.high:.2f}."]
    if rv is not None:
        ctx.append(f"Volume {rv:.1f}x 20-day avg{'' if final else ' (partial session)'}.")
    alerts: list[Alert] = []

    def alert(rule: str, level: str, headline: str, day=None) -> None:
        alerts.append(Alert(plan.ticker, rule, level, day or last.day, headline, list(ctx), [src]))

    if plan.status == "open":
        if plan.entry:
            parts.append(f"{pct(plan.entry, price):+.1f}% vs entry {plan.entry:.2f}")
        if plan.stop is not None:
            stop = plan.stop
            parts.append(f"stop {stop:.2f} ({_dist(price, stop)})")
            if final and last.close < stop:
                alert("closed_below_stop", f"{stop:.2f}", f"Closed {last.close:.2f}, below your {stop:.2f} close-stop: matches your stop rule.")
            elif last.low < stop:
                if final:
                    msg = f"Dipped to {last.low:.2f} intraday but closed {last.close:.2f}, above your {stop:.2f} close-stop. Close-based, so not triggered."
                else:
                    msg = f"Traded {last.low:.2f} intraday, below your {stop:.2f} close-stop (now {price:.2f}). Close-based: triggers only on a close below."
                alert("intraday_below_stop", f"{stop:.2f}", msg)
            elif 0 <= pct(stop, price) <= cfg.alerts.near_stop_band_pct:
                alert(
                    "near_stop",
                    f"{stop:.2f}",
                    f"{price:.2f} is {pct(stop, price):.1f}% above your {stop:.2f} close-stop (near-stop band {cfg.alerts.near_stop_band_pct:g}%).",
                )
        if plan.t1 is not None:
            parts.append(f"T1 {plan.t1:.2f} ({_dist(price, plan.t1)})" + (" hit" if plan.t1_hit else ""))
            if not plan.t1_hit and last.high >= plan.t1:
                gain = f" (+{pct(plan.entry, plan.t1):.1f}% from entry)" if plan.entry else ""
                alert("t1_touch", f"{plan.t1:.2f}", f"Touched {last.high:.2f}, at/above your T1 {plan.t1:.2f}{gain}: matches your sell-half rule.")
        prop = propose_trail(plan, [b for b in bars if is_final(series, b, now)], cfg.rules)
        if prop:
            level, why, origin, gain = prop
            alert(
                "trail_stop",
                f"{level:.2f}",
                f"Up {gain:.1f}% from entry: your lock-in rule proposes raising the stop {plan.stop:.2f} → {level:.2f} ({why}). "
                f"Stops only move up. Record with: dh plan update {plan.ticker} --stop {level:.2f}",
                day=origin,
            )
        if plan.out_by:
            days = (plan.out_by - local(now, cfg.timezone).date()).days
            parts.append(f"out by {fmt_day(plan.out_by)} ({days}d)")
            if days <= 0:
                alert("out_by", plan.out_by.isoformat(), f"Out-by date {fmt_day(plan.out_by)} reached: your plan says review or close.", day=plan.out_by)

    elif plan.status == "pending":
        t = plan.trigger or {}
        kind = t.get("type")
        if kind == "limit":
            lim = t["price"]
            parts.append(f"buy limit {lim:.2f} ({_dist(price, lim)})")
            if last.low <= lim:
                alert("buy_limit", f"{lim:.2f}", f"Traded {last.low:.2f}, at/below your {lim:.2f} buy limit.")
        elif kind == "close_above":
            x = t["price"]
            parts.append(f"buy on close above {x:.2f} ({_dist(price, x)})")
            if final and last.close > x:
                alert("buy_trigger_close", f"{x:.2f}", f"Closed {last.close:.2f}, above your {x:.2f} trigger: matches your buy rule.")
            elif not final and price > x:
                alert("buy_trigger_intraday", f"{x:.2f}", f"Trading {price:.2f}, above your {x:.2f} trigger. The trigger is close-based.")
        elif kind == "zone":
            lo, hi = t["low"], t["high"]
            parts.append(f"buy zone {lo:.2f}–{hi:.2f}")
            if last.low <= hi and last.high >= lo:
                alert("buy_zone", f"{lo:.2f}-{hi:.2f}", f"Traded inside your {lo:.2f}–{hi:.2f} buy zone (day range {last.low:.2f}–{last.high:.2f}).")
        if plan.stop is not None:
            parts.append(f"planned stop {plan.stop:.2f}")

    return " | ".join(parts), alerts
