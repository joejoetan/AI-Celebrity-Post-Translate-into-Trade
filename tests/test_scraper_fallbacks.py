"""Tests for the layered X scraper fallback logic:
    RSS override  →  syndication  →  Nitter  →  snscrape.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.models import Post
from src.scraper.x_nitter import XScraper, _parse_generic_rss_entries


def _sample_post(id="1", handle="elonmusk"):
    return Post(
        id=id, platform="x", author_handle=handle, author_display=handle,
        text="hello", url=f"https://x.com/{handle}/status/{id}",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )


def test_rss_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("RSS_URL_ELONMUSK", "https://rss.app/feeds/x.xml")

    scraper = XScraper(instances=[])

    # Intercept the override fetcher to return one post.
    with patch.object(
        XScraper, "_fetch_generic_rss",
        return_value=[_sample_post("100")],
    ) as override_fn, patch(
        "src.scraper.x_nitter.fetch_syndication",
        return_value=[_sample_post("nope")],
    ) as syn_fn:
        posts = scraper.fetch("elonmusk", datetime.now(timezone.utc) - timedelta(hours=1))

    assert [p.id for p in posts] == ["100"]
    assert scraper.last_path.startswith("rss_override:")
    override_fn.assert_called_once()
    syn_fn.assert_not_called()  # override won


def test_syndication_used_when_no_override(monkeypatch):
    monkeypatch.delenv("RSS_URL_ELONMUSK", raising=False)
    scraper = XScraper(instances=[])
    with patch(
        "src.scraper.x_nitter.fetch_syndication",
        return_value=[_sample_post("200")],
    ):
        posts = scraper.fetch("elonmusk", datetime.now(timezone.utc) - timedelta(hours=1))
    assert [p.id for p in posts] == ["200"]
    assert scraper.last_path == "syndication"


def test_falls_through_to_nitter_when_syndication_empty(monkeypatch):
    monkeypatch.delenv("RSS_URL_ELONMUSK", raising=False)
    scraper = XScraper(instances=["https://xcancel.com"])
    with patch("src.scraper.x_nitter.fetch_syndication", return_value=[]), \
         patch.object(XScraper, "_fetch_nitter",
                      return_value=[_sample_post("300")]) as n:
        # _fetch_nitter normally sets last_path internally; we simulate:
        def fake_nitter(self, handle, since):
            scraper.last_path = "nitter:https://xcancel.com"
            return [_sample_post("300")]
        with patch.object(XScraper, "_fetch_nitter", fake_nitter):
            posts = scraper.fetch("elonmusk",
                                  datetime.now(timezone.utc) - timedelta(hours=1))
    assert [p.id for p in posts] == ["300"]
    assert scraper.last_path.startswith("nitter:")


def test_all_empty_sets_last_path_none(monkeypatch):
    monkeypatch.delenv("RSS_URL_ELONMUSK", raising=False)
    scraper = XScraper(instances=[])
    with patch("src.scraper.x_nitter.fetch_syndication", return_value=[]), \
         patch.object(XScraper, "_fetch_snscrape", return_value=[]):
        posts = scraper.fetch("elonmusk",
                              datetime.now(timezone.utc) - timedelta(hours=1))
    assert posts == []
    assert scraper.last_path == "none"


def test_generic_rss_parser_accepts_non_status_links():
    """rss.app entries often don't have /status/ in the URL — the generic
    parser still creates Post objects from them."""
    entries = [
        {
            "link": "https://rss.app/entry/abc-123",
            "published": "Thu, 18 Apr 2026 14:30:00 GMT",
            "title": "hello world",
        },
    ]
    since = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    posts = list(_parse_generic_rss_entries("elonmusk", entries, since))
    assert len(posts) == 1
    assert posts[0].text == "hello world"
    assert posts[0].url.startswith("https://rss.app/")
