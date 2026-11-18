---
name: analytics
description: Price, volume, and candle analytics for a ticker. Use when asked about unusual volume, big green or red candles, gaps, streaks, trend vs moving averages, RSI, ATR, support/resistance, or relative strength vs the index or sector.
---

# Analytics

Run `uv run dh analytics TICKER [TICKER ...]` (no tickers = every active plan plus the watchlist). It uses two years of daily bars plus pre-market and after-hours trading, from Nasdaq for US listings with Yahoo Finance as the fallback. During the session, today's bar is live and provisional; close-based flags wait for the official close.

When the trader asks for one section only (`/volume`, `/candles`), relay just those lines.

## Volume
- Relative volume vs the 20- and 50-day average; each of the last 5 candles shows its own 20-day multiple.
- Up-volume vs down-volume, 5 and 20 days.
- Whether a close beyond the prior 20-day high/low came on heavy volume (flag at `alerts.relative_volume_flag`).
- Pre-market / after-hours volume vs the prior sessions.

## Candles (last 5 sessions)
- Big body: body ≥ `alerts.big_candle_body_pct` of price; body as % of range.
- Doji, hammer, shooting star, bullish/bearish engulfing, inside day, outside day.
- Gaps up/down and whether they filled.
- Consecutive up/down closes.

## Trend and levels
- Price vs 20/50/200-day MAs and whether each is rising or falling.
- Distance from 52-week high/low.
- Recent swing highs/lows, weekly trend vs the 10-week MA.
- ATR(14); today's range as a multiple of ATR.
- RSI(14) value (report the number; don't label it as advice).
- Relative strength vs S&P 500 or TSX and vs the sector index in `config.yaml` `benchmarks.sector`, 5 and 20 days.

## Output
Plain sentences, newest first, for example (example numbers only):
"Nov 12: big green candle, +6.1%, body 92% of range, on 2.4x 20-day volume. Nov 13: inside day, 1.1x volume. Price 4.2% above a rising 50-day, 11% under the 52-week high."
