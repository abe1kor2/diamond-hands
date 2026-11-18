---
name: holdings
description: Show what the trader holds priced right now, or look up the current price of any ticker. Use for "what am I holding," "how are my positions doing," "what's NVDA at," "price of X," or "I hold X, where is it now."
---

# Holdings and quotes

- **Everything held:** `uv run dh holdings`. Each open position from the trade log, priced now: shares, average cost, last price and today's change, value, P&L, distance to stop and T1, out-by date. Then totals by currency, and pending ideas vs their triggers.
- **Paper book:** `uv run dh holdings --paper` (the paper scorecard). Never mixed with real holdings.
- **Any ticker:** `uv run dh quote NVDA [AMD ...]`. Last price, change, day range, volume, 52-week range, market status, the source's last-trade time, and the pre-market or after-hours price when there is one. If the trader holds it, a line with their cost basis and stop/T1 distances follows.

If the trader says they hold a ticker that isn't in the log yet, record it first with `trade-log` (ask for shares and average cost), then show it.

Relay the output as printed, including the last-trade time and market status, so a closed-market or after-hours price is never mistaken for a live one.
