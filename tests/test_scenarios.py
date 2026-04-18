"""End-to-end scenarios with mocked Claude + mocked market data.

These run offline — no ANTHROPIC_API_KEY, no network — so anyone can
verify the pipeline behaves correctly on realistic inputs.

Each scenario:
  1. Builds canned Post objects.
  2. Stubs `Anthropic.messages.create` to return a deterministic tool_use
     response matching what Claude would realistically return.
  3. Stubs `market.snapshot` with canned quote/news.
  4. Drives the analyst → strategist → formatter pipeline.
  5. Asserts the output shape and key business rules (filtering,
     aggregation, formatting).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import analyst, notifier, strategist
from src.config import MIN_CONVICTION
from src.models import MarketSnapshot, Post


# --- helpers ---------------------------------------------------------------

def _post(pid: str, handle: str, text: str, platform: str = "x") -> Post:
    return Post(
        id=pid,
        platform=platform,
        author_handle=handle,
        author_display=handle,
        text=text,
        url=f"https://{'x.com' if platform == 'x' else 'truthsocial.com'}/{handle}/status/{pid}",
        created_at=datetime(2026, 4, 18, 14, 30, tzinfo=timezone.utc),
    )


def _tool_block(name: str, payload: dict):
    """Build a fake Anthropic tool_use content block."""
    return SimpleNamespace(type="tool_use", name=name, input=payload)


def _response(blocks):
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=0,
        ),
    )


class FakeAnthropic:
    """Scripted Anthropic client — returns canned tool_use responses."""

    def __init__(self, script: list):
        self._script = script
        self._call = 0
        self.messages = self

    def create(self, **_):
        resp = self._script[self._call]
        self._call += 1
        return resp


# --- scenario 1: Musk → high-conviction Tesla long -------------------------

def test_scenario_musk_tsla_bullish(monkeypatch):
    posts = [
        _post("p1", "elonmusk",
              "Cybertruck production ramping 40% QoQ. Shipping 20k this quarter."),
    ]

    # Analyst returns ONE insight: TSLA long, high conviction.
    insight_payload = {
        "insights": [{
            "post_id": "p1",
            "author": "@elonmusk",
            "tickers": ["TSLA"],
            "direction": "long",
            "conviction": 0.75,
            "timeframe": "swing",
            "rationale": "Concrete production numbers from the CEO — tradeable.",
            "risks": "Musk often under-delivers on delivery guidance.",
        }]
    }
    # Strategist returns ONE strategy for TSLA.
    strategy_payload = {
        "strategies": [{
            "ticker": "TSLA",
            "side": "long",
            "conviction": 0.72,
            "entry_zone": "249.00-251.00",
            "stop": "243.90 (-2.5%)",
            "targets": ["258 (+3.5%)", "265 (+6.3%)"],
            "size_pct": "2% of book",
            "time_in_force": "Day + 2",
            "exit_rules": [
                "Trail stop to entry at T1",
                "Flat immediately if Musk deletes the post",
            ],
            "execution_steps": [
                "Limit buy 1% @ 249.50",
                "Add 1% on break of 251.00 with volume",
                "OCO: stop 243.90, TP ladder 258/265",
            ],
            "invalidation": "Break 245 on >2x avg volume",
            "source_post_id": "p1",
        }]
    }
    client = FakeAnthropic([
        _response([_tool_block("submit_insights", insight_payload)]),
        _response([_tool_block("submit_strategies", strategy_payload)]),
    ])

    monkeypatch.setattr(
        "src.market.snapshot",
        lambda t: MarketSnapshot(
            ticker=t, spot=250.12, prev_close=248.00,
            day_pct=0.85, five_day_pct=2.1,
            headlines=["Tesla beats Q1 deliveries", "Analysts lift TSLA targets"],
        ),
    )

    insights = analyst.analyze(client, posts)
    assert len(insights) == 1
    assert insights[0].tickers == ["TSLA"]
    assert insights[0].conviction >= MIN_CONVICTION

    snapshots = {"TSLA": MarketSnapshot(
        ticker="TSLA", spot=250.12, prev_close=248.00, day_pct=0.85,
        five_day_pct=2.1, headlines=["Tesla beats Q1 deliveries"],
    )}
    strategies = strategist.strategize(client, insights, {"p1": posts[0]}, snapshots)
    assert len(strategies) == 1
    s = strategies[0]
    assert s.ticker == "TSLA" and s.side == "long"
    assert s.source_url.startswith("https://x.com/elonmusk/")

    msg = notifier.format_strategy(s)
    assert "TSLA LONG" in msg
    assert "Entry: 249.00-251.00" in msg
    assert "Spot 250.12" in msg
    assert "Musk deletes" in msg


# --- scenario 2: Trump tariff → multi-ticker macro signal ------------------

def test_scenario_trump_tariff_macro(monkeypatch):
    posts = [
        _post("t42", "realDonaldTrump",
              "I will impose 25% TARIFF on all Chinese EVs starting May 1. "
              "American jobs first!",
              platform="truth_social"),
    ]

    insight_payload = {
        "insights": [{
            "post_id": "t42",
            "author": "@realDonaldTrump",
            "tickers": ["TSLA", "XLI", "FXI"],
            "direction": "long",
            "conviction": 0.68,
            "timeframe": "position",
            "rationale": "Specific tariff policy, dated, sector-wide impact.",
            "risks": "Policy may not survive legal challenges / WTO pushback.",
        }]
    }
    # Strategist picks the two cleanest plays: long TSLA, short FXI.
    strategy_payload = {
        "strategies": [
            {
                "ticker": "TSLA",
                "side": "long", "conviction": 0.65,
                "entry_zone": "250-253", "stop": "244",
                "targets": ["262", "270"],
                "size_pct": "2% of book", "time_in_force": "GTC 30d",
                "exit_rules": ["Flat if tariff is paused or watered down"],
                "execution_steps": ["Scale in 50/50 on pullbacks"],
                "invalidation": "Policy reversal by May 1",
                "source_post_id": "t42",
            },
            {
                "ticker": "FXI",
                "side": "short", "conviction": 0.55,
                "entry_zone": "28.50-29.00", "stop": "29.80",
                "targets": ["27.50", "26.80"],
                "size_pct": "1.5% of book", "time_in_force": "GTC 14d",
                "exit_rules": ["Cover half at T1"],
                "execution_steps": ["Short FXI via puts if liquid"],
                "invalidation": "Broad China stimulus headline",
                "source_post_id": "t42",
            },
        ]
    }

    client = FakeAnthropic([
        _response([_tool_block("submit_insights", insight_payload)]),
        _response([_tool_block("submit_strategies", strategy_payload)]),
    ])

    snapshots = {
        "TSLA": MarketSnapshot(ticker="TSLA", spot=251.0, day_pct=1.2),
        "FXI":  MarketSnapshot(ticker="FXI",  spot=28.75, day_pct=-0.8),
        "XLI":  MarketSnapshot(ticker="XLI",  spot=130.0),
    }

    insights = analyst.analyze(client, posts)
    strategies = strategist.strategize(
        client, insights, {"t42": posts[0]}, snapshots,
    )
    assert {s.ticker for s in strategies} == {"TSLA", "FXI"}
    assert any(s.side == "short" for s in strategies)
    assert all(s.source_post_id == "t42" for s in strategies)


# --- scenario 3: Saylor meme → neutral, filtered ---------------------------

def test_scenario_saylor_meme_filtered(monkeypatch):
    posts = [_post("s9", "saylor", "Bitcoin. 💎🙌")]
    # Analyst correctly classifies this as neutral / low conviction.
    insight_payload = {
        "insights": [{
            "post_id": "s9",
            "author": "@saylor",
            "tickers": ["BTC-USD"],
            "direction": "neutral",
            "conviction": 0.1,
            "timeframe": "intraday",
            "rationale": "Meme with no new information.",
            "risks": "n/a",
        }]
    }
    client = FakeAnthropic([
        _response([_tool_block("submit_insights", insight_payload)]),
    ])

    insights = analyst.analyze(client, posts)
    # Apply the same MIN_CONVICTION + neutral filter main.py applies.
    actionable = [
        i for i in insights
        if i.conviction >= MIN_CONVICTION and i.direction != "neutral"
    ]
    assert actionable == []  # nothing to send → no Telegram noise


# --- scenario 4: Ackman activist stake → position trade --------------------

def test_scenario_ackman_activist_cmg(monkeypatch):
    posts = [
        _post("a1", "BillAckman",
              "Pershing Square has taken a $1B position in $CMG. "
              "We see material upside from intl expansion."),
    ]
    insight_payload = {
        "insights": [{
            "post_id": "a1",
            "author": "@BillAckman",
            "tickers": ["CMG"],
            "direction": "long",
            "conviction": 0.82,
            "timeframe": "position",
            "rationale": "Named activist stake with size — historically alpha.",
            "risks": "Trade already crowded if headlines leaked pre-post.",
        }]
    }
    strategy_payload = {
        "strategies": [{
            "ticker": "CMG",
            "side": "long", "conviction": 0.78,
            "entry_zone": "pullbacks to 58.00-59.00",
            "stop": "55.50 (-4.3%)",
            "targets": ["65.00", "72.00"],
            "size_pct": "3% of book",
            "time_in_force": "GTC 90d",
            "exit_rules": ["Trim 1/3 at T1"],
            "execution_steps": [
                "Limit buy 1.5% at 58.50",
                "Add on 50/200DMA reclaim",
            ],
            "invalidation": "Ackman publicly exits position",
            "source_post_id": "a1",
        }]
    }
    client = FakeAnthropic([
        _response([_tool_block("submit_insights", insight_payload)]),
        _response([_tool_block("submit_strategies", strategy_payload)]),
    ])

    insights = analyst.analyze(client, posts)
    strategies = strategist.strategize(
        client, insights, {"a1": posts[0]},
        {"CMG": MarketSnapshot(ticker="CMG", spot=59.20, day_pct=4.1,
                               headlines=["Ackman takes Chipotle stake"])},
    )
    assert len(strategies) == 1
    s = strategies[0]
    assert s.ticker == "CMG"
    assert s.conviction > 0.7
    assert "Ackman" in s.invalidation
    assert "GTC" in s.time_in_force
