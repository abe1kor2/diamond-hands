---
name: trade-log
description: Record a buy or sell the trader reports, or a position they already hold, then update holdings and the plan. Use when the trader says "I bought," "I sold," "I trimmed," "I'm holding," "I own," or sends a fill.
---

# Trade log

1. Record exactly what was reported: ticker, account, shares, price, time. Ask only for what's missing. For a position they already hold, the price is their average cost.
2. On a buy with no existing plan, ask once for the stop, T1 and out-by date. If they don't have them yet, record without them; the risk card shows NOT SET until they're added with `dh plan update`.
3. Run one command:
   - Buy or existing position: `uv run dh trade buy TICKER --shares N --price P [--stop S --t1 T --out-by YYYY-MM-DD] [--account IRA] [--time "2026-11-14 10:32"]`
   - Sell: `uv run dh trade sell TICKER --shares N --price P [--account IRA] [--time "..."]`
   `dh` appends the trade and a new plan entry to `state/plans.jsonl`, regenerates `state/holdings.yaml` (new average cost, remaining shares), and keeps separate accounts separate.
4. For sells: realized P&L in native and base currency at the official FX rate (not built yet; say so).
5. Confirm back in one short message with the risk card `dh` printed.

If a sell happened at or above T1, `dh` prints the breakeven-stop proposal from the after-T1 rule. Pass it on; record it only if the trader confirms.
