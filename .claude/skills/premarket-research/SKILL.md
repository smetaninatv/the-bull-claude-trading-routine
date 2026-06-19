---
name: premarket-research
description: Pre-market research (~13:30 CET / 7:30 ET, Mon-Fri). Screens the watchlist + market for day and swing candidates, computes indicators, reads overnight news, and produces a ranked candidate list with charts, proposed entry/stop/target, and risk-checked sizing. Recommends only — places no orders.
---

# Pre-market research routine

Goal: hand the user a ranked, decision-ready shortlist of day- and swing-trade candidates with charts and reasoning, before the US open.

## Steps

0. **Market-holiday / weekend guard — check FIRST, before anything else.**
   ```python
   from lib.realtime import market_session, is_market_holiday, next_trading_day
   sess = market_session()
   if sess == "holiday":
       hol = is_market_holiday()
       print(f"MARKET CLOSED today — {hol}. Next session: {next_trading_day():%a %b %d}.")
   ```
   If `market_session()` is `"holiday"`, the US market is **closed today** — **STOP the routine**. Tell the user plainly: "🛑 Market closed today — *<holiday>*; next session *<date>*." Do **NOT** run the scan, tape read, or candidate analysis: `day_trade_conditions()` returns score 0 on a holiday, and any yfinance "movers"/prices are **stale carry-over** that will mislead. (For scheduled auto-runs, also skip silently on a `"closed"` weekend.) Only continue when a real pre/open session is pending.

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

1c. **ALWAYS print the ranked day-trade scan board with drivers + pros/cons** (when score >= 5). Run `lib.daytrade.scan()` then `annotate()` — this is a required output every premarket run, not optional. `annotate()` attaches each top name's **driver headline** (the news/catalyst) and **objective, rule-derived pros/cons**:
   ```python
   from lib.daytrade import scan, annotate
   movers = [m["sym"] for m in premarket_movers(top_n=12)]
   liquid = ["NVDA","AMD","AVGO","MU","MRVL","SMCI","ARM","TSM","PLTR","META","TSLA","COIN","AMKR"]
   board = annotate(scan(list(dict.fromkeys(movers + liquid))), top_n=10)  # ranked + driver + pros/cons
   for r in board[:10]:
       d = r.get("driver")
       drv = f"[{d['date']}] {d['title']}" if d else "(no recent headline)"
       print(f"{r['sym']:6} score={r['score']:2} RVOL={r['rvol']} dATR%={r['atr_pct']} gap={r['gap_pct']:+.1f} >VWAP={r['above_vwap']} ORB={r['orb_breakout']}")
       print(f"   DRIVER: {drv}")
       print(f"   PROS: {'; '.join(r.get('pros', [])) or '-'}")
       print(f"   CONS: {'; '.join(r.get('cons', [])) or '-'}")
   ```
   Render as **Table 0 — Day-trade scan board** in the report (see step 8), with a **Driver** column and **Pros / Cons**. The driver is decisive context: it exposes when a gap is on a *dubious* catalyst (a "could rally 89%" pump, an analyst favoring a competitor, or **high RVOL with NO news = pump risk**) vs. a real one. Lead the shortlist with high-score names whose driver is legit AND that pass the entry gates. **Caveat:** premarket (before 9:30 ET) RVOL/gap/ATR%/RS/driver are meaningful but VWAP/ORB aren't formed yet — note that, and re-run `scan()+annotate()` in market-open for the live board.

2. **Build the candidate universe.** Combine: (a) current **open positions** — `lib.ibkr.portfolio(ib)` — so you always research what you hold, persisting any new ones with `lib.config.add_to_watchlist([...])`; (b) `config/watchlist.txt` via `lib.config.load_watchlist()`; (c) **screener discovery** via Yahoo Finance (free, reliable): `lib.screener.discover(("gainers","most_active","growth_tech"), per=25, market_scan_kwargs={"min_relvol": 0})` — this runs the **custom full-market relative-volume scan FIRST** (`market_scan`, TradingView-style: Price>$5, Mkt cap>$2B, Vol>1M, %change>3, sorted by % change), then unions the predefined Yahoo screens. This is the primary discovery source — `market_scan` is what surfaces broad-market movers the canned screens miss (the NTLA/XNDU/CHDN class of names that aren't on the watchlist). **Pass `market_scan_kwargs={"min_relvol": 0}` in pre-market** — relvol uses cumulative session volume, so before 9:30 ET it under-reports; lean on %change/gap pre-open and re-apply the relvol>2 cut at market-open. The IBKR scanner (`lib.data.scan`) tends to time out on this Gateway — try it only as a bonus and skip gracefully; (d) **pre-market movers** from step 1b (Webull/yfinance) — these go straight to the day trade shortlist. Tag held names so their analysis covers managing the existing position, not just a fresh entry.
   - **Inspect the rich `market_scan` rows directly** (not just symbols) when you want the relvol/%change/mktcap to rank by: `rows = lib.screener.market_scan(min_relvol=0, size=50)` returns `{symbol, price, pct_change, volume, avg_vol_3m, relvol, mktcap}` per name. Surface the top relvol/%change names that also clear the swing or day rules — this is the TradingView "Rel vol > 2" board, built in-house.
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
   - **Rank with `lib.daytrade.scan([...])`.** Scores each name 0-10 on **RVOL** (relative volume — the #1 day-trade selection metric), **daily ATR%** (juice, ~2-8%), **above-VWAP**, **Opening-Range Breakout**, **relative strength vs SPY**, and **gap%**. Premarket, RVOL/gap/ATR%/RS are meaningful; VWAP/ORB only firm up after the open (re-rank in market-open). Lead the day shortlist with the highest-RVOL leaders, not just the biggest gappers — RVOL tells you where the volume (and follow-through) actually is.
5. **Add judgment.** Check overnight/pre-market news for each shortlisted name (use web search). Note catalysts, earnings, or red flags. Down-rank anything with event risk that conflicts with the setup.
5b. **Social sentiment overlay (contextual, never a trigger).** For the **shortlisted** names only (not the whole universe — keeps API calls light), call `lib.sentiment.social_snapshot(shortlist)`. It returns, per symbol: Reddit/WSB `mentions`/`rank`/24h deltas (ApeWisdom), StockTwits `bull_pct`/`messages`, a finance-VADER `news_score`, and a short `label`. Use it as a **tiebreaker and risk flag**, not a buy signal:
   - A `[!] crowded/late` flag (high WSB rank or a big mention spike **with** ≥70% bull) means the name is *already crowded* — treat as a caution on chasing, especially for a fresh breakout entry. Tighten or wait for a pullback rather than buying euphoria.
   - A `[!] neg catalyst` flag (news_score ≤ −0.3) is a red flag — reconcile with the setup before recommending.
   - Rising mentions + improving bull% on a name that *also* passes the technical rules is a mild confirmation. Sentiment alone never promotes a name onto the shortlist.
   - Degrades gracefully: if a source is down it returns blanks (`—`); do not block the routine on it.
6. **Propose levels & size.** For each pick set entry (a **zone** for day trades), stop (consider `lib.indicators.atr_stop`; for day trades the stop must clear `lib.indicators.noise_band(df, 1.5)`), target (intraday levels for day trades; "trail, no fixed target" on a trend day); size with `lib.config.position_size(entry, stop, style)`. **Verify `shares > 0` and `R:R` are real before presenting** — a 2026-06-11 run showed every candidate with `qty=0 R:R=0.0x` because sizing silently failed; never present a plan with zero/blank sizing, debug it instead. Respect `limits` in `risk.yaml` (max positions, daily loss state).
7. **Charts + journal.** For each pick: `lib.charts.save_chart(df, sym, date_str, entry=, stop=, target=)` and `lib.journal.append_note(...)` with the rule trigger + your reasoning + chart path. Use today's date string for `date_str`.
8. **Report** to the user. **Place no orders** — this routine is research only. Lead with the scan board, then the two detail tables.

   **Table 0 — Day-trade scan board** (from step 1c, when score >= 5; always include it): `symbol | score (0-10) | RVOL | daily ATR% | gap% | >VWAP | ORB | RS vs SPY | Driver (news headline + date) | Pros | Cons`, sorted best-first, top ~10 names. This is the "where's the action today AND why" board — the day shortlist comes off the top of it, but the **Driver** column is a veto: a high score on a pump/dubious-catalyst name (e.g. high RVOL + no news) is a pass, not a buy.

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
