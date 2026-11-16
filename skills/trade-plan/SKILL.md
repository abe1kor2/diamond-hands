---
name: trade-plan
description: Build a written plan and risk card for a trade idea or an existing position. Use when the trader asks to plan a trade, size a position, or see entry, stop, target, and out-by levels.
---

# Trade plan

Every trade gets these before it starts, using `config.yaml` `rules`:
- **Buy-in:** zone or trigger (limit, or "first close above X").
- **T1:** about +10–12%, sell half. Then the stop moves to breakeven and trails under higher swing lows. No fixed cap on the rest.
- **Stop:** close-based, about 7–10% under entry or under the weekly trend.
- **Out-by:** 4–8 weeks.
- **Size:** about 1% equity at risk to the stop, capped at 10% of equity.
- **Catalysts inside the hold:** earnings (from `dh`). Ex-dividend and macro releases come from `calendar` (not built yet).

The trader supplies the levels. If the trigger, stop, T1 or out-by date is missing, ask for it; don't pick one.

## Commands
- New idea: `uv run dh plan new TICKER --limit X | --close-above X | --zone LOW HIGH --stop S --t1 T --out-by YYYY-MM-DD [--shares N]`. With no `--shares`, the card shows the share count that fits the risk and size rules.
- Change a plan: `uv run dh plan update TICKER [--stop S] [--t1 T] [--out-by D] --reason "..."`.
- Existing plan's card: `uv run dh risk-card TICKER`.
- Paper ideas go through `paper-book`.

## Risk card
`dh` prints it in this shape (example numbers only):
```
NVDA  |  Entry 35.29  |  Stop (close) 31.80  |  Risk/share 3.49
Shares 97  |  $ at risk 338.53 CAD (0.9% of equity)
T1 39.00 (+10.5%, 1.06R) sell half  |  Out by Dec 11
Earnings inside hold: YES (Nov 17)
Rule check: size 9.10%/10% ✓  risk 0.90%/1% ✓  idea count 7/8 ✓  → PASS
Plan vs rules: stop 9.9% under entry (rule 7–10) ✓ · T1 +10.5% (rule 10–12) ✓ · hold 5.6 wks (rule 4–8) ✓
```
PASS/BLOCK reports against the limits; it never decides. Exits are never blocked. Every plan and change is appended to `state/plans.jsonl`.
