"""Claude Opus 4.7 turns a batch of posts into structured TradeInsight objects.

Design notes:
- One call per run (not per post). All new posts in a cycle are analyzed in a
  single message — cheaper and keeps relative ranking consistent.
- System prompt + tool schema are marked with `cache_control: ephemeral` so
  the 5-minute cadence hits the Anthropic prompt cache (~90% discount after
  the first run).
- We use tool-use to force a well-typed JSON response instead of regexing
  free text.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

from src.config import CLAUDE_MODEL, LLM_MAX_TOKENS
from src.models import Post, TradeInsight

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a sell-side equity + macro analyst reading posts by
high-influence public figures and extracting trade-relevant signals.

For each post you receive, decide:
1. Which specific, tradeable tickers are implicated. Use standard symbols:
   - US equities: e.g. TSLA, AAPL, DJT
   - Crypto: BTC-USD, ETH-USD, DOGE-USD
   - Index proxies: SPY, QQQ, IWM, DXY, TLT
   - Commodities: GLD, USO, UNG
   - Sectors if specific: XLE, XLF, XLK, ITA (defense)
   If the post is too vague to identify any real ticker, return an empty list.

2. Direction: "long", "short", or "neutral" (neutral = notable info but no
   clear side).

3. Conviction 0.0–1.0 based on:
   - Specificity of the statement (named company / product / policy > vibes)
   - Author's track record of moving that asset
   - Whether the message is substantive vs. humor/meme
   - Whether it introduces genuinely new information

4. Timeframe: "intraday" (reaction fades same day), "swing" (days–weeks),
   "position" (weeks–months).

5. Rationale (1–2 sentences): what the post says and why it matters.

6. Risks (1 sentence): the main way this thesis gets invalidated.

Base rates to calibrate against:
- Musk tweets about Tesla product → TSLA intraday reaction, often fades.
- Musk memes about crypto → DOGE/BTC very short-term spikes.
- Trump policy statements (tariffs, rates, specific companies) → swing-to-
  position moves in the named sector and USD.
- Trump posts about his own media co → DJT intraday, high vol.
- Cathie Wood ARK conviction buys → short-term lift on ARKK holdings.
- Ackman / Chamath longs → name-specific swing moves.
- Saylor BTC posts → mild BTC/MSTR reaction; high frequency so low signal
  per post unless unusually specific.

Be conservative: if the post is a joke, a retweet of someone else with no
commentary, a personal update, or pure politics with no market nexus,
return direction="neutral" and conviction < 0.3. Those will be filtered.

HARD RULE — NO FABRICATION: Your rationale and risks must ONLY reference
(a) exact content of the post, or (b) widely-known base-rate knowledge
about the author (Musk moves TSLA, Ackman runs an activist fund, etc.).
Do NOT invent specific prices, earnings dates, recent news, or company
fundamentals. The strategist layer sees real market data; you do not.
If you need a specific number, say "(pending snapshot verification)".

Return your answer by calling the `submit_insights` tool exactly once with
one entry per input post, in the same order."""


INSIGHT_TOOL: dict[str, Any] = {
    "name": "submit_insights",
    "description": "Submit structured trade insights for every input post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "insights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "post_id": {"type": "string"},
                        "author": {"type": "string"},
                        "tickers": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["long", "short", "neutral"],
                        },
                        "conviction": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "timeframe": {
                            "type": "string",
                            "enum": ["intraday", "swing", "position"],
                        },
                        "rationale": {"type": "string"},
                        "risks": {"type": "string"},
                    },
                    "required": [
                        "post_id",
                        "author",
                        "tickers",
                        "direction",
                        "conviction",
                        "timeframe",
                        "rationale",
                        "risks",
                    ],
                },
            }
        },
        "required": ["insights"],
    },
}


def analyze(client: Anthropic, posts: list[Post]) -> list[TradeInsight]:
    if not posts:
        return []

    user_payload = {
        "posts": [
            {
                "post_id": p.id,
                "author": f"@{p.author_handle} ({p.author_display})",
                "platform": p.platform,
                "created_at": p.created_at.isoformat(),
                "text": p.text,
            }
            for p in posts
        ]
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
        tools=[{**INSIGHT_TOOL, "cache_control": {"type": "ephemeral"}}],
        tool_choice={"type": "tool", "name": "submit_insights"},
        messages=[
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            }
        ],
    )

    _log_usage("analyst", resp)
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_insights":
            raw_items = block.input.get("insights", [])
            return [TradeInsight(**item) for item in raw_items]

    log.warning("Analyst returned no tool_use block; response=%s", resp)
    return []


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
