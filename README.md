# trading-routine

Claude Routines for **day/swing trading US equities through Interactive Brokers**.

Scheduled routines (run as Claude Code skills) research the market, generate
annotated charts with reasoning, and prepare orders. **Human-in-the-loop**: Claude
proposes orders; nothing is sent to IBKR until you approve. The one exception is
*raising* a protective stop — that only de-risks, so the exit scan does it
automatically.

<img width="1916" height="1108" alt="image" src="https://github.com/user-attachments/assets/83070c33-2496-4068-94e6-efac82771d52" />

---

## Two styles: swing vs. intraday

| | **Swing** | **Intraday (day trade)** |
|---|---|---|
| **Hold time** | Days to ~2 weeks | Minutes to hours — **always flat by the close** |
| **Chart** | Daily bars | 5-minute bars |
| **What it rides** | A multi-day trend | A single day's momentum |
| **Trend filter** | 200-day moving average | The day's VWAP (volume-weighted average price) |
| **Data** | 15-min delayed is fine | Near-real-time (~1 min) |

Both look for the same thing — *price moving up with the trend behind it* — just on different clocks.

---

## Swing entries (daily chart)

A name has to pass all of these to be a swing candidate. The "why" matters:

- **Above the 200-day moving average** — this is the macro trend filter. Above it = uptrend, longs allowed. Below it = downtrend, skip (don't catch falling knives).
- **Holding a rising 8-day EMA** — the short-term momentum line. We enter when price is riding above a rising 8 EMA, *or* pulling back to it and bouncing. That pullback is the low-risk entry.
- **Resistance breakout (optional, strongest setup)** — a price ceiling that's been tested ≥ 2 times, then broken **on rising volume**. A breakout on weak volume usually fails, so those are skipped. (The tool that finds these returns the *nearest relevant* ceiling, so it won't flag a stale level from months ago.)

**Goal:** surface 1–2 genuinely high-conviction swing setups each session — actively hunt clean pre-breakout names, don't just trade whatever gapped up that morning.

---

## Intraday entries (5-minute chart)

Day trades only happen on days the market itself is cooperative. Each morning a
"day-trade conditions" score (0–10, from the VIX and whether SPY/QQQ are above
their VWAP) gates everything:

- **Score < 5 → no day trades that day.** Choppy or fearful tape; sit out.
- **Score ≥ 5 → hunt for a setup.**

A qualifying intraday long must be:

- **Above the session VWAP** — the day's fair-value line; above it = buyers in control.
- **Liquid enough** — average dollar-volume ≥ $5M per 5-min bar ($8M mid-day). (Dollar volume, not share count, because 70k shares means something totally different for a $12 stock vs. a $500 stock.)
- **Near its 8 EMA** — within ~2%; we want momentum, not something extended far above or collapsing below.
- **Worth the risk** — reward-to-risk ≥ 2:1 at the open, ≥ 3:1 mid-day (the thinner mid-day tape needs a bigger edge).

The entry is given as a **price zone** (a pullback to VWAP / 8 EMA / recent low), never at or above the current price — and the order is **pre-built the moment the setup is validated**, so when you approve it fires instantly instead of chasing a price that moved in the meantime.

**Goal:** take one quality day trade *every session the tape allows* — but **quality only, never forced.** No solid, low-risk candidate = **no trade that day.** A flat day is a correct outcome, not a failure.

---

## Position sizing (how many shares)

Always the same formula, so every trade risks the same small slice of the account:

```
shares = (account × risk%) ÷ (entry − catastrophic stop)
```

- **risk%** = 0.5% for day trades, 1.0% for swings.
- The stop in that formula is the **catastrophic stop** (−8% hard floor) — the loss that's *actually* enforced — so the risk-per-trade is real, not fictional.
- **Caps:** no single position > 20% of the account; max 3 day / 5 swing positions at once; if the account is down 3% on the day, stop opening anything new.

---

## Exits — cap the loss, let the winner run

- **Catastrophic stop (−8%)** — placed at entry, **always rests at the broker**, never removed. The hard floor beneath everything. It's also what sizing is measured from.
- **Trailing stop (runs the winner):**
  - *Swing:* a **2-bar trailing stop on daily bars** — it ratchets **up** as the trade makes new highs and never moves down; the trade exits when price finally drops below it. Plus a hard exit if the daily close breaks back below the 200-day average.
  - *Intraday:* the **2-bar ratchet is primary**, with a **VWAP-based floor** beneath it (`VWAP − 1.5×ATR`). On a strong trend day there's **no fixed profit target** — the trailing stop decides when the move is done, so a runaway winner isn't capped. If a stop gets shaken out on a trend day but price reclaims VWAP within an hour, a **re-entry is flagged** (the trend's still intact).
- **`no_exit_at_loss` (your rule):** don't bail on small dips while underwater — hold and wait for recovery — but this is **bounded by the −8% floor**, so a position can never lose more than the catastrophic amount. (Exception you've used deliberately: cut a deep, going-nowhere loser to free cash for a clearly better setup.)
- **Raising a target:** if a winner is recovering toward a resting sell-limit, the scan flags **"raise the target"** before it caps you out — but moving it needs your OK (selling higher isn't de-risking, so it isn't automatic).
- **Approvals:** the scan **auto-tightens** stops (de-risk only, no approval). **Closing a position always needs your explicit OK.**

> These exit rules were tuned from a real trade — see the AMD 2026-06-12 retro in
> `output/journal.md` for why the intraday exit leads with the ratchet (not a tight
> VWAP trail) and drops the fixed target on trend days.

---

## The life of a trade (which routine does what)

```
  FIND          VALIDATE         ENTER            MANAGE              EXIT
  premarket  →  market-open  →  market-open  →   exit-scan      →   exit-scan
  research      (re-check at    (you approve,    (trail the stop     (stop hit or
  (ranked       the open)        order fires)     up, raise target,   safety exit;
  candidates)                                     re-validate any     you approve
                                                  unfilled orders)    the close)
```

- **Pre-market research** — screens the watchlist + market movers, applies the rules above, and hands you a ranked shortlist with charts and proposed entry/stop/target. **Recommends only — places nothing.**
- **Market-open execution** — first reviews what you already hold and any resting orders (stop still there? target about to fill? gapped overnight?), then re-validates the morning's candidates against the *actual* open and builds approval-ready orders.
- **Exit scan** — the position manager. Trails stops up, raises targets, runs safety exits, and now also **re-checks unfilled entry orders** (cancel a stale/invalidated limit, re-adjust a drifted one, and a Friday sweep so a weekend gap can't fill a stale order on Monday).
- **Daily summary** — the day's P&L, a retro on every closed trade (what we got vs. what was possible, mistakes, lessons), and end-of-day charts.
- **Weekly review** — the week's stats, what the rules got right/wrong, and proposed tweaks for next week.

---

## Schedule

US equities trade 9:30–16:00 ET. You're in CET; times shift independently on US vs
EU DST, so all logic anchors to US/Eastern and converts dynamically.

| Routine | Skill | US Eastern | CET (approx) | Cadence |
|---|---|---|---|---|
| Pre-market research | `/premarket-research` | 7:30 | ~13:30 | once |
| Market-open execution | `/market-open` | 9:30 (+30m) | ~15:30 | once |
| Exit scan | `/exit-scan` | 9:30–16:00 | ~15:30–22:00 | **day trades: every 5–15 min · swings: hourly** |
| Daily summary | `/daily-summary` | after close | ~22:15 | once (incl. exit retro) |
| Weekly review (Fri) | `/weekly-review` | after close | ~22:15 Fri | once |

**Exit-scan cadence:** the protective stop rests at IBKR and fires between scans
regardless — the scan's job is to *raise* it. Active day-trade runner → every **5 min**
(matches the 5-min bar close); consolidating → 10–15 min; last 30 min to close → 5 min
+ a hard MUST-CLOSE check at T-15. Swing-only days → hourly is plenty.

---

## Data sources
- **Day-trade signals:** `lib.realtime.get_intraday_bars(sym, "5m")` — yfinance, **~1 min lag** (near-real-time). Optional **Webull** tick-level via `config/webull_creds.json`. The latest bar is still forming, so the ratchet uses *completed* bars.
- **Positions / orders / swing daily bars:** IBKR (`ib_async`). The IBKR price feed is **15-min delayed** and is **not** used for day-trade signals (delayed is fine for daily-bar swings).

## Setup

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

1. Install & launch **IB Gateway**, log into the **paper** account first (free, delayed data).
2. In Gateway: Configure → Settings → API → *Enable ActiveX and Socket Clients*; add `127.0.0.1` to Trusted IPs; set the Socket port to match `lib.ibkr.DEFAULT_PORT` (**4001** here — IB Gateway's paper default is often 4002, so keep the two consistent).
3. (Recommended) automate the daily Gateway re-login with **IBC**.
4. Verify the connection:
   ```powershell
   python -c "from lib.ibkr import connect; ib=connect(); print(ib.accountSummary()[:3]); ib.disconnect()"
   ```

Paper vs live is the **login**, not the port (paper = `DU…`, live = `U…`).
`connect()` defaults to `require_paper=True` and refuses a live account unless explicitly overridden.

## Configure
- `config/watchlist.txt` — core tickers you always want checked (the screener adds more; held names are auto-added).
- `config/risk.yaml` — **set `account.size_usd` to your balance**; tune risk %, position caps, daily-loss limit, catastrophic %.
- `config/strategy.yaml` — signal thresholds: VWAP, dollar-volume floors, R:R minimums, trend-day score, ratchet lookback, VWAP-trail buffer, stale-order handling, `no_exit_at_loss`.

## Running

This runs in **assisted-trigger** mode (your laptop isn't always on): open Claude Code
when you sit down to trade and invoke the relevant skill. The schedule is a
reminder/checklist, not an unattended bot. For an active day-trade position, re-run
`/exit-scan` every ~5 min — the resting broker-side stop protects you between runs.
Move to an always-on VM + cron later for hands-off scheduling.

## Notes
- Paper account + delayed data is **free** and runs the whole system end to end.
- Real-time data (small monthly fee) and commissions only apply on a funded/live account.
- See [`CLAUDE.md`](CLAUDE.md) for architecture and design decisions; [`docs/STRATEGY.md`](docs/STRATEGY.md) for the full strategy + an honest critique; `output/journal.md` for the running trade journal and retros.

> Not financial advice. Trading involves risk of loss. Test thoroughly on paper.
