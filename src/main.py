"""Orchestrator — one pass of the scrape → analyze → notify pipeline.

Designed to be invoked on a 5-minute schedule by GitHub Actions (or cron).
State is read from STATE_DIR and written back; the workflow is responsible
for persisting STATE_DIR across runs (via a dedicated state branch).
"""
from __future__ import annotations

import argparse
import logging
import sys

from anthropic import Anthropic

from src import analyst, influence, market, notifier, state, strategist
from src.config import (
    AUTO_PROMOTE,
    LOOKBACK_HOURS,
    MIN_CONVICTION,
    PROMOTE_SCORE,
    SEED_TARGETS,
    RuntimeConfig,
    Target,
)
from src.models import Post
from src.scraper.base import cutoff
from src.scraper.truth_social import TruthSocialScraper
from src.scraper.x_nitter import XScraper


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Skip Telegram send; print messages to stdout.")
    p.add_argument("--since-hours", type=int, default=LOOKBACK_HOURS,
                   help="Lookback window for fresh posts (default from env).")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("main")

    cfg = RuntimeConfig.from_env(dry_run=args.dry_run)
    st = state.load(cfg.state_dir)
    since = cutoff(args.since_hours)

    # 1. Resolve any pending influence hits from earlier runs first.
    influence.resolve_pending_hits(st)

    # 2. Scrape new posts across all active targets.
    new_posts = _scrape_all(SEED_TARGETS, st, since)
    log.info("Scraped %d new posts across %d targets", len(new_posts), len(SEED_TARGETS))

    if not new_posts:
        state.save(st, cfg.state_dir)
        return 0

    # 3. Claude analyst: posts → insights.
    client = Anthropic(api_key=cfg.anthropic_api_key) if cfg.anthropic_api_key else None
    if client is None:
        log.warning("No ANTHROPIC_API_KEY — skipping LLM analysis (dry-run only).")
        state.save(st, cfg.state_dir)
        return 0

    insights = analyst.analyze(client, new_posts)
    insights = [i for i in insights if i.conviction >= MIN_CONVICTION and i.direction != "neutral"]
    log.info("Analyst produced %d actionable insights", len(insights))
    if not insights:
        state.save(st, cfg.state_dir)
        return 0

    # 4. Market snapshots for all tickers mentioned.
    tickers = sorted({t.upper() for i in insights for t in i.tickers})
    snapshots = {t: market.snapshot(t) for t in tickers}

    # 5. Claude strategist: insights + snapshots → strategies.
    posts_by_id = {p.id: p for p in new_posts}
    strategies = strategist.strategize(client, insights, posts_by_id, snapshots)
    log.info("Strategist produced %d strategies", len(strategies))

    # 6. Notify.
    notifier.send(
        cfg.telegram_bot_token,
        cfg.telegram_chat_id,
        strategies,
        dry_run=cfg.dry_run,
    )

    # 7. Influence bookkeeping for follow-through scoring.
    influence.queue_hits(st, strategies)
    promoted = influence.promote_candidates(st, threshold=PROMOTE_SCORE, auto=AUTO_PROMOTE)
    if promoted:
        log.info("Promotion-eligible (auto=%s): %s", AUTO_PROMOTE, promoted)

    # 8. Persist.
    state.save(st, cfg.state_dir)
    return 0


def _scrape_all(targets: list[Target], st: state.State, since) -> list[Post]:
    x = XScraper()
    fresh: list[Post] = []
    for tgt in targets:
        try:
            if tgt.platform == "x":
                posts = x.fetch(tgt.handle, since)
            elif tgt.platform == "truth_social":
                if not tgt.platform_id:
                    continue
                posts = TruthSocialScraper(tgt.platform_id).fetch(tgt.handle, since)
            else:
                continue
        except Exception as e:  # noqa: BLE001 - one bad target can't sink the run
            logging.getLogger("scrape").warning("Scrape failed for %s/%s: %s",
                                                tgt.platform, tgt.handle, e)
            continue
        for p in posts:
            key = f"{p.platform}:{p.author_handle}"
            if st.mark_seen(key, p.id):
                fresh.append(p)
    fresh.sort(key=lambda p: p.created_at)
    return fresh


if __name__ == "__main__":
    sys.exit(main())
