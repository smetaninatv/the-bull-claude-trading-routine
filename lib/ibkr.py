"""Interactive Brokers connection + order helpers (ib_async).

HUMAN-IN-THE-LOOP: orders are built with transmit=False. Nothing is sent to
IBKR until `transmit()` is called after the user approves.
"""
from ib_async import IB, LimitOrder, Stock, StopOrder

HOST = "127.0.0.1"
# This machine's Gateway API socket listens on 4001. Note: the port does NOT
# decide paper vs live — the login does. Paper accounts are DU..., live are U....
DEFAULT_PORT = 4001
CLIENT_ID = 17


def connect(host=HOST, port=DEFAULT_PORT, client_id=CLIENT_ID, readonly=False,
            require_paper=True):
    """Connect to IB Gateway/TWS.

    SAFETY: by default this refuses to stay connected to a live (U...) account.
    Pass require_paper=False only when you deliberately intend live trading.
    """
    ib = IB()
    ib.connect(host, port, clientId=client_id, readonly=readonly)
    if require_paper:
        live = [a for a in ib.managedAccounts() if not a.startswith("DU")]
        if live:
            ib.disconnect()
            raise RuntimeError(
                f"Refusing to proceed: connected to non-paper account(s) {live}. "
                "Log IB Gateway into the PAPER account (DU...), or call "
                "connect(require_paper=False) to override deliberately."
            )
    return ib


def account_value(ib, tag="NetLiquidation"):
    for v in ib.accountValues():
        if v.tag == tag and v.currency in ("USD", "BASE"):
            return float(v.value)
    return None


def equity(ib):
    """Net liquidation value (account equity), or None if unavailable."""
    return account_value(ib, "NetLiquidation")


def day_trades_remaining(ib):
    """Day trades IBKR will still allow under the PDT rule.

    Returns an int, or None if not reported. IBKR uses a large/negative value
    (e.g. -1) to mean effectively unlimited (account >= $25k or not a margin
    account). For PDT-restricted accounts it counts down toward 0.
    """
    for v in ib.accountValues():
        if v.tag == "DayTradesRemaining":
            try:
                return int(float(v.value))
            except (TypeError, ValueError):
                return None
    return None


def positions(ib):
    return ib.positions()


def portfolio(ib):
    """Portfolio items: position size, market price, avg cost, unrealized P&L.

    Richer than positions() — use this for analysis and the no-exit-at-loss check
    (item.unrealizedPNL, item.averageCost, item.marketPrice).
    """
    return ib.portfolio()


def open_orders(ib):
    """All open orders as Trade objects (contract + order + orderStatus).

    Used to find the current resting stop order so the exit scan can raise it
    (and to avoid double-sending).
    """
    ib.reqAllOpenOrders()
    return list(ib.openTrades())


def qualify(ib, symbol, exchange="SMART", currency="USD"):
    c = Stock(symbol, exchange, currency)
    ib.qualifyContracts(c)
    return c


def prepare_entry(ib, symbol, action, quantity, entry, stop):
    """Entry limit + attached hard protective stop, NO fixed take-profit.

    Preferred over prepare_bracket for this strategy: the 2-bar TRAILING stop
    (managed by the exit scan) governs the upside, so a fixed target would only
    cap the winner. `stop` should be the catastrophic floor (see
    lib.config.catastrophic_stop). Both orders have transmit=False until approved.
    Returns (contract, [parent, protective_stop]).
    """
    contract = qualify(ib, symbol)
    action = action.upper()
    exit_action = "SELL" if action == "BUY" else "BUY"
    parent = LimitOrder(action, quantity, entry,
                        orderId=ib.client.getReqId(), transmit=False)
    protective = StopOrder(exit_action, quantity, stop,
                           orderId=ib.client.getReqId(),
                           parentId=parent.orderId, transmit=False)
    return contract, [parent, protective]


def prepare_bracket(ib, symbol, action, quantity, entry, stop, target):
    """Build a bracket (entry limit + stop-loss + take-profit) WITHOUT sending.

    Use only if you deliberately want a fixed take-profit (e.g. a partial
    scale-out). For the default trailing-stop strategy, prefer prepare_entry.
    Returns (contract, [parent, take_profit, stop_loss]). All have transmit=False.
    """
    contract = qualify(ib, symbol)
    bracket = ib.bracketOrder(
        action.upper(), quantity,
        limitPrice=entry, takeProfitPrice=target, stopLossPrice=stop,
    )
    for o in bracket:
        o.transmit = False
    return contract, list(bracket)


def transmit(ib, contract, bracket):
    """Send a previously-prepared bracket AFTER the user approves it.

    Placing the last child with transmit=True releases the whole group.
    """
    bracket[-1].transmit = True
    return [ib.placeOrder(contract, o) for o in bracket]
