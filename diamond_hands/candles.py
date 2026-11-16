"""Daily candle classification. Shapes are reported as facts, not signals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from .indicators import pct
from .models import Bar
from .timeutil import fmt_day


@dataclass
class CandleRead:
    day: date
    labels: list[str]
    color: str  # green | red | flat
    big: bool
    change_pct: float | None
    body_pct_of_price: float
    body_pct_of_range: float | None
    rel_volume: float | None

    def text(self) -> str:
        parts = [", ".join(self.labels)]
        if self.change_pct is not None:
            parts.append(f"{self.change_pct:+.1f}%")
        if self.big and self.body_pct_of_range is not None:
            parts.append(f"body {self.body_pct_of_range:.0f}% of range")
        if self.rel_volume is not None:
            parts.append(f"volume {self.rel_volume:.1f}x")
        return f"{fmt_day(self.day)}: " + ", ".join(parts)


def read_candle(bars: Sequence[Bar], i: int, big_body_pct: float, rel_volume: float | None = None) -> CandleRead:
    """Classify bars[i].

    Definitions:
      big candle      body >= big_body_pct of the open
      doji            body <= 10% of the day's range
      hammer          lower shadow >= 2x body, upper shadow <= 0.5x body
      shooting star   upper shadow >= 2x body, lower shadow <= 0.5x body
      engulfing       today's body covers yesterday's opposite-colour body
      inside/outside  range inside / beyond yesterday's range
      gap up/down     open beyond yesterday's high/low; "filled" once price trades back to yesterday's close
    """
    b = bars[i]
    p = bars[i - 1] if i > 0 else None
    body = abs(b.close - b.open)
    rng = b.high - b.low
    upper = b.high - max(b.open, b.close)
    lower = min(b.open, b.close) - b.low
    color = "green" if b.close > b.open else "red" if b.close < b.open else "flat"
    body_pct_price = body / b.open * 100 if b.open else 0.0
    body_pct_range = body / rng * 100 if rng > 0 else None
    big = color != "flat" and body_pct_price >= big_body_pct

    labels: list[str] = []
    if big:
        labels.append(f"big {color} candle")
    if rng > 0 and body <= 0.1 * rng:
        labels.append("doji")
    elif body > 0:
        if lower >= 2 * body and upper <= 0.5 * body:
            labels.append("hammer")
        elif upper >= 2 * body and lower <= 0.5 * body:
            labels.append("shooting star")

    if p is not None:
        if color == "green" and p.close < p.open and b.open <= p.close and b.close >= p.open and (b.open < p.close or b.close > p.open):
            labels.append("bullish engulfing")
        elif color == "red" and p.close > p.open and b.open >= p.close and b.close <= p.open and (b.open > p.close or b.close < p.open):
            labels.append("bearish engulfing")
        if b.high <= p.high and b.low >= p.low and (b.high < p.high or b.low > p.low):
            labels.append("inside day")
        elif b.high > p.high and b.low < p.low:
            labels.append("outside day")
        if b.open > p.high:
            labels.append(f"gap up {pct(p.close, b.open):+.1f}% ({'filled' if b.low <= p.close else 'unfilled'})")
        elif b.open < p.low:
            labels.append(f"gap down {pct(p.close, b.open):+.1f}% ({'filled' if b.high >= p.close else 'unfilled'})")

    if not labels:
        labels.append(f"{color} candle")
    return CandleRead(
        day=b.day,
        labels=labels,
        color=color,
        big=big,
        change_pct=pct(p.close, b.close) if p else None,
        body_pct_of_price=body_pct_price,
        body_pct_of_range=body_pct_range,
        rel_volume=rel_volume,
    )


def streak(bars: Sequence[Bar]) -> tuple[str, int]:
    """Consecutive up or down closes ending at the last bar."""
    if len(bars) < 2:
        return "flat", 0
    direction = None
    n = 0
    for p, b in zip(reversed(bars[:-1]), reversed(bars[1:])):
        d = "up" if b.close > p.close else "down" if b.close < p.close else "flat"
        if direction is None:
            direction = d
        if d != direction or d == "flat":
            break
        n += 1
    return direction or "flat", n
