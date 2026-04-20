"""Claude Opus 4.7 fuses an insight + market snapshot into a TradeStrategy.

Same prompt-cached pattern as analyst.py. Run once per batch — the LLM sees
all surviving insights (+ their market context) together so it can de-conflict
overlapping ideas (e.g. two posts both hitting SPY).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

from src.config import CLAUDE_MODEL, LLM_MAX_TOKENS
from src.models import MarketSnapshot, Post, TradeInsight, TradeStrategy

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a portfolio manager turning analyst insights into
decision-ready trade briefs. For each insight (with its current market
snapshot), produce one strategy per tradeable ticker.

Design principles:
- Be SPECIFIC. The user will read this on their phone and act. Give price
  zones, not prose.
- Size conservatively: 0.5%–3% of book for intraday reactions; up to 5%
  for high-conviction swings. Never recommend >5%.
- Always include a hard stop and a clear invalidation rule tied to the
  post (e.g. "flat if author deletes/retracts").
- Respect the snapshot: if spot is already up 5% on the news, the alpha
  is gone — reduce size, widen entry, or mark as 'chase — skip'.
- For crypto/24h markets set TIF accordingly.
- If two insights point at the same ticker in the same direction, combine
  them into one strategy and cite both. If they conflict, pick the higher-
  conviction one and note the other as a risk.
- If market data is missing (snapshot.note), acknowledge it and widen
  margins / reduce size.

Output via the `submit_strategies` tool — one call, one entry per
actionable ticker. Skip insights with direction='neutral' or where the
market has already fully priced the move."""


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
                        "conviction": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
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
                        "execution_steps": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "invalidation": {"type": "string"},
                        "source_post_id": {"type": "string"},
                    },
                    "required": [
                        "ticker",
                        "side",
                        "conviction",
                        "entry_zone",
                        "stop",
                        "targets",
                        "size_pct",
                        "time_in_force",
                        "exit_rules",
                        "execution_steps",
                        "invalidation",
                        "source_post_id",
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
        "market": {
            t: s.model_dump() for t, s in snapshots.items()
        },
    }

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[{**STRATEGY_TOOL, "cache_control": {"type": "ephemeral"}}],
        tool_choice={"type": "tool", "name": "submit_strategies"},
        messages=[
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }
        ],
    )

    _log_usage("strategist", resp)
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_strategies":
            raw = block.input.get("strategies", [])
            return [_hydrate(item, posts_by_id, snapshots) for item in raw]

    log.warning("Strategist returned no tool_use block")
    return []


def _hydrate(
    raw: dict[str, Any],
    posts_by_id: dict[str, Post],
    snapshots: dict[str, MarketSnapshot],
) -> TradeStrategy:
    post = posts_by_id.get(raw.get("source_post_id", ""))
    return TradeStrategy(
        **raw,
        source_author=post.author_handle if post else "",
        source_url=post.url if post else "",
        source_excerpt=_excerpt(post),
        market_snapshot=snapshots.get(raw["ticker"].upper()),
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
