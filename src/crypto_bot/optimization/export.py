from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from crypto_bot.optimization.engine import OptimizationRow, WalkForwardRow


def export_optimization_reports(
    optimization_rows: list[OptimizationRow],
    walk_forward_rows: list[WalkForwardRow],
    export_dir: str | Path,
    timestamp: str | None = None,
) -> dict[str, Path]:
    output_dir = Path(export_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    paths = {
        "optimization_results": output_dir / f"optimization_results_{stamp}.csv",
        "optimization_summary": output_dir / f"optimization_summary_{stamp}.json",
        "walk_forward_results": output_dir / f"walk_forward_results_{stamp}.csv",
        "walk_forward_summary": output_dir / f"walk_forward_summary_{stamp}.json",
    }
    _write_csv(paths["optimization_results"], _optimization_columns(), [asdict(row) for row in optimization_rows])
    _write_optimization_summary(paths["optimization_summary"], optimization_rows)
    _write_csv(paths["walk_forward_results"], _walk_forward_columns(), [asdict(row) for row in walk_forward_rows])
    _write_walk_forward_summary(paths["walk_forward_summary"], walk_forward_rows)
    return paths


def _write_csv(path: Path, columns: list[tuple[str, str]], rows: list[dict]) -> None:
    fieldnames = [label for _, label in columns]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({label: _csv_value(row.get(field)) for field, label in columns})


def _csv_value(value):
    return "null" if value is None else value


def _write_optimization_summary(path: Path, rows: list[OptimizationRow]) -> None:
    best = rows[0] if rows else None
    payload = {
        "result_count": len(rows),
        "best_parameters": None
        if best is None
        else {
            "strategy_name": best.strategy_name,
            "fast_window": best.fast_window,
            "slow_window": best.slow_window,
            "entry_window": best.entry_window,
            "exit_window": best.exit_window,
            "atr_window": best.atr_window,
            "atr_multiplier": best.atr_multiplier,
            "rsi_window": best.rsi_window,
            "buy_threshold": best.buy_threshold,
            "sell_threshold": best.sell_threshold,
            "window": best.window,
            "num_std": best.num_std,
            "trend_ema_window": best.trend_ema_window,
            "pullback_ema_window": best.pullback_ema_window,
            "score": best.score,
            "status": best.status,
        },
        "filter_enabled": any(row.filter_enabled for row in rows),
        "filter_reject_count": sum(row.filter_reject_count for row in rows),
        "note": "Parameter scans are historical simulations and may overfit.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_walk_forward_summary(path: Path, rows: list[WalkForwardRow]) -> None:
    returns = [row.test_total_return_pct for row in rows]
    drawdowns = [row.test_max_drawdown_pct for row in rows]
    trade_counts = [row.test_trade_count for row in rows]
    selected_parameters = Counter(_selected_parameter_label(row) for row in rows)
    payload = {
        "window_count": len(rows),
        "positive_test_window_count": sum(1 for value in returns if value > 0),
        "negative_test_window_count": sum(1 for value in returns if value < 0),
        "average_test_return_pct": _average(returns),
        "median_test_return_pct": None if not returns else round(float(median(returns)), 10),
        "average_test_max_drawdown_pct": _average(drawdowns),
        "total_test_trade_count": sum(trade_counts),
        "average_test_trade_count": _average(trade_counts),
        "selected_parameter_distribution": dict(selected_parameters),
        "worst_test_return_pct": None if not returns else min(returns),
        "best_test_return_pct": None if not returns else max(returns),
        "filter_reject_count": sum(row.filter_reject_count for row in rows),
        "filter_enabled": any(row.filter_enabled for row in rows),
        "note": "Prefer test results over train results when judging robustness.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _average(values: list[float] | list[int]) -> float | None:
    if not values:
        return None
    return round(float(sum(values) / len(values)), 10)


def _selected_parameter_label(row: WalkForwardRow) -> str:
    if row.selected_entry_window is not None and row.selected_exit_window is not None:
        return (
            f"entry={row.selected_entry_window}/exit={row.selected_exit_window}/"
            f"atr={row.selected_atr_window}/mult={row.selected_atr_multiplier}"
        )
    if row.selected_rsi_window is not None:
        return (
            f"rsi={row.selected_rsi_window}/buy={row.selected_buy_threshold}/"
            f"sell={row.selected_sell_threshold}"
        )
    if row.selected_window is not None:
        return f"window={row.selected_window}/std={row.selected_num_std}"
    if row.selected_trend_ema_window is not None:
        return f"trend={row.selected_trend_ema_window}/pullback={row.selected_pullback_ema_window}"
    return f"{row.selected_fast_window}/{row.selected_slow_window}"


def _optimization_columns() -> list[tuple[str, str]]:
    return [
        ("fast_window", "\u5feb\u5747\u7ebf\u7a97\u53e3"),
        ("slow_window", "\u6162\u5747\u7ebf\u7a97\u53e3"),
        ("strategy_name", "策略名称"),
        ("entry_window", "Donchian入场窗口"),
        ("exit_window", "Donchian出场窗口"),
        ("atr_window", "ATR窗口"),
        ("atr_multiplier", "ATR倍数"),
        ("rsi_window", "RSI窗口"),
        ("buy_threshold", "买入阈值"),
        ("sell_threshold", "卖出阈值"),
        ("window", "布林带窗口"),
        ("num_std", "标准差倍数"),
        ("trend_ema_window", "趋势EMA窗口"),
        ("pullback_ema_window", "回踩EMA窗口"),
        ("total_return_pct", "\u603b\u6536\u76ca\u7387\u767e\u5206\u6bd4"),
        ("annualized_return_pct", "\u5e74\u5316\u6536\u76ca\u7387\u767e\u5206\u6bd4"),
        ("max_drawdown_pct", "\u6700\u5927\u56de\u64a4\u767e\u5206\u6bd4"),
        ("win_rate_pct", "\u80dc\u7387\u767e\u5206\u6bd4"),
        ("profit_factor", "\u76c8\u4e8f\u56e0\u5b50"),
        ("profit_factor_note", "\u76c8\u4e8f\u56e0\u5b50\u8bf4\u660e"),
        ("trade_count", "\u4ea4\u6613\u6b21\u6570"),
        ("rejected_order_count", "\u62d2\u5355\u6b21\u6570"),
        ("filter_reject_count", "\u8fc7\u6ee4\u62d2\u7edd\u6b21\u6570"),
        ("filter_enabled", "\u8fc7\u6ee4\u5668\u542f\u7528"),
        ("final_equity", "\u6700\u7ec8\u6743\u76ca"),
        ("score", "\u8bc4\u5206"),
        ("status", "\u72b6\u6001"),
    ]


def _walk_forward_columns() -> list[tuple[str, str]]:
    return [
        ("window_id", "\u7a97\u53e3ID"),
        ("train_start", "\u8bad\u7ec3\u5f00\u59cb"),
        ("train_end", "\u8bad\u7ec3\u7ed3\u675f"),
        ("test_start", "\u6d4b\u8bd5\u5f00\u59cb"),
        ("test_end", "\u6d4b\u8bd5\u7ed3\u675f"),
        ("selected_fast_window", "\u9009\u62e9\u5feb\u5747\u7ebf\u7a97\u53e3"),
        ("selected_slow_window", "\u9009\u62e9\u6162\u5747\u7ebf\u7a97\u53e3"),
        ("selected_entry_window", "选择Donchian入场窗口"),
        ("selected_exit_window", "选择Donchian出场窗口"),
        ("selected_atr_window", "选择ATR窗口"),
        ("selected_atr_multiplier", "选择ATR倍数"),
        ("selected_rsi_window", "选择RSI窗口"),
        ("selected_buy_threshold", "选择买入阈值"),
        ("selected_sell_threshold", "选择卖出阈值"),
        ("selected_window", "选择布林带窗口"),
        ("selected_num_std", "选择标准差倍数"),
        ("selected_trend_ema_window", "选择趋势EMA窗口"),
        ("selected_pullback_ema_window", "选择回踩EMA窗口"),
        ("train_score", "\u8bad\u7ec3\u8bc4\u5206"),
        ("train_total_return_pct", "\u8bad\u7ec3\u603b\u6536\u76ca\u7387\u767e\u5206\u6bd4"),
        ("train_max_drawdown_pct", "\u8bad\u7ec3\u6700\u5927\u56de\u64a4\u767e\u5206\u6bd4"),
        ("test_total_return_pct", "\u6d4b\u8bd5\u603b\u6536\u76ca\u7387\u767e\u5206\u6bd4"),
        ("test_max_drawdown_pct", "\u6d4b\u8bd5\u6700\u5927\u56de\u64a4\u767e\u5206\u6bd4"),
        ("test_trade_count", "\u6d4b\u8bd5\u4ea4\u6613\u6b21\u6570"),
        ("test_profit_factor", "\u6d4b\u8bd5\u76c8\u4e8f\u56e0\u5b50"),
        ("test_profit_factor_note", "\u6d4b\u8bd5\u76c8\u4e8f\u56e0\u5b50\u8bf4\u660e"),
        ("filter_reject_count", "\u8fc7\u6ee4\u62d2\u7edd\u6b21\u6570"),
        ("filter_enabled", "\u8fc7\u6ee4\u5668\u542f\u7528"),
    ]
