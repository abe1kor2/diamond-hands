from datetime import datetime, timezone

import pytest

from diamond_hands.cli import main
from diamond_hands.data import nasdaq, yahoo
from diamond_hands.data.http import DataError
from diamond_hands.models import Quote


def fake_quote(symbol):
    return Quote(
        symbol=symbol.upper(),
        name="Example Corp",
        price=110.0,
        change=2.0,
        change_pct=1.85,
        prev_close=None,
        day_low=107.0,
        day_high=111.0,
        volume=1_000_000,
        low_52w=80.0,
        high_52w=130.0,
        market_status="Open",
        last_trade="Nov 17, 2026 2:18 PM ET",
        extra=[],
        source="Nasdaq",
        fetched_at=datetime(2026, 11, 17, 19, 18, tzinfo=timezone.utc),
        real_time=True,
    )


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("timezone: America/New_York\nbase_currency: USD\n")
    monkeypatch.setattr(nasdaq, "fetch_quote", fake_quote)

    def no_network(*a, **k):
        raise DataError("test", "offline")

    for name in ("fetch_quote", "fetch_chart", "fetch_calendar"):
        monkeypatch.setattr(yahoo, name, no_network)
    monkeypatch.setattr(nasdaq, "fetch_earnings", no_network)
    return str(p)


def test_quote_shows_price_and_held_position(cfg_path, capsys):
    main(["--config", cfg_path, "trade", "buy", "XYZ", "--shares", "10", "--price", "100", "--stop", "92", "--t1", "111", "--offline"])
    capsys.readouterr()
    assert main(["--config", cfg_path, "quote", "XYZ"]) == 0
    out = capsys.readouterr().out
    assert "XYZ (Example Corp) 110.00, +2.00 (+1.85%)" in out
    assert "last trade Nov 17, 2026 2:18 PM ET (real-time)" in out
    assert "You hold 10 sh @ 100.00 (real): +10.0% vs your cost · stop 92.00 (-16.4%) · T1 111.00 (+0.9%)" in out


def test_holdings_values_positions(cfg_path, capsys):
    main(["--config", cfg_path, "trade", "buy", "XYZ", "--shares", "10", "--price", "100", "--offline"])
    capsys.readouterr()
    assert main(["--config", cfg_path, "holdings"]) == 0
    out = capsys.readouterr().out
    assert "XYZ (real) 10 sh @ 100.00 USD | last 110.00, +1.85% today | value 1,100.00 | P&L +100.00 (+10.0%)" in out
    assert "Total USD: value 1,100.00 | P&L +100.00 (+10.0%)" in out


def test_holdings_when_empty(cfg_path, capsys):
    assert main(["--config", cfg_path, "holdings"]) == 0
    assert "No holdings recorded yet" in capsys.readouterr().out


def test_quote_reports_source_failure(cfg_path, capsys, monkeypatch):
    monkeypatch.setattr(nasdaq, "fetch_quote", lambda s: (_ for _ in ()).throw(DataError("Nasdaq", "HTTP 503")))
    main(["--config", cfg_path, "quote", "XYZ"])
    out = capsys.readouterr().out
    assert "no data from Nasdaq: HTTP 503; fallback no data from test: offline" in out
