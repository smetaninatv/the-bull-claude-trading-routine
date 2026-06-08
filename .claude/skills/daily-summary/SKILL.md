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
4. **Write the summary** to the journal (`lib.journal.append_note`):
   - Account value start → end, day P&L ($ and %).
   - Trades opened (symbol, style, thesis) and closed (outcome vs. plan).
   - Did we follow the rules? Any rule violations or judgment calls worth noting.
   - Open positions carried overnight + their stops.
5. **Report** the summary to the user with chart paths.

Be honest about losers and process slips — that's what makes the weekly review useful.
