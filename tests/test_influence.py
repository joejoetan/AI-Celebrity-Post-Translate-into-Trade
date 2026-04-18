from datetime import datetime, timezone
from unittest.mock import patch

from src import influence
from src.models import MarketSnapshot, TradeStrategy
from src.state import State


def _strategy(ticker="TSLA", spot=100.0, author="elonmusk"):
    snap = MarketSnapshot(ticker=ticker, spot=spot)
    return TradeStrategy(
        ticker=ticker, side="long", conviction=0.7,
        entry_zone="99-101", stop="95", targets=["105"],
        size_pct="2%", time_in_force="Day",
        exit_rules=["x"], execution_steps=["y"], invalidation="z",
        source_post_id="p1", source_author=author,
        source_url="https://x.com/x/status/1", source_excerpt="t",
        market_snapshot=snap,
    )


def test_queue_hits_bumps_posts():
    st = State()
    influence.queue_hits(st, [_strategy()])
    assert len(st.pending_hits) == 1
    assert st.influence_scores["elonmusk"]["posts"] == 1
    assert st.influence_scores["elonmusk"]["hits"] == 0


def test_resolve_pending_hits_counts_moves():
    st = State()
    influence.queue_hits(st, [_strategy(spot=100.0)])
    # Force the check window to be due already.
    st.pending_hits[0]["check_after_ts"] = 0

    def fake_snapshot(ticker):
        return MarketSnapshot(ticker=ticker, spot=103.0)  # +3% → hit for large-cap

    with patch.object(influence, "snapshot", side_effect=fake_snapshot):
        influence.resolve_pending_hits(st)

    assert st.influence_scores["elonmusk"]["hits"] == 1
    assert st.pending_hits == []


def test_resolve_pending_hits_skips_small_moves():
    st = State()
    influence.queue_hits(st, [_strategy(spot=100.0)])
    st.pending_hits[0]["check_after_ts"] = 0

    def fake_snapshot(ticker):
        return MarketSnapshot(ticker=ticker, spot=100.5)  # 0.5% → miss

    with patch.object(influence, "snapshot", side_effect=fake_snapshot):
        influence.resolve_pending_hits(st)

    assert st.influence_scores["elonmusk"]["hits"] == 0


def test_promote_candidates_gate():
    st = State()
    st.influence_scores["newaccount"] = {
        "score": 10, "posts": 5, "hits": 3, "last_updated": None,
    }
    ready = influence.promote_candidates(st, threshold=5, auto=False)
    assert ready == ["newaccount"]
    # auto=False leaves it un-promoted so it keeps showing up in digests.
    assert st.influence_scores["newaccount"].get("promoted") is not True

    ready2 = influence.promote_candidates(st, threshold=5, auto=True)
    assert ready2 == ["newaccount"]
    assert st.influence_scores["newaccount"]["promoted"] is True
