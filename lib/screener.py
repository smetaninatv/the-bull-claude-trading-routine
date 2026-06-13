"""Yahoo Finance candidate discovery + data fallback (free, no subscription).

Use for SCREENING (finding candidates beyond the watchlist) and as a HISTORICAL
DATA fallback when the IBKR scanner times out or you're offline from Gateway.
IBKR remains the source of truth for positions, orders, and execution.

Quotes are delayed — fine for swing/screening, not for real-time day-trade fills.
Returns bars with the same lowercase columns as lib.data (date/open/high/low/
close/volume) so lib.indicators works unchanged.
"""
import sys
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")


def _warn(msg):
    """Visible warning to stderr (warnings module is filtered to ignore above)."""
    print(f"[screener] WARN: {msg}", file=sys.stderr)

# Yahoo predefined screens most relevant to day/swing discovery.
SCREENS = {
    "gainers": "day_gainers",
    "losers": "day_losers",
    "most_active": "most_actives",
    "small_cap_gainers": "small_cap_gainers",
    "aggressive_small_caps": "aggressive_small_caps",
    "growth_tech": "growth_technology_stocks",
    "most_shorted": "most_shorted_stocks",
}


def yahoo_screen(name="gainers", count=25):
    """Return a list of ticker symbols from a Yahoo predefined screen.

    `name` is one of SCREENS keys (or a raw Yahoo screen id). Skips gracefully
    (returns []) if Yahoo is unreachable or the screen id is unknown — but emits
    a stderr warning on FAILURE so a silent degradation to watchlist-only is
    visible in the run log (a genuine empty screen warns nothing).
    """
    key = SCREENS.get(name, name)
    try:
        res = yf.screen(key, count=count)
        return [q["symbol"] for q in res.get("quotes", []) if q.get("symbol")]
    except Exception as e:
        _warn(f"screen '{name}' failed ({type(e).__name__}: {e}) — skipped")
        return []


def discover(names=("gainers", "most_active"), per=25):
    """Union of several screens -> de-duplicated symbol list (preserves order).

    Emits a stderr warning if EVERY screen came back empty — that means discovery
    has degraded to watchlist + holdings only, which the caller must surface in
    the report rather than imply the whole market was scanned.
    """
    seen, out = set(), []
    for n in names:
        for s in yahoo_screen(n, per):
            if s not in seen:
                seen.add(s)
                out.append(s)
    if not out:
        _warn(f"all screens {tuple(names)} returned 0 symbols — discovery DEGRADED "
              f"to watchlist/holdings only; say so in the report")
    return out


def yahoo_bars(symbol, period="1y", interval="1d"):
    """Historical OHLCV from Yahoo as a DataFrame matching lib.data's format.

    Columns: date, open, high, low, close, volume (lowercase). period/interval
    use yfinance conventions (e.g. period='1y'/'5d', interval='1d'/'5m').
    Returns None if no data.
    """
    h = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    if h is None or h.empty:
        return None
    h = h.reset_index()
    h.columns = [str(c).lower() for c in h.columns]
    if "datetime" in h.columns:        # intraday index is 'Datetime'
        h = h.rename(columns={"datetime": "date"})
    keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in h.columns]
    return h[keep]
