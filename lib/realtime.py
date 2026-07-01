"""Real-time intraday data and day-trade viability checks.

Primary source: yfinance — near-real-time 5-min bars during market hours, no auth.
Optional: Webull — pre-market movers + deeper intraday data. Requires credentials at
  config/webull_creds.json: {"username": "...", "password": "...", "did": "<uuid>"}
  Generate a DID once with: python -c "import uuid; print(uuid.uuid4())"
  Install: pip install webull

Falls back to yfinance silently if Webull is not configured or unavailable.
"""
import datetime
import json
import os
import warnings

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

from lib.indicators import add_vwap

_CREDS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "webull_creds.json")


# ---------------------------------------------------------------------------
# US market holiday calendar (NYSE/Nasdaq full closures)
# ---------------------------------------------------------------------------

def _easter(year):
    """Gregorian Easter Sunday (Anonymous/Meeus algorithm) — anchors Good Friday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def _observed(d):
    """NYSE observance: a holiday on Saturday -> preceding Friday; Sunday -> Monday."""
    if d.weekday() == 5:
        return d - datetime.timedelta(days=1)
    if d.weekday() == 6:
        return d + datetime.timedelta(days=1)
    return d


def _nth_weekday(year, month, weekday, n):
    """nth `weekday` (Mon=0) of a month, e.g. 3rd Monday of January."""
    first = datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    """Last `weekday` (Mon=0) of a month, e.g. last Monday of May."""
    if month == 12:
        last = datetime.date(year, 12, 31)
    else:
        last = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - datetime.timedelta(days=offset)


def us_market_holidays(year):
    """{date: name} of full NYSE/Nasdaq closures for `year`.

    Covers fixed-date holidays (with Sat->Fri / Sun->Mon observance), the
    Monday/Thursday floating holidays, and Good Friday (Easter-derived).
    Does NOT include half-days (e.g. day after Thanksgiving) — those still trade.
    """
    h = {}
    nyd = datetime.date(year, 1, 1)
    if nyd.weekday() != 5:                       # NYSE: no Fri obs. when Jan 1 is Sat
        h[_observed(nyd)] = "New Year's Day"
    h[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    h[_nth_weekday(year, 2, 0, 3)] = "Presidents' Day"
    h[_easter(year) - datetime.timedelta(days=2)] = "Good Friday"
    h[_last_weekday(year, 5, 0)] = "Memorial Day"
    if year >= 2022:                             # Juneteenth became a market holiday in 2022
        h[_observed(datetime.date(year, 6, 19))] = "Juneteenth"
    h[_observed(datetime.date(year, 7, 4))] = "Independence Day"
    h[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    h[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    h[_observed(datetime.date(year, 12, 25))] = "Christmas Day"
    return h


def _et_today():
    import pytz
    return datetime.datetime.now(pytz.timezone("US/Eastern")).date()


def is_market_holiday(d=None):
    """Holiday name if `d` (default: today ET) is a US market holiday, else None."""
    if d is None:
        d = _et_today()
    return us_market_holidays(d.year).get(d)


def next_trading_day(d=None):
    """Next date after `d` (default today ET) that is a weekday and not a holiday."""
    if d is None:
        d = _et_today()
    nd = d + datetime.timedelta(days=1)
    while nd.weekday() >= 5 or is_market_holiday(nd):
        nd += datetime.timedelta(days=1)
    return nd


# ---------------------------------------------------------------------------
# Market session
# ---------------------------------------------------------------------------

def market_session():
    """Return 'holiday', 'pre', 'open', 'post', or 'closed' (US/Eastern).

    'holiday' takes precedence (full-closure days); weekends return 'closed'.
    """
    import pytz

    now_dt = datetime.datetime.now(pytz.timezone("US/Eastern"))
    if is_market_holiday(now_dt.date()):
        return "holiday"
    if now_dt.weekday() >= 5:                     # Sat/Sun
        return "closed"
    now = now_dt.time()
    if datetime.time(4, 0) <= now < datetime.time(9, 30):
        return "pre"
    if datetime.time(9, 30) <= now < datetime.time(16, 0):
        return "open"
    if datetime.time(16, 0) <= now < datetime.time(20, 0):
        return "post"
    return "closed"


def market_closed_reason(d=None):
    """Human reason the market is closed right now, or None if a session is live.

    One guard for the routines: covers **holidays AND weekends** (and weekday
    off-hours) with a single clear message. Returns None when `market_session()`
    is a tradeable/extended session ('pre'/'open'/'post') so the routine proceeds.
    Examples: "Juneteenth (US holiday)", "the weekend (Saturday)",
    "outside trading hours (US/Eastern)".
    """
    import pytz

    sess = market_session()
    if sess in ("pre", "open", "post"):
        return None
    hol = is_market_holiday()
    if hol:
        return f"{hol} (US holiday)"
    now = datetime.datetime.now(pytz.timezone("US/Eastern"))
    if now.weekday() >= 5:
        return f"the weekend ({now:%A})"
    return "outside trading hours (US/Eastern)"


def minutes_to_close():
    """Minutes remaining until 4 PM ET. Negative if after close."""
    import datetime
    import pytz

    et = pytz.timezone("US/Eastern")
    now = datetime.datetime.now(et)
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return int((close - now).total_seconds() / 60)


def minutes_since_open():
    """Minutes since the 9:30 AM ET open (negative before the open).

    ET-anchored (DST-safe via pytz), so it stays correct year-round even though
    a CET wall-clock schedule drifts vs the US open when US/EU DST shift on
    different dates. Use to gate the market-open routine to a real post-open time.
    """
    import pytz

    et = pytz.timezone("US/Eastern")
    now = datetime.datetime.now(et)
    open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return int((now - open_dt).total_seconds() / 60)


# Market-open routine runs this many minutes after the 9:30 ET open, so live
# VWAP / opening-range / RVOL are formed before it validates anything.
MARKET_OPEN_DELAY_MIN = 5


def market_open_ready():
    """True once it's a live regular session AND >= MARKET_OPEN_DELAY_MIN past the open.

    The market-open routine should gate on this: if False, it's too early (or not
    a trading session) — don't run the open review yet. Returns False on
    holidays/weekends/pre-market and in the first MARKET_OPEN_DELAY_MIN minutes.
    """
    if market_closed_reason() is not None:
        return False
    m = minutes_since_open()
    return 0 <= m - MARKET_OPEN_DELAY_MIN < 360        # within the trading session, past the delay


# ---------------------------------------------------------------------------
# Intraday bars (yfinance — primary)
# ---------------------------------------------------------------------------

def intraday_bars(sym, interval="5m", period="1d"):
    """Near-real-time intraday bars via yfinance.

    interval: '1m', '2m', '5m', '15m', '30m', '60m'
    Returns DataFrame with columns open/high/low/close/volume, DatetimeIndex (no tz).
    Returns None on failure.
    """
    try:
        df = yf.Ticker(sym).history(period=period, interval=interval, prepost=False)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df[["open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        return None


def current_quote(sym):
    """Return dict: price, change_pct, last_volume, avg_volume. None on failure."""
    try:
        fi = yf.Ticker(sym).fast_info
        price = fi.last_price or fi.previous_close
        prev = fi.previous_close
        chg = (price - prev) / prev * 100 if prev else 0.0
        return {
            "price": price,
            "change_pct": chg,
            "last_volume": getattr(fi, "last_volume", None),
            "avg_volume": getattr(fi, "three_month_average_volume", None),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Day trade viability check
# ---------------------------------------------------------------------------

def day_trade_conditions():
    """Assess whether today is a good day for day trading.

    Returns dict:
      vix         float or None
      spy_trend   'bullish' | 'bearish' | 'choppy' | 'unknown'
      qqq_trend   'bullish' | 'bearish' | 'unknown'
      score       0-10
      verdict     short human-readable verdict
      notes       list of explanation strings
      session     market_session() value

    Score guide:
      8-10  Excellent — trend day, clear directional bias
      5-7   Moderate  — selective, wait for clean setups
      3-4   Poor      — choppy or high VIX, consider skipping
      0-2   Avoid     — high VIX + downtrend, sit out
    """
    # --- Market-holiday short-circuit (no live session today) ---
    hol = is_market_holiday()
    if hol:
        nxt = next_trading_day()
        return {
            "vix": None, "spy_trend": "n/a", "qqq_trend": "n/a",
            "score": 0,
            "verdict": f"MARKET CLOSED — {hol} (US holiday)",
            "notes": [
                f"US equity markets are closed today ({hol}).",
                f"Any tape/scan data is stale — do NOT trade. Next session: {nxt:%a %b %d}.",
            ],
            "session": "holiday",
        }

    notes = []
    score = 5  # neutral start

    # --- VIX ---
    vix = None
    try:
        v = yf.Ticker("^VIX").history(period="2d", interval="1d")
        if not v.empty:
            vix = float(v["Close"].iloc[-1])
    except Exception:
        pass

    if vix is not None:
        if vix < 15:
            score += 2
            notes.append(f"VIX {vix:.1f} — calm; momentum setups work well")
        elif vix < 20:
            score += 1
            notes.append(f"VIX {vix:.1f} — normal; day trading is fine")
        elif vix < 30:
            score -= 1
            notes.append(f"VIX {vix:.1f} — elevated; be selective, use tighter stops")
        else:
            score -= 3
            notes.append(f"VIX {vix:.1f} — HIGH; whipsaw risk, avoid or size very small")
    else:
        notes.append("VIX: unavailable")

    # --- SPY intraday trend ---
    spy_trend = "unknown"
    try:
        spy = intraday_bars("SPY", "5m", "1d")
        if spy is not None and len(spy) >= 4:
            spy = add_vwap(spy)
            last_price = spy["close"].iloc[-1]
            last_vwap = spy["vwap"].iloc[-1]
            open_price = spy["open"].iloc[0]
            above_vwap = last_price > last_vwap
            up_from_open = last_price > open_price

            if above_vwap and up_from_open:
                spy_trend = "bullish"
                score += 2
                notes.append("SPY above VWAP and above open — long bias, good tape")
            elif above_vwap:
                spy_trend = "neutral-bull"
                score += 1
                notes.append("SPY above VWAP but below open — cautious long bias")
            elif not above_vwap and not up_from_open:
                spy_trend = "bearish"
                score -= 2
                notes.append("SPY below VWAP + below open — short bias or sit out")
            else:
                spy_trend = "choppy"
                score -= 1
                notes.append("SPY mixed signals — choppy tape, reduce size")
    except Exception as e:
        notes.append(f"SPY trend: error ({e})")

    # --- QQQ intraday trend ---
    qqq_trend = "unknown"
    try:
        qqq = intraday_bars("QQQ", "5m", "1d")
        if qqq is not None and len(qqq) >= 4:
            qqq = add_vwap(qqq)
            above_vwap = qqq["close"].iloc[-1] > qqq["vwap"].iloc[-1]
            qqq_trend = "bullish" if above_vwap else "bearish"
            if above_vwap:
                score += 1
                notes.append("QQQ above VWAP — tech names have bid")
            else:
                score -= 1
                notes.append("QQQ below VWAP — avoid tech/growth day trades")
    except Exception:
        notes.append("QQQ: unavailable")

    score = max(0, min(10, score))

    if score >= 8:
        verdict = "EXCELLENT — trend day, go for it"
    elif score >= 5:
        verdict = "MODERATE — be selective, wait for clean setups"
    elif score >= 3:
        verdict = "POOR — choppy or uncertain, consider skipping"
    else:
        verdict = "AVOID — high VIX or strong downtrend, sit out"

    return {
        "vix": vix,
        "spy_trend": spy_trend,
        "qqq_trend": qqq_trend,
        "score": score,
        "verdict": verdict,
        "notes": notes,
        "session": market_session(),
    }


# ---------------------------------------------------------------------------
# Webull integration (optional)
# ---------------------------------------------------------------------------

def _webull_client():
    """Return authenticated webull client, or None if not configured/available."""
    if not os.path.exists(_CREDS_PATH):
        return None
    try:
        from webull import webull  # noqa: PLC0415
        with open(_CREDS_PATH) as f:
            creds = json.load(f)
        wb = webull()
        wb.login(creds["username"], creds["password"], device_id=creds.get("did", ""))
        return wb
    except Exception:
        return None


def webull_configured():
    """True if webull credentials file exists."""
    return os.path.exists(_CREDS_PATH)


def premarket_movers(top_n=10):
    """Pre-market movers: tries Webull first, falls back to yfinance screener.

    Returns list of dicts: {sym, price, change_pct, volume, source}
    """
    wb = _webull_client()
    if wb is not None:
        try:
            movers = wb.get_pre_market_gainers() or []
            results = []
            for m in movers[:top_n]:
                sym = m.get("ticker", {}).get("symbol", "")
                if sym:
                    results.append({
                        "sym": sym,
                        "price": m.get("close", 0),
                        "change_pct": m.get("change", 0),
                        "volume": m.get("volume", 0),
                        "source": "webull",
                    })
            if results:
                return results
        except Exception:
            pass

    # Fallback: yfinance day gainers screen
    try:
        from lib.screener import discover  # noqa: PLC0415
        syms = discover(("gainers",), per=top_n)
        results = []
        for sym in syms[:top_n]:
            q = current_quote(sym)
            if q:
                results.append({
                    "sym": sym,
                    "price": q["price"],
                    "change_pct": q["change_pct"],
                    "volume": q["last_volume"],
                    "source": "yfinance",
                })
        return results
    except Exception:
        return []


def get_intraday_bars(sym, interval="5m"):
    """Intraday bars: tries Webull first for richer data, falls back to yfinance.

    Returns same format as intraday_bars() (open/high/low/close/volume DataFrame).
    """
    wb = _webull_client()
    if wb is not None:
        interval_map = {"1m": "m1", "5m": "m5", "15m": "m15", "30m": "m30", "60m": "m60"}
        wb_interval = interval_map.get(interval, "m5")
        try:
            bars = wb.get_bars(sym, interval=wb_interval, count=100)
            if bars:
                df = pd.DataFrame(bars)
                rename = {}
                for col in df.columns:
                    low = col.lower()
                    if "open" in low:
                        rename[col] = "open"
                    elif "high" in low:
                        rename[col] = "high"
                    elif "low" in low:
                        rename[col] = "low"
                    elif "close" in low:
                        rename[col] = "close"
                    elif "volume" in low:
                        rename[col] = "volume"
                    elif "time" in low or "timestamp" in low:
                        rename[col] = "timestamp"
                df = df.rename(columns=rename)
                if "timestamp" in df.columns:
                    df.index = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce")
                df = df[["open", "high", "low", "close", "volume"]].dropna()
                if not df.empty:
                    return df
        except Exception:
            pass

    return intraday_bars(sym, interval=interval)
