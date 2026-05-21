from __future__ import annotations

from datetime import date

from crypto_bot.config import AppConfig
from crypto_bot.storage.repositories import SQLiteStorage


def create_daily_summary(config: AppConfig, day: date) -> dict[str, float | int | str]:
    storage = SQLiteStorage(config.storage.url)
    try:
        return storage.create_daily_summary(day)
    finally:
        storage.close()
