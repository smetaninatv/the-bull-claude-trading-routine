"""Trade journal: human-readable markdown notes + a structured SQLite log.

The markdown file is the narrative ("why we bought/sold", with chart links).
The SQLite table is for the daily/weekly review aggregations.
"""
import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD_PATH = os.path.join(_ROOT, "output", "journal.md")
DB_PATH = os.path.join(_ROOT, "output", "journal.db")


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
