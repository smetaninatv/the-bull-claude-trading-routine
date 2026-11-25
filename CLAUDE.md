# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**trading-routine** is a set of Claude Code skills for day/swing trading US
equities through **Interactive Brokers** (`ib_async`). Each skill runs the
Python tools in `lib/` to research, chart and prepare orders. **Human-in-the-loop:**
Claude prepares orders; nothing that opens, closes or re-prices a trade is sent
without the user's explicit OK.

**All trading rules live in [`docs/STRATEGY.md`](docs/STRATEGY.md)** — read it
before changing trading logic. Thresholds live in `config/risk.yaml` and
`config/strategy.yaml`; docs and skills cite the config keys rather than
copying numbers.

## Invariants (do not change without asking the user)

- **Approval:** orders are built with `transmit=False` and sent via
  `lib.ibkr.transmit(...)` only after the user approves each one. The only
  automatic actions are de-risking ones: raising a stop, the in-profit OCA
  lock, and re-placing a missing protective stop.
- **Paper or live** is decided by the Gateway login (paper = `DU…`, live =
  `U…`), not the port. `connect()` accepts live only when
  `account.allow_live: true` in `config/risk.yaml` (enabled by the user
  2026-11-25). On a live account, every approval ask must start with
  **LIVE — real money** (`lib.ibkr.account_mode(ib)`). Never flip
  `allow_live` yourself. Gateway API port here is `4001`
  (`lib.ibkr.DEFAULT_PORT`).
- **Sizing** always goes through `lib.config.position_size(...)`, sized off the
  stop that will actually be honored (normally the catastrophic floor).
- **Entries** are non-marketable limits at a pullback level — never at or above
  market. Use `prepare_entry()` (entry + catastrophic stop, no fixed target)
  by default.
- **Catastrophic stop** (user-mandated 2026-06-10): always compute and offer it;
  the user may decline per trade — then build without it and note the open
  downside once.
- **`no_exit_at_loss`** (user-mandated): never propose a loss-realizing exit
  above the catastrophic floor. See STRATEGY §5.
- **Universe** is fully dynamic (`lib.screener.dynamic_universe()` + holdings);
  no hardcoded watchlist. `config/watchlist.txt` is an optional pin list.
- **Decision style:** rules build the shortlist and enforce limits; Claude adds
  judgment (news, catalyst, chart read) before recommending.
- **Runtime:** user's laptop, not always on → skills are triggered manually.
  Don't assume a run happened on schedule.
- **Time:** US equities, 9:30–16:00 ET. User is in **CET**. Anchor schedule
  logic to US/Eastern and convert dynamically (US and EU DST differ).

## Schedule

| Routine | Skill | US Eastern | CET (approx) |
|---|---|---|---|
| Pre-market research | `/premarket-research` | 7:30 | ~13:30 |
| Market-open execution | `/market-open` | 9:30 (+30 min) | ~15:30 |
| Exit scan | `/exit-scan` | 9:30–16:00 — day trades every 5–15 min, swings hourly | ~15:30–22:00 |
| Daily summary | `/daily-summary` | after close | ~22:15 |
| Weekly review | `/weekly-review` | Fri after close | ~22:15 Fri |

## Architecture

- `lib/ibkr.py` — connect, account values, positions/portfolio, `open_orders`
  (use this, not raw `openTrades()`), order builders (`prepare_entry`,
  `prepare_bracket`, `prepare_scaled_bracket`, `ratchet_lock_oca`), `transmit`.
- `lib/data.py` — IBKR historical/intraday bars (15-min delayed), IBKR scanner.
- `lib/realtime.py` — near-real-time intraday bars (Webull if
  `config/webull_creds.json`, else yfinance), `day_trade_conditions`,
  `premarket_movers`, `market_session`, `market_closed_reason`,
  `minutes_to_close`. Use this, not `data.py`, for day-trade signals.
- `lib/screener.py` — Yahoo/TradingView-style discovery: `market_scan`,
  `discover`, `dynamic_universe`, `oversold_reversion`, bar fallback.
- `lib/daytrade.py` — day-trade ranking (`scan`, `annotate`, `latest_headline`).
- `lib/indicators.py` — plain-pandas indicators (no pandas-ta: numba lacks
  Py 3.14), resistance/breakout, `level_ladder`, `entry_quality`,
  `reversion_quality`, stops (`ratchet_2bar_stop`, `day_trade_stop`,
  `noise_band`, `chart_target`).
- `lib/scalps.py` — the five 1-min scalp detectors + `detect_all`.
- `lib/sentiment.py` — `social_snapshot` (WSB / StockTwits / news VADER);
  context only, never a trigger.
- `lib/charts.py` — annotated `mplfinance` PNGs.
- `lib/journal.py` — markdown journal, SQLite trade log, monthly perf tracker.
- `lib/config.py` — load YAML/watchlist, `catastrophic_stop`, `position_size`.
- `lib/desk.py` — `desk_status()` (which routine is due, from the ET clock +
  `output/desk_runs.json`) and `record_run()`. No IBKR connection.
- `.claude/skills/<routine>/SKILL.md` — the five routines (procedure only).
- `.claude/agents/trading-desk.md` — the desk agent: runs the due routine,
  then schedules the next check. Run as the main session
  (`claude --agent trading-desk`). Background subagents can't ask questions or
  schedule wakeups, so approvals need it to be the main session.
- `output/` — charts per run (`output/charts/<date>/`), `journal.md`/`.db`
  (git-ignored).

## Conventions

- Every recommendation produces a saved chart, the rule that shortlisted it,
  and written reasoning, all recorded in the journal.
- `ib_async` is synchronous-style (it runs its own loop); follow the existing
  patterns in `lib/`, don't mix in raw asyncio.
- Setup and connection check: see [README](README.md#setup).
