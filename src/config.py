"""Static configuration: targets, tickers, thresholds, instance lists.

Runtime secrets come from env vars (see .env.example).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


Platform = Literal["x", "truth_social"]


@dataclass(frozen=True)
class Target:
    handle: str
    platform: Platform
    display_name: str
    # truth_social requires a stable numeric account id
    platform_id: str | None = None
    # optional hint so the LLM knows what domain this person usually moves
    domain_hint: str = ""


# Seed list of high-influence accounts. influence.py may promote more.
SEED_TARGETS: list[Target] = [
    Target(
        handle="elonmusk",
        platform="x",
        display_name="Elon Musk",
        domain_hint="TSLA, TWTR/X, DOGE, crypto, SpaceX suppliers, AI",
    ),
    Target(
        handle="realDonaldTrump",
        platform="truth_social",
        # Trump's numeric account id on truthsocial.com is stable.
        platform_id="107780257626128497",
        display_name="Donald Trump",
        domain_hint="DJT, tariffs, defense, energy, banks, macro, USD, bonds",
    ),
    Target(
        handle="realDonaldTrump",
        platform="x",
        display_name="Donald Trump (X mirror)",
        domain_hint="DJT, tariffs, defense, energy, banks, macro",
    ),
    Target(
        handle="CathieDWood",
        platform="x",
        display_name="Cathie Wood",
        domain_hint="ARKK holdings, disruptive tech, TSLA, COIN, ROKU, PATH",
    ),
    Target(
        handle="BillAckman",
        platform="x",
        display_name="Bill Ackman",
        domain_hint="activist positions, CMG, HHH, restaurants, macro",
    ),
    Target(
        handle="chamath",
        platform="x",
        display_name="Chamath Palihapitiya",
        domain_hint="SPACs, tech, crypto, macro",
    ),
    Target(
        handle="saylor",
        platform="x",
        display_name="Michael Saylor",
        domain_hint="BTC, MSTR, bitcoin equities",
    ),
    Target(
        handle="aleabitoreddit",
        platform="x",
        display_name="Serenity",
        domain_hint="crypto, macro commentary, market sentiment",
    ),
]


# Nitter / Nitter-alternative instances. Order doesn't matter — scraper
# randomizes. Nitter churns heavily; check https://status.d420.de/ or
# https://xcancel.com/ if scraping stops working and update this list.
NITTER_INSTANCES: list[str] = [
    "https://xcancel.com",              # Nitter fork, most stable in 2025+
    "https://nitter.space",
    "https://nitter.privacyredirect.com",
    "https://nitter.poast.org",
    "https://nitter.tiekoetter.com",
    "https://nitter.privacydev.net",
    "https://nitter.net",
]


# Thresholds
MIN_CONVICTION: float = float(os.getenv("MIN_CONVICTION", "0.3"))
LOOKBACK_HOURS: int = int(os.getenv("LOOKBACK_HOURS", "2"))
AUTO_PROMOTE: bool = os.getenv("AUTO_PROMOTE", "false").lower() == "true"
# Promote candidate to active list when score >= this over rolling window.
PROMOTE_SCORE: int = int(os.getenv("PROMOTE_SCORE", "5"))
# Price-move threshold for counting a "hit" on a ticker mentioned in a post.
HIT_MOVE_PCT_LARGE: float = 1.5
HIT_MOVE_PCT_SMALL: float = 4.0
# Minutes after a post to check follow-through.
HIT_WINDOW_MIN: int = 60

# LLM
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
LLM_MAX_TOKENS: int = 2000

# HTTP
HTTP_TIMEOUT_S: float = 10.0
USER_AGENT: str = (
    "Mozilla/5.0 (compatible; MarketTrendBot/0.1; +https://github.com/)"
)


def rss_override(handle: str) -> str | None:
    """User-provided direct RSS URL for a given handle, via env var.

    Set RSS_URL_<HANDLE>=https://... in repo secrets to plug in any
    external RSS feed (rss.app, FetchRSS, Feedly, etc.) for a specific
    account. Takes precedence over syndication and Nitter.
    """
    return os.getenv(f"RSS_URL_{handle.upper()}")


@dataclass
class RuntimeConfig:
    anthropic_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    state_dir: str = field(default_factory=lambda: os.getenv("STATE_DIR", "state"))
    dry_run: bool = False

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "RuntimeConfig":
        missing = [
            k for k in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
            if not os.getenv(k)
        ]
        if missing and not dry_run:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            dry_run=dry_run,
        )
