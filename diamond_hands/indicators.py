"""Plain-Python indicators. Each returns None when there isn't enough data."""

from __future__ import annotations

from collections.abc import Sequence

from .models import Bar


def pct(base: float, value: float) -> float:
    """Percent change from base to value."""
    return (value / base - 1) * 100


def sma(values: Sequence[float], n: int, end: int | None = None) -> float | None:
    end = len(values) - 1 if end is None else end
    start = end - n + 1
    if n <= 0 or start < 0 or end >= len(values):
        return None
    return sum(values[start : end + 1]) / n


def wilder_rsi(closes: Sequence[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(d, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-d, 0.0)) / n
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    return [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:])]


def wilder_atr(bars: Sequence[Bar], n: int = 14) -> float | None:
    trs = true_ranges(bars)
    if len(trs) < n:
        return None
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def relative_volume(volumes: Sequence[float], n: int, idx: int | None = None) -> float | None:
    """Volume at idx divided by the average of the n sessions before it."""
    idx = len(volumes) - 1 if idx is None else idx
    if idx - n < 0:
        return None
    avg = sum(volumes[idx - n : idx]) / n
    return volumes[idx] / avg if avg > 0 else None


def up_down_volume(bars: Sequence[Bar], n: int) -> tuple[float, float] | None:
    """Total volume on up-close days vs down-close days over the last n sessions."""
    if len(bars) < n + 1:
        return None
    up = down = 0.0
    for p, b in zip(bars[-n - 1 : -1], bars[-n:]):
        if b.close > p.close:
            up += b.volume
        elif b.close < p.close:
            down += b.volume
    return up, down


def swing_points(bars: Sequence[Bar], k: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Confirmed swing highs/lows: strictly beyond the k bars on each side."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        nbrs = list(bars[i - k : i]) + list(bars[i + 1 : i + k + 1])
        if bars[i].high > max(b.high for b in nbrs):
            highs.append((i, bars[i].high))
        if bars[i].low < min(b.low for b in nbrs):
            lows.append((i, bars[i].low))
    return highs, lows


def weekly(bars: Sequence[Bar]) -> list[Bar]:
    """Roll daily bars into ISO-week bars; each weekly bar's day is its last session."""
    out: list[Bar] = []
    key = None
    for b in bars:
        k = tuple(b.day.isocalendar())[:2]
        if k != key:
            out.append(Bar(b.ts, b.day, b.open, b.high, b.low, b.close, b.volume))
            key = k
        else:
            w = out[-1]
            w.high = max(w.high, b.high)
            w.low = min(w.low, b.low)
            w.close = b.close
            w.volume += b.volume
            w.day = b.day
    return out


def relative_strength(bars: Sequence[Bar], bench: Sequence[Bar], n: int) -> tuple[float, float] | None:
    """(ticker % change, benchmark % change) over the last n shared sessions."""
    bench_close = {b.day: b.close for b in bench}
    common = [b for b in bars if b.day in bench_close]
    if len(common) < n + 1:
        return None
    a, z = common[-n - 1], common[-1]
    return pct(a.close, z.close), pct(bench_close[a.day], bench_close[z.day])
