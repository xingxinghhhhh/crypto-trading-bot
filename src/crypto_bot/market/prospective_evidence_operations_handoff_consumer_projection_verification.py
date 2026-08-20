from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection import (
    JSONScalar,
    format_governance_fresh_current_operations_handoff_projection,
    project_governance_fresh_current_operations_handoff,
)


def verify_governance_fresh_current_operations_handoff_projection(
    projection: str | Path,
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    epoch_closeout_rollover: str | Path,
) -> dict[str, JSONScalar]:
    """Replay one persisted projection against its explicit evidence inputs."""

    persisted = _load_persisted_projection(projection)
    recomputed = project_governance_fresh_current_operations_handoff(
        delivery_admission,
        handoff_delivery,
        current_operations_snapshot,
        epoch_closeout_rollover,
    )
    if persisted != recomputed:
        raise MarketDataError("consumer projection provenance mismatch")
    return persisted


def _load_persisted_projection(path: str | Path) -> dict[str, JSONScalar]:
    try:
        encoded = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MarketDataError("consumer projection json read failed") from exc

    if encoded.endswith("\n"):
        encoded = encoded[:-1]
        if encoded.endswith("\n"):
            raise MarketDataError("consumer projection newline mismatch")
    if encoded.startswith("\ufeff"):
        raise MarketDataError("consumer projection canonical text mismatch")
    if not encoded or "\n" in encoded or "\r" in encoded:
        raise MarketDataError("consumer projection canonical text mismatch")

    try:
        value: Any = json.loads(encoded)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("consumer projection json invalid") from exc
    if not isinstance(value, dict):
        raise MarketDataError("consumer projection json must be an object")

    persisted = cast(dict[str, JSONScalar], value)
    try:
        canonical = format_governance_fresh_current_operations_handoff_projection(
            persisted
        )
    except (TypeError, ValueError) as exc:
        raise MarketDataError("consumer projection schema or type mismatch") from exc
    if canonical != encoded:
        raise MarketDataError("consumer projection canonical text mismatch")
    return persisted
