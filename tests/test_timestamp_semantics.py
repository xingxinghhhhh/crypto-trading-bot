from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from crypto_bot.errors import MarketDataError
from crypto_bot.market.timestamp_semantics import (
    _DatasetEvidence,
    _Evidence,
    _EvidenceConfig,
    _Producer,
    _aggregate_panel_status,
    _assess_dataset,
    _assess_producers,
    _covers_range,
    _load_evidence_config,
    _load_probe,
    _optional_timestamp,
    _require_sha256,
    _validate_dataset_evidence_membership,
    audit_timestamp_semantics,
    capture_okx_timestamp_probe,
    format_okx_timestamp_probe,
    format_timestamp_semantics_audit,
)


RECEIVED_AT = datetime(2026, 8, 2, 12, 0, 30, tzinfo=timezone.utc)
CONTRACT = Path("docs/evidence/okx_candles_contract_v1.json")


def test_okx_probe_captures_deterministic_content_addressed_raw_response(tmp_path):
    response = _valid_response()

    first = capture_okx_timestamp_probe(
        "BTC-USDT",
        "1m",
        tmp_path / "first",
        fetcher=lambda _: response,
        received_at=RECEIVED_AT,
    )
    second = capture_okx_timestamp_probe(
        "BTC-USDT",
        "1m",
        tmp_path / "second",
        fetcher=lambda _: response,
        received_at=RECEIVED_AT,
    )

    assert first.report == second.report
    assert first.report["probe_status"] == "verified_open_time_public_probe_only"
    assert first.report["response"]["row_count"] == 3
    assert first.report["response"]["closed_row_count"] == 2
    assert first.report["response"]["order"] == "strictly_descending_newest_first"
    assert first.report["timestamp_mapping"][0]["normalized_timestamp"] == (
        "2026-08-02T11:59:00+00:00"
    )
    assert Path(first.export_paths["response"]).read_bytes() == response
    for name in ("report", "response"):
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()
    assert "private_api_used: false" in format_okx_timestamp_probe(first)
    assert "trading_api_used: false" in format_okx_timestamp_probe(first)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda payload: b"not-json", "invalid_response_json"),
        (lambda payload: _mutate(payload, code="1"), "unsuccessful_response"),
        (lambda payload: _mutate(payload, data=[]), "empty_data"),
        (lambda payload: _mutate_row(payload, 0, lambda row: row[:-1]), "invalid_row_shape"),
        (lambda payload: _mutate_cell(payload, 0, 1, "bad"), "invalid_row_value"),
        (lambda payload: _mutate_cell(payload, 0, 0, str(_ts("12:00") + 1)), "off_grid"),
        (lambda payload: _mutate_cell(payload, 0, 8, "2"), "invalid_confirm"),
        (lambda payload: _mutate(payload, data=json.loads(payload)["data"][::-1]), "descending"),
        (
            lambda payload: _mutate(
                payload,
                data=[json.loads(payload)["data"][0], json.loads(payload)["data"][0]],
            ),
            "descending",
        ),
        (
            lambda payload: _mutate(
                payload,
                data=[row[:-1] + ["0"] for row in json.loads(payload)["data"]],
            ),
            "no_closed",
        ),
        (lambda payload: _mutate_cell(payload, 0, 8, "1"), "confirmed_bar_not_closed"),
    ],
)
def test_okx_probe_fails_closed_on_invalid_raw_responses(tmp_path, mutator, expected):
    response = mutator(_valid_response())

    with pytest.raises(MarketDataError, match=expected):
        capture_okx_timestamp_probe(
            "BTC-USDT",
            "1m",
            tmp_path / "out",
            fetcher=lambda _: response,
            received_at=RECEIVED_AT,
        )

    assert not list((tmp_path / "out").glob("*.json")) if (tmp_path / "out").exists() else True


def test_okx_probe_rejects_invalid_request_and_fetch_failure(tmp_path):
    with pytest.raises(ValueError, match="inst_id"):
        capture_okx_timestamp_probe("BTC/USDT", "1m", tmp_path)
    with pytest.raises(ValueError, match="unsupported"):
        capture_okx_timestamp_probe("BTC-USDT", "1d", tmp_path)
    with pytest.raises(MarketDataError, match="fetch_failed"):
        capture_okx_timestamp_probe(
            "BTC-USDT",
            "1m",
            tmp_path,
            fetcher=lambda _: (_ for _ in ()).throw(OSError("network down")),
        )


def test_okx_probe_rejects_missing_and_mismatched_contract(tmp_path):
    with pytest.raises(FileNotFoundError, match="contract evidence"):
        capture_okx_timestamp_probe(
            "BTC-USDT",
            "1m",
            tmp_path,
            contract_evidence_path=tmp_path / "missing.json",
            fetcher=lambda _: _valid_response(),
            received_at=RECEIVED_AT,
        )
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["timestamp_meaning"] = "bar_close_time"
    invalid = tmp_path / "invalid-contract.json"
    invalid.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(MarketDataError, match="contract_mismatch"):
        capture_okx_timestamp_probe(
            "BTC-USDT",
            "1m",
            tmp_path,
            contract_evidence_path=invalid,
            fetcher=lambda _: _valid_response(),
            received_at=RECEIVED_AT,
        )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("not-json", "invalid_contract_json"),
        ("[]", "contract_must_be_mapping"),
        (
            json.dumps(
                json.loads(CONTRACT.read_text(encoding="utf-8"))
                | {"response_shape": ["wrong"]}
            ),
            "response_shape_mismatch",
        ),
    ],
)
def test_okx_probe_rejects_malformed_contract_artifacts(tmp_path, content, expected):
    contract = tmp_path / "contract.json"
    contract.write_text(content, encoding="utf-8")

    with pytest.raises(MarketDataError, match=expected):
        capture_okx_timestamp_probe(
            "BTC-USDT",
            "1m",
            tmp_path,
            contract_evidence_path=contract,
            fetcher=lambda _: _valid_response(),
            received_at=RECEIVED_AT,
        )


def test_okx_probe_requires_utc_received_at(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        capture_okx_timestamp_probe(
            "BTC-USDT",
            "1m",
            tmp_path,
            fetcher=lambda _: _valid_response(),
            received_at=datetime(2026, 8, 2, 12, 0, 30),
        )


def test_probe_loader_rejects_response_drift_and_path_escape(tmp_path):
    result = _write_probe(tmp_path)
    report_path = Path(result.export_paths["report"])
    response_path = Path(result.export_paths["response"])
    response_path.write_bytes(b"tampered")

    with pytest.raises(MarketDataError, match="response_hash_mismatch"):
        _load_probe(report_path)

    clean = _write_probe(tmp_path / "clean")
    clean_report_path = Path(clean.export_paths["report"])
    report = json.loads(clean_report_path.read_text(encoding="utf-8"))
    report["response"]["filename"] = "../escape.json"
    clean_report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError, match="path_escape"):
        _load_probe(clean_report_path)


def test_probe_content_addressed_collision_fails_closed(tmp_path):
    result = _write_probe(tmp_path)
    report_path = Path(result.export_paths["report"])
    report_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(MarketDataError, match="content_addressed_artifact_collision"):
        _write_probe(tmp_path)


def test_probe_loader_rejects_invalid_json_schema_filename_identity_and_safety(tmp_path):
    result = _write_probe(tmp_path / "source")
    original = Path(result.export_paths["report"])
    original_text = original.read_text(encoding="utf-8")

    invalid_json = original
    invalid_json.write_text("not-json", encoding="utf-8")
    with pytest.raises(MarketDataError, match="invalid_probe_json"):
        _load_probe(invalid_json)

    report = json.loads(original_text)
    invalid_json.write_text(json.dumps(report | {"schema_version": 2}), encoding="utf-8")
    with pytest.raises(MarketDataError, match="invalid_probe_schema"):
        _load_probe(invalid_json)

    wrong_name = tmp_path / "wrong.json"
    wrong_name.write_text(original_text, encoding="utf-8")
    with pytest.raises(MarketDataError, match="filename_mismatch"):
        _load_probe(wrong_name)

    report["received_at"] = "2026-08-02T12:01:00+00:00"
    invalid_json.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity_mismatch"):
        _load_probe(invalid_json)

    report = json.loads(original_text)
    report["private_api_used"] = True
    invalid_json.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError, match="unsafe_probe"):
        _load_probe(invalid_json)


def test_dataset_assessment_complete_partial_unknown_conflict(tmp_path):
    artifact = tmp_path / "evidence"
    artifact.write_text("evidence", encoding="utf-8")
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    end = pd.Timestamp("2026-01-02T00:00:00Z")
    evidence = _Evidence("e1", "open", start, end, artifact, "evidence", "a" * 64)
    audit = SimpleNamespace(observed=SimpleNamespace(start_time=start.isoformat(), end_time=end.isoformat()))

    complete = _DatasetEvidence("d", "complete", (evidence,))
    assert _assess_dataset(complete, audit, {"open": "verified_open_time"}, "1h") == (
        "verified_open_time",
        "complete_lineage_with_consistent_verified_meaning",
    )
    gap = _DatasetEvidence(
        "d",
        "complete",
        (_Evidence("e1", "open", start + pd.Timedelta(hours=2), end, artifact, "evidence", "a" * 64),),
    )
    assert _assess_dataset(gap, audit, {"open": "verified_open_time"}, "1h")[0] == (
        "partial_unverified"
    )
    assert _assess_dataset(_DatasetEvidence("d", "partial", (evidence,)), audit, {}, "1h")[0] == (
        "partial_unverified"
    )
    assert _assess_dataset(_DatasetEvidence("d", "none", ()), audit, {}, "1h")[0] == "unknown"
    conflicting = _DatasetEvidence(
        "d",
        "complete",
        (
            evidence,
            _Evidence("e2", "close", start, end, artifact, "evidence", "a" * 64),
        ),
    )
    with pytest.raises(MarketDataError, match="conflicting_dataset"):
        _assess_dataset(
            conflicting,
            audit,
            {"open": "verified_open_time", "close": "verified_close_time"},
            "1h",
        )
    assert _assess_dataset(complete, audit, {"open": "unknown"}, "1h")[0] == (
        "partial_unverified"
    )


def test_range_coverage_and_evidence_membership_fail_closed(tmp_path):
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    end = pd.Timestamp("2026-01-01T04:00:00Z")
    assert _covers_range([(start, start + pd.Timedelta(hours=2)), (start + pd.Timedelta(hours=3), end)], start, end, "1h")
    assert not _covers_range([(start, start + pd.Timedelta(hours=1)), (end, end)], start, end, "1h")
    assert not _covers_range([(start, start + pd.Timedelta(hours=2))], start, end, "1h")
    configured = _DatasetEvidence(
        "d",
        "partial",
        (_Evidence("e", "p", None, None, tmp_path, "not-registered", "a" * 64),),
    )
    with pytest.raises(MarketDataError, match="not_registered"):
        _validate_dataset_evidence_membership(("registered",), configured)


def test_panel_aggregation_requires_uniform_verified_meaning():
    assert _aggregate_panel_status(["verified_open_time", "verified_open_time"]) == (
        "verified_open_time"
    )
    assert _aggregate_panel_status(["verified_open_time", "unknown"]) == "unverified"
    with pytest.raises(MarketDataError, match="conflicting_panel"):
        _aggregate_panel_status(["verified_open_time", "verified_close_time"])


def test_evidence_config_rejects_hash_drift_path_escape_and_missing_dataset(tmp_path):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("evidence", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text("{}", encoding="utf-8")
    registry = SimpleNamespace(entries=[SimpleNamespace(dataset_id="d1")])
    base = {
        "schema_version": 1,
        "policy_version": 1,
        "producers": [
            {
                "producer_id": "unknown",
                "provider": "unknown",
                "market_type": "spot",
                "timestamp_field": "unverified",
                "claimed_meaning": "unverified",
                "requires_probe": False,
            }
        ],
        "datasets": [
            {
                "dataset_id": "d1",
                "lineage_coverage": "partial",
                "evidence": [
                    {
                        "evidence_id": "e1",
                        "producer_id": "unknown",
                        "covered_from": None,
                        "covered_to": None,
                        "repo_relative_artifact": "artifact.txt",
                        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    }
                ],
            }
        ],
    }
    config = tmp_path / "config.yaml"
    _write_json_as_yaml(config, base)
    loaded = _load_evidence_config(config, registry)
    assert loaded.datasets["d1"].lineage_coverage == "partial"

    base["datasets"][0]["evidence"][0]["artifact_sha256"] = "0" * 64
    _write_json_as_yaml(config, base)
    with pytest.raises(MarketDataError, match="evidence_hash_mismatch"):
        _load_evidence_config(config, registry)
    base["datasets"][0]["evidence"][0]["artifact_sha256"] = hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()
    base["datasets"][0]["evidence"][0]["repo_relative_artifact"] = "../escape.txt"
    _write_json_as_yaml(config, base)
    with pytest.raises(ValueError, match="escapes"):
        _load_evidence_config(config, registry)
    base["datasets"] = []
    _write_json_as_yaml(config, base)
    with pytest.raises(ValueError, match="every registry dataset"):
        _load_evidence_config(config, registry)


def test_low_level_evidence_validators_and_probe_producer_rejections(tmp_path):
    with pytest.raises(ValueError, match="UTC timestamp"):
        _optional_timestamp(123, "covered_from")
    with pytest.raises(ValueError, match="UTC timestamp"):
        _optional_timestamp("not-a-date", "covered_from")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _require_sha256("BAD", "hash")

    producer = _Producer(
        "bad",
        "binance",
        "spot",
        "ts",
        "bar_open_time",
        True,
        tmp_path,
        "contract",
        "a" * 64,
    )
    config = _EvidenceConfig({}, {"bad": producer}, {})
    with pytest.raises(MarketDataError, match="invalid_probe_producer"):
        _assess_producers(config, {"probe_status": "verified_open_time_public_probe_only"})


def test_real_registry_semantics_audit_matches_frozen_statuses_and_is_deterministic(tmp_path):
    probe = _write_probe(tmp_path / "probe")
    report_path = probe.export_paths["report"]

    first = audit_timestamp_semantics(
        "config.datasets.example.yaml",
        "config.dataset-panels.example.yaml",
        "config.timestamp-semantics.example.yaml",
        report_path,
        tmp_path / "first",
    )
    second = audit_timestamp_semantics(
        "config.datasets.example.yaml",
        "config.dataset-panels.example.yaml",
        "config.timestamp-semantics.example.yaml",
        report_path,
        tmp_path / "second",
    )

    assert first.report == second.report
    statuses = {row["dataset_id"]: row["status"] for row in first.report["datasets"]}
    assert statuses == {
        "btc_usdt_1h_v1": "partial_unverified",
        "btc_usdt_4h_v1": "partial_unverified",
        "eth_usdt_1h_v1": "unknown",
        "eth_usdt_4h_v1": "partial_unverified",
        "sol_usdt_1h_v1": "unknown",
        "sol_usdt_4h_v1": "partial_unverified",
    }
    assert {row["status"] for row in first.report["panels"]} == {"unverified"}
    producer = next(
        row for row in first.report["producers"] if row["producer_id"] == "okx_public_candles_v1"
    )
    assert producer["status"] == "verified_open_time"
    assert first.report["readiness_changed"] is False
    assert first.report["automatic_factor_approval"] is False
    assert "producer_count: 3" in format_timestamp_semantics_audit(first)
    for name in ("report", "producers", "datasets", "panels"):
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()


def test_timestamp_semantics_cli_consumes_frozen_probe_without_network(tmp_path):
    probe = _write_probe(tmp_path / "probe")
    output = tmp_path / "output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "audit-timestamp-semantics",
            "--registry",
            "config.datasets.example.yaml",
            "--panels-config",
            "config.dataset-panels.example.yaml",
            "--evidence-config",
            "config.timestamp-semantics.example.yaml",
            "--probe-report",
            probe.export_paths["report"],
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "dataset_count: 6" in completed.stdout
    assert "panel_count: 2" in completed.stdout
    assert len(list(output.glob("timestamp-semantics.*"))) == 4


def _write_probe(directory: Path):
    return capture_okx_timestamp_probe(
        "BTC-USDT",
        "1m",
        directory,
        fetcher=lambda _: _valid_response(),
        received_at=RECEIVED_AT,
    )


def _valid_response() -> bytes:
    rows = [
        _row("12:00", "0"),
        _row("11:59", "1"),
        _row("11:58", "1"),
    ]
    return json.dumps({"code": "0", "msg": "", "data": rows}, separators=(",", ":")).encode()


def _row(hhmm: str, confirm: str) -> list[str]:
    return [str(_ts(hhmm)), "100", "101", "99", "100.5", "1", "100.5", "100.5", confirm]


def _ts(hhmm: str) -> int:
    return int(pd.Timestamp(f"2026-08-02T{hhmm}:00Z").timestamp() * 1000)


def _mutate(payload: bytes, **changes) -> bytes:
    parsed = json.loads(payload)
    parsed.update(changes)
    return json.dumps(parsed, separators=(",", ":")).encode()


def _mutate_row(payload: bytes, row_index: int, mutator) -> bytes:
    parsed = json.loads(payload)
    parsed["data"][row_index] = mutator(parsed["data"][row_index])
    return json.dumps(parsed, separators=(",", ":")).encode()


def _mutate_cell(payload: bytes, row_index: int, cell_index: int, value: str) -> bytes:
    return _mutate_row(
        payload,
        row_index,
        lambda row: row[:cell_index] + [value] + row[cell_index + 1 :],
    )


def _write_json_as_yaml(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
