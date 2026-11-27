"""US stock session schedule, read from IBKR's published trading hours.

Nothing about half days, holiday closures or new extended sessions is
hardcoded here. IBKR's contract details carry the exchange schedule for the
coming days:

  liquidHours   (SMART contract)     regular session, incl. any early close
  tradingHours  (SMART contract)     every hour SMART routing trades
  tradingHours  (overnight venue)    the overnight session, if IBKR offers one

Format: "YYYYMMDD:HHMM-YYYYMMDD:HHMM;YYYYMMDD:CLOSED;..." in the contract's
`timeZoneId`.

`refresh(ib)` fetches that and caches it in `output/market_hours.json`. All
other functions only read the cache, so `lib.desk` and the routines need no
IBKR connection. For a date the cache doesn't cover, a standard weekday
schedule from `config/strategy.yaml` `session:` is used (pre, regular, post;
no overnight, no early closes), and `source` reports 'default'.
"""
import datetime
import json
import os
import sys

import pytz

from lib.config import load_strategy

ET = pytz.timezone("US/Eastern")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(_ROOT, "output", "market_hours.json")

# Sessions in which a US stock order can trade.
LIVE_SESSIONS = ("overnight", "pre", "open", "post")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _cfg():
    s = (load_strategy() or {}).get("session", {}) or {}
    return {
        "symbol": s.get("hours_symbol", "SPY"),
        "refresh_hours": float(s.get("hours_refresh_hours", 12)),
        "retry_minutes": float(s.get("hours_retry_minutes", 60)),
        "timeout_s": float(s.get("hours_timeout_s", 8)),
        "pre_open": _t(s.get("pre_open", "04:00")),
        "rth_open": _t(s.get("rth_open", "09:30")),
        "rth_close": _t(s.get("rth_close", "16:00")),
        "post_close": _t(s.get("post_close", "20:00")),
        "overnight_exchange": ((load_strategy() or {}).get("orders", {}) or {})
        .get("overnight_exchange", "OVERNIGHT"),
    }


def _t(hhmm):
    h, m = str(hhmm).split(":")
    return datetime.time(int(h), int(m))


# ---------------------------------------------------------------------------
# Parse + fetch
# ---------------------------------------------------------------------------

def parse_hours(text, tz="US/Eastern"):
    """Parse an IBKR hours string into ([(start, end), ...] aware ET, {closed dates}).

    Handles both the current "YYYYMMDD:HHMM-YYYYMMDD:HHMM" form and the legacy
    "YYYYMMDD:HHMM-HHMM[,HHMM-HHMM]" form.
    """
    zone = pytz.timezone(tz or "US/Eastern")
    intervals, closed = [], set()
    for part in (text or "").split(";"):
        part = part.strip()
        if not part:
            continue
        day, _, rest = part.partition(":")
        if rest.upper() == "CLOSED":
            closed.add(datetime.datetime.strptime(day, "%Y%m%d").date())
            continue
        for span in rest.split(","):
            a, _, b = span.partition("-")
            if ":" in b:                                   # YYYYMMDD:HHMM-YYYYMMDD:HHMM
                start = datetime.datetime.strptime(f"{day}{a}", "%Y%m%d%H%M")
                end = datetime.datetime.strptime(b.replace(":", ""), "%Y%m%d%H%M")
            else:                                          # legacy YYYYMMDD:HHMM-HHMM
                start = datetime.datetime.strptime(f"{day}{a}", "%Y%m%d%H%M")
                end = datetime.datetime.strptime(f"{day}{b}", "%Y%m%d%H%M")
                if end <= start:
                    end += datetime.timedelta(days=1)
            intervals.append((zone.localize(start).astimezone(ET),
                              zone.localize(end).astimezone(ET)))
    return intervals, closed


def _details(ib, symbol, exchange):
    from ib_async import Stock
    cds = ib.reqContractDetails(Stock(symbol, exchange, "USD"))
    return cds[0] if cds else None


def refresh(ib, symbol=None, path=None):
    """Fetch IBKR's schedule and cache it. Returns the cache dict, or None on failure.

    Best effort: a slow or failing request is recorded as `last_attempt` and
    never raises, so a connection is never blocked by it.
    """
    cfg = _cfg()
    symbol = symbol or cfg["symbol"]
    cache = _load(path)
    now = datetime.datetime.now(ET)
    old_timeout = getattr(ib, "RequestTimeout", 0)
    ib.RequestTimeout = cfg["timeout_s"]
    try:
        smart = _details(ib, symbol, "SMART")
        if smart is None:
            raise RuntimeError(f"no contract details for {symbol}")
        tz = smart.timeZoneId or "US/Eastern"
        liquid, closed = parse_hours(smart.liquidHours, tz)
        extended, _ = parse_hours(smart.tradingHours, tz)
        overnight = []
        try:
            on = _details(ib, symbol, cfg["overnight_exchange"])
            if on is not None:
                overnight, _ = parse_hours(on.tradingHours, on.timeZoneId or tz)
        except Exception:
            pass                       # no overnight venue for this symbol/account
        iso = lambda xs: [[a.isoformat(), b.isoformat()] for a, b in xs]  # noqa: E731
        cache = {
            "fetched": now.isoformat(),
            "last_attempt": now.isoformat(),
            "symbol": symbol,
            "liquid": iso(liquid),
            "extended": iso(extended),
            "overnight": iso(overnight),
            "closed": sorted(d.isoformat() for d in closed),
        }
        _save(cache, path)
        return cache
    except Exception as e:
        cache["last_attempt"] = now.isoformat()
        _save(cache, path)
        print(f"[hours] WARN: could not refresh IBKR trading hours ({e!r}); "
              "using cached/default schedule", file=sys.stderr)
        return None
    finally:
        ib.RequestTimeout = old_timeout


def refresh_if_stale(ib, path=None):
    """Refresh when the cache is older than `session.hours_refresh_hours`,
    retrying a failed attempt at most every `session.hours_retry_minutes`."""
    cfg = _cfg()
    cache = _load(path)
    now = datetime.datetime.now(ET)
    fetched = _ts(cache.get("fetched"))
    attempt = _ts(cache.get("last_attempt"))
    if fetched and now - fetched < datetime.timedelta(hours=cfg["refresh_hours"]):
        return cache
    if attempt and now - attempt < datetime.timedelta(minutes=cfg["retry_minutes"]):
        return cache
    return refresh(ib, path=path) or cache


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _ts(s):
    return datetime.datetime.fromisoformat(s).astimezone(ET) if s else None


def _load(path=None):
    path = path or CACHE_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(cache, path=None):
    path = path or CACHE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1)


class Schedule:
    """Parsed view of the cache. `covers(d)` = IBKR published hours for date d."""

    def __init__(self, cache=None, path=None):
        cache = _load(path) if cache is None else cache
        conv = lambda xs: [(_ts(a), _ts(b)) for a, b in xs or []]  # noqa: E731
        self.liquid = conv(cache.get("liquid"))
        self.extended = conv(cache.get("extended"))
        self.overnight = conv(cache.get("overnight"))
        self.closed = {datetime.date.fromisoformat(d) for d in cache.get("closed", [])}
        self.fetched = cache.get("fetched")
        self._dates = {a.date() for a, _ in self.liquid} | self.closed

    def covers(self, d):
        return d in self._dates

    def rth(self, d):
        for a, b in self.liquid:
            if a.date() == d:
                return a, b
        return None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _default_rth(d, cfg):
    from lib.realtime import is_rule_holiday
    if d.weekday() >= 5 or is_rule_holiday(d):
        return None
    return (ET.localize(datetime.datetime.combine(d, cfg["rth_open"])),
            ET.localize(datetime.datetime.combine(d, cfg["rth_close"])))


def rth_window(d, sched=None):
    """(open, close) aware ET datetimes of the regular session on date `d`, or
    None if there is none (weekend/holiday). Early closes come from IBKR."""
    sched = sched or Schedule()
    if sched.covers(d):
        return sched.rth(d)
    return _default_rth(d, _cfg())


def standard_rth_close():
    """The configured normal regular close (`session.rth_close`)."""
    return _cfg()["rth_close"]


def source(d=None, sched=None):
    """'ibkr' if IBKR's published schedule covers date `d`, else 'default'."""
    sched = sched or Schedule()
    d = d or datetime.datetime.now(ET).date()
    return "ibkr" if sched.covers(d) else "default"


def session_at(now=None, sched=None):
    """Session label at `now` (aware datetime; default now):

      'open'      regular hours (IBKR liquidHours)
      'pre'       extended hours before the regular open
      'post'      extended hours after the regular close, up to `session.post_close`
      'overnight' any other tradeable time (overnight venue, or SMART extended
                  hours outside pre/post)
      'holiday'   a weekday with no session
      'closed'    nothing trades
    """
    cfg = _cfg()
    sched = sched or Schedule()
    now = (now or datetime.datetime.now(ET)).astimezone(ET)
    d = now.date()
    within = lambda xs: any(a <= now < b for a, b in xs)  # noqa: E731

    if within(sched.liquid):
        return "open"
    if within(sched.extended) or within(sched.overnight):
        win = sched.rth(d) if sched.covers(d) else None
        if within(sched.extended) and win:
            if now < win[0] and now.time() >= cfg["pre_open"]:
                return "pre"
            if now >= win[1] and now.time() < cfg["post_close"]:
                return "post"
        return "overnight"
    if sched.covers(d):
        return "holiday" if d.weekday() < 5 and sched.rth(d) is None else "closed"

    # Default schedule (no IBKR hours cached for this date).
    win = _default_rth(d, cfg)
    if win is None:
        return "holiday" if d.weekday() < 5 else "closed"
    t = now.time()
    if cfg["pre_open"] <= t < cfg["rth_open"]:
        return "pre"
    if win[0] <= now < win[1]:
        return "open"
    if win[1] <= now and t < cfg["post_close"]:
        return "post"
    return "closed"
