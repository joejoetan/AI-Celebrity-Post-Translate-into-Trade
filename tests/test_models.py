from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models import MarketSnapshot, Post, TradeInsight, TradeStrategy


def test_insight_conviction_bounds():
    with pytest.raises(ValidationError):
        TradeInsight(
            post_id="1", author="x", tickers=[], direction="long",
            conviction=1.5, timeframe="intraday", rationale="r",
        )


def test_insight_direction_enum():
    with pytest.raises(ValidationError):
        TradeInsight(
            post_id="1", author="x", tickers=[], direction="up",
            conviction=0.5, timeframe="intraday", rationale="r",
        )


def test_strategy_roundtrip():
    post = Post(
        id="1", platform="x", author_handle="elonmusk",
        author_display="Elon", text="buy tesla",
        url="https://x.com/elonmusk/status/1",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    snap = MarketSnapshot(ticker="TSLA", spot=250.0, prev_close=248.0, day_pct=0.8)
    s = TradeStrategy(
        ticker="TSLA", side="long", conviction=0.7,
        entry_zone="249-251", stop="244", targets=["256", "260"],
        size_pct="2%", time_in_force="Day+1",
        exit_rules=["trail to entry at T1"],
        execution_steps=["open limit buy @250"],
        invalidation="break 245 on volume",
        source_post_id=post.id, source_author=post.author_handle,
        source_url=post.url, source_excerpt="buy tesla",
        market_snapshot=snap,
    )
    assert s.market_snapshot is not None
    assert s.market_snapshot.ticker == "TSLA"
