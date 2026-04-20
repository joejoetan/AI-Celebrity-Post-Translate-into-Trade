"""X/Twitter syndication endpoint scraper.

X exposes a public JSON endpoint that powers embedded timeline widgets
on third-party sites:

    https://cdn.syndication.twimg.com/timeline/profile
        ?screen_name=<handle>
        &suppress_response_codes=true

It returns the last ~20 public tweets with no auth required. Works where
Nitter is blocked because X still needs it for their own embed widgets.
Schema can drift — we parse defensively.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from dateutil import parser as dateparser

from src.config import HTTP_TIMEOUT_S, USER_AGENT
from src.models import Post

log = logging.getLogger(__name__)

_ENDPOINT = "https://cdn.syndication.twimg.com/timeline/profile"
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def fetch_syndication(handle: str, since: datetime) -> list[Post]:
    """Return posts authored by `handle` newer than `since`. Empty on failure."""
    try:
        r = httpx.get(
            _ENDPOINT,
            params={
                "screen_name": handle,
                "suppress_response_codes": "true",
            },
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
                "Referer": "https://platform.twitter.com/",
            },
            timeout=HTTP_TIMEOUT_S,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        log.debug("Syndication fetch failed for @%s: %s", handle, e)
        return []

    if r.status_code != 200 or not r.text:
        log.debug("Syndication returned %s for @%s", r.status_code, handle)
        return []

    try:
        data = r.json()
    except ValueError:
        log.debug("Syndication returned non-JSON for @%s", handle)
        return []

    tweets = _extract_tweets(data)
    posts: list[Post] = []
    for t in tweets:
        post = _tweet_to_post(t, handle)
        if post is None or post.created_at < since:
            continue
        posts.append(post)
    return posts


def _extract_tweets(data: Any) -> list[dict]:
    """Walk the syndication response to find the tweet list.

    The shape has varied over the years. Try a few known paths.
    """
    if not isinstance(data, dict):
        return []

    # Classic shape: body is a list of tweet dicts.
    body = data.get("body")
    if isinstance(body, list):
        return body

    # Newer nested shape.
    entries = (
        data.get("timeline", {})
            .get("instructions", [{}])[0]
            .get("addEntries", {})
            .get("entries", [])
        if isinstance(data.get("timeline"), dict) else []
    )
    tweets = []
    for e in entries:
        tweet = (
            e.get("content", {})
             .get("item", {})
             .get("content", {})
             .get("tweet", {})
        )
        if tweet:
            tweets.append(tweet)
    if tweets:
        return tweets

    # Another observed shape: { "tweets": [...] }
    if isinstance(data.get("tweets"), list):
        return data["tweets"]

    return []


def _tweet_to_post(t: dict, handle: str) -> Post | None:
    try:
        tid = t.get("id_str") or t.get("id") or ""
        text = t.get("full_text") or t.get("text") or ""
        created = t.get("created_at", "")
        if not tid or not created:
            return None
        dt = dateparser.parse(created)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        user = t.get("user", {}) or {}
        return Post(
            id=str(tid),
            platform="x",
            author_handle=handle,
            author_display=user.get("name") or handle,
            text=_clean(text),
            url=f"https://x.com/{handle}/status/{tid}",
            created_at=dt,
        )
    except (KeyError, ValueError) as e:
        log.debug("Bad syndication tweet for @%s: %s", handle, e)
        return None


_URL_RE = re.compile(r"https?://t\.co/\S+")


def _clean(s: str) -> str:
    # Remove trailing t.co URLs that are pure shorteners for the tweet itself.
    return _URL_RE.sub("", s).strip()
