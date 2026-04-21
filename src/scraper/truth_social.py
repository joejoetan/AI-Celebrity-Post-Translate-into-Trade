"""Scrape Truth Social via the public Mastodon-compatible API.

Endpoint shape (public, no auth):
  https://truthsocial.com/api/v1/accounts/{account_id}/statuses

Returns a JSON array of status objects. Trump's account id is stable.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from html import unescape
from typing import Any

import httpx
from dateutil import parser as dateparser

from src.config import HTTP_TIMEOUT_S
from src.models import Post
from src.scraper.base import Scraper

log = logging.getLogger(__name__)

_API = "https://truthsocial.com/api/v1/accounts/{id}/statuses"
_TAG_RE = re.compile(r"<[^>]+>")

# Truth Social rejects non-browser UAs with 403. Mimic a recent Chrome.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class TruthSocialScraper(Scraper):
    platform = "truth_social"

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id

    def fetch(self, handle: str, since: datetime) -> list[Post]:
        url = _API.format(id=self.account_id)
        try:
            r = httpx.get(
                url,
                params={"exclude_replies": "true", "limit": 40},
                timeout=HTTP_TIMEOUT_S,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://truthsocial.com/",
                    "Origin": "https://truthsocial.com",
                },
                follow_redirects=True,
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Truth Social fetch failed for %s: %s", handle, e)
            return []

        posts: list[Post] = []
        for status in data if isinstance(data, list) else []:
            post = _status_to_post(status, handle)
            if post is None or post.created_at < since:
                continue
            posts.append(post)
        return posts


def _status_to_post(status: dict[str, Any], handle: str) -> Post | None:
    try:
        created = dateparser.isoparse(status["created_at"])
        content_html = status.get("content") or ""
        text = unescape(_TAG_RE.sub("", content_html)).strip()
        # Truth reshares ("reblogs") have the real content under `reblog`.
        if not text and status.get("reblog"):
            rb = status["reblog"]
            text = unescape(_TAG_RE.sub("", rb.get("content", ""))).strip()
            text = f"(reshared) {text}"
        return Post(
            id=str(status["id"]),
            platform="truth_social",
            author_handle=handle,
            author_display=status.get("account", {}).get("display_name") or handle,
            text=text,
            url=status.get("url") or "",
            created_at=created,
        )
    except (KeyError, ValueError) as e:
        log.debug("Malformed Truth status: %s", e)
        return None
