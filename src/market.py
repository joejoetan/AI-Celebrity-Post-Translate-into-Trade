"""Market data + news for cross-checking trade insights.

Primary: yfinance (no key required).
Fallback: Yahoo Finance headline RSS.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import feedparser
import httpx

from src.config import HTTP_TIMEOUT_S, USER_AGENT
from src.models import MarketSnapshot

log = logging.getLogger(__name__)

_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"


@lru_cache(maxsize=256)
def snapshot(ticker: str) -> MarketSnapshot:
    """Return current price + recent news for `ticker`. Cached per run.

    Normalizes ticker casing. For crypto, yfinance wants e.g. "BTC-USD".
    """
    sym = ticker.upper().strip()
    snap = MarketSnapshot(ticker=sym)
    _fill_quote(sym, snap)
    _fill_headlines(sym, snap)
    return snap


def _fill_quote(sym: str, snap: MarketSnapshot) -> None:
    try:
        import yfinance as yf

        t = yf.Ticker(sym)
        # fast_info is lightweight vs .info
        fi = t.fast_info
        spot = _maybe_float(getattr(fi, "last_price", None))
        prev = _maybe_float(getattr(fi, "previous_close", None))
        snap.spot = spot
        snap.prev_close = prev
        if spot is not None and prev:
            snap.day_pct = round((spot - prev) / prev * 100, 2)

        # 5-day % from history (best-effort, slower)
        hist = t.history(period="6d", interval="1d")
        if hist is not None and len(hist) >= 2:
            close_series = hist["Close"].dropna()
            if len(close_series) >= 2:
                first = float(close_series.iloc[0])
                last = float(close_series.iloc[-1])
                if first:
                    snap.five_day_pct = round((last - first) / first * 100, 2)
    except Exception as e:  # noqa: BLE001 - yfinance surfaces many shapes
        snap.note = f"quote unavailable: {e.__class__.__name__}"
        log.debug("yfinance failed for %s: %s", sym, e)


def _fill_headlines(sym: str, snap: MarketSnapshot) -> None:
    # Try yfinance .news first (tends to be richer), then fall back to RSS.
    try:
        import yfinance as yf

        news = yf.Ticker(sym).news or []
        titles = []
        for item in news[:5]:
            # Shape varies across yfinance versions.
            title = (
                item.get("title")
                or item.get("content", {}).get("title")
            )
            if title:
                titles.append(title)
        if titles:
            snap.headlines = titles
            return
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance news failed for %s: %s", sym, e)

    try:
        r = httpx.get(
            _RSS,
            params={"s": sym},
            timeout=HTTP_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
        )
        if r.status_code == 200:
            feed = feedparser.parse(r.text)
            snap.headlines = [e.get("title", "") for e in feed.entries[:5] if e.get("title")]
    except httpx.HTTPError as e:
        log.debug("RSS news failed for %s: %s", sym, e)


def _maybe_float(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def clear_cache() -> None:
    """Call between runs in long-lived processes so cache doesn't go stale."""
    snapshot.cache_clear()
