import subprocess
import sys
from pathlib import Path

import pandas as pd

from crypto_bot.config import AppConfig, MarketDataConfig, RiskConfig, StorageConfig, StrategyConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.paper.runner import run_paper_session
from crypto_bot.storage.repositories import SQLiteStorage


class SequenceProvider:
    def __init__(self, frames):
        self.frames = list(frames)
        self.calls = 0

    def fetch_ohlcv(self, symbol, timeframe, limit):
        self.calls += 1
        if len(self.frames) == 1:
            return self.frames[0]
        return self.frames.pop(0)


class BrokenProvider:
    def fetch_ohlcv(self, symbol, timeframe, limit):
        raise MarketDataError("fetch_ohlcv_failed:network down")


def _bars(last_close=10.5, last_timestamp="2026-01-01 00:05:00+00:00"):
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-01 00:00:00+00:00",
                    "2026-01-01 00:01:00+00:00",
                    "2026-01-01 00:02:00+00:00",
                    "2026-01-01 00:03:00+00:00",
                    "2026-01-01 00:04:00+00:00",
                    last_timestamp,
                ],
                utc=True,
            ),
            "open": [10, 10, 10, 10, 10, last_close],
            "high": [10, 10, 10, 10, 10, last_close],
            "low": [10, 10, 10, 10, 10, last_close],
            "close": [10, 10, 10, 10, 10, last_close],
            "volume": [1, 1, 1, 1, 1, 1],
        }
    )


def _config(tmp_path):
    return AppConfig(
        mode="paper",
        symbols=["BTC/USDT"],
        live_trading=False,
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="binance",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=6,
        ),
        strategy=StrategyConfig(fast_window=2, slow_window=3),
        risk=RiskConfig(min_bars_required=4),
        storage=StorageConfig(url=f"sqlite:///{tmp_path / 'paper_runtime.db'}"),
    )


def test_paper_once_cli_runs_and_exits(tmp_path):
    config_path = tmp_path / "paper.yaml"
    db_path = tmp_path / "paper.db"
    csv_path = Path("data/sample_ohlcv.csv").resolve()
    config_path.write_text(
        f"""
mode: paper
live_trading: false
symbols: ["BTC/USDT"]
market_data:
  source: csv
  csv_path: "{csv_path.as_posix()}"
  exchange: binance
  symbols: ["BTC/USDT"]
  timeframe: 1m
  limit: 100
strategy:
  fast_window: 3
  slow_window: 5
risk:
  min_bars_required: 6
storage:
  url: "sqlite:///{db_path.as_posix()}"
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "crypto_bot.cli", "paper", "--config", str(config_path), "--once"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0
    assert "回测结果" in completed.stdout


def test_paper_max_iterations_records_each_iteration(tmp_path):
    config = _config(tmp_path)
    provider = SequenceProvider(
        [
            _bars(last_close=10.5, last_timestamp="2026-01-01 00:05:00+00:00"),
            _bars(last_close=10.7, last_timestamp="2026-01-01 00:06:00+00:00"),
        ]
    )

    run_paper_session(config, market_data_provider=provider, max_iterations=2, interval_seconds=0)

    storage = SQLiteStorage(config.storage.url)
    try:
        rows = storage.connection.execute(
            "SELECT iteration, source, exchange, timeframe FROM paper_state_snapshots ORDER BY iteration"
        ).fetchall()
    finally:
        storage.close()

    assert rows == [(1, "ccxt_public", "binance", "1m"), (2, "ccxt_public", "binance", "1m")]


def test_network_error_skips_iteration_and_records_snapshot(tmp_path):
    config = _config(tmp_path)

    result = run_paper_session(config, market_data_provider=BrokenProvider(), max_iterations=1)

    storage = SQLiteStorage(config.storage.url)
    try:
        row = storage.connection.execute(
            "SELECT skip_reason, error_message FROM paper_state_snapshots"
        ).fetchone()
    finally:
        storage.close()

    assert result.fills == []
    assert row[0] == "market_data_error"
    assert "network down" in row[1]


def test_duplicate_bar_does_not_create_duplicate_order(tmp_path):
    config = _config(tmp_path)
    repeated = _bars(last_close=10.5, last_timestamp="2026-01-01 00:05:00+00:00")

    result = run_paper_session(
        config,
        market_data_provider=SequenceProvider([repeated, repeated]),
        max_iterations=2,
        interval_seconds=0,
    )

    storage = SQLiteStorage(config.storage.url)
    try:
        rows = storage.connection.execute(
            "SELECT iteration, skip_reason FROM paper_state_snapshots ORDER BY iteration"
        ).fetchall()
    finally:
        storage.close()

    assert len(result.fills) == 1
    assert rows[0][1] is None
    assert rows[1] == (2, "duplicate_bar")


def test_sqlite_snapshot_contains_run_id_and_iteration(tmp_path):
    config = _config(tmp_path)

    run_paper_session(config, market_data_provider=SequenceProvider([_bars()]), max_iterations=1)

    storage = SQLiteStorage(config.storage.url)
    try:
        row = storage.connection.execute(
            "SELECT run_id, iteration, symbol, close, signal, risk_decision, order_status FROM paper_state_snapshots"
        ).fetchone()
    finally:
        storage.close()

    assert row[0]
    assert row[1] == 1
    assert row[2] == "BTC/USDT"
    assert row[3] == 10.5
    assert row[4] == "buy"
    assert row[5] == "approved"
    assert row[6] == "filled"
