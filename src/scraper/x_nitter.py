"""Scrape X/Twitter posts via Nitter RSS with snscrape fallback.

Nitter instances are flaky — we randomize order, apply a short timeout, and
fall back to `snscrape` (no login, public timelines only).
"""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from src.config import HTTP_TIMEOUT_S, NITTER_INSTANCES, USER_AGENT
from src.models import Post
from src.scraper.base import Scraper

log = logging.getLogger(__name__)


# Matches /{user}/status/{id} — extracts tweet id from a Nitter item link.
_STATUS_RE = re.compile(r"/status/(\d+)")


class XScraper(Scraper):
    platform = "x"

    def __init__(self, instances: list[str] | None = None) -> None:
        self.instances = list(instances or NITTER_INSTANCES)

    def fetch(self, handle: str, since: datetime) -> list[Post]:
        posts = self._fetch_nitter(handle, since)
        if posts:
            return posts
        log.info("Nitter exhausted for @%s; falling back to snscrape", handle)
        return self._fetch_snscrape(handle, since)

    def _fetch_nitter(self, handle: str, since: datetime) -> list[Post]:
        pool = self.instances.copy()
        random.shuffle(pool)
        for base in pool:
            url = f"{base.rstrip('/')}/{handle}/rss"
            try:
                r = httpx.get(
                    url,
                    timeout=HTTP_TIMEOUT_S,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                if r.status_code != 200 or not r.text:
                    continue
                feed = feedparser.parse(r.text)
                if not feed.entries:
                    continue
                return list(_parse_nitter_entries(handle, feed.entries, since))
            except (httpx.HTTPError, ValueError) as e:
                log.debug("Nitter %s failed for @%s: %s", base, handle, e)
                continue
        return []

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


def _parse_nitter_entries(handle: str, entries, since: datetime):
    for e in entries:
        link = e.get("link", "")
        m = _STATUS_RE.search(link)
        if not m:
            continue
        tweet_id = m.group(1)
        created = _parse_rss_date(e.get("published") or e.get("updated") or "")
        if created is None or created < since:
            continue
        # Nitter title contains the tweet text; description has HTML.
        text = e.get("title") or ""
        # Normalize link to canonical x.com URL so dedup keys don't depend on
        # which Nitter instance served the feed.
        url = f"https://x.com/{handle}/status/{tweet_id}"
        yield Post(
            id=tweet_id,
            platform="x",
            author_handle=handle,
            author_display=handle,
            text=text,
            url=url,
            created_at=created,
        )


def _parse_rss_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
