---
name: trade-log
description: Record a buy or sell the trader reports, update holdings, and refresh the plan. Use when the trader says "I bought," "I sold," "I trimmed," or sends a fill.
---

# Trade log

1. Record exactly what was reported: ticker, account, shares, price, time. Ask only for what's missing. On a buy with no existing plan, ask for the stop, T1 and out-by date.
2. Run one command:
   - Buy: `uv run dh trade buy TICKER --shares N --price P [--stop S --t1 T --out-by YYYY-MM-DD] [--account TFSA] [--time "2026-11-14 10:32"]`
   - Sell: `uv run dh trade sell TICKER --shares N --price P [--account TFSA] [--time "..."]`
   `dh` appends the trade and a new plan entry to `state/plans.jsonl`, regenerates `state/holdings.yaml` (new average cost, remaining shares), and keeps separate accounts separate.
3. For sells: realized P&L in native and base currency at the official FX rate (not built yet; say so).
4. Confirm back in one short message with the risk card `dh` printed.

If a sell happened at or above T1, `dh` prints the breakeven-stop proposal from the after-T1 rule. Pass it on; record it only if the trader confirms.
