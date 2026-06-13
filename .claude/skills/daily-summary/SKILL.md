---
name: daily-summary
description: Daily summary (~22:15 CET / after US close, Mon-Fri). Reports the day's realized/unrealized P&L, what was opened and closed and why, updates the journal, and renders end-of-day charts of the day's trades.
---

# Daily summary routine

Goal: a clean end-of-day record and a short reflection.

## Steps

1. **Connect & set delayed data.** Pull current `positions(ib)` and `account_value(ib)`.
2. **Gather the day's activity** from the journal: `lib.journal.trades_since(<today 00:00 ISO>)`. Pair entries with exits to compute realized P&L; mark still-open positions with unrealized P&L.
3. **Render end-of-day charts** for each symbol traded today (`lib.data.historical_bars` daily + `intraday_bars`, then `lib.charts.save_chart`) showing where entries/exits landed.

4. **Exit retro — run this for EVERY position closed today (skip if no exits).** For each exit, reconstruct what happened and grade it honestly:
   - **Pull the full intraday path** (`lib.realtime.get_intraday_bars(sym, "5m")` for day trades; daily bars for swing) from a lead-in before entry through the close.
   - **Potential vs. actual.** Compute the *best achievable* exit (day high after entry for longs) and what we actually got. Replay the alternative stops to quantify what each would have returned:
     - `lib.indicators.ratchet_2bar_stop(window)` bar-by-bar (the systematic stop),
     - `lib.indicators.day_trade_stop(window, resting, vwap_buffer_atr=1.5)` (the live rule),
     - the actual hand-set stop/target we used.
     Build a table: approach | exit price | exit time | P&L. This shows money left on the table and whether our stop beat or lagged the rule.
   - **Did the planned target get hit?** Compare the day high to the target we set. If we set a fixed target and it wasn't reached, note by how much and whether a trailing stop would have done better.
   - **Mistakes + WHY.** State each mistake plainly and the cause (e.g. "stop on a round number inside the consolidation band → shaken out by noise"; "fixed limit target capped a trend-day winner"; "missed re-entry after the shakeout"; "latency between validating and placing lost the entry"). Distinguish process mistakes (fixable) from variance (unavoidable — a stop that was correct but unlucky).
   - **Lessons learned.** 1–3 concrete, reusable takeaways.
   - Write the retro into the journal alongside the summary.

   **If the retro surfaces a change worth making to a skill, the strategy, or the risk config — STOP and ASK the user before editing anything.** Present the proposed change, the evidence from today that motivates it, and the trade-off. Do **not** auto-apply strategy/skill/risk changes from the daily summary (unlike intraday stop-raises, these are not de-risking-only and need a human decision). Only proceed on explicit approval.

5. **Write the summary** to the journal (`lib.journal.append_note`):
   - Account value start → end, day P&L ($ and %).
   - Trades opened (symbol, style, thesis) and closed (outcome vs. plan).
   - The exit retro from step 4 (potential vs. actual, mistakes, lessons).
   - Did we follow the rules? Any rule violations or judgment calls worth noting.
   - Open positions carried overnight + their stops.
6. **Report** the summary to the user with chart paths. If step 4 proposed any skill/strategy/risk changes, surface them as an explicit ask at the end — list them and wait for the user's decision.

Be honest about losers and process slips — that's what makes the weekly review useful.
