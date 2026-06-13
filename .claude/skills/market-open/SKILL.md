---
name: market-open
description: Market-open execution (~15:30 CET / 9:30 ET + first 30 min, Mon-Fri). First reviews existing holdings + resting orders at the open (stop integrity, raise-target proximity, gap-through alerts), then re-validates the pre-market candidates against opening price action, builds risk-checked entry orders with a hard catastrophic protective stop (no fixed target — the trailing stop runs the winner) WITHOUT transmitting, applies PDT and delayed-data guards, and presents each for explicit approval before sending to IBKR.
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
1b. **Review existing holdings & resting orders FIRST (before any new-entry work).** The open is when overnight gaps blow through resting limits and stops, and the first exit-scan may not run for ~an hour — so do a one-pass review here. Pull `lib.ibkr.portfolio(ib)` (avg cost + unrealized P&L) and `lib.ibkr.open_orders(ib)` (resting stops/targets). For each held position:
   - **Order integrity** — confirm the protective stop still rests at IBKR. If a position has no stop (overnight cancellation), flag `UNPROTECTED` and re-place it at the catastrophic floor (or last known level) immediately — same as the exit-scan integrity check.
   - **RAISE TARGET proximity** — if a resting limit-sell is **within ~1×ATR** of the (pre-market / opening) price AND trend/levels support more (above 200 SMA/8 EMA, or an analyst target sits above the resting limit), **flag `RAISE TARGET`** with a suggested higher level. Suggestion only — needs approval; selling higher is not de-risking. (IBKR 2026-06-12 filled its $91 limit one minute after the open, then ran to $93.10 — exactly this case.)
   - **Gap-through alert** — if a holding gapped near or through its stop or target overnight, surface it explicitly: a stop may fill at the open well past its level; a target may already be filling. Note it before processing new entries.
   - This is a *review*, not an auto-action (except re-placing a missing protective stop, which only de-risks). Carry these flags into the final report.

1c. **Re-validate UNFILLED resting entry orders (cancel / re-adjust / keep).** A resting entry is any open **BUY** order whose symbol is **not** in `positions()` — a pending entry, often left over from a prior session (the TSM/MRVL/CRWD 2026-06-12 case). Do NOT let a stale limit fill blindly. For each, re-run the entry rules against the open (same checks as a fresh candidate — swing: still > 200 SMA / > 8 EMA / breakout holding; day: above VWAP / dollar-vol / R:R) and decide:
   - **CANCEL (recommend, needs approval)** if the setup has **invalidated** (lost 200 SMA or 8 EMA, momentum rolled over, breakout failed back under the level), if it has been resting unfilled **longer than `orders.stale_entry_sessions`** (default 3) and the thesis is stale, or if price has run so far above the limit it would only fill on a pullback you no longer want.
   - **RE-ADJUST (recommend, needs approval)** if the setup is intact but the limit no longer reflects the right entry — the pullback level moved, ATR shifted, or resistance reset. Propose the new entry/stop/target (move all bracket legs together). Changing an entry is **not** de-risking, so it needs approval — never auto-move.
   - **GAP-THROUGH (urgent flag)** if price has gapped **below the buy limit** — a fill is imminent into weakness. Surface for keep/cancel **before** it fills.
   - **KEEP** if the setup is still valid and the limit still reflects the intended entry — say so and leave it.
   Cancelling/adjusting an unfilled entry has no P&L (no position yet), but it IS the user's decision — present recommendations and act only on approval. Cancel orders from a prior session with the master client (`connect(client_id=0)`) — the default client gets Error 10147 on another session's orders.

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
   - **ATR noise-band guard (day trades):** the *initial protective* stop must clear the noise band — `lib.indicators.noise_band(df, mult=1.5)` = 1.5×ATR. If the catastrophic floor is closer to entry than the band (rare for cheap names), widen the protective stop to `entry − noise_band(df)` so it isn't shaken out by normal wiggle. Never set a stop on a round number that sits inside the current consolidation range (AMD $512 lesson).
   - **No fixed take-profit — and on a trend day, no fixed target at all.** Let the trailing stop run the winner. If `day_trade_conditions()["score"] >= 8` (trend day), do **NOT** attach a fixed limit target — a fixed limit caps the winner and turns the trade binary (AMD $524 limit capped a move that ran to $521.69 then would have trailed higher). Use entry + protective stop only; the exit-scan's `day_trade_stop` runs the upside. (Below score 8, a single resistance-based limit target is acceptable.)
     ```python
     contract, orders = lib.ibkr.prepare_entry(ib, sym, "BUY", qty, entry, stop)
     ```
     `orders` = [entry limit, protective stop], both `transmit=False`.
   - **Pre-stage to cut latency:** build the order object **at the moment the candidate is validated**, before asking for approval — so approval → instant `transmit(...)`, with no second round of data-pull and order construction in between. CRWV 2026-06-12 was a valid $101.87 entry that bounced to $104 in the gap between validating it and being ready to place — the setup was lost to latency, not to a bad read.
6. **Present for approval.** Show each proposed order: symbol, qty, entry, **catastrophic stop** (price + the % / $ it risks), % of account, and the chart. Note the exit is trailing (no fixed target). Ask which to send.
7. **Transmit only approved orders:** `lib.ibkr.transmit(ib, contract, orders)`. Log each `status="approved"` (→ `filled` when confirmed) via `lib.journal.log_trade(ts, ...)` with an ISO timestamp.
8. **Report** in two parts: (a) the **holdings/orders review** from step 1b — each position's stop integrity, any `UNPROTECTED` re-placements done, `RAISE TARGET` suggestions awaiting approval, and gap-through alerts; and (b) **new entries** — what was sent, what was skipped (and why — invalidated / PDT / limits), and resulting exposure.
