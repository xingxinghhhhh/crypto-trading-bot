import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import pandas as pd
import yaml
import crypto_bot.market.okx_universe_history_extension as module
import crypto_bot.cli as cli_module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_promotion import ValidatedDatasetPromotion
from crypto_bot.market.okx_universe_intake import (
    download_okx_public_history,
    replay_okx_public_history,
)


def test_generic_okx_history_helpers_support_one_hour_without_changing_four_hour_rules():
    hour = 3_600_000
    start = 1_640_995_200_000
    end = start + 5 * hour
    rows = [_row(timestamp) for timestamp in range(start, end + hour, hour)]

    def fetch(url):
        params = parse_qs(urlparse(url).query)
        cursor = int(params["after"][0])
        page = [row for row in reversed(rows) if int(row[0]) < cursor][:3]
        return json.dumps({"code": "0", "msg": "", "data": page}, separators=(",", ":")).encode()

    bundle, pages, observed = download_okx_public_history(
        "KNC-USDT",
        start,
        end,
        okx_bar="1H",
        bar_duration_ms=hour,
        fetcher=fetch,
        request_interval_seconds=0,
    )
    replayed, replay_pages = replay_okx_public_history(
        bundle,
        "KNC-USDT",
        start,
        end,
        okx_bar="1H",
        bar_duration_ms=hour,
    )

    assert observed == replayed
    assert pages == replay_pages
    assert len(observed) == 6
    assert len(pages) == 2
    assert all(page["request_params"]["bar"] == "1H" for page in pages)


def test_capture_is_frozen_marker_driven_and_deterministic(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    promotion = _promotion(repo)
    policy = _small_policy()
    source_capture = SimpleNamespace(
        capture_path=repo / "source-capture.json",
        capture={
            "capture_sha256": "2" * 64,
            "identity": {"snapshot": {"received_at": policy["source_snapshot_received_at"]}},
        },
        selected_inst_ids=module.EXPECTED_SELECTED,
    )
    source_capture.capture_path.write_text("source", encoding="utf-8")
    monkeypatch.setattr(module, "_load_policy", lambda path: policy)
    monkeypatch.setattr(module, "validate_dataset_promotion_report", lambda path: promotion)
    monkeypatch.setattr(module, "_validate_source_promotion_selection", lambda *args: source_capture)
    monkeypatch.setattr(module, "_load_contract", lambda *args: {"filename": "contract", "sha256": "3" * 64})
    monkeypatch.setattr(module, "download_okx_public_history", _small_history)

    first = module.capture_okx_frozen_universe_1h_history(
        "promotion.json", "policy.yaml", repo / "reports" / "capture-a", request_interval_seconds=0
    )
    second = module.capture_okx_frozen_universe_1h_history(
        "promotion.json", "policy.yaml", repo / "reports" / "capture-b", request_interval_seconds=0
    )

    assert first.report == second.report
    assert first.report["identity"]["source_snapshot"]["selected_inst_ids"] == list(
        module.EXPECTED_SELECTED
    )
    assert [item["bar_count"] for item in first.report["identity"]["histories"]] == [3, 3, 3]
    assert all(Path(path).is_file() for path in first.export_paths.values())
    assert {Path(path).name for path in first.export_paths.values()} == {
        Path(path).name for path in second.export_paths.values()
    }
    assert "private_api_used: false" in module.format_frozen_universe_1h_capture(first)

    monkeypatch.setattr(module, "_validate_policy", lambda value: policy)
    monkeypatch.setattr(module, "_validate_contract_identity", lambda *args: None)
    validated = module.validate_okx_frozen_universe_1h_capture(first.export_paths["report"])
    assert validated.report == first.report
    assert len(validated.histories) == 3


def test_capture_rejects_history_shape_and_output_escape(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    promotion = _promotion(repo)
    policy = _small_policy()
    monkeypatch.setattr(module, "_load_policy", lambda path: policy)
    monkeypatch.setattr(module, "validate_dataset_promotion_report", lambda path: promotion)
    monkeypatch.setattr(module, "_validate_source_promotion_selection", lambda *args: SimpleNamespace())
    monkeypatch.setattr(module, "_load_contract", lambda *args: {})
    monkeypatch.setattr(
        module,
        "download_okx_public_history",
        lambda *args, **kwargs: (b"bundle", [], []),
    )

    with pytest.raises(ValueError, match="inside reports"):
        module.capture_okx_frozen_universe_1h_history(
            "promotion.json", "policy.yaml", repo / "outside", request_interval_seconds=0
        )

    with pytest.raises(MarketDataError, match="history_shape"):
        module.capture_okx_frozen_universe_1h_history(
            "promotion.json", "policy.yaml", repo / "reports" / "capture", request_interval_seconds=0
        )


def test_frozen_policy_and_end_open_are_exact():
    policy = module._load_policy("config.okx-universe-1h-extension.example.yaml")

    assert policy["expected_bar_count"] == 40191
    assert policy["expected_page_count"] == 134
    assert module._compute_end_open(policy["source_snapshot_received_at"]) == policy["end_open"]

    drift = dict(policy) | {"expected_page_count": 133}
    with pytest.raises(ValueError, match="frozen policy"):
        module._validate_policy(drift)

    repo = Path(__file__).parents[1]
    contract = module._load_contract(repo, module.DEFAULT_CONTRACT, policy)
    assert contract["filename"] == "okx_public_frozen_history_contract_v1.json"
    assert len(contract["sha256"]) == 64
    module._validate_contract_identity(repo, contract, policy)


def test_content_addressed_collision_is_fail_closed(tmp_path):
    target = tmp_path / "artifact"
    target.write_bytes(b"old")

    with pytest.raises(MarketDataError, match="content_addressed_collision"):
        module._commit_bytes(target, b"new")


def test_source_promotion_selection_replays_the_original_snapshot(tmp_path, monkeypatch):
    policy = module._load_policy("config.okx-universe-1h-extension.example.yaml")
    repo = tmp_path / "repo"
    capture_path = (
        repo
        / "reports"
        / "okx-universe-capture"
        / f"okx-universe-capture.{'8' * 64}.json"
    )
    capture_path.parent.mkdir(parents=True)
    capture_path.write_bytes(b"capture")
    promotion = SimpleNamespace(
        repo_root=repo,
        report={
            "promotion_sha256": policy["source_promotion_sha256"],
            "identity": {
                "inputs": {
                    "capture": {
                        "capture_sha256": "8" * 64,
                        "report_sha256": module._sha256(capture_path),
                    }
                }
            },
        },
    )
    capture = SimpleNamespace(
        selected_inst_ids=module.EXPECTED_SELECTED,
        capture={"identity": {"snapshot": {"received_at": policy["source_snapshot_received_at"]}}},
    )
    monkeypatch.setattr(module, "validate_okx_universe_capture", lambda path: capture)

    assert module._validate_source_promotion_selection(promotion, policy) is capture


def test_frozen_1h_clis_format_success(monkeypatch, capsys):
    capture = SimpleNamespace(
        report={
            "capture_status": "complete_frozen_convenience_universe_1h_history",
            "capture_sha256": "1" * 64,
            "identity": {"histories": [{}, {}, {}], "policy": {"expected_bar_count": 40191}},
        },
        export_paths={"report": "capture.json"},
    )
    monkeypatch.setattr(cli_module, "capture_okx_frozen_universe_1h_history", lambda *args: capture)
    monkeypatch.setattr(
        sys,
        "argv",
        ["crypto-bot", "capture-okx-frozen-universe-1h-history", "--source-promotion-report", "p.json"],
    )
    with pytest.raises(SystemExit, match="0"):
        cli_module.main()
    assert "bar_count_per_dataset: 40191" in capsys.readouterr().out

    promotion = SimpleNamespace(
        report={
            "promotion_status": "verified_frozen_convenience_universe_1h_promotion",
            "promotion_sha256": "2" * 64,
            "identity": {
                "promoted_panel": {
                    "panel_sha256": "3" * 64,
                    "alignment_summary": {"intersection_bar_count": 20424},
                }
            },
        },
        export_paths={"report": "promotion.json"},
    )
    monkeypatch.setattr(cli_module, "promote_okx_frozen_universe_1h_panel", lambda *args: promotion)
    monkeypatch.setattr(
        sys,
        "argv",
        ["crypto-bot", "promote-okx-frozen-universe-1h-panel", "--capture-report", "c.json"],
    )
    with pytest.raises(SystemExit, match="0"):
        cli_module.main()
    assert "intersection_bar_count: 20424" in capsys.readouterr().out


def test_promotion_builds_independent_six_asset_panel_deterministically(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    promotion = _promotion(repo)
    timestamps = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    data_dir = repo / "data"
    data_dir.mkdir()
    old_entries = []
    for dataset_id in module.EXPECTED_BASE_DATASETS:
        csv_path = data_dir / f"{dataset_id}.csv"
        _write_csv(csv_path, timestamps)
        old_entries.append(_registry_payload(dataset_id, csv_path.relative_to(repo).as_posix()))
    base_registry = repo / "config.datasets.example.yaml"
    base_registry.write_text(
        yaml.safe_dump({"schema_version": 1, "datasets": old_entries}, sort_keys=False),
        encoding="utf-8",
    )
    base_panels = repo / "config.dataset-panels.example.yaml"
    base_panels.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "panels": [
                    {
                        "panel_id": "btc_eth_sol_1h_v1",
                        "dataset_ids": list(module.EXPECTED_BASE_DATASETS),
                        "alignment": "inner_exact",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    promotion.report["identity"]["inputs"] = {
        "base_registry": {"sha256": module._sha256(base_registry)},
        "base_panels_config": {"sha256": module._sha256(base_panels)},
    }
    histories = []
    artifact_paths = {}
    capture_dir = repo / "reports" / "capture"
    capture_dir.mkdir()
    for inst_id in module.EXPECTED_SELECTED:
        csv_path = capture_dir / f"{inst_id}.csv"
        _write_csv(csv_path, timestamps)
        raw_sha = module._sha256(csv_path)
        histories.append(
            {
                "inst_id": inst_id,
                "dataset_id": module.DATASET_IDS[inst_id],
                "symbol": inst_id.replace("-", "/"),
                "bar_count": 3,
                "first_timestamp": timestamps[0].isoformat(),
                "last_timestamp": timestamps[-1].isoformat(),
                "history_bundle": {"sha256": "6" * 64},
                "csv": {"raw_sha256": raw_sha, "canonical_sha256": raw_sha},
            }
        )
        artifact_paths[f"{inst_id}_csv"] = csv_path
    capture_report = capture_dir / "capture.json"
    capture_report.write_text("capture", encoding="utf-8")
    validated = module.ValidatedFrozenUniverse1hCapture(
        capture_report,
        {"capture_sha256": "7" * 64, "identity": {"policy": {}}},
        promotion,
        tuple(histories),
        artifact_paths,
    )
    boundary = {dataset_id: [0, 0] for dataset_id in [*module.EXPECTED_BASE_DATASETS, *module.DATASET_IDS.values()]}
    policy = {
        "base_panel_id": "btc_eth_sol_1h_v1",
        "target_panel_id": "btc_eth_sol_knc_swftc_bico_1h_v1",
        "stable_data_root": "data/promoted/okx_convenience_v1/1h",
        "expected_panel": {
            "common_first_timestamp": timestamps[0].isoformat(),
            "common_last_timestamp": timestamps[-1].isoformat(),
            "intersection_bar_count": 3,
            "union_bar_count": 3,
            "coverage_rate": 1.0,
            "boundary_drops": boundary,
        },
        "claims": {"profitability_evidence": False},
    }
    validated.report["identity"]["policy"] = policy
    monkeypatch.setattr(module, "validate_okx_frozen_universe_1h_capture", lambda path: validated)
    monkeypatch.setattr(module, "_load_policy", lambda path: policy)

    first = module.promote_okx_frozen_universe_1h_panel(
        capture_report,
        base_registry,
        base_panels,
        "policy.yaml",
        repo / "reports" / "promotion-a",
    )
    second = module.promote_okx_frozen_universe_1h_panel(
        capture_report,
        base_registry,
        base_panels,
        "policy.yaml",
        repo / "reports" / "promotion-b",
    )

    assert first.report == second.report
    assert first.report["identity"]["promoted_panel"]["alignment_summary"]["intersection_bar_count"] == 3
    assert first.report["identity"]["timestamp_semantics"]["aggregate_status"] == "mixed_unverified"
    assert all(Path(path).is_file() for path in first.export_paths.values())
    assert {Path(path).name for path in first.export_paths.values()} == {
        Path(path).name for path in second.export_paths.values()
    }
    assert "intersection_bar_count: 3" in module.format_frozen_universe_1h_promotion(first)

    canonical_capture = (
        repo
        / "reports"
        / "okx-frozen-universe-1h-capture"
        / f"okx-frozen-universe-1h-capture.{'7' * 64}.json"
    )
    canonical_capture.parent.mkdir()
    canonical_capture.write_bytes(capture_report.read_bytes())
    monkeypatch.setattr(module, "validate_dataset_promotion_report", lambda path: promotion)
    monkeypatch.setattr(module, "_validate_policy", lambda value: policy)
    observed = module.validate_okx_frozen_universe_1h_promotion(first.export_paths["report"])
    assert observed.report == first.report
    assert observed.registry_path == Path(first.export_paths["promoted_registry"])


def _small_policy():
    return {
        "schema_version": 1,
        "source_promotion_sha256": "1" * 64,
        "source_snapshot_received_at": "2026-08-02T15:49:19.356645+00:00",
        "selected_inst_ids": list(module.EXPECTED_SELECTED),
        "history_start": "2022-01-01T00:00:00+00:00",
        "end_open": "2022-01-01T02:00:00+00:00",
        "timeframe": "1h",
        "okx_bar": "1H",
        "bar_duration_ms": 3_600_000,
        "expected_bar_count": 3,
        "expected_page_count": 1,
        "claims": {"profitability_evidence": False},
    }


def _small_history(*args, **kwargs):
    start = 1_640_995_200_000
    rows = [_row(start + index * 3_600_000) for index in range(3)]
    response = json.dumps({"code": "0", "msg": "", "data": list(reversed(rows))}, separators=(",", ":"))
    params = {"instId": args[0], "bar": "1H", "after": str(start + 3 * 3_600_000), "limit": "300"}
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


def _promotion(repo: Path):
    marker = repo / "reports" / "okx-universe-promotion" / "promotion.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("promotion", encoding="utf-8")
    for name in ("registry.yaml", "panels.yaml", "registry-audit.json", "panel-audit.json"):
        (repo / name).write_text(name, encoding="utf-8")
    return ValidatedDatasetPromotion(
        report_path=marker,
        report={
            "promotion_sha256": "1" * 64,
            "identity": {
                "promoted_registry": {"sha256": "4" * 64},
                "promoted_panel": {"panel_sha256": "5" * 64},
            },
        },
        repo_root=repo,
        registry_path=repo / "registry.yaml",
        panels_config_path=repo / "panels.yaml",
        registry_audit_path=repo / "registry-audit.json",
        panel_audit_path=repo / "panel-audit.json",
    )


def _row(timestamp):
    return [str(timestamp), "1", "2", "0.5", "1.5", "10", "15", "15", "1"]


def _write_csv(path: Path, timestamps) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [value.isoformat() for value in timestamps],
            "open": [1.0, 1.1, 1.2],
            "high": [1.2, 1.3, 1.4],
            "low": [0.9, 1.0, 1.1],
            "close": [1.1, 1.2, 1.3],
            "volume": [10.0, 11.0, 12.0],
        }
    )
    frame.to_csv(path, index=False, lineterminator="\n")


def _registry_payload(dataset_id: str, path: str) -> dict:
    return {
        "dataset_id": dataset_id,
        "symbol": dataset_id.split("_", 1)[0].upper() + "/USDT",
        "timeframe": "1h",
        "path": path,
        "source": {"status": "unknown", "evidence": []},
        "quality": {"validator": "validate_ohlcv_csv"},
        "expected": {},
    }
