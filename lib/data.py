"""Market data + screening via ib_async.

Note: scanner and real-time quotes may require a market-data subscription.
On a free paper account with delayed data, prefer historical bars (these work
with delayed data) and set delayed mode before requesting snapshots.
"""
from ib_async import ScannerSubscription, Stock, util


def set_delayed(ib):
    """Use free delayed data (type 3) instead of live."""
    ib.reqMarketDataType(3)


def historical_bars(ib, symbol, duration="60 D", bar_size="1 day",
                    what="TRADES", rth=True):
    """Return a DataFrame of OHLCV bars (columns: date, open, high, low, close, volume)."""
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr=duration,
        barSizeSetting=bar_size, whatToShow=what, useRTH=rth, formatDate=1,
    )
    return util.df(bars)


def intraday_bars(ib, symbol, duration="1 D", bar_size="5 mins"):
    return historical_bars(ib, symbol, duration, bar_size)


def scan(ib, scan_code="TOP_PERC_GAIN", n=20,
         location="STK.US.MAJOR", instrument="STK"):
    """Run an IBKR market scanner and return a list of symbols.

    Useful scan_code values: TOP_PERC_GAIN, TOP_PERC_LOSE, MOST_ACTIVE,
    HOT_BY_VOLUME, TOP_OPEN_PERC_GAIN.
    """
    sub = ScannerSubscription(
        instrument=instrument, locationCode=location, scanCode=scan_code,
    )
    rows = ib.reqScannerData(sub)
    return [r.contractDetails.contract.symbol for r in rows[:n]]
