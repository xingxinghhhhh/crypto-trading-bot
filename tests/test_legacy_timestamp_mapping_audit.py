import csv
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.legacy_timestamp_mapping_audit as module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.legacy_timestamp_mapping_audit import (
    assess_mapping_authorization,
    audit_legacy_1h_next_open_mapping,
    format_legacy_timestamp_mapping_audit,
    load_legacy_timestamp_mapping_config,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.legacy-1h-timestamp-mapping.example.yaml"
DATASET_IDS = tuple(module.EXPECTED_CONFIG["required_dataset_ids"])
STATUSES = {
    "okx_bico_usdt_1h_frozen": "verified_open_time",
    "btc_usdt_1h_v1": "partial_unverified",
    "eth_usdt_1h_v1": "unknown",
    "okx_knc_usdt_1h_frozen": "verified_open_time",
    "sol_usdt_1h_v1": "unknown",
    "okx_swftc_usdt_1h_frozen": "verified_open_time",
}


def test_config_is_frozen_and_does_not_allow_mapping_overrides(tmp_path):
    assert load_legacy_timestamp_mapping_config(CONFIG) == module.EXPECTED_CONFIG
    changed = json.loads(json.dumps(module.EXPECTED_CONFIG))
    changed["execution_offset_bars"] = 1
    path = tmp_path / "changed.yaml"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config_not_frozen"):
        load_legacy_timestamp_mapping_config(path)

    with pytest.raises(FileNotFoundError):
        load_legacy_timestamp_mapping_config(tmp_path / "missing.yaml")
    non_mapping = tmp_path / "non-mapping.yaml"
    non_mapping.write_text("- invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_legacy_timestamp_mapping_config(non_mapping)
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("key: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML is invalid"):
        load_legacy_timestamp_mapping_config(invalid_yaml)


def test_authorization_requires_all_six_verified_and_rejects_conflict():
    verified = {dataset_id: "verified_open_time" for dataset_id in DATASET_IDS}
    assert assess_mapping_authorization(
        verified,
        structural_valid_count=12,
        expected_structural_valid_count=12,
        internal_missing_count=0,
    )
    assert not assess_mapping_authorization(
        STATUSES,
        structural_valid_count=12,
        expected_structural_valid_count=12,
        internal_missing_count=0,
    )
    assert not assess_mapping_authorization(
        verified,
        structural_valid_count=11,
        expected_structural_valid_count=12,
        internal_missing_count=0,
    )
    conflict = verified | {DATASET_IDS[0]: "conflict"}
    with pytest.raises(MarketDataError, match="conflicting_semantics"):
        assess_mapping_authorization(
            conflict,
            structural_valid_count=12,
            expected_structural_valid_count=12,
            internal_missing_count=0,
        )


def test_exact_mapping_has_two_signal_tail_rows_per_asset_and_no_fill(monkeypatch):
    timestamps = tuple(pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC"))
    entries = tuple(_entry(dataset_id, index) for index, dataset_id in enumerate(DATASET_IDS))
    frames = {dataset_id: _frame(timestamps) for dataset_id in DATASET_IDS}
    rows = module._mapping_rows(entries, frames, timestamps, STATUSES, module.EXPECTED_CONFIG)
    assert len(rows) == 24
    assert sum(row["structural_status"] == "exact_finite_open" for row in rows) == 12
    assert sum(row["structural_status"] == "tail_outside_common_panel" for row in rows) == 12
    assert not any(row["exact_row_exists"] for row in rows[-12:])

    broken = dict(frames)
    broken[DATASET_IDS[0]] = broken[DATASET_IDS[0]].iloc[[0, 1, 3]].reset_index(drop=True)
    with pytest.raises(MarketDataError, match="internal_exact_open_missing"):
        module._mapping_rows(entries, broken, timestamps, STATUSES, module.EXPECTED_CONFIG)

    non_finite = dict(frames)
    non_finite[DATASET_IDS[0]] = non_finite[DATASET_IDS[0]].copy()
    non_finite[DATASET_IDS[0]].loc[2, "open"] = float("nan")
    with pytest.raises(MarketDataError, match="internal_exact_open_missing"):
        module._mapping_rows(entries, non_finite, timestamps, STATUSES, module.EXPECTED_CONFIG)

    duplicated = dict(frames)
    duplicated[DATASET_IDS[0]] = pd.concat(
        [duplicated[DATASET_IDS[0]], duplicated[DATASET_IDS[0]].iloc[[0]]],
        ignore_index=True,
    )
    with pytest.raises(MarketDataError, match="duplicate_timestamp"):
        module._mapping_rows(entries, duplicated, timestamps, STATUSES, module.EXPECTED_CONFIG)


def test_signal_grid_rejects_duplicates_and_offgrid(monkeypatch):
    monkeypatch.setattr(module, "EXPECTED_SIGNAL_TIMESTAMP_COUNT", 3)
    regular = tuple(pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC"))
    module._validate_signal_grid(regular)
    with pytest.raises(MarketDataError, match="shape_mismatch"):
        module._validate_signal_grid((regular[0], regular[0], regular[2]))
    with pytest.raises(MarketDataError, match="offgrid"):
        module._validate_signal_grid((regular[0], regular[1], regular[2] + pd.Timedelta(minutes=1)))


def test_full_audit_is_deterministic_and_remains_semantics_blocked(tmp_path, monkeypatch):
    config = tmp_path / CONFIG.name
    config.write_bytes(CONFIG.read_bytes())
    timestamps = tuple(pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC"))
    entries = tuple(_entry(dataset_id, index, tmp_path) for index, dataset_id in enumerate(DATASET_IDS))
    registry = SimpleNamespace(entries=entries, get=lambda dataset_id: next(item for item in entries if item.dataset_id == dataset_id))
    promotion = SimpleNamespace(
        repo_root=tmp_path,
        registry_path=tmp_path / "registry.yaml",
        panels_config_path=tmp_path / "panels.yaml",
        report={"promotion_sha256": "p" * 64},
    )
    chain = SimpleNamespace(
        report={
            "chain_sha256": "c" * 64,
            "promotion": {"registry": {"sha256": "r" * 64}},
        },
        promotion=promotion,
    )
    mechanism_path = tmp_path / "mechanism.json"
    mechanism_path.write_text("fixed", encoding="utf-8")
    mechanism = SimpleNamespace(
        report_path=mechanism_path,
        report={
            "mechanism_sha256": module.EXPECTED_MECHANISM_SHA256,
            "variant_count": 36,
            "feasibility": {"pnl_computation_authorized": False},
            "source_chain": {
                "timestamp_semantics": {
                    "components": [
                        {"dataset_id": dataset_id, "status": STATUSES[dataset_id]}
                        for dataset_id in DATASET_IDS
                    ]
                }
            },
        },
        chain=chain,
    )
    semantics = {
        "semantics_sha256": module.EXPECTED_CONFIG["timestamp_semantics_report"]["semantics_sha256"],
        "datasets": [
            {"dataset_id": dataset_id, "status": STATUSES[dataset_id], "timeframe": "1h"}
            for dataset_id in ("btc_usdt_1h_v1", "eth_usdt_1h_v1", "sol_usdt_1h_v1")
        ],
    }
    panel = SimpleNamespace(
        frame=pd.DataFrame({"timestamp": timestamps}),
        report={"panel_sha256": module.EXPECTED_PANEL_SHA256},
    )
    monkeypatch.setattr(module, "EXPECTED_SIGNAL_TIMESTAMP_COUNT", 4)
    monkeypatch.setattr(module, "EXPECTED_MAPPING_ROW_COUNT", 24)
    monkeypatch.setattr(module, "EXPECTED_STRUCTURAL_VALID_COUNT", 12)
    monkeypatch.setattr(module, "validate_cross_sectional_portfolio_mechanism", lambda _path: mechanism)
    monkeypatch.setattr(module, "_validate_semantics_evidence", lambda *_args: semantics)
    monkeypatch.setattr(module, "load_dataset_registry", lambda _path: registry)
    monkeypatch.setattr(module, "build_dataset_panel", lambda *_args: panel)

    first = audit_legacy_1h_next_open_mapping(mechanism_path, config, tmp_path / "first")
    second = audit_legacy_1h_next_open_mapping(mechanism_path, config, tmp_path / "second")

    assert first.report == second.report
    assert first.report["feasibility"]["structural_common_next_open_mapping_verified"] is True
    assert first.report["feasibility"]["execution_price_mapping_feasible"] is False
    assert first.report["feasibility"]["pnl_computation_authorized"] is False
    assert first.report["blockers"] == [
        "legacy_timestamp_semantics_not_verified",
        "timestamp_semantics_not_uniform",
    ]
    assert "returns" not in first.report and "pnl" not in first.report
    for name in first.export_paths:
        assert Path(first.export_paths[name]).name == Path(second.export_paths[name]).name
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()
    with Path(first.export_paths["mappings"]).open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 24
    assert "execution_price_mapping_feasible: false" in format_legacy_timestamp_mapping_audit(first)


def test_evidence_hash_path_escape_and_content_addressed_collision_fail_closed(tmp_path):
    with pytest.raises(MarketDataError, match="path_escape"):
        module._repo_file(tmp_path, "../outside")
    path = tmp_path / "artifact.csv"
    module._commit_bytes(path, b"first")
    with pytest.raises(MarketDataError, match="content_addressed_collision"):
        module._commit_bytes(path, b"second")
    module._commit_bytes(path, b"first")
    with pytest.raises(MarketDataError, match="artifact_missing"):
        module._repo_file(tmp_path, "missing")


def test_semantics_evidence_validates_every_hash_and_status(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    dataset_csv = artifacts / "datasets.csv"
    dataset_csv.write_text("dataset_id,status\nbtc,partial_unverified\n", encoding="utf-8")
    evidence = tmp_path / "btc.evidence"
    evidence.write_bytes(b"preserved evidence")
    report = {
        "semantics_sha256": "s" * 64,
        "assessment_status": "independent_timestamp_semantics_evidence_only",
        "artifacts": {
            "datasets": {
                "filename": dataset_csv.name,
                "sha256": _sha(dataset_csv),
            }
        },
        "datasets": [],
    }
    report_path = artifacts / "semantics.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    config = {
        "timestamp_semantics_report": {
            "repo_relative_artifact": "artifacts/semantics.json",
            "artifact_sha256": _sha(report_path),
            "semantics_sha256": "s" * 64,
        },
        "legacy_datasets": [
            {
                "dataset_id": "btc",
                "expected_status": "partial_unverified",
                "evidence": [
                    {
                        "repo_relative_artifact": evidence.name,
                        "artifact_sha256": _sha(evidence),
                    }
                ],
            }
        ],
    }
    assert module._validate_semantics_evidence(tmp_path, config) == report

    wrong_report_hash = json.loads(json.dumps(config))
    wrong_report_hash["timestamp_semantics_report"]["artifact_sha256"] = "0" * 64
    with pytest.raises(MarketDataError, match="semantics_report_hash_mismatch"):
        module._validate_semantics_evidence(tmp_path, wrong_report_hash)

    wrong_identity = json.loads(json.dumps(config))
    wrong_identity["timestamp_semantics_report"]["semantics_sha256"] = "0" * 64
    with pytest.raises(MarketDataError, match="invalid_semantics_report"):
        module._validate_semantics_evidence(tmp_path, wrong_identity)

    dataset_csv.write_text("tampered", encoding="utf-8")
    with pytest.raises(MarketDataError, match="semantics_artifact_hash_mismatch"):
        module._validate_semantics_evidence(tmp_path, config)
    dataset_csv.write_text("dataset_id,status\nbtc,partial_unverified\n", encoding="utf-8")
    evidence.write_text("tampered", encoding="utf-8")
    with pytest.raises(MarketDataError, match="evidence_hash_mismatch"):
        module._validate_semantics_evidence(tmp_path, config)


def test_status_mechanism_and_count_drift_fail_closed(tmp_path, monkeypatch):
    semantics = {
        "datasets": [
            {"dataset_id": item["dataset_id"], "status": item["expected_status"], "timeframe": "1h"}
            for item in module.EXPECTED_CONFIG["legacy_datasets"]
        ]
    }
    module._validate_legacy_statuses(module.EXPECTED_CONFIG, semantics, STATUSES)
    mismatched = dict(STATUSES) | {"btc_usdt_1h_v1": "unknown"}
    with pytest.raises(MarketDataError, match="semantics_status_mismatch"):
        module._validate_legacy_statuses(module.EXPECTED_CONFIG, semantics, mismatched)
    conflict = dict(STATUSES) | {"okx_bico_usdt_1h_frozen": "conflict"}
    with pytest.raises(MarketDataError, match="conflicting_semantics"):
        module._validate_legacy_statuses(module.EXPECTED_CONFIG, semantics, conflict)

    mechanism_path = tmp_path / "mechanism.json"
    mechanism_path.write_text("marker", encoding="utf-8")
    invalid_mechanism = SimpleNamespace(
        report={
            "mechanism_sha256": "wrong",
            "variant_count": 36,
            "feasibility": {"pnl_computation_authorized": False},
        },
        report_path=mechanism_path,
    )
    with pytest.raises(MarketDataError, match="mechanism_mismatch"):
        module._mechanism_context(invalid_mechanism)

    monkeypatch.setattr(module, "EXPECTED_SIGNAL_TIMESTAMP_COUNT", 1)
    monkeypatch.setattr(module, "EXPECTED_MAPPING_ROW_COUNT", 1)
    monkeypatch.setattr(module, "EXPECTED_STRUCTURAL_VALID_COUNT", 1)
    with pytest.raises(MarketDataError, match="count_mismatch"):
        module._mapping_counts((pd.Timestamp("2024-01-01", tz="UTC"),), [])


def test_cli_has_no_timing_or_fill_overrides(tmp_path, monkeypatch, capsys):
    result = SimpleNamespace(export_paths={"report": str(tmp_path / "report.json")})
    monkeypatch.setattr(cli_module, "audit_legacy_1h_next_open_mapping", lambda *_args: result)
    monkeypatch.setattr(cli_module, "format_legacy_timestamp_mapping_audit", lambda _result: "ok")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "audit-legacy-1h-next-open-mapping",
            "--mechanism-report",
            "mechanism.json",
            "--evidence-config",
            str(CONFIG),
            "--output-dir",
            str(tmp_path),
        ],
    )
    with pytest.raises(SystemExit) as exited:
        cli_module.main()
    assert exited.value.code == 0
    assert "ok" in capsys.readouterr().out


def _entry(dataset_id: str, index: int, root: Path | None = None):
    path = (root or ROOT) / f"{dataset_id}.csv"
    if root is not None:
        _frame(tuple(pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC"))).to_csv(path, index=False)
    return SimpleNamespace(
        dataset_id=dataset_id,
        symbol=f"ASSET{index}/USDT",
        source_status="verified" if dataset_id.startswith("okx_") else "unknown",
        source_evidence=(),
        resolved_path=path,
        expected={"raw_sha256": str(index) * 64, "canonical_sha256": str(index + 1) * 64},
    )


def _frame(timestamps: tuple[pd.Timestamp, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [float(index + 1) for index in range(len(timestamps))],
            "high": [float(index + 2) for index in range(len(timestamps))],
            "low": [float(index + 0.5) for index in range(len(timestamps))],
            "close": [float(index + 1.5) for index in range(len(timestamps))],
            "volume": [100.0] * len(timestamps),
        }
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
