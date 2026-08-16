# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**trading-routine** is a set of *Claude Routines* for day/swing trading US equities through **Interactive Brokers**. Each routine is a Claude Code skill that runs Python tools (via `ib_async`) to research, generate annotated charts with reasoning, and prepare orders. The system is **human-in-the-loop**: Claude prepares ready-to-send orders but transmits nothing until the user explicitly approves.

## Strategy

The full strategy (entries, sizing, exits) and a critical assessment live in **`docs/STRATEGY.md`** — read it before changing trading logic. Known open risk flagged there: `no_exit_at_loss` removes downside protection (user-mandated; documented with the trade-off).

## Core design decisions (do not change without asking the user)

- **Autonomy:** Suggest + approve. Orders are built with `transmit=False`; they are sent only after the user OKs each one. Never auto-transmit.
- **Broker:** IB Gateway + `ib_async`, **paper account first** (free, delayed data). This machine's Gateway API socket is on port `4001` (`lib.ibkr.DEFAULT_PORT`). The port does **not** determine paper vs live — the login does (paper = `DU…`, live = `U…`). `connect()` defaults to `require_paper=True` and refuses a live account unless explicitly overridden.
- **Decision style:** Hybrid — rules build the candidate shortlist and enforce risk limits; Claude adds judgment (news, context, chart read) before recommending.
- **Universe:** fully dynamic — discovered live every run via `lib.screener.dynamic_universe()` (full-market relative-volume scan + Yahoo screens + pre-market movers), unioned with current IBKR holdings. There is **no hardcoded watchlist**; `config/watchlist.txt` is an optional user pin list, empty by default (2026-08-10).
- **Risk:** fixed % risk per trade, sized off stop distance; see `config/risk.yaml`. Sizing: `shares = (account × risk%) ÷ |entry − stop|`, capped by `max_position_pct`.
- **Entry:** always use a limit order at the lowest realistic price — ATR-based pullback (0.5–1 ATR below close), 8 EMA pullback, or recent session low. Never enter at or above current market price.
- **Target:** always set a limit sell at the highest credible predicted price (highest plausible analyst target or key resistance). Use `prepare_bracket()`, not `prepare_entry()`.
- **Catastrophic stop:** optional (user-mandated, 2026-06-10). Always compute and offer it, but do not force it. Present as "include? yes/no" and build without it if user declines. Note open downside once when declining.
- **Runtime:** user's laptop, **not always on** → routines are **assisted/manual trigger** (user runs the skill when present), not unattended cron. Don't assume a job fired on time.
- **Market & timezone:** US equities (NYSE/Nasdaq, 9:30–16:00 ET). User is in **CET**. Anchor all schedule logic to US/Eastern and convert dynamically (US and EU DST shift on different dates).
- **Output:** annotated charts + written rationale saved per run under `output/charts/<date>/`, plus a running trade journal (`output/journal.md` / `.db`). No live-streaming UI — charts are snapshots taken at decision time.

## Schedule (local CET ≈ derived from US/Eastern)

| Routine | Skill | US Eastern | CET (approx) |
|---|---|---|---|
| Pre-market research | `/premarket-research` | 7:30 | ~13:30 |
| Market-open execution | `/market-open` | 9:30 (first 30m) | ~15:30 |
| Hourly exit scan | `/exit-scan` | 10:30–16:00 | ~16:30–22:00 |
| Daily summary | `/daily-summary` | after close | ~22:15 |
| Weekly review (Fri) | `/weekly-review` | after close | ~22:15 Fri |

## Architecture

- `lib/` — Python tools the skills call (importable package):
  - `ibkr.py` — connect, account value, positions, build bracket orders (un-transmitted), transmit after approval.
  - `data.py` — historical/intraday bars, watchlist loading, IBKR scanner screening.
  - `screener.py` — Yahoo Finance (`yfinance`) candidate discovery (gainers/most-active/growth-tech screens) + historical-data fallback. Primary discovery source since the IBKR scanner times out on this Gateway. Bars match `data.py` format; IBKR stays source of truth for positions/orders.
  - `realtime.py` — near-real-time intraday data for day trading: `get_intraday_bars(sym, "5m")` (Webull if configured, else yfinance), `day_trade_conditions()` (VIX + SPY/QQQ VWAP → score 0–10 + verdict), `premarket_movers()` (Webull/yfinance gappers), `market_session()`, `minutes_to_close()`. Optional Webull integration via `config/webull_creds.json` (see requirements.txt). Replaces the 15-min delayed IBKR feed for all day-trade signal work.
  - `indicators.py` — EMA(8)/SMAs/RSI/MACD/ATR/session-VWAP in plain pandas (no pandas-ta — numba lacks Py 3.14 support), plus `find_resistance`/`is_breakout` for the swing breakout rule, and `level_ladder`/`inflexion_bias` (game-plan-style S/R ladder + bias, 2026-08-05).
  - `scalps.py` — the five scalping-setup detectors (`rubber_band`, `fashionably_late`, `hitchhiker`, `backside`, `second_chance`) on 1-min bars; each returns mechanical entry/stop/target(s)/RR or None. `detect_all()` runs them all. Wired into premarket (watch), market-open (detect+place), exit-scan (setup-specific exits). Added 2026-08-10 from the scalping cheat sheets; still human-in-the-loop.
  - `charts.py` — annotated candlestick PNGs (indicator overlays + entry/exit markers) via `mplfinance`.
  - `journal.py` — append rationale to the markdown journal and log trades to SQLite.
  - `config.py` — load `watchlist.txt` and `risk.yaml`; position-sizing helper.
- `config/` — user-editable `watchlist.txt` and `risk.yaml`.
- `.claude/skills/<routine>/SKILL.md` — the five routines.
- `output/` — generated charts and journal (git-ignored).

## Setup / running

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then launch **IB Gateway** logged into the **paper** account with the API enabled (Configure → Settings → API → Enable ActiveX and Socket Clients; port 4002; trust 127.0.0.1). Run a routine by invoking its skill (e.g. `/premarket-research`).

Quick connection check:
```powershell
python -c "from lib.ibkr import connect; ib=connect(); print(ib.accountSummary()[:3]); ib.disconnect()"
```

## Conventions

- All order construction defaults to `transmit=False`. Approval → call `ibkr.transmit(...)`.
- Every buy/sell recommendation must produce: a saved chart, the rule trigger that shortlisted it, and Claude's written reasoning, all recorded in the journal.
- Sizing always goes through `config.position_size(...)` so risk limits and the position cap are enforced in one place.
- `ib_async` calls are synchronous-style (the library runs its own event loop); follow the existing pattern in `lib/` rather than mixing in raw asyncio.
