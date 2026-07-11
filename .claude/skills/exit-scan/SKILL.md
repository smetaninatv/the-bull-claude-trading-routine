---
name: exit-scan
description: Exit scan — hourly for swing positions, every 5–15 min for day trades (Mon-Fri). Maintains a strict-UP ratcheting 2-bar trailing stop on every open position, AUTO-RAISING the resting stop order at IBKR as new 2-bar highs print (no approval — it only de-risks), and exits when the stop is hit, plus secondary safety exits. Runs two ways: an unattended every-15-min job (scripts/auto_exit_scan.py) that auto-raises stops AND auto-closes at breakeven-or-better without approval (never at a loss, underwater names untouched), and this manual skill for the richer/discretionary work. Loss-making closes, target raises, and entry cancels are never automated — they stay human-in-the-loop.
---

# Exit scan routine

Goal: maintain a **ratcheting 2-bar trailing stop** on each position and exit when it's hit.

> **Two ways this runs (user, 2026-07-11):**
> 1. **Unattended (`scripts/auto_exit_scan.py --live`, Task Scheduler every 15 min in RTH).** The **de-risk-only slice**, authorized to act WITHOUT approval: it (a) raises/places each *profitable* position's protective stop to the 2-bar ratchet, and (b) auto-**closes** on a secondary trigger (daily close below the 200 SMA) — but **only at breakeven-or-better** (`price ≥ avg cost`), so it can never realize a loss and never touches an underwater name. Before any market close it cancels every resting SELL for the symbol and re-reads to confirm they're gone; if one can't be cancelled it does **not** sell (orphan-short risk) and notifies for manual action. The **primary** 2-bar-stop exit is executed by the resting STP at IBKR itself (exact price, between runs). Log: `output/auto_exit_scan.log`.
> 2. **Manual (this skill).** The full, judgment-rich scan — day-trade 5-min management, VWAP-trail floor, re-entry scan, target-raise suggestions, entry-order re-validation, charts, and the loss-making / discretionary exits the unattended job deliberately won't take (closing below cost is never automated). Run it when present for anything beyond the mechanical de-risk.
>
> Both share the same rules below; the unattended job is just the subset that only ever de-risks. **Closing a position at a loss, raising a target, or cancelling an entry is NEVER automated — those stay human-in-the-loop.**

> **Market-holiday guard — check FIRST.** Call `lib.realtime.market_session()`. If it returns `"holiday"`, the US market is **closed today** — there is no live tape and no bars print, so the ratchet/scan has nothing to do. Note "🛑 Market closed — *<holiday>*; resting stops stay in place, next session *<next_trading_day>*" and **skip the scan** (your protective stops still rest at IBKR and fire on the next session). Resume normal cadence on the next trading day.

## Cadence — how often to run

The real downside protection is the **stop order resting at IBKR**, which fires instantly between scans regardless of when this runs. The scan's job is to **raise** that stop (ratchet up), detect re-entry, and run safety exits — so cadence controls how *current* the trailed stop is, not whether you're protected.

| Situation | Run every | Why |
|-----------|-----------|-----|
| **Day trade — active runner on a trend day** | **5 min** | Matches the 5-min bar close (can't ratchet faster than bars print); catches a silently-cancelled stop fast; keeps prompt cache warm (5-min TTL) |
| **Day trade — consolidating / flat** | 10–15 min | Resting stop protects; nothing actionable between bars |
| **Day trade — last 30 min to close** | 5 min + a hard check at T-15 | Enforce the MUST-CLOSE rule; no overnight day trades |
| **Swing positions (daily bars)** | hourly (the scheduled cadence) | Daily-bar ratchet only changes once per day; hourly is ample |

Scanning a day trade *faster* than ~5 min buys nothing — the 2-bar ratchet level only changes when a new 5-min bar closes. Schedule the next run via `ScheduleWakeup` at the cadence above based on what's open; if only swing positions are held, hourly is fine.

**Data source for day-trade signals:** `lib.realtime.get_intraday_bars(sym, "5m")` — yfinance, **~1 min lag** (near-real-time), or Webull tick-level if `config/webull_creds.json` is configured. The **last bar is still forming**, so the ratchet uses completed bars (`include_current=False`). Do NOT use the IBKR feed for day-trade signals — it is 15-min delayed.

## Primary exit: ratcheting 2-bar stop (LONG)

- **Initial stop (set at entry):** lowest low of the past 2 bars.
- **Ratchet:** each time a bar makes a **new 2-bar high** (its high > the highs of the prior 2 bars), move the stop **up** to the lowest low of the past 2 bars.
- **Stop only moves UP, never down.** **Exit when price hits the stop** (`price <= stop`).

Recompute deterministically each scan with `lib.indicators.ratchet_2bar_stop(df, lookback, include_current)` — pass bars from a short lead-in before entry through now, so the full ratchet path replays without stored state. Timeframe by style (`strategy.exits`): **swing → daily**, **day → 5-min**. Lookback = `exits.trailing_lookback` (default 2). (Strategy is long-only; a short would mirror the ratchet with highs.)

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

## Day trade trailing stop (2-bar ratchet primary, VWAP-trail as floor)

**Lesson — AMD 2026-06-12:** the VWAP-trail was the *worst*-performing stop that day (it exited at the bottom of a flush that immediately reversed and ran +$10 higher). The 2-bar ratchet was the best. So for day trades the **2-bar ratchet leads**; the VWAP-trail is only a floor beneath it.

**Primary stop:** `lib.indicators.day_trade_stop(df, current_resting_stop=resting, vwap_buffer_atr=1.5)` on 5-min bars (call `add_vwap(df)` first). This returns `max(2bar_ratchet, vwap_floor, current_resting_stop)` — the ratchet adapts to structure, the VWAP floor (`VWAP − 1.5×ATR`, widened from 0.5×) only lifts the stop when the ratchet is below it, and it never lowers an existing stop (up-only).

**When to activate the resting stop:** only once the stop level is `>= avg_cost` (no_exit_at_loss — never rest a stop that would fill at a loss). Until then the catastrophic floor (−8%) is the only resting stop. Once `day_trade_stop(...) >= avg_cost`, **auto-raise the resting stop to it each scan** (no approval — it only de-risks). The 8% floor remains the hard minimum beneath everything.

**Post-stop re-entry scan (trend days only, score ≥ 8):** When a day-trade stop fires but the day is still a trend day, do **not** consider the name done. If within 60 minutes the stock **reclaims and holds session VWAP**, flag a potential re-entry (the original thesis is intact; the stop caught a shakeout, not a reversal). AMD flushed to $505.67 post-stop, reclaimed VWAP, and ran to $521.69 — a re-entry we missed. Present the re-entry as a new candidate (transmit=False, needs approval); never auto-enter.

**Round-number guard:** never set or raise a stop to a psychological round number that sits inside the current consolidation band. Use the `day_trade_stop` level (structure + ATR), not a hand-picked round figure — $512 on AMD sat mid-range ($505–$515) and was always going to be hit by noise.

**Order verification (each scan):** Connectivity blips can silently cancel resting stop orders. At the start of each scan, cross-check the expected resting stop against `lib.ibkr.open_orders(ib)`. If the stop order is missing for an open position, **immediately flag it as `UNPROTECTED`** and re-place the stop at the last known level before doing anything else. Log the incident.

## Steps

1. **Connect** (`lib.ibkr.connect`) **& set delayed data**; load `lib.config.load_strategy()`.
   ```python
   from lib.realtime import get_intraday_bars, minutes_to_close
   ```
2. **Pull portfolio + open orders:** `lib.ibkr.portfolio(ib)` (gives avg cost + unrealized P&L per holding) and `lib.ibkr.open_orders(ib)` (existing resting stops). If flat, report it and stop. **Auto-add every held ticker to the watchlist** via `lib.config.add_to_watchlist([...])` so research covers them too.
   - **Order integrity check:** for every open position, verify a resting stop order exists. **Read orders via `lib.ibkr.open_orders(ib)` — NOT raw `ib.openTrades()`.** On this Gateway `openTrades()` returns empty if read before `reqAllOpenOrders()` populates; `open_orders()` requests, waits, and retries so an empty result is real (2026-06-12: a single empty read would have falsely flagged every position UNPROTECTED). Only if `open_orders()` still shows no stop for a position → flag `UNPROTECTED`, re-place the stop at the catastrophic floor (or last known profit-lock level if above cost), and report it before continuing.
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

4b. **Resting target proximity — flag "RAISE TARGET" before a winner caps out.** For any held position that has a **resting limit-sell target** (find it in `lib.ibkr.open_orders(ib)`): if the current price has recovered to **within ~1×ATR of that target** AND the trend/levels support more upside (price above 200 SMA / 8 EMA, or a documented analyst target sits above the resting limit), **flag `RAISE TARGET`** with a suggested higher level (next resistance via `lib.indicators.find_resistance`, or the higher analyst target). This is a **suggestion only** — selling higher is **not** de-risking, so unlike a stop-raise it **must NOT be auto-applied**; present it for approval before moving the order. The lesson: IBKR 2026-06-12 filled its $91 limit, then ran to $93.10 four minutes later — the target should have been raised first (analyst levels $93/$98 sat above $91). Skip the flag if price is far from the target or the trend has rolled over (just let it fill).

4c. **Unfilled resting entry orders — staleness, invalidation & weekend check.** Read `lib.ibkr.open_orders(ib)`; a resting entry is any open **BUY** order whose symbol is **not** in the portfolio (a pending entry, not a holding). For each, flag (suggestion only — **needs approval**, since cancelling/moving an entry is not de-risking):
   - **Setup invalidated intraday** → recommend CANCEL: price has broken the thesis (swing lost 200 SMA / 8 EMA; day lost VWAP), or has run far enough above the limit that a fill would only come on a pullback no longer wanted.
   - **Stale** → recommend CANCEL: resting unfilled longer than `orders.stale_entry_sessions` (default 3) with no sign of filling.
   - **Drifted** → recommend RE-ADJUST: setup intact but the limit/stop/target no longer reflect the right levels; propose new ones (move all bracket legs together).
   - **Pre-weekend (Friday, last scan before close)** → if `orders.cancel_unfilled_before_weekend` is true, flag every unfilled entry for a **keep/cancel decision** before the weekend — a Friday GTC limit can fill on a Monday gap into weakness with no human re-check, and correlated names can all fill at once (TSM/MRVL/CRWD 2026-06-12). Recommend cancelling the weak/extended ones; keep only the cleanest, highest-conviction setups.
   Never auto-cancel or auto-move an entry — present and act on approval. Cancel a prior-session order with the master client (`connect(client_id=0)`); the default client gets Error 10147 on another session's orders.

5. **Apply the action types:**
   - **RAISE STOP** (new ratcheted level above the resting stop): **modify the stop order immediately, no approval** — it only de-risks. Log it to the journal.
   - **RAISE TARGET** (price within ~1×ATR of a resting limit-sell and higher levels justify it): **suggest a higher target and ask** — do **not** move the order without approval (selling higher is not de-risking). On approval, cancel/replace the limit at the new level and log it.
   - **EXIT** (stop hit / safety exit): prepare a closing order with `transmit=False` and present it with P&L, the trigger, reasoning, and an updated chart (the chart's `stop=` line = the ratcheted level). **Requires approval.**
   - **CANCEL / RE-ADJUST ENTRY** (unfilled resting entry, from step 4c): recommend and **ask** — never auto-cancel or auto-move an entry (not de-risking). On approval, cancel (or replace) the order, using `connect(client_id=0)` for a prior-session order.
6. **Transmit closing orders, target raises, and entry cancels/re-adjusts only after the user approves** (`lib.ibkr.transmit` / `cancelOrder`). Log exits via `lib.journal.log_trade(..., status="exited")` with an ISO timestamp + rationale.
7. **Report** two tables. (a) **Positions:** `symbol | style | qty | avg cost | last | ratchet stop | room | resting target | unreal P&L | action (hold / underwater-hold / RAISED STOP / RAISE TARGET? / EXIT) | reason`. (b) **Unfilled entry orders:** `symbol | buy limit | last | gap | setup still valid? | action (keep / CANCEL? / RE-ADJUST?) | reason`. Stop-raises are shown as already done; everything else (target-raises, entry cancels/re-adjusts) is a pending suggestion awaiting approval.

If nothing triggers, say so plainly and show each position's current ratcheted stop level — a quiet scan is a valid outcome.
