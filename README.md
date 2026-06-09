# trading-routine

Claude Routines for **day/swing trading US equities through Interactive Brokers**.

Five scheduled routines (run as Claude Code skills) research the market, generate
annotated charts with reasoning, and prepare orders. **Human-in-the-loop**: Claude
proposes orders; nothing is sent to IBKR until you approve.

<img width="1916" height="1108" alt="image" src="https://github.com/user-attachments/assets/83070c33-2496-4068-94e6-efac82771d52" />



| Routine | Skill | US Eastern | CET |
|---|---|---|---|
| Pre-market research | `/premarket-research` | 7:30 | ~13:30 |
| Market-open execution | `/market-open` | 9:30 (+30m) | ~15:30 |
| Hourly exit scan | `/exit-scan` | 10:30–16:00 | ~16:30–22:00 |
| Daily summary | `/daily-summary` | after close | ~22:15 |
| Weekly review (Fri) | `/weekly-review` | after close | ~22:15 |

## Setup

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

1. Install & launch **IB Gateway**, log into the **paper** account.
2. In Gateway: Configure → Settings → API → *Enable ActiveX and Socket Clients*; set Socket port to **4002**; add `127.0.0.1` to Trusted IPs.
3. (Recommended) automate the daily Gateway re-login with **IBC**.
4. Verify the connection:
   ```powershell
   python -c "from lib.ibkr import connect; ib=connect(); print(ib.accountSummary()[:3]); ib.disconnect()"
   ```

## Configure

- `config/watchlist.txt` — your core tickers (the screener adds more).
- `config/risk.yaml` — **set `account.size_usd` to your balance**; tune risk %, limits.

## Running

This runs in **assisted-trigger** mode (your laptop isn't always on): open Claude Code
when you sit down to trade and invoke the relevant skill. The schedule is your
reminder/checklist, not an unattended bot. Move to an always-on VM + cron later for
hands-off scheduling.

## Notes

- Paper account + delayed data is **free** and runs the entire system end to end.
- Real-time data (small monthly fee) and commissions only apply on a funded/live account.
- See `CLAUDE.md` for architecture and design decisions.

> Not financial advice. Trading involves risk of loss. Test thoroughly on paper.
