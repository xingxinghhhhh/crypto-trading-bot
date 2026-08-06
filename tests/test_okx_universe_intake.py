from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_universe_intake import (
    FOUR_HOURS_MS,
    _compute_end_open_ms,
    _evaluate_instrument_snapshot,
    _load_contract,
    _load_policy,
    _load_semantics_report,
    _fetch,
    _optional_millisecond_timestamp,
    _parse_okx_payload,
    _replay_history_bundle,
    _require_sha256,
    _timestamp_ms,
    _validate_history_response,
    audit_okx_universe_intake,
    capture_okx_universe_intake,
    validate_okx_universe_intake,
    format_okx_universe_audit,
    format_okx_universe_capture,
)


RECEIVED_AT = datetime(2022, 1, 2, 0, 0, 5, tzinfo=timezone.utc)
START_MS = int(pd.Timestamp("2022-01-01T00:00:00Z").timestamp() * 1000)
END_MS = int(pd.Timestamp("2022-01-01T20:00:00Z").timestamp() * 1000)
POLICY = Path("config.okx-universe-intake.example.yaml")
CONTRACT = Path("docs/evidence/okx_public_instruments_history_contract_v1.json")


def test_capture_and_offline_audit_are_deterministic_and_do_not_modify_registry(tmp_path):
    registry = _write_registry(tmp_path / "repo")
    semantics = _write_semantics(tmp_path / "semantics")
    registry_before = registry.read_bytes()
    fetcher = _FixtureFetcher()

    capture = capture_okx_universe_intake(
        registry,
        POLICY,
        semantics,
        tmp_path / "capture",
        fetcher=fetcher,
        received_at=RECEIVED_AT,
        request_interval_seconds=0,
    )
    first = audit_okx_universe_intake(capture.export_paths["report"], tmp_path / "audit-one")
    second = audit_okx_universe_intake(capture.export_paths["report"], tmp_path / "audit-two")
    validated = validate_okx_universe_intake(
        capture.export_paths["report"],
        first.export_paths["report"],
    )

    selected = sorted(
        ["AAA-USDT", "BBB-USDT", "CCC-USDT"],
        key=lambda item: hashlib.sha256(
            f"okx-convenience-universe-v1|{item}".encode()
        ).hexdigest(),
    )
    assert capture.report["identity"]["snapshot"]["selected_inst_ids"] == selected
    assert capture.report["identity"]["claims"] == {
        "universe_kind": "current_okx_live_convenience_snapshot",
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "profitability_evidence": False,
        "strategy_approval": False,
    }
    assert len(capture.report["identity"]["histories"]) == 3
    assert all(history["bar_count"] == 6 for history in capture.report["identity"]["histories"])
    assert all(history["timestamp_semantics"] == "verified_open_time" for history in capture.report["identity"]["histories"])
    assert first.report == second.report
    assert validated.intake_report == first.report
    assert len(validated.capture.datasets) == 3
    for name in first.export_paths:
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()
    candidates = yaml.safe_load(Path(first.export_paths["registry_candidates"]).read_text())
    assert candidates["automatic_registry_merge"] is False
    assert len(candidates["datasets"]) == 3
    assert registry.read_bytes() == registry_before
    assert "private_api_used: false" in format_okx_universe_capture(capture)
    assert "historical_point_in_time_membership: false" in format_okx_universe_audit(first)


def test_capture_is_idempotent_and_content_addressed_collision_fails(tmp_path):
    registry = _write_registry(tmp_path / "repo")
    semantics = _write_semantics(tmp_path / "semantics")
    output = tmp_path / "capture"
    kwargs = dict(
        fetcher=_FixtureFetcher(),
        received_at=RECEIVED_AT,
        request_interval_seconds=0,
    )
    first = capture_okx_universe_intake(registry, POLICY, semantics, output, **kwargs)
    second = capture_okx_universe_intake(registry, POLICY, semantics, output, **kwargs)
    assert first.report == second.report

    report_path = Path(first.export_paths["report"])
    report_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(MarketDataError, match="content_addressed_artifact_collision"):
        capture_okx_universe_intake(registry, POLICY, semantics, output, **kwargs)


def test_capture_failure_never_commits_final_marker(tmp_path):
    registry = _write_registry(tmp_path / "repo")
    semantics = _write_semantics(tmp_path / "semantics")
    output = tmp_path / "capture"
    fetcher = _FixtureFetcher(fail_inst_id="BBB-USDT")

    with pytest.raises(MarketDataError, match="public_fetch_failed"):
        capture_okx_universe_intake(
            registry,
            POLICY,
            semantics,
            output,
            fetcher=fetcher,
            received_at=RECEIVED_AT,
            request_interval_seconds=0,
        )

    assert not list(output.glob("okx-universe-capture.*.json")) if output.exists() else True


def test_effective_continuous_start_filters_and_selection_are_frozen():
    policy = _load_policy(POLICY)
    instruments = [
        _instrument("OLD", list_time="1600000000000", cont_time=""),
        _instrument("CALL", list_time="1600000000000", cont_time="1640995200001"),
        _instrument("USDC"),
        _instrument("BTC"),
        _instrument("ABC3L"),
        _instrument("WRONG", category="3"),
        _instrument("PRE", state="preopen"),
        _instrument("BAD", rule_type="pre_market"),
        _instrument("EUR", quote="EUR"),
    ]
    payload = _okx_payload(instruments)
    decisions, selected = _evaluate_instrument_snapshot(payload, policy, history_start_ms=START_MS)
    by_id = {row["inst_id"]: row for row in decisions}

    assert selected == ["OLD-USDT"]
    assert by_id["CALL-USDT"]["effective_continuous_start"].startswith("2022-01-01")
    assert "continuous_start_after_history_start" in by_id["CALL-USDT"]["exclusion_reasons"]
    assert "stablecoin_base_denied" in by_id["USDC-USDT"]["exclusion_reasons"]
    assert "existing_base_excluded" in by_id["BTC-USDT"]["exclusion_reasons"]
    assert "leveraged_token_pattern" in by_id["ABC3L-USDT"]["exclusion_reasons"]
    assert "inst_category_not_crypto" in by_id["WRONG-USDT"]["exclusion_reasons"]
    assert "state_not_live" in by_id["PRE-USDT"]["exclusion_reasons"]
    assert "rule_type_not_normal" in by_id["BAD-USDT"]["exclusion_reasons"]
    assert "quote_not_usdt" in by_id["EUR-EUR"]["exclusion_reasons"]


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda rows: [rows[0][:-1]], "invalid_history_row"),
        (lambda rows: [[*rows[0][:8], "0"]], "unconfirmed_history_bar"),
        (lambda rows: [[*rows[0][:1], "NaN", *rows[0][2:]]], "invalid_history_value"),
        (lambda rows: [[str(int(rows[0][0]) + 1), *rows[0][1:]]], "off_grid"),
        (lambda rows: [rows[0], rows[0]], "not_strictly_descending"),
    ],
)
def test_history_page_validation_fails_closed(mutator, expected):
    rows = [_history_row(END_MS)]
    with pytest.raises(MarketDataError, match=expected):
        _validate_history_response(
            _okx_payload(mutator(rows)),
            inst_id="AAA-USDT",
            cursor=END_MS + FOUR_HOURS_MS,
            previous_oldest=None,
        )


def test_history_page_rejects_cursor_boundary_and_nonadvancing_page():
    with pytest.raises(MarketDataError, match="cursor_not_exclusive"):
        _validate_history_response(
            _okx_payload([_history_row(END_MS + FOUR_HOURS_MS)]),
            inst_id="AAA-USDT",
            cursor=END_MS + FOUR_HOURS_MS,
            previous_oldest=None,
        )
    with pytest.raises(MarketDataError, match="cursor_did_not_advance"):
        _validate_history_response(
            _okx_payload([_history_row(END_MS)]),
            inst_id="AAA-USDT",
            cursor=END_MS + FOUR_HOURS_MS,
            previous_oldest=END_MS,
        )


def test_history_bundle_replay_detects_response_hash_and_request_drift(tmp_path):
    record = {
        "page_index": 0,
        "endpoint": "https://www.okx.com/api/v5/market/history-candles",
        "request_params": {
            "instId": "AAA-USDT",
            "bar": "4H",
            "after": str(END_MS + FOUR_HOURS_MS),
            "limit": "300",
        },
        "response_body": _okx_payload(_complete_rows()).decode(),
        "response_sha256": "0" * 64,
    }
    bundle = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(MarketDataError, match="response_hash_mismatch"):
        _replay_history_bundle(bundle, "AAA-USDT", START_MS, END_MS, _load_policy(POLICY))

    record["response_sha256"] = hashlib.sha256(record["response_body"].encode()).hexdigest()
    record["request_params"]["after"] = "wrong"
    bundle = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(MarketDataError, match="request_mismatch"):
        _replay_history_bundle(bundle, "AAA-USDT", START_MS, END_MS, _load_policy(POLICY))


def test_audit_detects_raw_bundle_csv_capture_identity_and_path_tampering(tmp_path):
    registry = _write_registry(tmp_path / "repo")
    semantics = _write_semantics(tmp_path / "semantics")
    capture = capture_okx_universe_intake(
        registry,
        POLICY,
        semantics,
        tmp_path / "capture",
        fetcher=_FixtureFetcher(),
        received_at=RECEIVED_AT,
        request_interval_seconds=0,
    )
    report_path = Path(capture.export_paths["report"])
    history_path = Path(capture.export_paths["AAA-USDT_history"])
    original_history = history_path.read_bytes()
    history_path.write_bytes(b"tampered")
    with pytest.raises(MarketDataError, match="bundle_hash_mismatch"):
        audit_okx_universe_intake(report_path, tmp_path / "audit")
    history_path.write_bytes(original_history)

    csv_path = Path(capture.export_paths["AAA-USDT_csv"])
    original_csv = csv_path.read_bytes()
    csv_path.write_bytes(original_csv + b"\n")
    with pytest.raises(MarketDataError, match="csv_replay_mismatch"):
        audit_okx_universe_intake(report_path, tmp_path / "audit")
    csv_path.write_bytes(original_csv)

    report = json.loads(report_path.read_text())
    report["identity"]["histories"][0]["history_bundle"]["filename"] = "../escape"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity_mismatch"):
        audit_okx_universe_intake(report_path, tmp_path / "audit")


def test_semantics_loader_rejects_hash_drift_wrong_status_and_path_escape(tmp_path):
    report_path = _write_semantics(tmp_path)
    report = json.loads(report_path.read_text())
    producers_path = tmp_path / report["artifacts"]["producers"]["filename"]
    producers_path.write_text("drift", encoding="utf-8")
    with pytest.raises(MarketDataError, match="artifact_hash_mismatch"):
        _load_semantics_report(report_path)

    report_path = _write_semantics(tmp_path / "fresh")
    report = json.loads(report_path.read_text())
    report["producers"][0]["status"] = "unknown"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError, match="not_verified"):
        _load_semantics_report(report_path)

    report_path = _write_semantics(tmp_path / "escape")
    report = json.loads(report_path.read_text())
    report["artifacts"]["producers"]["filename"] = "../escape.csv"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError, match="path_escape"):
        _load_semantics_report(report_path)


def test_policy_contract_and_time_boundaries_fail_closed(tmp_path):
    assert _compute_end_open_ms(int(RECEIVED_AT.timestamp() * 1000)) == END_MS
    contract, digest = _load_contract(CONTRACT)
    assert contract["history_candles"]["maximum_limit"] == 300
    assert len(digest) == 64

    policy = yaml.safe_load(POLICY.read_text())
    policy["target_count"] = 4
    invalid_policy = tmp_path / "policy.yaml"
    invalid_policy.write_text(yaml.safe_dump(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="target_count"):
        _load_policy(invalid_policy)

    invalid_contract = tmp_path / "contract.json"
    invalid_contract.write_text(json.dumps(contract | {"scope": "private"}), encoding="utf-8")
    with pytest.raises(MarketDataError, match="contract_mismatch"):
        _load_contract(invalid_contract)


def test_capture_argument_window_and_candidate_count_fail_closed(tmp_path):
    registry = _write_registry(tmp_path / "repo")
    semantics = _write_semantics(tmp_path / "semantics")
    with pytest.raises(ValueError, match="request_interval_seconds"):
        capture_okx_universe_intake(
            registry,
            POLICY,
            semantics,
            tmp_path / "out",
            request_interval_seconds=-1,
        )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        capture_okx_universe_intake(
            registry,
            POLICY,
            semantics,
            tmp_path / "out",
            received_at=datetime(2022, 1, 2),
            fetcher=_FixtureFetcher(),
            request_interval_seconds=0,
        )
    with pytest.raises(MarketDataError, match="history_window_empty"):
        capture_okx_universe_intake(
            registry,
            POLICY,
            semantics,
            tmp_path / "out",
            received_at=datetime(2021, 1, 2, tzinfo=timezone.utc),
            fetcher=_FixtureFetcher(),
            request_interval_seconds=0,
        )

    def only_two(url: str) -> bytes:
        return _okx_payload([_instrument("AAA"), _instrument("BBB")])

    with pytest.raises(MarketDataError, match="insufficient_eligible"):
        capture_okx_universe_intake(
            registry,
            POLICY,
            semantics,
            tmp_path / "out",
            received_at=RECEIVED_AT,
            fetcher=only_two,
            request_interval_seconds=0,
        )


def test_low_level_public_payload_and_identity_validators_fail_closed():
    with pytest.raises(MarketDataError, match="invalid_response_json"):
        _parse_okx_payload(b"not-json", "fixture")
    with pytest.raises(MarketDataError, match="unsuccessful_response"):
        _parse_okx_payload(_okx_payload([]).replace(b'"0"', b'"1"', 1), "fixture")
    with pytest.raises(MarketDataError, match="must_return_bytes"):
        _fetch(lambda _: "wrong", "https://example.invalid", "fixture")  # type: ignore[arg-type,return-value]
    assert _optional_millisecond_timestamp("") is None
    assert _optional_millisecond_timestamp("not-a-time") is None
    with pytest.raises(ValueError, match="UTC string"):
        _timestamp_ms("2022-01-01")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _require_sha256("BAD", "fixture")


def test_audit_cli_consumes_capture_without_network(tmp_path):
    registry = _write_registry(tmp_path / "repo")
    semantics = _write_semantics(tmp_path / "semantics")
    capture = capture_okx_universe_intake(
        registry,
        POLICY,
        semantics,
        tmp_path / "capture",
        fetcher=_FixtureFetcher(),
        received_at=RECEIVED_AT,
        request_interval_seconds=0,
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "audit-okx-universe-intake",
            "--capture-report",
            capture.export_paths["report"],
            "--output-dir",
            str(tmp_path / "audit"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "dataset_count: 3" in completed.stdout
    assert "automatic_registry_merge: false" in completed.stdout


class _FixtureFetcher:
    def __init__(self, fail_inst_id: str | None = None):
        self.fail_inst_id = fail_inst_id

    def __call__(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.path.endswith("/public/instruments"):
            return _okx_payload(
                [_instrument("AAA"), _instrument("BBB"), _instrument("CCC")]
            )
        params = parse_qs(parsed.query)
        inst_id = params["instId"][0]
        if inst_id == self.fail_inst_id:
            raise OSError("network unavailable")
        assert params == {
            "instId": [inst_id],
            "bar": ["4H"],
            "after": [str(END_MS + FOUR_HOURS_MS)],
            "limit": ["300"],
        }
        return _okx_payload(_complete_rows())


def _instrument(
    base: str,
    *,
    quote: str = "USDT",
    list_time: str = "1600000000000",
    cont_time: str = "",
    state: str = "live",
    rule_type: str = "normal",
    category: str = "1",
) -> dict[str, str]:
    return {
        "instType": "SPOT",
        "instId": f"{base}-{quote}",
        "baseCcy": base,
        "quoteCcy": quote,
        "state": state,
        "ruleType": rule_type,
        "instCategory": category,
        "listTime": list_time,
        "contTdSwTime": cont_time,
    }


def _complete_rows() -> list[list[str]]:
    return [_history_row(value) for value in range(END_MS, START_MS - 1, -FOUR_HOURS_MS)]


def _history_row(timestamp: int) -> list[str]:
    return [str(timestamp), "100", "101", "99", "100.5", "1", "100.5", "100.5", "1"]


def _okx_payload(data) -> bytes:
    return json.dumps({"code": "0", "msg": "", "data": data}, separators=(",", ":")).encode()


def _write_registry(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data = root / "data.csv"
    data.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2022-01-01T00:00:00+00:00,1,2,0.5,1.5,1\n"
        "2022-01-01T04:00:00+00:00,1.5,2,1,1.8,2\n",
        encoding="utf-8",
    )
    registry = {
        "schema_version": 1,
        "datasets": [
            {
                "dataset_id": "fixture_4h_v1",
                "symbol": "FIX/USDT",
                "timeframe": "4h",
                "path": "data.csv",
                "source": {"status": "unknown", "evidence": []},
                "quality": {"validator": "validate_ohlcv_csv"},
                "expected": {},
            }
        ],
    }
    path = root / "datasets.yaml"
    path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    return path


def _write_semantics(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name in ("producers", "datasets", "panels"):
        filename = f"timestamp-semantics.fixture.{name}.csv"
        content = f"{name}\n"
        (root / filename).write_text(content, encoding="utf-8")
        artifacts[name] = {
            "filename": filename,
            "sha256": hashlib.sha256((root / filename).read_bytes()).hexdigest(),
        }
    identity = {"fixture": "semantics"}
    semantics_sha = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    report = {
        "schema_version": 1,
        "semantics_sha256": semantics_sha,
        "assessment_status": "independent_timestamp_semantics_evidence_only",
        "artifacts": artifacts,
        "producers": [
            {
                "producer_id": "okx_public_candles_v1",
                "status": "verified_open_time",
                "contract_sha256": "a" * 64,
                "probe_sha256": "b" * 64,
            }
        ],
    }
    path = root / f"timestamp-semantics.{semantics_sha}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path
