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


def vwap_trail_stop(df, buffer_atr_mult=0.5):
    """VWAP-based trailing stop for day trades (long).

    Stop = VWAP - buffer_atr_mult × ATR(14). Requires a 'vwap' column
    (add via add_vwap). Use once the position is in profit — it locks in
    a floor below the session anchor and trails up as VWAP rises.
    Only moves up: caller must enforce the up-only constraint.
    """
    if "vwap" not in df.columns:
        raise ValueError("vwap_trail_stop: DataFrame has no 'vwap' column — call add_vwap() first")
    vwap_now = float(df["vwap"].iloc[-1])
    atr_val = float(atr(df["high"], df["low"], df["close"], 14).iloc[-1])
    return round(vwap_now - buffer_atr_mult * atr_val, 2)


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

def find_resistance(df, lookback=60, tolerance_pct=1.0, min_touches=2):
    """Find a horizontal resistance level touched >= min_touches times.

    A 'touch' is a local swing high (a bar whose high >= its neighbours).
    Swing highs within tolerance_pct of each other are treated as the same
    level. Returns (level, touches); (None, 0) if no qualifying level.

    Heuristic starter implementation — tune via config/strategy.yaml.
    """
    highs = df["high"].tail(lookback).reset_index(drop=True)
    pivots = [highs[i] for i in range(1, len(highs) - 1)
              if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]]
    if not pivots:
        return None, 0
    best_level, best_touches = None, 0
    for p in pivots:
        band = [q for q in pivots if abs(q - p) / p <= tolerance_pct / 100.0]
        if len(band) > best_touches:
            best_touches, best_level = len(band), sum(band) / len(band)
    if best_touches >= min_touches:
        return best_level, best_touches
    return None, 0


def is_breakout(df, level, confirm_pct=0.0):
    """True if the latest close has broken above `level` (by confirm_pct buffer)."""
    if level is None:
        return False
    return float(df["close"].iloc[-1]) > level * (1 + confirm_pct / 100.0)
