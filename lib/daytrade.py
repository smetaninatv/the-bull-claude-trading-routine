"""Day-trade stock-selection signals + a combined scanner.

Complements the existing VWAP / dollar-volume / R:R / noise-band checks with the
metrics professional day traders actually use to *find* the right stock:

  - Relative Volume (RVOL) — today's pace vs the stock's own normal (the #1 scan metric)
  - ATR%               — does it have enough range to be worth trading
  - Gap %              — fresh catalyst / overnight move
  - Above VWAP         — buyers in control
  - Opening-Range Breakout (ORB) — classic momentum entry off the first 15 min
  - Relative Strength vs SPY — is it a leader today

Deliberately ignores RSI/MACD/Bollinger intraday — they lag on 5-min bars.

Data via yfinance (near-real-time, prepost where needed). Returns plain dicts so
callers/skills can rank and present without extra deps.
"""
import datetime
import math
import warnings

import pandas as pd
import pytz

warnings.filterwarnings("ignore")

from lib.indicators import (add_vwap, atr_pct, opening_range, is_orb_breakout,
                            level_ladder, inflexion_bias)

_ET = pytz.timezone("US/Eastern")


def relative_volume(today_cum_vol, avg_daily_vol, minutes_elapsed):
    """RVOL estimate: today's cumulative volume vs the stock's normal pace.

    Compares volume-so-far to what the stock *normally* trades by this point in
    the session (avg_daily_vol prorated by session fraction). >1.5 = active,
    >3 = hot (something is happening). Returns None if inputs are missing.
    """
    if not avg_daily_vol or not minutes_elapsed or minutes_elapsed <= 0:
        return None
    expected = avg_daily_vol * min(minutes_elapsed / 390.0, 1.0)
    return round(today_cum_vol / expected, 2) if expected else None


def relative_strength(sym_pct, spy_pct):
    """Outperformance vs SPY today, in percentage points. >0 = leader (up more /
    down less than the market); <0 = laggard."""
    if sym_pct is None or spy_pct is None:
        return None
    return round(sym_pct - spy_pct, 2)


def _intraday(sym, prepost=True):
    """Today's 5-min bars (tz-aware ET index) + a fast_info snapshot."""
    import yfinance as yf
    tk = yf.Ticker(sym)
    h = tk.history(period="1d", interval="5m", prepost=prepost)
    if h is None or h.empty:
        return None, None
    h.columns = [c.lower() for c in h.columns]
    h.index = pd.to_datetime(h.index)
    h.index = h.index.tz_convert(_ET) if h.index.tz is not None else h.index.tz_localize(_ET)
    try:
        fi = tk.fast_info
    except Exception:
        fi = None
    return h, fi


def scan(symbols, spy_pct=None, rth_only_for_or=True):
    """Score each symbol for day-trade quality. Returns a list of dicts sorted
    best-first. Each dict has the raw metrics + a 0-10 `score` and `notes`.

    `spy_pct` = SPY's % change today (for relative strength); if None it's fetched.
    """
    now = datetime.datetime.now(_ET)
    minutes_elapsed = max(0, (now - now.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60)

    if spy_pct is None:
        _, sfi = _intraday("SPY", prepost=True)
        if sfi is not None:
            try:
                spy_pct = (float(sfi.last_price) - float(sfi.previous_close)) / float(sfi.previous_close) * 100
            except Exception:
                spy_pct = None

    out = []
    for sym in symbols:
        try:
            h, fi = _intraday(sym, prepost=True)
            if h is None or fi is None:
                continue
            last = float(fi.last_price); prev = float(fi.previous_close)
            gap = (last - prev) / prev * 100 if prev else 0.0
            avg_vol = getattr(fi, "three_month_average_volume", None) or getattr(fi, "ten_day_average_volume", None)

            today = h[h.index.map(lambda t: t.date() == now.date())]
            rth = today[today.index.map(lambda t: t.time() >= datetime.time(9, 30))]
            cum_vol = float(rth["volume"].sum()) if not rth.empty else float(today["volume"].sum())
            rvol = relative_volume(cum_vol, avg_vol, minutes_elapsed)

            # DAILY ATR% = the stock's real day-trade "juice" (5-min ATR% is tiny/useless here)
            atrp = None; daily = None
            try:
                import yfinance as yf
                d = yf.Ticker(sym).history(period="3mo", interval="1d")
                if d is not None and not d.empty:
                    d.columns = [c.lower() for c in d.columns]
                    daily = d
                    atrp = atr_pct(d, 14) if len(d) >= 14 else atr_pct(d, max(2, len(d) - 1))
            except Exception:
                pass

            # VWAP / ORB on RTH bars
            above_vwap = None; orb = None; or_high = None
            vwap = None
            src = rth if not rth.empty else today
            if not src.empty and len(src) >= 2:
                sv = add_vwap(src)
                vwap = float(sv["vwap"].iloc[-1])
                above_vwap = last > vwap
                or_high, _ = opening_range(rth, 15) if not rth.empty else (None, None)
                orb = is_orb_breakout(src, or_high) if or_high else None

            rs = relative_strength(gap, spy_pct)

            # --- LEVEL LADDER + INFLEXION (game-plan-style S/R for real targets) ---
            # Source: multi-day 15-min bars — the granularity that reproduces a
            # hand-drawn game plan's intraday S/R (daily bars give S/R too macro
            # for a day trade; 2026-08-05 game-plan calibration). max_dist_pct
            # drops stale far levels.
            supports = []; resistances = []; next_res = None; near_sup = None
            bias = None; inflex = None; rr_to_res = None
            try:
                import yfinance as yf
                ld = yf.Ticker(sym).history(period="5d", interval="15m")
                if ld is not None and len(ld) >= 10:
                    ld.columns = [c.lower() for c in ld.columns]
                    sup, res = level_ladder(ld, lookback=200, wing=2, tolerance_pct=0.6,
                                            n=3, ref_price=last, max_dist_pct=8.0)
                    supports = [lv for lv, _ in sup]; resistances = [lv for lv, _ in res]
                    next_res = resistances[0] if resistances else None
                    near_sup = supports[0] if supports else None
            except Exception:
                pass
            # bias off the intraday VWAP when valid, else the prior daily close
            inflex = vwap if (vwap and not math.isnan(vwap)) else (
                float(daily["close"].iloc[-2]) if daily is not None and len(daily) >= 2 else last)
            if inflex is not None and not (isinstance(inflex, float) and math.isnan(inflex)):
                bias = "long" if last >= inflex else "short"
                inflex = round(float(inflex), 2)
            # R:R using structure: entry ~ nearest support (buy the pullback), stop
            # just under it, target = next resistance. The game-plan fix for the
            # too-close day-high target that made every R:R < 1.
            if next_res and near_sup and next_res > near_sup:
                entry = near_sup; stop = round(near_sup * 0.985, 2)
                if entry > stop:
                    rr_to_res = round((next_res - entry) / (entry - stop), 2)

            # --- composite score (0-10), interpretable ---
            score = 0; notes = []
            if rvol is not None:
                if rvol >= 3: score += 3; notes.append(f"RVOL {rvol} (hot)")
                elif rvol >= 1.5: score += 2; notes.append(f"RVOL {rvol} (active)")
                elif rvol >= 1: score += 1; notes.append(f"RVOL {rvol}")
                else: notes.append(f"RVOL {rvol} (quiet)")
            if atrp is not None:
                if 2 <= atrp <= 8: score += 2; notes.append(f"ATR% {atrp} (good range)")
                elif atrp > 8: score += 1; notes.append(f"ATR% {atrp} (wide/erratic)")
                else: notes.append(f"ATR% {atrp} (low juice)")
            if above_vwap: score += 2; notes.append("above VWAP")
            elif above_vwap is False: notes.append("below VWAP")
            if orb: score += 1; notes.append("ORB breakout")
            if rs is not None and rs > 0: score += 1; notes.append(f"RS +{rs} vs SPY")
            elif rs is not None: notes.append(f"RS {rs} vs SPY")
            if abs(gap) >= 2: score += 1; notes.append(f"gap {gap:+.1f}%")

            out.append({
                "sym": sym, "last": round(last, 2), "gap_pct": round(gap, 2),
                "rvol": rvol, "atr_pct": atrp, "above_vwap": above_vwap,
                "vwap": round(vwap, 2) if vwap else None,
                "or_high": or_high, "orb_breakout": orb, "rs_vs_spy": rs,
                "support": supports, "resistance": resistances,
                "next_res": next_res, "near_sup": near_sup,
                "bias": bias, "inflexion": inflex, "rr_to_res": rr_to_res,
                "score": min(10, score), "notes": ", ".join(notes),
            })
        except Exception:
            continue

    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def _latest_headline(sym, max_age_days=5):
    """Most recent news headline for `sym` (the likely driver) as
    {title, date, provider}, or None if there is no headline within
    `max_age_days`. yfinance nests it under content.title.

    Picks the *newest* dated item within the window (not just the first in the
    list) and drops anything older than the cutoff — so a stale article never
    gets surfaced as today's "catalyst" (the CHRN-April-on-a-July-run bug).
    Undated items are skipped (we can't vouch they're recent)."""
    import yfinance as yf
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    best = None
    try:
        news = yf.Ticker(sym).news or []
        for item in news:
            c = item.get("content") if isinstance(item, dict) else None
            if not isinstance(c, dict):
                continue
            title = c.get("title")
            if not title:
                continue
            try:
                dt = datetime.fromisoformat((c.get("pubDate") or "").replace("Z", "+00:00"))
            except ValueError:
                continue                       # undated -> can't confirm recency
            if dt < cutoff:
                continue
            if best is None or dt > best[0]:
                prov = c.get("provider")
                best = (dt, {
                    "title": title.strip(),
                    "date": dt.date().isoformat(),
                    "provider": prov.get("displayName") if isinstance(prov, dict) else None,
                })
    except Exception:
        pass
    return best[1] if best else None


def latest_headline(sym, max_age_days=5):
    """Public accessor for the most recent driver headline of ANY symbol —
    day OR swing name — so the report can show a catalyst per ticker, not just
    for the day-trade board. Returns {title, date, provider} or None."""
    return _latest_headline(sym, max_age_days=max_age_days)


def _pros_cons(r):
    """Objective, rule-derived pros/cons from a scan row (no opinion — just the
    metrics restated as for/against)."""
    pros, cons = [], []
    rvol, atrp, gap, rs = r.get("rvol"), r.get("atr_pct"), r.get("gap_pct"), r.get("rs_vs_spy")
    av, orb = r.get("above_vwap"), r.get("orb_breakout")
    if rvol is not None:
        if rvol >= 2: pros.append(f"RVOL {rvol} - real participation")
        elif rvol < 1: cons.append(f"RVOL {rvol} - quiet, no follow-through yet")
    if av: pros.append("above VWAP (buyers in control)")
    elif av is False: cons.append("below VWAP (sellers in control)")
    if orb: pros.append("broke opening range (momentum)")
    if rs is not None:
        if rs > 1: pros.append(f"leading SPY (+{rs})")
        elif rs < 0: cons.append(f"lagging SPY ({rs})")
    if atrp is not None:
        if 2 <= atrp <= 8: pros.append(f"ATR% {atrp} - tradeable range")
        elif atrp > 10: cons.append(f"ATR% {atrp} - erratic, wide stops")
        elif atrp < 2: cons.append(f"ATR% {atrp} - low juice, may not travel")
    if gap is not None and abs(gap) >= 2:
        (pros if gap > 0 else cons).append(f"gap {gap:+.1f}%")
    return pros, cons


def annotate(rows, top_n=8, with_news=True):
    """Enrich the top `top_n` scan rows with `pros`, `cons`, and (if with_news)
    a `driver` headline dict. Returns the same list (top rows mutated)."""
    for r in rows[:top_n]:
        r["pros"], r["cons"] = _pros_cons(r)
        if with_news:
            r["driver"] = _latest_headline(r["sym"])
            # high volume + no news = possible pump / unexplained move -> objective caution
            if (r.get("rvol") or 0) >= 5 and not r["driver"]:
                r["cons"].append("high RVOL but NO news (unexplained - pump risk)")
    return rows
