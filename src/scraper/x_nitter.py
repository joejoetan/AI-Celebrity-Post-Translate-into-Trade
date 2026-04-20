"""X/Twitter scraper with layered fallbacks.

Priority (highest first):
  1. Per-target RSS override   (env var RSS_URL_<HANDLE>, e.g. rss.app)
  2. X syndication endpoint    (cdn.syndication.twimg.com — unauthenticated JSON)
  3. Nitter RSS                (randomised across known instances)
  4. snscrape                  (optional import; falls back cleanly if missing)

Each target succeeds on whichever path returns posts first.
"""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import feedparser
import httpx

from src.config import (
    HTTP_TIMEOUT_S,
    NITTER_INSTANCES,
    USER_AGENT,
    rss_override,
)
from src.models import Post
from src.scraper.base import Scraper
from src.scraper.x_syndication import fetch_syndication

log = logging.getLogger(__name__)

_STATUS_RE = re.compile(r"/status/(\d+)")


class XScraper(Scraper):
    platform = "x"

    def __init__(self, instances: list[str] | None = None) -> None:
        self.instances = list(instances or NITTER_INSTANCES)
        # Populated by .fetch(); lets diagnose scripts print which path won.
        self.last_path: str = ""

    def fetch(self, handle: str, since: datetime) -> list[Post]:
        # 1. Per-target RSS override (most reliable if user sets one).
        override_url = rss_override(handle)
        if override_url:
            posts = self._fetch_generic_rss(override_url, handle, since)
            if posts:
                self.last_path = f"rss_override:{override_url}"
                return posts
            log.info("RSS override for @%s returned nothing", handle)

        # 2. X syndication endpoint.
        posts = fetch_syndication(handle, since)
        if posts:
            self.last_path = "syndication"
            return posts

        # 3. Nitter.
        posts = self._fetch_nitter(handle, since)
        if posts:
            return posts

        # 4. snscrape.
        log.info("Nitter exhausted for @%s; trying snscrape", handle)
        posts = self._fetch_snscrape(handle, since)
        if posts:
            self.last_path = "snscrape"
            return posts

        self.last_path = "none"
        return []

    # --- RSS override ---

    def _fetch_generic_rss(self, url: str, handle: str, since: datetime) -> list[Post]:
        try:
            r = httpx.get(
                url,
                timeout=HTTP_TIMEOUT_S,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            if r.status_code != 200 or not r.text:
                return []
            feed = feedparser.parse(r.text)
            return list(_parse_generic_rss_entries(handle, feed.entries, since))
        except httpx.HTTPError as e:
            log.warning("RSS override failed for @%s: %s", handle, e)
            return []

    # --- Nitter ---

    def _fetch_nitter(self, handle: str, since: datetime) -> list[Post]:
        pool = self.instances.copy()
        random.shuffle(pool)
        last_status: dict[str, int] = {}
        for base in pool:
            url = f"{base.rstrip('/')}/{handle}/rss"
            try:
                r = httpx.get(
                    url,
                    timeout=HTTP_TIMEOUT_S,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                last_status[base] = r.status_code
                if r.status_code != 200 or not r.text:
                    continue
                feed = feedparser.parse(r.text)
                if not feed.entries:
                    continue
                posts = list(_parse_nitter_entries(handle, feed.entries, since))
                if posts:
                    self.last_path = f"nitter:{base}"
                    return posts
            except (httpx.HTTPError, ValueError) as e:
                log.debug("Nitter %s failed for @%s: %s", base, handle, e)
                last_status[base] = -1
                continue
        if last_status:
            log.debug("Nitter attempts for @%s: %s", handle, last_status)
        return []

    # --- snscrape ---

    def _fetch_snscrape(self, handle: str, since: datetime) -> list[Post]:
        try:
            from snscrape.modules.twitter import TwitterUserScraper
        except ImportError:
            log.warning("snscrape not installed; no fallback")
            return []

        posts: list[Post] = []
        try:
            for tweet in TwitterUserScraper(handle).get_items():
                created = tweet.date
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < since:
                    break
                posts.append(
                    Post(
                        id=str(tweet.id),
                        platform="x",
                        author_handle=handle,
                        author_display=getattr(tweet.user, "displayname", handle),
                        text=tweet.rawContent or "",
                        url=tweet.url,
                        created_at=created,
                    )
                )
                if len(posts) >= 50:
                    break
        except Exception as e:  # noqa: BLE001 - snscrape raises many shapes
            log.warning("snscrape failed for @%s: %s", handle, e)
        return posts


def _parse_nitter_entries(handle: str, entries: Iterable, since: datetime):
    """Strict: require a /status/<id> URL to treat the entry as a tweet."""
    for e in entries:
        link = e.get("link", "")
        m = _STATUS_RE.search(link)
        if not m:
            continue
        tweet_id = m.group(1)
        created = _parse_rss_date(e.get("published") or e.get("updated") or "")
        if created is None or created < since:
            continue
        yield Post(
            id=tweet_id,
            platform="x",
            author_handle=handle,
            author_display=handle,
            text=e.get("title") or "",
            url=f"https://x.com/{handle}/status/{tweet_id}",
            created_at=created,
        )


def _parse_generic_rss_entries(handle: str, entries: Iterable, since: datetime):
    """Lenient: accept any entry with a link + publish date. Used for user-
    provided RSS overrides (rss.app, FetchRSS, etc.) where the link format
    isn't necessarily /status/."""
    for e in entries:
        link = e.get("link", "")
        if not link:
            continue
        m = _STATUS_RE.search(link)
        tweet_id = m.group(1) if m else (e.get("id") or e.get("guid") or link)
        if not tweet_id:
            continue
        created = _parse_rss_date(e.get("published") or e.get("updated") or "")
        if created is None or created < since:
            continue
        yield Post(
            id=str(tweet_id),
            platform="x",
            author_handle=handle,
            author_display=handle,
            text=e.get("title") or "",
            url=f"https://x.com/{handle}/status/{tweet_id}" if m else link,
            created_at=created,
        )


def _parse_rss_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
