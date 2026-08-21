from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, cast

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection import (
    JSONScalar,
    format_governance_fresh_current_operations_handoff_projection,
)
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt import (
    RECEIPT_PREFIX,
    RECEIPT_VERSION,
)
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_verification import (
    verify_governance_fresh_current_operations_handoff_projection,
)


RECEIPT_FIELDS = frozenset(
    {
        "receipt_version",
        "projection_sha256",
        "delivery_admission_identity",
        "handoff_delivery_identity",
        "current_operations_snapshot_identity",
        "epoch_closeout_rollover_identity",
    }
)
PROJECTION_PREFIX = "prospective-evidence-operations-handoff-consumer-projection"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROLLOVER_PREFIX = "prospective-epoch-closeout-rollover."


def verify_governance_fresh_current_operations_handoff_projection_provenance_receipt(
    receipt: str | Path,
    projection: str | Path,
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    epoch_closeout_rollover: str | Path,
) -> dict[str, str]:
    """Verify one persisted provenance receipt without producing artifacts."""

    repo = _repo_root(
        Path(receipt),
        Path(projection),
        Path(delivery_admission),
        Path(handoff_delivery),
        Path(current_operations_snapshot),
        Path(epoch_closeout_rollover),
    )
    persisted_receipt = _load_receipt(receipt, repo)
    verified_projection = verify_governance_fresh_current_operations_handoff_projection(
        projection,
        delivery_admission,
        handoff_delivery,
        current_operations_snapshot,
        epoch_closeout_rollover,
    )
    projection_sha256 = _require_materialized_projection(
        projection, verified_projection, repo
    )
    rollover_identity = _rollover_identity(epoch_closeout_rollover)
    expected_receipt = _build_receipt(
        verified_projection,
        projection_sha256,
        rollover_identity,
    )
    if persisted_receipt != expected_receipt:
        raise MarketDataError("consumer projection provenance receipt mismatch")
    return persisted_receipt


def _load_receipt(path: str | Path, repo: Path) -> dict[str, str]:
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise MarketDataError("consumer projection provenance receipt path symlink")
    resolved = raw_path.resolve()
    artifacts = (repo / "artifacts").resolve()
    if not resolved.is_relative_to(artifacts) or not resolved.is_file():
        raise MarketDataError("consumer projection provenance receipt path mismatch")
    digest = _content_addressed_digest(resolved.name, RECEIPT_PREFIX)
    try:
        raw = resolved.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise MarketDataError("consumer projection provenance receipt bytes read failed") from exc
    if hashlib.sha256(raw).hexdigest() != digest:
        raise MarketDataError("consumer projection provenance receipt identity mismatch")
    try:
        encoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MarketDataError("consumer projection provenance receipt encoding mismatch") from exc
    if (
        not encoded
        or encoded.startswith("\ufeff")
        or "\n" in encoded
        or "\r" in encoded
        or encoded != encoded.strip()
    ):
        raise MarketDataError("consumer projection provenance receipt canonical text mismatch")
    try:
        value: Any = json.loads(encoded)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("consumer projection provenance receipt json invalid") from exc
    if not isinstance(value, dict):
        raise MarketDataError("consumer projection provenance receipt json must be an object")
    if set(value) != RECEIPT_FIELDS or any(not isinstance(item, str) for item in value.values()):
        raise MarketDataError("consumer projection provenance receipt schema mismatch")
    persisted = cast(dict[str, str], value)
    if persisted["receipt_version"] != RECEIPT_VERSION:
        raise MarketDataError("consumer projection provenance receipt version mismatch")
    canonical = json.dumps(
        persisted, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if raw != canonical:
        raise MarketDataError("consumer projection provenance receipt canonical bytes mismatch")
    return persisted


def _require_materialized_projection(
    path: str | Path,
    projection: Mapping[str, JSONScalar],
    repo: Path,
) -> str:
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise MarketDataError("consumer projection materialized path symlink")
    resolved = raw_path.resolve()
    artifacts = (repo / "artifacts").resolve()
    if not resolved.is_relative_to(artifacts) or not resolved.is_file():
        raise MarketDataError("consumer projection materialized path mismatch")
    digest = _content_addressed_digest(resolved.name, PROJECTION_PREFIX)
    try:
        raw = resolved.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise MarketDataError("consumer projection materialized bytes read failed") from exc
    expected = format_governance_fresh_current_operations_handoff_projection(projection).encode(
        "utf-8"
    )
    if raw != expected:
        raise MarketDataError("consumer projection materialized bytes mismatch")
    actual = hashlib.sha256(raw).hexdigest()
    if digest != actual:
        raise MarketDataError("consumer projection materialized identity mismatch")
    return actual


def _content_addressed_digest(filename: str, prefix: str) -> str:
    marker = f"{prefix}."
    if not filename.startswith(marker) or not filename.endswith(".json"):
        raise MarketDataError("content-addressed filename mismatch")
    digest = filename[len(marker) : -len(".json")]
    if not _SHA256.fullmatch(digest):
        raise MarketDataError("content-addressed filename mismatch")
    return digest


def _rollover_identity(path: str | Path) -> str:
    filename = Path(path).resolve().name
    marker = _ROLLOVER_PREFIX
    if not filename.startswith(marker) or not filename.endswith(".json"):
        raise MarketDataError("consumer projection rollover identity mismatch")
    digest = filename[len(marker) : -len(".json")]
    if not _SHA256.fullmatch(digest):
        raise MarketDataError("consumer projection rollover identity mismatch")
    return digest


def _build_receipt(
    projection: Mapping[str, JSONScalar],
    projection_sha256: str,
    rollover_identity: str,
) -> dict[str, str]:
    identity_fields = (
        "delivery_admission_identity",
        "handoff_delivery_identity",
        "current_operations_snapshot_identity",
    )
    identities: dict[str, str] = {}
    for field in identity_fields:
        value = projection.get(field)
        if not isinstance(value, str):
            raise MarketDataError("consumer projection provenance receipt field type mismatch")
        identities[field] = value
    return {
        "receipt_version": RECEIPT_VERSION,
        "projection_sha256": projection_sha256,
        **identities,
        "epoch_closeout_rollover_identity": rollover_identity,
    }


def _repo_root(*paths: Path) -> Path:
    for path in (Path.cwd(), *paths):
        resolved = path.resolve()
        for parent in (resolved, *resolved.parents):
            if (parent / "pyproject.toml").is_file() and (
                parent / "src" / "crypto_bot"
            ).is_dir():
                return parent
    raise MarketDataError("consumer projection provenance receipt verification repo root missing")
