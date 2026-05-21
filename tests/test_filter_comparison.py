import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


def test_compare_filters_generates_json(tmp_path):
    csv_path = tmp_path / "bars.csv"
    base_config = tmp_path / "base.yaml"
    filtered_config = tmp_path / "filtered.yaml"
    export_dir = tmp_path / "reports"
    _write_bars(csv_path, periods=40)
    base_config.write_text(_config_text(csv_path, filtered=False), encoding="utf-8")
    filtered_config.write_text(_config_text(csv_path, filtered=True), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "compare-filters",
            "--base-config",
            str(base_config),
            "--filtered-config",
            str(filtered_config),
            "--export-dir",
            str(export_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    comparison_files = list(export_dir.glob("filter_comparison_*.json"))
    assert comparison_files
    payload = json.loads(comparison_files[0].read_text(encoding="utf-8"))
    assert "base_total_return_pct" in payload
    assert "filtered_total_return_pct" in payload
    assert "base_readiness_conclusion" in payload
    assert "filtered_readiness_conclusion" in payload


def _write_bars(path: Path, periods: int) -> None:
    rows = []
    for index, timestamp in enumerate(pd.date_range("2026-01-01", periods=periods, freq="1h", tz="UTC")):
        close = 10 + ((index % 8) * 0.2)
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100 + index,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def _config_text(csv_path: Path, filtered: bool) -> str:
    filter_text = (
        "regime_filter:\n  enabled: true\n  min_moving_average_slope: 100\n"
        if filtered
        else "regime_filter:\n  enabled: false\n"
    )
    return f"""
mode: backtest
live_trading: false
symbols: ["BTC/USDT"]
initial_cash: 10000
market_data:
  source: csv
  csv_path: "{csv_path.as_posix()}"
  csv_files:
    "BTC/USDT": "{csv_path.as_posix()}"
  exchange: binance
  symbols: ["BTC/USDT"]
  timeframe: 1h
  limit: 100
strategy:
  name: moving_average_cross
  fast_window: 2
  slow_window: 4
risk:
  max_position_pct: 0.2
  min_bars_required: 4
execution:
  fee_rate: 0
  slippage_bps: 0
optimization:
  fast_windows: [2, 3]
  slow_windows: [4, 5]
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 12
    test_bars: 8
    step_bars: 8
    min_train_bars: 8
    min_test_bars: 4
storage:
  url: "sqlite:///{csv_path.parent.as_posix()}/test.db"
{filter_text}
"""
