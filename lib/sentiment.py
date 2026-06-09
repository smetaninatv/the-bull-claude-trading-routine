"""Social-media + headline sentiment overlay (free, no API key).

This is a CONTEXTUAL signal, never a trigger. The strategy shortlist is built
from the technical rules (VWAP / breakout / EMA-SMA); sentiment is a tiebreaker
and a crowding/risk flag layered on top. A mention spike + very bullish chatter
often means *crowded / late*, not "buy" — so we surface that as a caution.

Three free sources, all degrade gracefully (return neutral/empty on failure):
  - ApeWisdom   -> Reddit/WSB mention counts + 24h rank/mention deltas (no key)
  - StockTwits  -> user-tagged Bullish/Bearish ratio + message volume (no key)
  - yfinance .news headlines -> scored with a finance-tuned VADER lexicon

Everything is wrapped like lib.screener: a dead source must never break the
pre-market routine. Network calls are best-effort with short timeouts.
"""
import warnings

import requests

warnings.filterwarnings("ignore")

_HDR = {"User-Agent": "Mozilla/5.0 (trading-routine sentiment overlay)"}
_TIMEOUT = 12

# --- VADER + finance lexicon boost -----------------------------------------
# VADER's general lexicon misses market vocabulary ("soars" scores 0.0 raw), so
# we extend it. Scores are on VADER's roughly -4..+4 per-token scale.
_FINANCE_LEXICON = {
    # bullish
    "soars": 3.0, "soar": 3.0, "surge": 2.7, "surges": 2.7, "surged": 2.7,
    "rally": 2.2, "rallies": 2.2, "rallied": 2.2, "jumps": 2.3, "jumped": 2.3,
    "beat": 2.2, "beats": 2.2, "tops": 1.8, "upgrade": 2.5, "upgraded": 2.5,
    "outperform": 2.3, "buy": 1.5, "bullish": 2.8, "breakout": 2.0,
    "record": 1.8, "blowout": 2.8, "skyrockets": 3.2, "spikes": 1.8,
    "raises": 1.6, "raised": 1.6, "guidance": 0.4, "approval": 2.0, "approved": 2.0,
    "wins": 2.0, "won": 1.6, "gains": 1.8, "gained": 1.8, "rebound": 1.8,
    # bearish
    "plunge": -3.0, "plunges": -3.0, "plunged": -3.0, "plummet": -3.2,
    "plummets": -3.2, "crash": -3.0, "crashes": -3.0, "crashed": -3.0,
    "slump": -2.2, "slumps": -2.2, "sinks": -2.4, "sank": -2.4, "tumbles": -2.6,
    "tumbled": -2.6, "drops": -1.8, "dropped": -1.8, "falls": -1.6, "fell": -1.6,
    "miss": -2.2, "misses": -2.2, "missed": -2.2, "downgrade": -2.6,
    "downgraded": -2.6, "underperform": -2.3, "sell": -1.5, "bearish": -2.8,
    "warns": -1.8, "warning": -1.8, "cuts": -1.6, "cut": -1.4, "slashes": -2.4,
    "lawsuit": -1.8, "probe": -1.6, "investigation": -1.6, "halted": -2.2,
    "halt": -2.0, "recall": -1.8, "bankruptcy": -3.4, "fraud": -3.0,
    "dilution": -2.0, "offering": -1.2, "layoffs": -1.8, "weak": -1.4,
}

_analyzer = None


def _vader():
    """Lazy-build a VADER analyzer with the finance lexicon merged in."""
    global _analyzer
    if _analyzer is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        a = SentimentIntensityAnalyzer()
        a.lexicon.update(_FINANCE_LEXICON)
        _analyzer = a
    return _analyzer


# --- ApeWisdom (Reddit / WSB mention aggregate) ----------------------------
# Cached per-process: one call returns the whole leaderboard for all symbols.
_apewisdom_cache = {}


def apewisdom_table(filter_name="all-stocks", max_pages=2):
    """Return {TICKER: {mentions, rank, rank_24h_ago, mentions_24h_ago, upvotes}}.

    Aggregates Reddit (incl. r/wallstreetbets) ticker mentions. `filter_name`
    can be 'all-stocks', 'wallstreetbets', etc. Returns {} on any failure.
    """
    if filter_name in _apewisdom_cache:
        return _apewisdom_cache[filter_name]
    out = {}
    try:
        for page in range(1, max_pages + 1):
            url = f"https://apewisdom.io/api/v1.0/filter/{filter_name}/page/{page}"
            j = requests.get(url, headers=_HDR, timeout=_TIMEOUT).json()
            for row in j.get("results", []):
                t = (row.get("ticker") or "").upper()
                if t:
                    out[t] = row
            if page >= j.get("pages", 1):
                break
    except Exception:
        return {}
    _apewisdom_cache[filter_name] = out
    return out


# --- StockTwits (user-tagged bull/bear stream) -----------------------------
def stocktwits_sentiment(symbol):
    """Return {messages, bullish, bearish, bull_pct} from the recent stream.

    bull_pct is over *tagged* messages only (many messages carry no tag).
    Returns None if the stream is unavailable or rate-limited (HTTP 429).
    """
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        r = requests.get(url, headers=_HDR, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        msgs = r.json().get("messages", [])
        bull = bear = 0
        for m in msgs:
            s = (m.get("entities") or {}).get("sentiment") or {}
            basic = s.get("basic")
            if basic == "Bullish":
                bull += 1
            elif basic == "Bearish":
                bear += 1
        tagged = bull + bear
        return {
            "messages": len(msgs),
            "bullish": bull,
            "bearish": bear,
            "bull_pct": round(100 * bull / tagged) if tagged else None,
        }
    except Exception:
        return None


# --- Headline sentiment (yfinance news + finance VADER) --------------------
def headline_sentiment(symbol, max_items=8):
    """Mean finance-VADER compound score over recent headlines, in [-1, 1].

    Returns {count, score, headlines:[(title, score)...]} or None if no news.
    """
    try:
        import yfinance as yf
        news = yf.Ticker(symbol).news or []
    except Exception:
        return None
    a = _vader()
    scored = []
    for n in news[:max_items]:
        # yfinance has shipped a few shapes; check both flat and nested 'content'.
        title = n.get("title") or (n.get("content") or {}).get("title")
        if not title:
            continue
        scored.append((title, a.polarity_scores(title)["compound"]))
    if not scored:
        return None
    return {
        "count": len(scored),
        "score": round(sum(s for _, s in scored) / len(scored), 3),
        "headlines": scored,
    }


# --- Combined snapshot + human label ---------------------------------------
def _label(mentions, rank, rank_prev, mentions_prev, bull_pct, news_score):
    """Build a short human-readable tag + a crowding/caution flag."""
    bits, flags = [], []

    # Reddit attention
    if mentions:
        spike = (
            mentions_prev and mentions_prev > 0
            and (mentions - mentions_prev) / mentions_prev >= 0.5
        )
        climbing = rank and rank_prev and rank < rank_prev
        if rank:
            bits.append(f"WSB #{rank}")
        if spike:
            pct = round(100 * (mentions - mentions_prev) / mentions_prev)
            bits.append(f"+{pct}% mentions")
        elif climbing:
            bits.append("rising")

    # StockTwits bull/bear
    if bull_pct is not None:
        bits.append(f"{bull_pct}% bull")

    # Headlines
    if news_score is not None:
        tone = "pos" if news_score > 0.15 else "neg" if news_score < -0.15 else "mixed"
        bits.append(f"news {tone}")

    # Crowding caution: loud + euphoric is a late-stage tell, not a green light.
    # A spike only counts as "loud" off a meaningful base (>=25 mentions) — a
    # 1->3 jump is +200% but still noise, not a crowd. A top-10 WSB rank is loud
    # on its own regardless of the delta.
    hot = mentions and (
        (rank and rank <= 10)
        or (mentions >= 25 and mentions_prev
            and (mentions - mentions_prev) / max(mentions_prev, 1) >= 1.0)
    )
    if hot and (bull_pct is None or bull_pct >= 70):
        flags.append("crowded/late")
    if news_score is not None and news_score <= -0.3:
        flags.append("neg catalyst")

    if not bits:
        return "—"
    label = ", ".join(bits)
    if flags:
        # ASCII flag (not an emoji) so console output never hits cp1252 errors;
        # the reporting layer can render it as a warning glyph in markdown.
        label += "  [!] " + ", ".join(flags)
    return label


def social_snapshot(symbols, include_news=True, apewisdom_filter="all-stocks"):
    """Sentiment overlay for a list of symbols.

    Returns {SYMBOL: {mentions, rank, rank_24h_ago, bull_pct, messages,
    news_score, label}}. One ApeWisdom call covers all symbols; StockTwits and
    news are per-symbol, so keep `symbols` to the shortlist, not the full
    universe, to stay friendly to rate limits.
    """
    ape = apewisdom_table(apewisdom_filter)
    out = {}
    for sym in symbols:
        u = sym.upper()
        a = ape.get(u, {})
        st = stocktwits_sentiment(u) or {}
        news = headline_sentiment(u) if include_news else None
        ns = news["score"] if news else None
        out[sym] = {
            "mentions": a.get("mentions"),
            "rank": a.get("rank"),
            "rank_24h_ago": a.get("rank_24h_ago"),
            "mentions_24h_ago": a.get("mentions_24h_ago"),
            "bull_pct": st.get("bull_pct"),
            "messages": st.get("messages"),
            "news_score": ns,
            "label": _label(
                a.get("mentions"), a.get("rank"), a.get("rank_24h_ago"),
                a.get("mentions_24h_ago"), st.get("bull_pct"), ns,
            ),
        }
    return out
