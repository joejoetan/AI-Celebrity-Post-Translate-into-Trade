"""Market data + news for verifying trade insights.

Design contract: every field in the returned MarketSnapshot is either from
a real provider or empty/None. Nothing is fabricated or guessed. The LLM
downstream may ONLY cite facts found here or in the source post.

Sources:
  - yfinance:      quote (spot, prev close, volume, cap), earnings date, news
  - Yahoo RSS:     fallback headlines (`feeds.finance.yahoo.com/.../headline`)
  - Google News:   broader news (`news.google.com/rss/search?q=TICKER`)
"""
from __future__ import annotations

import logging
import urllib.parse
from functools import lru_cache

import feedparser
import httpx

from src.config import HTTP_TIMEOUT_S, USER_AGENT
from src.models import MarketSnapshot, NewsItem

log = logging.getLogger(__name__)

_YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"
_GOOGLE_NEWS = "https://news.google.com/rss/search"


@lru_cache(maxsize=256)
def snapshot(ticker: str) -> MarketSnapshot:
    """Return a verified snapshot for `ticker`. Cached per run."""
    sym = ticker.upper().strip()
    snap = MarketSnapshot(ticker=sym)
    _fill_quote(sym, snap)
    _fill_earnings(sym, snap)
    _fill_news(sym, snap)
    return snap


def _fill_quote(sym: str, snap: MarketSnapshot) -> None:
    try:
        import yfinance as yf

        t = yf.Ticker(sym)
        fi = t.fast_info
        snap.spot = _f(getattr(fi, "last_price", None))
        snap.prev_close = _f(getattr(fi, "previous_close", None))
        snap.day_volume = _i(getattr(fi, "last_volume", None))
        snap.avg_volume_10d = _i(getattr(fi, "ten_day_average_volume", None))
        snap.market_cap = _f(getattr(fi, "market_cap", None))

        if snap.spot is not None and snap.prev_close:
            snap.day_pct = round((snap.spot - snap.prev_close) / snap.prev_close * 100, 2)

        hist = t.history(period="6d", interval="1d")
        if hist is not None and len(hist) >= 2:
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                first, last = float(closes.iloc[0]), float(closes.iloc[-1])
                if first:
                    snap.five_day_pct = round((last - first) / first * 100, 2)
    except Exception as e:  # noqa: BLE001
        snap.note = f"quote unavailable ({e.__class__.__name__})"
        log.debug("yfinance quote failed for %s: %s", sym, e)


def _fill_earnings(sym: str, snap: MarketSnapshot) -> None:
    try:
        import yfinance as yf

        cal = yf.Ticker(sym).calendar
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
        else:  # older yfinance returned a DataFrame
            dates = []
        if dates:
            d = dates[0]
            snap.next_earnings = d.isoformat() if hasattr(d, "isoformat") else str(d)
    except Exception as e:  # noqa: BLE001
        log.debug("earnings fetch failed for %s: %s", sym, e)


def _fill_news(sym: str, snap: MarketSnapshot) -> None:
    items: list[NewsItem] = []

    # yfinance first (richer metadata).
    try:
        import yfinance as yf

        for item in (yf.Ticker(sym).news or [])[:5]:
            title = item.get("title") or item.get("content", {}).get("title")
            if not title:
                continue
            url = (
                item.get("link")
                or item.get("content", {}).get("canonicalUrl", {}).get("url", "")
            )
            items.append(NewsItem(title=title, source="yfinance", url=url or ""))
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance news failed for %s: %s", sym, e)

    # Yahoo Finance RSS.
    if len(items) < 3:
        items.extend(_fetch_rss(
            f"{_YAHOO_RSS}?s={sym}", source="yahoo_rss", limit=5 - len(items)
        ))

    # Google News — broader, catches non-finance outlets.
    items.extend(_fetch_rss(
        f"{_GOOGLE_NEWS}?q={urllib.parse.quote(sym + ' stock')}&hl=en-US",
        source="google_news",
        limit=3,
    ))

    # Dedup by title, keep order.
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for it in items:
        key = it.title.lower()[:100]
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    snap.news = unique[:8]


def _fetch_rss(url: str, *, source: str, limit: int) -> list[NewsItem]:
    if limit <= 0:
        return []
    try:
        r = httpx.get(url, timeout=HTTP_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return []
        feed = feedparser.parse(r.text)
        out: list[NewsItem] = []
        for e in feed.entries[:limit]:
            title = e.get("title", "").strip()
            if not title:
                continue
            out.append(NewsItem(
                title=title,
                source=source,
                url=e.get("link", ""),
                published=e.get("published", ""),
            ))
        return out
    except httpx.HTTPError as e:
        log.debug("RSS fetch failed %s: %s", url, e)
        return []


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _i(x) -> int | None:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def clear_cache() -> None:
    snapshot.cache_clear()
