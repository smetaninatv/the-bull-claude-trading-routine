# trading-routine

Claude Code routines for **day/swing trading US equities through Interactive Brokers**.

The routines research the market, generate annotated charts with written
reasoning, and prepare orders. **Human-in-the-loop:** Claude proposes orders and
nothing that opens or closes a trade is sent to IBKR until you approve it. The
only automatic actions are ones that reduce risk (raising a stop, locking in a
gain).

<img width="1916" height="1108" alt="image" src="https://github.com/user-attachments/assets/83070c33-2496-4068-94e6-efac82771d52" />

## How it trades (short version)

Three ways to find a long:

- **Swing momentum** — a Stage-2 uptrend bought on a pullback, never extended.
- **Day trade** — above VWAP, liquid, high relative volume, real catalyst; only
  on days the market tape scores ≥ 5/10. Plus five auto-detected scalp setups.
- **Dip-reversion** — a quality name that dropped hard and has started to turn
  up (never a falling knife).

Every trade risks a fixed slice of the account, enters on a limit at a pullback
level (never chasing), carries a catastrophic stop, and is then managed by a
ratcheting trailing stop that only moves up.

**Full rules, numbers and rationale: [`docs/STRATEGY.md`](docs/STRATEGY.md).**

## The life of a trade

```
  FIND          VALIDATE         ENTER            MANAGE              EXIT
  premarket  →  market-open  →  market-open  →   exit-scan      →   exit-scan
  research      (re-check at    (you approve,    (trail the stop,    (stop hit or
  (ranked       the open)        order fires)     lock gains,         safety exit;
  candidates)                                     re-check orders)    you approve)
```

| Routine | Skill | US Eastern | CET (approx) |
|---|---|---|---|
| Pre-market research | `/premarket-research` | 7:30 | ~13:30 |
| Market-open execution | `/market-open` | 9:30 (+30 min) | ~15:30 |
| Exit scan | `/exit-scan` | 9:30–16:00 | ~15:30–22:00 |
| Daily summary | `/daily-summary` | after close | ~22:15 |
| Weekly review | `/weekly-review` | Fri after close | ~22:15 Fri |

Session hours (early closes, holidays, overnight) come from IBKR's published
schedule, not from fixed dates. Orders may fill outside regular hours and
overnight, and the exit scan runs whenever a session is live.

- **Pre-market research** — ranked shortlist with charts, entry/stop/target and
  sizing; can prepare day-trade orders for strong fits, placed only on your OK.
- **Market-open** — reviews holdings and resting orders first, re-validates the
  morning's candidates against the live open, builds approval-ready orders.
- **Exit scan** — run every 5–15 min with an active day trade, hourly for swings.
  The stop rests at IBKR between runs; the scan raises it.
- **Daily summary** — P&L, a retro on every closed trade, end-of-day charts.
- **Weekly review** — stats, what the rules got right/wrong, proposed tweaks
  (never auto-applied).

### Running it: the trading desk agent

```bash
claude --agent trading-desk
```

The desk checks the US/Eastern clock and its run log, runs whichever routine is
due, asks you to approve any order, then schedules its own next check (every
5–15 min with an active day trade, hourly for swings). Leave the session open
while you trade; it stops after the daily summary and tells you when the next
session starts. You can still run any skill by hand, e.g. `/exit-scan`.

Check-ins only happen while that session is open (the laptop isn't always on).
Between them, your protection is the stop resting at IBKR.

## Setup

Needs **Python ≥ 3.10** (`ib_async` requirement; macOS's built-in 3.9 is too
old, so use e.g. `brew install python@3.12`).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

1. Install and launch **IB Gateway**, logged into the paper account (free,
   delayed data) or your live account (see below).
2. Gateway → Configure → Settings → API: enable *ActiveX and Socket Clients*,
   trust `127.0.0.1`, and set the socket port to match `lib.ibkr.DEFAULT_PORT`
   (**4001** here; Gateway's paper default is often 4002 — keep them consistent).
3. (Recommended) automate the daily Gateway re-login with **IBC**.
4. Check the connection:
   ```bash
   python -c "from lib.ibkr import connect; ib=connect(); print(ib.accountSummary()[:3]); ib.disconnect()"
   ```

Paper vs live is decided by the **login** (paper = `DU…`, live = `U…`), not the
port. Live trading is controlled by `account.allow_live` in `config/risk.yaml`:
`true` accepts a live login (every connection prints a **LIVE — REAL MONEY**
banner and every approval ask says so), `false` refuses it (paper only).

## Configure

- `config/risk.yaml` — **set `account.size_usd` to your balance**; risk %,
  position caps, daily loss limit, catastrophic stop %.
- `config/strategy.yaml` — signal thresholds, trailing-stop settings,
  unfilled-order handling, `no_exit_at_loss`.
- `config/watchlist.txt` — optional pins, empty by default (the universe is
  discovered live each run).
- `config/webull_creds.json` — optional, for Webull real-time data (see
  `requirements.txt`).

## Data

- **Day-trade signals:** `lib.realtime` — yfinance (~1 min lag) or Webull.
- **Positions, orders, swing daily bars:** IBKR (15-min delayed on paper; not
  used for day-trade signals).

## More

- [`CLAUDE.md`](CLAUDE.md) — architecture and invariants.
- [`docs/STRATEGY.md`](docs/STRATEGY.md) — the rules, open issues, lessons log.
- `output/journal.md` — running trade journal and retros.

> Not financial advice. Trading involves risk of loss. Test thoroughly on paper.
