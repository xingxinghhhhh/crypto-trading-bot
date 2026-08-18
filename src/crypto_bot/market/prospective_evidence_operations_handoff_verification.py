from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_manifest import (
    validate_prospective_evidence_operations_handoff_manifest,
)


VERIFICATION_STATUS = "consumer_handoff_verified"
VERIFICATION_FAILURE_STATUS = "consumer_handoff_rejected"
VERIFICATION_FIELDS = (
    "verification_status",
    "safe_to_consume_read_only",
    "handoff_manifest_identity",
    "bundle_admission_identity",
    "bundle_identity",
    "source_snapshot_identity",
    "governed_stage",
    "blocking_gate",
    "next_legal_action",
    "current_samples",
    "sample_threshold",
    "remaining_samples",
    "append_authorization_ready",
    "economic_authorized",
    "pnl_authorized",
    "paper_authorized",
    "live_authorized",
    "consumer_action_authorized",
    "state_mutation_authorized",
)


def verify_prospective_evidence_operations_handoff_manifest(
    handoff_manifest: str | Path,
    bundle_admission: str | Path,
    operations_bundle: str | Path,
) -> dict[str, Any]:
    """Verify a handoff manifest as a read-only consumer input.

    The verification gate deliberately delegates lineage, content-addressed
    integrity, currentness, and projection checks to the existing public
    validators.  It only asserts the frozen consumer boundary after those
    validators have returned a validated manifest.
    """

    manifest = validate_prospective_evidence_operations_handoff_manifest(
        handoff_manifest,
        bundle_admission,
        operations_bundle,
    )
    if manifest.get("status") != "handoff_manifest_ready":
        raise MarketDataError("operations handoff manifest is not current and readable")
    required_true = ("handoff_manifest_materialized", "consumer_readable")
    if any(manifest.get(field) is not True for field in required_true):
        raise MarketDataError("operations handoff manifest consumer boundary is not readable")
    required_false = (
        "consumer_action_authorized",
        "state_mutation_authorized",
        "append_authorization_ready",
        "economic_authorized",
        "pnl_authorized",
        "paper_authorized",
        "live_authorized",
    )
    if any(manifest.get(field) is not False for field in required_false):
        raise MarketDataError("operations handoff manifest authorization escalation")

    result = {
        "verification_status": VERIFICATION_STATUS,
        "safe_to_consume_read_only": True,
        "handoff_manifest_identity": manifest["manifest_sha256"],
        "bundle_admission_identity": manifest["bundle_currentness_admission_identity"],
        "bundle_identity": manifest["bundle_sha256"],
        "source_snapshot_identity": manifest["source_operations_snapshot_identity"],
        "governed_stage": manifest["governed_stage"],
        "blocking_gate": manifest["blocking_gate"],
        "next_legal_action": manifest["next_legal_action"],
        "current_samples": manifest["current_samples"],
        "sample_threshold": manifest["sample_threshold"],
        "remaining_samples": manifest["remaining_samples"],
        "append_authorization_ready": manifest["append_authorization_ready"],
        "economic_authorized": manifest["economic_authorized"],
        "pnl_authorized": manifest["pnl_authorized"],
        "paper_authorized": manifest["paper_authorized"],
        "live_authorized": manifest["live_authorized"],
        "consumer_action_authorized": False,
        "state_mutation_authorized": False,
    }
    _validate_result_shape(result)
    return result


def format_handoff_manifest_verification(result: Mapping[str, Any]) -> str:
    """Return deterministic machine-readable verification output."""

    _validate_result_shape(result)
    return json.dumps(
        {field: result[field] for field in VERIFICATION_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_result_shape(result: Mapping[str, Any]) -> None:
    if tuple(sorted(result)) != tuple(sorted(VERIFICATION_FIELDS)):
        raise MarketDataError("operations handoff verification result schema mismatch")
    if result.get("verification_status") != VERIFICATION_STATUS:
        raise MarketDataError("operations handoff verification status mismatch")
    if result.get("safe_to_consume_read_only") is not True:
        raise MarketDataError("operations handoff verification read-only boundary mismatch")
    for field in (
        "consumer_action_authorized",
        "state_mutation_authorized",
        "append_authorization_ready",
        "economic_authorized",
        "pnl_authorized",
        "paper_authorized",
        "live_authorized",
    ):
        if result.get(field) is not False:
            raise MarketDataError("operations handoff verification authorization escalation")
