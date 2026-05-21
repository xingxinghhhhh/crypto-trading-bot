from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from crypto_bot.backtest.engine import BacktestResult


def export_backtest_reports(
    result: BacktestResult,
    export_dir: str | Path,
    timestamp: str | None = None,
) -> dict[str, Path]:
    output_dir = Path(export_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    paths = {
        "summary": output_dir / f"backtest_summary_{stamp}.json",
        "trades": output_dir / f"trades_{stamp}.csv",
        "equity_curve": output_dir / f"equity_curve_{stamp}.csv",
        "risk_events": output_dir / f"risk_events_{stamp}.csv",
    }

    _write_summary(paths["summary"], result)
    _write_csv(paths["trades"], _trade_columns(), [asdict(record) for record in result.trades])
    _write_csv(paths["equity_curve"], _equity_columns(), [asdict(record) for record in result.equity_points])
    _write_csv(paths["risk_events"], _risk_columns(), [asdict(record) for record in result.risk_events])
    return paths


def _write_summary(path: Path, result: BacktestResult) -> None:
    metrics = result.metrics
    payload = {
        "initial_cash": metrics.initial_cash,
        "final_equity": metrics.final_equity,
        "total_return_pct": metrics.total_return_pct,
        "annualized_return_pct": metrics.annualized_return_pct,
        "annualized_return_note": metrics.annualized_return_note,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "win_rate_pct": metrics.win_rate_pct,
        "profit_factor": metrics.profit_factor,
        "profit_factor_note": metrics.profit_factor_note,
        "trade_count": metrics.trade_count,
        "winning_trades": metrics.winning_trades,
        "losing_trades": metrics.losing_trades,
        "average_win": metrics.average_win,
        "average_loss": metrics.average_loss,
        "largest_win": metrics.largest_win,
        "largest_loss": metrics.largest_loss,
        "total_fees": metrics.total_fees,
        "total_slippage_cost": metrics.total_slippage_cost,
        "exposure_time_pct": metrics.exposure_time_pct,
        "rejected_order_count": metrics.rejected_order_count,
        "risk_reject_reason_distribution": metrics.risk_reject_reason_distribution,
        "filter_reject_count": metrics.filter_reject_count,
        "filter_reject_reason_distribution": metrics.filter_reject_reason_distribution,
        "trades_after_filter": metrics.trades_after_filter,
        "filter_enabled": metrics.filter_enabled,
        "filter_config_snapshot": metrics.filter_config_snapshot,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_csv(path: Path, columns: list[tuple[str, str]], rows: list[dict]) -> None:
    fieldnames = [label for _, label in columns]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({label: row.get(field) for field, label in columns})


def _trade_columns() -> list[tuple[str, str]]:
    return [
        ("timestamp", "时间"),
        ("symbol", "交易对"),
        ("side", "方向"),
        ("qty", "数量"),
        ("price", "价格"),
        ("fee", "手续费"),
        ("slippage_cost", "滑点成本"),
        ("realized_pnl", "已实现盈亏"),
        ("reason", "原因"),
        ("source_signal_id", "来源信号ID"),
    ]


def _equity_columns() -> list[tuple[str, str]]:
    return [
        ("timestamp", "时间"),
        ("cash", "现金"),
        ("position_qty", "持仓数量"),
        ("position_value", "持仓价值"),
        ("equity", "权益"),
        ("drawdown_pct", "回撤百分比"),
        ("close_price", "收盘价"),
    ]


def _risk_columns() -> list[tuple[str, str]]:
    return [
        ("timestamp", "时间"),
        ("symbol", "交易对"),
        ("approved", "已批准"),
        ("rejected", "已拒绝"),
        ("reason", "原因"),
        ("adjusted_size", "调整后数量"),
        ("equity", "权益"),
        ("current_drawdown_pct", "当前回撤百分比"),
    ]
