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

try:                                    # custom full-market screener (yfinance >= 0.2.4x)
    from yfinance import EquityQuery as _Q
except Exception:                       # pragma: no cover - very old yfinance
    _Q = None

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


def discover(names=("gainers", "most_active"), per=25, market_scan_kwargs=None):
    """Union of the custom market scan + several predefined screens -> de-duped symbols.

    Discovery order (preserves first-seen): the **custom relative-volume market scan**
    (`market_scan`, TradingView-style — the broad net) FIRST, then the predefined Yahoo
    screens in `names`. Pass `market_scan_kwargs={}` to use the defaults, or a dict to
    tune thresholds (e.g. premarket: `{"min_relvol": 0}` since relvol under-reports
    before the session). Pass `market_scan_kwargs=None` (default) to RUN it with
    defaults; pass `False` to skip it (predefined screens only).

    Emits a stderr warning if EVERY source came back empty — discovery has degraded to
    watchlist + holdings only, which the caller must surface in the report rather than
    imply the whole market was scanned.
    """
    seen, out = set(), []

    if market_scan_kwargs is not False:
        for s in market_scan_symbols(**(market_scan_kwargs or {})):
            if s not in seen:
                seen.add(s)
                out.append(s)

    for n in names:
        for s in yahoo_screen(n, per):
            if s not in seen:
                seen.add(s)
                out.append(s)
    if not out:
        _warn(f"market_scan + screens {tuple(names)} returned 0 symbols — discovery "
              f"DEGRADED to watchlist/holdings only; say so in the report")
    return out


def market_scan(min_price=5, min_mktcap=2_000_000_000, min_dayvol=1_000_000,
                min_pct_change=3.0, min_relvol=2.0, region="us", size=50):
    """Full-market relative-volume screen (TradingView-style) via a custom Yahoo query.

    Replicates the user's TradingView swing filter (Price > $5, Mkt cap > $2B,
    Vol > 1M, Rel vol > 2, sorted by % change). Unlike `yahoo_screen`, which only
    pulls Yahoo's CANNED screens, this builds a custom `EquityQuery` so we can set
    our own thresholds and scan the WHOLE market, not just a fixed list.

    Server-side filters (Yahoo-side): region, intradayprice, intradaymarketcap,
    dayvolume, percentchange. Yahoo CANNOT filter on a ratio, so **relative volume
    is computed client-side** (`volume / averageDailyVolume3Month`) and the
    `min_relvol` cut is applied here after fetch.

    Returns a list of dicts, sorted by relvol desc:
        {symbol, price, pct_change, volume, avg_vol_3m, relvol, mktcap}

    NOTE on relvol timing: `volume` is the CUMULATIVE session volume so far vs the
    3-month daily average, so **pre-market it under-reports** (little has traded yet).
    Pre-market: pass `min_relvol=0` and lean on `pct_change` / gap; relvol is most
    meaningful once the regular session is underway. Returns [] (with a stderr warn)
    if the custom-query API is unavailable or Yahoo is unreachable.
    """
    if _Q is None:
        _warn("EquityQuery unavailable (old yfinance) - market_scan skipped; "
              "falling back to predefined screens only")
        return []
    try:
        q = _Q("and", [
            _Q("eq", ["region", region]),
            _Q("gt", ["intradayprice", min_price]),
            _Q("gt", ["intradaymarketcap", min_mktcap]),
            _Q("gt", ["dayvolume", min_dayvol]),
            _Q("gt", ["percentchange", min_pct_change]),
        ])
        res = yf.screen(q, sortField="percentchange", sortAsc=False, size=size)
    except Exception as e:
        _warn(f"market_scan query failed ({type(e).__name__}: {e}) - skipped")
        return []

    rows = []
    for x in res.get("quotes", []):
        sym = x.get("symbol")
        if not sym:
            continue
        vol = x.get("regularMarketVolume") or 0
        avg = x.get("averageDailyVolume3Month") or 0
        relvol = (vol / avg) if avg else None
        if min_relvol > 0 and (relvol is None or relvol < min_relvol):
            continue
        rows.append({
            "symbol": sym,
            "price": x.get("regularMarketPrice"),
            "pct_change": x.get("regularMarketChangePercent"),
            "volume": vol,
            "avg_vol_3m": avg,
            "relvol": round(relvol, 2) if relvol is not None else None,
            "mktcap": x.get("marketCap"),
        })
    rows.sort(key=lambda r: (r["relvol"] is None, -(r["relvol"] or 0)))
    return rows


def market_scan_symbols(**kwargs):
    """`market_scan` reduced to just the ticker list (for feeding into discover/scan)."""
    return [r["symbol"] for r in market_scan(**kwargs)]


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
