from datetime import date, timedelta

from helpers import mkbar

from diamond_hands.indicators import relative_strength, relative_volume, sma, swing_points, up_down_volume, wilder_atr, wilder_rsi


def days(n, start=date(2026, 1, 5)):
    return [start + timedelta(days=i) for i in range(n)]


def test_sma():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1, 2, 3, 4], 2, end=1) == 1.5
    assert sma([1, 2], 3) is None


def test_rsi_extremes():
    assert wilder_rsi([float(i) for i in range(30)]) == 100.0
    assert wilder_rsi([float(30 - i) for i in range(30)]) == 0.0
    assert wilder_rsi([1.0] * 10) is None


def test_atr_constant_range():
    bars = [mkbar(d, 10, 11, 9, 10) for d in days(20)]
    assert wilder_atr(bars, 14) == 2.0


def test_relative_volume_excludes_today():
    assert relative_volume([100] * 20 + [300], 20) == 3.0
    assert relative_volume([100] * 5, 20) is None


def test_up_down_volume():
    bars = [mkbar(d, 10, 11, 9, c, v) for d, c, v in zip(days(4), [10, 11, 10, 12], [0, 100, 50, 200])]
    assert up_down_volume(bars, 3) == (300, 50)


def test_swing_points():
    lows = [5, 4, 2, 4, 5, 6, 3, 6, 7]
    bars = [mkbar(d, l + 1, l + 2, l, l + 1) for d, l in zip(days(len(lows)), lows)]
    _, sl = swing_points(bars, 2)
    assert sl == [(2, 2), (6, 3)]


def test_relative_strength_aligns_dates():
    ds = days(6)
    t = [mkbar(d, c, c, c, c) for d, c in zip(ds, [100, 101, 102, 103, 104, 110])]
    b = [mkbar(d, c, c, c, c) for d, c in zip(ds, [100, 100, 100, 100, 100, 105])]
    tick, bench = relative_strength(t, b, 5)
    assert round(tick, 6) == 10.0 and round(bench, 6) == 5.0
