"""Interactive Brokers connection + order helpers (ib_async).

HUMAN-IN-THE-LOOP: orders are built with transmit=False. Nothing is sent to
IBKR until `transmit()` is called after the user approves.
"""
import math

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


def open_orders(ib, settle=1.0, retries=2):
    """All open orders as Trade objects (contract + order + orderStatus).

    Used to find the current resting stop/target orders so the exit scan can
    raise them (and to avoid double-sending).

    On this Gateway `ib.openTrades()` frequently comes back EMPTY if read
    immediately — `reqAllOpenOrders()` is async and the response needs a moment
    to populate over the event loop. A single empty read was falsely flagging
    every position UNPROTECTED (2026-06-12). So: request, wait `settle` seconds,
    and retry up to `retries` times before trusting an empty result. A genuinely
    flat book still returns [] after the retries.
    """
    trades = []
    for _ in range(retries + 1):
        ib.reqAllOpenOrders()
        ib.sleep(settle)
        trades = list(ib.openTrades())
        if trades:
            return trades
    return trades


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


def scaled_split(quantity, partial_frac=0.5):
    """Split `quantity` into (partial, runner) shares for a scale-out.

    Both halves are >= 1 (a scale-out needs at least 2 shares). The partial is
    `floor(quantity * partial_frac)`, the runner gets the remainder.
    """
    if quantity < 2:
        raise ValueError(
            "scale-out needs quantity >= 2 (can't split 1 share); "
            "use prepare_entry / prepare_bracket instead"
        )
    q1 = max(1, math.floor(quantity * partial_frac))
    q2 = quantity - q1
    if q2 < 1:                       # partial_frac rounded up to the whole size
        q1, q2 = quantity - 1, 1
    return q1, q2


def prepare_scaled_bracket(ib, symbol, action, quantity, entry, stop,
                           target1=None, target2=None, partial_frac=0.5):
    """Scale-out entry: take a partial at target1 (default 1:1 R), run the rest.

    The professional exit: bank ~half at the first target, move risk off, and let
    the remainder run on the trailing stop (the exit scan's 2-bar ratchet). Built
    as TWO independent OCA sub-brackets so a partial fill does NOT strand the runner:

        parent  : <action> quantity @ entry            (limit, transmit=False)
        partial : <exit> q1 @ target1  +  <exit> q1 STP stop   (OCA group ...-P)
        runner  : [<exit> q2 @ target2]  +  <exit> q2 STP stop (OCA group ...-R)

    - When target1 fills, only the PARTIAL's stop cancels (its own OCA); the runner
      keeps its own stop, which the exit scan then ratchets up. The two halves never
      interfere.
    - `target1=None` defaults to **1:1 reward:risk** (`entry +/- |entry-stop|`) — the
      standard "first scale at 1R".
    - `target2=None` (default) gives the runner **no fixed target** — the trailing
      2-bar stop runs it (house style). Pass a price to cap the runner instead.
    - Sizing: pass the full `quantity` from `config.position_size`; it's split here.
      Needs quantity >= 2.

    All legs are transmit=False. Returns (contract, [parent, ...legs]); release with
    `transmit()` (sets the last leg transmit=True) after the user approves.
    """
    action = action.upper()
    exit_action = "SELL" if action == "BUY" else "BUY"
    sign = 1 if action == "BUY" else -1
    risk = abs(entry - stop)
    if target1 is None:
        target1 = round(entry + sign * risk, 2)          # 1:1 R

    q1, q2 = scaled_split(quantity, partial_frac)
    contract = qualify(ib, symbol)
    rid = ib.client.getReqId
    parent = LimitOrder(action, quantity, entry, orderId=rid(), transmit=False)
    base = f"{symbol}_SCALE_{parent.orderId}"
    legs = [parent]

    # Partial sub-bracket (OCO: target1 vs stop), size q1
    ocp = f"{base}_P"
    legs.append(LimitOrder(exit_action, q1, target1, orderId=rid(),
                           parentId=parent.orderId, ocaGroup=ocp, ocaType=1,
                           transmit=False))
    legs.append(StopOrder(exit_action, q1, stop, orderId=rid(),
                          parentId=parent.orderId, ocaGroup=ocp, ocaType=1,
                          transmit=False))

    # Runner sub-bracket, size q2. With a fixed target -> its own OCO; without ->
    # a lone protective stop (the exit scan trails it). Only OCA-link when paired.
    if target2 is not None:
        ocr = f"{base}_R"
        legs.append(LimitOrder(exit_action, q2, target2, orderId=rid(),
                               parentId=parent.orderId, ocaGroup=ocr, ocaType=1,
                               transmit=False))
        legs.append(StopOrder(exit_action, q2, stop, orderId=rid(),
                              parentId=parent.orderId, ocaGroup=ocr, ocaType=1,
                              transmit=False))
    else:
        legs.append(StopOrder(exit_action, q2, stop, orderId=rid(),
                              parentId=parent.orderId, transmit=False))
    return contract, legs


def transmit(ib, contract, bracket):
    """Send a previously-prepared bracket AFTER the user approves it.

    Placing the last child with transmit=True releases the whole group. Works for
    prepare_entry / prepare_bracket / prepare_scaled_bracket (the parent is first
    and the final leg's transmit=True releases every order in the group).
    """
    bracket[-1].transmit = True
    return [ib.placeOrder(contract, o) for o in bracket]
