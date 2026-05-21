import csv
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from crypto_bot.backtest.metrics import BacktestMetrics
from crypto_bot.config import load_config
from crypto_bot.optimization.engine import (
    WalkForwardRow,
    generate_parameter_grid,
    generate_strategy_parameter_grid,
    run_optimization,
    run_walk_forward,
    score_metrics,
    split_walk_forward,
)
from crypto_bot.optimization.export import export_optimization_reports


def test_parameter_grid_skips_fast_greater_than_or_equal_to_slow():
    combos = generate_parameter_grid(fast_windows=[5, 10, 20], slow_windows=[10, 15])

    assert combos == [(5, 10), (5, 15), (10, 15)]
    assert all(fast < slow for fast, slow in combos)


def test_donchian_parameter_grid_skips_exit_greater_than_or_equal_to_entry():
    combos = generate_strategy_parameter_grid(
        "donchian_breakout",
        entry_windows=[20, 40],
        exit_windows=[10, 20, 50],
        atr_windows=[14],
        atr_multipliers=[1.5, 2.0],
    )

    assert all(combo["exit_window"] < combo["entry_window"] for combo in combos)
    assert {"entry_window": 20, "exit_window": 10, "atr_window": 14, "atr_multiplier": 1.5} in combos
    assert {"entry_window": 20, "exit_window": 20, "atr_window": 14, "atr_multiplier": 1.5} not in combos


def test_rsi_parameter_grid_skips_invalid_thresholds():
    combos = generate_strategy_parameter_grid(
        "rsi_mean_reversion",
        rsi_windows=[7, 14],
        buy_thresholds=[30, 60],
        sell_thresholds=[50, 60],
    )

    assert all(combo["buy_threshold"] < combo["sell_threshold"] for combo in combos)
    assert {"rsi_window": 7, "buy_threshold": 30, "sell_threshold": 50} in combos
    assert {"rsi_window": 7, "buy_threshold": 60, "sell_threshold": 60} not in combos


def test_bollinger_parameter_grid_generates_window_std_pairs():
    combos = generate_strategy_parameter_grid(
        "bollinger_mean_reversion",
        windows=[20, 30],
        num_stds=[1.5, 2.0],
    )

    assert combos == [
        {"window": 20, "num_std": 1.5},
        {"window": 20, "num_std": 2.0},
        {"window": 30, "num_std": 1.5},
        {"window": 30, "num_std": 2.0},
    ]


def test_ema_parameter_grid_skips_pullback_greater_than_or_equal_to_trend():
    combos = generate_strategy_parameter_grid(
        "ema_pullback",
        trend_ema_windows=[20, 50],
        pullback_ema_windows=[10, 20, 60],
    )

    assert all(combo["pullback_ema_window"] < combo["trend_ema_window"] for combo in combos)
    assert {"trend_ema_window": 20, "pullback_ema_window": 10} in combos
    assert {"trend_ema_window": 20, "pullback_ema_window": 20} not in combos


def test_score_uses_conservative_drawdown_penalty():
    metrics = BacktestMetrics(
        total_return_pct=12,
        max_drawdown_pct=3,
        win_rate_pct=50,
        trade_count=5,
        total_fees=0,
        total_slippage=0,
    )

    scored = score_metrics(metrics, min_trades=3, max_drawdown_penalty=2.0)

    assert scored.score == 6
    assert scored.status == "ok"


def test_score_marks_insufficient_trades():
    metrics = BacktestMetrics(
        total_return_pct=12,
        max_drawdown_pct=3,
        win_rate_pct=50,
        trade_count=1,
        total_fees=0,
        total_slippage=0,
    )

    scored = score_metrics(metrics, min_trades=3, max_drawdown_penalty=2.0)

    assert scored.status == "insufficient_trades"
    assert scored.score < 6


def test_walk_forward_split_uses_train_and_test_ratios():
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=10, freq="1min", tz="UTC"),
            "open": range(10),
            "high": range(10),
            "low": range(10),
            "close": range(10),
            "volume": [1] * 10,
        }
    )

    windows = split_walk_forward(
        bars,
        train_ratio=0.6,
        test_ratio=0.4,
        min_train_bars=6,
        min_test_bars=4,
    )

    assert len(windows) == 1
    assert len(windows[0].train) == 6
    assert len(windows[0].test) == 4
    assert windows[0].window_id == 1


def test_rolling_walk_forward_generates_multiple_non_overlapping_windows_and_uses_step():
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=30, freq="1h", tz="UTC"),
            "open": range(30),
            "high": range(30),
            "low": range(30),
            "close": range(30),
            "volume": [1] * 30,
        }
    )

    windows = split_walk_forward(
        bars,
        train_ratio=0.6,
        test_ratio=0.4,
        min_train_bars=5,
        min_test_bars=3,
        mode="rolling",
        train_bars=10,
        test_bars=5,
        step_bars=5,
    )

    assert len(windows) == 4
    assert len(windows[0].train) == 10
    assert len(windows[0].test) == 5
    assert windows[0].train.iloc[-1]["timestamp"] < windows[0].test.iloc[0]["timestamp"]
    assert windows[1].train.iloc[0]["timestamp"] == bars.iloc[5]["timestamp"]
    assert windows[1].test.iloc[0]["timestamp"] == bars.iloc[15]["timestamp"]


def test_rolling_walk_forward_respects_minimum_bars():
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=12, freq="1h", tz="UTC"),
            "open": range(12),
            "high": range(12),
            "low": range(12),
            "close": range(12),
            "volume": [1] * 12,
        }
    )

    windows = split_walk_forward(
        bars,
        train_ratio=0.6,
        test_ratio=0.4,
        min_train_bars=20,
        min_test_bars=3,
        mode="rolling",
        train_bars=10,
        test_bars=5,
        step_bars=5,
    )

    assert windows == []


def test_walk_forward_summary_includes_stability_metrics(tmp_path):
    rows = [
        WalkForwardRow(
            window_id=1,
            train_start="2026-01-01T00:00:00+00:00",
            train_end="2026-01-02T00:00:00+00:00",
            test_start="2026-01-02T01:00:00+00:00",
            test_end="2026-01-03T00:00:00+00:00",
            selected_fast_window=5,
            selected_slow_window=30,
            selected_entry_window=None,
            selected_exit_window=None,
            selected_atr_window=None,
            selected_atr_multiplier=None,
            selected_rsi_window=None,
            selected_buy_threshold=None,
            selected_sell_threshold=None,
            selected_window=None,
            selected_num_std=None,
            selected_trend_ema_window=None,
            selected_pullback_ema_window=None,
            train_score=1,
            train_total_return_pct=2,
            train_max_drawdown_pct=1,
            test_total_return_pct=3,
            test_max_drawdown_pct=2,
            test_trade_count=10,
            test_profit_factor=1.5,
            test_profit_factor_note="calculated",
            filter_reject_count=1,
            filter_enabled=True,
        ),
        WalkForwardRow(
            window_id=2,
            train_start="2026-01-02T00:00:00+00:00",
            train_end="2026-01-03T00:00:00+00:00",
            test_start="2026-01-03T01:00:00+00:00",
            test_end="2026-01-04T00:00:00+00:00",
            selected_fast_window=5,
            selected_slow_window=30,
            selected_entry_window=None,
            selected_exit_window=None,
            selected_atr_window=None,
            selected_atr_multiplier=None,
            selected_rsi_window=None,
            selected_buy_threshold=None,
            selected_sell_threshold=None,
            selected_window=None,
            selected_num_std=None,
            selected_trend_ema_window=None,
            selected_pullback_ema_window=None,
            train_score=1,
            train_total_return_pct=2,
            train_max_drawdown_pct=1,
            test_total_return_pct=-1,
            test_max_drawdown_pct=4,
            test_trade_count=6,
            test_profit_factor=0.8,
            test_profit_factor_note="calculated",
            filter_reject_count=2,
            filter_enabled=True,
        ),
        WalkForwardRow(
            window_id=3,
            train_start="2026-01-03T00:00:00+00:00",
            train_end="2026-01-04T00:00:00+00:00",
            test_start="2026-01-04T01:00:00+00:00",
            test_end="2026-01-05T00:00:00+00:00",
            selected_fast_window=10,
            selected_slow_window=50,
            selected_entry_window=None,
            selected_exit_window=None,
            selected_atr_window=None,
            selected_atr_multiplier=None,
            selected_rsi_window=None,
            selected_buy_threshold=None,
            selected_sell_threshold=None,
            selected_window=None,
            selected_num_std=None,
            selected_trend_ema_window=None,
            selected_pullback_ema_window=None,
            train_score=1,
            train_total_return_pct=2,
            train_max_drawdown_pct=1,
            test_total_return_pct=5,
            test_max_drawdown_pct=3,
            test_trade_count=8,
            test_profit_factor=2.0,
            test_profit_factor_note="calculated",
            filter_reject_count=0,
            filter_enabled=True,
        ),
    ]

    paths = export_optimization_reports([], rows, tmp_path, timestamp="20260101T000000Z")
    summary = json.loads(Path(paths["walk_forward_summary"]).read_text(encoding="utf-8"))

    assert summary["window_count"] == 3
    assert summary["positive_test_window_count"] == 2
    assert summary["negative_test_window_count"] == 1
    assert summary["average_test_return_pct"] == 2.3333333333
    assert summary["median_test_return_pct"] == 3
    assert summary["average_test_max_drawdown_pct"] == 3
    assert summary["total_test_trade_count"] == 24
    assert summary["average_test_trade_count"] == 8
    assert summary["selected_parameter_distribution"] == {"5/30": 2, "10/50": 1}
    assert summary["worst_test_return_pct"] == -1
    assert summary["best_test_return_pct"] == 5
    assert summary["filter_reject_count"] == 3
    assert summary["filter_enabled"] is True


def test_optimize_cli_generates_csv_and_json(tmp_path):
    csv_path = tmp_path / "bars.csv"
    config_path = tmp_path / "config.yaml"
    export_dir = tmp_path / "reports"
    _write_bars(csv_path, periods=20)
    config_path.write_text(_config_text(csv_path), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "optimize",
            "--config",
            str(config_path),
            "--export-dir",
            str(export_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result_files = list(export_dir.glob("optimization_results_*.csv"))
    summary_files = list(export_dir.glob("optimization_summary_*.json"))
    assert result_files
    assert summary_files

    rows = list(csv.DictReader(result_files[0].open("r", encoding="utf-8-sig", newline="")))
    assert rows
    assert "\u8fc7\u6ee4\u62d2\u7edd\u6b21\u6570" in rows[0]
    assert "\u8fc7\u6ee4\u5668\u542f\u7528" in rows[0]
    assert {
        "快均线窗口",
        "慢均线窗口",
        "总收益率百分比",
        "年化收益率百分比",
        "最大回撤百分比",
        "胜率百分比",
        "盈亏因子",
        "盈亏因子说明",
        "交易次数",
        "拒单次数",
        "最终权益",
        "评分",
    }.issubset(rows[0].keys())

    summary = json.loads(summary_files[0].read_text(encoding="utf-8"))
    assert "best_parameters" in summary
    assert "result_count" in summary


def test_walk_forward_outputs_train_and_test_results(tmp_path):
    csv_path = tmp_path / "bars.csv"
    config_path = tmp_path / "config.yaml"
    export_dir = tmp_path / "reports"
    _write_bars(csv_path, periods=24)
    config_path.write_text(_config_text(csv_path), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "optimize",
            "--config",
            str(config_path),
            "--export-dir",
            str(export_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    wf_files = list(export_dir.glob("walk_forward_results_*.csv"))
    wf_summary_files = list(export_dir.glob("walk_forward_summary_*.json"))
    assert wf_files
    assert wf_summary_files

    rows = list(csv.DictReader(wf_files[0].open("r", encoding="utf-8-sig", newline="")))
    assert rows
    assert "\u8fc7\u6ee4\u62d2\u7edd\u6b21\u6570" in rows[0]
    assert "\u8fc7\u6ee4\u5668\u542f\u7528" in rows[0]
    assert {
        "窗口ID",
        "训练开始",
        "训练结束",
        "测试开始",
        "测试结束",
        "选择快均线窗口",
        "选择慢均线窗口",
        "训练评分",
        "训练总收益率百分比",
        "训练最大回撤百分比",
        "测试总收益率百分比",
        "测试最大回撤百分比",
        "测试交易次数",
        "测试盈亏因子",
        "测试盈亏因子说明",
    }.issubset(rows[0].keys())


def test_optimize_and_walk_forward_include_filter_fields(tmp_path):
    csv_path = tmp_path / "bars.csv"
    config_path = tmp_path / "config.yaml"
    _write_bars(csv_path, periods=24)
    config_path.write_text(
        _config_text(csv_path)
        + "\nregime_filter:\n  enabled: true\n  min_moving_average_slope: 100\n",
        encoding="utf-8",
    )
    config = load_config(config_path)

    optimization_rows = run_optimization(config)
    walk_forward_rows = run_walk_forward(config)

    assert optimization_rows
    assert optimization_rows[0].filter_enabled is True
    assert optimization_rows[0].filter_reject_count >= 0
    assert walk_forward_rows
    assert walk_forward_rows[0].filter_enabled is True
    assert walk_forward_rows[0].filter_reject_count >= 0


def test_optimize_and_walk_forward_can_run_donchian(tmp_path):
    csv_path = tmp_path / "bars.csv"
    config_path = tmp_path / "donchian.yaml"
    _write_bars(csv_path, periods=80)
    config_path.write_text(_donchian_config_text(csv_path), encoding="utf-8")
    config = load_config(config_path)

    optimization_rows = run_optimization(config)
    walk_forward_rows = run_walk_forward(config)

    assert optimization_rows
    assert optimization_rows[0].strategy_name == "donchian_breakout"
    assert optimization_rows[0].entry_window is not None
    assert walk_forward_rows
    assert walk_forward_rows[0].selected_entry_window is not None


def test_optimize_can_run_rsi_bollinger_and_ema(tmp_path):
    csv_path = tmp_path / "bars.csv"
    _write_bars(csv_path, periods=260)
    for strategy_name, config_text in {
        "rsi_mean_reversion": _rsi_config_text(csv_path),
        "bollinger_mean_reversion": _bollinger_config_text(csv_path),
        "ema_pullback": _ema_config_text(csv_path),
    }.items():
        config_path = tmp_path / f"{strategy_name}.yaml"
        config_path.write_text(config_text, encoding="utf-8")
        config = load_config(config_path)

        optimization_rows = run_optimization(config)
        walk_forward_rows = run_walk_forward(config)

        assert optimization_rows
        assert optimization_rows[0].strategy_name == strategy_name
        assert walk_forward_rows


def test_optimize_handles_no_trades_without_crashing(tmp_path):
    csv_path = tmp_path / "flat.csv"
    config_path = tmp_path / "config.yaml"
    export_dir = tmp_path / "reports"
    _write_bars(csv_path, periods=20, flat=True)
    config_path.write_text(_config_text(csv_path), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "optimize",
            "--config",
            str(config_path),
            "--export-dir",
            str(export_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    rows = list(csv.DictReader(next(export_dir.glob("optimization_results_*.csv")).open("r", encoding="utf-8-sig", newline="")))
    assert rows
    assert all(int(row["交易次数"]) == 0 for row in rows)
    assert all(row["盈亏因子"] == "null" for row in rows)
    assert all(row["盈亏因子说明"] == "no_trades" for row in rows)


def _write_bars(path: Path, periods: int, flat: bool = False) -> None:
    rows = []
    for index, timestamp in enumerate(pd.date_range("2026-01-01", periods=periods, freq="1min", tz="UTC")):
        close = 10.0 if flat else 10 + ((index % 8) * 0.2)
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def _config_text(csv_path: Path) -> str:
    return f"""
mode: paper
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
  timeframe: 1m
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
  min_trades: 3
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    train_ratio: 0.5
    test_ratio: 0.5
    min_train_bars: 8
    min_test_bars: 8
storage:
  url: "sqlite:///{csv_path.parent.as_posix()}/test.db"
"""


def _donchian_config_text(csv_path: Path) -> str:
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
  name: donchian_breakout
  entry_window: 6
  exit_window: 3
  atr_window: 4
  atr_multiplier: 2.0
risk:
  max_position_pct: 0.2
  min_bars_required: 8
execution:
  fee_rate: 0
  slippage_bps: 0
optimization:
  strategy_name: donchian_breakout
  entry_windows: [6, 10]
  exit_windows: [3, 6]
  atr_windows: [4]
  atr_multipliers: [1.5, 2.0]
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


def _rsi_config_text(csv_path: Path) -> str:
    return _research_config(
        csv_path,
        """
strategy:
  name: rsi_mean_reversion
  rsi_window: 7
  buy_threshold: 30
  sell_threshold: 55
optimization:
  strategy_name: rsi_mean_reversion
  rsi_windows: [7, 14]
  buy_thresholds: [30]
  sell_thresholds: [55, 60]
""",
    )


def _bollinger_config_text(csv_path: Path) -> str:
    return _research_config(
        csv_path,
        """
strategy:
  name: bollinger_mean_reversion
  window: 10
  num_std: 2.0
optimization:
  strategy_name: bollinger_mean_reversion
  windows: [10, 20]
  num_stds: [1.5, 2.0]
""",
    )


def _ema_config_text(csv_path: Path) -> str:
    return _research_config(
        csv_path,
        """
strategy:
  name: ema_pullback
  trend_ema_window: 50
  pullback_ema_window: 10
optimization:
  strategy_name: ema_pullback
  trend_ema_windows: [50, 100]
  pullback_ema_windows: [10, 20]
""",
    )


def _research_config(csv_path: Path, strategy_block: str) -> str:
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
{strategy_block}
  min_trades: 1
  score:
    max_drawdown_penalty: 2.0
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 80
    test_bars: 40
    step_bars: 40
    min_train_bars: 60
    min_test_bars: 20
risk:
  max_position_pct: 0.2
  min_bars_required: 4
execution:
  fee_rate: 0
  slippage_bps: 0
storage:
  url: "sqlite:///{csv_path.parent.as_posix()}/test.db"
"""
