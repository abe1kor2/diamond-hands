from datetime import date

from helpers import mkbar

from diamond_hands.candles import read_candle, streak

D1, D2, D3 = date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)


def labels(prev, cur):
    return read_candle([prev, cur], 1, 3.0).labels


def test_inside_day():
    assert "inside day" in labels(mkbar(D1, 100, 110, 90, 105), mkbar(D2, 101, 108, 95, 103))


def test_outside_day_and_bullish_engulfing():
    got = labels(mkbar(D1, 104, 105, 100, 101), mkbar(D2, 100, 107, 99, 106))
    assert "bullish engulfing" in got and "outside day" in got


def test_big_green_candle():
    c = read_candle([mkbar(D1, 100, 101, 99, 100), mkbar(D2, 100, 106.5, 99.8, 106)], 1, 3.0)
    assert c.big and c.labels[0] == "big green candle"
    assert round(c.change_pct, 1) == 6.0


def test_gap_up_filled_and_unfilled():
    prev = mkbar(D1, 100, 102, 99, 101)
    assert any(l.startswith("gap up") and "(unfilled)" in l for l in labels(prev, mkbar(D2, 103, 105, 102.5, 104)))
    assert any(l.startswith("gap up") and "(filled)" in l for l in labels(prev, mkbar(D2, 103, 105, 100.5, 104)))


def test_doji_and_hammer():
    assert "doji" in read_candle([mkbar(D1, 100, 105, 95, 100.2)], 0, 3.0).labels
    assert "hammer" in read_candle([mkbar(D1, 100, 101.2, 97, 101)], 0, 3.0).labels


def test_streak():
    bars = [mkbar(D1, 1, 1, 1, 10), mkbar(D2, 1, 1, 1, 11), mkbar(D3, 1, 1, 1, 12)]
    assert streak(bars) == ("up", 2)
