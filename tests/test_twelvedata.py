import pytest

from diamond_hands.data import twelvedata
from diamond_hands.data.http import DataError

PAYLOAD = {
    "meta": {"symbol": "NVDA", "interval": "1day", "currency": "USD", "exchange_timezone": "America/New_York"},
    "values": [
        {"datetime": "2026-11-13", "open": "11", "high": "12", "low": "10", "close": "11.5", "volume": "200"},
        {"datetime": "2026-11-12", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "100"},
    ],
    "status": "ok",
}


def test_symbol_mapping():
    assert twelvedata.td_params("NVDA") == {"symbol": "NVDA"}
    assert twelvedata.td_params("xeqt.to") == {"symbol": "XEQT", "exchange": "TSX"}
    assert twelvedata.td_params("^GSPC") is None


def test_parse_orders_oldest_first():
    s = twelvedata.parse("NVDA", PAYLOAD)
    assert [b.close for b in s.bars] == [10.5, 11.5]
    assert s.source == "Twelve Data" and s.currency == "USD"


def test_parse_passes_api_error_through():
    with pytest.raises(DataError, match="requires a paid plan"):
        twelvedata.parse("XEQT.TO", {"status": "error", "code": 403, "message": "This symbol requires a paid plan"})


def test_api_key_env_then_dotenv(monkeypatch, tmp_path):
    monkeypatch.setattr(twelvedata, "ROOT", tmp_path)
    monkeypatch.delenv(twelvedata.ENV_KEY, raising=False)
    assert twelvedata.api_key() is None
    (tmp_path / ".env").write_text('TWELVEDATA_API_KEY="from-file"\n')
    assert twelvedata.api_key() == "from-file"
    monkeypatch.setenv(twelvedata.ENV_KEY, "from-env")
    assert twelvedata.api_key() == "from-env"


def test_fetch_without_key_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(twelvedata, "ROOT", tmp_path)
    monkeypatch.delenv(twelvedata.ENV_KEY, raising=False)
    with pytest.raises(DataError, match="TWELVEDATA_API_KEY is not set"):
        twelvedata.fetch_daily("NVDA")
