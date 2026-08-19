from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission import (
    validate_prospective_evidence_operations_handoff_delivery_admission,
)


VERIFICATION_STATUS = "current_delivery_handoff_verified"
VERIFICATION_FIELDS = (
    "verification_status",
    "safe_to_consume_current_read_only",
    "delivery_admission_identity",
    "handoff_delivery_identity",
    "source_operations_snapshot_identity",
    "current_operations_snapshot_identity",
    "delivery_currentness_admitted",
    "delivery_stale",
    "delivery_action_authorized",
    "state_mutation_authorized",
    "network_activity_performed",
    "new_samples_counted",
)


def verify_prospective_evidence_operations_handoff_delivery(
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
) -> dict[str, Any]:
    """Verify the final delivery read barrier without creating artifacts.

    All lineage, currentness, projection, and package-closure checks remain in
    the existing admission validator. This function only evaluates the final
    read-only safety predicate over its already validated report.
    """

    report = validate_prospective_evidence_operations_handoff_delivery_admission(
        delivery_admission,
        handoff_delivery=handoff_delivery,
        current_operations_snapshot=current_operations_snapshot,
    )
    _require_verified_admission(report)
    result = {
        "verification_status": VERIFICATION_STATUS,
        "safe_to_consume_current_read_only": True,
        "delivery_admission_identity": report["admission_sha256"],
        "handoff_delivery_identity": report["delivery_identity"],
        "source_operations_snapshot_identity": report[
            "delivery_source_snapshot_identity"
        ],
        "current_operations_snapshot_identity": report["current_snapshot_identity"],
        "delivery_currentness_admitted": True,
        "delivery_stale": False,
        "delivery_action_authorized": False,
        "state_mutation_authorized": False,
        "network_activity_performed": False,
        "new_samples_counted": 0,
    }
    _validate_result_shape(result)
    return result


def format_prospective_evidence_operations_handoff_delivery_verification(
    result: Mapping[str, Any],
) -> str:
    _validate_result_shape(result)
    return json.dumps(
        {field: result[field] for field in VERIFICATION_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _require_verified_admission(report: Mapping[str, Any]) -> None:
    required = {
        "status": "current_delivery_admitted",
        "delivery_currentness_admitted": True,
        "delivery_stale": False,
        "safe_to_consume_current_read_only": True,
        "delivery_action_authorized": False,
        "state_mutation_authorized": False,
        "network_activity_performed": False,
        "new_samples_counted": 0,
    }
    if any(report.get(field) != expected for field, expected in required.items()):
        raise MarketDataError("current delivery handoff verification rejected")


def _validate_result_shape(result: Mapping[str, Any]) -> None:
    if tuple(sorted(result)) != tuple(sorted(VERIFICATION_FIELDS)):
        raise MarketDataError("current delivery handoff verification schema mismatch")
    if result.get("verification_status") != VERIFICATION_STATUS:
        raise MarketDataError("current delivery handoff verification status mismatch")
    for field in (
        "safe_to_consume_current_read_only",
        "delivery_currentness_admitted",
    ):
        if result.get(field) is not True:
            raise MarketDataError("current delivery handoff verification safety mismatch")
    for field in (
        "delivery_stale",
        "delivery_action_authorized",
        "state_mutation_authorized",
        "network_activity_performed",
    ):
        if result.get(field) is not False:
            raise MarketDataError("current delivery handoff verification safety mismatch")
    if result.get("new_samples_counted") != 0:
        raise MarketDataError("current delivery handoff verification sample mutation")
