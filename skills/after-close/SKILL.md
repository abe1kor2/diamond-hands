---
name: after-close
description: End-of-day check. Use for the scheduled after-close run or "how did today close."
---

# After close

Run `uv run dh close-check`. It covers:
1. Official closes vs close-based stops, T1 touches and out-by dates (`level-watch`).
2. Candle and volume flags for every plan and watchlist name (`analytics`).
3. Trailing stop raises due (proposed; logged only when the trader confirms).
4. Close cross-check between Nasdaq and Yahoo Finance for open positions (skipped, with the reason, when the second source doesn't answer).
5. Paper book fills, the day's equity entry and the scorecard (`paper-book`).
6. Heads-up for tomorrow's events (`calendar`: not built yet).

If it says there's no official close yet (the session is still open, or the source hasn't published it), close-based checks wait; say so. Send only what changed or matched a rule.
