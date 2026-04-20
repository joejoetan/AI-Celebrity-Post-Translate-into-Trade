"""Durable state read from / written to disk (committed to `bot-state` branch).

Files:
  state/seen_posts.json        {handle: [post_id, ...]}
  state/influence_scores.json  {handle: {score, posts, hits, last_updated}}
  state/discovered.json        {handle: {first_seen, mentions, parent}}
  state/pending_hits.json      [{post_id, author, ticker, price_at_post,
                                 check_after_ts, url}]
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SEEN = "seen_posts.json"
INFLUENCE = "influence_scores.json"
DISCOVERED = "discovered.json"
PENDING_HITS = "pending_hits.json"


@dataclass
class State:
    seen_posts: dict[str, list[str]] = field(default_factory=dict)
    influence_scores: dict[str, dict[str, Any]] = field(default_factory=dict)
    discovered: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_hits: list[dict[str, Any]] = field(default_factory=list)

    # Keep this many post IDs per account in seen_posts (enough to dedup
    # across the rate at which these accounts post).
    MAX_SEEN_PER_ACCOUNT = 500

    def mark_seen(self, handle: str, post_id: str) -> bool:
        """Return True if newly seen, False if already seen."""
        seen = self.seen_posts.setdefault(handle, [])
        if post_id in seen:
            return False
        seen.append(post_id)
        if len(seen) > self.MAX_SEEN_PER_ACCOUNT:
            del seen[: len(seen) - self.MAX_SEEN_PER_ACCOUNT]
        return True


def load(state_dir: str | Path) -> State:
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    return State(
        seen_posts=_read_json(d / SEEN, {}),
        influence_scores=_read_json(d / INFLUENCE, {}),
        discovered=_read_json(d / DISCOVERED, {}),
        pending_hits=_read_json(d / PENDING_HITS, []),
    )


def save(state: State, state_dir: str | Path) -> None:
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / SEEN, state.seen_posts)
    _write_json(d / INFLUENCE, state.influence_scores)
    _write_json(d / DISCOVERED, state.discovered)
    _write_json(d / PENDING_HITS, state.pending_hits)


def _read_json(p: Path, default: Any) -> Any:
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        log.warning("Corrupt state file %s — resetting", p)
        return default


def _write_json(p: Path, obj: Any) -> None:
    p.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str))
