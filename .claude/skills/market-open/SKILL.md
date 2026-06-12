---
name: market-open
description: Market-open execution (~15:30 CET / 9:30 ET + first 30 min, Mon-Fri). Re-validates the pre-market candidates against opening price action, builds risk-checked entry orders with a hard catastrophic protective stop (no fixed target — the trailing stop runs the winner) WITHOUT transmitting, applies PDT and delayed-data guards, and presents each for explicit approval before sending to IBKR.
---

# Market-open execution routine

Goal: turn approved candidates into live orders — human-in-the-loop. **Never transmit without the user's explicit OK.**

## Steps

1. **Connect + data setup.**
   - Swing positions: `lib.ibkr.connect()`, `lib.data.set_delayed()` as usual — delayed data is fine for daily-bar swing entries.
   - Day trade positions: use `lib.realtime` (near-real-time, no subscription needed):
     ```python
     from lib.realtime import day_trade_conditions, get_intraday_bars
     dt = day_trade_conditions()
     ```
     If `dt["score"] < 5`: **do not open new day trades this session** — print the verdict and skip to swing-only processing. This replaces the old "warn and proceed anyway" behavior.
     If `dt["score"] >= 5`: proceed with day trade validation using `get_intraday_bars(sym, "5m")` for VWAP/volume checks instead of the 15-min delayed IBKR feed.
2. **Load this morning's candidates** from the journal / pre-market report. If none, say so and stop.
3. **Re-validate at the open against the user's rules.** Pull fresh intraday bars (`lib.data.intraday_bars(ib, sym, "1 D", "5 mins")`); `lib.indicators.add_vwap(df)` for day names, `add_indicators(df)` for swing. Confirm:
   - *Day:* all four checks must pass — drop and say why if any fail:
     1. **Above VWAP** — price > session VWAP.
     2. **Dollar volume** — `lib.indicators.dollar_volume_ok(df, min_dv=5_000_000, lookback=3)` (3-bar avg ≥ $5M/bar). Replaces the raw 70k share floor, which is meaningless for high-price names and passes junk cheap stocks.
     3. **EMA8 proximity** — price is within 2% of EMA8 (neither extended far above nor collapsed below). Flag but don't hard-drop if price is 2-4% below EMA8 — note it as "weak momentum."
     4. **R:R ≥ 2.0** (market-open / first 30 min) or **R:R ≥ 3.0** if running mid-day (> 60 min after open — thinner tape, need a wider edge). Check `lib.realtime.market_session()` and `datetime` to determine.
   - *Mid-day mode* (> 60 min after open): additionally require 3-bar avg dollar volume ≥ $8M (tighter than the base $5M — mid-day volume dries up and thin setups fail more often).
   - *Swing:* still **above 200 SMA**, 8 EMA momentum intact, and (for breakout entries) price holding above the broken resistance level rather than failing back under it **on expanding volume** (skip low-volume breakouts — they fail often).
   Drop invalidated names and say why.
4. **Check account state & limits.** `lib.ibkr.equity(ib)`, current `positions(ib)`. Enforce `risk.yaml` limits: max concurrent positions, daily loss limit (if breached, propose no new entries).
   - **PDT guard (day trades):** if `equity < limits.pdt_min_equity_usd` ($25k) AND `lib.ibkr.day_trades_remaining(ib)` is 0, **do not propose new day trades** — opening one risks a PDT flag/restriction. Warn and offer to hold as swing instead. (A negative/large value = unlimited; fine.)
5. **Size + build the entry order (un-transmitted).** For each surviving pick:
   - **Stop & sizing use the catastrophic floor** so risk-per-trade is real: `stop = lib.config.catastrophic_stop(entry)`; `qty = lib.config.position_size(entry, stop, style)`. (This is the hard floor that's always honored; the 2-bar trailing stop, managed by the exit scan, takes over the upside.)
   - **No fixed take-profit** — let the trailing stop run the winner:
     ```python
     contract, orders = lib.ibkr.prepare_entry(ib, sym, "BUY", qty, entry, stop)
     ```
     `orders` = [entry limit, protective stop], both `transmit=False`.
6. **Present for approval.** Show each proposed order: symbol, qty, entry, **catastrophic stop** (price + the % / $ it risks), % of account, and the chart. Note the exit is trailing (no fixed target). Ask which to send.
7. **Transmit only approved orders:** `lib.ibkr.transmit(ib, contract, orders)`. Log each `status="approved"` (→ `filled` when confirmed) via `lib.journal.log_trade(ts, ...)` with an ISO timestamp.
8. **Report** what was sent, what was skipped (and why — invalidated / PDT / limits), and resulting exposure.
