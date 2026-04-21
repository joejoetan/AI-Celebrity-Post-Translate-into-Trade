"""Diagnostic: see what the bot WOULD have scraped, analyzed, and sent —
but without actually sending to Telegram or writing state.

Runs against the live scrapers and (if ANTHROPIC_API_KEY set) live
analyst, and prints per-target, per-post verdicts:

  ✅ SENT       — passed all filters, would have been pushed to Telegram
  🟡 FILTERED   — scraped & analyzed, but dropped (neutral / low conviction)
  ⚫ NO POSTS   — scraped successfully but nothing new
  ❌ SCRAPE FAIL — all scraper fallbacks failed (Nitter all down, etc.)

Usage:
    python scripts/diagnose.py --since-hours 6
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import analyst, market, strategist  # noqa: E402
from src.config import MIN_CONVICTION, SEED_TARGETS  # noqa: E402
from src.scraper.truth_social import TruthSocialScraper  # noqa: E402
from src.scraper.x_nitter import XScraper  # noqa: E402


GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since-hours", type=int, default=6)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip the Claude call; only show raw scrape results.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    print(f"{BOLD}Diagnostic run — looking back {args.since_hours}h "
          f"(since {since:%Y-%m-%d %H:%M UTC}){RESET}\n")

    # Step 1: scrape every target, recording which path won.
    all_posts = []
    scrape_results: dict[str, tuple[str, list, str]] = {}

    for tgt in SEED_TARGETS:
        key = f"{tgt.platform}:@{tgt.handle}"
        path = ""
        try:
            if tgt.platform == "x":
                x_scraper = XScraper()  # fresh instance so last_path is clean
                posts = x_scraper.fetch(tgt.handle, since)
                path = x_scraper.last_path or "none"
            elif tgt.platform == "truth_social" and tgt.platform_id:
                posts = TruthSocialScraper(tgt.platform_id).fetch(tgt.handle, since)
                path = "truth_social_api" if posts else "none"
            else:
                posts = []
            if posts:
                scrape_results[key] = ("ok", posts, path)
                all_posts.extend(posts)
            else:
                scrape_results[key] = ("empty", [], path or "all fallbacks empty")
        except Exception as e:  # noqa: BLE001
            scrape_results[key] = (f"error: {e.__class__.__name__}", [], "")

    # Step 2: report scrape stage with path info.
    print(f"{BOLD}── Stage 1: Scrape results ──{RESET}")
    print(f"  {'target':<42} {'result':<14} path")
    print(f"  {'-'*42} {'-'*14} {'-'*40}")
    for key, (status, posts, path) in scrape_results.items():
        if status == "ok":
            result = f"{GREEN}✓ {len(posts)} post(s){RESET}"
            path_col = GREEN + path + RESET
        elif status == "empty":
            result = f"{GRAY}· empty       {RESET}"
            path_col = GRAY + path + RESET
        else:
            result = f"{RED}✗ fail       {RESET}"
            path_col = RED + status + RESET
        print(f"  {key:<42} {result} {path_col}")

    total_scraped = sum(len(posts) for _, posts, _ in scrape_results.values())
    print(f"\n{BOLD}Scraped {total_scraped} post(s) total{RESET}\n")

    if not all_posts:
        print(f"{YELLOW}No posts in the lookback window. Possible reasons:{RESET}")
        print("  • Nobody in the seed list posted in the last "
              f"{args.since_hours}h (try a wider window).")
        print("  • All Nitter instances rate-limited — accounts showing")
        print("    scraper errors above are the culprits.")
        return 0

    # Step 3: show raw posts.
    print(f"{BOLD}── Stage 2: Raw posts ──{RESET}")
    for p in all_posts:
        age = (datetime.now(timezone.utc) - p.created_at)
        age_str = _human_age(age)
        excerpt = p.text.replace("\n", " ")[:100]
        print(f"  [{age_str:>6}] @{p.author_handle}: {excerpt}")
    print()

    if args.no_llm:
        return 0

    # Step 4: analyst (real Claude call).
    if not os.getenv("ANTHROPIC_API_KEY"):
        print(f"{YELLOW}Skipping analyst — ANTHROPIC_API_KEY not set.{RESET}")
        return 0

    from anthropic import Anthropic
    import os as _os
    import traceback as _tb

    model = _os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    print(f"{GRAY}Using model: {model}{RESET}")
    client = Anthropic()

    print(f"{BOLD}── Stage 3: Analyst verdict per post ──{RESET}")
    try:
        insights = analyst.analyze(client, all_posts)
    except Exception as e:
        print(f"  {RED}Analyst failed: {e.__class__.__name__}: {e}{RESET}")
        _tb.print_exc()
        print(f"\n{YELLOW}Common causes:{RESET}")
        print(f"  • Invalid model ID. Check CLAUDE_MODEL env var (currently '{model}').")
        print(f"  • Billing not set up at console.anthropic.com.")
        print(f"  • API key doesn't have access to this model.")
        return 0  # keep exit code clean so the rest of diagnose output is visible

    insights_by_post = {i.post_id: i for i in insights}
    passed_insights = []
    counts = {"passed": 0, "neutral": 0, "low_conv": 0, "missing": 0}
    for p in all_posts:
        i = insights_by_post.get(p.id)
        if i is None:
            verdict = f"{RED}✗ analyst did not return a verdict{RESET}"
            counts["missing"] += 1
        elif i.direction == "neutral":
            verdict = (f"{YELLOW}🟡 FILTERED — NEUTRAL (conv {i.conviction:.2f}):"
                       f" {i.rationale[:80]}{RESET}")
            counts["neutral"] += 1
        elif i.conviction < MIN_CONVICTION:
            verdict = (f"{YELLOW}🟡 FILTERED — LOW CONVICTION "
                       f"{i.conviction:.2f} < {MIN_CONVICTION} ({i.direction} "
                       f"{i.tickers}): {i.rationale[:60]}{RESET}")
            counts["low_conv"] += 1
        else:
            verdict = (f"{GREEN}✅ PASSED — {i.direction.upper()} conv "
                       f"{i.conviction:.2f} {i.tickers}{RESET}")
            passed_insights.append(i)
            counts["passed"] += 1
        excerpt = p.text.replace("\n", " ")[:80]
        print(f"  @{p.author_handle}: {excerpt}")
        print(f"    → {verdict}\n")

    print(f"{BOLD}Analyst breakdown:{RESET} "
          f"{GREEN}{counts['passed']} passed{RESET}, "
          f"{YELLOW}{counts['neutral']} neutral{RESET}, "
          f"{YELLOW}{counts['low_conv']} low-conviction{RESET}, "
          f"{RED}{counts['missing']} missing verdict{RESET}")

    if not passed_insights:
        print(f"\n{YELLOW}══ No insights passed the conviction filter ══{RESET}")
        print(f"  That's why you're not seeing Telegram messages — the "
              f"analyst judged every post as either pure noise (neutral) or "
              f"not confident enough (< {MIN_CONVICTION}).")
        print(f"\n  To see MORE messages:")
        print(f"    • Edit .github/workflows/scrape.yml → set "
              f"MIN_CONVICTION: \"0.25\" (or even \"0.15\" for maximum volume)")
        print(f"    • Or accept that these specific posts genuinely weren't")
        print(f"      market-moving (retweets, personal posts, etc.)")
        return 0

    # Step 5: strategist (real Claude call).
    print(f"{BOLD}── Stage 4: Strategist output ──{RESET}")
    posts_by_id = {p.id: p for p in all_posts}
    tickers = sorted({t.upper() for i in passed_insights for t in i.tickers})
    snapshots = {t: market.snapshot(t) for t in tickers}
    try:
        strategies = strategist.strategize(client, passed_insights, posts_by_id, snapshots)
    except Exception as e:
        print(f"  {RED}Strategist failed: {e.__class__.__name__}: {e}{RESET}")
        import traceback as _tb
        _tb.print_exc()
        return 0

    for s in strategies:
        print(f"  {GREEN}✅ {s.ticker} {s.side.upper()}{RESET} "
              f"(conv {s.conviction:.2f}, source @{s.source_author})")
        if s.data_limited:
            print(f"    {YELLOW}⚠ DATA-LIMITED — some market data was missing{RESET}")

    print(f"\n{BOLD}Summary:{RESET} "
          f"{total_scraped} scraped → "
          f"{len(passed_insights)} passed analyst → "
          f"{len(strategies)} would send to Telegram")
    return 0


def _human_age(delta: timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


if __name__ == "__main__":
    sys.exit(main())
