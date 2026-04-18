"""Track how often an account's posts precede real price moves.

Two loops:
  1. `record_pending_hits` — when we notify on a post, stash a pending-hit
     entry. Checked on subsequent runs.
  2. `resolve_pending_hits` — for each stashed entry whose check window has
     elapsed, compare current price to price-at-post. If move >= threshold,
     increment the account's `hits`. Always increment `posts`.

Auto-discovery: we keep a `discovered` map of candidate handles (parents are
seeds). Candidates accumulate scores the same way and are promoted to active
targets once `score >= PROMOTE_SCORE`, if AUTO_PROMOTE is on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.config import (
    HIT_MOVE_PCT_LARGE,
    HIT_MOVE_PCT_SMALL,
    HIT_WINDOW_MIN,
)
from src.market import snapshot
from src.models import TradeStrategy
from src.state import State

log = logging.getLogger(__name__)


_SMALL_CAP_SUFFIXES = ("-USD",)  # crypto tickers use wider threshold


def queue_hits(state: State, strategies: list[TradeStrategy]) -> None:
    """Stash a pending-hit entry for each (author, ticker) strategy."""
    now = datetime.now(timezone.utc)
    for s in strategies:
        m = s.market_snapshot
        if not m or m.spot is None:
            continue
        state.pending_hits.append(
            {
                "post_id": s.source_post_id,
                "author": s.source_author,
                "ticker": s.ticker,
                "price_at_post": m.spot,
                "check_after_ts": now.timestamp() + HIT_WINDOW_MIN * 60,
                "url": s.source_url,
            }
        )
        # Always bump `posts` counter for this author immediately — we'll
        # bump `hits` later if follow-through materializes.
        rec = _ensure_score_record(state, s.source_author)
        rec["posts"] = int(rec.get("posts", 0)) + 1
        rec["last_updated"] = now.isoformat()


def resolve_pending_hits(state: State) -> None:
    """Check due pending hits and update hit counts."""
    now_ts = datetime.now(timezone.utc).timestamp()
    remaining: list[dict[str, Any]] = []
    for entry in state.pending_hits:
        if entry["check_after_ts"] > now_ts:
            remaining.append(entry)
            continue
        try:
            snap = snapshot(entry["ticker"])
        except Exception as e:  # noqa: BLE001 - scorer shouldn't crash run
            log.debug("hit-resolve snapshot failed %s: %s", entry["ticker"], e)
            remaining.append(entry)  # try again next run
            continue
        if snap.spot is None or entry["price_at_post"] in (None, 0):
            continue  # drop — can't score
        move = abs(snap.spot - entry["price_at_post"]) / entry["price_at_post"] * 100
        threshold = (
            HIT_MOVE_PCT_SMALL
            if any(entry["ticker"].endswith(suf) for suf in _SMALL_CAP_SUFFIXES)
            else HIT_MOVE_PCT_LARGE
        )
        rec = _ensure_score_record(state, entry["author"])
        if move >= threshold:
            rec["hits"] = int(rec.get("hits", 0)) + 1
            log.info(
                "HIT: @%s on %s moved %.2f%% (>= %.1f%%)",
                entry["author"], entry["ticker"], move, threshold,
            )
        rec["score"] = _score(rec)
        rec["last_updated"] = datetime.now(timezone.utc).isoformat()
    state.pending_hits = remaining


def record_candidates(state: State, parent_handle: str, mentioned_handles: list[str]) -> None:
    """Seed accounts retweeted/quoted by seeds as discovery candidates."""
    if not mentioned_handles:
        return
    now = datetime.now(timezone.utc).isoformat()
    for h in mentioned_handles:
        if h == parent_handle:
            continue
        rec = state.discovered.setdefault(
            h, {"first_seen": now, "mentions": 0, "parent": parent_handle}
        )
        rec["mentions"] = int(rec.get("mentions", 0)) + 1


def promote_candidates(state: State, *, threshold: int, auto: bool) -> list[str]:
    """Return handles eligible for promotion. Only actually promotes if `auto`."""
    ready: list[str] = []
    for handle, rec in state.influence_scores.items():
        if rec.get("score", 0) >= threshold and rec.get("promoted") is not True:
            ready.append(handle)
            if auto:
                rec["promoted"] = True
    return ready


def _ensure_score_record(state: State, handle: str) -> dict[str, Any]:
    return state.influence_scores.setdefault(
        handle, {"score": 0, "posts": 0, "hits": 0, "last_updated": None}
    )


def _score(rec: dict[str, Any]) -> float:
    """Hit rate weighted by sample size. Bayesian-flavored to avoid
    1/1 = 100% noise dominating the leaderboard.
    """
    posts = int(rec.get("posts", 0))
    hits = int(rec.get("hits", 0))
    # prior: 10 posts of 15% hit rate
    prior_n, prior_rate = 10, 0.15
    return round((hits + prior_n * prior_rate) / (posts + prior_n) * (posts + prior_n), 2)
