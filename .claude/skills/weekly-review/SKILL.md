---
name: weekly-review
description: Weekly review (~22:15 CET Friday / after US close). Aggregates the week's performance, rule hit-rate, best/worst trades, and process observations, then proposes concrete adjustments to the watchlist, rules, or risk settings for next week.
---

# Weekly review routine

Goal: turn a week of journal entries into lessons and concrete tweaks.

## Steps

1. **Connect** (for current account value / open positions) and pull the week's trades: `lib.journal.trades_since(<Monday 00:00 ISO>)`.
2. **Compute weekly stats:** number of trades, win rate, average win vs. average loss, expectancy, largest drawdown, total P&L ($ and %), day vs. swing breakdown.
3. **Rule performance.** Which shortlist rules produced winners vs. losers? Where did discretionary overrides help or hurt? Did risk limits ever bind?
4. **Best & worst trades** with charts — what was right/wrong about each, in hindsight.
5. **Propose adjustments** (do NOT auto-apply — recommend and let the user edit):
   - `config/watchlist.txt`: names to add/drop.
   - shortlist rules: thresholds to tighten/loosen.
   - `config/risk.yaml`: sizing or limit changes if warranted.
6. **Write the review** to the journal and **report** it with charts and a short "focus for next week" list.

Frame everything against the plan: process quality first, P&L second.
