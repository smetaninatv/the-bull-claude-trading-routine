"""Trading-desk dispatcher: which routine is due right now?

Used by the `trading-desk` agent (.claude/agents/trading-desk.md). Pure clock +
run-log logic — no IBKR connection — so it is cheap to call at the start of
every desk run. All times are anchored to US/Eastern (DST-safe via pytz) and
also reported in the user's CET/CEST.

Run log: `output/desk_runs.json` = {routine: [iso_ts, ...]}. The desk records a
routine with `record_run()` after it completes, so a routine isn't repeated the
same day (the laptop isn't always on — we never assume a run happened).
"""
import datetime
import json
import os

import pytz

from lib.realtime import is_market_holiday, next_trading_day

ET = pytz.timezone("US/Eastern")
LOCAL = pytz.timezone("Europe/Berlin")  # CET/CEST

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_PATH = os.path.join(_ROOT, "output", "desk_runs.json")

ROUTINES = ("premarket-research", "market-open", "exit-scan",
            "daily-summary", "weekly-review")

PREMARKET_FROM = datetime.time(7, 0)    # research is useful from ~7:00 ET
OPEN = datetime.time(9, 30)
CLOSE = datetime.time(16, 0)
MARKET_OPEN_WINDOW_MIN = 60             # market-open routine valid for first hour


def _load(path=RUNS_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_run(routine, now=None, path=RUNS_PATH):
    """Record that `routine` completed at `now` (default: current time)."""
    if routine not in ROUTINES:
        raise ValueError(f"unknown routine {routine!r}; expected one of {ROUTINES}")
    now = now or datetime.datetime.now(ET)
    runs = _load(path)
    runs.setdefault(routine, []).append(now.astimezone(ET).isoformat())
    runs[routine] = runs[routine][-50:]          # keep the log small
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=1)


def last_run(routine, path=RUNS_PATH):
    """Datetime (ET) of the last recorded run of `routine`, or None."""
    ts = _load(path).get(routine)
    return datetime.datetime.fromisoformat(ts[-1]).astimezone(ET) if ts else None


def _ran_since(routine, since, path):
    lr = last_run(routine, path)
    return lr is not None and lr >= since


def _is_trading_day(d):
    return d.weekday() < 5 and not is_market_holiday(d)


def _last_trading_friday_close(now):
    """The most recent Friday 16:00 ET that is at or before `now`."""
    d = now.date()
    while d.weekday() != 4:
        d -= datetime.timedelta(days=1)
    close = ET.localize(datetime.datetime.combine(d, CLOSE))
    if close > now:
        close -= datetime.timedelta(days=7)
    return close


def desk_status(now=None, path=RUNS_PATH):
    """What should the desk do right now?

    Returns a dict:
      now_et, now_local     — formatted clocks
      session               — 'pre' | 'open' | 'post' | 'closed' | 'holiday'
      trading_day           — today is a weekday and not a US holiday
      minutes_since_open, minutes_to_close
      due                   — ordered list of routines to run now (may be empty)
      next                  — human hint for what comes next if nothing is due
      last_runs             — {routine: iso or None}
    """
    now = (now or datetime.datetime.now(ET)).astimezone(ET)
    today = now.date()
    t = now.time()
    trading_day = _is_trading_day(today)
    open_dt = ET.localize(datetime.datetime.combine(today, OPEN))
    close_dt = ET.localize(datetime.datetime.combine(today, CLOSE))
    midnight = ET.localize(datetime.datetime.combine(today, datetime.time(0, 0)))
    since_open = int((now - open_dt).total_seconds() // 60)
    to_close = int((close_dt - now).total_seconds() // 60)

    if is_market_holiday(today):
        session = "holiday"
    elif not trading_day:
        session = "closed"
    elif datetime.time(4, 0) <= t < OPEN:
        session = "pre"
    elif OPEN <= t < CLOSE:
        session = "open"
    elif CLOSE <= t < datetime.time(20, 0):
        session = "post"
    else:
        session = "closed"

    ran_today = lambda r: _ran_since(r, midnight, path)  # noqa: E731
    due, nxt = [], None

    if trading_day and PREMARKET_FROM <= t < OPEN:
        if not ran_today("premarket-research"):
            due.append("premarket-research")
        else:
            nxt = "market-open at 9:30 ET"
    elif trading_day and OPEN <= t < CLOSE:
        if since_open < MARKET_OPEN_WINDOW_MIN and not ran_today("market-open"):
            due.append("market-open")
        else:
            due.append("exit-scan")            # cadence decided by open positions
    elif trading_day and t >= CLOSE:
        if not ran_today("daily-summary"):
            due.append("daily-summary")
    elif trading_day and t < PREMARKET_FROM:
        nxt = "premarket-research from 7:00 ET"

    # Weekly review: after Friday's close, through the weekend, once per week.
    fri_close = _last_trading_friday_close(now)
    if (now - fri_close) < datetime.timedelta(days=3) and \
            not _ran_since("weekly-review", fri_close, path) and \
            (today.weekday() != 4 or t >= CLOSE):
        if "daily-summary" not in due:          # summary first, then review
            due.append("weekly-review")
        else:
            nxt = "weekly-review after the daily summary"

    if not due and nxt is None:
        nd = today if (trading_day and t < PREMARKET_FROM) else next_trading_day(today)
        nxt = f"premarket-research on {nd:%a %b %d} from 7:00 ET"

    return {
        "now_et": now.strftime("%a %Y-%m-%d %H:%M ET"),
        "now_local": now.astimezone(LOCAL).strftime("%H:%M %Z"),
        "session": session,
        "trading_day": trading_day,
        "minutes_since_open": since_open,
        "minutes_to_close": to_close,
        "due": due,
        "next": nxt,
        "last_runs": {r: (lr.isoformat() if (lr := last_run(r, path)) else None)
                      for r in ROUTINES},
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(desk_status())
