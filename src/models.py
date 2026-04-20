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


class NewsItem(BaseModel):
    title: str
    source: str  # "yfinance", "yahoo_rss", "google_news"
    url: str = ""
    published: str = ""  # ISO8601 if available


class MarketSnapshot(BaseModel):
    """Verified market context for a ticker. Every field is either from a
    real provider (yfinance / RSS) or empty — never fabricated."""

    ticker: str
    spot: float | None = None
    prev_close: float | None = None
    day_pct: float | None = None
    five_day_pct: float | None = None
    day_volume: int | None = None
    avg_volume_10d: int | None = None
    market_cap: float | None = None
    next_earnings: str = ""  # "YYYY-MM-DD" or ""
    news: list[NewsItem] = Field(default_factory=list)
    # Free-form notes when data is unavailable.
    note: str = ""

    @property
    def headlines(self) -> list[str]:
        """Compat shim for older code that read `headlines`."""
        return [n.title for n in self.news]


class SupportingFact(BaseModel):
    """One verifiable fact with its source citation."""

    fact: str
    source: str  # e.g. "post", "yfinance quote", "google_news: Reuters"


class TradeStrategy(BaseModel):
    """Final decision-ready brief for one ticker/idea.

    Every field is grounded in either the source post or the provided
    market snapshot — the LLM is forbidden from fabricating facts.
    """

    ticker: str
    side: Direction
    conviction: float = Field(ge=0.0, le=1.0)
    # Financial-analyst sections
    opportunity_summary: str = ""
    supporting_facts: list[SupportingFact] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    # Trade mechanics
    entry_zone: str
    stop: str
    targets: list[str]
    size_pct: str
    time_in_force: str
    exit_rules: list[str]
    # Time-sequenced immediate action plan
    action_plan: list[str] = Field(default_factory=list)
    # Legacy — kept for backward compatibility with older tests
    execution_steps: list[str] = Field(default_factory=list)
    invalidation: str
    # Provenance
    source_post_id: str
    source_author: str
    source_url: str
    source_excerpt: str
    market_snapshot: MarketSnapshot | None = None
    data_limited: bool = False  # true when market data was missing/stale
