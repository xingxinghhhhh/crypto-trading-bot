import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

import crypto_bot.cli as cli_module
import crypto_bot.market.okx_direct_six_asset_migration as module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_registry import canonical_ohlcv_sha256


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.okx-direct-six-1h-migration.example.yaml"


def test_frozen_config_contract_and_asset_membership_are_exact():
    config = module.load_okx_direct_six_asset_migration_config(CONFIG)
    assert config["anchor_inst_ids"] == list(module.ANCHOR_INST_IDS)
    assert config["existing_direct_dataset_ids"] == list(module.EXISTING_DATASET_IDS)
    assert config["expected_bar_count"] == 40191
    assert config["expected_page_count"] == 134
    assert config["expected_panel"]["coverage_rate"] == 1.0
    assert module._load_contract(ROOT, config)["sha256"] == (
        "9c568eef6fb882aaa4825150d684c9b7a9ad8b4068bceb821c82d9069a462b05"
    )
    with pytest.raises(ValueError, match="frozen config"):
        module._validate_config(config | {"anchor_inst_ids": ["BTC-USDT"]})


def test_config_and_low_level_identity_helpers_fail_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        module.load_okx_direct_six_asset_migration_config(tmp_path / "missing.yaml")

    non_mapping = tmp_path / "list.yaml"
    non_mapping.write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        module.load_okx_direct_six_asset_migration_config(non_mapping)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("[unterminated", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML is invalid"):
        module.load_okx_direct_six_asset_migration_config(invalid)

    with pytest.raises(MarketDataError, match="invalid_payload"):
        module._mapping([], "payload")
    with pytest.raises(MarketDataError, match="invalid_digest"):
        module._sha_value("wrong", "digest")
    with pytest.raises(ValueError, match="UTC"):
        module._timestamp_ms("2026-01-01T00:00:00+08:00")
    with pytest.raises(FileNotFoundError):
        module._repo_file(tmp_path, "missing")

    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError, match="dependency_missing"):
        module._locate_dependency(report, Path("missing"), "0" * 64)
    with pytest.raises(MarketDataError, match="path_escape"):
        module._sibling(report, "../escape")
    with pytest.raises(MarketDataError, match="artifact_missing"):
        module._sibling(report, "missing.json")

    changed = module.load_okx_direct_six_asset_migration_config(CONFIG) | {"limit": 299}
    with pytest.raises(MarketDataError, match="contract_mismatch"):
        module._load_contract(ROOT, changed)


def test_capture_is_marker_last_deterministic_and_fully_replayable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    promotion = _promotion(repo)
    config = _small_config()
    monkeypatch.setattr(module, "validate_okx_frozen_universe_1h_promotion", lambda _path: promotion)
    monkeypatch.setattr(module, "load_okx_direct_six_asset_migration_config", lambda _path: config)
    monkeypatch.setattr(module, "_validate_config", lambda _value: config)
    monkeypatch.setattr(module, "_validate_source_promotion", lambda *_args: None)
    monkeypatch.setattr(module, "_load_contract", lambda *_args: {"filename": "contract", "sha256": "c" * 64})
    monkeypatch.setattr(module, "download_okx_public_history", _small_history)

    first = module.capture_okx_direct_anchor_1h_history(
        "promotion.json", "config.yaml", repo / "reports" / "capture-a", request_interval_seconds=0
    )
    second = module.capture_okx_direct_anchor_1h_history(
        "promotion.json", "config.yaml", repo / "reports" / "capture-b", request_interval_seconds=0
    )

    assert first.report == second.report
    assert [item["inst_id"] for item in first.report["identity"]["histories"]] == list(
        module.ANCHOR_INST_IDS
    )
    assert all(item["bar_count"] == 3 for item in first.report["identity"]["histories"])
    assert {Path(path).name for path in first.export_paths.values()} == {
        Path(path).name for path in second.export_paths.values()
    }
    assert "private_api_used: false" in module.format_okx_direct_anchor_1h_capture(first)

    monkeypatch.setattr(module, "_locate_dependency", lambda *_args: promotion.report_path)
    validated = module.validate_okx_direct_anchor_1h_capture(first.export_paths["report"])
    assert validated.report == first.report
    assert len(validated.histories) == 3


def test_capture_shape_output_escape_and_collision_fail_closed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    promotion = _promotion(repo)
    config = _small_config()
    monkeypatch.setattr(module, "validate_okx_frozen_universe_1h_promotion", lambda _path: promotion)
    monkeypatch.setattr(module, "load_okx_direct_six_asset_migration_config", lambda _path: config)
    monkeypatch.setattr(module, "_validate_source_promotion", lambda *_args: None)
    monkeypatch.setattr(module, "_load_contract", lambda *_args: {})
    monkeypatch.setattr(module, "download_okx_public_history", lambda *_args, **_kwargs: (b"", [], []))
    with pytest.raises(ValueError, match="inside reports"):
        module.capture_okx_direct_anchor_1h_history(
            "promotion", "config", repo / "outside", request_interval_seconds=0
        )
    with pytest.raises(MarketDataError, match="history_shape"):
        module.capture_okx_direct_anchor_1h_history(
            "promotion", "config", repo / "reports" / "capture", request_interval_seconds=0
        )
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"old")
    with pytest.raises(MarketDataError, match="content_addressed_collision"):
        module._commit_bytes(artifact, b"new")


def test_migration_builds_only_direct_six_asset_panel_and_is_deterministic(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    timestamps = pd.date_range("2022-01-01", periods=3, freq="1h", tz="UTC")
    promotion = _promotion(repo, timestamps)
    config = _small_config()
    capture = _validated_capture(repo, promotion, config, timestamps)
    monkeypatch.setattr(module, "validate_okx_direct_anchor_1h_capture", lambda _path: capture)
    monkeypatch.setattr(module, "validate_okx_frozen_universe_1h_promotion", lambda _path: promotion)
    monkeypatch.setattr(module, "load_okx_direct_six_asset_migration_config", lambda _path: config)
    monkeypatch.setattr(module, "_validate_config", lambda _value: config)
    monkeypatch.setattr(module, "_validate_source_promotion", lambda *_args: None)

    first = module.freeze_okx_direct_six_asset_1h_panel(
        capture.report_path,
        promotion.report_path,
        "config.yaml",
        repo / "reports" / "migration-a",
    )
    second = module.freeze_okx_direct_six_asset_1h_panel(
        capture.report_path,
        promotion.report_path,
        "config.yaml",
        repo / "reports" / "migration-b",
    )

    assert first.report == second.report
    identity = first.report["identity"]
    assert [item["dataset_id"] for item in identity["datasets"]] == list(module.ALL_DATASET_IDS)
    assert identity["timestamp_semantics"]["aggregate_status"] == "verified_open_time"
    assert identity["timestamp_semantics"]["timestamp_semantics_uniform"] is True
    assert identity["panel"]["alignment_summary"]["intersection_bar_count"] == 3
    registry_text = Path(first.export_paths["registry"]).read_text(encoding="utf-8")
    assert "btc_usdt_1h_v1" not in registry_text
    assert "eth_usdt_1h_v1" not in registry_text
    assert "sol_usdt_1h_v1" not in registry_text
    assert {Path(path).name for path in first.export_paths.values()} == {
        Path(path).name for path in second.export_paths.values()
    }
    assert "timestamp_semantics_uniform: true" in module.format_okx_direct_six_asset_1h_migration(first)

    monkeypatch.setattr(
        module,
        "_locate_dependency",
        lambda _report, relative, _sha: (
            capture.report_path if "anchor-1h-capture" in relative.as_posix() else promotion.report_path
        ),
    )
    validated = module.validate_okx_direct_six_asset_1h_migration(first.export_paths["report"])
    assert validated.report == first.report
    assert validated.registry_path == Path(first.export_paths["registry"])


def test_panel_rejects_boundary_drop_and_cli_has_no_capture_overrides(tmp_path, monkeypatch, capsys):
    config = _small_config()
    report = {
        "alignment_summary": config["expected_panel"] | {"coverage_rate": 1.0},
        "datasets": [
            {
                "dataset_id": dataset_id,
                "dropped_before_common_start": 0,
                "dropped_after_common_end": 0,
                "missing_inside_common_window": 0,
            }
            for dataset_id in module.ALL_DATASET_IDS
        ],
    }
    module._validate_direct_panel(report, config)
    report["datasets"][0]["dropped_before_common_start"] = 1
    with pytest.raises(MarketDataError, match="boundary_mismatch"):
        module._validate_direct_panel(report, config)
    report["datasets"][0]["dropped_before_common_start"] = 0
    report["alignment_summary"]["coverage_rate"] = 0.99
    with pytest.raises(MarketDataError, match="coverage_mismatch"):
        module._validate_direct_panel(report, config)
    report["alignment_summary"]["coverage_rate"] = 1.0
    report["alignment_summary"]["intersection_bar_count"] = 2
    with pytest.raises(MarketDataError, match="intersection_bar_count_mismatch"):
        module._validate_direct_panel(report, config)
    report["alignment_summary"]["intersection_bar_count"] = 3
    report["datasets"].pop()
    with pytest.raises(MarketDataError, match="membership_mismatch"):
        module._validate_direct_panel(report, config)

    result = SimpleNamespace(
        report={
            "capture_status": module.CAPTURE_STATUS,
            "capture_sha256": "a" * 64,
            "identity": {"policy": {"expected_bar_count": 40191}},
        },
        export_paths={"report": "capture.json"},
    )
    monkeypatch.setattr(cli_module, "capture_okx_direct_anchor_1h_history", lambda *_args: result)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "capture-okx-direct-anchor-1h-history",
            "--source-promotion-report",
            "promotion.json",
        ],
    )
    with pytest.raises(SystemExit) as exited:
        cli_module.main()
    assert exited.value.code == 0
    assert "bar_count_per_dataset: 40191" in capsys.readouterr().out


def _small_config():
    return {
        "schema_version": 1,
        "policy_version": 1,
        "migration_id": "test",
        "source_promotion_sha256": "p" * 64,
        "source_snapshot_received_at": "2026-08-02T15:49:19.356645+00:00",
        "anchor_inst_ids": list(module.ANCHOR_INST_IDS),
        "existing_direct_dataset_ids": list(module.EXISTING_DATASET_IDS),
        "history_start": "2022-01-01T00:00:00+00:00",
        "end_open": "2022-01-01T02:00:00+00:00",
        "end_policy": "fixed",
        "timeframe": "1h",
        "okx_bar": "1H",
        "bar_duration_ms": 3_600_000,
        "limit": 300,
        "expected_bar_count": 3,
        "expected_page_count": 1,
        "stable_data_root": "data/promoted/okx_direct_six_v1/1h",
        "target_panel_id": "okx_btc_eth_sol_knc_swftc_bico_1h_direct_v1",
        "alignment": "inner_exact",
        "expected_panel": {
            "common_first_timestamp": "2022-01-01T00:00:00+00:00",
            "common_last_timestamp": "2022-01-01T02:00:00+00:00",
            "intersection_bar_count": 3,
            "union_bar_count": 3,
            "coverage_rate": 1.0,
        },
        "claims": {
            "historical_point_in_time_membership": False,
            "survivorship_bias_resolved": False,
            "profitability_evidence": False,
        },
    }


def _promotion(repo: Path, timestamps=None):
    marker = repo / "reports" / "okx-frozen-universe-1h-promotion" / "promotion.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("promotion", encoding="utf-8")
    registry_path = repo / "source-registry.yaml"
    datasets = []
    registry_entries = []
    if timestamps is not None:
        data_root = repo / "data" / "promoted" / "okx_convenience_v1" / "1h"
        data_root.mkdir(parents=True)
        for index, dataset_id in enumerate(module.EXISTING_DATASET_IDS):
            csv_path = data_root / f"{dataset_id}.csv"
            _write_csv(csv_path, timestamps)
            raw_sha = _sha(csv_path)
            canonical_sha = canonical_ohlcv_sha256(csv_path)
            lineage_identity = {
                "schema_version": 1,
                "dataset_id": dataset_id,
                "inst_id": ("KNC-USDT", "SWFTC-USDT", "BICO-USDT")[index],
                "destination_repo_relative_path": csv_path.relative_to(repo).as_posix(),
                "destination_raw_sha256": raw_sha,
                "destination_canonical_sha256": canonical_sha,
                "timestamp_semantics": "verified_open_time",
            }
            lineage_sha = hashlib.sha256(
                json.dumps(lineage_identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            lineage_path = data_root / f"lineage.{dataset_id}.{lineage_sha}.json"
            lineage_path.write_text(
                json.dumps(lineage_identity | {"lineage_sha256": lineage_sha}, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            datasets.append(
                lineage_identity
                | {
                    "lineage_sha256": lineage_sha,
                    "lineage_repo_relative_path": lineage_path.relative_to(repo).as_posix(),
                }
            )
            registry_entries.append(
                _registry_entry(
                    dataset_id,
                    ("KNC/USDT", "SWFTC/USDT", "BICO/USDT")[index],
                    csv_path.relative_to(repo).as_posix(),
                    lineage_path.relative_to(repo).as_posix(),
                    raw_sha,
                    canonical_sha,
                    timestamps,
                )
            )
    registry_path.write_text(
        yaml.safe_dump({"schema_version": 1, "datasets": registry_entries}, sort_keys=False),
        encoding="utf-8",
    )
    panels = repo / "source-panels.yaml"
    panels.write_text("schema_version: 1\npanels: []\n", encoding="utf-8")
    return SimpleNamespace(
        report_path=marker,
        repo_root=repo,
        registry_path=registry_path,
        panels_config_path=panels,
        report={
            "promotion_sha256": "p" * 64,
            "identity": {
                "policy": {
                    "source_snapshot_received_at": "2026-08-02T15:49:19.356645+00:00"
                },
                "promoted_registry": {"sha256": "r" * 64},
                "promoted_panel": {"panel_sha256": "q" * 64},
                "datasets": datasets,
            },
        },
    )


def _validated_capture(repo, promotion, config, timestamps):
    capture_dir = repo / "reports" / "capture"
    capture_dir.mkdir()
    histories = []
    artifacts = {}
    for index, inst_id in enumerate(module.ANCHOR_INST_IDS):
        csv_path = capture_dir / f"{inst_id}.csv"
        _write_csv(csv_path, timestamps)
        raw_sha = _sha(csv_path)
        histories.append(
            {
                "inst_id": inst_id,
                "dataset_id": module.ANCHOR_DATASET_IDS[inst_id],
                "symbol": inst_id.replace("-", "/"),
                "bar_count": 3,
                "first_timestamp": timestamps[0].isoformat(),
                "last_timestamp": timestamps[-1].isoformat(),
                "history_bundle": {"sha256": str(index) * 64},
                "csv": {
                    "raw_sha256": raw_sha,
                    "canonical_sha256": canonical_ohlcv_sha256(csv_path),
                },
            }
        )
        artifacts[f"{inst_id}_csv"] = csv_path
    report_path = capture_dir / "capture.json"
    report_path.write_text("capture", encoding="utf-8")
    return module.ValidatedOkxDirectAnchor1hCapture(
        report_path,
        {
            "capture_sha256": "c" * 64,
            "identity": {"policy": config, "contract": {"sha256": "d" * 64}},
        },
        promotion,
        tuple(histories),
        artifacts,
    )


def _small_history(*args, **kwargs):
    start = 1_640_995_200_000
    rows = [_row(start + index * 3_600_000) for index in range(3)]
    response = json.dumps(
        {"code": "0", "msg": "", "data": list(reversed(rows))}, separators=(",", ":")
    )
    params = {
        "instId": args[0],
        "bar": "1H",
        "after": str(start + 3 * 3_600_000),
        "limit": "300",
    }
    record = {
        "page_index": 0,
        "endpoint": "https://www.okx.com/api/v5/market/history-candles",
        "request_params": params,
        "response_body": response,
        "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
    }
    bundle = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    pages = [
        {
            "page_index": 0,
            "request_params": params,
            "response_sha256": record["response_sha256"],
            "row_count": 3,
            "newest_ts": rows[-1][0],
            "oldest_ts": rows[0][0],
        }
    ]
    return bundle, pages, rows


def _row(timestamp):
    return [str(timestamp), "1", "2", "0.5", "1.5", "10", "15", "15", "1"]


def _write_csv(path, timestamps):
    pd.DataFrame(
        {
            "timestamp": [value.isoformat() for value in timestamps],
            "open": [1.0, 1.1, 1.2],
            "high": [1.2, 1.3, 1.4],
            "low": [0.9, 1.0, 1.1],
            "close": [1.1, 1.2, 1.3],
            "volume": [10.0, 11.0, 12.0],
        }
    ).to_csv(path, index=False, lineterminator="\n")


def _registry_entry(dataset_id, symbol, path, evidence, raw_sha, canonical_sha, timestamps):
    return {
        "dataset_id": dataset_id,
        "symbol": symbol,
        "timeframe": "1h",
        "path": path,
        "source": {
            "status": "verified",
            "evidence": [evidence],
            "timestamp_semantics": "verified_open_time",
        },
        "quality": {"validator": "validate_ohlcv_csv"},
        "expected": {
            "bar_count": 3,
            "first_timestamp": timestamps[0].isoformat(),
            "last_timestamp": timestamps[-1].isoformat(),
            "raw_sha256": raw_sha,
            "canonical_sha256": canonical_sha,
        },
    }


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
