"""Pydantic models shared across modules."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Direction = Literal["long", "short", "neutral"]
Timeframe = Literal["intraday", "swing", "position"]


class Post(BaseModel):
    """A single social-media post we scraped."""

    id: str  # stable per-platform id (URL-safe)
    platform: Literal["x", "truth_social"]
    author_handle: str
    author_display: str
    text: str
    url: str
    created_at: datetime


class TradeInsight(BaseModel):
    """LLM-extracted trading angle for one post."""

    post_id: str
    author: str
    tickers: list[str] = Field(default_factory=list)
    direction: Direction
    conviction: float = Field(ge=0.0, le=1.0)
    timeframe: Timeframe
    rationale: str
    risks: str = ""


class MarketSnapshot(BaseModel):
    """Current market context for a ticker."""

    ticker: str
    spot: float | None = None
    prev_close: float | None = None
    day_pct: float | None = None
    five_day_pct: float | None = None
    headlines: list[str] = Field(default_factory=list)
    # Free-form notes when data is unavailable.
    note: str = ""


class TradeStrategy(BaseModel):
    """Final decision-ready brief for one ticker/idea."""

    ticker: str
    side: Direction
    conviction: float = Field(ge=0.0, le=1.0)
    entry_zone: str
    stop: str
    targets: list[str]
    size_pct: str
    time_in_force: str
    exit_rules: list[str]
    execution_steps: list[str]
    invalidation: str
    source_post_id: str
    source_author: str
    source_url: str
    source_excerpt: str
    market_snapshot: MarketSnapshot | None = None
