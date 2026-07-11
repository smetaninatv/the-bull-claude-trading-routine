"""Unattended exit scan — the automated slice of /exit-scan (user-authorized 2026-07-11).

Runs headless every ~15 min on a local schedule and manages open longs WITHOUT
human approval, within hard de-risk-only guardrails. It does two things:

  1. STOP RAISE (always safe): keep each profitable long's protective stop at its
     2-bar trailing level — raise an existing stop, or place one if a profitable
     position has none. The resting STP at IBKR is the real-time primary exit; the
     broker fires it at the exact price between runs. Never lowers a stop, never
     places a stop above market, never puts a stop below cost.

  2. AUTO-CLOSE on a *secondary* trigger (breakeven+ only): if a completed daily
     close is below the 200 SMA (swing trend-break), flatten the position at
     market — but ONLY if the fill is at breakeven-or-better (price >= avg cost),
     so no_exit_at_loss is never violated. The primary 2-bar-stop exit is handled
     by the resting stop itself; a market close here is the safety net for that
     plus the trend-break the resting stop can't see.

Hard rules (never crossed):
  * NEVER realizes a loss — every close is gated on price >= avg cost. Underwater
    names are left completely untouched (honors the user's naked-hold decision).
  * NEVER strands a resting order: before a market close it cancels every resting
    SELL for the symbol and RE-READS to confirm they're gone; if any can't be
    cancelled (e.g. a prior-session order), it does NOT sell (an orphan resting
    order would flip the account short) — it logs + notifies for manual action.
  * NEVER auto-opens anything. Long-only. Only touches existing positions.

Default is DRY-RUN. Pass --live to transmit. Only acts while the US session is open.
"""

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.realtime import market_session               # noqa: E402
from lib.ibkr import connect, portfolio, open_orders  # noqa: E402
from lib.data import historical_bars                  # noqa: E402
from lib.indicators import add_indicators, ratchet_2bar_stop  # noqa: E402
from lib.config import load_strategy                  # noqa: E402
from lib.journal import append_note                   # noqa: E402
from ib_async import StopOrder, MarketOrder           # noqa: E402

LOG = ROOT / "output" / "auto_exit_scan.log"
TAG = "auto_exit_scan"
CLIENT_ID = 0
RATCHET_WINDOW = 20  # trailing daily bars for the ratchet (matches the manual scan)


def _log(msg):
    ts = datetime.datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts}\t{msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _notify(title, body):
    ps = f"""
$ErrorActionPreference='SilentlyContinue'
try {{
  [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null
  $t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $n=$t.GetElementsByTagName('text')
  $n.Item(0).AppendChild($t.CreateTextNode({title!r}))|Out-Null
  $n.Item(1).AppendChild($t.CreateTextNode({body!r}))|Out-Null
  $toast=[Windows.UI.Notifications.ToastNotification]::new($t)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Claude.AutoExitScan').Show($toast)
}} catch {{}}
"""
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True)


def _sell_orders(oo, sym):
    return [t for t in oo if t.contract.symbol == sym and t.order.action == "SELL"]


def _oca_group_for(oo, sym):
    for t in oo:
        if t.contract.symbol == sym and t.order.ocaGroup:
            return t.order.ocaGroup
    return None


def _manage_stop(ib, it, oo, ratchet, last, avg, live):
    """Raise/place the resting protective stop to the ratchet (de-risk only)."""
    sym = it.contract.symbol
    if not (avg <= ratchet < last):          # must lock profit AND sit below market
        _log(f"{sym}: ratchet {ratchet:.2f} not a valid raise vs cost {avg:.2f}/price {last:.2f} — no stop change")
        return
    stops = [t for t in oo if t.contract.symbol == sym and t.order.orderType == "STP"]
    ours = [t for t in stops
            if t.order.orderRef == TAG or (t.order.clientId == CLIENT_ID and t.order.parentId in (0, None))]
    if ours:
        t = max(ours, key=lambda x: x.order.auxPrice)
        if ratchet <= t.order.auxPrice:
            _log(f"{sym}: stop {t.order.auxPrice:.2f} already >= ratchet {ratchet:.2f} — no raise")
            return
        _log(f"{sym}: RAISE stop {t.order.auxPrice:.2f} -> {ratchet:.2f} [id={t.order.orderId}]"
             + ("" if live else "  [DRY-RUN]"))
        if live:
            o = t.order; o.auxPrice = ratchet; o.parentId = 0; o.transmit = True
            ib.placeOrder(t.contract, o); ib.sleep(1.0)
    else:
        grp = _oca_group_for(oo, sym)
        cur = max((t.order.auxPrice for t in stops), default=None)
        if cur is not None and ratchet <= cur:
            _log(f"{sym}: existing stop {cur:.2f} >= ratchet {ratchet:.2f} — no raise")
            return
        _log(f"{sym}: PLACE stop {ratchet:.2f}" + (f" in OCA {grp}" if grp else " (standalone)")
             + ("" if live else "  [DRY-RUN]"))
        if live:
            stp = StopOrder("SELL", int(it.position), ratchet)
            stp.tif = "GTC"; stp.orderRef = TAG; stp.transmit = True
            if grp:
                stp.ocaGroup = grp; stp.ocaType = 1
            ib.placeOrder(it.contract, stp); ib.sleep(1.0)


def _auto_close(ib, it, oo, reason, last, avg, live):
    """Flatten at market — only after confirming no resting SELL will be stranded."""
    sym = it.contract.symbol
    qty = int(it.position)
    resting = _sell_orders(oo, sym)
    if live:
        for t in resting:
            ib.cancelOrder(t.order)
        ib.sleep(2.0)
        still = _sell_orders(open_orders(ib), sym)
        if still:
            msg = (f"{sym}: EXIT ({reason}) BLOCKED — {len(still)} resting SELL order(s) "
                   f"could not be cancelled; NOT selling (orphan-short risk). Manual action needed.")
            _log(msg); _notify(f"Manual exit needed: {sym}", msg)
            return
        ib.placeOrder(it.contract, MarketOrder("SELL", qty))
        ib.sleep(1.5)
    pnl = (last - avg) * qty
    msg = f"{sym}: AUTO-CLOSED {qty} @~{last:.2f} ({reason}); est P&L {pnl:+.0f} (cost {avg:.2f})"
    _log(msg + ("" if live else "  [DRY-RUN]"))
    if live:
        _notify(f"Auto-closed {sym}", msg)
        append_note(f"  - **AUTO-EXIT {sym}** ({datetime.datetime.now(pytz.timezone('US/Eastern')):%Y-%m-%d %H:%M ET}): "
                    f"{reason}; sold {qty} @~{last:.2f}, est P&L {pnl:+.0f}. Unattended auto_exit_scan.")


def process(ib, live, lookback):
    pf = portfolio(ib)
    if not pf:
        _log("flat — nothing to scan")
        return
    oo = open_orders(ib)
    for it in pf:
        sym = it.contract.symbol
        if int(it.position) <= 0:
            continue
        avg, last = it.averageCost, it.marketPrice
        if last is None or last <= 0:
            _log(f"{sym}: no live price — skip"); continue
        if last < avg:
            _log(f"{sym}: underwater ({last:.2f} < cost {avg:.2f}) — hold, no action"); continue
        try:
            full = add_indicators(historical_bars(ib, sym, "300 D", "1 day"))
            ratchet = round(float(ratchet_2bar_stop(full.tail(RATCHET_WINDOW), lookback, False)), 2)
            close_prev = float(full["close"].iloc[-2])          # last COMPLETED daily close
            sma200_prev = float(full["sma200"].iloc[-2])
        except Exception as e:
            _log(f"{sym}: data/indicator failed ({e}) — skip"); continue

        # EXIT check first (breakeven+ only). Primary stop-hit is the broker's job;
        # this is the safety net + the trend-break the resting stop can't see.
        reason = None
        if last <= ratchet:
            reason = f"2-bar ratchet stop hit ({last:.2f} <= {ratchet:.2f})"
        elif sma200_prev == sma200_prev and close_prev < sma200_prev:   # not-NaN and below
            reason = f"daily close {close_prev:.2f} below 200 SMA {sma200_prev:.2f} (trend break)"
        if reason:
            if last >= avg:
                _auto_close(ib, it, oo, reason, last, avg, live)
            else:
                _log(f"{sym}: {reason} BUT underwater ({last:.2f} < {avg:.2f}) — HOLD (no_exit_at_loss)")
            continue

        # No exit → keep the protective stop trailed up.
        _manage_stop(ib, it, oo, ratchet, last, avg, live)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually transmit (default: dry-run)")
    args = ap.parse_args()
    sess = market_session()
    if sess != "open":
        _log(f"market session '{sess}' (not open) — skip"); return
    lookback = int(load_strategy().get("exits", {}).get("trailing_lookback", 2))
    mode = "LIVE" if args.live else "DRY-RUN"
    _log(f"=== auto exit scan start ({mode}) ===")
    ib = connect(require_paper=False, client_id=CLIENT_ID)
    try:
        process(ib, args.live, lookback)
    finally:
        ib.disconnect()
    _log(f"=== auto exit scan done ({mode}) ===")


if __name__ == "__main__":
    main()
