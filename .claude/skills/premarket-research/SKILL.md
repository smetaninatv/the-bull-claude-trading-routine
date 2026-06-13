---
name: premarket-research
description: Pre-market research (~13:30 CET / 7:30 ET, Mon-Fri). Screens the watchlist + market for day and swing candidates, computes indicators, reads overnight news, and produces a ranked candidate list with charts, proposed entry/stop/target, and risk-checked sizing. Recommends only — places no orders.
---

# Pre-market research routine

Goal: hand the user a ranked, decision-ready shortlist of day- and swing-trade candidates with charts and reasoning, before the US open.

## Steps

1. **Connect & set delayed data.**
   ```python
   from lib.ibkr import connect
   from lib.data import set_delayed
   ib = connect(); set_delayed(ib)
   ```

1b. **Day trade viability check.** Run this before building the full candidate universe so you can skip day-trade analysis on bad days:
   ```python
   from lib.realtime import day_trade_conditions, premarket_movers, webull_configured
   dt = day_trade_conditions()
   print(f"Day trade score: {dt['score']}/10 — {dt['verdict']}")
   for note in dt['notes']:
       print(f"  {note}")
   ```
   - **Score < 5:** Skip day trade candidates entirely. Note the reason (VIX too high / bearish tape). Proceed with swing research only.
   - **Score >= 5:** Continue with day trade analysis.
   - **Pre-market movers:** call `premarket_movers(top_n=10)` and add any high-volume gapper symbols to the candidate universe (Webull if configured, else yfinance). These are the best day trade setups — fresh catalysts.
   - **Webull status:** `webull_configured()` returns True if `config/webull_creds.json` exists. Note in the report whether pre-market data came from Webull or yfinance fallback.

2. **Build the candidate universe.** Combine: (a) current **open positions** — `lib.ibkr.portfolio(ib)` — so you always research what you hold, persisting any new ones with `lib.config.add_to_watchlist([...])`; (b) `config/watchlist.txt` via `lib.config.load_watchlist()`; (c) **screener discovery** via Yahoo Finance (free, reliable): `lib.screener.discover(("gainers","most_active","growth_tech"), per=25)` — this is the primary discovery source since the IBKR scanner (`lib.data.scan`) tends to time out on this Gateway; try IBKR scan only as a bonus and skip gracefully; (d) **pre-market movers** from step 1b (Webull/yfinance) — these go straight to the day trade shortlist. Tag held names so their analysis covers managing the existing position, not just a fresh entry.
   - **Watch for degraded discovery.** `discover()` prints `[screener] WARN: ... DEGRADED` to stderr if every Yahoo screen returned 0 (API hiccup) — and individual screen failures print `[screener] WARN: screen '…' failed`. If you see these, the universe has quietly shrunk to **watchlist + holdings only**. **State this explicitly at the top of the report** ("⚠ market discovery unavailable this run — screened watchlist + holdings only") so a thin candidate list isn't mistaken for a quiet market. Don't silently proceed as if you scanned the whole market.
3. **Pull data + indicators** for each candidate.
   - **Swing:** daily bars via `lib.data.historical_bars(ib, sym, "1 Y", "1 day")` (or `lib.screener.yahoo_bars` as fallback) then `lib.indicators.add_indicators(df)`.
   - **Day trade:** use `lib.realtime.get_intraday_bars(sym, "5m")` — this is near-real-time (Webull if configured, else yfinance), replacing the 15-min delayed IBKR feed. Then `lib.indicators.add_vwap(df)`. Do **not** use `lib.data.intraday_bars()` for day trade decisions — it is delayed.
   - Load thresholds via `lib.config.load_strategy()`.
4. **Apply the user's rule shortlist (hybrid).** These are the user's rules — apply them, don't substitute generic ones. Tag each candidate `day` or `swing`.

   **Swing (daily chart):**
   - **Macro trend filter — 200 SMA:** only go long when price is above `sma200` (uptrend). Skip / treat as short-bias if below.
   - **Momentum/entry — 8 EMA:** favour names where price is holding above a rising `ema8`, or pulling back to the `ema8` and resuming — that's the entry trigger inside the larger 200 SMA trend.
   - **Resistance breakout:** use `lib.indicators.find_resistance(df, lookback, tolerance_pct, min_touches)` then `lib.indicators.is_breakout(df, level, confirm_pct)`. `find_resistance` now returns the **nearest relevant** tested level (within `max_dist_pct` of price), not the globally most-touched one — so it no longer hands back a stale level far below price (the old failure mode that made breakout flags useless). A `None` result is legitimate: a name in price discovery / clean uptrend has **no horizontal 2-touch ceiling** — don't force a breakout read, fall back to the 200 SMA + 8 EMA momentum rule. A fresh breakout (`is_breakout` True) of a real nearby level, while above the 200 SMA with 8 EMA momentum, is the strongest swing setup. Note the level and touch count in the rationale.

   **Day (5-min chart):**
   - **VWAP:** long bias when price is above session `vwap`; entries on a reclaim/hold of VWAP. Avoid longs trading below VWAP.
   - **Dollar-volume floor:** require `lib.indicators.dollar_volume_ok(df, min_dv=5_000_000)` (3-bar avg ≥ $5M/bar) instead of the raw 70k share floor — share counts mean different things for a $12 vs a $500 stock.
   - **Entry ZONE, not a single price.** Give a band (e.g. "$101.50–$102.20, VWAP reclaim/8-EMA pullback"), so a fast move within it still qualifies rather than missing on a 30¢ tick (CRWV 2026-06-12 lesson). Note the lowest realistic fill at the bottom of the zone.
   - **Target from intraday levels, NOT round analyst numbers.** Use prior-day high, premarket high, or a measured move off the opening range — these are where price actually reacts. AMD's $524 target was an analyst figure; the day topped at $521.69. On a trend day (`day_trade_conditions()["score"] >= 8`) note "no fixed target — trail" rather than a price.
   - **ATR noise band.** Compute `lib.indicators.noise_band(df, mult=1.5)` and surface it so we know up front how much room a stop needs. A stop tighter than the band will be shaken out by normal wiggle.
   - **Re-entry plan.** For each day candidate add a one-line re-entry note: "if stopped but still above VWAP on a trend day, re-enter on the VWAP reclaim." This pre-authorizes the exit-scan's post-stop re-entry scan.
5. **Add judgment.** Check overnight/pre-market news for each shortlisted name (use web search). Note catalysts, earnings, or red flags. Down-rank anything with event risk that conflicts with the setup.
5b. **Social sentiment overlay (contextual, never a trigger).** For the **shortlisted** names only (not the whole universe — keeps API calls light), call `lib.sentiment.social_snapshot(shortlist)`. It returns, per symbol: Reddit/WSB `mentions`/`rank`/24h deltas (ApeWisdom), StockTwits `bull_pct`/`messages`, a finance-VADER `news_score`, and a short `label`. Use it as a **tiebreaker and risk flag**, not a buy signal:
   - A `[!] crowded/late` flag (high WSB rank or a big mention spike **with** ≥70% bull) means the name is *already crowded* — treat as a caution on chasing, especially for a fresh breakout entry. Tighten or wait for a pullback rather than buying euphoria.
   - A `[!] neg catalyst` flag (news_score ≤ −0.3) is a red flag — reconcile with the setup before recommending.
   - Rising mentions + improving bull% on a name that *also* passes the technical rules is a mild confirmation. Sentiment alone never promotes a name onto the shortlist.
   - Degrades gracefully: if a source is down it returns blanks (`—`); do not block the routine on it.
6. **Propose levels & size.** For each pick set entry (a **zone** for day trades), stop (consider `lib.indicators.atr_stop`; for day trades the stop must clear `lib.indicators.noise_band(df, 1.5)`), target (intraday levels for day trades; "trail, no fixed target" on a trend day); size with `lib.config.position_size(entry, stop, style)`. **Verify `shares > 0` and `R:R` are real before presenting** — a 2026-06-11 run showed every candidate with `qty=0 R:R=0.0x` because sizing silently failed; never present a plan with zero/blank sizing, debug it instead. Respect `limits` in `risk.yaml` (max positions, daily loss state).
7. **Charts + journal.** For each pick: `lib.charts.save_chart(df, sym, date_str, entry=, stop=, target=)` and `lib.journal.append_note(...)` with the rule trigger + your reasoning + chart path. Use today's date string for `date_str`.
8. **Report** to the user in TWO tables. **Place no orders** — this routine is research only.

   **Table 1 — Pre-market snapshot** (this exact format; timestamp in market time / EST):

   ```
   PRE-MARKET DATA (as of HH:MM AM EST):
   Symbol | Price    | Change | Volume      | Key Levels        | Suggested Buy              | Social
   TSLA   | $245.30  | +2.1%  | High volume | R: $248, S: $242  | $242.50 (pullback to 8 EMA) | WSB #9, 64% bull, news mixed
   NVDA   | $892.50  | +3.8%  | Very high   | R: $900, S: $885  | $885.50 (breakout retest)   | WSB #5, 81% bull ⚠ crowded/late
   ```
   - **Price/Change** = last pre-market vs prior close. **Volume** = qualitative (Low/Average/High/Very high) vs the name's norm.
   - **Key Levels** = nearest Resistance (use `find_resistance`) and Support.
   - **Suggested Buy** = the proposed entry price + a 2–3 word reason (e.g. "breakout > R", "pullback to 8 EMA", "VWAP reclaim").
   - **Social** = the `label` from `lib.sentiment.social_snapshot` (step 5b); render the `[!]` flag as ⚠. Show `—` if no signal. Sentiment is context, not a recommendation.

   **Table 2 — Trade plan** (the actionable risk detail): `symbol | style | entry | stop | target | shares | risk$ | thesis (1 line) | chart path`.

Keep it tight: a handful of high-conviction names beats a long list.
