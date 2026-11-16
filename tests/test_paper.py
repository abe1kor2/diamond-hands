from datetime import date, datetime

from helpers import ET, mkbar, mkseries

from diamond_hands.data.fx import FxConverter
from diamond_hands.data.http import DataError
from diamond_hands.paper import PaperBook
from diamond_hands.plans import PAPER, PlanBook
from diamond_hands.store import Store

BARS = [
    mkbar(date(2026, 11, 3), 101, 102, 99.5, 100.5),  # trades through the 100 limit
    mkbar(date(2026, 11, 4), 101, 111, 100, 109),  # touches T1 110
    mkbar(date(2026, 11, 5), 105, 106, 98, 99),  # closes below breakeven 100
    mkbar(date(2026, 11, 6), 99, 100, 97, 98),
]


def fake_fetch(symbol, range_="2y", interval="1d", prepost=False):
    if interval != "1d":
        raise DataError("test", "no intraday")
    return mkseries(symbol, BARS)


def book(cfg, now):
    store = Store(cfg.state_dir)
    plans = PlanBook(store, cfg)
    pb = PaperBook(store, cfg, plans, FxConverter("CAD"), fetch=fake_fetch, now_fn=lambda: now)
    pb.start()
    return pb, plans


def test_limit_fill_t1_half_then_breakeven_stop(cfg):
    placed = datetime(2026, 11, 2, 20, 0, tzinfo=ET)
    pb, plans = book(cfg, placed)
    pb.place_buy("XYZ.TO", "limit", 100.0, 100, stop=95.0, t1=110.0, out_by=date(2026, 12, 15))

    msgs = pb.process(datetime(2026, 11, 9, 18, 0, tzinfo=ET))

    fills = [e for e in pb.entries() if e["type"] == "fill"]
    assert [(f["side"], f["shares"], f["price"], f["reason"]) for f in fills] == [
        ("buy", 100, 100.0, "limit"),
        ("sell", 50, 110.0, "t1"),
        ("sell", 50, 99.0, "stop"),
    ]
    st = pb.state()
    assert round(st.cash, 2) == 100450.00
    assert round(st.realized, 2) == 450.00
    assert st.positions == {}
    assert plans.find("XYZ.TO", PAPER) is None  # plan closed
    assert len(msgs) == 3


def test_raised_stop_ignores_older_bars(cfg):
    placed = datetime(2026, 11, 2, 20, 0, tzinfo=ET)
    pb, plans = book(cfg, placed)
    pb.place_buy("XYZ.TO", "limit", 100.0, 100, stop=90.0, t1=None, out_by=None)
    pb.process(datetime(2026, 11, 3, 18, 0, tzinfo=ET))  # fills the buy only

    plan = plans.find("XYZ.TO", PAPER)
    plan.stop = 99.5  # raised after the Nov 5 close of 99
    plans.save(plan, "raise stop", effective_at=datetime(2026, 11, 6, 17, 0, tzinfo=ET).isoformat())

    pb.process(datetime(2026, 11, 9, 18, 0, tzinfo=ET))
    sells = [e for e in pb.entries() if e["type"] == "fill" and e["side"] == "sell"]
    assert sells == []  # the Nov 5 (99) and Nov 6 (98) closes both happened before the raise took effect
