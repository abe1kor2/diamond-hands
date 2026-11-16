"""Paper book: a virtual account that fills only on real prices.

Fill rules:
  limit orders   fill when price trades through the level (buy: low < limit), at the limit or a better open
  close triggers buy-on-close-above and stops use the official close
  targets (T1)   fill on an intraday touch, at T1 or a better open
Stops and T1 apply only to prices after the plan snapshot that set them (`effective_at`),
so raising a stop never triggers on old bars. USD fills convert at the Bank of Canada rate
for the fill date. Paper and real accounts never mix.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import DISCLAIMER
from .config import Config, infer_currency
from .data.fx import FxConverter
from .data.http import DataError
from .data.yahoo import fetch_chart
from .indicators import pct
from .models import Bar, Series, is_final, session_close
from .plans import PAPER, Plan, PlanBook
from .store import Store, new_id
from .timeutil import fmt_day, fmt_time, iso, now_utc, parse_dt

LEDGER = "ledger.jsonl"
MAX_CHAINED_EVENTS = 20


@dataclass
class Order:
    order_id: str
    ticker: str
    side: str  # buy | sell
    kind: str  # limit | close_above | moc
    price: float | None
    shares: float | None
    placed_at: datetime
    plan_id: str | None
    currency: str
    sell_all: bool = False


@dataclass
class Position:
    ticker: str
    currency: str
    shares: float = 0.0
    cost_base: float = 0.0
    cost_local: float = 0.0

    @property
    def avg_local(self) -> float:
        return self.cost_local / self.shares if self.shares else 0.0


@dataclass
class PaperState:
    started: dict | None = None
    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    realized: float = 0.0
    equity_dates: set[str] = field(default_factory=set)


@dataclass
class Event:
    at: datetime
    reason: str  # limit | close_above | moc | stop | t1
    side: str
    shares: float | None
    price: float
    day: date
    basis: str
    order: Order | None = None


@dataclass
class Valuation:
    as_of: date | None
    equity: float
    positions_value: float
    rows: list[dict]
    prices: dict[str, float]
    fx_rate: float | None
    fx_note: str | None
    gaps: list[str]
    bench_close: float | None
    bench_return: float | None
    bench_since: date | None


def _intraday_by_day(intra: Series | None) -> dict[date, list[Bar]]:
    out: dict[date, list[Bar]] = {}
    if intra:
        for b in intra.bars:
            if b.session == "regular":
                out.setdefault(b.day, []).append(b)
    return out


def _first_touch(daily: Series, intra: dict[date, list[Bar]], start: datetime, cond: Callable[[float, float], bool], fill: Callable[[float], float]):
    """First bar after `start` whose (high, low) meets cond. Uses 5-minute bars where available."""
    start_day = start.astimezone(ZoneInfo(daily.exchange_tz)).date()
    for b in daily.bars:
        if b.day < start_day:
            continue
        ibars = intra.get(b.day)
        if ibars is not None:
            for x in ibars:
                if x.ts > start and cond(x.high, x.low):
                    return x.ts, fill(x.open), b.day, "5-min bar"
            continue
        if b.day == start_day:
            continue  # without intraday bars we can't tell what traded after `start`
        if cond(b.high, b.low):
            return b.ts, fill(b.open), b.day, "daily bar"
    return None


def _first_close(daily: Series, start: datetime, cond: Callable[[float], bool], now: datetime):
    """First official close after `start` that meets cond."""
    for b in daily.bars:
        close_at = session_close(b.day, daily.exchange_tz)
        if close_at <= start:
            continue
        if not is_final(daily, b, now):
            break
        if cond(b.close):
            return close_at, b.close, b.day, "official close"
    return None


class PaperBook:
    def __init__(self, store: Store, cfg: Config, plans: PlanBook, fx: FxConverter, fetch=fetch_chart, now_fn=now_utc):
        self.store = store
        self.cfg = cfg
        self.plans = plans
        self.fx = fx
        self.fetch = fetch
        self.now_fn = now_fn

    # ---- state -------------------------------------------------------------

    def entries(self) -> list[dict]:
        return self.store.read(LEDGER)

    def state(self) -> PaperState:
        st = PaperState()
        for e in self.entries():
            kind = e.get("type")
            if kind == "start":
                st.started = e
                st.cash = float(e["capital"])
            elif kind == "order":
                st.orders[e["order_id"]] = Order(
                    order_id=e["order_id"],
                    ticker=e["ticker"],
                    side=e["side"],
                    kind=e["kind"],
                    price=e.get("price"),
                    shares=e.get("shares"),
                    placed_at=parse_dt(e["placed_at"]),
                    plan_id=e.get("plan_id"),
                    currency=e.get("currency") or infer_currency(e["ticker"]),
                    sell_all=bool(e.get("sell_all")),
                )
            elif kind in ("cancel", "reject"):
                st.orders.pop(e.get("order_id"), None)
            elif kind == "fill":
                if e.get("order_id"):
                    st.orders.pop(e["order_id"], None)
                self._apply_fill(st, e)
            elif kind == "equity":
                st.equity_dates.add(e["date"])
        return st

    @staticmethod
    def _apply_fill(st: PaperState, e: dict) -> None:
        t, sh, amt = e["ticker"], float(e["shares"]), float(e["amount_base"])
        pos = st.positions.get(t) or Position(t, e["currency"])
        if e["side"] == "buy":
            st.cash -= amt
            pos.shares += sh
            pos.cost_base += amt
            pos.cost_local += sh * float(e["price"])
            st.positions[t] = pos
            return
        if pos.shares <= 0:
            return
        frac = min(sh / pos.shares, 1.0)
        cost_b, cost_l = pos.cost_base * frac, pos.cost_local * frac
        st.realized += amt - cost_b
        st.cash += amt
        pos.shares -= sh
        pos.cost_base -= cost_b
        pos.cost_local -= cost_l
        if pos.shares <= 1e-9:
            st.positions.pop(t, None)
        else:
            st.positions[t] = pos

    def start(self) -> bool:
        if self.state().started:
            return False
        pb = self.cfg.paper_book
        self.store.append(LEDGER, {"type": "start", "capital": pb.starting_capital, "currency": self.cfg.base_currency, "benchmark": pb.benchmark}, prefix="pl")
        return True

    def equity_at_cost(self) -> float:
        st = self.state()
        return st.cash + sum(p.cost_base for p in st.positions.values())

    # ---- orders ------------------------------------------------------------

    def place_buy(self, ticker: str, kind: str, price: float | None, shares: float, stop: float | None, t1: float | None, out_by: date | None) -> tuple[dict, Plan]:
        now = self.now_fn()
        t = ticker.upper()
        cur = infer_currency(t)
        trig = {"type": kind, "price": price} if kind != "moc" else {"type": "moc"}
        plan = self.plans.find(t, PAPER)
        if plan is None:
            plan = Plan(plan_id=new_id("plan"), account=PAPER, ticker=t, status="pending", stop=stop, t1=t1, out_by=out_by, shares=shares, trigger=trig, currency=cur)
            reason = "new paper idea"
        else:
            plan.trigger = trig
            plan.stop = stop if stop is not None else plan.stop
            plan.t1 = t1 if t1 is not None else plan.t1
            plan.out_by = out_by or plan.out_by
            reason = "paper add-on order"
        self.plans.save(plan, reason, effective_at=iso(now))
        order = self.store.append(
            LEDGER,
            {
                "type": "order",
                "order_id": new_id("ord"),
                "ticker": t,
                "side": "buy",
                "kind": kind,
                "price": price,
                "shares": shares,
                "placed_at": iso(now),
                "plan_id": plan.plan_id,
                "currency": cur,
            },
            prefix="pl",
        )
        return order, plan

    def place_sell(self, ticker: str, kind: str, price: float | None, shares: float | None, sell_all: bool) -> dict:
        t = ticker.upper()
        pos = self.state().positions.get(t)
        if pos is None:
            raise ValueError(f"no paper position in {t}")
        return self.store.append(
            LEDGER,
            {
                "type": "order",
                "order_id": new_id("ord"),
                "ticker": t,
                "side": "sell",
                "kind": kind,
                "price": price,
                "shares": None if sell_all else shares,
                "sell_all": sell_all,
                "placed_at": iso(self.now_fn()),
                "currency": pos.currency,
            },
            prefix="pl",
        )

    def cancel(self, order_id: str) -> bool:
        st = self.state()
        o = st.orders.get(order_id)
        if o is None:
            return False
        self.store.append(LEDGER, {"type": "cancel", "order_id": order_id}, prefix="pl")
        if o.side == "buy":
            plan = self.plans.find(o.ticker, PAPER)
            if plan and plan.status == "pending":
                plan.status = "closed"
                self.plans.save(plan, f"paper order {order_id} cancelled")
        return True

    # ---- fills -------------------------------------------------------------

    def process(self, now: datetime | None = None) -> list[str]:
        now = now or self.now_fn()
        st = self.state()
        if not st.started:
            return []
        tickers = sorted({o.ticker for o in st.orders.values()} | set(st.positions))
        msgs: list[str] = []
        for t in tickers:
            try:
                daily = self.fetch(t, "2y", "1d")
            except DataError as e:
                msgs.append(f"[{t}] {e}; paper fills not checked")
                continue
            try:
                intra = self.fetch(t, "5d", "5m")
            except DataError:
                intra = None
            msgs += self._process_ticker(t, daily, _intraday_by_day(intra), now)
        return msgs

    def _process_ticker(self, t: str, daily: Series, intra: dict[date, list[Bar]], now: datetime) -> list[str]:
        msgs = []
        for _ in range(MAX_CHAINED_EVENTS):
            st = self.state()
            events = [ev for o in st.orders.values() if o.ticker == t for ev in [self._order_event(o, daily, intra, now)] if ev]
            pos = st.positions.get(t)
            plan = self.plans.find(t, PAPER)
            if pos and plan and plan.status == "open":
                events += self._exit_events(plan, pos, daily, intra, now)
            if not events:
                break
            msg, ok = self._apply(t, min(events, key=lambda e: e.at), st, daily)
            msgs.append(msg)
            if not ok:
                break
        return msgs

    def _order_event(self, o: Order, daily: Series, intra: dict, now: datetime) -> Event | None:
        if o.kind == "limit" and o.price is not None:
            lim = o.price
            if o.side == "buy":
                hit = _first_touch(daily, intra, o.placed_at, lambda h, l: l < lim, lambda op: min(op, lim))
            else:
                hit = _first_touch(daily, intra, o.placed_at, lambda h, l: h > lim, lambda op: max(op, lim))
        elif o.kind == "close_above" and o.price is not None:
            hit = _first_close(daily, o.placed_at, lambda c: c > o.price, now)
        elif o.kind == "moc":
            hit = _first_close(daily, o.placed_at, lambda c: True, now)
        else:
            return None
        if not hit:
            return None
        at, price, day, basis = hit
        return Event(at, o.kind, o.side, o.shares, price, day, basis, o)

    def _exit_events(self, plan: Plan, pos: Position, daily: Series, intra: dict, now: datetime) -> list[Event]:
        if not plan.effective_at:
            return []
        start = parse_dt(plan.effective_at)
        events = []
        if plan.stop is not None:
            stop = plan.stop
            hit = _first_close(daily, start, lambda c: c < stop, now)
            if hit:
                events.append(Event(hit[0], "stop", "sell", pos.shares, hit[1], hit[2], hit[3]))
        if plan.t1 is not None and not plan.t1_hit:
            t1 = plan.t1
            hit = _first_touch(daily, intra, start, lambda h, l: h >= t1, lambda op: max(op, t1))
            if hit:
                n = math.floor(pos.shares * self.cfg.rules.t1_sell_fraction) or pos.shares
                events.append(Event(hit[0], "t1", "sell", n, hit[1], hit[2], hit[3]))
        return events

    def _apply(self, t: str, ev: Event, st: PaperState, daily: Series) -> tuple[str, bool]:
        pos = st.positions.get(t)
        cur = ev.order.currency if ev.order else (pos.currency if pos else infer_currency(t))
        try:
            rate, fxinfo = self.fx.rate(cur, on=ev.day)
        except DataError as e:
            return f"[{t}] {e}; paper fill on {fmt_day(ev.day)} deferred", False
        plan = self.plans.find(t, PAPER)
        order_id = ev.order.order_id if ev.order else None

        if ev.side == "buy":
            shares = float(ev.shares or 0)
            amt = shares * ev.price * rate
            if shares <= 0 or amt > st.cash + 1e-6:
                why = f"insufficient paper cash: needs {amt:,.2f}, has {st.cash:,.2f}" if shares > 0 else "no share count"
                self.store.append(LEDGER, {"type": "reject", "order_id": order_id, "reason": why}, prefix="pl")
                if plan and plan.status == "pending":
                    plan.status = "closed"
                    self.plans.save(plan, f"paper order rejected: {why}")
                return f"[{t}] Paper order {order_id} rejected: {why}", True
        else:
            if pos is None:
                self.store.append(LEDGER, {"type": "reject", "order_id": order_id, "reason": "no position"}, prefix="pl")
                return f"[{t}] Paper sell {order_id} rejected: no position", True
            want = pos.shares if (ev.order and ev.order.sell_all) or ev.shares is None else float(ev.shares)
            shares = min(want, pos.shares)
            amt = shares * ev.price * rate

        self.store.append(
            LEDGER,
            {
                "type": "fill",
                "order_id": order_id,
                "ticker": t,
                "side": ev.side,
                "shares": shares,
                "price": round(ev.price, 4),
                "currency": cur,
                "fx_rate": rate,
                "fx_date": fxinfo.day.isoformat() if fxinfo else None,
                "amount_base": round(amt, 2),
                "reason": ev.reason,
                "at": iso(ev.at),
                "bar_day": ev.day.isoformat(),
                "basis": ev.basis,
                "source": daily.source,
            },
            prefix="pl",
        )
        fill_txt = f"{ev.side.upper()} {shares:g} @ {ev.price:.2f} {cur}"
        msg = f"[{t}] Paper {fill_txt} ({ev.reason}, {ev.basis} {fmt_day(ev.day)}, {daily.source})"
        if fxinfo:
            msg += f"; {fxinfo.source} USD/CAD {fxinfo.rate:.4f} ({fmt_day(fxinfo.day)})"

        if plan:
            after = self.state().positions.get(t)
            if ev.side == "buy" and after:
                plan.status = "open"
                plan.entry = round(after.avg_local, 4)
                plan.shares = after.shares
                plan.entry_date = plan.entry_date or ev.day
                self.plans.save(plan, f"paper fill: {fill_txt}", effective_at=iso(ev.at))
            elif ev.side == "sell":
                if after is None:
                    plan.status = "closed"
                    plan.shares = 0
                    reason = f"paper fill: {fill_txt} ({ev.reason}); position closed"
                else:
                    plan.shares = after.shares
                    reason = f"paper fill: {fill_txt} ({ev.reason})"
                    if ev.reason == "t1":
                        plan.t1_hit = True
                        if plan.entry is not None and (plan.stop is None or plan.stop < plan.entry):
                            plan.stop = plan.entry
                            reason += f"; stop to breakeven {plan.entry:.2f} per your after-T1 rule"
                            msg += f"; stop moved to breakeven {plan.entry:.2f} (after-T1 rule)"
                self.plans.save(plan, reason, effective_at=iso(ev.at))
        return msg, True

    # ---- reporting ---------------------------------------------------------

    def valuation(self, st: PaperState, now: datetime) -> Valuation:
        rows, prices, gaps = [], {}, []
        total = 0.0
        as_of: date | None = None
        fx_rate = fx_note = None
        for t, pos in sorted(st.positions.items()):
            try:
                s = self.fetch(t, "2y", "1d")
                bar = ([b for b in s.bars if is_final(s, b, now)] or s.bars)[-1]
                rate, info = self.fx.rate(pos.currency)
                if info:
                    fx_rate, fx_note = info.rate, f"{info.source} USD/CAD {info.rate:.4f} ({fmt_day(info.day)})"
                value = pos.shares * bar.close * rate
                prices[t] = bar.close
                as_of = max(as_of, bar.day) if as_of else bar.day
                rows.append({"ticker": t, "shares": pos.shares, "avg": pos.avg_local, "currency": pos.currency, "last": bar.close, "day": bar.day, "value": value, "pnl": value - pos.cost_base})
            except DataError as e:
                value = pos.cost_base
                gaps.append(f"[{t}] valued at cost: {e}")
                rows.append({"ticker": t, "shares": pos.shares, "avg": pos.avg_local, "currency": pos.currency, "last": None, "day": None, "value": value, "pnl": 0.0})
            total += value

        bench = self.cfg.paper_book.benchmark
        bench_close = bench_return = bench_since = None
        try:
            bs = self.fetch(bench, "2y", "1d")
            final = [b for b in bs.bars if is_final(bs, b, now)] or bs.bars
            start_day = parse_dt(st.started["ts"]).astimezone(ZoneInfo(bs.exchange_tz)).date()
            before = [b for b in final if b.day <= start_day]
            base = before[-1] if before else final[0]
            bench_close, bench_since = final[-1].close, base.day
            bench_return = pct(base.close, bench_close)
            as_of = max(as_of, final[-1].day) if as_of else final[-1].day
        except DataError as e:
            gaps.append(f"benchmark {bench}: {e}")
        return Valuation(as_of, st.cash + total, total, rows, prices, fx_rate, fx_note, gaps, bench_close, bench_return, bench_since)

    def snapshot(self, now: datetime | None = None) -> str:
        now = now or self.now_fn()
        st = self.state()
        if not st.started:
            return "Paper book not started."
        v = self.valuation(st, now)
        if v.as_of is None:
            return "No price data, so paper equity was not recorded."
        key = v.as_of.isoformat()
        if key in st.equity_dates:
            return f"Paper equity for {fmt_day(v.as_of)} already recorded."
        self.store.append(
            LEDGER,
            {
                "type": "equity",
                "date": key,
                "equity": round(v.equity, 2),
                "cash": round(st.cash, 2),
                "positions_value": round(v.positions_value, 2),
                "realized": round(st.realized, 2),
                "prices": v.prices,
                "fx_rate": v.fx_rate,
                "benchmark": self.cfg.paper_book.benchmark,
                "benchmark_close": v.bench_close,
                "gaps": v.gaps,
            },
            prefix="pl",
        )
        return f"Paper equity recorded for {fmt_day(v.as_of)}: {v.equity:,.2f} {self.cfg.base_currency}."

    def scorecard(self, now: datetime | None = None) -> str:
        now = now or self.now_fn()
        st = self.state()
        if not st.started:
            return "Paper book not started. Run `dh init`."
        v = self.valuation(st, now)
        base = self.cfg.base_currency
        cap = float(st.started["capital"])
        start_day = parse_dt(st.started["ts"]).astimezone(ZoneInfo(self.cfg.timezone)).date()
        ret = pct(cap, v.equity)
        out = [f"Paper book scorecard, {fmt_day(v.as_of)} close" if v.as_of else "Paper book scorecard"]
        out.append(f"Equity {v.equity:,.2f} {base} ({ret:+.2f}% since {fmt_day(start_day)}) · Cash {st.cash:,.2f}")
        if v.bench_return is not None:
            out.append(
                f"Benchmark {self.cfg.paper_book.benchmark} {v.bench_return:+.2f}% since the {fmt_day(v.bench_since)} close · "
                f"paper minus benchmark {ret - v.bench_return:+.2f} pts (yardstick only)"
            )
        out.append(f"Realized P&L {st.realized:+,.2f} · Unrealized P&L {sum(r['pnl'] for r in v.rows):+,.2f}")
        if v.rows:
            out.append("Positions:")
            for r in v.rows:
                last = f"last {r['last']:.2f} ({fmt_day(r['day'])})" if r["last"] is not None else "no price"
                cost = r["value"] - r["pnl"]
                pnl_pct = f" ({pct(cost, r['value']):+.1f}%)" if cost else ""
                out.append(f"  {r['ticker']} {r['shares']:g} @ {r['avg']:.2f} {r['currency']} | {last} | value {r['value']:,.2f} {base} | P&L {r['pnl']:+,.2f}{pnl_pct}")
        else:
            out.append("Positions: none")
        if st.orders:
            out.append("Open orders:")
            for o in sorted(st.orders.values(), key=lambda o: o.placed_at, reverse=True):
                px = f" {o.price:.2f}" if o.price is not None else ""
                qty = "all" if o.sell_all else f"{(o.shares or 0):g}"
                out.append(f"  {o.order_id} {o.side.upper()} {qty} {o.ticker} {o.kind}{px} (placed {fmt_time(o.placed_at, self.cfg.timezone)})")
        out += v.gaps
        src = ["Yahoo Finance daily closes"]
        if v.fx_note:
            src.append(v.fx_note)
        out.append("Source: " + "; ".join(src) + ".")
        out.append(DISCLAIMER)
        return "\n".join(out)

    def ledger_lines(self, limit: int = 20) -> list[str]:
        base = self.cfg.base_currency
        out = []
        for e in reversed(self.entries()[-limit:]):  # newest first
            kind = e.get("type")
            if kind == "fill":
                s = (
                    f"FILL {e['side'].upper()} {e['shares']:g} {e['ticker']} @ {e['price']:.2f} {e['currency']} "
                    f"({e['reason']}, {e['basis']} {e['bar_day']}) = {e['amount_base']:,.2f} {base}"
                )
            elif kind == "order":
                px = f" {e['price']:.2f}" if e.get("price") is not None else ""
                qty = "all" if e.get("sell_all") else f"{(e.get('shares') or 0):g}"
                s = f"ORDER {e['order_id']} {e['side'].upper()} {qty} {e['ticker']} {e['kind']}{px}"
            elif kind == "cancel":
                s = f"CANCEL {e['order_id']}"
            elif kind == "reject":
                s = f"REJECT {e.get('order_id')}: {e.get('reason')}"
            elif kind == "equity":
                s = f"EQUITY {e['date']}: {e['equity']:,.2f} {base} (cash {e['cash']:,.2f})"
            elif kind == "start":
                s = f"START capital {e['capital']:,.2f} {e.get('currency')}, benchmark {e.get('benchmark')}"
            else:
                s = str(e)
            out.append(f"{fmt_time(parse_dt(e.get('at') or e['ts']), self.cfg.timezone)}  {s}")
        return out
