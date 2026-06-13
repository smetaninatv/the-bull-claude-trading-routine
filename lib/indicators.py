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


def dollar_volume_ok(df, min_dv=5_000_000, lookback=3):
    """True if the rolling average dollar volume over the last `lookback` bars
    meets the minimum threshold.

    Prefer this over a raw share-count floor — 70k shares means $35M/bar for
    AMD ($500) but only $840k/bar for a $12 stock. $5M/bar filters both.
    """
    recent = df.tail(lookback)
    avg_dv = float((recent["close"] * recent["volume"]).mean())
    return avg_dv >= min_dv


def trailing_low_stop(df, lookback=2, drop_forming=False):
    """2-bar (configurable) trailing stop for a LONG position.

    Stop level = the lowest low of the last `lookback` candles, recomputed each
    scan. In an uptrend it trails UP as higher candles print; on a pullback the
    2-bar low can tick down (it is not a strictly monotonic ratchet — that is
    inherent to a "last N candle lows" stop). Exit when price drops below it.

    drop_forming=True ignores the most recent (possibly still-forming) bar and
    trails off the last `lookback` *completed* candles instead.
    """
    lows = df["low"].iloc[:-1] if drop_forming else df["low"]
    return float(lows.tail(lookback).min())


def trailing_high_stop(df, lookback=2, drop_forming=False):
    """Mirror of trailing_low_stop for a SHORT position: highest high of the
    last `lookback` candles. Exit when price rises above it."""
    highs = df["high"].iloc[:-1] if drop_forming else df["high"]
    return float(highs.tail(lookback).max())


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
