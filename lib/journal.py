"""Trade journal: human-readable markdown notes + a structured SQLite log.

The markdown file is the narrative ("why we bought/sold", with chart links).
The SQLite table is for the daily/weekly review aggregations.
"""
import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD_PATH = os.path.join(_ROOT, "output", "journal.md")
DB_PATH = os.path.join(_ROOT, "output", "journal.db")
PERF_PATH = os.path.join(_ROOT, "output", "performance.md")


def _ensure_dir():
    os.makedirs(os.path.join(_ROOT, "output"), exist_ok=True)


def append_note(text):
    """Append a markdown block to the running journal."""
    _ensure_dir()
    with open(MD_PATH, "a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n")


def _conn():
    _ensure_dir()
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,            -- ISO timestamp (pass in, do not generate here)
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,        -- BUY / SELL
            style TEXT,                  -- day / swing
            qty INTEGER,
            entry REAL, stop REAL, target REAL,
            status TEXT,                 -- proposed / approved / filled / exited
            rationale TEXT,
            chart_path TEXT
        )"""
    )
    return c


def log_trade(ts, symbol, action, style, qty, entry, stop, target,
              status, rationale, chart_path=None):
    """Insert a trade row. `ts` must be an ISO timestamp from the caller."""
    c = _conn()
    c.execute(
        """INSERT INTO trades
           (ts, symbol, action, style, qty, entry, stop, target, status, rationale, chart_path)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, symbol, action, style, qty, entry, stop, target, status, rationale, chart_path),
    )
    c.commit()
    rid = c.lastrowid
    c.close()
    return rid


def trades_since(iso_ts):
    """Return trade rows with ts >= iso_ts (for daily/weekly review)."""
    c = _conn()
    rows = c.execute("SELECT * FROM trades WHERE ts >= ? ORDER BY ts", (iso_ts,)).fetchall()
    c.close()
    return rows


# --- Monthly performance tracker -------------------------------------------
# Answers "am I on track?" with a number: account return vs SPY vs a 1-2%/month
# target. First row logged is the baseline; everything is measured from it.

def _perf_conn():
    c = _conn()
    c.execute(
        """CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,            -- ISO timestamp (caller supplies)
            date TEXT NOT NULL,          -- YYYY-MM-DD label
            netliq REAL NOT NULL,        -- account net liquidation value
            spy REAL,                    -- SPY close, for benchmark
            note TEXT
        )"""
    )
    return c


def log_perf(ts, date, netliq, spy=None, note=""):
    """Record one performance snapshot (call ~monthly). `ts` is an ISO string."""
    c = _perf_conn()
    cur = c.execute(
        "INSERT INTO performance (ts, date, netliq, spy, note) VALUES (?,?,?,?,?)",
        (ts, date, float(netliq), (float(spy) if spy is not None else None), note),
    )
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def perf_rows():
    c = _perf_conn()
    rows = c.execute(
        "SELECT date, netliq, spy, note FROM performance ORDER BY ts"
    ).fetchall()
    c.close()
    return rows


def write_perf_report(path=PERF_PATH, target_lo=1.0, target_hi=2.0):
    """Regenerate output/performance.md from the logged snapshots.

    Shows, per snapshot: NetLiq, month-over-month %, cumulative % since baseline,
    SPY cumulative %, alpha (you - SPY), and an on-track flag vs the
    target_lo..target_hi %/month band. Returns the markdown string.
    """
    from datetime import datetime
    rows = perf_rows()
    lines = [
        "# Monthly performance tracker",
        "",
        "**Question this answers:** am I on track? Target band: "
        f"**+{target_lo:.0f}–{target_hi:.0f}% / month averaged**, benchmark **SPY**. "
        "Trading returns are lumpy — judge the *cumulative* line and the average, "
        "not any single month. _Amounts are in the account base currency._",
        "",
        "| Date | NetLiq | MoM % | MoM gain | Cum % | Cum gain | SPY Cum % | vs SPY | On track? |",
        "|------|-------:|------:|---------:|------:|---------:|----------:|-------:|-----------|",
    ]
    if not rows:
        lines.append("| _(no snapshots yet)_ | | | | | | | | |")
        out = "\n".join(lines) + "\n"
        _ensure_dir()
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        return out

    base_date = datetime.strptime(rows[0][0], "%Y-%m-%d")
    base_nl, base_spy = rows[0][1], rows[0][2]
    prev_nl = None
    for i, (date, nl, spy, note) in enumerate(rows):
        if i == 0:
            lines.append(
                f"| {date} (baseline) | {nl:,.0f} | — | — | — | — | — | — | baseline |"
            )
            prev_nl = nl
            continue
        mom = (nl / prev_nl - 1) * 100 if prev_nl else 0.0
        mom_d = nl - prev_nl                       # money gained this period
        cum = (nl / base_nl - 1) * 100
        cum_d = nl - base_nl                       # total money gained since baseline
        spy_cum = ((spy / base_spy - 1) * 100) if (spy and base_spy) else None
        alpha = (cum - spy_cum) if spy_cum is not None else None
        # dollars ahead/behind vs having held SPY with the same starting capital
        alpha_d = (base_nl * alpha / 100) if alpha is not None else None
        months = max((datetime.strptime(date, "%Y-%m-%d") - base_date).days / 30.0, 0.0001)
        exp_lo = target_lo * months                # cumulative target band (lower bound)
        if cum >= exp_lo:
            flag = "[OK] on/above"
        elif cum >= 0:
            flag = "[~] below target"
        else:
            flag = "[!] drawdown"
        spy_s = f"{spy_cum:+.1f}%" if spy_cum is not None else "—"
        a_s = f"{alpha_d:+,.0f}" if alpha_d is not None else "—"
        lines.append(
            f"| {date} | {nl:,.0f} | {mom:+.1f}% | {mom_d:+,.0f} | {cum:+.1f}% | "
            f"{cum_d:+,.0f} | {spy_s} | {a_s} | {flag} |"
        )
        prev_nl = nl

    # summary footer
    last = rows[-1]
    cum = (last[1] / base_nl - 1) * 100
    cum_d = last[1] - base_nl
    months = max((datetime.strptime(last[0], "%Y-%m-%d") - base_date).days / 30.0, 0.0001)
    avg_mom = cum / months
    lines += [
        "",
        f"_Since baseline ({rows[0][0]}): cumulative **{cum:+.1f}% ({cum_d:+,.0f})** "
        f"over ~{months:.1f} months = **{avg_mom:+.2f}%/month** average. "
        f"Target is +{target_lo:.0f}–{target_hi:.0f}%/month._",
    ]
    out = "\n".join(lines) + "\n"
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return out
