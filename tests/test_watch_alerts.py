from datetime import date, datetime

from helpers import ET, mkbar, mkseries

from diamond_hands.alerts import Alert, Dispatcher
from diamond_hands.plans import Plan
from diamond_hands.store import Store
from diamond_hands.watch import check_plan


def plan():
    return Plan(
        plan_id="p1",
        account="real",
        ticker="XYZ",
        status="open",
        entry=35.29,
        stop=31.80,
        t1=39.0,
        shares=97,
        entry_date=date(2026, 11, 2),
        out_by=date(2026, 12, 11),
    )


def series(last_close, last_low):
    bars = [mkbar(date(2026, 11, d), 34, 35, 33, 34) for d in (2, 3, 4, 5)]
    bars.append(mkbar(date(2026, 11, 6), 33, 33.5, last_low, last_close))
    return mkseries("XYZ", bars)


def test_closed_below_stop_after_close(cfg):
    now = datetime(2026, 11, 6, 17, 0, tzinfo=ET)
    _, alerts = check_plan(cfg, plan(), series(31.5, 31.2), now)
    assert [a.rule for a in alerts] == ["closed_below_stop"]


def test_intraday_break_does_not_trigger_stop(cfg):
    now = datetime(2026, 11, 6, 12, 0, tzinfo=ET)
    _, alerts = check_plan(cfg, plan(), series(31.5, 31.2), now)
    rules = [a.rule for a in alerts]
    assert "closed_below_stop" not in rules and "intraday_below_stop" in rules


def test_near_stop_band(cfg):
    now = datetime(2026, 11, 6, 17, 0, tzinfo=ET)
    _, alerts = check_plan(cfg, plan(), series(32.10, 31.95), now)
    assert [a.rule for a in alerts] == ["near_stop"]


def test_alert_fires_once(tmp_path):
    store = Store(tmp_path / "state")
    a = Alert("XYZ", "closed_below_stop", "31.80", date(2026, 11, 6), "Closed 31.50, below your 31.80 close-stop.")
    assert len(Dispatcher(store, tmp_path / "logs", "America/St_Johns").send([a])) == 1
    assert Dispatcher(store, tmp_path / "logs", "America/St_Johns").send([a]) == []
    assert (tmp_path / "logs" / "alerts.log").read_text().count("closed_below_stop") == 1
