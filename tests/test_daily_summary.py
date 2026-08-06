from __future__ import annotations

from datetime import date
import sqlite3

from crypto_bot.config import AppConfig, StorageConfig
from crypto_bot.paper.summary import create_daily_summary


def test_create_daily_summary_persists_empty_day(tmp_path) -> None:
    database_path = tmp_path / "paper.db"
    config = AppConfig(storage=StorageConfig(url=f"sqlite:///{database_path}"))

    summary = create_daily_summary(config, date(2026, 7, 21))

    assert summary == {
        "day": "2026-07-21",
        "trade_count": 0,
        "total_fees": 0.0,
        "starting_equity": 0.0,
        "ending_equity": 0.0,
        "pnl": 0.0,
    }
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT trade_count, total_fees, pnl FROM daily_summaries WHERE day = ?",
            ("2026-07-21",),
        ).fetchone() == (0, 0.0, 0.0)
