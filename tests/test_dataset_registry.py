import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from crypto_bot.market.dataset_registry import audit_dataset_registry, load_dataset_registry
from crypto_bot.strategy_benchmark import run_strategy_benchmark


def test_registry_audit_is_deterministic_and_canonicalizes_equivalent_csvs(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01 00:00:00,10,12,9,11,100\n"
        "2026-01-01 01:00:00,11,13,10,12,120\n",
        encoding="utf-8",
    )
    second.write_text(
        "timestamp,open,high,low,close,volume\r\n"
        "2026-01-01T00:00:00Z,10.0,12.0,9.0,11.0,100.0\r\n"
        "2026-01-01T01:00:00Z,11.0,13.0,10.0,12.0,120.0\r\n",
        encoding="utf-8",
    )
    registry_path = _write_registry(
        tmp_path,
        [
            _entry("first_v1", "first.csv"),
            _entry("second_v1", "second.csv"),
        ],
    )
    first_export = tmp_path / "first-audit.json"
    second_export = tmp_path / "second-audit.json"

    first_run = audit_dataset_registry(registry_path, first_export)
    second_run = audit_dataset_registry(registry_path, second_export)

    assert first_run == second_run
    assert first_export.read_bytes() == second_export.read_bytes()
    assert first_run["valid"] is True
    first_audit, second_audit = first_run["datasets"]
    assert first_audit["hashes"]["raw_sha256"] != second_audit["hashes"]["raw_sha256"]
    assert first_audit["hashes"]["canonical_sha256"] == second_audit["hashes"]["canonical_sha256"]


def test_registry_audit_reuses_quality_validation_for_duplicates_and_gaps(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T00:00:00Z,10,12,9,11,100\n"
        "2026-01-01T02:00:00Z,11,13,10,12,120\n"
        "2026-01-01T02:00:00Z,11,13,10,12,120\n",
        encoding="utf-8",
    )
    registry_path = _write_registry(tmp_path, [_entry("bad_v1", "bad.csv")])

    report = audit_dataset_registry(registry_path)

    dataset = report["datasets"][0]
    assert report["valid"] is False
    assert dataset["observed"]["duplicate_timestamp_count"] == 1
    assert dataset["observed"]["time_gap_count"] == 1
    assert dataset["observed"]["missing_bar_count"] == 1
    assert dataset["hashes"]["canonical_sha256"]


def test_registry_audit_reports_malformed_csv_without_canonical_hash(tmp_path):
    (tmp_path / "malformed.csv").write_text("timestamp,close\n2026-01-01T00:00:00Z,10\n", encoding="utf-8")
    registry_path = _write_registry(tmp_path, [_entry("malformed_v1", "malformed.csv")])

    report = audit_dataset_registry(registry_path)

    dataset = report["datasets"][0]
    assert dataset["valid"] is False
    assert dataset["observed"]["issues"] == ["invalid_columns"]
    assert dataset["hashes"]["raw_sha256"]
    assert dataset["hashes"]["canonical_sha256"] is None


@pytest.mark.parametrize(
    "entry,match",
    [
        ({"path": "bars.csv"}, "dataset_id"),
        (
            {
                "dataset_id": "escape_v1",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "path": "../outside.csv",
                "source": {"status": "unknown", "evidence": []},
                "quality": {"validator": "validate_ohlcv_csv"},
            },
            "escapes registry root",
        ),
        (
            {
                "dataset_id": "missing_evidence_v1",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "path": "bars.csv",
                "source": {"status": "partial", "evidence": ["missing-source.zip"]},
                "quality": {"validator": "validate_ohlcv_csv"},
            },
            "source evidence not found",
        ),
    ],
)
def test_registry_rejects_invalid_schema_and_path_escape(tmp_path, entry, match):
    registry_path = _write_registry(tmp_path, [entry])

    with pytest.raises(ValueError, match=match):
        load_dataset_registry(registry_path)


def test_dataset_registry_audit_cli_exports_json(tmp_path):
    csv_path = tmp_path / "bars.csv"
    _write_bars(csv_path, periods=3)
    registry_path = _write_registry(tmp_path, [_entry("bars_v1", "bars.csv")])
    export_path = tmp_path / "audit.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "dataset-registry-audit",
            "--registry",
            str(registry_path),
            "--export",
            str(export_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "dataset_registry_valid: true" in completed.stdout
    assert json.loads(export_path.read_text(encoding="utf-8"))["datasets"][0]["dataset_id"] == "bars_v1"


def test_registry_rejects_duplicate_ids_and_unknown_lookup(tmp_path):
    registry_path = _write_registry(
        tmp_path,
        [_entry("duplicate_v1", "first.csv"), _entry("duplicate_v1", "second.csv")],
    )
    with pytest.raises(ValueError, match="must be unique"):
        load_dataset_registry(registry_path)

    registry_path = _write_registry(tmp_path, [_entry("known_v1", "bars.csv")])
    registry = load_dataset_registry(registry_path)
    with pytest.raises(ValueError, match="dataset_id not found"):
        registry.get("unknown_v1")


def test_registry_detects_expected_metadata_drift(tmp_path):
    csv_path = tmp_path / "bars.csv"
    _write_bars(csv_path, periods=3)
    entry = _entry("drift_v1", "bars.csv")
    entry["expected"] = {"bar_count": 99}
    registry_path = _write_registry(tmp_path, [entry])

    report = audit_dataset_registry(registry_path)

    comparison = report["datasets"][0]["declared_vs_observed"]
    assert report["valid"] is False
    assert comparison["matches"] is False
    assert comparison["mismatches"] == [{"field": "bar_count", "declared": 99, "observed": 3}]


@pytest.mark.parametrize(
    "registry_text,match",
    [
        ("- not-a-mapping\n", "must be a mapping"),
        ("schema_version: 2\ndatasets: []\n", "schema_version"),
        ("schema_version: 1\ndatasets: []\n", "at least one entry"),
        ("schema_version: [\n", "YAML is invalid"),
    ],
)
def test_registry_rejects_invalid_document_shapes(tmp_path, registry_text, match):
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(registry_text, encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        load_dataset_registry(registry_path)


def test_registry_rejects_invalid_expected_hash(tmp_path):
    entry = _entry("bad_hash_v1", "bars.csv")
    entry["expected"] = {"raw_sha256": "not-a-sha256"}
    registry_path = _write_registry(tmp_path, [entry])

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        load_dataset_registry(registry_path)


def test_benchmark_resolves_registry_and_records_matching_hashes(tmp_path):
    csv_path = tmp_path / "bars.csv"
    _write_bars(csv_path, periods=80)
    registry_path = _write_registry(tmp_path, [_entry("btc_test_v1", "bars.csv")])
    audit = audit_dataset_registry(registry_path)["datasets"][0]
    strategy_path = tmp_path / "strategy.yaml"
    strategy_path.write_text(_strategy_config(csv_path), encoding="utf-8")
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        "benchmark:\n"
        "  dataset_registry: registry.yaml\n"
        "  datasets:\n"
        "    - dataset_id: btc_test_v1\n"
        "      name: tiny\n"
        "  strategies:\n"
        "    - name: ma\n"
        f'      config: "{strategy_path.as_posix()}"\n',
        encoding="utf-8",
    )

    result = run_strategy_benchmark(benchmark_path, tmp_path / "reports")

    row = result["rows"][0]
    assert row["dataset_id"] == "btc_test_v1"
    assert row["raw_sha256"] == audit["hashes"]["raw_sha256"]
    assert row["canonical_sha256"] == audit["hashes"]["canonical_sha256"]
    payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    dataset_summary = payload["matrix"]["dataset_summaries"][0]
    assert payload["dataset_registry_path"] == str(registry_path.resolve())
    assert dataset_summary["dataset_id"] == "btc_test_v1"
    assert dataset_summary["raw_sha256"] == audit["hashes"]["raw_sha256"]
    assert dataset_summary["canonical_sha256"] == audit["hashes"]["canonical_sha256"]


def test_benchmark_rejects_registry_metadata_drift_before_strategy_run(tmp_path):
    csv_path = tmp_path / "bars.csv"
    _write_bars(csv_path, periods=3)
    entry = _entry("drift_v1", "bars.csv")
    entry["expected"] = {"bar_count": 999}
    _write_registry(tmp_path, [entry])
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        "benchmark:\n"
        "  dataset_registry: registry.yaml\n"
        "  datasets:\n"
        "    - dataset_id: drift_v1\n"
        "  strategies:\n"
        "    - name: must-not-run\n"
        "      config: missing-strategy.yaml\n",
        encoding="utf-8",
    )

    result = run_strategy_benchmark(benchmark_path, tmp_path / "reports")

    row = result["rows"][0]
    assert row["readiness_conclusion"] == "error"
    assert row["error"] == "dataset_registry_invalid:expected_bar_count_mismatch"


def _entry(dataset_id: str, path: str) -> dict:
    return {
        "dataset_id": dataset_id,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "path": path,
        "source": {"status": "unknown", "evidence": []},
        "quality": {"validator": "validate_ohlcv_csv"},
    }


def _write_registry(tmp_path: Path, datasets: list[dict]) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "datasets": datasets}, sort_keys=False),
        encoding="utf-8",
    )
    return path


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


def _strategy_config(csv_path: Path) -> str:
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
risk:
  max_position_pct: 0.2
  min_bars_required: 4
execution:
  fee_rate: 0
  slippage_bps: 0
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
storage:
  url: "sqlite:///{csv_path.parent.as_posix()}/test.db"
"""
