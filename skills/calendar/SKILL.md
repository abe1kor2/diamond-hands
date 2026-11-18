---
name: calendar
description: Upcoming events that touch the trader's holdings and the market. Use for earnings dates, ex-dividend dates, Fed/BoC meetings, CPI/PPI/jobs releases, exchange holidays, and out-by dates.
---

# Calendar

**Status: mostly not built yet.** What works today:
- Earnings dates inside each hold: on the risk card (`uv run dh risk-card TICKER`), from Nasdaq (Yahoo Finance as fallback). When no date has been announced, the card says so.
- Out-by dates: `uv run dh plan show`.

For anything else, reply "calendar: not built yet".

## Planned spec
List, soonest first, for the next 2 weeks (or the range asked): earnings (with time: before open / after close), ex-dividend, macro releases, central bank decisions, exchange holidays, and each plan's out-by date. Flag any event that falls inside an open hold. Cite the source for each date.
