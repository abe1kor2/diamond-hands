# Diamond Hands

A facts-first swing-trading partner for Claude Code. It tracks every trade against a written plan, prints risk cards, shows what you hold at current prices, watches stops and targets, runs volume and candle analytics, and keeps a paper book that fills only on real prices. It reports what matched your rules; you make every call. It never places orders.

Two parts:
- **The plugin** (`skills/`, `commands/`) tells Claude what to do and how to talk.
- **The `dh` CLI** (`diamond_hands/`) does every calculation, so no number is ever estimated.

No API keys, no accounts, no config file required.

## Quick start
Requires [uv](https://docs.astral.sh/uv/) and Claude Code.

```sh
git clone <this repo> && cd diamond-hands
uv sync
claude --plugin-dir .
```

Then talk to it:
- "I'm holding 20 NVDA at 150, stop 138, target 166, out by Dec 31"
- "What am I holding?" / `/holdings`
- "What's AMD at?" / `/quote AMD`
- "Plan a buy of AVGO on a close above 360" / `/plan AVGO`

Or use the CLI directly: `uv run dh --help`.

## Optional settings
Everything has a built-in default: US markets, USD, SPY as the benchmark, your computer's time zone, and the trading rules in [`config.example.yaml`](config.example.yaml). To change any of it (watchlist, account equity for risk %, CAD as base currency, thresholds), copy that file to `config.yaml` and keep the lines you want. `config.yaml`, `state/` and `logs/` are git-ignored, so your positions stay on your machine.

## Layout
```
.claude-plugin/plugin.json   plugin manifest
skills/diamond-hands/        main skill: shared rules, dh reference, router
skills/<sub-skill>/          holdings, level-watch, trade-plan, trade-log, analytics,
                             paper-book, after-close, morning-brief, weekly-review,
                             ticker-deep-dive, news-watch, sentiment, calendar, setup-odds
commands/                    slash commands
diamond_hands/               dh CLI (Python)
tests/                       pytest suite
config.example.yaml          optional overrides
scripts/crontab.example      24/7 schedule
state/, logs/                created on first use; git-ignored
```

## Commands
Plugin commands are namespaced (`/diamond-hands:brief`); the short form works when no other command has the same name. `/holdings`, `/sentiment` and `/calendar` are the sub-skills themselves, invoked directly.

| Command | What it does | Status |
|---|---|---|
| `/holdings` | What you hold, priced now, with P&L and distance to stop and T1 | ✅ |
| `/quote TICKER` | Current price of any ticker, plus your position if you hold it | ✅ |
| `/log-trade bought 10 NVDA @ 160` | Record your own fill or an existing position | ✅ |
| `/plan TICKER` | Trade plan + risk card | ✅ |
| `/levels` | Holdings vs stops, targets, buy zones | ✅ |
| `/analyze TICKER` | Volume, candles, trend, levels | ✅ |
| `/volume TICKER` | Relative volume only | ✅ |
| `/candles TICKER` | Last 5 daily candles | ✅ |
| `/paper` | Paper book orders and scorecard | ✅ |
| `/close` | After-close check | ✅ (no calendar heads-up) |
| `/brief` | Pre-market brief | partial |
| `/week` | Weekly review | partial |
| `/deep-dive TICKER` | Everything on one ticker | partial |
| `/news [TICKER]` | Material news and filings | planned |
| `/sentiment [TICKER\|market]` | Crowd mood, buy-the-dip vs sell-the-top | planned |
| `/mood` | Fear & Greed, put/call, VIX, AAII | planned |
| `/calendar [days]` | Earnings, macro, out-by dates | planned |
| `/odds TICKER` | How often the setup worked historically | planned |

## Data sources
All public, no key needed.

| Source | Used for |
|---|---|
| Nasdaq | US stocks and ETFs: live quotes, daily bars, pre-market and after-hours, earnings dates |
| Yahoo Finance | Fallback for all of the above, and the only source for TSX tickers |
| Bank of Canada Valet | Official USD/CAD rate, only when CAD and USD are mixed |

Both price APIs are public but unofficial, so either can change or rate-limit without notice. Diamond Hands tries Nasdaq first, then Yahoo; when both fail, the output says `no data from <source>` and skips that check.

## 24/7 runs
See `scripts/crontab.example`. Each job runs `claude --plugin-dir . -p "/diamond-hands:<command>"` from the repo.

## Tests
```sh
uv run pytest
```

## License
[MIT](LICENSE)

Analysis only, not financial advice.
