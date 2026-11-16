"""`dh` command line. Every number Diamond Hands reports comes from here."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from . import DISCLAIMER, __version__
from .alerts import Alert, Dispatcher
from .analytics import Analytics, compute as compute_analytics
from .config import Config, ConfigError, infer_currency, load_config
from .data import stooq, yahoo
from .data.fx import FxConverter, usdcad
from .data.http import DataError
from .indicators import pct
from .models import Series, is_final
from .paper import PaperBook
from .plans import PAPER, CardContext, Plan, PlanBook, plan_line, risk_card, sized_shares, trigger_label
from .store import Store, new_id
from .timeutil import fmt_day, fmt_time, local, now_utc, parse_date, parse_dt
from .watch import check_plan


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.state_dir)
        self.plans = PlanBook(self.store, cfg)
        self.fx = FxConverter(cfg.base_currency)
        self.paper = PaperBook(self.store, cfg, self.plans, self.fx, fetch=self.series)
        self.dispatcher = Dispatcher(self.store, cfg.logs_dir, cfg.timezone)
        self._cache: dict[tuple, Series | DataError] = {}

    def series(self, symbol: str, range_: str = "2y", interval: str = "1d", prepost: bool = False) -> Series:
        """Yahoo first; Stooq as backup for daily US bars. Results (and failures) are cached per run."""
        key = (symbol.upper(), range_, interval, prepost)
        if key not in self._cache:
            try:
                self._cache[key] = yahoo.fetch_chart(symbol, range_, interval, prepost)
            except DataError as e:
                result: Series | DataError = e
                if interval == "1d" and stooq.covers(symbol):
                    try:
                        result = stooq.fetch_daily(symbol)
                    except DataError as e2:
                        result = DataError(e.source, f"{e.detail}; backup {e2}")
                self._cache[key] = result
        v = self._cache[key]
        if isinstance(v, DataError):
            raise v
        return v

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
            cal = yahoo.fetch_calendar(plan.ticker)
            earnings, e_note = cal.earnings, f"{cal.source} {fmt_time(cal.fetched_at, cfg.timezone)}"
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


# ---- commands --------------------------------------------------------------


def cmd_init(app: App, args) -> int:
    n = app.plans.seed_from_config()
    print(f"State folder: {app.store.dir}")
    print(f"Imported {n} swing holding(s) from config.yaml." if n else "Trade log already has entries; config.yaml swing_holdings not re-imported.")
    if app.cfg.paper_book.enabled and app.paper.start():
        print(f"Paper book started with {app.cfg.paper_book.starting_capital:,.2f} {app.cfg.base_currency}.")
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
    pos = app.plans.positions("real").get((acct, t), {"shares": -args.shares})
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


def cmd_watch(app: App, args) -> int:
    now = now_utc()
    status, alerts, gaps = [], [], []
    for p in app.plans.active("real"):
        try:
            s = app.series(p.ticker)
        except DataError as e:
            gaps.append(f"[{p.ticker}] {e}")
            continue
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
            print("No active real-account plans. Record trades with `dh trade` or ideas with `dh plan new`.")
        for line in status:
            print(line)
        if status:
            print(f"Source: Yahoo Finance, checked {fmt_time(now, app.cfg.timezone)}.")
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
        daily = app.series(sym)
    except DataError as e:
        return None, [f"[{sym}] {e}"]
    benches = {}
    for b in dict.fromkeys(filter(None, [app.cfg.index_for(sym), app.cfg.sector_index.get(sym)])):
        if b == sym:
            continue
        try:
            benches[b] = app.series(b)
        except DataError as e:
            gaps.append(f"[{sym}] relative strength vs {b} skipped: {e}")
    try:
        intra = app.series(sym, "5d", "5m", True)
    except DataError as e:
        intra = None
        gaps.append(f"[{sym}] extended-hours volume skipped: {e}")
    try:
        return compute_analytics(app.cfg, daily, now, benches, intra), gaps
    except ValueError as e:
        return None, gaps + [f"[{sym}] {e}"]


def cmd_analytics(app: App, args) -> int:
    now = now_utc()
    symbols = [s.upper() for s in args.tickers] or _analytics_symbols(app)
    if not symbols:
        return fail("No tickers given and none in plans or watchlist.")
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


def _cross_check(sym: str, ys: Series, now: datetime) -> str | None:
    """Compare Yahoo's last final close with Stooq's; report gaps of 0.1% or more."""
    if ys.source != yahoo.SOURCE or not stooq.covers(sym):
        return None
    final = [b for b in ys.bars if is_final(ys, b, now)]
    if not final:
        return None
    yb = final[-1]
    try:
        ss = stooq.fetch_daily(sym)
    except DataError as e:
        return f"[{sym}] price cross-check skipped: {e}"
    sb = next((b for b in reversed(ss.bars) if b.day == yb.day), None)
    if sb is None:
        return f"[{sym}] price cross-check: Stooq has no {fmt_day(yb.day)} bar yet."
    gap = pct(yb.close, sb.close)
    if abs(gap) >= 0.1:
        return f"[{sym}] Price gap on {fmt_day(yb.day)}: Yahoo close {yb.close:.2f} vs Stooq {sb.close:.2f} ({gap:+.2f}%). Using Yahoo (primary)."
    return None


def cmd_close_check(app: App, args) -> int:
    now = now_utc()
    status, alerts, gaps, checks = [], [], [], []
    for p in app.plans.active("real"):
        try:
            s = app.series(p.ticker)
        except DataError as e:
            gaps.append(f"[{p.ticker}] {e}")
            continue
        line, al = check_plan(app.cfg, p, s, now)
        status.append(line)
        alerts += al
        if p.status == "open":
            x = _cross_check(p.ticker, s, now)
            if x:
                checks.append(x)

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
        print(f"Session not closed yet for: {', '.join(open_sessions)}. Close-based checks wait for the official close.")
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
                    entry = app.series(t).bars[-1].close
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
    p.add_argument("--config", type=Path, default=None, help="path to config.yaml (default: repo root)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create state/, import config swing holdings once, start the paper book")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("trade", help="record a trade you made in a real account")
    s.add_argument("side", choices=["buy", "sell"])
    s.add_argument("ticker")
    s.add_argument("--shares", type=float, required=True)
    s.add_argument("--price", type=float, required=True)
    s.add_argument("--account", default="real", help="account label, e.g. TFSA (default: real)")
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
