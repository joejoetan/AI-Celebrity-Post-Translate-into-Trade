from datetime import datetime, timezone

from src.models import MarketSnapshot, TradeStrategy
from src.notifier import format_strategy


def _make():
    snap = MarketSnapshot(
        ticker="TSLA", spot=250.12, prev_close=248.0, day_pct=0.85,
        five_day_pct=2.1, headlines=["Tesla beats Q1 deliveries", "Musk teases robotaxi"],
    )
    return TradeStrategy(
        ticker="TSLA", side="long", conviction=0.72,
        entry_zone="249.50-250.50", stop="244.00 (-2.5%)",
        targets=["256 (+2.3%)", "262 (+4.7%)"],
        size_pct="2% of book", time_in_force="Day + 1",
        exit_rules=["trail stop to entry at T1", "flat if author deletes post"],
        execution_steps=["Limit buy TSLA 250", "OCO stop 244 / TP 256"],
        invalidation="break 245 on >2x avg volume",
        source_post_id="1", source_author="elonmusk",
        source_url="https://x.com/elonmusk/status/1",
        source_excerpt="Cybertruck production ramping 40% QoQ — wild numbers",
        market_snapshot=snap,
    )


def test_format_contains_key_fields():
    out = format_strategy(_make())
    assert "TSLA LONG" in out
    assert "Entry: 249.50-250.50" in out
    assert "Stop:" in out
    assert "Invalidation" in out
    assert "elonmusk" in out
    assert "https://x.com/elonmusk/status/1" in out


def test_format_escapes_markdown():
    s = _make()
    s.source_excerpt = "This *should* be escaped_ and `quoted`"
    out = format_strategy(s)
    assert "\\*should\\*" in out
    assert "escaped\\_" in out
    assert "\\`quoted\\`" in out
