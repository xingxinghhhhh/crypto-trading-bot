import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from crypto_bot.config import AppConfig, MarketDataConfig, RiskConfig, StorageConfig, StrategyConfig
from crypto_bot.errors import MarketDataError, SafetyError
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
        strategy=StrategyConfig(
            readiness="paper_ready",
            fast_window=2,
            slow_window=3,
        ),
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


def test_not_ready_strategy_cannot_start_long_running_paper(tmp_path):
    config = _config(tmp_path)
    config = AppConfig(
        **{
            **config.__dict__,
            "strategy": StrategyConfig(
                readiness="not_ready",
                fast_window=2,
                slow_window=3,
            ),
        }
    )

    with pytest.raises(SafetyError, match="long-running paper"):
        run_paper_session(
            config,
            market_data_provider=SequenceProvider([_bars()]),
            max_iterations=2,
        )


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


def test_paper_restart_restores_cash_position_and_peak_equity(tmp_path):
    config = _config(tmp_path)
    first_bar = _bars(last_close=10.5, last_timestamp="2026-01-01 00:05:00+00:00")

    first_result = run_paper_session(
        config,
        market_data_provider=SequenceProvider([first_bar]),
        max_iterations=1,
    )
    assert len(first_result.fills) == 1

    run_paper_session(
        config,
        market_data_provider=SequenceProvider([first_bar]),
        max_iterations=1,
    )

    storage = SQLiteStorage(config.storage.url)
    try:
        rows = storage.connection.execute(
            """
            SELECT cash, position_quantity, position_avg_price, peak_equity, skip_reason
            FROM paper_state_snapshots
            ORDER BY id
            """
        ).fetchall()
    finally:
        storage.close()

    assert len(rows) == 2
    assert rows[1][:4] == rows[0][:4]
    assert rows[1][4] == "duplicate_bar"


def test_persistent_kill_switch_blocks_market_fetch_until_manual_release(tmp_path):
    config = _config(tmp_path)
    storage = SQLiteStorage(config.storage.url)
    try:
        storage.set_kill_switch(True, "incident_test")
    finally:
        storage.close()

    provider = SequenceProvider([_bars()])
    blocked_result = run_paper_session(
        config,
        market_data_provider=provider,
        max_iterations=1,
    )

    assert provider.calls == 0
    assert blocked_result.fills == []

    storage = SQLiteStorage(config.storage.url)
    try:
        row = storage.connection.execute(
            "SELECT skip_reason, error_message FROM paper_state_snapshots ORDER BY id DESC"
        ).fetchone()
        storage.set_kill_switch(False, "manual_recovery")
    finally:
        storage.close()

    assert row == ("kill_switch_engaged", "incident_test")

    resumed_result = run_paper_session(
        config,
        market_data_provider=provider,
        max_iterations=1,
    )

    assert provider.calls == 1
    assert len(resumed_result.fills) == 1


def test_paper_iteration_rolls_back_all_audit_rows_when_checkpoint_fails(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)

    def fail_checkpoint(self, snapshot, *, commit=True):
        raise RuntimeError("checkpoint_write_failed")

    monkeypatch.setattr(
        SQLiteStorage,
        "record_paper_state_snapshot",
        fail_checkpoint,
    )

    with pytest.raises(RuntimeError, match="checkpoint_write_failed"):
        run_paper_session(
            config,
            market_data_provider=SequenceProvider([_bars()]),
            max_iterations=1,
        )

    storage = SQLiteStorage(config.storage.url)
    try:
        counts = {
            table: storage.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in [
                "signals",
                "risk_events",
                "orders",
                "fills",
                "balance_snapshots",
                "processed_bars",
            ]
        }
    finally:
        storage.close()

    assert set(counts.values()) == {0}

    storage = SQLiteStorage(config.storage.url)
    try:
        heartbeat = storage.latest_runtime_heartbeat("paper")
    finally:
        storage.close()

    assert heartbeat is not None
    assert heartbeat["status"] == "failed"


def test_daily_trade_limit_remains_effective_after_restart(tmp_path):
    config = _config(tmp_path)
    config = AppConfig(
        **{
            **config.__dict__,
            "risk": RiskConfig(
                min_bars_required=4,
                max_trades_per_day=1,
                stop_loss_pct=0.5,
                take_profit_pct=0.5,
                abnormal_move_pct=0.5,
            ),
        }
    )
    first = _bars(
        last_close=10.5,
        last_timestamp="2026-01-01 00:05:00+00:00",
    )
    second = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01 00:00:00+00:00",
                periods=7,
                freq="1min",
            ),
            "open": [10, 10, 10, 10, 11, 11, 9],
            "high": [10, 10, 10, 10, 11, 11, 9],
            "low": [10, 10, 10, 10, 11, 11, 9],
            "close": [10, 10, 10, 10, 11, 11, 9],
            "volume": [1, 1, 1, 1, 1, 1, 1],
        }
    )

    first_result = run_paper_session(
        config,
        market_data_provider=SequenceProvider([first]),
        max_iterations=1,
    )
    second_result = run_paper_session(
        config,
        market_data_provider=SequenceProvider([second]),
        max_iterations=1,
    )

    assert len(first_result.fills) == 1
    assert second_result.fills == []

    storage = SQLiteStorage(config.storage.url)
    try:
        latest = storage.connection.execute(
            """
            SELECT risk_decision, order_status, position_quantity
            FROM paper_state_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        storage.close()

    assert latest[0] == "max_trades_per_day"
    assert latest[1] == "risk_rejected"
    assert latest[2] > 0
