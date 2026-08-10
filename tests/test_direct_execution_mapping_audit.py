from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import crypto_bot.market.direct_execution_mapping_audit as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.okx-direct-six-1h-execution-mapping.example.yaml"
MIGRATION = next((ROOT / "reports" / "okx-direct-six-asset-1h-migration").glob("okx-direct-six-migration.*.json"))
MUTABILITY = ROOT / "reports" / "okx-public-response-mutability" / (
    "public-response-mutability.3ff4b3736a00eb55b34bcc49c87fcc4dd3bb0d70f741399c1112a3711a9dfb2c.json"
)


def test_config_is_frozen_and_has_six_direct_assets():
    config = module.load_direct_execution_mapping_config(CONFIG, ROOT)
    assert config["dataset_ids"] == list(module.ALL_DATASET_IDS)
    assert config["expected_mapping_row_count"] == 241146
    assert config["execution_offset_bars"] == 2


def test_config_rejects_wrong_filename_and_content(tmp_path):
    wrong_name = tmp_path / "wrong.yaml"
    wrong_name.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        module.load_direct_execution_mapping_config(wrong_name)
    wrong = tmp_path / CONFIG.name
    wrong.write_text("schema_version: 99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="equal"):
        module.load_direct_execution_mapping_config(wrong)


def test_mapping_counts_and_timestamp_statuses_fail_closed():
    config = {
        "expected_signal_timestamp_count": 2,
        "expected_mapping_row_count": 4,
        "expected_structural_valid_count": 2,
        "expected_tail_count": 2,
    }
    timestamps = (pd.Timestamp("2026-01-01T00:00:00Z"), pd.Timestamp("2026-01-01T01:00:00Z"))
    rows = [
        {"structural_status": "exact_finite_open"},
        {"structural_status": "exact_finite_open"},
        {"structural_status": "tail_outside_common_panel"},
        {"structural_status": "tail_outside_common_panel"},
    ]
    assert module._mapping_counts(timestamps, rows, config)["internal_missing_count"] == 0
    with pytest.raises(MarketDataError, match="count_mismatch"):
        module._mapping_counts(timestamps, rows[:-1], config)
    report = {"identity": {"timestamp_semantics": {"components": [
        {"dataset_id": "a", "status": "verified_open_time"},
    ]}}}
    assert module._timestamp_statuses(report, ["a"]) == {"a": "verified_open_time"}
    with pytest.raises(MarketDataError, match="semantics"):
        module._timestamp_statuses(report, ["b"])


def test_mutability_validator_accepts_frozen_marker_and_rejects_tampering():
    config = module.load_direct_execution_mapping_config(CONFIG, ROOT)
    report = module._validate_mutability_report(MUTABILITY, config, ROOT)
    assert report["identity"]["public_historical_response_mutability_observed"] is True
    tampered = MUTABILITY.parent / "public-response-mutability.3ff4b3736a00eb55b34bcc49c87fcc4dd3bb0d70f741399c1112a3711a9dfb2c.tmp.json"
    tampered.write_text(MUTABILITY.read_text(encoding="utf-8").replace("false", "true", 1), encoding="utf-8")
    try:
        with pytest.raises(MarketDataError):
            module._validate_mutability_report(tampered, config, ROOT)
    finally:
        tampered.unlink()


def test_audit_success_uses_shared_mapping_helper(monkeypatch, tmp_path):
    config = module.load_direct_execution_mapping_config(CONFIG, ROOT)
    timestamps = pd.date_range("2026-01-01", periods=40191, freq="h", tz="UTC")
    panel = SimpleNamespace(
        frame=pd.DataFrame({"timestamp": timestamps}),
        report={"panel_sha256": config["panel_sha256"]},
    )
    entries = tuple(
        SimpleNamespace(dataset_id=item, symbol=item, resolved_path=ROOT / "dummy.csv")
        for item in config["dataset_ids"]
    )
    migration = SimpleNamespace(
        report_path=MIGRATION,
        report={
            "identity": {
                "panel": {"panel_sha256": config["panel_sha256"], "panel_id": config["panel_id"]},
                "timestamp_semantics": {"components": [
                    {"dataset_id": item, "status": "verified_open_time"}
                    for item in config["dataset_ids"]
                ]},
                "datasets": [{"dataset_id": item} for item in config["dataset_ids"]],
            },
            "migration_sha256": config["migration_sha256"],
        },
        registry_path=ROOT / "okx-direct-six-registry.0efab2f048e49aa691da174fa1f1285d8a79f6ccd1b15b685c2b2e0bb2e5ae03.yaml",
        panels_config_path=ROOT / "okx-direct-six-panels.5027eb67ddf55516d2321873ab33b1ffd11c62d4155938e53577e470c8d9eb2d.yaml",
    )
    fake_rows = [{field: "" for field in module.MAPPING_FIELDS}]
    monkeypatch.setattr(module, "validate_okx_direct_six_asset_1h_migration", lambda _: migration)
    monkeypatch.setattr(module, "_validate_migration_pin", lambda *_args: None)
    monkeypatch.setattr(module, "_validate_mutability_report", lambda *_args: {
        "identity": {"capture_replay_deterministic": True, "network_recapture_byte_identical": False}
    })
    monkeypatch.setattr(module, "build_dataset_panel", lambda *_args: panel)
    monkeypatch.setattr(module, "load_dataset_registry", lambda *_args: SimpleNamespace(
        get=lambda dataset_id: next(item for item in entries if item.dataset_id == dataset_id)
    ))
    monkeypatch.setattr(module.pd, "read_csv", lambda *_args: pd.DataFrame({
        "timestamp": [pd.Timestamp("2026-01-01", tz="UTC")], "open": [1.0]
    }))
    monkeypatch.setattr(module, "canonicalize_ohlcv_frame", lambda frame: frame)
    monkeypatch.setattr(module, "build_exact_next_open_mapping_rows", lambda *_args, **_kwargs: fake_rows)
    monkeypatch.setattr(module, "_mapping_counts", lambda *_args: {
        "signal_timestamp_count": 40191, "mapping_row_count": 241146,
        "structural_valid_count": 241134, "tail_count": 12, "internal_missing_count": 0,
    })
    output = tmp_path / "reports" / "mapping"
    output.mkdir(parents=True)
    monkeypatch.setattr(module, "_reports_output", lambda *_args: output)
    result = module.audit_okx_direct_six_1h_execution_mapping(MIGRATION, MUTABILITY, CONFIG, output)
    assert result.report["feasibility"]["execution_price_mapping_feasible"] is True
    assert result.report["feasibility"]["pnl_computation_authorized"] is False
    assert Path(result.export_paths["mappings"]).is_file()
    assert "pnl_prerequisite_timestamp_mapping_satisfied: true" in module.format_direct_execution_mapping_audit(result)


def test_reports_output_rejects_path_escape(tmp_path):
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(ROOT, tmp_path)
