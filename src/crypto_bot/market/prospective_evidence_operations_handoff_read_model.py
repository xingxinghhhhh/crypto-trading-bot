from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_bundle import SOURCE_STATE_FIELDS
from crypto_bot.market.prospective_evidence_operations_handoff_delivery import (
    inspect_prospective_evidence_operations_handoff_delivery,
)
from crypto_bot.market.prospective_evidence_operations_handoff_delivery_verification import (
    verify_prospective_evidence_operations_handoff_delivery,
)


@dataclass(frozen=True, slots=True)
class VerifiedCurrentOperationsHandoff:
    """Immutable, path-free read model for a verified current handoff."""

    delivery_admission_identity: str
    handoff_delivery_identity: str
    source_operations_snapshot_identity: str
    current_operations_snapshot_identity: str
    safe_to_consume_current_read_only: bool
    governed_stage: str
    blocking_gate: str
    next_legal_action: str
    current_samples: int
    sample_threshold: int
    remaining_samples: int
    append_authorization_ready: bool
    economic_authorized: bool
    pnl_authorized: bool
    paper_authorized: bool
    live_authorized: bool
    consumer_action_authorized: bool
    state_mutation_authorized: bool


def load_verified_current_operations_handoff(
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
) -> VerifiedCurrentOperationsHandoff:
    """Load a verified current handoff as an immutable read-only object."""

    verification = verify_prospective_evidence_operations_handoff_delivery(
        delivery_admission,
        handoff_delivery,
        current_operations_snapshot,
    )
    _validate_verification(verification)
    inspection = inspect_prospective_evidence_operations_handoff_delivery(
        handoff_delivery
    )
    if (
        inspection.delivery_identity != verification["handoff_delivery_identity"]
        or inspection.source_operations_snapshot_identity
        != verification["source_operations_snapshot_identity"]
    ):
        raise MarketDataError("verified current operations handoff identity mismatch")
    projection = inspection.source_projection
    _validate_projection(projection)
    return VerifiedCurrentOperationsHandoff(
        delivery_admission_identity=verification["delivery_admission_identity"],
        handoff_delivery_identity=verification["handoff_delivery_identity"],
        source_operations_snapshot_identity=verification[
            "source_operations_snapshot_identity"
        ],
        current_operations_snapshot_identity=verification[
            "current_operations_snapshot_identity"
        ],
        safe_to_consume_current_read_only=verification[
            "safe_to_consume_current_read_only"
        ],
        governed_stage=_string_field(projection, "governed_stage"),
        blocking_gate=_string_field(projection, "blocking_gate"),
        next_legal_action=_string_field(projection, "next_legal_action"),
        current_samples=_int_field(projection, "current_samples"),
        sample_threshold=_int_field(projection, "sample_threshold"),
        remaining_samples=_int_field(projection, "remaining_samples"),
        append_authorization_ready=_bool_field(
            projection, "append_authorization_ready"
        ),
        economic_authorized=_bool_field(projection, "economic_authorized"),
        pnl_authorized=_bool_field(projection, "pnl_authorized"),
        paper_authorized=_bool_field(projection, "paper_authorized"),
        live_authorized=_bool_field(projection, "live_authorized"),
        consumer_action_authorized=False,
        state_mutation_authorized=False,
    )


def _validate_projection(projection: dict[str, Any]) -> None:
    if tuple(projection) != SOURCE_STATE_FIELDS:
        raise MarketDataError("verified current operations handoff projection schema mismatch")
    for field in ("state_changed", "network_activity_performed"):
        if projection.get(field) is not False:
            raise MarketDataError("verified current operations handoff mutation boundary")
    if projection.get("new_samples_counted") != 0:
        raise MarketDataError("verified current operations handoff sample mutation")


def _validate_verification(verification: dict[str, Any]) -> None:
    required = {
        "safe_to_consume_current_read_only": True,
        "delivery_currentness_admitted": True,
        "delivery_stale": False,
        "delivery_action_authorized": False,
        "state_mutation_authorized": False,
        "network_activity_performed": False,
        "new_samples_counted": 0,
    }
    if any(verification.get(field) != expected for field, expected in required.items()):
        raise MarketDataError("verified current operations handoff verification boundary")


def _string_field(projection: dict[str, Any], field: str) -> str:
    value = projection.get(field)
    if not isinstance(value, str):
        raise MarketDataError(f"verified current operations handoff field:{field}")
    return value


def _int_field(projection: dict[str, Any], field: str) -> int:
    value = projection.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketDataError(f"verified current operations handoff field:{field}")
    return value


def _bool_field(projection: dict[str, Any], field: str) -> bool:
    value = projection.get(field)
    if not isinstance(value, bool):
        raise MarketDataError(f"verified current operations handoff field:{field}")
    return value
