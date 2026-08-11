from __future__ import annotations

import csv
import html
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.backtest.export import export_backtest_reports
from crypto_bot.config import AppConfig, MarketDataConfig, load_config
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.market.csv_data import load_ohlcv_csv
from crypto_bot.market.data_quality import DataQualityReport, validate_ohlcv_csv
from crypto_bot.market.dataset_registry import DatasetRegistry, audit_dataset, load_dataset_registry
from crypto_bot.optimization.engine import run_optimization, run_walk_forward
from crypto_bot.optimization.export import export_optimization_reports
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter, RegimeFilterSettings
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.factory import create_strategy
from crypto_bot.strategy_readiness import evaluate_strategy_readiness
from crypto_bot.walk_forward_diagnostics import diagnose_walk_forward


BENCHMARK_COLUMNS = [
    "strategy_name",
    "dataset_name",
    "dataset_id",
    "raw_sha256",
    "canonical_sha256",
    "symbol",
    "timeframe",
    "bar_count",
    "data_start",
    "data_end",
    "total_return_pct",
    "max_drawdown_pct",
    "trade_count",
    "best_parameters",
    "walk_forward_window_count",
    "positive_test_window_count",
    "negative_test_window_count",
    "average_test_return_pct",
    "median_test_return_pct",
    "worst_test_return_pct",
    "best_test_return_pct",
    "total_test_trade_count",
    "average_test_profit_factor",
    "passing_window_count",
    "readiness_conclusion",
    "readiness_issues",
    "stability_rating",
    "top_20pct_profit_contribution_pct",
    "parameter_switching_detected",
    "error",
]


def run_strategy_benchmark(config_path: str | Path, export_dir: str | Path) -> dict[str, Any]:
    config = _load_benchmark_config(config_path)
    output_dir = Path(export_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict[str, Any]] = []
    for dataset in config["datasets"]:
        data_quality_path = output_dir / f"data_quality_benchmark_{_slug(str(dataset.get('name') or 'dataset'))}_{stamp}.json"
        data_quality = validate_ohlcv_csv(
            dataset["csv_path"],
            str(dataset.get("timeframe") or ""),
            data_quality_path,
        )
        if not data_quality.valid:
            rows.extend(
                _dataset_error_row(dataset, strategy, data_quality)
                for strategy in config["strategies"]
            )
            continue
        if dataset.get("_registry_valid") is False:
            issues = ",".join(str(issue) for issue in dataset.get("_registry_issues", [])) or "unknown"
            rows.extend(
                _dataset_registry_error_row(dataset, strategy, data_quality, issues)
                for strategy in config["strategies"]
            )
            continue
        for strategy in config["strategies"]:
            rows.append(_run_one(dataset, strategy, output_dir, stamp, data_quality, data_quality_path))
    rows = sort_benchmark_rows(rows)
    csv_path = output_dir / f"strategy_benchmark_{stamp}.csv"
    json_path = output_dir / f"strategy_benchmark_{stamp}.json"
    matrix_paths = _write_matrix_reports(rows, output_dir, stamp)
    dashboard_path = _write_dashboard(rows, matrix_paths["matrix"], output_dir, stamp)
    _write_csv(csv_path, rows)
    payload = {
        "generated_at": stamp,
        "dataset_registry_path": config.get("dataset_registry_path"),
        "rows": rows,
        "matrix": {
            "csv_path": str(matrix_paths["csv_path"]),
            "json_path": str(matrix_paths["json_path"]),
            "html_path": str(dashboard_path),
            **matrix_paths["matrix"],
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "matrix_csv_path": str(matrix_paths["csv_path"]),
        "matrix_json_path": str(matrix_paths["json_path"]),
        "dashboard_path": str(dashboard_path),
        "rows": rows,
    }


def format_benchmark_result(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"strategy_benchmark_csv: {result['csv_path']}",
            f"strategy_benchmark_json: {result['json_path']}",
            f"strategy_benchmark_matrix_csv: {result['matrix_csv_path']}",
            f"strategy_benchmark_matrix_json: {result['matrix_json_path']}",
            f"strategy_benchmark_dashboard_html: {result['dashboard_path']}",
            f"strategy_benchmark_row_count: {len(result['rows'])}",
        ]
    )


def sort_benchmark_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_sort_key)


def _sort_key(row: dict[str, Any]) -> tuple:
    return (
        _readiness_rank(row.get("readiness_conclusion")),
        -_number(row.get("passing_window_count")),
        -_number(row.get("average_test_profit_factor")),
        -_number(row.get("average_test_return_pct")),
        _number(row.get("max_drawdown_pct"), default=10**9),
        1 if row.get("parameter_switching_detected") else 0,
    )


def _readiness_rank(conclusion: Any) -> int:
    return {"paper_ready": 0, "review_required": 1, "not_ready": 2, "error": 3}.get(str(conclusion), 4)


def _number(value: Any, default: float = 0.0) -> float:
    if value in {None, "", "null"}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _run_one(
    dataset: dict[str, Any],
    strategy: dict[str, Any],
    output_dir: Path,
    stamp: str,
    data_quality: DataQualityReport,
    data_quality_path: Path,
) -> dict[str, Any]:
    strategy_name = str(strategy.get("name") or "")
    dataset_name = str(dataset.get("name") or "")
    prefix = f"benchmark_{_slug(dataset_name)}_{_slug(strategy_name)}_{stamp}"
    base_row = _empty_row(strategy_name, dataset, data_quality)
    try:
        app_config = _config_for_dataset(load_config(strategy["config"]), dataset)
        symbol = str(dataset.get("symbol") or app_config.symbols[0])
        bars = load_ohlcv_csv(dataset["csv_path"])
        backtest_result = _run_backtest(app_config, bars, symbol)
        backtest_paths = export_backtest_reports(backtest_result, output_dir, timestamp=prefix)
        optimization_rows = run_optimization(app_config, bars=bars)
        walk_forward_rows = run_walk_forward(app_config, bars=bars)
        optimization_paths = export_optimization_reports(
            optimization_rows,
            walk_forward_rows,
            output_dir,
            timestamp=prefix,
        )
        readiness_path = output_dir / f"strategy_readiness_{prefix}.json"
        readiness = evaluate_strategy_readiness(
            backtest_summary_path=backtest_paths["summary"],
            optimization_summary_path=optimization_paths["optimization_summary"],
            walk_forward_summary_path=optimization_paths["walk_forward_summary"],
            walk_forward_results_path=optimization_paths["walk_forward_results"],
            data_quality_path=data_quality_path,
            max_drawdown_pct=app_config.risk.max_drawdown_pct,
            export_path=readiness_path,
        )
        diagnosis_path = output_dir / f"walk_forward_diagnosis_{prefix}.json"
        diagnosis = diagnose_walk_forward(
            optimization_paths["walk_forward_results"],
            export_path=diagnosis_path,
        )
        backtest_summary = _load_json(backtest_paths["summary"])
        optimization_summary = _load_json(optimization_paths["optimization_summary"])
        walk_forward_summary = _load_json(optimization_paths["walk_forward_summary"])
        return {
            **base_row,
            "total_return_pct": backtest_summary.get("total_return_pct"),
            "max_drawdown_pct": backtest_summary.get("max_drawdown_pct"),
            "trade_count": backtest_summary.get("trade_count"),
            "best_parameters": json.dumps(optimization_summary.get("best_parameters"), ensure_ascii=False),
            "walk_forward_window_count": walk_forward_summary.get("window_count"),
            "positive_test_window_count": walk_forward_summary.get("positive_test_window_count"),
            "negative_test_window_count": walk_forward_summary.get("negative_test_window_count"),
            "average_test_return_pct": walk_forward_summary.get("average_test_return_pct"),
            "median_test_return_pct": walk_forward_summary.get("median_test_return_pct"),
            "worst_test_return_pct": walk_forward_summary.get("worst_test_return_pct"),
            "best_test_return_pct": walk_forward_summary.get("best_test_return_pct"),
            "total_test_trade_count": walk_forward_summary.get("total_test_trade_count"),
            "average_test_profit_factor": readiness.average_test_profit_factor,
            "passing_window_count": readiness.passing_window_count,
            "readiness_conclusion": readiness.conclusion,
            "readiness_issues": ",".join(readiness.issues),
            "stability_rating": diagnosis.stability_rating,
            "top_20pct_profit_contribution_pct": diagnosis.percentage_of_profit_from_top_20pct_windows,
            "parameter_switching_detected": diagnosis.parameter_switching_detected,
            "error": "",
        }
    except Exception as exc:
        return {**base_row, "readiness_conclusion": "error", "error": str(exc)}


def _empty_row(strategy_name: str, dataset: dict[str, Any], data_quality: DataQualityReport | None = None) -> dict[str, Any]:
    dataset_name = str(dataset.get("name") or "")
    return {column: "" for column in BENCHMARK_COLUMNS} | {
        "strategy_name": strategy_name,
        "dataset_name": dataset_name,
        "dataset_id": str(dataset.get("dataset_id") or ""),
        "raw_sha256": str(dataset.get("raw_sha256") or ""),
        "canonical_sha256": str(dataset.get("canonical_sha256") or ""),
        "symbol": str(dataset.get("symbol") or ""),
        "timeframe": str(dataset.get("timeframe") or ""),
        "bar_count": data_quality.bar_count if data_quality else "",
        "data_start": data_quality.start_time if data_quality else "",
        "data_end": data_quality.end_time if data_quality else "",
        "readiness_conclusion": "error",
    }


def _dataset_error_row(
    dataset: dict[str, Any],
    strategy: dict[str, Any],
    data_quality: DataQualityReport,
) -> dict[str, Any]:
    issues = ",".join(data_quality.issues) or "unknown"
    return {
        **_empty_row(str(strategy.get("name") or ""), dataset, data_quality),
        "readiness_conclusion": "error",
        "error": f"data_quality_invalid:{issues}",
    }


def _dataset_registry_error_row(
    dataset: dict[str, Any],
    strategy: dict[str, Any],
    data_quality: DataQualityReport,
    issues: str,
) -> dict[str, Any]:
    return {
        **_empty_row(str(strategy.get("name") or ""), dataset, data_quality),
        "readiness_conclusion": "error",
        "error": f"dataset_registry_invalid:{issues}",
    }


def _config_for_dataset(config: AppConfig, dataset: dict[str, Any]) -> AppConfig:
    symbol = str(dataset.get("symbol") or config.symbols[0])
    timeframe = str(dataset.get("timeframe") or config.timeframe)
    csv_path = str(dataset["csv_path"])
    return replace(
        config,
        symbols=[symbol],
        timeframe=timeframe,
        market_data=MarketDataConfig(
            source="csv",
            csv_path=csv_path,
            csv_files={symbol: csv_path},
            exchange=config.market_data.exchange,
            symbols=[symbol],
            timeframe=timeframe,
            since=config.market_data.since,
            until=config.market_data.until,
            limit=config.market_data.limit,
        ),
    )


def _run_backtest(config: AppConfig, bars, symbol: str):
    engine = BacktestEngine(
        strategy=_strategy_from_config(config),
        risk_manager=RiskManager(RiskSettings(**config.risk.__dict__)),
        execution_engine=PaperExecutionEngine(config.execution.fee_rate, config.execution.slippage_bps),
        account=Account(config.initial_cash),
        regime_filter=RegimeFilter(RegimeFilterSettings(**config.regime_filter.__dict__)),
    )
    return engine.run(symbol, bars)


def _strategy_from_config(config: AppConfig):
    return create_strategy(config.strategy)


def _load_benchmark_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    benchmark = raw.get("benchmark") or {}
    datasets = benchmark.get("datasets") or []
    strategies = benchmark.get("strategies") or []
    if not datasets:
        raise ValueError("benchmark.datasets must contain at least one dataset")
    if not strategies:
        raise ValueError("benchmark.strategies must contain at least one strategy")
    registry_value = benchmark.get("dataset_registry")
    if registry_value is None:
        return {"datasets": datasets, "strategies": strategies, "dataset_registry_path": None}
    if not isinstance(registry_value, str) or not registry_value.strip():
        raise ValueError("benchmark.dataset_registry must be a non-empty path")
    registry_path = Path(registry_value)
    if not registry_path.is_absolute():
        registry_path = config_path.resolve().parent / registry_path
    registry = load_dataset_registry(registry_path)
    resolved_datasets = [_resolve_registry_dataset(reference, registry) for reference in datasets]
    return {
        "datasets": resolved_datasets,
        "strategies": strategies,
        "dataset_registry_path": str(registry.registry_path),
    }


def _resolve_registry_dataset(reference: object, registry: DatasetRegistry) -> dict[str, Any]:
    if not isinstance(reference, dict):
        raise ValueError("benchmark dataset references must be mappings")
    dataset_id = reference.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("benchmark datasets must declare dataset_id when dataset_registry is configured")
    entry = registry.get(dataset_id.strip())
    for field_name, declared_value in (
        ("symbol", entry.symbol),
        ("timeframe", entry.timeframe),
        ("csv_path", entry.declared_path),
    ):
        if field_name in reference and str(reference[field_name]) != declared_value:
            raise ValueError(f"benchmark dataset {dataset_id} conflicts with registry field {field_name}")
    audit = audit_dataset(entry)
    registry_issues = list(audit.observed.issues)
    registry_issues.extend(
        f"expected_{item['field']}_mismatch"
        for item in audit.declared_vs_observed["mismatches"]
    )
    if audit.canonical_sha256 is None:
        registry_issues.append("canonical_hash_unavailable")
    return {
        "name": str(reference.get("name") or entry.dataset_id),
        "dataset_id": entry.dataset_id,
        "symbol": entry.symbol,
        "timeframe": entry.timeframe,
        "csv_path": str(entry.resolved_path),
        "raw_sha256": audit.raw_sha256,
        "canonical_sha256": audit.canonical_sha256,
        "_registry_valid": audit.valid,
        "_registry_issues": registry_issues,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BENCHMARK_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_matrix_reports(rows: list[dict[str, Any]], output_dir: Path, stamp: str) -> dict[str, Any]:
    matrix = _build_matrix(rows)
    csv_path = output_dir / f"strategy_benchmark_matrix_{stamp}.csv"
    json_path = output_dir / f"strategy_benchmark_matrix_{stamp}.json"
    fieldnames = [
        "strategy_name",
        "paper_ready_count",
        "average_passing_window_count",
        "average_test_return_pct",
        "average_test_profit_factor",
        "dataset_count",
        "error_count",
        "single_dataset_effective",
        "readiness_by_dataset",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in matrix["strategy_summaries"]:
            writer.writerow(
                {
                    **{key: row.get(key, "") for key in fieldnames},
                    "readiness_by_dataset": json.dumps(row["readiness_by_dataset"], ensure_ascii=False),
                }
            )
    # Keep the source rows alongside the summaries.  The matrix artifact is
    # intentionally self-contained, and this also prevents consumers that
    # discover ``strategy_benchmark_*.json`` files from mistaking the matrix
    # artifact for an incomplete benchmark report.
    dashboard_name = f"strategy_benchmark_dashboard_{stamp}.html"
    matrix_json_payload = {**matrix, "rows": rows, "html_path": dashboard_name}
    matrix_json_payload["matrix"] = {**matrix, "html_path": dashboard_name}
    json_path.write_text(json.dumps(matrix_json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv_path": csv_path, "json_path": json_path, "matrix": matrix}


def _build_matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_names = sorted({str(row.get("strategy_name") or "") for row in rows})
    dataset_names = sorted({str(row.get("dataset_name") or "") for row in rows})
    strategy_summaries = [_strategy_summary(strategy_name, rows) for strategy_name in strategy_names]
    dataset_summaries = [_dataset_summary(dataset_name, rows) for dataset_name in dataset_names]
    return {
        "strategy_summaries": strategy_summaries,
        "dataset_summaries": dataset_summaries,
        "readiness_matrix": {
            strategy_name: {
                dataset_name: _readiness_for(strategy_name, dataset_name, rows)
                for dataset_name in dataset_names
            }
            for strategy_name in strategy_names
        },
        "single_dataset_effective_strategies": [
            row["strategy_name"] for row in strategy_summaries if row["single_dataset_effective"]
        ],
    }


def _strategy_summary(strategy_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_rows = [row for row in rows if row.get("strategy_name") == strategy_name]
    non_error_rows = [row for row in strategy_rows if row.get("readiness_conclusion") != "error"]
    paper_ready_count = sum(1 for row in strategy_rows if row.get("readiness_conclusion") == "paper_ready")
    positive_dataset_count = sum(1 for row in non_error_rows if _number(row.get("average_test_return_pct")) > 0)
    readiness_by_dataset = {
        str(row.get("dataset_name") or ""): row.get("readiness_conclusion")
        for row in strategy_rows
    }
    return {
        "strategy_name": strategy_name,
        "paper_ready_count": paper_ready_count,
        "average_passing_window_count": _mean(row.get("passing_window_count") for row in non_error_rows),
        "average_test_return_pct": _mean(row.get("average_test_return_pct") for row in non_error_rows),
        "average_test_profit_factor": _mean(row.get("average_test_profit_factor") for row in non_error_rows),
        "dataset_count": len(strategy_rows),
        "error_count": sum(1 for row in strategy_rows if row.get("readiness_conclusion") == "error"),
        "single_dataset_effective": paper_ready_count == 1 or positive_dataset_count == 1,
        "readiness_by_dataset": readiness_by_dataset,
    }


def _dataset_summary(dataset_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    dataset_rows = [row for row in rows if row.get("dataset_name") == dataset_name]
    valid_rows = [row for row in dataset_rows if row.get("readiness_conclusion") != "error"]
    best = sort_benchmark_rows(valid_rows)[0] if valid_rows else None
    return {
        "dataset_name": dataset_name,
        "dataset_id": dataset_rows[0].get("dataset_id") if dataset_rows else "",
        "raw_sha256": dataset_rows[0].get("raw_sha256") if dataset_rows else "",
        "canonical_sha256": dataset_rows[0].get("canonical_sha256") if dataset_rows else "",
        "symbol": dataset_rows[0].get("symbol") if dataset_rows else "",
        "timeframe": dataset_rows[0].get("timeframe") if dataset_rows else "",
        "bar_count": dataset_rows[0].get("bar_count") if dataset_rows else "",
        "data_start": dataset_rows[0].get("data_start") if dataset_rows else "",
        "data_end": dataset_rows[0].get("data_end") if dataset_rows else "",
        "best_strategy": best.get("strategy_name") if best else None,
        "best_strategy_readiness": best.get("readiness_conclusion") if best else None,
        "error_count": sum(1 for row in dataset_rows if row.get("readiness_conclusion") == "error"),
    }


def _readiness_for(strategy_name: str, dataset_name: str, rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        if row.get("strategy_name") == strategy_name and row.get("dataset_name") == dataset_name:
            return row.get("readiness_conclusion")
    return None


def _mean(values) -> float | None:
    numbers = [_number(value, default=float("nan")) for value in values]
    numbers = [value for value in numbers if value == value]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 10)


def _write_dashboard(rows: list[dict[str, Any]], matrix: dict[str, Any], output_dir: Path, stamp: str) -> Path:
    path = output_dir / f"strategy_benchmark_dashboard_{stamp}.html"
    cards = "\n".join(_dashboard_card(row) for row in matrix["strategy_summaries"])
    table_rows = "\n".join(_dashboard_table_row(row) for row in rows)
    dataset_options = "\n".join(
        f"<span>{html.escape(str(item['dataset_name']))}: {html.escape(str(item['best_strategy']))}</span>"
        for item in matrix["dataset_summaries"]
    )
    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Strategy Benchmark Dashboard {html.escape(stamp)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ink: #152033;
      --muted: #607086;
      --line: #dce3ee;
      --accent: #0f766e;
      --warn: #b45309;
      --bad: #b91c1c;
      --good: #047857;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    .sub {{ color: var(--muted); font-size: 14px; }}
    main {{ padding: 24px 32px 40px; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin-bottom: 22px; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .card h2 {{ margin: 0 0 12px; font-size: 16px; }}
    .metric {{ display: grid; gap: 6px; color: var(--muted); font-size: 13px; }}
    .metric strong {{ color: var(--ink); font-size: 22px; }}
    .dataset-strip {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 22px; }}
    .dataset-strip span {{ border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 7px 10px; font-size: 13px; color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; font-size: 13px; white-space: nowrap; }}
    th {{ background: #eef3f8; color: #39475b; font-weight: 700; position: sticky; top: 0; }}
    tr:last-child td {{ border-bottom: 0; }}
    .scroll {{ overflow-x: auto; border-radius: 8px; }}
    .pill {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 8px; font-weight: 700; font-size: 12px; }}
    .paper_ready {{ background: #d1fae5; color: var(--good); }}
    .review_required {{ background: #fef3c7; color: var(--warn); }}
    .not_ready, .error {{ background: #fee2e2; color: var(--bad); }}
  </style>
</head>
<body>
  <header>
    <h1>策略 Benchmark 仪表盘</h1>
    <div class="sub">生成时间 {html.escape(stamp)}。离线研究报告，不代表收益保证，不连接 paper/live 或任何 private API。</div>
  </header>
  <main>
    <section class="stats">{cards}</section>
    <section class="dataset-strip">{dataset_options}</section>
    <section class="scroll">
      <table>
        <thead>
          <tr>
            <th>策略</th><th>数据集</th><th>标的</th><th>周期</th><th>Bar</th><th>Readiness</th>
            <th>通过窗口</th><th>平均收益%</th><th>平均PF</th><th>最大回撤%</th><th>问题</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return path


def _dashboard_card(row: dict[str, Any]) -> str:
    return (
        '<article class="card">'
        f"<h2>{html.escape(str(row['strategy_name']))}</h2>"
        '<div class="metric">'
        f"<span>paper_ready 数据集</span><strong>{html.escape(str(row['paper_ready_count']))}</strong>"
        f"<span>平均通过窗口: {html.escape(str(row['average_passing_window_count']))}</span>"
        f"<span>平均收益: {html.escape(str(row['average_test_return_pct']))}</span>"
        f"<span>平均 PF: {html.escape(str(row['average_test_profit_factor']))}</span>"
        "</div></article>"
    )


def _dashboard_table_row(row: dict[str, Any]) -> str:
    conclusion = str(row.get("readiness_conclusion") or "error")
    return (
        "<tr>"
        f"<td>{html.escape(str(row.get('strategy_name') or ''))}</td>"
        f"<td>{html.escape(str(row.get('dataset_name') or ''))}</td>"
        f"<td>{html.escape(str(row.get('symbol') or ''))}</td>"
        f"<td>{html.escape(str(row.get('timeframe') or ''))}</td>"
        f"<td>{html.escape(str(row.get('bar_count') or ''))}</td>"
        f'<td><span class="pill {html.escape(conclusion)}">{html.escape(conclusion)}</span></td>'
        f"<td>{html.escape(str(row.get('passing_window_count') or ''))}</td>"
        f"<td>{html.escape(str(row.get('average_test_return_pct') or ''))}</td>"
        f"<td>{html.escape(str(row.get('average_test_profit_factor') or ''))}</td>"
        f"<td>{html.escape(str(row.get('max_drawdown_pct') or ''))}</td>"
        f"<td>{html.escape(str(row.get('readiness_issues') or row.get('error') or ''))}</td>"
        "</tr>"
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_").lower() or "item"
