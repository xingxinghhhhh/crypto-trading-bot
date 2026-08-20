from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, TypeAlias

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_freshness import (
    load_governance_fresh_current_operations_handoff,
)


JSONScalar: TypeAlias = str | int | bool
PROJECTION_VERSION = "verified_current_operations_handoff_consumer_projection_v1"
PROJECTION_FIELDS = (
    "projection_version",
    "delivery_admission_identity",
    "handoff_delivery_identity",
    "source_operations_snapshot_identity",
    "current_operations_snapshot_identity",
    "governance_freshness_verified",
    "safe_to_consume_current_read_only",
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
_STRING_FIELDS = {
    "projection_version",
    "delivery_admission_identity",
    "handoff_delivery_identity",
    "source_operations_snapshot_identity",
    "current_operations_snapshot_identity",
    "governed_stage",
    "blocking_gate",
    "next_legal_action",
}
_INTEGER_FIELDS = {"current_samples", "sample_threshold", "remaining_samples"}
_BOOLEAN_FIELDS = {
    "governance_freshness_verified",
    "safe_to_consume_current_read_only",
    "append_authorization_ready",
    "economic_authorized",
    "pnl_authorized",
    "paper_authorized",
    "live_authorized",
    "consumer_action_authorized",
    "state_mutation_authorized",
}


def project_governance_fresh_current_operations_handoff(
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    epoch_closeout_rollover: str | Path,
) -> dict[str, JSONScalar]:
    """Project the already verified handoff into the stable consumer contract."""

    handoff = load_governance_fresh_current_operations_handoff(
        delivery_admission,
        handoff_delivery,
        current_operations_snapshot,
        epoch_closeout_rollover,
    )
    projection: dict[str, JSONScalar] = {
        "projection_version": PROJECTION_VERSION,
        "delivery_admission_identity": handoff.delivery_admission_identity,
        "handoff_delivery_identity": handoff.handoff_delivery_identity,
        "source_operations_snapshot_identity": handoff.source_operations_snapshot_identity,
        "current_operations_snapshot_identity": handoff.current_operations_snapshot_identity,
        "governance_freshness_verified": True,
        "safe_to_consume_current_read_only": handoff.safe_to_consume_current_read_only,
        "governed_stage": handoff.governed_stage,
        "blocking_gate": handoff.blocking_gate,
        "next_legal_action": handoff.next_legal_action,
        "current_samples": handoff.current_samples,
        "sample_threshold": handoff.sample_threshold,
        "remaining_samples": handoff.remaining_samples,
        "append_authorization_ready": handoff.append_authorization_ready,
        "economic_authorized": handoff.economic_authorized,
        "pnl_authorized": handoff.pnl_authorized,
        "paper_authorized": handoff.paper_authorized,
        "live_authorized": handoff.live_authorized,
        "consumer_action_authorized": handoff.consumer_action_authorized,
        "state_mutation_authorized": handoff.state_mutation_authorized,
    }
    _validate_projection(projection)
    return projection


def format_governance_fresh_current_operations_handoff_projection(
    projection: Mapping[str, JSONScalar],
) -> str:
    """Serialize one exact projection as canonical JSON without a newline."""

    _validate_projection(projection)
    return json.dumps(
        dict(projection),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_projection(projection: Mapping[str, JSONScalar]) -> None:
    if set(projection) != set(PROJECTION_FIELDS):
        raise MarketDataError("consumer projection schema mismatch")
    for field in _STRING_FIELDS:
        if not isinstance(projection.get(field), str):
            raise MarketDataError(f"consumer projection field type:{field}")
    for field in _INTEGER_FIELDS:
        value = projection.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise MarketDataError(f"consumer projection field type:{field}")
    for field in _BOOLEAN_FIELDS:
        if not isinstance(projection.get(field), bool):
            raise MarketDataError(f"consumer projection field type:{field}")
