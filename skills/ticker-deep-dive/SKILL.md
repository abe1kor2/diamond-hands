---
name: ticker-deep-dive
description: One full picture of a single ticker. Use for "tell me everything about X" or before planning a new trade.
---

# Ticker deep dive

Run in order and merge into one report: `level-watch` (if held: `uv run dh watch`, that ticker's line), `analytics` (`uv run dh analytics TICKER`), the risk card if a plan exists (`uv run dh risk-card TICKER`), then `sentiment`, `news-watch` (last 7 days), `calendar` (next 30 days) and `setup-odds`, which are not built yet. Facts first, one line per section headline, sources at the end.
