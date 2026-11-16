# Diamond Hands

A facts-first swing-trading partner for Claude Code. It tracks every trade against a written plan, prints risk cards, watches prices and levels, runs volume and candle analytics, and keeps a paper book that fills only on real prices. It reports what matched your rules; you make every call. It never places orders.

Two parts:
- **The plugin** (`skills/`, `commands/`) tells Claude what to do and how to talk.
- **The `dh` CLI** (`diamond_hands/`) does every calculation, so no number is ever estimated.

## Layout
```
.claude-plugin/plugin.json   plugin manifest
skills/diamond-hands/        main skill: shared rules, dh reference, router
skills/<sub-skill>/          level-watch, trade-plan, trade-log, analytics, paper-book,
                             after-close, morning-brief, weekly-review, ticker-deep-dive,
                             news-watch, sentiment, calendar, setup-odds
commands/                    slash commands
diamond_hands/               dh CLI (Python)
tests/                       pytest suite
config.example.yaml          your rules and thresholds
.env.example                 optional API keys (Twelve Data backup)
scripts/crontab.example      24/7 schedule
state/, logs/                created at runtime; git-ignored
```

## Setup
Requires [uv](https://docs.astral.sh/uv/) and Claude Code.

```sh
cp config.example.yaml config.yaml   # fill in your holdings, watchlist and account equity
uv sync
uv run dh init                       # creates state/, imports swing_holdings once, starts the paper book
claude --plugin-dir .                # start Claude with the plugin loaded
```

`config.yaml`, `state/` and `logs/` are git-ignored, so your positions stay on this machine.

## Commands
Plugin commands are namespaced (`/diamond-hands:brief`); the short form works when no other command has the same name. `/sentiment` and `/calendar` are the sub-skills themselves, invoked directly.

| Command | What it does | Status |
|---|---|---|
| `/log-trade bought 10 NVDA @ 160` | Record your own fill | ✅ |
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

The CLI works on its own too: `uv run dh --help`.

## 24/7 runs
See `scripts/crontab.example`. Each job runs `claude --plugin-dir . -p "/diamond-hands:<command>"` from the repo.

## Data sources
| Source | Used for | Key |
|---|---|---|
| Yahoo Finance | Prices (daily, 5-minute, pre/post), earnings dates | none |
| Twelve Data | Backup daily bars and close cross-check for US tickers | optional, free |
| Bank of Canada Valet | Official USD/CAD rate | none |

Yahoo's API is public but unofficial and sometimes rate-limits whole networks (HTTP 429). For a backup, get a free key at [twelvedata.com](https://twelvedata.com) (8 requests/minute, 800/day, US listings; TSX needs a paid plan) and put it in `.env`:

```sh
cp .env.example .env    # then set TWELVEDATA_API_KEY=...
```

When a source fails, the output says `no data from <source>` and skips that check.

## License
[MIT](LICENSE)

## Tests
```sh
uv run pytest
```

Analysis only, not financial advice.
