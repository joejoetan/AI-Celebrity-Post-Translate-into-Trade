"""Telegram notification. One richly-formatted message per TradeStrategy.

Uses the Bot API directly (no python-telegram-bot dep). Markdown V1 for
broad compatibility.

Message layout (sections are only rendered when they have content):
  - Header: side emoji, ticker, direction, conviction, data-limited flag
  - Source: author, post excerpt, link
  - Opportunity: analyst's plain-English summary
  - Market: verified quote + volume + earnings
  - Supporting facts: each with its cited source
  - Risks
  - Strategy mechanics: entry, stop, targets, size, TIF, exit rules
  - Action plan: time-sequenced immediate steps
  - Invalidation
"""
from __future__ import annotations

import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import HTTP_TIMEOUT_S
from src.models import TradeStrategy

log = logging.getLogger(__name__)

_SEND = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_LEN = 4000  # Telegram hard limit is 4096 — leave headroom


def send(token: str, chat_id: str, strategies: list[TradeStrategy], *, dry_run: bool = False) -> None:
    if not strategies:
        log.info("No strategies to notify")
        return
    for s in strategies:
        msg = format_strategy(s)
        if len(msg) > _MAX_LEN:
            msg = msg[:_MAX_LEN - 20] + "\n…(truncated)"
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

    # Header
    flag = " ⚠️ DATA-LIMITED" if s.data_limited else ""
    lines.append(f"{side_emoji} *{s.ticker} {s.side.upper()}* — conviction {s.conviction:.2f}{flag}")

    # Source
    lines.append(f"Source: @{s.source_author}")
    if s.source_excerpt:
        lines.append(f"_{_escape(s.source_excerpt)}_")
    lines.append("")

    # Opportunity summary
    if s.opportunity_summary:
        lines.append("*📋 Opportunity*")
        lines.append(_escape(s.opportunity_summary))
        lines.append("")

    # Market snapshot (verified data only)
    if s.market_snapshot:
        m = s.market_snapshot
        bits: list[str] = []
        if m.spot is not None:
            bits.append(f"Spot {m.spot:.2f}")
        if m.day_pct is not None:
            bits.append(f"{m.day_pct:+.1f}% today")
        if m.five_day_pct is not None:
            bits.append(f"{m.five_day_pct:+.1f}% 5d")
        if m.day_volume and m.avg_volume_10d:
            rel = m.day_volume / m.avg_volume_10d
            bits.append(f"{rel:.1f}x avg vol")
        if bits:
            lines.append("*📊 Market* — " + ", ".join(bits))
        if m.next_earnings:
            lines.append(f"Next earnings: {m.next_earnings}")
        if m.note:
            lines.append(f"_{_escape(m.note)}_")
        lines.append("")

    # Supporting facts (each cited)
    if s.supporting_facts:
        lines.append("*✅ Supporting facts*")
        for f in s.supporting_facts[:8]:
            lines.append(f"  • {_escape(f.fact)}  _[{_escape(f.source)}]_")
        lines.append("")

    # Risks
    if s.risks:
        lines.append("*⚠️ Risks*")
        for r in s.risks[:5]:
            lines.append(f"  • {_escape(r)}")
        lines.append("")

    # Strategy mechanics
    lines.append("*🎯 Strategy*")
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
    lines.append("")

    # Action plan (time-sequenced)
    plan = s.action_plan or s.execution_steps
    if plan:
        lines.append("*⏱ Action plan*")
        for i, step in enumerate(plan, 1):
            lines.append(f"  {i}. {_escape(step)}")
        lines.append("")

    lines.append(f"🛑 Invalidation: {_escape(s.invalidation)}")
    if s.source_url:
        lines.append(f"🔗 {s.source_url}")
    return "\n".join(lines)


def _escape(s: str) -> str:
    return (
        s.replace("_", "\\_")
        .replace("*", "\\*")
        .replace("[", "\\[")
        .replace("`", "\\`")
    )
