"""Tests for the financial-analyst-mode upgrades.

Covers:
 - New TradeStrategy fields (opportunity_summary, supporting_facts,
   action_plan, data_limited) round-trip cleanly.
 - Telegram formatter renders each new section with citations.
 - data_limited=true surfaces the DATA-LIMITED warning flag.
 - MarketSnapshot.headlines compat shim still works for old callers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src import analyst, notifier, strategist
from src.models import MarketSnapshot, NewsItem, Post, SupportingFact, TradeStrategy


def _strategy(data_limited: bool = False) -> TradeStrategy:
    snap = MarketSnapshot(
        ticker="TSLA", spot=250.12, prev_close=248.00,
        day_pct=0.85, five_day_pct=2.1,
        day_volume=95_000_000, avg_volume_10d=80_000_000,
        next_earnings="2026-04-23",
        news=[
            NewsItem(title="Tesla beats Q1 deliveries", source="yfinance",
                     url="https://finance.yahoo.com/x"),
            NewsItem(title="Musk teases robotaxi", source="google_news",
                     url="https://news.google.com/x"),
        ],
    )
    return TradeStrategy(
        ticker="TSLA", side="long", conviction=0.72,
        opportunity_summary=("Musk's production claim + rising deliveries "
                             "suggest a 1–3 day momentum long into earnings."),
        supporting_facts=[
            SupportingFact(fact="Cybertruck production up 40% QoQ",
                           source="post"),
            SupportingFact(fact="Spot 250.12, +0.85% today", source="quote: spot"),
            SupportingFact(fact="Day volume 1.2x 10d avg — supportive",
                           source="quote: volume"),
            SupportingFact(fact="Earnings 2026-04-23 (5 days out)",
                           source="quote: earnings"),
            SupportingFact(fact="Tesla beats Q1 deliveries",
                           source="news: yfinance"),
        ],
        risks=["Musk has historically missed guidance by ~2 quarters",
               "Pre-earnings IV crush could cap short-dated option plays"],
        entry_zone="249.00-251.00", stop="243.90 (-2.5%)",
        targets=["258 (+3.5%)", "265 (+6.3%)"],
        size_pct="2% of book", time_in_force="Day + 2",
        exit_rules=["Trail stop to entry at T1",
                    "Flat immediately if Musk deletes the post"],
        action_plan=[
            "T+0–5min: Limit buy 1% @ 249.50",
            "T+15min: If >251 with >1.5x vol, add 1% @ 251.20",
            "EOD: Move stop to break-even if T1 hit",
            "Pre-earnings (2026-04-22 close): trim to 0.5% unless conviction rises",
        ],
        invalidation="Break 245 on >2x avg volume OR policy reversal",
        source_post_id="p1", source_author="elonmusk",
        source_url="https://x.com/elonmusk/status/1",
        source_excerpt="Cybertruck production ramping 40% QoQ",
        market_snapshot=snap, data_limited=data_limited,
    )


def test_new_fields_round_trip():
    s = _strategy()
    assert s.opportunity_summary.startswith("Musk")
    assert len(s.supporting_facts) == 5
    assert s.supporting_facts[0].source == "post"
    assert len(s.action_plan) == 4
    assert "T+0–5min" in s.action_plan[0]
    assert s.data_limited is False


def test_formatter_renders_new_sections():
    out = notifier.format_strategy(_strategy())
    # Opportunity summary
    assert "Opportunity" in out
    assert "Musk" in out
    # Supporting facts with citations
    assert "Supporting facts" in out
    assert "[post]" in out
    assert "[quote: spot]" in out or "\\[quote: spot\\]" in out  # escaped
    # Risks
    assert "Risks" in out
    assert "missed guidance" in out
    # Action plan, time-sequenced
    assert "Action plan" in out
    assert "T+0" in out
    # Volume ratio rendered
    assert "x avg vol" in out
    # Earnings date surfaced
    assert "2026-04-23" in out


def test_data_limited_flag_renders():
    out = notifier.format_strategy(_strategy(data_limited=True))
    assert "DATA-LIMITED" in out


def test_market_snapshot_headlines_compat():
    snap = MarketSnapshot(
        ticker="TSLA",
        news=[NewsItem(title="A", source="x"), NewsItem(title="B", source="y")],
    )
    assert snap.headlines == ["A", "B"]


def test_strategist_hydrates_supporting_facts():
    """The scripted Anthropic client returns raw dicts; the strategist must
    convert supporting_facts dicts into SupportingFact models."""
    post = Post(
        id="p1", platform="x", author_handle="elonmusk", author_display="Elon",
        text="test", url="https://x.com/elonmusk/status/1",
        created_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
    )
    raw_strategy = {
        "ticker": "TSLA", "side": "long", "conviction": 0.7,
        "opportunity_summary": "test opp",
        "supporting_facts": [
            {"fact": "Spot 250", "source": "quote: spot"},
            {"fact": "Post claim", "source": "post"},
        ],
        "risks": ["r1"],
        "entry_zone": "249-251", "stop": "244", "targets": ["258"],
        "size_pct": "2%", "time_in_force": "Day",
        "exit_rules": ["e1"], "action_plan": ["T+0: buy"],
        "invalidation": "break 245",
        "source_post_id": "p1", "data_limited": False,
    }
    fake_resp = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="submit_strategies",
                                  input={"strategies": [raw_strategy]})],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0),
    )

    class FakeClient:
        def __init__(self):
            self.messages = self

        def create(self, **_):
            return fake_resp

    from src.models import TradeInsight
    insight = TradeInsight(
        post_id="p1", author="@elonmusk", tickers=["TSLA"],
        direction="long", conviction=0.7, timeframe="swing",
        rationale="test", risks="test",
    )
    strategies = strategist.strategize(
        FakeClient(), [insight], {"p1": post},
        {"TSLA": MarketSnapshot(ticker="TSLA", spot=250.0)},
    )
    assert len(strategies) == 1
    s = strategies[0]
    assert len(s.supporting_facts) == 2
    assert s.supporting_facts[0].source == "quote: spot"
    assert s.action_plan == ["T+0: buy"]
    assert s.data_limited is False
