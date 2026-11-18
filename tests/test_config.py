import pytest

from diamond_hands import config
from diamond_hands.config import ConfigError, load_config, system_timezone


def test_runs_with_no_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    cfg = load_config()
    assert not cfg.from_file
    assert cfg.base_currency == "USD"
    assert cfg.paper_book.benchmark == "SPY" and cfg.us_index == "SPY"
    assert cfg.swing_holdings == [] and cfg.watchlist == []
    assert cfg.rules.max_open_ideas == 8 and cfg.rules.stop_distance_pct == (7, 10)
    assert cfg.state_dir == (tmp_path / "state").resolve()


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_partial_config_keeps_other_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("watchlist: [amd]\naccount_equity: 25000\n")
    cfg = load_config(p)
    assert cfg.from_file and cfg.watchlist == ["AMD"] and cfg.account_equity == 25000
    assert cfg.base_currency == "USD" and cfg.paper_book.benchmark == "SPY"


def test_system_timezone_reads_tz(monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    assert system_timezone() == "America/Chicago"
    monkeypatch.setenv("TZ", "Not/AZone")
    assert system_timezone()  # falls back to /etc/localtime or US Eastern
