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
- **Day trade profit-lock (new):** once a day trade is profitable AND price > VWAP + 0.5×ATR, the resting stop is raised from the catastrophic floor to `VWAP − 0.5×ATR`. This locks in a minimum gain and trails up automatically as VWAP rises. Auto-applied (no approval) — it only de-risks. Implemented in `lib.indicators.vwap_trail_stop()`.
- **Ratcheting 2-bar trailing stop (swing):** initial reference = lowest low of past 2 bars at entry; each new 2-bar high raises the stop to the 2-bar low; **only moves up**; exit when price hits it. For day trades the profit-lock takes precedence once triggered; the 2-bar ratchet applies to swing positions on daily bars.
- **Secondary safety exits:** swing close below 200 SMA; day loss of VWAP (suppressed while underwater per the rule below, but the catastrophic floor still applies).
- **`no_exit_at_loss: true`** — don't exit on small dips/normal stops while underwater (hold and wait for recovery) — BUT bounded by the catastrophic floor, so max loss per position is capped.

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

**🟡 Resistance detection is an unvalidated heuristic.** `find_resistance` clusters swing highs — reasonable, but unproven. *Suggestion:* eyeball its output against real charts during paper trading before trusting it.

**✅ Volume floor upgraded to dollar volume.** The raw 70k share floor is replaced by a 3-bar average dollar volume check (≥ $5M/bar at open, ≥ $8M mid-day). Implemented in `lib.indicators.dollar_volume_ok()`.

**🟡 Concentration / correlation.** 20% max single position and up to 5 swings with no sector limit means 5 correlated tech names = effectively one large bet. *Suggestion:* add a per-sector or correlation cap; 20% single-name is high.

**🟡 No slippage/commission assumption.** Breakout fills and market exits slip. *Suggestion:* model a few cents of slippage + commissions in paper results so live performance isn't a surprise.

### Bottom line
The skeleton is good and the discipline (journaling, review, approval, paper-first) is better than most retail setups. The critical risk-model flaw has been **fixed**: a hard catastrophic stop now caps per-position loss and sizing is done off it, so risk-per-trade is honest. PDT, delayed-data, fixed-target, and breakout-volume issues are also addressed. The remaining 🟡 items above (correlation cap, relative volume, slippage modeling) are tuning to refine during paper trading — not dangers.
