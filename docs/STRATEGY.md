# Trading Strategy

> **Canonical source for the trading rules.** README and CLAUDE.md link here
> instead of restating rules; skills describe *procedure* and cite config keys.
> Numbers live in `config/risk.yaml` and `config/strategy.yaml` — this file
> names the keys, so update the YAML (not this doc) when tuning a threshold.

Long-only US equities. Three lenses find candidates; one sizing model and one
exit model manage them. Everything is human-approved except stop raises and
in-profit locks, which only de-risk.

---

## 1. Universe

- **Fully dynamic**, rebuilt every run by `lib.screener.dynamic_universe()`:
  full-market relative-volume scan (`market_scan`) + Yahoo screens (gainers /
  most-active / growth-tech) + pre-market movers.
- **Always unioned with current IBKR holdings.**
- `config/watchlist.txt` is an optional pin list, empty by default. No
  hardcoded core list anywhere.
- If discovery degrades, the screener prints `[screener] WARN`; the report must
  say "live market discovery unavailable — holdings only".

## 2. Entry lenses

### 2a. Swing momentum (daily bars)

1. **Entry-timing gate — `lib.indicators.entry_quality(df)`** (Stage-2 /
   Weinstein–Minervini–O'Neil). Only `buyable=True` labels are actionable:
   - `BUYABLE_PULLBACK` / `BUYABLE_BASE` → buy at `ideal_entry` (just above the
     rising 20 SMA / pivot), never at market.
   - `EXTENDED` / `MID_TREND` → watch-only, with the pullback level.
   - `NOT_STAGE2` → drop.
2. **Trend + momentum:** above the 200 SMA (`swing.trend_sma`), holding a
   rising 8 EMA (`swing.entry_ema`). `entry_quality` already implies this.
3. **Resistance breakout (strongest setup, optional):** `find_resistance`
   returns the *nearest* level tested ≥ `swing.resistance.min_touches` within
   `max_dist_pct`; `is_breakout` confirms. Requires **expanding volume** —
   low-volume breakouts are skipped. `None` is a valid answer (price discovery).

### 2b. Day trade (5-min bars, near-real-time feed)

**Gate:** `lib.realtime.day_trade_conditions()` score (VIX + SPY/QQQ vs VWAP).
Score < 5 → no day trades this session. Score ≥ `day.trend_day_score` (8) →
trend day (see exits).

A candidate must pass all of:

1. **Above session VWAP.**
2. **Dollar volume:** `dollar_volume_ok(df)` 3-bar average ≥
   `day.min_dollar_volume` (≥ `day.min_dollar_volume_midday` > 60 min after open).
3. **Near the 8 EMA:** within `day.ema8_max_below_pct`; more than 5% below → skip.
4. **R:R** ≥ `day.min_rr_open` in the first hour, ≥ `day.min_rr_midday` after.
5. **No chasing:** price ≤ VWAP + `day.max_entry_above_vwap_atr` × ATR.

**Ranking:** `lib.daytrade.scan()` scores 0–10 on RVOL (the #1 selector),
daily ATR% (~2–8% sweet spot), above-VWAP, opening-range breakout, relative
strength vs SPY and gap%. `annotate()` adds the news driver + pros/cons.
RSI/MACD are deliberately excluded intraday (they lag). High RVOL with **no
news** is a pump-risk veto, not a buy.

**Scalp setups** (`lib.scalps.detect_all` on 1-min bars, at the open):
`rubber_band`, `fashionably_late`, `hitchhiker`, `backside`, `second_chance`.
Each returns mechanical entry/stop/targets/RR and has its own exit rule
(see the exit-scan skill). Signals are candidates, never auto-orders.

### 2c. Dip-reversion (daily bars) — the book's realized edge

The momentum lenses structurally reject names that dropped hard (they are
`NOT_STAGE2`), yet most realized winners were mean-reversion recoveries.
`lib.screener.oversold_reversion()` screens Yahoo `day_losers` /
`most_shorted` + holdings through `lib.indicators.reversion_quality()`:

- **`BUYABLE_DIP`** — *dropped* (≥ 2.5 ATR below the 20 SMA, or ≥ 12% off the
  60-bar high) **and** *stabilizing* (RSI leaving ≤ 35, or a higher-low reversal
  bar). Entry on the reversal bar, stop under the last-3-bar swing low, target
  = nearest moving average ≥ 1.5R above (else 2R).
- **`OVERSOLD_WAIT`** — dropped, not turned yet. Watch.
- **`FALLING_KNIFE`** — close within 1% of the 20-day low. **Excluded.**
- `knife_gate_200`: `False` (default) also allows recoveries from below the
  200 SMA (higher beta); `True` restricts to dips inside an uptrend.

Primarily a swing setup; the same name can be day-traded on its intraday VWAP
reclaim (the `backside` / `rubber_band` scalps).

## 3. Entry price discipline (all lenses)

- **Always a non-marketable limit at the pullback level — never at or above
  the current price.** Day: VWAP + `day.entry_limit_vwap_buffer_atr` × ATR.
  Swing: `entry_quality().ideal_entry`. Dip: the reversal-bar level.
- Present day entries as a **zone**, and pre-build the order when the setup is
  validated so approval transmits instantly.
- A missed fill beats an overpaid one; never widen the limit to force a fill.

## 4. Sizing & risk (`config/risk.yaml`)

```
shares = min( account × risk% ÷ |entry − stop| ,  account × max_position% ÷ entry )
```

Always via `lib.config.position_size(entry, stop, style)` — never by hand.

| | Swing | Day |
|---|---|---|
| risk per trade | `swing_trading.risk_per_trade_pct` | `day_trading.risk_per_trade_pct` |
| capital cap per name | `limits.max_position_pct` | `day_trading.max_position_pct` (overrides the global cap) |
| max concurrent | `limits.max_concurrent_positions` | `day_trading.max_concurrent_positions` |

- Size off the stop that is **actually honored** — normally the catastrophic
  floor (§5), so risk-per-trade is real.
- **Daily loss limit** `limits.daily_loss_limit_pct`: no new entries once hit.
- **PDT:** below `limits.pdt_min_equity_usd` with 0 day trades remaining → no
  new day trades.
- The routines may also offer an **all-in size** (`min(AvailableFunds,
  BuyingPower) ÷ entry`) beside the risk-capped one; the loss-if-stopped figure
  must always be shown for both.

## 5. Exits

**Order shape.** Entries use `lib.ibkr.prepare_entry` = entry limit + attached
catastrophic stop, **no fixed take-profit** — the trailing stop runs the winner.
`prepare_bracket` / `prepare_scaled_bracket` are only for a deliberate target or
scale-out (half at 1R, trail the rest).

**Targets.** Come from the chart (resistance / level ladder / measured move),
not analyst price targets. On a trend day (score ≥ `day.trend_day_score`) no
fixed target at all. Analyst targets are context only, e.g. for a RAISE-TARGET
suggestion.

**Catastrophic stop** — `lib.config.catastrophic_stop(entry)` at
−`limits.catastrophic_stop_pct`. Always computed, placed by default, and used
for sizing. The user may decline it per trade; if so, build without it and
state once that downside is then unbounded.

**Trailing stops (up-only, never lowered):**
- *Swing:* 2-bar ratchet on daily bars (`ratchet_2bar_stop`, lookback
  `exits.trailing_lookback`, `include_current: false` = the 2 bars before the
  new-high bar). Hard exit on a daily close below the 200 SMA.
- *Day:* `day_trade_stop()` = max(2-bar ratchet on 5-min, VWAP −
  `exits.day_vwap_buffer_atr` × ATR, current resting stop). Initial stop must
  clear `noise_band(df, day.noise_band_atr_mult)`; never on a round number
  inside the consolidation band.
- Stop raises are applied automatically (de-risking).

**`no_exit_at_loss`** (`exits.no_exit_at_loss`, user-mandated). Underwater
positions are held — no trailing or safety exit below average cost, no resting
stop below cost — except the catastrophic floor, which still fires.

**In-profit auto-lock.** Once price > avg cost by ≥ 0.5×ATR
(`should_profit_lock`), replace loose orders with an OCA pair: ratchet stop
(never below cost, `profit_lock_stop`) + chart target (`chart_target`). Auto,
no approval.

**Day-trade specifics.** Flat by the close (MUST-CLOSE at T-15). On a trend
day, a stopped-out name that reclaims VWAP within 60 min is flagged for
re-entry (approval required).

**Unfilled entry orders** are re-validated at the open and in each exit scan:
cancel if invalidated or older than `orders.stale_entry_sessions`, re-adjust if
drifted, flag gap-throughs, and sweep all of them on Friday
(`orders.cancel_unfilled_before_weekend`). Always approval-only. Prior-session
orders are cancelled with `connect(client_id=0)`.

## 6. What needs approval

| Action | Approval? |
|---|---|
| New entry, close a position, raise a target, cancel/move an entry | **Yes** |
| Raise a stop, in-profit OCA lock, re-place a missing protective stop | No (de-risking only) |

## 7. Open issues

- **Gap risk:** a stop is not a guaranteed fill; overnight gaps can exceed the
  catastrophic %. IBKR stop orders only trigger in regular hours, so a stop
  does nothing during the extended sessions.
- **Overnight fills:** with `orders.outside_rth` and `orders.overnight` on,
  orders can fill in the overnight session, in thin liquidity. A GTC entry limit can fill at
  3 a.m. ET on a headline, hours before any routine sees it.
- **No correlation / sector cap:** five correlated tech names are effectively
  one bet, and day trades may deploy up to `day_trading.max_position_pct`.
- **No slippage / commission model** in paper results.
- **Dip board skews small-cap / high-beta**, so judgment on liquidity and
  catalyst still decides.

---

## Lessons log (why the rules look like this)

| Trade | Lesson → rule |
|---|---|
| — | `no_exit_at_loss` was unbounded → catastrophic floor + size off it |
| various | `find_resistance` returned stale far-below levels → nearest-level search |
| premarket run | Every candidate showed qty=0 → always verify shares > 0 before presenting |
| AMD | VWAP-trail exited at a flush bottom → 2-bar ratchet primary, VWAP floor widened 0.5→1.5 ATR; no fixed target on trend days; re-entry on VWAP reclaim |
| AMD $512 stop | Round number inside the range was shaken out → noise-band + no round numbers |
| CRWV | Valid entry lost to latency → pre-build orders at validation; entry as a zone |
| IBKR | $91 limit filled, ran to $93.10 → RAISE-TARGET proximity flag |
| TSM/MRVL/CRWD | Stale correlated entries filled together → unfilled-order re-validation + Friday sweep |
| Gateway | Single empty `openTrades()` read looked like "unprotected" → always use `open_orders()` |
| AMD | Marketable limit $5 above VWAP faded → non-marketable limit, `max_entry_above_vwap_atr` |
| COIN/SMCI/PTCT | Bought extended / non-Stage-2 names → `entry_quality` gate |
| BABA | Lock on a tiny gain put the stop 1¢ under market → 0.5×ATR lock threshold |
| book review | Winners were mean-reversion (CCL, COIN, NBIS, BABA, OKTA, RDDT/UBER) → dip-reversion lens |
