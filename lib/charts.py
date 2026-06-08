"""Annotated candlestick charts via mplfinance.

Renders price candles with SMA overlays, an RSI panel, optional entry/stop/
target lines, and saves a PNG to the dated output folder.
"""
import os

import mplfinance as mpf
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prep(df):
    """mplfinance wants a DatetimeIndex and capitalized OHLCV columns."""
    d = df.copy()
    if "date" in d.columns:
        d = d.set_index("date")
    d.index = pd.to_datetime(d.index)
    d = d.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    return d


def chart_dir(date_str):
    path = os.path.join(_ROOT, "output", "charts", date_str)
    os.makedirs(path, exist_ok=True)
    return path


def save_chart(df, symbol, date_str, title="", entry=None, stop=None, target=None):
    """Save an annotated chart and return its file path."""
    d = _prep(df)
    out = os.path.join(chart_dir(date_str), f"{symbol}.png")

    addplots = []
    # ema8 = momentum/entry, sma200 = macro trend, vwap = intraday reference
    for col, color in (("ema8", "#2ca02c"), ("sma20", "#1f77b4"),
                       ("sma50", "#ff7f0e"), ("sma200", "#d62728"),
                       ("vwap", "#9467bd")):
        if col in df.columns and df[col].notna().any():
            addplots.append(mpf.make_addplot(df[col].values, color=color, width=0.8))
    if "rsi" in df.columns and df["rsi"].notna().any():
        addplots.append(mpf.make_addplot(df["rsi"].values, panel=1, color="purple", ylabel="RSI"))

    hlines = [v for v in (entry, stop, target) if v is not None]
    hl = None
    if hlines:
        colors = []
        if entry is not None: colors.append("green")
        if stop is not None: colors.append("red")
        if target is not None: colors.append("blue")
        hl = dict(hlines=hlines, colors=colors, linestyle="--", linewidths=0.8)

    mpf.plot(
        d, type="candle", style="charles", addplot=addplots or None,
        volume=True, title=title or symbol, hlines=hl, savefig=out,
    )
    return out
