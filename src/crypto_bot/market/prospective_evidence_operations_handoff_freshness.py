from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_read_model import (
    VerifiedCurrentOperationsHandoff,
    load_verified_current_operations_handoff,
)
from crypto_bot.market.prospective_evidence_operations_snapshot import (
    validate_prospective_evidence_operations_snapshot,
)
from crypto_bot.prospective_epoch_closeout_rollover import (
    validate_prospective_epoch_closeout_rollover,
)


def load_governance_fresh_current_operations_handoff(
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    epoch_closeout_rollover: str | Path,
) -> VerifiedCurrentOperationsHandoff:
    """Load a current handoff only when no validated governance evidence supersedes it.

    The existing delivery/read-model validators remain the source of truth for
    artifact currentness.  This read barrier adds only the explicit,
    content-addressed epoch closeout/rollover supersession check; it never
    discovers or creates governance artifacts and never consults wall-clock
    time.
    """

    current_snapshot = validate_prospective_evidence_operations_snapshot(
        current_operations_snapshot
    )
    handoff = load_verified_current_operations_handoff(
        delivery_admission,
        handoff_delivery,
        current_operations_snapshot,
    )
    rollover = validate_prospective_epoch_closeout_rollover(epoch_closeout_rollover)
    _validate_rollover_binding(current_snapshot, rollover)
    action = rollover.get("action")
    if action == "hold":
        return handoff
    if action in {"rollover_next_window", "transition_eligible"}:
        raise MarketDataError(
            "current operations handoff superseded by epoch governance progression"
        )
    raise MarketDataError("unknown epoch closeout rollover action")


def _validate_rollover_binding(
    current_snapshot: Mapping[str, Any], rollover: Mapping[str, Any]
) -> None:
    parent_reports = current_snapshot.get("identity", {}).get("parent_reports")
    if not isinstance(parent_reports, Mapping):
        raise MarketDataError("governance freshness snapshot parent reports missing")
    epoch_parent = parent_reports.get("epoch_assembly")
    identity = rollover.get("identity")
    if not isinstance(epoch_parent, Mapping) or not isinstance(identity, Mapping):
        raise MarketDataError("governance freshness epoch binding missing")
    snapshot_assembly = epoch_parent.get("sha256")
    rollover_assembly = identity.get("assembly_sha256")
    if (
        not isinstance(snapshot_assembly, str)
        or not isinstance(rollover_assembly, str)
        or snapshot_assembly != rollover_assembly
    ):
        raise MarketDataError("governance freshness epoch assembly mismatch")
