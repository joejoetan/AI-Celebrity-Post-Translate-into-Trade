# AI Celebrity-Post → Trade Signal Bot

Every 10 minutes, scrape the latest posts from a set of high-influence public
figures on X/Twitter and Truth Social, use Claude Sonnet 4.6 to translate them
into structured trade insights, cross-check with live market data + news, and
push decision-ready strategies to your Telegram.

Signals only — no auto-trading.

## Targets (default seed list)

- **Elon Musk** — `@elonmusk` (X)
- **Donald Trump** — `@realDonaldTrump` (Truth Social + X mirror)
- **Cathie Wood** — `@CathieDWood` (X)
- **Bill Ackman** — `@BillAckman` (X)
- **Chamath Palihapitiya** — `@chamath` (X)
- **Michael Saylor** — `@saylor` (X)

The bot also scores follow-through on every signal and can auto-promote
newly-discovered high-influence accounts (gated behind `AUTO_PROMOTE=false`
by default).

## Architecture

```
GH Actions (cron */10)
    │
    ▼
 main.py ──► scraper/x_nitter.py   (Nitter RSS with rotation + snscrape fallback)
         ├► scraper/truth_social.py (public JSON API)
         ├► analyst.py             (Claude Sonnet 4.6, prompt-cached)
         ├► market.py              (yfinance + Yahoo RSS)
         ├► strategist.py          (Claude Sonnet 4.6, prompt-cached)
         ├► notifier.py            (Telegram Bot API)
         └► influence.py           (follow-through scoring, discovery)

state/  (persisted on `bot-state` branch between runs)
```

## Setup

### 1. Secrets (GitHub repo → Settings → Secrets → Actions)

| Secret                | Required | Notes |
|-----------------------|----------|-------|
| `ANTHROPIC_API_KEY`   | yes      | `sk-ant-...` |
| `TELEGRAM_BOT_TOKEN`  | yes      | Create via [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID`    | yes      | Your user ID — get from [@userinfobot](https://t.me/userinfobot) |

No X API key needed — the bot uses public Nitter instances and falls back to
`snscrape`. No broker keys — signals only.

### 2. Enable the workflow

Push to the main branch. The workflow (`.github/workflows/scrape.yml`) runs
on `cron: */10 * * * *`. Trigger manually once via **Actions → scrape → Run
workflow** to verify Telegram delivery.

### 3. State branch

On the first run the workflow creates an orphan branch `bot-state` holding
`state/*.json`. Subsequent runs pull it, update it, and push. You can inspect
the full history of signals by looking at that branch.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env   # fill in secrets
set -a && source .env && set +a

# Full run, prints Telegram messages to stdout, skips actual send
python -m src.main --dry-run --since-hours 6

# Tests
pytest
```

## Configuration

All tunable via env vars (see `.env.example` and `src/config.py`):

| Var              | Default           | Purpose |
|------------------|-------------------|---------|
| `CLAUDE_MODEL`   | `claude-sonnet-4-6` | LLM for analyst + strategist |
| `MIN_CONVICTION` | `0.3`             | Drop insights below this score |
| `LOOKBACK_HOURS` | `2`               | How far back to scrape |
| `AUTO_PROMOTE`   | `false`           | Auto-add discovered influencers |
| `STATE_DIR`      | `state`           | Where to read/write state files |

## Notification format

```
🟢 *TSLA LONG* — conviction 0.72
Source: @elonmusk
_Cybertruck production ramping 40% QoQ — wild numbers_

*Market* — Spot 250.12, +0.8% today, +2.1% 5d
Headlines:
  • Tesla beats Q1 deliveries
  • Musk teases robotaxi

*Strategy*
Entry: 249.50-250.50
Stop:  244.00 (-2.5%)
Targets: 256 (+2.3%), 262 (+4.7%)
Size:  2% of book
TIF:   Day + 1
Exit rules:
  • trail stop to entry at T1
  • flat if author deletes post
Execution:
  1. Limit buy TSLA 250
  2. OCO stop 244 / TP 256
⚠️ Invalidation: break 245 on >2x avg volume
🔗 https://x.com/elonmusk/status/...
```

## When a specific account isn't being scraped

The X scraper tries, in order: **per-target RSS override → X syndication
endpoint → Nitter instances → snscrape**. If one handle consistently shows
zero posts in `state/seen_posts.json`, all fallbacks are failing for it.

**Fastest fix — use an RSS override:**

1. Go to [rss.app](https://rss.app) (free tier works) or [FetchRSS](https://fetchrss.com).
2. Create an RSS feed for the X account. Copy the feed URL.
3. In your repo: Settings → Secrets → Actions → New secret.
4. Name: `RSS_URL_<HANDLE>` (uppercase, e.g. `RSS_URL_ELONMUSK`).
5. Value: the feed URL.

The scraper will use that feed first and ignore Nitter/syndication entirely
for that handle. Cost: free tier, or ~$10/mo for unlimited feeds.

## Caveats

- **GitHub Actions cron is best-effort.** Under platform load, scheduled
  runs can be delayed 5–15 min or occasionally skipped. The pipeline is
  idempotent (dedup via `seen_posts.json`) so a skipped run just means
  the next run catches up.
- **Nitter instances churn.** Expect occasional fallback to `snscrape`.
  Update `NITTER_INSTANCES` in `src/config.py` when instances go dark.
- **yfinance is unofficial.** Treat snapshot data as indicative, not
  execution-grade quotes. For live trading, swap in a paid provider in
  `src/market.py`.
- **LLM costs.** Default model is Sonnet 4.6 — typical post volume runs
  $1–$2/day ($30–60/month). Switch to `claude-opus-4-7` for higher
  reasoning quality at ~5× the cost. Run the workflow's token usage log
  to tune.
- **This is not investment advice.** The bot produces structured
  suggestions from public posts. You are responsible for every trade
  decision and its consequences.

## Disabling / pausing

- Stop the cron: **Actions → scrape → ⋯ → Disable workflow**.
- Mute notifications: mute the Telegram bot chat.
- Quiet a single author: remove them from `SEED_TARGETS` in `src/config.py`.

## License

Personal use. Not for redistribution.
