# trading-routine

Claude Routines for **day/swing trading US equities through Interactive Brokers**.

Scheduled routines (run as Claude Code skills) research the market, generate
annotated charts with reasoning, and prepare orders. **Human-in-the-loop**: Claude
proposes orders; nothing is sent to IBKR until you approve. The one exception is
*raising* a protective stop — that only de-risks, so the exit scan does it
automatically.

<img width="1916" height="1108" alt="image" src="https://github.com/user-attachments/assets/83070c33-2496-4068-94e6-efac82771d52" />

## Schedule

US equities trade 9:30–16:00 ET. The user is in CET; times shift independently on
US vs EU DST, so all logic anchors to US/Eastern and converts dynamically.

| Routine | Skill | US Eastern | CET (approx) | Cadence |
|---|---|---|---|---|
| Pre-market research | `/premarket-research` | 7:30 | ~13:30 | once |
| Market-open execution | `/market-open` | 9:30 (+30m) | ~15:30 | once |
| Exit scan | `/exit-scan` | 9:30–16:00 | ~15:30–22:00 | **day trades: every 5–15 min · swings: hourly** |
| Daily summary | `/daily-summary` | after close | ~22:15 | once (incl. exit retro) |
| Weekly review (Fri) | `/weekly-review` | after close | ~22:15 Fri | once |

**Exit-scan cadence detail:** the protective stop rests at IBKR and fires between
scans regardless — the scan's job is to *raise* it. Day-trade runner on a trend day →
every **5 min** (matches the 5-min bar close); consolidating → 10–15 min; last 30 min
to close → 5 min + a hard MUST-CLOSE check at T-15. Swing positions on daily bars only
need hourly.

## Strategy

Full detail and a critical assessment live in [`docs/STRATEGY.md`](docs/STRATEGY.md).
Summary:

### Entries — Swing (daily chart)
- **200 SMA** macro filter — long only above it.
- **8 EMA** momentum/timing — enter holding above a rising 8 EMA, or on a pullback to it that resumes.
- **Resistance breakout** — a level tested ≥ 2 times, then breaks out **on expanding volume** (low-volume breakouts are skipped).

### Entries — Day (5-min chart)
- **Above session VWAP** — long bias; entries on a reclaim/hold of VWAP.
- **Dollar volume ≥ $5M/bar** (3-bar avg; ≥ $8M mid-day) — replaces the old raw 70k-share floor, which meant different things for a $12 vs a $500 stock.
- **EMA8 proximity** — price within ~2% of EMA8 (flag if 2–4% below, skip if > 5% below).
- **R:R ≥ 2.0** at the open, **≥ 3.0** mid-day (thinner tape needs a wider edge).
- Only when `day_trade_conditions()` scores **≥ 5** (VIX + SPY/QQQ vs VWAP). Below 5 → swing only.
- Entry given as a **zone** (ATR/VWAP/8-EMA pullback), never at or above market; orders pre-staged at validation so approval → instant send.

### Sizing & risk (`config/risk.yaml`)
- **Fixed fractional:** day **0.5%** / swing **1.0%** of account per trade, sized off the **catastrophic stop** so risk-per-trade is real: `shares = (account × risk%) ÷ |entry − catastrophic_stop|`.
- **Catastrophic stop:** −8% hard floor, **always rests at the broker**.
- **Caps:** single position ≤ 20% of account; concurrent day ≤ 3 / swing ≤ 5; daily loss limit 3% → stop opening new trades.
- **PDT guard** + delayed-data guard applied in market-open.

### Exits
- **Catastrophic 8% floor** — placed at entry, always honored (the hard minimum beneath everything).
- **Day trade trailing stop:** the **2-bar ratchet is primary**, the **VWAP-trail is a floor** beneath it — `day_trade_stop()` = `max(2-bar ratchet, VWAP − 1.5×ATR, current stop)`, up-only. **No fixed target on a trend day** (score ≥ 8) — the trail runs the winner. After a stop fires on a trend day, a **VWAP-reclaim within 60 min is flagged as a re-entry**. **MUST-CLOSE** in the last 15 min — no overnight day trades.
- **Swing trailing stop:** strict-up **2-bar ratchet on daily bars**; close below the 200 SMA is a hard trend-break exit.
- **`no_exit_at_loss`** (user-mandated): hold small dips while underwater rather than realizing a loss — **bounded by the 8% catastrophic floor**, so tail risk is capped.
- The exit scan **auto-raises** resting stops (no approval — de-risk only); **position-closing exits need explicit approval**.

> These rules were tuned from a live retro — see the AMD 2026-06-12 entry in `output/journal.md` for why the day-trade exit uses a ratchet (not a tight VWAP-trail) and drops the fixed target on trend days.

## Data sources
- **Day-trade signals:** `lib.realtime.get_intraday_bars(sym, "5m")` — yfinance, **~1 min lag** (near-real-time). Optional **Webull** tick-level data via `config/webull_creds.json`. The last bar is still forming, so the ratchet uses completed bars.
- **Positions / orders / swing daily bars:** IBKR (`ib_async`). The IBKR price feed is **15-min delayed** and is **not** used for day-trade signals (delayed is fine for daily-bar swing entries).

## Setup

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

1. Install & launch **IB Gateway**, log into the **paper** account first (free, delayed data).
2. In Gateway: Configure → Settings → API → *Enable ActiveX and Socket Clients*; add `127.0.0.1` to Trusted IPs; set the Socket port to match `lib.ibkr.DEFAULT_PORT` (**4001** on this setup — IB Gateway's paper default is often 4002, so keep the two consistent).
3. (Recommended) automate the daily Gateway re-login with **IBC**.
4. Verify the connection:
   ```powershell
   python -c "from lib.ibkr import connect; ib=connect(); print(ib.accountSummary()[:3]); ib.disconnect()"
   ```

Paper vs live is determined by the **login**, not the port (paper = `DU…`, live = `U…`).
`connect()` defaults to `require_paper=True` and refuses a live account unless explicitly overridden.

## Configure
- `config/watchlist.txt` — core tickers (the screener adds more; held names are auto-added).
- `config/risk.yaml` — **set `account.size_usd` to your balance**; tune risk %, limits, catastrophic %.
- `config/strategy.yaml` — signal thresholds: VWAP, dollar-volume floors, R:R minimums, trend-day score, ratchet lookback, VWAP-trail buffer, `no_exit_at_loss`.

## Running

This runs in **assisted-trigger** mode (your laptop isn't always on): open Claude Code
when you sit down to trade and invoke the relevant skill. The schedule is your
reminder/checklist, not an unattended bot. For an active day-trade position, re-run
`/exit-scan` every ~5 min; the resting broker-side stop protects you between runs.
Move to an always-on VM + cron later for hands-off scheduling.

## Notes
- Paper account + delayed data is **free** and runs the entire system end to end.
- Real-time data (small monthly fee) and commissions only apply on a funded/live account.
- See [`CLAUDE.md`](CLAUDE.md) for architecture and design decisions; `output/journal.md` for the running trade journal and retros.

> Not financial advice. Trading involves risk of loss. Test thoroughly on paper.
