"""Claude Opus 4.7 in financial-analyst mode: fuses an insight + verified
market snapshot into a TradeStrategy with cited supporting facts and an
immediate action plan.

Key design choice: Claude is explicitly forbidden from fabricating any
price, earnings number, company fact, or news item. Every supporting
fact it produces must cite one of:
  (a) the source post text,
  (b) the provided market snapshot (yfinance quote / volume / earnings),
  (c) a headline URL from the provided news feed.
If data is missing, Claude must say so and cap conviction at 0.4.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

from src.config import CLAUDE_MODEL, LLM_MAX_TOKENS
from src.models import MarketSnapshot, Post, SupportingFact, TradeInsight, TradeStrategy

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are operating as a buy-side financial analyst
preparing decision-ready trade briefs for a portfolio manager who acts
on them in real time.

=== HARD RULE: NO FABRICATION ===

For each brief you must:
 1. Summarize the opportunity in plain English — what specifically is
    the trade, why now, what's the thesis.
 2. Assemble supporting_facts[]. EVERY fact must cite exactly one source:
       - "post"                    — the author's own words
       - "quote: spot"             — price from the market snapshot
       - "quote: day_pct"          — intraday change from the snapshot
       - "quote: volume"           — volume data from the snapshot
       - "quote: market_cap"       — market cap from the snapshot
       - "quote: earnings"         — next_earnings from the snapshot
       - "news: <outlet>"          — a headline we provided
    You are FORBIDDEN from inventing prices, earnings dates, volumes,
    company fundamentals, historical statistics, or news. If a fact is
    not present in the post or snapshot, do NOT include it.
 3. If critical market data is missing (snapshot.note is set, or spot is
    null, or news is empty), set data_limited=true, cap conviction at
    0.4, and widen entry ranges.
 4. List risks[] — what invalidates the thesis.
 5. Emit a time-sequenced action_plan[]. Each step must include timing.
    Example: "T+0–5min: place limit buy 1% @ 249.50", "At next open: add
    1% on break of 251 with volume confirmation", "EOD: move stop to
    break-even if T1 hit".
 6. Set trade mechanics: entry_zone, stop, targets, size_pct (never
    exceed 5%), time_in_force, exit_rules, invalidation.

=== SIZING DISCIPLINE ===

- Intraday reactions to celebrity posts: 0.5–2% of book.
- Swing trades with solid fundamental backing (from news/earnings):
  up to 3%.
- Position trades on activist stakes from named investors: up to 5%.
- Any trade with data_limited=true: cap at 1%.

=== OUTPUT ===

Call `submit_strategies` exactly once with one entry per actionable
ticker. Skip neutral insights and skip tickers where the move is already
fully priced (news day_pct already exceeds your first target on typical
volatility). If two insights point at the same ticker in the same
direction, merge them and cite both posts in supporting_facts."""


STRATEGY_TOOL: dict[str, Any] = {
    "name": "submit_strategies",
    "description": "Submit one trade strategy per actionable ticker.",
    "input_schema": {
        "type": "object",
        "properties": {
            "strategies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "side": {
                            "type": "string",
                            "enum": ["long", "short", "neutral"],
                        },
                        "conviction": {"type": "number", "minimum": 0, "maximum": 1},
                        "opportunity_summary": {"type": "string"},
                        "supporting_facts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "fact": {"type": "string"},
                                    "source": {"type": "string"},
                                },
                                "required": ["fact", "source"],
                            },
                        },
                        "risks": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "entry_zone": {"type": "string"},
                        "stop": {"type": "string"},
                        "targets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "size_pct": {"type": "string"},
                        "time_in_force": {"type": "string"},
                        "exit_rules": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "action_plan": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Time-sequenced immediate steps with explicit timing prefixes (T+0-5min, At open, EOD, etc.).",
                        },
                        "invalidation": {"type": "string"},
                        "source_post_id": {"type": "string"},
                        "data_limited": {
                            "type": "boolean",
                            "description": "True if critical market data was missing/stale.",
                        },
                    },
                    "required": [
                        "ticker",
                        "side",
                        "conviction",
                        "opportunity_summary",
                        "supporting_facts",
                        "risks",
                        "entry_zone",
                        "stop",
                        "targets",
                        "size_pct",
                        "time_in_force",
                        "exit_rules",
                        "action_plan",
                        "invalidation",
                        "source_post_id",
                        "data_limited",
                    ],
                },
            }
        },
        "required": ["strategies"],
    },
}


def strategize(
    client: Anthropic,
    insights: list[TradeInsight],
    posts_by_id: dict[str, Post],
    snapshots: dict[str, MarketSnapshot],
) -> list[TradeStrategy]:
    if not insights:
        return []

    payload = {
        "insights": [
            {
                **i.model_dump(),
                "post_excerpt": _excerpt(posts_by_id.get(i.post_id)),
                "post_url": posts_by_id[i.post_id].url if i.post_id in posts_by_id else "",
            }
            for i in insights
        ],
        "market": {t: s.model_dump() for t, s in snapshots.items()},
    }

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[{**STRATEGY_TOOL, "cache_control": {"type": "ephemeral"}}],
        tool_choice={"type": "tool", "name": "submit_strategies"},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )

    _log_usage("strategist", resp)
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_strategies":
            return [_hydrate(item, posts_by_id, snapshots)
                    for item in block.input.get("strategies", [])]

    log.warning("Strategist returned no tool_use block")
    return []


def _hydrate(
    raw: dict[str, Any],
    posts_by_id: dict[str, Post],
    snapshots: dict[str, MarketSnapshot],
) -> TradeStrategy:
    post = posts_by_id.get(raw.get("source_post_id", ""))
    supporting = [SupportingFact(**sf) for sf in raw.pop("supporting_facts", [])]
    return TradeStrategy(
        supporting_facts=supporting,
        source_author=post.author_handle if post else "",
        source_url=post.url if post else "",
        source_excerpt=_excerpt(post),
        market_snapshot=snapshots.get(raw["ticker"].upper()),
        **raw,
    )


def _excerpt(post: Post | None, n: int = 240) -> str:
    if post is None:
        return ""
    text = post.text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _log_usage(label: str, resp) -> None:
    u = getattr(resp, "usage", None)
    if not u:
        return
    log.info(
        "%s: in=%s out=%s cache_read=%s cache_write=%s",
        label,
        getattr(u, "input_tokens", None),
        getattr(u, "output_tokens", None),
        getattr(u, "cache_read_input_tokens", None),
        getattr(u, "cache_creation_input_tokens", None),
    )
