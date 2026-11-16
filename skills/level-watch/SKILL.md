---
name: level-watch
description: Check where each holding and pending order sits against its stop, targets, buy zone, and trailing stop. Use for "where are my stops," "anything near a target," or the scheduled hourly price watch.
---

# Level watch

Run `uv run dh watch` (scheduled runs: `uv run dh watch --quiet`, which prints only new alerts, data gaps and paper fills). It covers every active real-account plan in `state/plans.jsonl`:
- Current price, day high/low, change vs prior close (source + time).
- Distance to stop, T1, and buy trigger in % and price.

## Flags (each fires once per key: ticker + rule + level + date)
- T1 touched intraday.
- Entered buy zone / limit traded through / close above trigger.
- Within `alerts.near_stop_band_pct` of the stop.
- **After the close only:** closed below the stop (close-based; intraday breaks are reported, not triggered).
- Trailing lock-in: once up ≥ `rules.trailing_lock_in_after_gain_pct` (or after T1), `dh` proposes a higher stop: breakeven or the latest higher swing low. Stops never move down. The raise is logged only when the trader confirms: `uv run dh plan update TICKER --stop X --reason "..."`.
- Out-by date reached.

Relay the new alerts as printed. If nothing new matched on a scheduled run, send nothing.
