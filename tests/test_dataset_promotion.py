import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.dataset_promotion as promotion_module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_promotion import (
    EXPECTED_BASE_DATASETS,
    EXPECTED_BOUNDARY_DROPS,
    EXPECTED_CANDIDATES,
    EXPECTED_PANEL,
    format_dataset_promotion,
    promote_okx_universe_candidates,
    validate_dataset_promotion_report,
)
from crypto_bot.market.okx_universe_intake import (
    OkxUniverseValidatedCapture,
    OkxUniverseValidatedIntake,
)


class _Entry:
    def __init__(self, dataset_id):
        self.dataset_id = dataset_id

    def declared_metadata(self):
        return {
            "path": f"data/{self.dataset_id}.csv",
            "symbol": self.dataset_id.split("_", 1)[0].upper() + "/USDT",
            "timeframe": "4h",
            "source": {"status": "partial", "evidence": ["evidence.txt"]},
            "expected": {},
            "quality": {"validator": "validate_ohlcv_csv"},
        }


class _Registry:
    def __init__(self):
        self.entries = tuple(_Entry(dataset_id) for dataset_id in EXPECTED_BASE_DATASETS)

    def get(self, dataset_id):
        return next(entry for entry in self.entries if entry.dataset_id == dataset_id)


def test_promotion_success_is_deterministic_and_fail_closed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "reports" / "capture").mkdir(parents=True)
    (repo / "reports" / "intake").mkdir(parents=True)
    (repo / "reports" / "semantics").mkdir(parents=True)
    (repo / "evidence.txt").write_text("evidence", encoding="utf-8")
    base_registry = repo / "config.datasets.example.yaml"
    base_registry.write_text("base", encoding="utf-8")
    base_panels = repo / "config.dataset-panels.example.yaml"
    base_panels.write_text("panels", encoding="utf-8")
    config = repo / "config.okx-universe-promotion.example.yaml"
    config.write_bytes(Path("config.okx-universe-promotion.example.yaml").read_bytes())
    semantics = repo / "reports" / "semantics" / "timestamp-semantics.fake.json"
    base_panel_sha = "1" * 64
    semantics_payload = {
        "datasets": [
            {"dataset_id": dataset_id, "status": "partial_unverified"}
            for dataset_id in EXPECTED_BASE_DATASETS
        ],
        "panels": [{"panel_id": "btc_eth_sol_4h_v1", "panel_sha256": base_panel_sha}],
    }
    semantics.write_text(json.dumps(semantics_payload), encoding="utf-8")
    capture = repo / "reports" / "capture" / f"okx-universe-capture.{'2' * 64}.json"
    capture.write_text("{}", encoding="utf-8")
    intake = repo / "reports" / "intake" / f"okx-universe-intake.{'3' * 64}.json"
    intake.write_text("{}", encoding="utf-8")

    semantics_identity = {
        "semantics_sha256": "4" * 64,
        "report_filename": semantics.name,
        "report_sha256": hashlib.sha256(semantics.read_bytes()).hexdigest(),
        "artifact_hashes": {"datasets": "5" * 64, "panels": "6" * 64, "producers": "7" * 64},
        "producer_id": "okx_public_candles_v1",
        "producer_status": "verified_open_time",
        "producer_contract_sha256": "8" * 64,
        "producer_probe_sha256": "9" * 64,
    }
    datasets = []
    candidates = []
    source_raw = hashlib.sha256(b"timestamp,open,high,low,close,volume\n").hexdigest()
    canonical = "a" * 64
    for inst_id, dataset_id in EXPECTED_CANDIDATES:
        slug = inst_id.lower().replace("-", "_")
        csv_name = f"{slug}.csv"
        bundle_name = f"{slug}.jsonl"
        (capture.parent / csv_name).write_bytes(b"timestamp,open,high,low,close,volume\n")
        (capture.parent / bundle_name).write_bytes(b"bundle")
        row = {
            "dataset_id": dataset_id,
            "inst_id": inst_id,
            "symbol": inst_id.replace("-", "/"),
            "timeframe": "4h",
            "first_timestamp": "2022-01-01T00:00:00+00:00",
            "last_timestamp": "2026-08-02T08:00:00+00:00",
            "bar_count": 10047,
            "history_bundle_filename": bundle_name,
            "history_bundle_sha256": hashlib.sha256(b"bundle").hexdigest(),
            "csv_filename": csv_name,
            "raw_sha256": source_raw,
            "canonical_sha256": canonical,
            "timestamp_semantics": "verified_open_time",
            "lineage_status": "complete_direct_okx_public",
        }
        datasets.append(row)
        candidates.append(
            {
                "dataset_id": dataset_id,
                "symbol": row["symbol"],
                "timeframe": "4h",
                "expected": {
                    "bar_count": 10047,
                    "first_timestamp": row["first_timestamp"],
                    "last_timestamp": row["last_timestamp"],
                    "raw_sha256": source_raw,
                    "canonical_sha256": canonical,
                },
            }
        )
    validated_capture = OkxUniverseValidatedCapture(
        capture_path=capture,
        capture={
            "capture_sha256": "2" * 64,
            "identity": {
                "semantics": semantics_identity,
                "registry": {"sha256": hashlib.sha256(base_registry.read_bytes()).hexdigest()},
            },
        },
        policy={},
        selected_inst_ids=tuple(inst_id for inst_id, _ in EXPECTED_CANDIDATES),
        eligibility=(),
        datasets=tuple(datasets),
        candidate_datasets=tuple(candidates),
        eligibility_bytes=b"",
        datasets_bytes=b"",
        candidates_bytes=b"candidate",
    )
    candidate_artifact = intake.parent / "candidates.yaml"
    candidate_artifact.write_bytes(b"candidate")
    validated_intake = OkxUniverseValidatedIntake(
        capture=validated_capture,
        intake_path=intake,
        intake_report={"intake_sha256": "3" * 64},
        artifact_paths={"registry_candidates": candidate_artifact},
    )
    registry = _Registry()
    panel_components = []
    for dataset_id, (before, after) in EXPECTED_BOUNDARY_DROPS.items():
        panel_components.append(
            {
                "dataset_id": dataset_id,
                "raw_sha256": source_raw,
                "canonical_sha256": canonical,
                "source_bar_count": 10047,
                "aligned_bar_count": 9486,
                "dropped_before_common_start": before,
                "dropped_after_common_end": after,
                "dropped_inside_common_window": 0,
                "missing_inside_common_window": 0,
            }
        )
    promoted_panel_report = {
        "panel_sha256": "b" * 64,
        "timestamp_semantics": {"status": "unverified"},
        "alignment_summary": EXPECTED_PANEL,
        "datasets": panel_components,
    }
    panel_calls = 0

    def fake_build_panel(*args, **kwargs):
        nonlocal panel_calls
        panel_calls += 1
        report = {"panel_sha256": base_panel_sha} if panel_calls == 1 else promoted_panel_report
        return SimpleNamespace(report=report)

    monkeypatch.setattr(promotion_module, "validate_okx_universe_intake", lambda *args: validated_intake)
    monkeypatch.setattr(
        promotion_module,
        "validate_okx_timestamp_semantics_report",
        lambda path: semantics_identity,
    )
    monkeypatch.setattr(promotion_module, "load_dataset_registry", lambda path: registry)
    monkeypatch.setattr(
        promotion_module,
        "load_dataset_panel_config",
        lambda path: SimpleNamespace(
            get=lambda panel_id: SimpleNamespace(dataset_ids=EXPECTED_BASE_DATASETS)
        ),
    )
    monkeypatch.setattr(promotion_module, "build_dataset_panel", fake_build_panel)
    monkeypatch.setattr(
        promotion_module,
        "audit_dataset_registry",
        lambda path: {"valid": True, "dataset_count": 6, "datasets": []},
    )
    monkeypatch.setattr(
        promotion_module,
        "validate_ohlcv_csv",
        lambda path, timeframe: SimpleNamespace(valid=True),
    )
    monkeypatch.setattr(promotion_module, "canonical_ohlcv_sha256", lambda path: canonical)

    first = promote_okx_universe_candidates(
        base_registry,
        base_panels,
        semantics,
        capture,
        intake,
        config,
        repo / "reports" / "promotion-a",
    )
    panel_calls = 0
    second = promote_okx_universe_candidates(
        base_registry,
        base_panels,
        semantics,
        capture,
        intake,
        config,
        repo / "reports" / "promotion-b",
    )

    assert first.report == second.report
    assert first.report["identity"]["timestamp_semantics"]["aggregate_status"] == "mixed_unverified"
    assert first.report["identity"]["timestamp_semantics"]["timestamp_semantics_uniform"] is False
    assert first.report["identity"]["promoted_panel"]["alignment"] == EXPECTED_PANEL
    assert "profitability_evidence: false" in format_dataset_promotion(first)
    assert Path(first.export_paths["promoted_registry"]).parent == repo
    assert all(
        Path(first.export_paths[f"{inst_id}_csv"]).is_relative_to(repo / "data" / "promoted")
        for inst_id, _ in EXPECTED_CANDIDATES
    )
    panel_calls = 1
    validated = validate_dataset_promotion_report(first.export_paths["report"])
    assert validated.report == first.report
    assert validated.registry_path == Path(first.export_paths["promoted_registry"])
    assert validated.panels_config_path == Path(first.export_paths["promoted_panels"])
    Path(first.export_paths["KNC-USDT_csv"]).write_bytes(b"collision")
    panel_calls = 0
    with pytest.raises(MarketDataError, match="content_addressed_artifact_collision"):
        promote_okx_universe_candidates(
            base_registry,
            base_panels,
            semantics,
            capture,
            intake,
            config,
            repo / "reports" / "promotion-c",
        )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("outside", "inside reports"),
        ("reports/../data", "inside reports"),
    ],
)
def test_output_dir_rejects_non_report_paths(tmp_path, path, expected):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match=expected):
        promotion_module._resolve_output_dir(repo, repo / path)


def test_frozen_config_and_path_guards_reject_drift(tmp_path):
    config = tmp_path / "promotion.yaml"
    payload = Path("config.okx-universe-promotion.example.yaml").read_text(encoding="utf-8")
    config.write_text(payload.replace("inner_exact", "outer", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen policy"):
        promotion_module._load_promotion_config(config)

    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="frozen path"):
        promotion_module._resolve_stable_root(repo, "reports/promoted")
    with pytest.raises(ValueError, match="inside the repository"):
        promotion_module._existing_repo_file(repo, tmp_path / "outside.json", "input")
    with pytest.raises(FileNotFoundError, match="not found"):
        promotion_module._existing_repo_file(repo, repo / "missing.json", "input")


def test_semantics_and_panel_guards_fail_closed():
    partial = {
        "datasets": [
            {"dataset_id": dataset_id, "status": "partial_unverified"}
            for dataset_id in EXPECTED_BASE_DATASETS
        ],
        "panels": [{"panel_id": "btc_eth_sol_4h_v1", "panel_sha256": "1" * 64}],
    }
    promotion_module._validate_base_semantics(
        partial,
        "1" * 64,
        EXPECTED_BASE_DATASETS,
    )
    partial["datasets"][0]["status"] = "verified_open_time"
    with pytest.raises(MarketDataError, match="base_dataset_semantics"):
        promotion_module._validate_base_semantics(
            partial,
            "1" * 64,
            EXPECTED_BASE_DATASETS,
        )
    partial["datasets"][0]["status"] = "partial_unverified"
    with pytest.raises(MarketDataError, match="base_panel_semantics"):
        promotion_module._validate_base_semantics(
            partial,
            "2" * 64,
            EXPECTED_BASE_DATASETS,
        )

    components = [
        {
            "dataset_id": dataset_id,
            "dropped_before_common_start": before,
            "dropped_after_common_end": after,
            "dropped_inside_common_window": 0,
            "missing_inside_common_window": 0,
        }
        for dataset_id, (before, after) in EXPECTED_BOUNDARY_DROPS.items()
    ]
    report = {
        "timestamp_semantics": {"status": "verified"},
        "alignment_summary": EXPECTED_PANEL,
        "datasets": components,
    }
    with pytest.raises(MarketDataError, match="generic_panel_semantics"):
        promotion_module._validate_promoted_panel(report)
    report["timestamp_semantics"]["status"] = "unverified"
    report["alignment_summary"] = {**EXPECTED_PANEL, "intersection_bar_count": 1}
    with pytest.raises(MarketDataError, match="panel_alignment"):
        promotion_module._validate_promoted_panel(report)
    report["alignment_summary"] = EXPECTED_PANEL
    report["datasets"] = components[:-1]
    with pytest.raises(MarketDataError, match="component_membership"):
        promotion_module._validate_promoted_panel(report)
    report["datasets"] = components
    report["datasets"][0]["missing_inside_common_window"] = 1
    with pytest.raises(MarketDataError, match="panel_boundary"):
        promotion_module._validate_promoted_panel(report)


def test_invalid_json_and_cli_failure_are_reported(tmp_path, monkeypatch, capsys):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(MarketDataError, match="bad_json"):
        promotion_module._load_json_mapping(invalid, "bad_json")

    monkeypatch.setattr(
        cli_module,
        "promote_okx_universe_candidates",
        lambda *args: (_ for _ in ()).throw(MarketDataError("blocked")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "promote-okx-universe-candidates",
            "--semantics-report",
            "semantics.json",
            "--capture-report",
            "capture.json",
            "--intake-report",
            "intake.json",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        cli_module.main()
    assert "okx_universe_promotion_failed: blocked" in capsys.readouterr().err


def test_promotion_cli_formats_result(monkeypatch, capsys):
    fake = SimpleNamespace(
        report={
            "promotion_sha256": "f" * 64,
            "promotion_status": "verified_offline_candidate_promotion",
            "identity": {
                "promoted_registry": {"sha256": "e" * 64},
                "promoted_panel": {
                    "panel_sha256": "d" * 64,
                    "alignment": {"intersection_bar_count": 1, "union_bar_count": 2},
                },
                "timestamp_semantics": {
                    "aggregate_status": "mixed_unverified",
                    "timestamp_semantics_uniform": False,
                },
            },
        },
        export_paths={"report": "report.json"},
    )
    monkeypatch.setattr(cli_module, "promote_okx_universe_candidates", lambda *args: fake)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "promote-okx-universe-candidates",
            "--semantics-report",
            "semantics.json",
            "--capture-report",
            "capture.json",
            "--intake-report",
            "intake.json",
        ],
    )
    with pytest.raises(SystemExit, match="0"):
        cli_module.main()
    output = capsys.readouterr().out
    assert "promotion_status: verified_offline_candidate_promotion" in output
    assert "exported_report: report.json" in output
