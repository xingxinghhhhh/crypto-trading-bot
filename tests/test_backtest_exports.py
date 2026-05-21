import csv
import json
from pathlib import Path

import pandas as pd

from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.backtest.export import export_backtest_reports
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.portfolio.account import Account
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.moving_average_cross import MovingAverageCrossStrategy


def _bars():
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=7, freq="1min", tz="UTC"),
            "open": [10, 10, 10, 10.5, 10.6, 10.0, 9.8],
            "high": [10, 10, 10, 10.5, 10.6, 10.0, 9.8],
            "low": [10, 10, 10, 10.5, 10.6, 10.0, 9.8],
            "close": [10, 10, 10, 10.5, 10.6, 10.0, 9.8],
            "volume": [1, 1, 1, 1, 1, 1, 1],
        }
    )


def _run_result():
    engine = BacktestEngine(
        strategy=MovingAverageCrossStrategy(fast_window=2, slow_window=3),
        risk_manager=RiskManager(RiskSettings(max_position_pct=0.2, min_bars_required=4)),
        execution_engine=PaperExecutionEngine(fee_rate=0, slippage_bps=0),
        account=Account(initial_cash=1000),
    )
    return engine.run("BTC/USDT", _bars())


def test_backtest_export_writes_summary_json(tmp_path):
    result = _run_result()

    paths = export_backtest_reports(result, tmp_path, timestamp="20260101T000000Z")

    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    assert summary["initial_cash"] == 1000
    assert "final_equity" in summary
    assert "annualized_return_pct" in summary
    assert "risk_reject_reason_distribution" in summary
    assert "profit_factor_note" in summary
    assert "filter_reject_count" in summary
    assert "filter_reject_reason_distribution" in summary
    assert "trades_after_filter" in summary
    assert "filter_enabled" in summary
    assert "filter_config_snapshot" in summary


def test_backtest_export_writes_trades_csv(tmp_path):
    result = _run_result()

    paths = export_backtest_reports(result, tmp_path, timestamp="20260101T000000Z")

    rows = list(csv.DictReader(Path(paths["trades"]).open("r", encoding="utf-8-sig", newline="")))
    assert rows
    assert {
        "时间",
        "交易对",
        "方向",
        "数量",
        "价格",
        "手续费",
        "滑点成本",
        "已实现盈亏",
        "原因",
        "来源信号ID",
    }.issubset(rows[0].keys())


def test_backtest_export_writes_equity_curve_csv(tmp_path):
    result = _run_result()

    paths = export_backtest_reports(result, tmp_path, timestamp="20260101T000000Z")

    rows = list(csv.DictReader(Path(paths["equity_curve"]).open("r", encoding="utf-8-sig", newline="")))
    assert len(rows) == len(_bars())
    assert {
        "时间",
        "现金",
        "持仓数量",
        "持仓价值",
        "权益",
        "回撤百分比",
        "收盘价",
    }.issubset(rows[0].keys())


def test_backtest_export_writes_risk_events_csv(tmp_path):
    result = _run_result()

    paths = export_backtest_reports(result, tmp_path, timestamp="20260101T000000Z")

    rows = list(csv.DictReader(Path(paths["risk_events"]).open("r", encoding="utf-8-sig", newline="")))
    assert rows
    assert {
        "时间",
        "交易对",
        "已批准",
        "已拒绝",
        "原因",
        "调整后数量",
        "权益",
        "当前回撤百分比",
    }.issubset(rows[0].keys())
