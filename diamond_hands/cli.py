"""`dh` command line. Every number Diamond Hands reports comes from here."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from . import DISCLAIMER, __version__
from .alerts import Alert, Dispatcher
from .analytics import Analytics, compute as compute_analytics, ext_hours_summary
from .config import Config, ConfigError, infer_currency, load_config
from .data import nasdaq, yahoo
from .data.fx import FxConverter, usdcad
from .data.http import DataError
from .indicators import pct
from .models import Quote, Series, is_final
from .paper import PaperBook
from .plans import PAPER, CardContext, Plan, PlanBook, plan_line, risk_card, sized_shares, trigger_label
from .store import Store, new_id
from .timeutil import fmt_day, fmt_time, local, now_utc, parse_date, parse_dt
from .watch import check_plan


class App:
    """Shared state for one command run. Prices come from Nasdaq first (US listings), then Yahoo."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.state_dir)
        self.plans = PlanBook(self.store, cfg)
        self.fx = FxConverter(cfg.base_currency)
        self.paper = PaperBook(self.store, cfg, self.plans, self.fx, fetch=self.series)
        self.dispatcher = Dispatcher(self.store, cfg.logs_dir, cfg.timezone)
        self._cache: dict[tuple, object] = {}

    def _first(self, key: tuple, symbol: str, from_nasdaq: Callable, from_yahoo: Callable):
        """Try Nasdaq (when it covers the symbol), then Yahoo. Caches the result or the combined failure."""
        if key not in self._cache:
            attempts = ([from_nasdaq] if nasdaq.covers(symbol) else []) + [from_yahoo]
            errors: list[DataError] = []
            result: object = None
            for fn in attempts:
                try:
                    result = fn()
                    break
                except DataError as e:
                    errors.append(e)
            if result is None:
                first, rest = errors[0], errors[1:]
                result = DataError(first.source, "; ".join([first.detail] + [f"fallback {e}" for e in rest]))
            self._cache[key] = result
        value = self._cache[key]
        if isinstance(value, DataError):
            raise value
        return value

    def daily(self, symbol: str) -> Series:
        s = symbol.upper()
        return self._first(("daily", s), s, lambda: nasdaq.fetch_daily(s), lambda: yahoo.fetch_chart(s, "2y", "1d"))

    def intraday(self, symbol: str) -> Series:
        s = symbol.upper()
        return self._first(("intraday", s), s, lambda: nasdaq.fetch_intraday(s), lambda: yahoo.fetch_chart(s, "5d", "5m"))

    def quote(self, symbol: str) -> Quote:
        s = symbol.upper()
        return self._first(("quote", s), s, lambda: nasdaq.fetch_quote(s), lambda: yahoo.fetch_quote(s))

    def ext_hours(self, symbol: str) -> tuple[str, str]:
        s = symbol.upper()
        return self._first(
            ("ext", s),
            s,
            lambda: (nasdaq.extended_hours(s), nasdaq.SOURCE),
            lambda: (ext_hours_summary(yahoo.fetch_chart(s, "5d", "5m", True)), yahoo.SOURCE),
        )

    def earnings(self, symbol: str) -> tuple[list[date] | None, str]:
        s = symbol.upper()

        def from_yahoo():
            cal = yahoo.fetch_calendar(s)
            return (cal.earnings or None), (cal.source if cal.earnings else f"{cal.source}: no upcoming earnings date")

        return self._first(("earnings", s), s, lambda: nasdaq.fetch_earnings(s), from_yahoo)

    def series(self, symbol: str, range_: str = "2y", interval: str = "1d", prepost: bool = False) -> Series:
        """Adapter for the paper book: daily bars, or the latest intraday prices."""
        return self.daily(symbol) if interval == "1d" else self.intraday(symbol)

    def today(self) -> date:
        return local(now_utc(), self.cfg.timezone).date()


def fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


# ---- risk card -------------------------------------------------------------


def card_context(app: App, plan: Plan, offline: bool) -> CardContext:
    cfg = app.cfg
    group = PAPER if plan.is_paper else "real"
    others = [p for p in app.plans.active(group) if p.plan_id != plan.plan_id]
    ideas = len(others) + (1 if plan.active else 0)
    equity = app.paper.equity_at_cost() if plan.is_paper else cfg.account_equity
    cur = (plan.currency or infer_currency(plan.ticker)).upper()

    fx: float | None = 1.0
    fx_note = None
    if cur != cfg.base_currency:
        if offline:
            fx, fx_note = None, "not fetched (offline)"
        else:
            try:
                fx, info = app.fx.rate(cur)
                if info:
                    fx_note = f"{info.source} USD/CAD {info.rate:.4f} ({fmt_day(info.day)})"
            except DataError as e:
                fx, fx_note = None, str(e)

    earnings, e_note = None, "not checked (offline)"
    if not offline:
        try:
            earnings, e_note = app.earnings(plan.ticker)
            e_note += f", fetched {fmt_time(now_utc(), cfg.timezone)}"
        except DataError as e:
            e_note = str(e)
    return CardContext(equity, cfg.base_currency, fx, fx_note, ideas, earnings, e_note, app.today())


def print_card(app: App, plan: Plan, offline: bool = False) -> None:
    text, _ = risk_card(plan, app.cfg.rules, card_context(app, plan, offline))
    print(text)
    print(DISCLAIMER)


def _apply_plan_args(plan: Plan, args) -> str | None:
    """Apply --stop/--t1/--out-by. Returns an error message if a stop would move down."""
    if getattr(args, "stop", None) is not None:
        if plan.stop is not None and args.stop < plan.stop and not getattr(args, "override", False):
            return (
                f"New stop {args.stop:.2f} is below the current {plan.stop:.2f}. Your rules say stops only move up. "
                "Re-run with --override to record it anyway."
            )
        plan.stop = args.stop
    if getattr(args, "t1", None) is not None:
        plan.t1 = args.t1
    if getattr(args, "out_by", None) is not None:
        plan.out_by = args.out_by
    return None


def _reported_date(value: str | None) -> date | None:
    try:
        return parse_date(value)
    except ValueError:
        return None  # free-form time like "10:32"; the raw text is still recorded


def _trigger(args) -> dict | None:
    if getattr(args, "limit", None) is not None:
        return {"type": "limit", "price": args.limit}
    if getattr(args, "close_above", None) is not None:
        return {"type": "close_above", "price": args.close_above}
    if getattr(args, "zone", None):
        lo, hi = sorted(args.zone)
        return {"type": "zone", "low": lo, "high": hi}
    return None


def _vs(price: float, level: float) -> str:
    return f"{pct(price, level):+.1f}%"


# ---- commands --------------------------------------------------------------


def cmd_init(app: App, args) -> int:
    cfg = app.cfg
    if not cfg.from_file:
        print(f"No config.yaml; using built-in defaults ({cfg.base_currency}, benchmark {cfg.paper_book.benchmark}, time zone {cfg.timezone}).")
    n = app.plans.seed_from_config()
    print(f"State folder: {app.store.dir}")
    if n:
        print(f"Imported {n} swing holding(s) from config.yaml.")
    if cfg.paper_book.enabled and app.paper.start():
        print(f"Paper book started with {cfg.paper_book.starting_capital:,.2f} {cfg.base_currency}.")
    for msg in app.plans.config_drift():
        print(msg)
    return 0


def cmd_trade(app: App, args) -> int:
    t, acct = args.ticker.upper(), args.account
    if acct == PAPER:
        return fail("Paper trades go through `dh paper buy|sell`.")
    cur = (args.currency or infer_currency(t)).upper()
    plan = app.plans.find(t, acct)

    if args.side == "buy":
        if plan is None:
            plan = Plan(plan_id=new_id("plan"), account=acct, ticker=t, currency=cur)
        err = _apply_plan_args(plan, args)
        if err:
            return fail(err + " Nothing was recorded.")
        rec = app.plans.record_trade(account=acct, ticker=t, side="buy", shares=args.shares, price=args.price, currency=cur, reported_time=args.time, note=args.note or "")
        pos = app.plans.positions("real")[(acct, t)]
        plan.status = "open"
        plan.entry = round(pos["avg_cost"], 4)
        plan.shares = pos["shares"]
        plan.currency = cur
        plan.entry_date = plan.entry_date or _reported_date(args.time) or app.today()
        app.plans.save(plan, f"buy {args.shares:g} @ {args.price} recorded ({rec['id']})")
        app.plans.write_holdings()
        print(f"Recorded: BUY {args.shares:g} {t} @ {args.price:.2f} {cur}, account {acct}" + (f", {args.time}" if args.time else "") + ".")
        print_card(app, plan, args.offline)
        return 0

    rec = app.plans.record_trade(account=acct, ticker=t, side="sell", shares=args.shares, price=args.price, currency=cur, reported_time=args.time, note=args.note or "")
    pos = app.plans.positions("real")[(acct, t)]
    notes = []
    if plan is None:
        notes.append(f"No active plan for {t} in account {acct}; the sale is recorded without one.")
    else:
        plan.shares = max(pos["shares"], 0)
        if pos["shares"] <= 0:
            plan.status = "closed"
        elif plan.t1 is not None and not plan.t1_hit and args.price >= plan.t1:
            plan.t1_hit = True
            if plan.entry is not None and (plan.stop is None or plan.stop < plan.entry):
                notes.append(
                    f"Sold at/above T1 {plan.t1:.2f}. Your after-T1 rule moves the stop to breakeven ({plan.entry:.2f}). "
                    f"Record it with: dh plan update {t} --stop {plan.entry:.2f} --reason 'T1 hit, stop to breakeven'"
                )
        app.plans.save(plan, f"sell {args.shares:g} @ {args.price} recorded ({rec['id']})")
    if pos["shares"] < 0:
        notes.append(f"Sold more shares than the log shows held ({pos['shares']:g} remaining).")
    app.plans.write_holdings()
    print(
        f"Recorded: SELL {args.shares:g} {t} @ {args.price:.2f} {cur}, account {acct}"
        + (f", {args.time}" if args.time else "")
        + f". Remaining {max(pos['shares'], 0):g} shares. Exits are never rule-checked."
    )
    for n in notes:
        print(n)
    return 0


def cmd_plan_new(app: App, args) -> int:
    t, acct = args.ticker.upper(), args.account
    if acct == PAPER:
        return fail("Paper ideas go through `dh paper buy`.")
    if app.plans.find(t, acct):
        return fail(f"{t} already has an active plan in account {acct}. Use `dh plan update`.")
    plan = Plan(
        plan_id=new_id("plan"),
        account=acct,
        ticker=t,
        status="pending",
        stop=args.stop,
        t1=args.t1,
        out_by=args.out_by,
        shares=args.shares,
        trigger=_trigger(args),
        currency=infer_currency(t),
    )
    app.plans.save(plan, args.reason or "new idea")
    print(f"Plan written for {t} ({acct}), pending until you report a buy.")
    print_card(app, plan, args.offline)
    return 0


def cmd_plan_update(app: App, args) -> int:
    t = args.ticker.upper()
    plan = app.plans.find(t, args.account)
    if plan is None:
        return fail(f"No active plan for {t} in account {args.account}.")
    before = (plan.stop, plan.t1, plan.out_by, plan.shares, plan.trigger)
    err = _apply_plan_args(plan, args)
    if err:
        return fail(err)
    if args.shares is not None and plan.status == "pending":
        plan.shares = args.shares
    trig = _trigger(args)
    if trig and plan.status == "pending":
        plan.trigger = trig
    after = (plan.stop, plan.t1, plan.out_by, plan.shares, plan.trigger)
    labels = ("stop", "T1", "out-by", "shares", "trigger")
    changes = [f"{lbl} {b} → {a}" for lbl, b, a in zip(labels, before, after) if b != a]
    if not changes:
        return fail("Nothing to change.")
    app.plans.save(plan, args.reason or "; ".join(changes))
    if not plan.is_paper:
        app.plans.write_holdings()
    print(f"{t} plan updated: {'; '.join(changes)}. Logged as a new entry; the previous one is kept.")
    print_card(app, plan, args.offline)
    return 0


def cmd_plan_drop(app: App, args) -> int:
    t = args.ticker.upper()
    plan = app.plans.find(t, args.account)
    if plan is None:
        return fail(f"No active plan for {t} in account {args.account}.")
    if plan.status == "open":
        return fail(f"{t} is an open position. Report the sale with `dh trade sell` and the plan closes itself.")
    plan.status = "closed"
    app.plans.save(plan, f"dropped: {args.reason}")
    print(f"{t} idea dropped ({args.reason}). Logged as a new entry.")
    return 0


def cmd_plan_show(app: App, args) -> int:
    plans = app.plans.active(None)
    if args.ticker:
        plans = [p for p in plans if p.ticker == args.ticker.upper()]
    if not plans:
        print("No active plans.")
    for p in plans:
        print(plan_line(p))
        if args.history:
            for e in reversed(app.plans.history(p.plan_id)):  # newest first
                when = fmt_time(parse_dt(e["ts"]), app.cfg.timezone)
                print(f"    {when}  {e.get('status')}  stop {e.get('stop')}  T1 {e.get('t1')}  out-by {e.get('out_by')}  ({e.get('reason')})")
    for msg in app.plans.config_drift():
        print(msg)
    return 0


def cmd_risk_card(app: App, args) -> int:
    t = args.ticker.upper()
    plan = app.plans.find(t, args.account) or (app.plans.find(t, PAPER) if args.account == "real" else None)
    if plan is None:
        return fail(f"No active plan for {t}. Write one with `dh plan new` or record a buy with `dh trade buy`.")
    print_card(app, plan, args.offline)
    return 0


def quote_lines(q: Quote, tz: str) -> list[str]:
    head = f"{q.symbol}" + (f" ({q.name})" if q.name else "") + f" {q.price:,.2f}"
    if q.change is not None:
        head += f", {q.change:+.2f}" + (f" ({q.change_pct:+.2f}%)" if q.change_pct is not None else "")
    out = [head]
    detail = []
    if q.prev_close is not None:
        detail.append(f"prior close {q.prev_close:,.2f}")
    if q.day_low is not None and q.day_high is not None:
        detail.append(f"day {q.day_low:,.2f}–{q.day_high:,.2f}")
    if q.volume is not None:
        detail.append(f"volume {q.volume:,.0f}")
    if q.low_52w is not None and q.high_52w is not None:
        detail.append(f"52-week {q.low_52w:,.2f}–{q.high_52w:,.2f}")
    if detail:
        out.append(" · ".join(detail))
    status = []
    if q.market_status:
        status.append(f"Market {q.market_status.lower()}")
    if q.last_trade:
        status.append(f"last trade {q.last_trade}" + (" (real-time)" if q.real_time else ""))
    if status:
        out.append(" · ".join(status))
    out += [f"Other session: {x}" for x in q.extra]
    out.append(f"Source: {q.source}, fetched {fmt_time(q.fetched_at, tz)}.")
    return out


def _held_lines(app: App, sym: str, price: float) -> list[str]:
    out = []
    plans = {(p.account, p.ticker): p for p in app.plans.active("real")}
    for (acct, t), pos in sorted(app.plans.positions("real").items()):
        if t != sym or pos["shares"] <= 0:
            continue
        line = f"You hold {pos['shares']:g} sh @ {pos['avg_cost']:.2f} ({acct}): {pct(pos['avg_cost'], price):+.1f}% vs your cost"
        plan = plans.get((acct, t))
        if plan and plan.stop is not None:
            line += f" · stop {plan.stop:.2f} ({_vs(price, plan.stop)})"
        if plan and plan.t1 is not None:
            line += f" · T1 {plan.t1:.2f} ({_vs(price, plan.t1)})" + (" hit" if plan.t1_hit else "")
        out.append(line)
    pp = app.paper.state().positions.get(sym)
    if pp:
        out.append(f"Paper book holds {pp.shares:g} sh @ {pp.avg_local:.2f}: {pct(pp.avg_local, price):+.1f}% vs paper cost")
    return out


def cmd_quote(app: App, args) -> int:
    for i, sym in enumerate(dict.fromkeys(s.upper() for s in args.tickers)):
        if i:
            print()
        try:
            q = app.quote(sym)
        except DataError as e:
            print(f"[{sym}] {e}")
            continue
        for line in quote_lines(q, app.cfg.timezone) + _held_lines(app, sym, q.price):
            print(line)
    print(DISCLAIMER)
    return 0


def cmd_holdings(app: App, args) -> int:
    if args.paper:
        print(app.paper.scorecard())
        return 0
    cfg = app.cfg
    positions = {k: v for k, v in app.plans.positions("real").items() if v["shares"] > 0}
    pending = [p for p in app.plans.active("real") if p.status == "pending"]
    if not positions and not pending:
        print("No holdings recorded yet. Tell Claude what you bought, or run: dh trade buy TICKER --shares N --price P")
        return 0

    plans = {(p.account, p.ticker): p for p in app.plans.active("real")}
    totals: dict[str, dict[str, float]] = {}
    gaps, sources = [], set()
    print(f"Holdings, {fmt_time(now_utc(), cfg.timezone)}")
    for (acct, t), pos in sorted(positions.items()):
        cur = pos["currency"] or infer_currency(t)
        shares, avg = pos["shares"], pos["avg_cost"]
        parts = [f"{t} ({acct}) {shares:g} sh @ {avg:.2f} {cur}"]
        try:
            q = app.quote(t)
            sources.add(q.source)
            value, cost = shares * q.price, shares * avg
            today = f", {q.change_pct:+.2f}% today" if q.change_pct is not None else ""
            parts += [f"last {q.price:,.2f}{today}", f"value {value:,.2f}", f"P&L {value - cost:+,.2f} ({pct(cost, value):+.1f}%)"]
            plan = plans.get((acct, t))
            if plan and plan.stop is not None:
                parts.append(f"stop {plan.stop:.2f} ({_vs(q.price, plan.stop)})")
            if plan and plan.t1 is not None:
                parts.append(f"T1 {plan.t1:.2f} ({_vs(q.price, plan.t1)})" + (" hit" if plan.t1_hit else ""))
            if plan and plan.out_by:
                parts.append(f"out by {fmt_day(plan.out_by)}")
            tot = totals.setdefault(cur, {"value": 0.0, "cost": 0.0})
            tot["value"] += value
            tot["cost"] += cost
        except DataError as e:
            parts.append("no price")
            gaps.append(f"[{t}] {e}")
        print(" | ".join(parts))

    for cur, tot in sorted(totals.items()):
        line = f"Total {cur}: value {tot['value']:,.2f} | P&L {tot['value'] - tot['cost']:+,.2f} ({pct(tot['cost'], tot['value']):+.1f}%)"
        if cur != cfg.base_currency:
            try:
                rate, info = app.fx.rate(cur)
                line += f" = {tot['value'] * rate:,.2f} {cfg.base_currency} ({info.source} USD/CAD {info.rate:.4f}, {fmt_day(info.day)})"
            except DataError as e:
                line += f" ({cfg.base_currency} value not shown: {e})"
        print(line)

    for p in pending:
        line = f"Idea {p.ticker} ({p.account}): {trigger_label(p.trigger)}"
        level = p.planned_entry()
        try:
            q = app.quote(p.ticker)
            sources.add(q.source)
            line += f" | last {q.price:,.2f}" + (f" ({pct(q.price, level):+.1f}% to the trigger)" if level else "")
        except DataError as e:
            gaps.append(f"[{p.ticker}] {e}")
        print(line)

    for g in gaps:
        print(g)
    if sources:
        print(f"Source: {', '.join(sorted(sources))} quotes. Each quote's own last-trade time: dh quote TICKER.")
    print(DISCLAIMER)
    return 0


def cmd_watch(app: App, args) -> int:
    now = now_utc()
    status, alerts, gaps, used = [], [], [], set()
    for p in app.plans.active("real"):
        try:
            s = app.daily(p.ticker)
        except DataError as e:
            gaps.append(f"[{p.ticker}] {e}")
            continue
        used.add(s.source)
        line, al = check_plan(app.cfg, p, s, now)
        status.append(line)
        alerts += al
    paper = app.paper.process(now) if app.cfg.paper_book.enabled else []
    new = app.dispatcher.send(alerts, dry_run=args.dry_run)

    if args.json:
        print(json.dumps({"status": status, "new_alerts": [a.text() for a in new], "gaps": gaps, "paper": paper}, indent=2))
        return 0
    if not args.quiet:
        if not status and not gaps:
            print("No active plans in a real account. Record trades with `dh trade` or ideas with `dh plan new`.")
        for line in status:
            print(line)
        if status:
            print(f"Source: {', '.join(sorted(used))}, checked {fmt_time(now, app.cfg.timezone)}.")
    for a in new:
        print("\n" + a.text())
    for g in gaps:
        print(g)
    for m in paper:
        print(m)
    return 0


def _analytics_symbols(app: App) -> list[str]:
    seen: dict[str, None] = {}
    for p in app.plans.active(None):
        seen.setdefault(p.ticker)
    for t in app.cfg.watchlist:
        seen.setdefault(t)
    return list(seen)


def _run_analytics(app: App, sym: str, now: datetime) -> tuple[Analytics | None, list[str]]:
    gaps: list[str] = []
    try:
        daily = app.daily(sym)
    except DataError as e:
        return None, [f"[{sym}] {e}"]
    benches = {}
    for b in dict.fromkeys(filter(None, [app.cfg.index_for(sym), app.cfg.sector_index.get(sym)])):
        if b == sym:
            continue
        try:
            benches[b] = app.daily(b)
        except DataError as e:
            gaps.append(f"[{sym}] relative strength vs {b} skipped: {e}")
    ext = ext_src = None
    try:
        ext, ext_src = app.ext_hours(sym)
    except DataError as e:
        gaps.append(f"[{sym}] extended-hours data skipped: {e}")
    try:
        return compute_analytics(app.cfg, daily, now, benches, ext, ext_src), gaps
    except ValueError as e:
        return None, gaps + [f"[{sym}] {e}"]


def cmd_analytics(app: App, args) -> int:
    now = now_utc()
    symbols = [s.upper() for s in args.tickers] or _analytics_symbols(app)
    if not symbols:
        return fail("No tickers given, and there are no plans or watchlist names. Try: dh analytics NVDA")
    results, gaps = [], []
    for sym in symbols:
        a, g = _run_analytics(app, sym, now)
        gaps += g
        if a:
            results.append(a)
    if args.json:
        print(json.dumps({"analytics": [a.to_dict() for a in results], "gaps": gaps}, indent=2, default=str))
        return 0
    for a in results:
        print("\n".join(a.lines()))
        print()
    for g in gaps:
        print(g)
    return 0


def _cross_check(sym: str, primary: Series, now: datetime) -> tuple[str | None, str | None]:
    """Compare the primary source's last official close with the other source's.

    Returns (gap message, skip reason); both None when the closes agree within 0.1%.
    """
    final = [b for b in primary.bars if is_final(primary, b, now)]
    if not final:
        return None, None
    pb = final[-1]
    try:
        if primary.source == nasdaq.SOURCE:
            other = yahoo.fetch_chart(sym, "1mo", "1d")
        elif nasdaq.covers(sym):
            other = nasdaq.fetch_daily(sym)
        else:
            return None, None
    except DataError as e:
        return None, str(e)
    ob = next((b for b in reversed(other.bars) if b.day == pb.day and not b.provisional), None)
    if ob is None:
        return None, f"{other.source} has no {fmt_day(pb.day)} close yet"
    gap = pct(pb.close, ob.close)
    if abs(gap) >= 0.1:
        return (
            f"[{sym}] Price gap on {fmt_day(pb.day)}: {primary.source} close {pb.close:.2f} vs {other.source} {ob.close:.2f} "
            f"({gap:+.2f}%). Using {primary.source} (primary).",
            None,
        )
    return None, None


def cmd_close_check(app: App, args) -> int:
    now = now_utc()
    status, alerts, gaps, checks = [], [], [], []
    skipped: dict[str, list[str]] = {}
    for p in app.plans.active("real"):
        try:
            s = app.daily(p.ticker)
        except DataError as e:
            gaps.append(f"[{p.ticker}] {e}")
            continue
        line, al = check_plan(app.cfg, p, s, now)
        status.append(line)
        alerts += al
        if p.status == "open":
            msg, skip = _cross_check(p.ticker, s, now)
            if msg:
                checks.append(msg)
            if skip:
                skipped.setdefault(skip, []).append(p.ticker)
    for reason, tickers in skipped.items():
        checks.append(f"Price cross-check skipped for {', '.join(tickers)}: {reason}")

    flagged, open_sessions = 0, []
    symbols = _analytics_symbols(app)
    for sym in symbols:
        a, g = _run_analytics(app, sym, now)
        gaps += g
        if a is None:
            continue
        if not a.final:
            open_sessions.append(sym)
        for f in a.flags:
            flagged += 1
            alerts.append(Alert(sym, f["rule"], f["level"], a.as_of, f["text"], [], a.sources[:1]))

    paper = []
    if app.cfg.paper_book.enabled and app.paper.state().started:
        paper += app.paper.process(now)
        paper.append(app.paper.snapshot(now))
        paper.append(app.paper.scorecard(now))

    new = app.dispatcher.send(alerts, dry_run=args.dry_run)
    if args.json:
        print(json.dumps({"status": status, "new_alerts": [a.text() for a in new], "checks": checks, "gaps": gaps, "paper": paper}, indent=2))
        return 0
    print(f"After-close check, {fmt_time(now, app.cfg.timezone)}")
    for line in status:
        print(line)
    print(f"Analytics run on {len(symbols)} symbol(s); {flagged} rule match(es).")
    if open_sessions:
        print(f"No official close yet for: {', '.join(open_sessions)}. Close-based checks wait for it.")
    for c in checks:
        print(c)
    for a in new:
        print("\n" + a.text())
    for g in gaps:
        print(g)
    for m in paper:
        print("\n" + m if "\n" in m else m)
    return 0


def cmd_paper(app: App, args) -> int:
    if not app.cfg.paper_book.enabled:
        return fail("paper_book.enabled is false in config.yaml.")
    pb = app.paper
    pb.start()
    cmd = args.paper_cmd

    if cmd == "buy":
        t = args.ticker.upper()
        kind, price = ("limit", args.limit) if args.limit is not None else ("close_above", args.close_above) if args.close_above is not None else ("moc", None)
        shares = args.shares
        if shares is None:
            entry = price
            if entry is None:
                try:
                    entry = app.quote(t).price
                except DataError as e:
                    return fail(f"{e}. Pass --shares.")
            try:
                fx, _ = app.fx.rate(infer_currency(t))
            except DataError as e:
                return fail(f"{e}. Pass --shares.")
            shares = sized_shares(entry, args.stop, app.cfg.rules, pb.equity_at_cost(), fx)
            if not shares:
                return fail("Could not size from your rules (the stop must be below entry). Pass --shares.")
        order, plan = pb.place_buy(t, kind, price, shares, args.stop, args.t1, args.out_by)
        print(f"Paper order {order['order_id']}: BUY {shares:g} {t}, {trigger_label(plan.trigger)}. Fills only on real prices.")
        print_card(app, plan, args.offline)
        return 0

    if cmd == "sell":
        kind, price = ("limit", args.limit) if args.limit is not None else ("moc", None)
        try:
            order = pb.place_sell(args.ticker, kind, price, args.shares, args.all)
        except ValueError as e:
            return fail(str(e))
        qty = "all" if args.all else f"{args.shares:g}"
        print(f"Paper order {order['order_id']}: SELL {qty} {args.ticker.upper()} {kind}" + (f" {price:.2f}" if price is not None else "") + ". Exits are never blocked.")
        return 0

    if cmd == "cancel":
        if not pb.cancel(args.order_id):
            return fail(f"No open paper order {args.order_id}.")
        print(f"Cancelled {args.order_id}.")
        return 0

    if cmd == "process":
        now = now_utc()
        msgs = pb.process(now)
        for m in msgs or ["No paper fills."]:
            print(m)
        if args.eod:
            print(pb.snapshot(now))
        return 0

    if cmd == "scorecard":
        print(pb.scorecard())
        return 0

    if cmd == "ledger":
        for line in pb.ledger_lines(args.limit) or ["Ledger is empty."]:
            print(line)
        return 0
    return fail(f"unknown paper command {cmd}")


def cmd_fx(app: App, args) -> int:
    try:
        fx = usdcad()
    except DataError as e:
        return fail(str(e))
    print(f"USD/CAD {fx.rate:.4f} ({fmt_day(fx.day)}), {fx.source}, fetched {fmt_time(fx.fetched_at, app.cfg.timezone)}.")
    return 0


# ---- parser ----------------------------------------------------------------


def _plan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--stop", type=float, help="close-based stop")
    p.add_argument("--t1", type=float, help="first target (sell half)")
    p.add_argument("--out-by", type=date.fromisoformat, help="YYYY-MM-DD")


def _trigger_args(p: argparse.ArgumentParser, required: bool) -> None:
    g = p.add_mutually_exclusive_group(required=required)
    g.add_argument("--limit", type=float, help="buy limit price")
    g.add_argument("--close-above", type=float, help="buy on the first close above this price")
    g.add_argument("--zone", type=float, nargs=2, metavar=("LOW", "HIGH"), help="buy zone")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dh", description="Diamond Hands: facts-first swing-trading partner.")
    p.add_argument("--config", type=Path, default=None, help="path to config.yaml (optional; default: repo root)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create state/, import config.yaml swing holdings once, start the paper book")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("quote", help="current price of any ticker, plus your holding if you have one")
    s.add_argument("tickers", nargs="+")
    s.set_defaults(func=cmd_quote)

    s = sub.add_parser("holdings", help="what you hold, priced now, with P&L and distance to stop and T1")
    s.add_argument("--paper", action="store_true", help="show the paper book instead")
    s.set_defaults(func=cmd_holdings)

    s = sub.add_parser("trade", help="record a trade you made in a real account")
    s.add_argument("side", choices=["buy", "sell"])
    s.add_argument("ticker")
    s.add_argument("--shares", type=float, required=True)
    s.add_argument("--price", type=float, required=True)
    s.add_argument("--account", default="real", help="account label, e.g. IRA or TFSA (default: real)")
    s.add_argument("--time", help="when you traded, as reported (e.g. '2026-11-14 10:32')")
    s.add_argument("--currency", help="override the currency inferred from the ticker")
    s.add_argument("--note")
    _plan_args(s)
    s.add_argument("--override", action="store_true", help="allow lowering an existing stop")
    s.add_argument("--offline", action="store_true", help="skip FX and earnings lookups on the risk card")
    s.set_defaults(func=cmd_trade)

    plan = sub.add_parser("plan", help="trade plans")
    psub = plan.add_subparsers(dest="plan_cmd", required=True)

    s = psub.add_parser("new", help="write a plan for a new idea (pending until you buy)")
    s.add_argument("ticker")
    _trigger_args(s, required=True)
    _plan_args(s)
    s.add_argument("--shares", type=float)
    s.add_argument("--account", default="real")
    s.add_argument("--reason")
    s.add_argument("--offline", action="store_true")
    s.set_defaults(func=cmd_plan_new)

    s = psub.add_parser("update", help="change stop / T1 / out-by (logged as a new entry)")
    s.add_argument("ticker")
    _plan_args(s)
    _trigger_args(s, required=False)
    s.add_argument("--shares", type=float, help="planned shares (pending ideas only)")
    s.add_argument("--account", default="real")
    s.add_argument("--reason")
    s.add_argument("--override", action="store_true", help="allow lowering a stop")
    s.add_argument("--offline", action="store_true")
    s.set_defaults(func=cmd_plan_update)

    s = psub.add_parser("drop", help="drop a pending idea")
    s.add_argument("ticker")
    s.add_argument("--account", default="real")
    s.add_argument("--reason", required=True)
    s.set_defaults(func=cmd_plan_drop)

    s = psub.add_parser("show", help="list active plans")
    s.add_argument("ticker", nargs="?")
    s.add_argument("--history", action="store_true", help="show every entry, newest first")
    s.set_defaults(func=cmd_plan_show)

    s = sub.add_parser("risk-card", help="print the risk card for a plan")
    s.add_argument("ticker")
    s.add_argument("--account", default="real")
    s.add_argument("--offline", action="store_true")
    s.set_defaults(func=cmd_risk_card)

    s = sub.add_parser("watch", help="price/level watch for real plans; processes paper fills")
    s.add_argument("--json", action="store_true")
    s.add_argument("--quiet", action="store_true", help="print only new alerts, data gaps and paper fills")
    s.add_argument("--dry-run", action="store_true", help="don't mark alerts as sent")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("analytics", help="volume, candles, trend and levels")
    s.add_argument("tickers", nargs="*")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_analytics)

    s = sub.add_parser("close-check", help="after-close: close-based stops, analytics flags, trailing stops, paper scorecard")
    s.add_argument("--json", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_close_check)

    paper = sub.add_parser("paper", help="paper book")
    ppsub = paper.add_subparsers(dest="paper_cmd", required=True)

    s = ppsub.add_parser("buy", help="place a paper buy with its plan")
    s.add_argument("ticker")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--limit", type=float)
    g.add_argument("--close-above", type=float)
    g.add_argument("--moc", action="store_true", help="buy at the next official close")
    s.add_argument("--shares", type=float, help="default: size to your risk and position rules")
    s.add_argument("--stop", type=float, required=True)
    s.add_argument("--t1", type=float)
    s.add_argument("--out-by", type=date.fromisoformat)
    s.add_argument("--offline", action="store_true")
    s.set_defaults(func=cmd_paper)

    s = ppsub.add_parser("sell", help="place a paper sell")
    s.add_argument("ticker")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--limit", type=float)
    g.add_argument("--moc", action="store_true")
    q = s.add_mutually_exclusive_group(required=True)
    q.add_argument("--shares", type=float)
    q.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_paper)

    s = ppsub.add_parser("cancel", help="cancel an open paper order")
    s.add_argument("order_id")
    s.set_defaults(func=cmd_paper)

    s = ppsub.add_parser("process", help="check open orders, stops and targets against real prices")
    s.add_argument("--eod", action="store_true", help="also record today's equity")
    s.set_defaults(func=cmd_paper)

    s = ppsub.add_parser("scorecard", help="equity, cash, positions, P&L vs benchmark")
    s.set_defaults(func=cmd_paper)

    s = ppsub.add_parser("ledger", help="ledger entries, newest first")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_paper)

    s = sub.add_parser("fx", help="latest Bank of Canada USD/CAD rate")
    s.set_defaults(func=cmd_fx)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    return args.func(App(cfg), args) or 0


if __name__ == "__main__":
    sys.exit(main())
