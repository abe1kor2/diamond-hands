---
name: paper-book
description: Run and report the virtual paper account. Use for paper trade orders and fills, the end-of-day scorecard, or questions about paper performance vs the benchmark.
---

# Paper book

- Starts at `paper_book.starting_capital`; compared to `paper_book.benchmark` as a yardstick only.
- Fills on real prices only: limits when price trades through; buy-on-close triggers and stops on the official close; targets on an intraday touch. After T1 the stop moves to breakeven automatically.
- USD converts at the official Bank of Canada rate for the fill date.
- Append-only `state/ledger.jsonl`; every list shown newest first.
- Paper and real never mix.

## Commands
- Buy (with its plan): `uv run dh paper buy TICKER --limit X | --close-above X | --moc --stop S [--t1 T] [--out-by D] [--shares N]`. With no `--shares`, size follows the risk and position rules.
- Sell: `uv run dh paper sell TICKER --limit X | --moc --shares N | --all`
- Cancel: `uv run dh paper cancel ORDER_ID`
- Check fills now: `uv run dh paper process [--eod]` (`dh watch` and `dh close-check` also do this)
- Scorecard: `uv run dh paper scorecard` (equity, cash, positions, realized and unrealized P&L, vs benchmark, open orders)
- Ledger: `uv run dh paper ledger [--limit N]`

## Not built yet
- Capping paper orders and positions at `rules.max_open_ideas` (the risk card shows the count but doesn't stop the order).
- Day change, and today's fills, targets, stops and expiries on the scorecard.
- Finalizing provisional FX rates with a new entry.
