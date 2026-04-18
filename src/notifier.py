"""Telegram notification. One message per TradeStrategy.

Uses the Bot API directly (no python-telegram-bot dep). Markdown V1 for
broad compatibility.
"""
from __future__ import annotations

import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import HTTP_TIMEOUT_S
from src.models import TradeStrategy

log = logging.getLogger(__name__)

_SEND = "https://api.telegram.org/bot{token}/sendMessage"


def send(token: str, chat_id: str, strategies: list[TradeStrategy], *, dry_run: bool = False) -> None:
    if not strategies:
        log.info("No strategies to notify")
        return
    for s in strategies:
        msg = format_strategy(s)
        if dry_run:
            print("---\n" + msg + "\n---")
            continue
        _post(token, chat_id, msg)


def send_text(token: str, chat_id: str, text: str, *, dry_run: bool = False) -> None:
    if dry_run:
        print(text)
        return
    _post(token, chat_id, text)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def _post(token: str, chat_id: str, text: str) -> None:
    r = httpx.post(
        _SEND.format(token=token),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        },
        timeout=HTTP_TIMEOUT_S,
    )
    r.raise_for_status()


def format_strategy(s: TradeStrategy) -> str:
    side_emoji = {"long": "🟢", "short": "🔴", "neutral": "⚪"}.get(s.side, "•")
    lines: list[str] = []
    lines.append(f"{side_emoji} *{s.ticker} {s.side.upper()}* — conviction {s.conviction:.2f}")
    lines.append(f"Source: @{s.source_author}")
    if s.source_excerpt:
        lines.append(f"_{_escape(s.source_excerpt)}_")
    lines.append("")

    if s.market_snapshot:
        m = s.market_snapshot
        quote_bits = []
        if m.spot is not None:
            quote_bits.append(f"Spot {m.spot:.2f}")
        if m.day_pct is not None:
            quote_bits.append(f"{m.day_pct:+.1f}% today")
        if m.five_day_pct is not None:
            quote_bits.append(f"{m.five_day_pct:+.1f}% 5d")
        if quote_bits:
            lines.append("*Market* — " + ", ".join(quote_bits))
        if m.headlines:
            lines.append("Headlines:")
            for h in m.headlines[:3]:
                lines.append(f"  • {_escape(h)}")
        if m.note:
            lines.append(f"_{_escape(m.note)}_")
        lines.append("")

    lines.append("*Strategy*")
    lines.append(f"Entry: {_escape(s.entry_zone)}")
    lines.append(f"Stop:  {_escape(s.stop)}")
    if s.targets:
        lines.append("Targets: " + ", ".join(_escape(t) for t in s.targets))
    lines.append(f"Size:  {_escape(s.size_pct)}")
    lines.append(f"TIF:   {_escape(s.time_in_force)}")
    if s.exit_rules:
        lines.append("Exit rules:")
        for r in s.exit_rules:
            lines.append(f"  • {_escape(r)}")
    if s.execution_steps:
        lines.append("Execution:")
        for i, step in enumerate(s.execution_steps, 1):
            lines.append(f"  {i}. {_escape(step)}")
    lines.append(f"⚠️ Invalidation: {_escape(s.invalidation)}")
    if s.source_url:
        lines.append(f"🔗 {s.source_url}")
    return "\n".join(lines)


def _escape(s: str) -> str:
    # Telegram Markdown v1: escape the characters that break formatting.
    return s.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
