---
name: exit-scan
description: Hourly exit scan (~16:30-22:00 CET / each trading hour, Mon-Fri). Maintains a strict-UP ratcheting 2-bar trailing stop on every open position, AUTO-RAISING the resting stop order at IBKR as new 2-bar highs print (no approval — it only de-risks), and exits when the stop is hit, plus secondary safety exits. Position-closing exits still require human approval before transmitting.
---

# Hourly exit scan routine

Goal: maintain a **ratcheting 2-bar trailing stop** on each position and exit when it's hit — human-in-the-loop.

## Primary exit: ratcheting 2-bar stop (LONG)

- **Initial stop (set at entry):** lowest low of the past 2 bars.
- **Ratchet:** each time a bar makes a **new 2-bar high** (its high > the highs of the prior 2 bars), move the stop **up** to the lowest low of the past 2 bars.
- **Stop only moves UP, never down.** **Exit when price hits the stop** (`price <= stop`).

Recompute deterministically each scan with `lib.indicators.ratchet_2bar_stop(df, lookback, include_current)` — pass bars from a short lead-in before entry through now, so the full ratchet path replays without stored state. Timeframe by style (`strategy.exits`): **swing → daily**, **day → 5-min**. Lookback = `exits.trailing_lookback` (default 2). (Shorts: mirror with highs/`trailing_high_stop`.)

> **Window ambiguity to confirm with the user:** "lowest low of the past 2 bars" can mean the 2 bars *before* the new-high bar (`include_current=False`, default) or the 2 most recent bars *including* it (`include_current=True`, tighter). Default is the former, for consistency with the new-high trigger's "past 2 bars".

## Do NOT exit at a loss (hold underwater positions)

When `strategy.exits.no_exit_at_loss` is true (default), **never realize a loss** — hold and wait for recovery:
- An exit (trailing-stop hit OR a secondary safety exit) is acted on **only if the fill would be at breakeven or better**, i.e. current price **>= the position's average cost**.
- If price **< average cost**, the position is **underwater → HOLD**. Report it as "underwater, holding, waiting for recovery" with the distance back to breakeven. Do not propose a close.
- Consequently, **do not place/keep a resting stop below average cost** (it would fill at a loss). The resting stop is only activated once the ratcheted level reaches **>= average cost**.

**Exception — the catastrophic floor always holds.** The hard stop placed at entry (`lib.config.catastrophic_stop`, default −8% from entry, `risk.limits.catastrophic_stop_pct`) is **never removed and always rests at the broker**, even underwater. So "hold underwater" applies only **between the catastrophic floor and average cost**: small dips are tolerated, but a position can't lose more than the catastrophic %. This bounds the tail risk that `no_exit_at_loss` would otherwise leave open. (Use `lib.ibkr.portfolio(ib)` → `item.averageCost` / `item.unrealizedPNL` to judge profit vs loss.)

## Keep the stop resting at the broker

Because the laptop isn't always on, the protective stop must live as a **resting stop order at IBKR**, not just in this scan — so it protects the position between runs. Find the current resting stop via `lib.ibkr.open_orders(ib)`. The resting stop starts at the **catastrophic floor** (placed at entry) and never goes below it. Each scan: recompute the ratcheted 2-bar level; if it's **strictly higher** than the current resting stop **and >= average cost** (per the no-loss rule above), **AUTO-RAISE the stop order to the new level — no approval needed** (raising a protective stop only de-risks). **Never lower a stop.** If `price <= stop` and the exit would be at breakeven+, that's an exit: propose a close for approval (see below).

## Day trade exits

Day trades require intraday exit management — they use **5-min bars and a real-time price feed**, not daily bars. Two extra rules apply to day trades only:

- **Near-close alert:** if a day trade position is still open with **≤ 15 minutes to the 4 PM ET close** (`lib.realtime.minutes_to_close() <= 15`), flag it as `MUST CLOSE` immediately regardless of the ratchet stop. Prepare a market order with `transmit=False` and present it for approval. Carrying a day trade overnight is not allowed unless the user explicitly converts it to a swing.
- **Real-time bars:** for day trade stops, use `lib.realtime.get_intraday_bars(sym, "5m")` (Webull if configured, else yfinance near-real-time) instead of `lib.data.intraday_bars()` (15-min delayed).

## Day trade profit-lock (replaces catastrophic stop once triggered)

Once a day trade is in profit **and** above VWAP, replace the resting catastrophic stop with a tighter VWAP-trailing stop that locks in the gain:

**Trigger condition** (check each scan):
- `price > avg_cost` (position is profitable), AND
- `price > vwap + 0.5 × ATR` (meaningfully above VWAP, not just scraping it)

**Action when triggered:**
1. Compute `profit_lock = lib.indicators.vwap_trail_stop(df, buffer_atr_mult=0.5)` — this returns `VWAP − 0.5 × ATR`.
2. If `profit_lock > current_resting_stop` → **auto-raise the resting stop to `profit_lock`** (no approval — it only de-risks, same as a normal stop raise).
3. On subsequent scans, keep trailing: recompute `profit_lock` each time VWAP rises; raise the stop if the new level is strictly higher. **Never lower it.**

This ensures a profitable day trade cannot reverse back to a loss. The catastrophic stop (8% floor) remains the hard minimum; the profit-lock operates above it.

**Order verification (each scan):** Connectivity blips can silently cancel resting stop orders. At the start of each scan, cross-check the expected resting stop against `lib.ibkr.open_orders(ib)`. If the stop order is missing for an open position, **immediately flag it as `UNPROTECTED`** and re-place the stop at the last known level before doing anything else. Log the incident.

## Steps

1. **Connect** (`lib.ibkr.connect`) **& set delayed data**; load `lib.config.load_strategy()`.
   ```python
   from lib.realtime import get_intraday_bars, minutes_to_close
   ```
2. **Pull portfolio + open orders:** `lib.ibkr.portfolio(ib)` (gives avg cost + unrealized P&L per holding) and `lib.ibkr.open_orders(ib)` (existing resting stops). If flat, report it and stop. **Auto-add every held ticker to the watchlist** via `lib.config.add_to_watchlist([...])` so research covers them too.
   - **Order integrity check:** for every open position, verify a resting stop order exists. If a position has no resting stop, flag it as `UNPROTECTED` immediately, re-place the stop at the catastrophic floor (or last known profit-lock level if above cost), and report the re-placement before continuing.
3. **For each holding:**
   - Determine style (swing or day) from the journal / position tag.
   - Pull bars on the style's exit timeframe (with lead-in):
     - **Swing:** `lib.data.historical_bars(ib, sym, "15 D", "1 day")` (delayed OK for daily bars).
     - **Day:** `get_intraday_bars(sym, "5m")` — near-real-time via Webull/yfinance. Do NOT use `lib.data.intraday_bars()` for day trade exits.
   - Compute `stop = ratchet_2bar_stop(df, lookback, include_current)`; get current price; note `avg_cost` and unrealized P&L from the portfolio item.
   - **Underwater check (no_exit_at_loss):** if `price < avg_cost`, **HOLD** — no exit, no resting stop below cost. Report "underwater, holding."
   - Otherwise (breakeven+): **exit if `price <= stop`** (needs approval); else if the new stop is strictly above the resting stop **and >= avg_cost**, **auto-raise it now** (no approval). Report the stop level, the bars it came from, and the room above it.
4. **Secondary safety exits** — flag these too, but they are **also suppressed when underwater** if `no_exit_at_loss` is on (they'd realize a loss):
   - *Swing:* close **below the 200 SMA** = hard trend-break exit.
   - *Day:* **loses session VWAP**, or held near the close (don't carry day trades overnight unless intended).
   - **Daily loss limit** (`risk.yaml`): if the account is down past `daily_loss_limit_pct`, lean toward de-risking.
5. **Apply the two action types:**
   - **RAISE STOP** (new ratcheted level above the resting stop): **modify the stop order immediately, no approval** — it only de-risks. Log it to the journal.
   - **EXIT** (stop hit / safety exit): prepare a closing order with `transmit=False` and present it with P&L, the trigger, reasoning, and an updated chart (the chart's `stop=` line = the ratcheted level). **Requires approval.**
6. **Transmit closing orders only after the user approves** (`lib.ibkr.transmit`). Log exits via `lib.journal.log_trade(..., status="exited")` with an ISO timestamp + rationale.
7. **Report** a position table: `symbol | style | qty | avg cost | last | ratchet stop | room | unreal P&L | action (hold / underwater-hold / RAISED STOP / EXIT) | reason`. Stop-raises are shown as already done.

If nothing triggers, say so plainly and show each position's current ratcheted stop level — a quiet scan is a valid outcome.
