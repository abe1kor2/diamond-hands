---
name: setup-odds
description: Replay a setup on a ticker's own history to show how often it reached its target before its stop. Use when asked "how often does this work," "what are the odds," or to test a rule before trusting it.
---

# Setup odds

**Status: not built yet.** Reply "setup-odds: not built yet" and stop. The spec below describes the planned `dh` module.

1. Define the setup precisely (entry trigger, stop, T1, max hold) from the plan or the question.
2. Scan 1–2 years of daily bars for the same trigger.
3. For each instance: did it hit T1 or the stop first, and after how many days? Did it time out?
4. Report: count, hit rate, median days, worst drawdown. Label any sample under 15 as small.

Example (example numbers only): "11 similar setups since Nov 2024: 6 hit T1 first (55%), 4 stopped, 1 timed out; median 9 days." Past results are a description, not a forecast.
