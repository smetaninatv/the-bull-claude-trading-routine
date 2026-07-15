"""Technical indicators in plain pandas (no pandas-ta/numba — works on Py 3.14).

Operates on the OHLCV DataFrame returned by lib.data (lowercase columns:
open, high, low, close, volume).
"""
import pandas as pd


def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()


def sma(series, length):
    return series.rolling(length).mean()


def rsi(close, length=14):
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    # avg_loss == 0 (all gains) -> rs = inf -> RSI = 100, which is correct.
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def atr(high, low, close, length=14):
    """Average True Range (Wilder's smoothing)."""
    prev_close = close.shift()
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def add_indicators(df):
    """Add EMA(8), SMAs, RSI(14), MACD, and ATR(14). Returns a new DataFrame.

    Key strategy columns: sma200 (macro trend filter) and ema8 (short-term
    momentum / entry timing).
    """
    df = df.copy()
    df["ema8"] = ema(df["close"], 8)        # short-term momentum / entries
    df["sma20"] = sma(df["close"], 20)
    df["sma50"] = sma(df["close"], 50)
    df["sma200"] = sma(df["close"], 200)    # macro trend filter
    df["rsi"] = rsi(df["close"], 14)
    df["atr"] = atr(df["high"], df["low"], df["close"], 14)
    macd_line = ema(df["close"], 12) - ema(df["close"], 26)
    df["macd"] = macd_line
    df["macd_signal"] = ema(macd_line, 9)
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def entry_quality(df, extended_atr=4.0, pullback_atr=2.0):
    """WHEN to buy — separate a low-risk entry from an extended chase.

    Synthesizes how the consistent winners actually buy — Weinstein (Stage 2),
    Minervini (volatility contraction / VCP), O'Neil (buy at the pivot, NEVER
    extended). Our old screen rewarded "trending + breakout + momentum", which
    surfaces names that have ALREADY RUN (52-wk highs); this adds the missing
    question: *is now a good moment, or is it extended?*

      stage2      : close > a RISING 50 SMA and > 200 SMA (an uptrend that's advancing)
      ext_atr     : ATRs the price sits ABOVE the 20 SMA — the 'chase' gauge.
                    O'Neil's rule: far above the MA = extended = wait for a pullback.
      contraction : avg range of the last 5 bars / the prior 15 (<1 = coiling, VCP-like)
      buyable     : True only at a low-risk moment (pullback to the rising MA, or a
                    tight base near the pivot) WITHIN a Stage 2 trend.
      ideal_entry : the level to actually buy AT (just above the 20 SMA) — not the
                    current extended price.
      label       : BUYABLE_PULLBACK | BUYABLE_BASE | EXTENDED | NOT_STAGE2 | MID_TREND

    Expects add_indicators() columns (sma20/sma50/sma200/atr). Returns a dict.
    """
    last = df.iloc[-1]
    c = float(last["close"]); a = float(last["atr"]) or 1e-9
    ma20 = float(last["sma20"]); s50 = float(last["sma50"]); s200 = float(last["sma200"])
    s50_prev = float(df["sma50"].iloc[-11]) if len(df) > 11 else s50
    s200_prev = float(df["sma200"].iloc[-21]) if len(df) > 21 else s200
    stage2 = (c > s50) and (c > s200) and (s50 >= s50_prev) and (s200 >= s200_prev)

    ext_atr = (c - ma20) / a
    tr = (df["high"] - df["low"]).abs()
    recent = float(tr.tail(5).mean())
    prior = float(tr.iloc[-20:-5].mean()) if len(df) >= 20 else recent
    contraction = (recent / prior) if prior else 1.0
    ideal_entry = round(ma20 + 0.3 * a, 2)        # just above the rising MA = the low-risk buy

    if not stage2:
        label, buyable, reason = "NOT_STAGE2", False, \
            "not a Stage 2 uptrend (not above a rising 50/200 SMA) — skip, no trend behind it"
    elif ext_atr > extended_atr:
        label, buyable, reason = "EXTENDED", False, \
            f"{ext_atr:.1f} ATR above the 20 SMA — EXTENDED, do NOT chase; wait for a pullback to ~{ideal_entry}"
    elif ext_atr <= pullback_atr:
        label, buyable, reason = "BUYABLE_PULLBACK", True, \
            f"near the rising 20 SMA ({ext_atr:.1f} ATR above) — low-risk pullback entry, tight stop"
    elif contraction < 0.8:
        label, buyable, reason = "BUYABLE_BASE", True, \
            f"tight base (range contracting to {contraction:.2f}×) near the pivot — buyable on the breakout"
    else:
        label, buyable, reason = "MID_TREND", False, \
            f"in trend but mid-range ({ext_atr:.1f} ATR above MA, no contraction) — wait for a pullback or a tight pivot"

    return {"stage2": stage2, "ext_atr": round(ext_atr, 2), "contraction": round(contraction, 2),
            "buyable": buyable, "ideal_entry": ideal_entry, "label": label, "reason": reason}


def add_vwap(df):
    """Add session VWAP to an INTRADAY DataFrame (e.g. 5-min bars).

    VWAP resets each trading day. Needs a DatetimeIndex (or a 'date' column).
    Returns a new DataFrame with a 'vwap' column.
    """
    d = df.copy()
    if "date" in d.columns:
        d = d.set_index("date")
    d.index = pd.to_datetime(d.index)
    typical = (d["high"] + d["low"] + d["close"]) / 3
    day = d.index.normalize()
    cum_pv = (typical * d["volume"]).groupby(day).cumsum()
    cum_vol = d["volume"].groupby(day).cumsum()
    d["vwap"] = cum_pv / cum_vol
    return d


def atr_stop(df, entry, mult=2.0, side="long"):
    """Suggest an ATR-based stop price from the latest ATR."""
    last_atr = float(df["atr"].iloc[-1])
    return entry - mult * last_atr if side == "long" else entry + mult * last_atr


def intraday_atr(df, length=14):
    """ATR(length) computed from an intraday OHLC DataFrame. Convenience wrapper
    around atr() that takes the whole df. Returns the latest value as float."""
    return float(atr(df["high"], df["low"], df["close"], length).iloc[-1])


def noise_band(df, mult=1.5, length=14):
    """The ATR-based 'noise band' width = mult × ATR(length).

    A stop placed closer than this to entry will likely be triggered by normal
    intraday wiggle. Use it to (a) reject stops that sit inside the band, and
    (b) size the minimum stop distance. AMD 2026-06-12 lesson: a $2 stop on a
    name with $2.5 5-min ATR is inside the noise — it got shaken out before the
    real move. Default 1.5× = enough room for a routine pullback.
    """
    return round(mult * intraday_atr(df, length), 2)


def vwap_trail_stop(df, buffer_atr_mult=1.5):
    """VWAP-based trailing stop for day trades (long).

    Stop = VWAP - buffer_atr_mult × ATR(14). Requires a 'vwap' column
    (add via add_vwap). Use as a FLOOR beneath the 2-bar ratchet, not as the
    primary stop — the ratchet adapts to structure; this only holds while VWAP
    holds as support. Only moves up: caller must enforce the up-only constraint.

    Buffer default is 1.5×ATR (was 0.5×): AMD 2026-06-12 broke VWAP by 1.4×ATR
    on a flush that immediately reversed and ran higher — a 0.5× buffer exited
    at the worst point. 1.5× survives a normal VWAP undercut.
    """
    if "vwap" not in df.columns:
        raise ValueError("vwap_trail_stop: DataFrame has no 'vwap' column — call add_vwap() first")
    vwap_now = float(df["vwap"].iloc[-1])
    atr_val = float(atr(df["high"], df["low"], df["close"], 14).iloc[-1])
    return round(vwap_now - buffer_atr_mult * atr_val, 2)


def day_trade_stop(df, current_resting_stop=None, vwap_buffer_atr=1.5, lookback=2,
                   include_current=False):
    """Combined day-trade trailing stop (long). PRIMARY = 2-bar ratchet;
    VWAP-trail is only a FLOOR beneath it. Returns the stop to rest at IBKR.

    Logic (AMD 2026-06-12 retro):
      1. ratchet = strict-UP 2-bar ratchet (adapts to structure, best performer).
      2. floor   = vwap_trail_stop (VWAP − vwap_buffer_atr×ATR).
      3. candidate = max(ratchet, floor) — the ratchet leads; the VWAP floor only
         lifts the stop when the ratchet is somehow below it, never lowers it.
      4. Never lower an existing resting stop (up-only): the returned level is
         max(candidate, current_resting_stop).

    Requires a 'vwap' column (call add_vwap first). Pass bars from a short
    lead-in before entry through now, on 5-min timeframe.
    """
    ratchet = ratchet_2bar_stop(df, lookback=lookback, include_current=include_current)
    floor = vwap_trail_stop(df, buffer_atr_mult=vwap_buffer_atr)
    candidate = max(ratchet, floor)
    if current_resting_stop is not None:
        candidate = max(candidate, float(current_resting_stop))
    return round(candidate, 2)


def dollar_volume_ok(df, min_dv=5_000_000, lookback=3):
    """True if the rolling average dollar volume over the last `lookback` bars
    meets the minimum threshold.

    Prefer this over a raw share-count floor — 70k shares means $35M/bar for
    AMD ($500) but only $840k/bar for a $12 stock. $5M/bar filters both.
    """
    recent = df.tail(lookback)
    avg_dv = float((recent["close"] * recent["volume"]).mean())
    return avg_dv >= min_dv


def atr_pct(df, length=14):
    """ATR as a % of the latest close — the stock's intraday 'juice'.

    Day-trade names usually want >~2% to be worth the spread and give a trade
    room to move. Too low = it won't travel; too high (>~10%) = erratic, wide
    stops. Sweet spot ~2-8%.
    """
    a = float(atr(df["high"], df["low"], df["close"], length).iloc[-1])
    last = float(df["close"].iloc[-1])
    return round(a / last * 100, 2) if last else 0.0


def opening_range(df, minutes=15, session_open="09:30"):
    """High/low of the first `minutes` of the regular session (the Opening Range).

    df must be an INTRADAY DataFrame with a tz-aware DatetimeIndex (US/Eastern).
    Uses the most recent day in df. Returns (or_high, or_low); (None, None) if
    the opening-range window hasn't formed yet.
    """
    import datetime as _dt
    if df is None or df.empty:
        return None, None
    day = df.index[-1].date()
    oh, om = (int(x) for x in session_open.split(":"))
    start = _dt.time(oh, om)
    end_total = oh * 60 + om + minutes
    end = _dt.time(end_total // 60, end_total % 60)
    win = df[df.index.map(lambda t: t.date() == day and start <= t.time() < end)]
    if win.empty:
        return None, None
    return round(float(win["high"].max()), 2), round(float(win["low"].min()), 2)


def is_orb_breakout(df, or_high, confirm_pct=0.0):
    """True if the latest close has broken above the opening-range high (Opening
    Range Breakout) by an optional confirm buffer."""
    if or_high is None:
        return False
    return float(df["close"].iloc[-1]) > or_high * (1 + confirm_pct / 100.0)


def ratchet_2bar_stop(df, lookback=2, include_current=False):
    """Strict-UP ratcheting 2-bar trailing stop for a LONG. Returns the current
    stop level, recomputed deterministically from the bars (no stored state).

    Rules:
      - Initial stop = lowest low of the first `lookback` bars (entry).
      - For each later bar i, if it makes a NEW `lookback`-bar high
        (high[i] > max high of the prior `lookback` bars), move the stop up to
        the lowest low of the past `lookback` bars.
      - The stop only ever moves UP; a lower candidate is ignored.
      - Caller exits the position when price <= this level ("stop hit").

    Pass `df` as the bars from a short lead-in before entry through now, on the
    position's timeframe (swing=daily, day=5-min). `include_current` chooses
    whether the 2-bar low window includes the new-high bar itself (see note in
    the exit-scan skill — confirm the intended window with the user).
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(highs)
    if n == 0:
        raise ValueError("ratchet_2bar_stop: no bars")
    if n <= lookback:
        return float(lows[:n].min())

    stop = float(lows[:lookback].min())  # initial stop at entry
    for i in range(lookback, n):
        prior_high = highs[i - lookback:i].max()
        if highs[i] > prior_high:                       # new 2-bar high
            window = lows[i - lookback + 1:i + 1] if include_current else lows[i - lookback:i]
            candidate = float(window.min())             # lowest low of past 2 bars
            if candidate > stop:                        # only moves UP
                stop = candidate
    return stop


# --- Swing rule: resistance line tested >= N times, then breakout ----------

def find_resistance(df, lookback=60, tolerance_pct=1.0, min_touches=2,
                    wing=2, max_dist_pct=12.0, ref_price=None):
    """Nearest *relevant* horizontal resistance: a level tested >= min_touches
    times, clustered within tolerance_pct, restricted to within max_dist_pct of
    the current price, and the one CLOSEST to price is returned. (None, 0) if no
    qualifying level.

    Why "closest to price" and not "most touches": for a breakout setup the
    meaningful ceiling is the level price is pressing against or has just cleared
    — not a heavily-tested level far below that is really old support. The prior
    implementation returned the globally most-touched pivot, which on a trending
    name was a stale level far below price (breakout flags became useless — see
    journal 2026-06-08). This filters to relevant levels and picks the nearest.

    A swing-high pivot is a bar whose high is the max of the [-wing, +wing]
    window and which rose into that high (so a flat top counts as one touch, not
    one per bar). Levels within tolerance_pct of each other are one cluster;
    `touches` is the number of distinct pivots in the chosen cluster.
    `ref_price` defaults to the last close.
    """
    sub = df.tail(lookback).reset_index(drop=True)
    highs = sub["high"].to_numpy(dtype=float)
    n = len(highs)
    if n < 2 * wing + 1:
        return None, 0
    ref = float(ref_price) if ref_price is not None else float(sub["close"].iloc[-1])

    # Swing-high pivots: local maxima over the wing window, deduped on plateaus
    # by requiring a strict rise into the high.
    pivots = []
    for i in range(wing, n - wing):
        window = highs[i - wing:i + wing + 1]
        if highs[i] == window.max() and highs[i] > highs[i - 1]:
            pivots.append(highs[i])
    if not pivots:
        return None, 0

    # Cluster pivots within tolerance_pct -> (level_mean, touches)
    clusters, used = [], [False] * len(pivots)
    for a in range(len(pivots)):
        if used[a]:
            continue
        band = [pivots[a]]
        used[a] = True
        for b in range(a + 1, len(pivots)):
            if not used[b] and abs(pivots[b] - pivots[a]) / pivots[a] <= tolerance_pct / 100.0:
                band.append(pivots[b])
                used[b] = True
        clusters.append((sum(band) / len(band), len(band)))

    # Keep clusters tested enough AND within max_dist_pct of price; pick nearest.
    candidates = [(lvl, t) for (lvl, t) in clusters
                  if t >= min_touches and abs(lvl - ref) / ref <= max_dist_pct / 100.0]
    if not candidates:
        return None, 0
    level, touches = min(candidates, key=lambda lt: abs(lt[0] - ref))
    return round(level, 2), touches


def is_breakout(df, level, confirm_pct=0.0):
    """True if the latest close has broken above `level` (by confirm_pct buffer).

    Pair with find_resistance, which now returns the NEAREST tested level — so a
    True here means price has cleared the relevant ceiling (a real breakout/retest),
    not that it sits far above some stale old level.
    """
    if level is None:
        return False
    return float(df["close"].iloc[-1]) > level * (1 + confirm_pct / 100.0)


def chart_target(df, ref_price=None, lookback=60, min_gap_pct=0.5,
                 tolerance_pct=1.0, min_touches=2):
    """A profit target read off the CHART (never an external/analyst number).

    For a position now in profit, pick the next credible ceiling ABOVE the
    reference price, in priority order:
      1. the nearest tested horizontal resistance above price
         (`find_resistance`, >= min_touches pivots) — a level price actually
         reacts to;
      2. the highest swing high in the lookback window (the recent peak), if it
         sits above price;
      3. a measured extension `ref + 2*ATR` — the last-resort chart-derived level
         when price is already at/near its highs (in clean discovery, no ceiling).

    Returns (level, source_str). `level` is always strictly above `ref_price` by
    at least `min_gap_pct` so it is a usable resting limit-sell.
    """
    sub = df.tail(lookback)
    ref = float(ref_price) if ref_price is not None else float(sub["close"].iloc[-1])
    floor = ref * (1 + min_gap_pct / 100.0)

    # 1) nearest tested resistance above price (look further up than the default)
    lvl, touches = find_resistance(sub, lookback=lookback, tolerance_pct=tolerance_pct,
                                   min_touches=min_touches, max_dist_pct=25.0,
                                   ref_price=ref)
    if lvl is not None and lvl >= floor:
        return round(float(lvl), 2), f"resistance x{touches}"

    # 2) recent swing high (the peak we last printed)
    swing_high = float(sub["high"].max())
    if swing_high >= floor:
        return round(swing_high, 2), "swing high"

    # 3) measured extension off ATR (price is at/near highs, no ceiling overhead)
    return round(ref + 2.0 * atr(sub["high"], sub["low"], sub["close"]).iloc[-1], 2), "ATR extension"
