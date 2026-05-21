import csv
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from crypto_bot.strategy_benchmark import _load_benchmark_config, sort_benchmark_rows


def test_benchmark_runs_multiple_strategy_configs_and_records_errors(tmp_path):
    csv_path = tmp_path / "bars.csv"
    _write_bars(csv_path, periods=80)
    ma_config = tmp_path / "ma.yaml"
    donchian_config = tmp_path / "donchian.yaml"
    rsi_config = tmp_path / "rsi.yaml"
    bollinger_config = tmp_path / "bollinger.yaml"
    ema_config = tmp_path / "ema.yaml"
    missing_config = tmp_path / "missing.yaml"
    benchmark_config = tmp_path / "benchmark.yaml"
    export_dir = tmp_path / "reports"
    ma_config.write_text(_ma_config(csv_path), encoding="utf-8")
    donchian_config.write_text(_donchian_config(csv_path), encoding="utf-8")
    rsi_config.write_text(_rsi_config(csv_path), encoding="utf-8")
    bollinger_config.write_text(_bollinger_config(csv_path), encoding="utf-8")
    ema_config.write_text(_ema_config(csv_path), encoding="utf-8")
    benchmark_config.write_text(
        f"""
benchmark:
  datasets:
    - name: tiny
      symbol: BTC/USDT
      timeframe: 1h
      csv_path: "{csv_path.as_posix()}"
  strategies:
    - name: ma
      config: "{ma_config.as_posix()}"
    - name: donchian
      config: "{donchian_config.as_posix()}"
    - name: rsi
      config: "{rsi_config.as_posix()}"
    - name: bollinger
      config: "{bollinger_config.as_posix()}"
    - name: ema
      config: "{ema_config.as_posix()}"
    - name: broken
      config: "{missing_config.as_posix()}"
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "benchmark-strategies",
            "--config",
            str(benchmark_config),
            "--export-dir",
            str(export_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    csv_files = list(export_dir.glob("strategy_benchmark_*.csv"))
    json_files = list(export_dir.glob("strategy_benchmark_*.json"))
    assert csv_files
    assert json_files

    rows = list(csv.DictReader(csv_files[0].open("r", encoding="utf-8", newline="")))
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert len(rows) == 6
    assert len(payload["rows"]) == 6
    assert {row["strategy_name"] for row in rows} == {"ma", "donchian", "rsi", "bollinger", "ema", "broken"}
    broken = next(row for row in payload["rows"] if row["strategy_name"] == "broken")
    assert broken["readiness_conclusion"] == "error"
    assert broken["error"]
    assert any(row["readiness_conclusion"] == "not_ready" for row in payload["rows"])
    assert "matrix" in payload
    assert (export_dir / payload["matrix"]["html_path"]).name.startswith("strategy_benchmark_dashboard_")


def test_multi_dataset_config_loads_datasets(tmp_path):
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(
        """
benchmark:
  datasets:
    - name: btc_1h
      symbol: BTC/USDT
      timeframe: 1h
      csv_path: data/BTC_USDT_1h.csv
    - name: eth_4h
      symbol: ETH/USDT
      timeframe: 4h
      csv_path: data/ETH_USDT_4h.csv
  strategies:
    - name: ma
      config: config.long.btc.1h.yaml
""",
        encoding="utf-8",
    )

    loaded = _load_benchmark_config(config_path)

    assert [dataset["name"] for dataset in loaded["datasets"]] == ["btc_1h", "eth_4h"]
    assert loaded["datasets"][1]["symbol"] == "ETH/USDT"


def test_benchmark_skips_invalid_dataset_and_writes_matrix(tmp_path):
    good_csv = tmp_path / "good.csv"
    bad_csv = tmp_path / "bad.csv"
    _write_bars(good_csv, periods=80)
    bad_csv.write_text("timestamp,open,high,low,close\n2026-01-01T00:00:00Z,1,1,1,1\n", encoding="utf-8")
    ma_config = tmp_path / "ma.yaml"
    missing_config = tmp_path / "missing.yaml"
    benchmark_config = tmp_path / "benchmark.yaml"
    export_dir = tmp_path / "reports"
    ma_config.write_text(_ma_config(good_csv), encoding="utf-8")
    benchmark_config.write_text(
        f"""
benchmark:
  datasets:
    - name: good
      symbol: BTC/USDT
      timeframe: 1h
      csv_path: "{good_csv.as_posix()}"
    - name: bad
      symbol: ETH/USDT
      timeframe: 1h
      csv_path: "{bad_csv.as_posix()}"
  strategies:
    - name: ma
      config: "{ma_config.as_posix()}"
    - name: broken
      config: "{missing_config.as_posix()}"
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "benchmark-strategies",
            "--config",
            str(benchmark_config),
            "--export-dir",
            str(export_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    benchmark_json = json.loads(next(export_dir.glob("strategy_benchmark_*.json")).read_text(encoding="utf-8"))
    rows = benchmark_json["rows"]
    assert len(rows) == 4
    bad_rows = [row for row in rows if row["dataset_name"] == "bad"]
    assert {row["readiness_conclusion"] for row in bad_rows} == {"error"}
    assert all("data_quality_invalid" in row["error"] for row in bad_rows)
    broken_good = next(row for row in rows if row["dataset_name"] == "good" and row["strategy_name"] == "broken")
    assert broken_good["readiness_conclusion"] == "error"
    assert broken_good["error"]
    assert rows[0]["symbol"] in {"BTC/USDT", "ETH/USDT"}
    assert rows[0]["timeframe"] == "1h"
    assert "bar_count" in rows[0]
    assert list(export_dir.glob("strategy_benchmark_matrix_*.csv"))
    assert list(export_dir.glob("strategy_benchmark_matrix_*.json"))
    assert list(export_dir.glob("strategy_benchmark_dashboard_*.html"))


def test_benchmark_sorting_prefers_readiness_passing_windows_profit_and_stability():
    rows = [
        _row("unstable", "not_ready", 4, 1.4, 2.0, 2.0, True),
        _row("ready", "paper_ready", 3, 1.1, 1.0, 5.0, True),
        _row("better", "not_ready", 5, 1.2, 1.0, 4.0, True),
        _row("stable", "not_ready", 5, 1.2, 1.0, 4.0, False),
    ]

    sorted_rows = sort_benchmark_rows(rows)

    assert [row["strategy_name"] for row in sorted_rows] == ["ready", "stable", "better", "unstable"]


def _row(
    name: str,
    conclusion: str,
    passing_windows: int,
    profit_factor: float,
    average_return: float,
    drawdown: float,
    switching: bool,
) -> dict:
    return {
        "strategy_name": name,
        "readiness_conclusion": conclusion,
        "passing_window_count": passing_windows,
        "average_test_profit_factor": profit_factor,
        "average_test_return_pct": average_return,
        "max_drawdown_pct": drawdown,
        "parameter_switching_detected": switching,
    }


def _write_bars(path: Path, periods: int) -> None:
    rows = []
    for index, timestamp in enumerate(pd.date_range("2026-01-01", periods=periods, freq="1h", tz="UTC")):
        close = 10 + ((index % 12) * 0.2)
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 100 + index,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def _common_config(csv_path: Path) -> str:
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
risk:
  max_position_pct: 0.2
  min_bars_required: 4
execution:
  fee_rate: 0
  slippage_bps: 0
optimization:
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 30
    test_bars: 20
    step_bars: 20
    min_train_bars: 20
    min_test_bars: 10
storage:
  url: "sqlite:///{csv_path.parent.as_posix()}/test.db"
"""


def _ma_config(csv_path: Path) -> str:
    return (
        _common_config(csv_path)
        + """
strategy:
  name: moving_average_cross
  fast_window: 2
  slow_window: 4
optimization:
  strategy_name: moving_average_cross
  fast_windows: [2]
  slow_windows: [4]
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 30
    test_bars: 20
    step_bars: 20
    min_train_bars: 20
    min_test_bars: 10
"""
    )


def _donchian_config(csv_path: Path) -> str:
    return (
        _common_config(csv_path)
        + """
strategy:
  name: donchian_breakout
  entry_window: 6
  exit_window: 3
  atr_window: 4
  atr_multiplier: 2.0
optimization:
  strategy_name: donchian_breakout
  entry_windows: [6]
  exit_windows: [3]
  atr_windows: [4]
  atr_multipliers: [2.0]
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 30
    test_bars: 20
    step_bars: 20
    min_train_bars: 20
    min_test_bars: 10
"""
    )


def _rsi_config(csv_path: Path) -> str:
    return (
        _common_config(csv_path)
        + """
strategy:
  name: rsi_mean_reversion
  rsi_window: 7
  buy_threshold: 30
  sell_threshold: 55
optimization:
  strategy_name: rsi_mean_reversion
  rsi_windows: [7]
  buy_thresholds: [30]
  sell_thresholds: [55]
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 30
    test_bars: 20
    step_bars: 20
    min_train_bars: 20
    min_test_bars: 10
"""
    )


def _bollinger_config(csv_path: Path) -> str:
    return (
        _common_config(csv_path)
        + """
strategy:
  name: bollinger_mean_reversion
  window: 10
  num_std: 2.0
optimization:
  strategy_name: bollinger_mean_reversion
  windows: [10]
  num_stds: [2.0]
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 30
    test_bars: 20
    step_bars: 20
    min_train_bars: 20
    min_test_bars: 10
"""
    )


def _ema_config(csv_path: Path) -> str:
    return (
        _common_config(csv_path)
        + """
strategy:
  name: ema_pullback
  trend_ema_window: 20
  pullback_ema_window: 5
optimization:
  strategy_name: ema_pullback
  trend_ema_windows: [20]
  pullback_ema_windows: [5]
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 30
    test_bars: 20
    step_bars: 20
    min_train_bars: 20
    min_test_bars: 10
"""
    )
