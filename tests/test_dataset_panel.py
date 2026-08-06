import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_panel import build_dataset_panel, load_dataset_panel_config
from crypto_bot.market.dataset_registry import write_json_atomically


def test_panel_build_is_exact_deterministic_and_order_independent(tmp_path):
    timestamps = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    entries = _write_three_datasets(tmp_path, timestamps, timestamps, timestamps)
    registry_path = _write_registry(tmp_path, entries)
    first_config = _write_panel_config(tmp_path / "first.yaml", ["sol_v1", "btc_v1", "eth_v1"])
    second_config = _write_panel_config(tmp_path / "second.yaml", ["eth_v1", "btc_v1", "sol_v1"])
    first_export = tmp_path / "first.json"
    second_export = tmp_path / "second.json"

    first = build_dataset_panel(registry_path, first_config, "three_1h_v1", export_path=first_export)
    second = build_dataset_panel(registry_path, second_config, "three_1h_v1", export_path=second_export)

    assert list(first.frame.columns) == [
        "timestamp",
        "BTC_USDT.open",
        "BTC_USDT.high",
        "BTC_USDT.low",
        "BTC_USDT.close",
        "BTC_USDT.volume",
        "ETH_USDT.open",
        "ETH_USDT.high",
        "ETH_USDT.low",
        "ETH_USDT.close",
        "ETH_USDT.volume",
        "SOL_USDT.open",
        "SOL_USDT.high",
        "SOL_USDT.low",
        "SOL_USDT.close",
        "SOL_USDT.volume",
    ]
    pd.testing.assert_frame_equal(first.frame, second.frame)
    assert first.report["panel_sha256"] == second.report["panel_sha256"]
    assert first_export.read_bytes() == second_export.read_bytes()
    assert first.report["dataset_ids"] == ["btc_v1", "eth_v1", "sol_v1"]
    assert first.report["alignment_summary"] == {
        "union_bar_count": 4,
        "intersection_bar_count": 4,
        "coverage_rate": 1.0,
        "common_first_timestamp": "2026-01-01T00:00:00+00:00",
        "common_last_timestamp": "2026-01-01T03:00:00+00:00",
    }
    assert first.report["timestamp_semantics"]["status"] == "unverified"
    assert first.report["readiness_changed"] is False
    assert first.report["automatic_factor_approval"] is False


def test_panel_reports_boundary_drops_separately(tmp_path):
    all_timestamps = pd.date_range("2026-01-01", periods=6, freq="1h", tz="UTC")
    entries = _write_three_datasets(
        tmp_path,
        all_timestamps,
        all_timestamps[1:],
        all_timestamps[1:-1],
    )
    registry_path = _write_registry(tmp_path, entries)
    config_path = _write_panel_config(tmp_path / "panels.yaml", ["btc_v1", "eth_v1", "sol_v1"])

    result = build_dataset_panel(registry_path, config_path, "three_1h_v1")

    alignment = result.report["alignment_summary"]
    assert alignment["union_bar_count"] == 6
    assert alignment["intersection_bar_count"] == 4
    assert alignment["coverage_rate"] == round(4 / 6, 12)
    summaries = {item["dataset_id"]: item for item in result.report["datasets"]}
    assert summaries["btc_v1"]["dropped_before_common_start"] == 1
    assert summaries["btc_v1"]["dropped_after_common_end"] == 1
    assert summaries["eth_v1"]["dropped_after_common_end"] == 1
    assert summaries["sol_v1"]["dropped_before_common_start"] == 0
    assert all(item["missing_inside_common_window"] == 0 for item in summaries.values())


def test_panel_hash_binds_source_history_outside_common_window(tmp_path):
    all_timestamps = pd.date_range("2026-01-01", periods=6, freq="1h", tz="UTC")
    entries = _write_three_datasets(
        tmp_path,
        all_timestamps,
        all_timestamps[1:-1],
        all_timestamps[1:-1],
    )
    registry_path = _write_registry(tmp_path, entries)
    config_path = _write_panel_config(tmp_path / "panels.yaml", ["btc_v1", "eth_v1", "sol_v1"])
    first = build_dataset_panel(registry_path, config_path, "three_1h_v1")
    btc_path = tmp_path / "btc.csv"
    changed = pd.read_csv(btc_path)
    changed.loc[0, ["open", "high", "low", "close"]] = [999.0, 1000.0, 998.0, 999.5]
    changed.to_csv(btc_path, index=False)

    second = build_dataset_panel(registry_path, config_path, "three_1h_v1")

    pd.testing.assert_frame_equal(first.frame, second.frame)
    assert first.report["panel_sha256"] != second.report["panel_sha256"]


def test_panel_rejects_source_gap_without_exporting_success_report(tmp_path):
    continuous = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    with_gap = continuous.delete(2)
    entries = _write_three_datasets(tmp_path, continuous, with_gap, continuous)
    registry_path = _write_registry(tmp_path, entries)
    config_path = _write_panel_config(tmp_path / "panels.yaml", ["btc_v1", "eth_v1", "sol_v1"])
    export_path = tmp_path / "must-not-exist.json"

    with pytest.raises(MarketDataError, match="dataset_panel_source_invalid:eth_v1:time_gap_detected"):
        build_dataset_panel(registry_path, config_path, "three_1h_v1", export_path=export_path)

    assert not export_path.exists()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("duplicate", "duplicate_timestamp"),
        ("malformed", "invalid_columns"),
    ],
)
def test_panel_rejects_other_source_quality_failures(tmp_path, mutation, expected):
    timestamps = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    entries = _write_three_datasets(tmp_path, timestamps, timestamps, timestamps)
    eth_path = tmp_path / "eth.csv"
    frame = pd.read_csv(eth_path)
    if mutation == "duplicate":
        frame = pd.concat([frame.iloc[:2], frame.iloc[[1]], frame.iloc[2:]], ignore_index=True)
    else:
        frame = frame.drop(columns=["volume"])
    frame.to_csv(eth_path, index=False)
    registry_path = _write_registry(tmp_path, entries)
    config_path = _write_panel_config(tmp_path / "panels.yaml", ["btc_v1", "eth_v1", "sol_v1"])

    with pytest.raises(MarketDataError, match=expected):
        build_dataset_panel(registry_path, config_path, "three_1h_v1")


def test_panel_rejects_registry_hash_drift(tmp_path):
    timestamps = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    entries = _write_three_datasets(tmp_path, timestamps, timestamps, timestamps)
    entries[0]["expected"] = {"canonical_sha256": "0" * 64}
    registry_path = _write_registry(tmp_path, entries)
    config_path = _write_panel_config(tmp_path / "panels.yaml", ["btc_v1", "eth_v1", "sol_v1"])

    with pytest.raises(MarketDataError, match="expected_canonical_sha256_mismatch"):
        build_dataset_panel(registry_path, config_path, "three_1h_v1")


@pytest.mark.parametrize(
    ("entry_changes", "expected"),
    [
        ({"eth_v1": {"timeframe": "4h"}}, "dataset_panel_timeframes_must_match"),
        ({"eth_v1": {"symbol": "BTC/USDT"}}, "dataset_panel_symbols_must_be_unique"),
    ],
)
def test_panel_rejects_incompatible_registry_entries(tmp_path, entry_changes, expected):
    timestamps = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    entries = _write_three_datasets(tmp_path, timestamps, timestamps, timestamps)
    for entry in entries:
        entry.update(entry_changes.get(entry["dataset_id"], {}))
    registry_path = _write_registry(tmp_path, entries)
    config_path = _write_panel_config(tmp_path / "panels.yaml", ["btc_v1", "eth_v1", "sol_v1"])

    with pytest.raises(MarketDataError, match=expected):
        build_dataset_panel(registry_path, config_path, "three_1h_v1")


def test_panel_rejects_unknown_dataset_and_no_common_timestamps(tmp_path):
    first = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    second = pd.date_range("2026-02-01", periods=3, freq="1h", tz="UTC")
    entries = _write_three_datasets(tmp_path, first, second, second)
    registry_path = _write_registry(tmp_path, entries)
    unknown_config = _write_panel_config(tmp_path / "unknown.yaml", ["btc_v1", "missing_v1"])
    disjoint_config = _write_panel_config(tmp_path / "disjoint.yaml", ["btc_v1", "eth_v1", "sol_v1"])

    with pytest.raises(ValueError, match="dataset_id not found"):
        build_dataset_panel(registry_path, unknown_config, "three_1h_v1")
    with pytest.raises(MarketDataError, match="dataset_panel_no_common_timestamps"):
        build_dataset_panel(registry_path, disjoint_config, "three_1h_v1")


@pytest.mark.parametrize(
    ("panels", "expected"),
    [
        ([{"panel_id": "one", "dataset_ids": ["btc_v1"], "alignment": "inner_exact"}], "at least two"),
        (
            [{"panel_id": "one", "dataset_ids": ["btc_v1", "btc_v1"], "alignment": "inner_exact"}],
            "must be unique",
        ),
        ([{"panel_id": "one", "dataset_ids": ["btc_v1", "eth_v1"], "alignment": "outer"}], "inner_exact"),
    ],
)
def test_panel_config_rejects_invalid_specs(tmp_path, panels, expected):
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "panels": panels}), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        load_dataset_panel_config(path)


def test_panel_audit_cli_is_byte_deterministic(tmp_path):
    timestamps = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    entries = _write_three_datasets(tmp_path, timestamps, timestamps, timestamps)
    registry_path = _write_registry(tmp_path, entries)
    config_path = _write_panel_config(tmp_path / "panels.yaml", ["btc_v1", "eth_v1", "sol_v1"])
    first_export = tmp_path / "cli-first.json"
    second_export = tmp_path / "cli-second.json"
    base_command = [
        sys.executable,
        "-m",
        "crypto_bot.cli",
        "dataset-panel-audit",
        "--registry",
        str(registry_path),
        "--panels-config",
        str(config_path),
        "--panel-id",
        "three_1h_v1",
    ]

    first = subprocess.run(
        [*base_command, "--export", str(first_export)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    second = subprocess.run(
        [*base_command, "--export", str(second_export)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert first.returncode == second.returncode == 0
    assert "intersection_bar_count: 4" in first.stdout
    assert "timestamp_semantics: unverified" in first.stdout
    assert first_export.read_bytes() == second_export.read_bytes()
    payload = json.loads(first_export.read_text(encoding="utf-8"))
    assert all(item["provenance"]["status"] == "unknown" for item in payload["datasets"])


def test_atomic_json_failure_preserves_existing_file_and_removes_temporary(tmp_path, monkeypatch):
    import crypto_bot.market.dataset_registry as registry_module

    target = tmp_path / "report.json"
    target.write_text("existing", encoding="utf-8")

    def fail_serialization(*args, **kwargs):
        raise TypeError("serialization failed")

    monkeypatch.setattr(registry_module.json, "dumps", fail_serialization)

    with pytest.raises(TypeError, match="serialization failed"):
        write_json_atomically(target, {"value": object()})

    assert target.read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".report.json.*.tmp"))


def _write_three_datasets(tmp_path, btc_timestamps, eth_timestamps, sol_timestamps) -> list[dict]:
    definitions = [
        ("btc_v1", "BTC/USDT", "btc.csv", btc_timestamps, 100.0),
        ("eth_v1", "ETH/USDT", "eth.csv", eth_timestamps, 200.0),
        ("sol_v1", "SOL/USDT", "sol.csv", sol_timestamps, 300.0),
    ]
    entries = []
    for dataset_id, symbol, filename, timestamps, base in definitions:
        _write_bars(tmp_path / filename, timestamps, base)
        entries.append(
            {
                "dataset_id": dataset_id,
                "symbol": symbol,
                "timeframe": "1h",
                "path": filename,
                "source": {"status": "unknown", "evidence": []},
                "quality": {"validator": "validate_ohlcv_csv"},
            }
        )
    return entries


def _write_bars(path: Path, timestamps, base: float) -> None:
    rows = []
    for index, timestamp in enumerate(timestamps):
        close = base + index
        rows.append(
            {
                "timestamp": pd.Timestamp(timestamp).isoformat(),
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close + 0.5,
                "volume": 1000 + index,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_registry(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "datasets": entries}, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _write_panel_config(path: Path, dataset_ids: list[str]) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "panels": [
                    {
                        "panel_id": "three_1h_v1",
                        "dataset_ids": dataset_ids,
                        "alignment": "inner_exact",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path
