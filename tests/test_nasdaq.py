from datetime import date, datetime, timezone

from diamond_hands.data import nasdaq

ET = nasdaq.ET

# 4:00 AM, 9:30 AM and 10:00 AM ET on Nov 17 2026, encoded the way the chart endpoint does it
X_0400, X_0930, X_1000 = 1794888000000, 1794907800000, 1794909600000

INFO = {
    "symbol": "NVDA",
    "companyName": "NVIDIA Corporation Common Stock",
    "primaryData": {
        "lastSalePrice": "$154.48",
        "netChange": "-1.66",
        "percentageChange": "-1.06%",
        "lastTradeTimestamp": "Nov 17, 2026 2:18 PM ET",
        "isRealTime": True,
        "volume": "235,730,052.015179",
    },
    "secondaryData": None,
    "marketStatus": "Open",
    "keyStats": {"fiftyTwoWeekHighLow": {"value": "148.80 - 243.37"}, "dayrange": {"value": "150.33 - 157.46"}},
}


def test_num_and_span():
    assert nasdaq.num("$1,234.56") == 1234.56
    assert nasdaq.num("-1.06%") == -1.06
    assert nasdaq.num("235,730,052.015179") == 235730052.015179
    assert nasdaq.num("N/A") is None
    assert nasdaq.span("150.33 - 157.46") == (150.33, 157.46)
    assert nasdaq.span("$157.46/$150.3301") == (150.3301, 157.46)
    assert nasdaq.span("") == (None, None)


def test_covers_us_listings_only():
    assert nasdaq.covers("NVDA") and nasdaq.covers("spy")
    assert not nasdaq.covers("XEQT.TO") and not nasdaq.covers("^GSPC")


def test_parse_quote():
    q = nasdaq.parse_quote("NVDA", INFO)
    assert (q.price, q.change, q.change_pct) == (154.48, -1.66, -1.06)
    assert (q.day_low, q.day_high, q.low_52w, q.high_52w) == (150.33, 157.46, 148.80, 243.37)
    assert q.last_trade == "Nov 17, 2026 2:18 PM ET" and q.market_status == "Open"


def test_parse_historical_orders_oldest_first_and_skips_bad_rows():
    rows = [
        {"date": "11/16/2026", "close": "$156.14", "volume": "333,822,100", "open": "$159.015", "high": "$163.20", "low": "$155.01"},
        {"date": "11/13/2026", "close": "N/A", "volume": "1", "open": "1", "high": "1", "low": "1"},
        {"date": "11/12/2026", "close": "$166.19", "volume": "747,707,600", "open": "$151.005", "high": "$167.00", "low": "$150.00"},
    ]
    bars = nasdaq.parse_historical(rows)
    assert [b.day for b in bars] == [date(2026, 11, 12), date(2026, 11, 16)]
    assert bars[-1].close == 156.14 and bars[-1].volume == 333822100


def test_chart_x_is_eastern_wall_clock():
    (t, v), = nasdaq.chart_points({"chart": [{"x": X_0400, "y": 156.66}]})
    assert t.astimezone(ET).strftime("%Y-%m-%d %H:%M") == "2026-11-17 04:00"
    assert t == datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc) and v == 156.66


def test_live_bar_uses_regular_session_and_is_provisional():
    chart = {"chart": [{"x": X_0400, "y": 170.0}, {"x": X_0930, "y": 156.80}, {"x": X_1000, "y": 154.20}], "volume": "1,000"}
    bar = nasdaq.live_bar(INFO, chart, date(2026, 11, 17))
    assert bar.provisional
    assert (bar.open, bar.close) == (156.80, 154.20)  # the 4 AM pre-market print is ignored
    assert (bar.low, bar.high) == (150.33, 157.46)  # exact day range from the quote
    assert nasdaq.live_bar(INFO, {"chart": [{"x": X_0400, "y": 170.0}]}, date(2026, 11, 17)) is None


def test_fetch_earnings(monkeypatch):
    monkeypatch.setattr(nasdaq, "info", lambda s: (INFO, "stocks"))
    monkeypatch.setattr(
        nasdaq,
        "_json",
        lambda *a: {"data": {"announcement": "Earnings announcement* for NVDA: Nov 19, 2026", "reportText": "expected to report after market close."}},
    )
    dates, note = nasdaq.fetch_earnings("NVDA")
    assert dates == [date(2026, 11, 19)] and "after market close" in note

    monkeypatch.setattr(nasdaq, "_json", lambda *a: {"data": {"announcement": "Earnings announcement* for NVDA: ", "reportText": ""}})
    assert nasdaq.fetch_earnings("NVDA")[0] is None

    monkeypatch.setattr(nasdaq, "info", lambda s: (INFO, "etf"))
    assert nasdaq.fetch_earnings("SPY")[0] == []
