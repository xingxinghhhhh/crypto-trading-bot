from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection import (
    JSONScalar,
)
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt import (
    RECEIPT_PREFIX,
)
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt_verification import (
    verify_governance_fresh_current_operations_handoff_projection_provenance_receipt,
)


AUDIT_SUMMARY_VERSION = (
    "verified_current_operations_handoff_projection_provenance_audit_summary_v1"
)
AUDIT_SUMMARY_FIELDS = (
    "audit_summary_version",
    "receipt_sha256",
    "projection_sha256",
    "delivery_admission_identity",
    "handoff_delivery_identity",
    "source_operations_snapshot_identity",
    "current_operations_snapshot_identity",
    "epoch_closeout_rollover_identity",
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
_RECEIPT_SHA256 = re.compile(
    rf"^{re.escape(RECEIPT_PREFIX)}\.([0-9a-f]{{64}})\.json$"
)


def build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
    receipt: str | Path,
    projection: str | Path,
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    epoch_closeout_rollover: str | Path,
) -> dict[str, JSONScalar]:
    """Build a read-only operator summary from one verified receipt and projection."""

    verified_receipt = (
        verify_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            receipt,
            projection,
            delivery_admission,
            handoff_delivery,
            current_operations_snapshot,
            epoch_closeout_rollover,
        )
    )
    projection_body = _read_projection_body(projection)
    receipt_sha256 = _receipt_filename_digest(receipt)
    summary: dict[str, JSONScalar] = {
        "audit_summary_version": AUDIT_SUMMARY_VERSION,
        "receipt_sha256": receipt_sha256,
        "projection_sha256": _receipt_string(verified_receipt, "projection_sha256"),
        "delivery_admission_identity": _receipt_string(
            verified_receipt, "delivery_admission_identity"
        ),
        "handoff_delivery_identity": _receipt_string(
            verified_receipt, "handoff_delivery_identity"
        ),
        "source_operations_snapshot_identity": _projection_string(
            projection_body, "source_operations_snapshot_identity"
        ),
        "current_operations_snapshot_identity": _receipt_string(
            verified_receipt, "current_operations_snapshot_identity"
        ),
        "epoch_closeout_rollover_identity": _receipt_string(
            verified_receipt, "epoch_closeout_rollover_identity"
        ),
        "governance_freshness_verified": _projection_bool(
            projection_body, "governance_freshness_verified"
        ),
        "safe_to_consume_current_read_only": _projection_bool(
            projection_body, "safe_to_consume_current_read_only"
        ),
        "governed_stage": _projection_string(projection_body, "governed_stage"),
        "blocking_gate": _projection_string(projection_body, "blocking_gate"),
        "next_legal_action": _projection_string(projection_body, "next_legal_action"),
        "current_samples": _projection_int(projection_body, "current_samples"),
        "sample_threshold": _projection_int(projection_body, "sample_threshold"),
        "remaining_samples": _projection_int(projection_body, "remaining_samples"),
        "append_authorization_ready": _projection_bool(
            projection_body, "append_authorization_ready"
        ),
        "economic_authorized": _projection_bool(projection_body, "economic_authorized"),
        "pnl_authorized": _projection_bool(projection_body, "pnl_authorized"),
        "paper_authorized": _projection_bool(projection_body, "paper_authorized"),
        "live_authorized": _projection_bool(projection_body, "live_authorized"),
        "consumer_action_authorized": _projection_bool(
            projection_body, "consumer_action_authorized"
        ),
        "state_mutation_authorized": _projection_bool(
            projection_body, "state_mutation_authorized"
        ),
    }
    if set(summary) != set(AUDIT_SUMMARY_FIELDS):
        raise MarketDataError("consumer projection provenance audit summary schema mismatch")
    return summary


def _read_projection_body(path: str | Path) -> dict[str, JSONScalar]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        value: Any = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("consumer projection provenance audit summary projection read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("consumer projection provenance audit summary projection must be an object")
    return cast(dict[str, JSONScalar], value)


def _receipt_filename_digest(path: str | Path) -> str:
    match = _RECEIPT_SHA256.fullmatch(Path(path).name)
    if match is None:
        raise MarketDataError("consumer projection provenance audit summary receipt identity mismatch")
    return match.group(1)


def _receipt_string(value: dict[str, str], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str):
        raise MarketDataError(f"consumer projection provenance audit summary receipt field:{field}")
    return item


def _projection_string(value: dict[str, JSONScalar], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str):
        raise MarketDataError(f"consumer projection provenance audit summary projection field:{field}")
    return item


def _projection_int(value: dict[str, JSONScalar], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int):
        raise MarketDataError(f"consumer projection provenance audit summary projection field:{field}")
    return item


def _projection_bool(value: dict[str, JSONScalar], field: str) -> bool:
    item = value.get(field)
    if not isinstance(item, bool):
        raise MarketDataError(f"consumer projection provenance audit summary projection field:{field}")
    return item
