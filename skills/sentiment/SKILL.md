---
name: sentiment
description: Measure crowd and market sentiment for a ticker or the whole market. Use when asked what people are saying, whether the crowd is buying the dip or selling the top, how the mood has shifted, or for the Fear & Greed / put-call / VIX picture.
---

# Sentiment

**Status: not built yet.** Reply "sentiment: not built yet" and stop. The spec below describes the planned `dh` module.

Report the mood as numbers and quotes. Never turn it into a call. With no ticker given, cover all holdings plus market mood.

## Sources
StockTwits public streams, Reddit (r/stocks, r/investing, r/wallstreetbets, r/CanadianInvestor), news headline tone. Market-wide: CNN Fear & Greed, AAII survey, CBOE put/call, VIX. X is not used (no free API).

## Method
1. Collect posts for the last 24h and last 7 days. Drop bots, spam, duplicates, and paid promos.
2. Classify each post:
   - **Direction:** bullish / bearish / neutral.
   - **Intent:** buy-the-dip · buy-the-breakout · take-profit / sell-the-top · panic / capitulation · hold.
3. Weight: news > named analysts > large accounts > general posts.
4. Score = %bull − %bear (−100 to +100). Compare to the 7-day average.
5. Mention volume vs its 7-day average.
6. Pull 2–3 representative quotes with source and time.

## Flags
- **Shift:** score moves more than `alerts.sentiment_shift_flag` points in a day.
- **Spike:** mention volume ≥ 2x the 7-day average.
- **Divergence:** sentiment and price moving opposite ways over 3+ days. Report as a fact, not a forecast.

## Output
```
<TICKER> sentiment (last 24h, <n> posts)
Bull x% | Bear y% | Neutral z%   Score +s (7-day avg +a, Δd)
Intent: buy-the-dip x% · take-profit y% · panic z% · hold w%
Mention volume: n.nx 7-day avg
Top themes: …
Sample: "…" (source, time)
Market mood: Fear & Greed n (label), put/call n.nn, VIX n.n
```
State a sample size every time; label samples under 100 posts as thin.
