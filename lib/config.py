"""Load user configuration and compute risk-checked position sizes.

All position sizing flows through `position_size()` so the risk % and the
max-position cap are enforced in exactly one place.
"""
import math
import os

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_risk(path=None):
    path = path or os.path.join(_ROOT, "config", "risk.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_strategy(path=None):
    path = path or os.path.join(_ROOT, "config", "strategy.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_watchlist(path=None):
    path = path or os.path.join(_ROOT, "config", "watchlist.txt")
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line.upper())
    return out


def add_to_watchlist(symbols, path=None):
    """Append any symbols not already in the watchlist file. Returns those added.

    Used to auto-add currently-held tickers so research/exit routines cover them.
    """
    path = path or os.path.join(_ROOT, "config", "watchlist.txt")
    existing = set(load_watchlist(path))
    to_add = [s.upper() for s in symbols if s.upper() not in existing]
    if to_add:
        # Ensure the file ends with a newline before appending, so a missing
        # trailing newline can't concatenate the last line with the first add.
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        prefix = "" if (content == "" or content.endswith("\n")) else "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(prefix + "\n".join(to_add) + "\n")
    return to_add


def catastrophic_stop(entry, side="long", risk=None):
    """Hard max-loss stop price — the floor that is ALWAYS honored, even under
    no_exit_at_loss. Distance from entry = limits.catastrophic_stop_pct."""
    risk = risk or load_risk()
    pct = float(risk["limits"]["catastrophic_stop_pct"])
    entry = float(entry)
    return entry * (1 - pct / 100.0) if side == "long" else entry * (1 + pct / 100.0)


def position_size(entry, stop, style="swing", risk=None):
    """Return integer share count for a trade, risk-checked and capped.

    shares = min(risk_budget / |entry-stop|, capital_cap / entry), floored.

    All limits are PERCENTAGES of `account.size_usd` (no hardcoded $ amounts):
      - risk_budget = account × `<style>.risk_per_trade_pct`%  (max loss if stop hits)
      - capital_cap = account × `<style>.max_position_pct`% if set, else the global
        `limits.max_position_pct`%.
    So a tight stop hits the capital cap (deploys near max_position_pct); a wide
    stop trims the position to hold the risk cap. Day trades carry their own
    higher caps (risk 1.3% / position 43%); swings use 1% risk + the global 20% cap.

    IMPORTANT: pass the stop that will actually be HONORED. With no_exit_at_loss
    on, the only enforced downside stop is the catastrophic floor, so size off
    `catastrophic_stop(entry, ...)` — otherwise risk-per-trade is understated.
    """
    risk = risk or load_risk()
    account = float(risk["account"]["size_usd"])
    style_key = "day_trading" if style.startswith("day") else "swing_trading"
    cfg = risk.get(style_key, {})

    per_share_risk = abs(float(entry) - float(stop))
    if per_share_risk <= 0:
        raise ValueError("entry and stop must differ")

    risk_budget = account * float(cfg.get("risk_per_trade_pct", 0.5)) / 100.0
    cap_pct = float(cfg.get("max_position_pct", risk["limits"]["max_position_pct"]))
    cap_value = account * cap_pct / 100.0

    raw = risk_budget / per_share_risk
    cap_shares = cap_value / float(entry)
    return max(0, math.floor(min(raw, cap_shares)))
