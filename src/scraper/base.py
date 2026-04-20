"""Common scraper interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from src.models import Post


class Scraper(ABC):
    platform: str

    @abstractmethod
    def fetch(self, handle: str, since: datetime) -> list[Post]:
        """Return posts authored by `handle` newer than `since`, newest first."""


def cutoff(lookback_hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
