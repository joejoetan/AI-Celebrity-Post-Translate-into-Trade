"""Offline end-to-end demo. Runs the full pipeline against canned posts
with mocked Claude + mocked market data, then prints the Telegram messages
that would have been sent. No API keys, no network.

Run:  python scripts/demo.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# Allow `python scripts/demo.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import analyst, notifier, strategist  # noqa: E402
from src.models import MarketSnapshot, Post  # noqa: E402


# -- canned scenarios -------------------------------------------------------

SCENARIOS = [
    {
        "name": "Musk — Cybertruck production",
        "post": Post(
            id="1776000000000000001",
            platform="x", author_handle="elonmusk", author_display="Elon Musk",
            text=("Cybertruck production ramping 40% QoQ. "
                  "Shipping 20k this quarter — first profitable quarter for the line."),
            url="https://x.com/elonmusk/status/1776000000000000001",
            created_at=datetime(2026, 4, 18, 14, 30, tzinfo=timezone.utc),
        ),
        "snapshots": {
            "TSLA": MarketSnapshot(
                ticker="TSLA", spot=250.12, prev_close=248.00,
                day_pct=0.85, five_day_pct=2.1,
                headlines=["Tesla beats Q1 deliveries",
                           "Analysts lift TSLA price targets"],
            ),
        },
        "insights": [{
            "post_id": "1776000000000000001", "author": "@elonmusk",
            "tickers": ["TSLA"], "direction": "long", "conviction": 0.75,
            "timeframe": "swing",
            "rationale": "Concrete production numbers with profitability claim.",
            "risks": "Musk has historically missed delivery guidance.",
        }],
        "strategies": [{
            "ticker": "TSLA", "side": "long", "conviction": 0.72,
            "entry_zone": "249.00-251.00", "stop": "243.90 (-2.5%)",
            "targets": ["258 (+3.5%)", "265 (+6.3%)"],
            "size_pct": "2% of book", "time_in_force": "Day + 2",
            "exit_rules": ["Trail stop to entry at T1",
                           "Flat immediately if Musk deletes the post"],
            "execution_steps": ["Limit buy 1% @ 249.50",
                                "Add 1% on break of 251.00 w/ volume",
                                "OCO: stop 243.90, TP 258/265"],
            "invalidation": "Break 245 on >2x avg volume",
            "source_post_id": "1776000000000000001",
        }],
    },
    {
        "name": "Trump — China EV tariff",
        "post": Post(
            id="truth-112345",
            platform="truth_social", author_handle="realDonaldTrump",
            author_display="Donald J. Trump",
            text="I will impose 25% TARIFF on all Chinese EVs starting May 1. American jobs first!",
            url="https://truthsocial.com/@realDonaldTrump/posts/112345",
            created_at=datetime(2026, 4, 18, 14, 32, tzinfo=timezone.utc),
        ),
        "snapshots": {
            "TSLA": MarketSnapshot(ticker="TSLA", spot=251.00, day_pct=1.2,
                                   headlines=["Tesla rallies on tariff news"]),
            "FXI":  MarketSnapshot(ticker="FXI",  spot=28.75,  day_pct=-0.8,
                                   headlines=["China ETF drops on US tariff threat"]),
        },
        "insights": [{
            "post_id": "truth-112345", "author": "@realDonaldTrump",
            "tickers": ["TSLA", "FXI", "XLI"], "direction": "long",
            "conviction": 0.68, "timeframe": "position",
            "rationale": "Specific tariff with named date and sector scope.",
            "risks": "Legal challenges or WTO action could delay.",
        }],
        "strategies": [
            {
                "ticker": "TSLA", "side": "long", "conviction": 0.65,
                "entry_zone": "250-253", "stop": "244",
                "targets": ["262", "270"],
                "size_pct": "2% of book", "time_in_force": "GTC 30d",
                "exit_rules": ["Flat if tariff is paused or watered down"],
                "execution_steps": ["Scale in 50/50 on pullbacks"],
                "invalidation": "Policy reversal before May 1",
                "source_post_id": "truth-112345",
            },
            {
                "ticker": "FXI", "side": "short", "conviction": 0.55,
                "entry_zone": "28.50-29.00", "stop": "29.80",
                "targets": ["27.50", "26.80"],
                "size_pct": "1.5% of book", "time_in_force": "GTC 14d",
                "exit_rules": ["Cover half at T1"],
                "execution_steps": ["Use FXI puts if liquid, else short shares"],
                "invalidation": "Broad China stimulus headline",
                "source_post_id": "truth-112345",
            },
        ],
    },
    {
        "name": "Saylor — BTC meme (should be filtered)",
        "post": Post(
            id="1776000000000000002",
            platform="x", author_handle="saylor", author_display="Michael Saylor",
            text="Bitcoin. 💎🙌",
            url="https://x.com/saylor/status/1776000000000000002",
            created_at=datetime(2026, 4, 18, 14, 34, tzinfo=timezone.utc),
        ),
        "snapshots": {"BTC-USD": MarketSnapshot(ticker="BTC-USD", spot=72000.0)},
        "insights": [{
            "post_id": "1776000000000000002", "author": "@saylor",
            "tickers": ["BTC-USD"], "direction": "neutral", "conviction": 0.1,
            "timeframe": "intraday",
            "rationale": "Meme post with no new information.",
            "risks": "n/a",
        }],
        "strategies": [],  # correctly empty
    },
    {
        "name": "Ackman — Chipotle activist stake",
        "post": Post(
            id="1776000000000000003",
            platform="x", author_handle="BillAckman", author_display="Bill Ackman",
            text=("Pershing Square has taken a $1B position in $CMG. "
                  "Material upside from intl expansion, loyalty program, digital."),
            url="https://x.com/BillAckman/status/1776000000000000003",
            created_at=datetime(2026, 4, 18, 14, 36, tzinfo=timezone.utc),
        ),
        "snapshots": {
            "CMG": MarketSnapshot(ticker="CMG", spot=59.20, day_pct=4.1,
                                  headlines=["Ackman discloses Chipotle stake",
                                             "CMG jumps on activist news"]),
        },
        "insights": [{
            "post_id": "1776000000000000003", "author": "@BillAckman",
            "tickers": ["CMG"], "direction": "long", "conviction": 0.82,
            "timeframe": "position",
            "rationale": "Sized activist stake disclosed by CEO of fund.",
            "risks": "Trade crowded if leaked to other funds pre-post.",
        }],
        "strategies": [{
            "ticker": "CMG", "side": "long", "conviction": 0.78,
            "entry_zone": "pullbacks to 58.00-59.00",
            "stop": "55.50 (-4.3%)",
            "targets": ["65.00 (+9.8%)", "72.00 (+21.6%)"],
            "size_pct": "3% of book", "time_in_force": "GTC 90d",
            "exit_rules": ["Trim 1/3 at T1",
                           "Full exit if Ackman files 13D amendment reducing"],
            "execution_steps": ["Limit buy 1.5% at 58.50",
                                "Add on 50/200DMA reclaim"],
            "invalidation": "Ackman publicly exits position",
            "source_post_id": "1776000000000000003",
        }],
    },
]


# -- scripted fake Anthropic client -----------------------------------------

def _tool_block(name, payload):
    return SimpleNamespace(type="tool_use", name=name, input=payload)


def _response(blocks):
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(
            input_tokens=1200, output_tokens=250,
            cache_read_input_tokens=1100, cache_creation_input_tokens=0,
        ),
    )


class ScriptedAnthropic:
    def __init__(self, queue):
        self._q = queue
        self._i = 0
        self.messages = self

    def create(self, **_):
        resp = self._q[self._i]
        self._i += 1
        return resp


def main() -> int:
    print("=" * 72)
    print("AI CELEBRITY-POST → TRADE SIGNAL BOT — OFFLINE DEMO")
    print("=" * 72)
    total_sent = 0

    for sc in SCENARIOS:
        print(f"\n>>> SCENARIO: {sc['name']}")
        print(f"    @{sc['post'].author_handle}: {sc['post'].text[:100]}")

        script = [_response([_tool_block("submit_insights",
                                         {"insights": sc["insights"]})])]
        if sc["strategies"]:
            script.append(
                _response([_tool_block("submit_strategies",
                                       {"strategies": sc["strategies"]})])
            )
        client = ScriptedAnthropic(script)

        insights = analyst.analyze(client, [sc["post"]])
        insights = [i for i in insights
                    if i.conviction >= 0.3 and i.direction != "neutral"]
        if not insights:
            print("    → analyst filtered (neutral / low conviction). "
                  "No Telegram message.")
            continue

        strategies = strategist.strategize(
            client, insights, {sc["post"].id: sc["post"]}, sc["snapshots"],
        )
        for s in strategies:
            total_sent += 1
            print("\n--- TELEGRAM MESSAGE ---")
            print(notifier.format_strategy(s))
            print("--- END MESSAGE ---")

    print("\n" + "=" * 72)
    print(f"Demo complete. Messages that would have been sent: {total_sent}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
