---
name: news-watch
description: Check news and regulatory filings for holdings and watchlist names. Use for "any news on X," CEO/CFO changes, guidance, deals, lawsuits, analyst rating changes, insider trades, or the scheduled hourly news sweep.
---

# News watch

**Status: not built yet.** Reply "news-watch: not built yet" and stop. The spec below describes the planned `dh` module.

## Sources
Company press releases, SEC EDGAR (8-K, Form 4, 10-Q), SEDAR+ for Canadian issuers, Reuters, AP, CNBC, Bloomberg headlines, Google News RSS per ticker. Core ETFs: fund-level news only.

## Material
Leadership changes, guidance changes, earnings, M&A, major lawsuits or regulatory actions, export restrictions, major-bank rating changes, large insider sales, offerings, buybacks.

## Method
1. Pull items since the last run.
2. Drop anything already in `state/news_seen.json`; mark follow-ups to stories already sent as follow-ups.
3. Tag: leadership · earnings · deal · legal · regulatory · analyst · insider.
4. Attach the price move since the headline.
5. Add every reviewed item to `news_seen.json`.

## Output
One alert per material item: what happened, who reported it, whether it's confirmed, price reaction, and how it touches the trader's plan (stop distance, earnings inside the hold). Scheduled runs with nothing material send nothing.
