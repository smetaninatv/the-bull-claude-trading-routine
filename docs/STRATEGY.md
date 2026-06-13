# Trading Strategy — Summary & Assessment

> Canonical description of the strategy the routines implement, plus an honest
> critique. Update this when the rules change. Referenced by `CLAUDE.md`.

## 1. Summary of the strategy

### Universe
- Core watchlist (`config/watchlist.txt`) + pre-market screener (top % gainers, most active).
- All currently-held tickers are always included (research + exit scan).

### Entry — Swing (daily chart)
1. **Macro trend filter — 200 SMA:** long only when price is above the 200 SMA.
2. **Momentum/entry — 8 EMA:** enter on price holding above a rising 8 EMA, or a pullback to the 8 EMA that resumes.
3. **Resistance breakout:** a horizontal level tested ≥ 2 times, then a breakout above it.

### Entry — Day (5-min chart)
1. **VWAP:** long bias when price is above session VWAP; entries on a reclaim/hold of VWAP.
2. **Dollar volume:** 3-bar average dollar volume ≥ $5M per 5-min bar (`price × volume`). Replaces the raw 70k share floor — 70k shares is a different thing for a $10 stock vs a $500 stock. Mid-day (> 60 min after open): raise threshold to $8M.
3. **EMA8 proximity:** price within 2% of EMA8. More than 2% below EMA8 = weakening momentum, flag but allow. More than 5% below = skip.
4. **R:R:** at least 2.0 at market-open; at least 3.0 mid-day (thinner tape, higher edge required).

### Position sizing & risk (`config/risk.yaml`)
- **Fixed fractional risk:** day 0.5% / swing 1.0% of account per trade, sized off stop distance: `shares = (account × risk%) / |entry − stop|`.
- **Max single position:** 20% of account (cap).
- **Max concurrent positions:** day 3 / swing 5.
- **Daily loss limit:** 3% → stop opening new trades for the day.

### Exits (`config/strategy.yaml`, `config/risk.yaml`)
- **Hard catastrophic stop (floor):** placed at entry at −`catastrophic_stop_pct` (default 8%) from entry; **always rests at the broker and is always honored**, even under `no_exit_at_loss`. Sizing is done off THIS stop, so risk-per-trade is real.
- **Day trade trailing stop (revised 2026-06-12):** the **2-bar ratchet is primary** (on 5-min bars); the VWAP-trail is only a **floor** beneath it. Combined in `lib.indicators.day_trade_stop()` = `max(2bar_ratchet, VWAP − 1.5×ATR, current_resting_stop)`, up-only. The resting stop activates once the level reaches `>= avg_cost`. *Why the change:* on AMD 2026-06-12 the VWAP-trail-as-primary (0.5×ATR buffer) exited at the bottom of a flush that immediately reversed +$10; the 2-bar ratchet held better and the buffer was widened to 1.5×ATR. The 8% catastrophic floor remains the hard minimum.
- **Post-stop re-entry (trend days, score ≥ 8):** after a day-trade stop fires, if the stock reclaims and holds VWAP within 60 min, re-entry is flagged (thesis intact; the stop caught a shakeout). Needs approval — never auto-entered.
- **No fixed target on a trend day:** when `day_trade_conditions` score ≥ 8, day entries carry **no fixed limit target** — the trailing stop runs the winner. A fixed limit caps the move and makes the trade binary (AMD $524 limit vs a $521.69 day high that would have trailed higher).
- **ATR noise-band stop placement:** initial protective stops must clear `1.5×ATR` (`lib.indicators.noise_band()`); never place a stop on a round number inside the consolidation band (AMD $512 sat mid-range and was shaken out).
- **Ratcheting 2-bar trailing stop (swing):** initial reference = lowest low of past 2 bars at entry; each new 2-bar high raises the stop to the 2-bar low; **only moves up**; exit when price hits it. Applies to swing positions on daily bars.
- **Secondary safety exits:** swing close below 200 SMA; day loss of VWAP (suppressed while underwater per the rule below, but the catastrophic floor still applies).
- **`no_exit_at_loss: true`** — don't exit on small dips/normal stops while underwater (hold and wait for recovery) — BUT bounded by the catastrophic floor, so max loss per position is capped.

### Unfilled resting entry orders (added 2026-06-12)
A resting **BUY** order whose symbol isn't yet a position is a *pending entry* — it must be re-validated, not left to fill blindly off a stale limit. **market-open** (step 1c) and **exit-scan** (step 4c) both check them: **CANCEL** if the setup invalidated (lost 200 SMA / 8 EMA / VWAP) or it's stale (`orders.stale_entry_sessions`, default 3); **RE-ADJUST** if the setup holds but the level drifted; **GAP-THROUGH** flag if price gapped below the limit (imminent fill into weakness); and a **pre-weekend** sweep (`orders.cancel_unfilled_before_weekend`) that flags every unfilled entry Friday so a Monday gap can't fill a stale limit — especially correlated names filling at once (TSM/MRVL/CRWD 2026-06-12). All are **suggestions needing approval** — cancelling/moving an entry is not de-risking, so it's never automatic. Prior-session orders are cancelled with the master client (`connect(client_id=0)`; default client gets Error 10147).

### Execution model
- IB Gateway + `ib_async`, paper-first. **Human-in-the-loop:** orders prepared, transmitted only on approval (exception: protective stop-raises auto-apply).
- Assisted-trigger (laptop not always on). 5 scheduled routines (CET): pre-market research, market-open, hourly exit scan, daily summary, weekly review.
- Annotated charts + trade journal for every decision.

---

## 2. Assessment — is it good or bad?

**Overall: a sound, conventional trend-following core wrapped around one rule that undermines the whole risk model.** The entry logic, sizing math, and process discipline are solid and follow recognized best practice. The `no_exit_at_loss` rule is the serious problem — it converts a defined-risk system into an undefined-risk one. Fix that and this is a reasonable retail swing/day framework.

### What's good (keep)
- **Trend-aligned entries.** The 200 SMA filter + 8 EMA timing is a legitimate, widely-used combination — trade with the macro trend, time with short-term momentum.
- **Fixed-fractional position sizing.** Sizing off stop distance so each trade risks a constant % is textbook risk management.
- **Let winners run via a trailing stop.** The ratcheting 2-bar stop is a clean way to ride trends and not cap upside.
- **Circuit breaker.** A daily loss limit is a good behavioral guardrail.
- **Process: journaling, weekly review, paper-first, human approval.** These are exactly the habits that separate disciplined traders from gamblers.

### Critical/high issues — STATUS: FIXED (2026-06-07)

**🔴→✅ `no_exit_at_loss` unbounded risk — FIXED with a hard catastrophic floor.**
A **catastrophic stop** (`risk.limits.catastrophic_stop_pct`, default −8%) is now placed at entry, always rests at the broker, and is always honored even underwater. Crucially, **position sizing is now done off this floor** (`position_size(entry, catastrophic_stop(entry), …)`), so risk-per-trade is real (verified: 1% swing risk = exactly 1.00% of account), not fictional. `no_exit_at_loss` now only means "tolerate small dips between the floor and breakeven," not "hold to zero." Tail risk is bounded. *(Gap risk past the stop still exists — a stop is not a guaranteed fill — but loss is now bounded in normal conditions.)*

**🟠→✅ PDT rule — FIXED.** Market-open now checks `equity < pdt_min_equity_usd` ($25k) and `day_trades_remaining(ib)`; blocks/​warns on new day trades when restricted (`lib.ibkr.day_trades_remaining`).

**🟠→✅ Delayed data for day trading — FIXED (guard/warn).** Market-open warns that day-trade signals are stale on delayed data and treats them as paper-only until a real-time feed is active. Swing on delayed remains fine.

**🟠→✅ Fixed take-profit vs. trailing conflict — FIXED.** Entries now use `prepare_entry` (entry + protective stop, **no fixed target**); the trailing stop governs the upside. `prepare_bracket` remains only for a deliberate partial scale-out.

**🟡→✅ Swing breakout volume confirmation — FIXED.** Market-open now requires expanding volume on the breakout; low-volume breakouts are skipped.

### Remaining lower-priority items (tuning, not dangerous)

**✅ Resistance detection fixed (2026-06-12).** `find_resistance` previously returned the globally most-touched pivot, which on a trending name was a stale level far below price — making breakout flags useless (journal 2026-06-08). It now returns the **nearest relevant** tested level (within `max_dist_pct`, default 12% of price), uses wing-window pivots deduped on plateaus, and returns `None` honestly when a name in price discovery has no horizontal 2-touch ceiling. Verified against real data (KO/TSM/JNJ get sensible levels; AMD/NVDA at highs correctly return None).

**✅ Volume floor upgraded to dollar volume.** The raw 70k share floor is replaced by a 3-bar average dollar volume check (≥ $5M/bar at open, ≥ $8M mid-day). Implemented in `lib.indicators.dollar_volume_ok()`.

**🟡 Concentration / correlation.** 20% max single position and up to 5 swings with no sector limit means 5 correlated tech names = effectively one large bet. *Suggestion:* add a per-sector or correlation cap; 20% single-name is high.

**🟡 No slippage/commission assumption.** Breakout fills and market exits slip. *Suggestion:* model a few cents of slippage + commissions in paper results so live performance isn't a surprise.

**✅ Discovery degradation now visible (2026-06-12).** `lib.screener.yahoo_screen`/`discover` emit `[screener] WARN` to stderr when a Yahoo screen fails or all screens return empty — so a silent fallback to watchlist-only is caught. premarket-research surfaces it in the report ("⚠ market discovery unavailable — watchlist + holdings only") instead of implying a full-market scan.

### Bottom line
The skeleton is good and the discipline (journaling, review, approval, paper-first) is better than most retail setups. The critical risk-model flaw has been **fixed**: a hard catastrophic stop now caps per-position loss and sizing is done off it, so risk-per-trade is honest. PDT, delayed-data, fixed-target, and breakout-volume issues are also addressed. The remaining 🟡 items above (correlation cap, relative volume, slippage modeling) are tuning to refine during paper trading — not dangers.
