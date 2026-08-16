"""Scalping-setup detectors (long-biased; each notes the short mirror).

Implements the mechanical entry/stop/target rules from the five scalping
cheat sheets (2026-08-10). Each detector takes intraday 1-minute bars (with a
`vwap` column via lib.indicators.add_vwap) and returns a signal dict or None:

    {setup, side, entry, stop, targets:[...], rr, factors:[...], caveats:[...]}

These are INTRADAY scalp patterns — feed 1-min RTH bars. They complement (not
replace) the five day-trade gates: a scalp signal still gets sized via
config.position_size and is presented/placed human-in-the-loop. `detect_all`
runs every applicable detector and returns the signals found.

Rules are long-biased as written; the cheat sheets say each inverts exactly for
shorts. This module implements the long side (the strategy is long-only); short
mirrors are left as documented TODOs.
"""
import numpy as np

from lib.indicators import ema, intraday_atr


TICK = 0.02  # stops sit ".02 below" the reference level


def _rr_targets(entry, stop):
    r = entry - stop
    return r, [round(entry + r, 2), round(entry + 2 * r, 2)]


def rubber_band(df, atr_daily=None, rvol=None, lookback=2):
    """Rubber Band (mean-reversion snapback). LONG after an extended DOWN move.

    Trigger: the latest completed candle is green AND its high clears the highs of
    the prior `lookback` (2) candles — the "double-bar break" snapback. Best odds:
    RVOL>5, price >=3 daily-ATR below the open, snapback bar a top-5 volume bar.
    Stop .02 below LoD. Exit thirds: 1R / 2R / VWAP. (Short mirror: red candle
    breaking prior lows after an extended UP move.)
    """
    if df is None or len(df) < lookback + 2:
        return None
    o = df.iloc[-1]                       # latest completed bar (caller passes completed bars)
    highs = df["high"].to_numpy(dtype=float)
    lod = float(df["low"].min())
    open_px = float(df["open"].iloc[0])
    last = float(o["close"])
    vwap = float(o["vwap"]) if "vwap" in df else None

    green = float(o["close"]) > float(o["open"])
    prior_high = highs[-1 - lookback:-1].max()
    double_break = float(o["high"]) > prior_high
    if not (green and double_break):
        return None

    # extension check: prefer daily-ATR (>=3 ATR below open); else >=1 intraday-ATR below VWAP
    extended = False; ext_note = ""
    if atr_daily:
        extended = (open_px - lod) >= 3 * atr_daily
        ext_note = f"{(open_px-lod)/atr_daily:.1f} ATR below open"
    elif vwap is not None:
        ia = intraday_atr(df)
        extended = (vwap - lod) >= 1.5 * ia
        ext_note = f"{(vwap-lod)/ia:.1f}x intraday-ATR below VWAP"
    if not extended:
        return None

    entry = round(float(o["high"]), 2)   # break of the snapback bar's high
    stop = round(lod - TICK, 2)
    r, tgts = _rr_targets(entry, stop)
    if vwap and vwap > entry:
        tgts.append(round(vwap, 2))      # final third into VWAP
    factors = [f"extended {ext_note}", "double-bar-break snapback"]
    if rvol is not None:
        factors.append(f"RVOL {rvol}" + (" (>5 ideal)" if rvol >= 5 else " (<5, weak)"))
    caveats = ["mean-reversion fade — skip if market is cleanly trending down",
               "2 strikes/day max; avoid on fresh negative news"]
    if entry <= stop:
        return None
    return {"setup": "rubber_band", "side": "long", "entry": entry, "stop": stop,
            "targets": tgts, "rr": round((tgts[0]-entry)/r, 2) if r else 0,
            "factors": factors, "caveats": caveats}


def fashionably_late(df, cross_lookback=3):
    """Fashionably Late (momentum). LONG when an upsloping 9 EMA crosses a
    flat/down VWAP.

    Trigger: 9 EMA was below VWAP `cross_lookback` bars ago and is now >= VWAP,
    with the 9 EMA rising and VWAP flat-to-down. Stop = 1/3 of the VWAP->LoD
    distance below VWAP. Target = one measured move (LoD->cross) above the cross.
    Avoid if the 9 EMA went flat >15 min before the cross (choppy).
    """
    if df is None or len(df) < cross_lookback + 10:
        return None
    close = df["close"]
    e9 = ema(close, 9)
    vwap = df["vwap"] if "vwap" in df else None
    if vwap is None:
        return None
    now_e9, now_v = float(e9.iloc[-1]), float(vwap.iloc[-1])
    prev_e9, prev_v = float(e9.iloc[-1 - cross_lookback]), float(vwap.iloc[-1 - cross_lookback])
    crossed_up = prev_e9 < prev_v and now_e9 >= now_v
    e9_rising = now_e9 > float(e9.iloc[-3])
    vwap_flat_down = now_v <= float(vwap.iloc[-3]) * 1.001   # flat to down
    if not (crossed_up and e9_rising and vwap_flat_down):
        return None

    lod = float(df["low"].min())
    cross = now_v                                   # cross price ~= VWAP at the cross
    measured = cross - lod
    if measured <= 0:
        return None
    entry = round(float(close.iloc[-1]), 2)
    stop = round(cross - measured / 3.0, 2)         # 1/3 of VWAP->LoD below VWAP
    target = round(cross + measured, 2)             # one measured move above the cross
    if entry <= stop:
        return None
    r = entry - stop
    return {"setup": "fashionably_late", "side": "long", "entry": entry, "stop": stop,
            "targets": [target], "rr": round((target - entry) / r, 2) if r else 0,
            "factors": ["9EMA crossed up through flat/down VWAP", "measured-move target"],
            "caveats": ["skip if 9EMA went flat >15min before cross (choppy)"]}


def _consolidation(df, min_bars=5, max_bars=20):
    """Return (hi, lo, n) of the trailing consolidation (the tightest recent range
    of min..max bars), or None. Used by HitchHiker."""
    n = len(df)
    if n < min_bars + 1:
        return None
    best = None
    for w in range(min_bars, min(max_bars, n - 1) + 1):
        seg = df.iloc[-w:]
        hi = float(seg["high"].max()); lo = float(seg["low"].min())
        rng = hi - lo
        if best is None or rng < best[3]:
            best = (hi, lo, w, rng)
    if best is None:
        return None
    return best[0], best[1], best[2]


def hitchhiker(df, or_minutes=15):
    """HitchHiker (opening-drive continuation). LONG on the break of a tight
    consolidation that held the upper 1/3 of the day's range after an opening drive.

    Trigger: price breaks above the consolidation high with a volume bump (>=30%
    over the prior bar). Consolidation low must sit in the upper 1/3 of the day
    range. Stop .02 below the consolidation low. Exit in waves (½ + ½) — approx
    with 1R / 2R here.
    """
    if df is None or len(df) < 8:
        return None
    con = _consolidation(df)
    if con is None:
        return None
    chi, clo, cn = con
    dhi = float(df["high"].max()); dlo = float(df["low"].min())
    rng = dhi - dlo
    if rng <= 0:
        return None
    # consolidation low in the upper 1/3 of the day's range
    if (clo - dlo) / rng < 0.66:
        return None
    last = float(df["close"].iloc[-1]); prevbar_hi = chi
    broke = last > prevbar_hi
    vol = df["volume"].to_numpy(dtype=float)
    vol_bump = len(vol) >= 2 and vol[-1] >= 1.3 * vol[-2]
    if not (broke and vol_bump):
        return None
    entry = round(chi, 2)
    stop = round(clo - TICK, 2)
    if entry <= stop:
        return None
    r, tgts = _rr_targets(entry, stop)
    return {"setup": "hitchhiker", "side": "long", "entry": entry, "stop": stop,
            "targets": tgts, "rr": 1.0,
            "factors": [f"{cn}-bar consolidation in upper 1/3 of range", "range break +vol"],
            "caveats": ["skip if opening move was one sloppy candle / choppy consolidation"]}


def _swings(df, wing=1):
    """Return lists of (idx, price) swing highs and lows on the bars."""
    h = df["high"].to_numpy(dtype=float); l = df["low"].to_numpy(dtype=float)
    sh, sl = [], []
    for i in range(wing, len(df) - wing):
        if h[i] == h[i - wing:i + wing + 1].max() and h[i] > h[i - 1]:
            sh.append((i, float(h[i])))
        if l[i] == l[i - wing:i + wing + 1].min() and l[i] < l[i - 1]:
            sl.append((i, float(l[i])))
    return sh, sl


def backside(df):
    """Back$ide (below-VWAP reversal to VWAP). LONG after price extended BELOW
    VWAP, then prints a higher-high AND higher-low above a rising 9 EMA.

    Trigger: >=1 higher-high and >=1 higher-low off the LoD, majority of recent
    bars above a rising 9 EMA, price still below VWAP (room to run to it). Entry
    on the break of the small consolidation high. Stop .02 below the most recent
    higher-low. Target = VWAP.
    """
    if df is None or len(df) < 12 or "vwap" not in df:
        return None
    vwap = float(df["vwap"].iloc[-1]); last = float(df["close"].iloc[-1])
    lod = float(df["low"].min())
    if last >= vwap:                     # target (VWAP) must be above us
        return None
    # location: current range should be > halfway between LoD and VWAP
    if (last - lod) < 0.5 * (vwap - lod):
        return None
    e9 = ema(df["close"], 9)
    if not (float(e9.iloc[-1]) > float(e9.iloc[-4])):   # rising 9 EMA
        return None
    above = (df["close"].to_numpy(dtype=float)[-6:] > e9.to_numpy(dtype=float)[-6:]).mean()
    if above < 0.6:                      # majority above 9 EMA
        return None
    sh, sl = _swings(df.tail(15).reset_index(drop=True))
    if len(sh) < 2 or len(sl) < 2:
        return None
    higher_high = sh[-1][1] > sh[-2][1]
    higher_low = sl[-1][1] > sl[-2][1]
    if not (higher_high and higher_low):
        return None
    recent_hl = sl[-1][1]
    entry = round(max(float(df["high"].iloc[-2]), float(df["high"].iloc[-1])), 2)
    stop = round(recent_hl - TICK, 2)
    if entry <= stop or vwap <= entry:
        return None
    r = entry - stop
    return {"setup": "backside", "side": "long", "entry": entry, "stop": stop,
            "targets": [round(vwap, 2)], "rr": round((vwap - entry) / r, 2) if r else 0,
            "factors": ["higher-high + higher-low above rising 9EMA", "range>halfway LoD->VWAP"],
            "caveats": ["skip if market trending down / day-1 higher-TF breakdown", "one-and-done"]}


def second_chance(df, level, pullback_high=None):
    """Second Chance (breakout retest). LONG when a broken resistance `level`
    is retested and holds (old resistance -> new support).

    Trigger: price broke above `level`, pulled back to retest it, and the latest
    candle is green and closes above the prior candle's high (buyers return).
    Stop .02 below the turn (retest) candle low. Target1 = high of the initial
    pullback (`pullback_high`, else recent swing high); trail the rest under 9 EMA.
    """
    if df is None or len(df) < 5 or level is None:
        return None
    o = df.iloc[-1]; prev = df.iloc[-2]
    green = float(o["close"]) > float(o["open"])
    closes_above_prior = float(o["close"]) > float(prev["high"])
    near_level = abs(float(prev["low"]) - level) / level <= 0.01   # retested the level
    held = float(prev["low"]) >= level - level * 0.005             # held as support (didn't break back in)
    if not (green and closes_above_prior and near_level and held):
        return None
    turn_low = min(float(prev["low"]), float(o["low"]))
    entry = round(float(o["close"]), 2)
    stop = round(turn_low - TICK, 2)
    if entry <= stop:
        return None
    tgt = round(pullback_high if pullback_high else float(df["high"].max()), 2)
    r = entry - stop
    return {"setup": "second_chance", "side": "long", "entry": entry, "stop": stop,
            "targets": [tgt], "rr": round((tgt - entry) / r, 2) if r else 0,
            "factors": [f"retest of broken level {round(level,2)} held as support"],
            "caveats": ["abort if it breaks back below the level and doesn't recover next bar",
                        "2 strikes max; trail runner under 9EMA (1-min close below)"]}


def detect_all(df, atr_daily=None, rvol=None, resistance_level=None):
    """Run every applicable long detector on 1-min bars; return signals found
    (each a dict), best R:R first. `df` must have a `vwap` column (add_vwap).
    Pass `atr_daily` (daily ATR) and `rvol` to strengthen the Rubber Band check,
    and `resistance_level` to enable Second Chance."""
    out = []
    for fn, kw in ((rubber_band, dict(atr_daily=atr_daily, rvol=rvol)),
                   (fashionably_late, {}),
                   (hitchhiker, {}),
                   (backside, {})):
        try:
            s = fn(df, **kw)
            if s:
                out.append(s)
        except Exception:
            pass
    if resistance_level is not None:
        try:
            s = second_chance(df, resistance_level)
            if s:
                out.append(s)
        except Exception:
            pass
    out.sort(key=lambda s: s.get("rr", 0), reverse=True)
    return out
