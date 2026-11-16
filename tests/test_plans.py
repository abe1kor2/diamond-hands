from datetime import date, timedelta

from helpers import mkbar

from diamond_hands.config import Rules
from diamond_hands.plans import CardContext, Plan, PlanBook, propose_trail, risk_card
from diamond_hands.store import Store


def example_plan(**kw):
    base = dict(
        plan_id="plan-x",
        account="real",
        ticker="NVDA",
        status="open",
        entry=35.29,
        stop=31.80,
        t1=39.00,
        out_by=date(2026, 12, 11),
        shares=97,
        entry_date=date(2026, 11, 2),
        currency="CAD",
    )
    base.update(kw)
    return Plan(**base)


def ctx(**kw):
    base = dict(
        equity_base=37615,
        base_currency="CAD",
        fx_to_base=1.0,
        fx_note=None,
        open_ideas=7,
        earnings=[date(2026, 11, 17)],
        earnings_note="test",
        today=date(2026, 11, 14),
    )
    base.update(kw)
    return CardContext(**base)


def test_risk_card_matches_documented_example():
    text, verdict = risk_card(example_plan(), Rules(), ctx())
    assert "Entry 35.29  |  Stop (close) 31.80  |  Risk/share 3.49" in text
    assert "$ at risk 338.53 CAD (0.9% of equity)" in text
    assert "T1 39.00 (+10.5%, 1.06R) sell half  |  Out by Dec 11" in text
    assert "Earnings inside hold: YES (Nov 17)" in text
    assert "idea count 7/8 ✓" in text
    assert verdict == "PASS"


def test_risk_card_blocks_oversized_risk():
    _, verdict = risk_card(example_plan(shares=200), Rules(), ctx())
    assert verdict == "BLOCK"


def test_risk_card_without_equity_skips_checks():
    text, verdict = risk_card(example_plan(), Rules(), ctx(equity_base=None))
    assert "size ?" in text and "risk ?" in text and verdict == "PASS"
    assert "account_equity is not set" in text


def test_risk_card_converts_usd():
    text, _ = risk_card(example_plan(currency="USD"), Rules(), ctx(fx_to_base=1.4, fx_note="BoC"))
    assert "$ at risk 338.53 USD = 473.94 CAD (1.3% of equity)" in text


def test_plan_log_is_append_only(cfg):
    book = PlanBook(Store(cfg.state_dir), cfg)
    p = example_plan(plan_id="plan-1")
    book.save(p, "first")
    first_id = p.entry_id
    p.stop = 33.0
    book.save(p, "raise stop")
    entries = book.entries()
    assert len(entries) == 2
    assert entries[0]["stop"] == 31.80 and entries[1]["stop"] == 33.0
    assert entries[1]["supersedes"] == first_id
    assert book.find("NVDA", "real").stop == 33.0


def test_trailing_proposal_uses_higher_swing_low():
    lows = [103, 102, 100, 102, 104, 106, 105, 104, 107, 109, 111]
    start = date(2026, 10, 1)
    bars = [mkbar(start + timedelta(days=i), l + 1, l + 2, l, l + 1) for i, l in enumerate(lows)]
    plan = example_plan(entry=100.0, stop=92.0, t1=111.0)
    level, why, origin, gain = propose_trail(plan, bars, Rules())
    assert level == 104 and "higher swing low" in why
    assert round(gain, 1) == 12.0


def test_trailing_never_proposes_lower_stop():
    lows = [103, 102, 100, 102, 104, 106, 105, 104, 107, 109, 111]
    start = date(2026, 10, 1)
    bars = [mkbar(start + timedelta(days=i), l + 1, l + 2, l, l + 1) for i, l in enumerate(lows)]
    assert propose_trail(example_plan(entry=100.0, stop=105.0), bars, Rules()) is None
