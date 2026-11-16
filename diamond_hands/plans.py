"""Trade plans, the append-only plan log, risk cards and trailing-stop proposals."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from datetime import date

import yaml

from .config import Config, Rules, infer_currency
from .indicators import pct, swing_points
from .models import Bar
from .store import Store, new_id
from .timeutil import fmt_day, iso, now_utc, parse_date

PAPER = "paper"
PLANS = "plans.jsonl"
HOLDINGS = "holdings.yaml"
MARK = {True: "✓", False: "✗", None: "?"}


def in_group(account: str, group: str | None) -> bool:
    """group: 'paper', 'real' (every non-paper account), or None for all."""
    if group is None:
        return True
    return account == PAPER if group == PAPER else account != PAPER


def trigger_label(trigger: dict | None) -> str:
    if not trigger:
        return "Entry: NOT SET"
    kind = trigger.get("type")
    if kind == "limit":
        return f"Buy limit {trigger['price']:.2f}"
    if kind == "close_above":
        return f"Buy on first close above {trigger['price']:.2f}"
    if kind == "zone":
        return f"Buy zone {trigger['low']:.2f}–{trigger['high']:.2f}"
    if kind == "moc":
        return "Buy at the next close"
    return f"Trigger {trigger}"


@dataclass
class Plan:
    plan_id: str
    account: str
    ticker: str
    status: str = "pending"  # pending | open | closed
    entry: float | None = None  # average cost once open
    stop: float | None = None  # close-based
    t1: float | None = None
    out_by: date | None = None
    shares: float | None = None
    entry_date: date | None = None
    trigger: dict | None = None
    currency: str | None = None
    t1_hit: bool = False
    effective_at: str | None = None  # stop/T1 apply to prices after this moment
    reason: str = ""
    supersedes: str | None = None  # id of the log entry this one replaces
    entry_id: str | None = None  # id of this log entry (not stored in the record)

    @classmethod
    def from_entry(cls, e: dict) -> Plan:
        names = {f.name for f in fields(cls)}
        kw = {k: v for k, v in e.items() if k in names}
        kw["entry_id"] = e.get("id")
        kw["out_by"] = parse_date(kw.get("out_by"))
        kw["entry_date"] = parse_date(kw.get("entry_date"))
        return cls(**kw)

    def to_record(self) -> dict:
        d = asdict(self)
        d.pop("entry_id")
        d["out_by"] = self.out_by.isoformat() if self.out_by else None
        d["entry_date"] = self.entry_date.isoformat() if self.entry_date else None
        return {"type": "plan", **d}

    @property
    def is_paper(self) -> bool:
        return self.account == PAPER

    @property
    def active(self) -> bool:
        return self.status in ("pending", "open")

    def planned_entry(self) -> float | None:
        if self.entry is not None:
            return self.entry
        t = self.trigger or {}
        if t.get("type") == "zone":
            return t.get("high")  # worst-case fill inside the zone
        return t.get("price")


@dataclass
class CardContext:
    equity_base: float | None
    base_currency: str
    fx_to_base: float | None  # 1.0 for same currency; None if unknown
    fx_note: str | None
    open_ideas: int  # including this plan
    earnings: list[date] | None  # None means no data
    earnings_note: str | None
    today: date


def sized_shares(entry: float | None, stop: float | None, rules: Rules, equity: float | None, fx: float | None) -> int | None:
    """Shares that keep risk-to-stop within risk_per_trade_pct and size within max_position_pct."""
    if entry is None or stop is None or not equity or fx is None or entry <= stop:
        return None
    by_risk = equity * rules.risk_per_trade_pct / 100 / ((entry - stop) * fx)
    by_size = equity * rules.max_position_pct / 100 / (entry * fx)
    return max(0, math.floor(min(by_risk, by_size)))


def risk_card(plan: Plan, rules: Rules, ctx: CardContext) -> tuple[str, str]:
    """Return (card text, 'PASS' | 'BLOCK'). The card reports; it never decides."""
    cur = (plan.currency or infer_currency(plan.ticker)).upper()
    base = ctx.base_currency
    entry = plan.planned_entry()
    stop = plan.stop
    rps = entry - stop if entry is not None and stop is not None else None
    fx, eq, shares = ctx.fx_to_base, ctx.equity_base, plan.shares
    notes: list[str] = []

    name = plan.ticker + (" (paper)" if plan.is_paper else "")
    entry_txt = f"Entry {entry:.2f}" if plan.status == "open" and entry is not None else trigger_label(plan.trigger)
    head = [name, entry_txt, f"Stop (close) {stop:.2f}" if stop is not None else "Stop: NOT SET"]
    if rps is not None:
        head.append(f"Risk/share {rps:.2f}")
        if rps <= 0:
            notes.append("Stop is at or above entry, so risk per share is not positive.")
    lines = ["  |  ".join(head)]

    risk_amt = rps * shares if rps is not None and rps > 0 and shares else None
    l2 = [f"Shares {shares:g}" if shares else "Shares not set"]
    if risk_amt is not None:
        s = f"$ at risk {risk_amt:,.2f} {cur}"
        conv = risk_amt * fx if fx is not None else None
        if cur != base and conv is not None:
            s += f" = {conv:,.2f} {base}"
        if conv is not None and eq:
            s += f" ({conv / eq * 100:.1f}% of equity)"
        l2.append(s)
    sized = sized_shares(entry, stop, rules, eq, fx)
    if sized is not None:
        l2.append(f"size to your rules: {sized} sh")
    lines.append("  |  ".join(l2))

    l3 = []
    if plan.t1 is not None and entry:
        frac = "sell half" if abs(rules.t1_sell_fraction - 0.5) < 1e-9 else f"sell {rules.t1_sell_fraction:.0%}"
        r_mult = f", {(plan.t1 - entry) / rps:.2f}R" if rps and rps > 0 else ""
        t1 = f"T1 {plan.t1:.2f} ({pct(entry, plan.t1):+.1f}%{r_mult}) {frac}"
        if plan.t1_hit:
            t1 += " (hit: after-T1 rule is breakeven, then trail higher swing lows)"
        l3.append(t1)
    else:
        l3.append("T1: NOT SET")
    l3.append(f"Out by {fmt_day(plan.out_by)}" if plan.out_by else "Out by: NOT SET")
    lines.append("  |  ".join(l3))

    hold_start = plan.entry_date or ctx.today
    if plan.out_by is None:
        lines.append("Earnings inside hold: unknown (no out-by date)")
    elif ctx.earnings is None:
        lines.append(f"Earnings inside hold: unknown ({ctx.earnings_note})")
    else:
        inside = [d for d in ctx.earnings if hold_start <= d <= plan.out_by]
        lines.append("Earnings inside hold: " + (f"YES ({', '.join(fmt_day(d) for d in inside)})" if inside else "NO"))

    checks: list[tuple[str, bool | None]] = []
    if shares and entry and eq and fx is not None:
        size_pct = shares * entry * fx / eq * 100
        checks.append((f"size {size_pct:.2f}%/{rules.max_position_pct:g}%", size_pct <= rules.max_position_pct + 1e-9))
    else:
        checks.append(("size", None))
    if risk_amt is not None and eq and fx is not None:
        risk_pct = risk_amt * fx / eq * 100
        checks.append((f"risk {risk_pct:.2f}%/{rules.risk_per_trade_pct:g}%", risk_pct <= rules.risk_per_trade_pct + 1e-9))
    else:
        checks.append(("risk", None))
    checks.append((f"idea count {ctx.open_ideas}/{rules.max_open_ideas}", ctx.open_ideas <= rules.max_open_ideas))
    verdict = "BLOCK" if any(ok is False for _, ok in checks) else "PASS"
    lines.append("Rule check: " + "  ".join(f"{label} {MARK[ok]}" for label, ok in checks) + f"  → {verdict}")

    vs = []
    if rps is not None and rps > 0 and entry:
        sd, (lo, hi) = rps / entry * 100, rules.stop_distance_pct
        vs.append(f"stop {sd:.1f}% under entry (rule {lo:g}–{hi:g}) {MARK[lo <= sd <= hi]}")
    if plan.t1 is not None and entry:
        tp, (lo, hi) = pct(entry, plan.t1), rules.target_1_pct
        vs.append(f"T1 {tp:+.1f}% (rule {lo:g}–{hi:g}) {MARK[lo <= tp <= hi]}")
    if plan.out_by:
        wks, (lo, hi) = (plan.out_by - hold_start).days / 7, rules.hold_weeks
        vs.append(f"hold {wks:.1f} wks (rule {lo:g}–{hi:g}) {MARK[lo <= wks <= hi]}")
    if vs:
        lines.append("Plan vs rules: " + " · ".join(vs))

    if not eq:
        notes.append(
            "Paper equity unavailable, so size and risk are not checked."
            if plan.is_paper
            else "account_equity is not set in config.yaml, so size and risk are not checked."
        )
    if cur != base and ctx.fx_note:
        notes.append(f"FX: {ctx.fx_note}")
    if ctx.earnings is not None and ctx.earnings_note:
        notes.append(f"Earnings dates: {ctx.earnings_note}")
    lines += notes
    return "\n".join(lines), verdict


def propose_trail(plan: Plan, bars: Sequence[Bar], rules: Rules) -> tuple[float, str, date, float] | None:
    """Propose a higher stop once the lock-in gain is reached or T1 was hit.

    Candidates are breakeven and the latest swing low that is higher than the one before it.
    Returns (new_stop, why, origin_day, gain_pct) or None. Never proposes a lower stop.
    `bars` must contain final (closed) sessions only.
    """
    if plan.status != "open" or plan.entry is None or plan.stop is None or len(bars) < 2 * rules.swing_lookback + 2:
        return None
    last = bars[-1].close
    gain = pct(plan.entry, last)
    if not (plan.t1_hit or gain >= rules.trailing_lock_in_after_gain_pct):
        return None
    candidates = [(plan.entry, "breakeven", plan.entry_date or bars[-1].day)]
    _, lows = swing_points(bars, rules.swing_lookback)
    if len(lows) >= 2:
        (_, p1), (i2, p2) = lows[-2], lows[-1]
        if p2 > p1:
            candidates.append((p2, f"under the {fmt_day(bars[i2].day)} higher swing low", bars[i2].day))
    level, why, origin = max(candidates, key=lambda c: c[0])
    if level <= plan.stop + 1e-9 or level >= last:
        return None
    return level, why, origin, gain


def plan_line(p: Plan) -> str:
    bits = [p.ticker, p.account, p.status]
    if p.status == "open" and p.entry is not None:
        bits.append(f"{(p.shares or 0):g} sh @ {p.entry:.2f}")
    elif p.trigger:
        bits.append(trigger_label(p.trigger))
    bits.append(f"stop {p.stop:.2f}" if p.stop is not None else "stop NOT SET")
    if p.t1 is not None:
        bits.append(f"T1 {p.t1:.2f}" + (" (hit)" if p.t1_hit else ""))
    if p.out_by:
        bits.append(f"out by {fmt_day(p.out_by)}")
    return " | ".join(bits)


class PlanBook:
    """Reads and appends state/plans.jsonl. Nothing in it is ever edited."""

    def __init__(self, store: Store, cfg: Config):
        self.store = store
        self.cfg = cfg

    def entries(self) -> list[dict]:
        return self.store.read(PLANS)

    def plans(self) -> dict[str, Plan]:
        latest: dict[str, Plan] = {}
        for e in self.entries():
            if e.get("type") == "plan":
                latest[e["plan_id"]] = Plan.from_entry(e)
        return latest

    def history(self, plan_id: str) -> list[dict]:
        return [e for e in self.entries() if e.get("type") == "plan" and e.get("plan_id") == plan_id]

    def active(self, group: str | None = None) -> list[Plan]:
        out = [p for p in self.plans().values() if p.active and in_group(p.account, group)]
        return sorted(out, key=lambda p: (p.account, p.ticker))

    def find(self, ticker: str, account: str) -> Plan | None:
        t = ticker.upper()
        for p in self.plans().values():
            if p.active and p.ticker == t and p.account == account:
                return p
        return None

    def save(self, plan: Plan, reason: str, effective_at: str | None = None) -> Plan:
        """Append a new snapshot of the plan that references the one it replaces."""
        plan.reason = reason
        plan.supersedes = plan.entry_id
        plan.effective_at = effective_at or iso(now_utc())
        rec = self.store.append(PLANS, plan.to_record(), prefix="pe")
        plan.entry_id = rec["id"]
        return plan

    def record_trade(self, *, account: str, ticker: str, side: str, shares: float, price: float, currency: str, reported_time: str | None, note: str = "") -> dict:
        return self.store.append(
            PLANS,
            {
                "type": "trade",
                "account": account,
                "ticker": ticker.upper(),
                "side": side,
                "shares": shares,
                "price": price,
                "currency": currency,
                "reported_time": reported_time,
                "note": note,
            },
            prefix="tr",
        )

    def positions(self, group: str | None = "real") -> dict[tuple[str, str], dict]:
        """Shares and average cost per (account, ticker), replayed from reported trades."""
        pos: dict[tuple[str, str], dict] = {}
        for e in self.entries():
            if e.get("type") != "trade" or not in_group(e["account"], group):
                continue
            p = pos.setdefault((e["account"], e["ticker"]), {"shares": 0.0, "avg_cost": 0.0, "currency": e.get("currency"), "realized": 0.0})
            sh, px = float(e["shares"]), float(e["price"])
            if e["side"] == "buy":
                total = p["shares"] * p["avg_cost"] + sh * px
                p["shares"] += sh
                p["avg_cost"] = total / p["shares"] if p["shares"] else 0.0
            else:
                p["realized"] += (px - p["avg_cost"]) * sh
                p["shares"] -= sh
                if abs(p["shares"]) < 1e-9:
                    p["shares"] = 0.0
        return pos

    def write_holdings(self) -> None:
        """Regenerate state/holdings.yaml (a derived view) from the log."""
        plans = {(p.account, p.ticker): p for p in self.active("real")}
        rows = []
        for (acct, t), p in sorted(self.positions("real").items()):
            if p["shares"] <= 0:
                continue
            pl = plans.get((acct, t))
            rows.append(
                {
                    "ticker": t,
                    "account": acct,
                    "shares": p["shares"],
                    "avg_cost": round(p["avg_cost"], 4),
                    "currency": p["currency"],
                    "stop_close_below": pl.stop if pl else None,
                    "target_1": pl.t1 if pl else None,
                    "out_by": pl.out_by.isoformat() if pl and pl.out_by else None,
                }
            )
        header = "# Generated from state/plans.jsonl. Do not edit by hand;\n# report trades with `dh trade buy|sell ...` instead.\n"
        self.store.path(HOLDINGS).write_text(header + yaml.safe_dump({"swing_holdings": rows}, sort_keys=False), encoding="utf-8")

    def seed_from_config(self) -> int:
        """Import config.yaml swing_holdings once, when the log has no trades or plans yet."""
        if any(e.get("type") in ("trade", "plan") for e in self.entries()):
            return 0
        for h in self.cfg.swing_holdings:
            cur = infer_currency(h.ticker)
            self.record_trade(
                account=h.account,
                ticker=h.ticker,
                side="buy",
                shares=h.shares,
                price=h.avg_cost,
                currency=cur,
                reported_time=h.entry_date.isoformat() if h.entry_date else None,
                note="imported from config.yaml",
            )
            plan = Plan(
                plan_id=new_id("plan"),
                account=h.account,
                ticker=h.ticker,
                status="open",
                entry=h.avg_cost,
                stop=h.stop_close_below,
                t1=h.target_1,
                out_by=h.out_by,
                shares=h.shares,
                entry_date=h.entry_date,
                currency=cur,
            )
            self.save(plan, "imported from config.yaml")
        self.write_holdings()
        return len(self.cfg.swing_holdings)

    def config_drift(self) -> list[str]:
        in_cfg = {(h.account, h.ticker) for h in self.cfg.swing_holdings}
        in_log = {(p.account, p.ticker) for p in self.active("real") if p.status == "open"}
        return [
            f"config.yaml lists {t} ({a}) but the trade log has no open position. "
            f"config.yaml is only read by `dh init`; record it with `dh trade buy {t} ...`."
            for a, t in sorted(in_cfg - in_log)
        ]
