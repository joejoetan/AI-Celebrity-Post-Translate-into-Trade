from datetime import datetime, timezone

from src.scraper.x_nitter import _parse_nitter_entries, _parse_rss_date
from src.scraper.truth_social import _status_to_post


def test_parse_rss_date_rfc822():
    d = _parse_rss_date("Wed, 02 Oct 2024 15:30:00 GMT")
    assert d is not None
    assert d.tzinfo is not None


def test_parse_rss_date_garbage():
    assert _parse_rss_date("") is None
    assert _parse_rss_date("not a date") is None


def test_parse_nitter_entries_filters_since():
    entries = [
        {
            "link": "https://nitter.net/elonmusk/status/999#m",
            "published": "Wed, 02 Oct 2024 15:30:00 GMT",
            "title": "hello world",
        },
        {
            "link": "https://nitter.net/elonmusk/status/888",
            "published": "Wed, 02 Oct 2024 08:00:00 GMT",
            "title": "older",
        },
        {
            "link": "https://nitter.net/elonmusk/not-a-status",
            "published": "Wed, 02 Oct 2024 15:00:00 GMT",
            "title": "skip me",
        },
    ]
    since = datetime(2024, 10, 2, 12, 0, tzinfo=timezone.utc)
    posts = list(_parse_nitter_entries("elonmusk", entries, since))
    assert [p.id for p in posts] == ["999"]
    assert posts[0].url == "https://x.com/elonmusk/status/999"
    assert posts[0].platform == "x"


def test_truth_social_status_to_post():
    status = {
        "id": "112345",
        "created_at": "2024-10-02T15:30:00.000Z",
        "content": "<p>Strong jobs report today!</p>",
        "url": "https://truthsocial.com/@realDonaldTrump/posts/112345",
        "account": {"display_name": "Donald J. Trump"},
    }
    post = _status_to_post(status, "realDonaldTrump")
    assert post is not None
    assert post.id == "112345"
    assert post.text == "Strong jobs report today!"
    assert post.platform == "truth_social"


def test_truth_social_reshare():
    status = {
        "id": "1",
        "created_at": "2024-10-02T15:30:00.000Z",
        "content": "",
        "url": "",
        "reblog": {"content": "<p>Inner post</p>"},
        "account": {},
    }
    post = _status_to_post(status, "realDonaldTrump")
    assert post is not None
    assert "Inner post" in post.text
    assert post.text.startswith("(reshared)")
