from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection import (
    JSONScalar,
    format_governance_fresh_current_operations_handoff_projection,
)
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_verification import (
    verify_governance_fresh_current_operations_handoff_projection,
)


DEFAULT_OUTPUT_DIR = (
    "artifacts/prospective-evidence-operations-handoff-consumer-projection-provenance-receipt"
)
PROJECTION_PREFIX = "prospective-evidence-operations-handoff-consumer-projection"
RECEIPT_PREFIX = (
    "prospective-evidence-operations-handoff-consumer-projection-provenance-receipt"
)
RECEIPT_VERSION = (
    "verified_current_operations_handoff_consumer_projection_provenance_receipt_v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROLLOVER_PREFIX = "prospective-epoch-closeout-rollover."


@dataclass(frozen=True, slots=True)
class ConsumerProjectionProvenanceReceiptResult:
    receipt: dict[str, str]
    receipt_sha256: str
    receipt_path: str


def materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
    projection: str | Path,
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    epoch_closeout_rollover: str | Path,
    output_dir: str | Path,
) -> ConsumerProjectionProvenanceReceiptResult:
    """Bind one verified materialized projection to its explicit governance lineage."""

    verified_projection = verify_governance_fresh_current_operations_handoff_projection(
        projection,
        delivery_admission,
        handoff_delivery,
        current_operations_snapshot,
        epoch_closeout_rollover,
    )
    repo = _repo_root(
        Path(projection),
        Path(delivery_admission),
        Path(handoff_delivery),
        Path(current_operations_snapshot),
        Path(epoch_closeout_rollover),
    )
    projection_path, projection_sha256 = _require_materialized_projection(
        projection, verified_projection, repo
    )
    del projection_path
    rollover_identity = _rollover_identity(epoch_closeout_rollover)

    receipt = _build_receipt(
        verified_projection,
        projection_sha256,
        rollover_identity,
    )
    content = _canonical_receipt_bytes(receipt)
    receipt_sha256 = hashlib.sha256(content).hexdigest()
    output = _artifact_output_dir(repo, output_dir)
    target = output / f"{RECEIPT_PREFIX}.{receipt_sha256}.json"
    _commit_bytes(target, content)
    return ConsumerProjectionProvenanceReceiptResult(
        receipt=receipt,
        receipt_sha256=receipt_sha256,
        receipt_path=str(target),
    )


def _build_receipt(
    projection: Mapping[str, JSONScalar],
    projection_sha256: str,
    rollover_identity: str,
) -> dict[str, str]:
    values = {
        "receipt_version": RECEIPT_VERSION,
        "projection_sha256": projection_sha256,
        "delivery_admission_identity": projection.get("delivery_admission_identity"),
        "handoff_delivery_identity": projection.get("handoff_delivery_identity"),
        "current_operations_snapshot_identity": projection.get(
            "current_operations_snapshot_identity"
        ),
        "epoch_closeout_rollover_identity": rollover_identity,
    }
    if any(not isinstance(value, str) for value in values.values()):
        raise MarketDataError("consumer projection provenance receipt field type mismatch")
    return {key: value for key, value in values.items() if isinstance(value, str)}


def _require_materialized_projection(
    path: str | Path,
    projection: Mapping[str, JSONScalar],
    repo: Path,
) -> tuple[Path, str]:
    raw = Path(path)
    if raw.is_symlink():
        raise MarketDataError("consumer projection materialized path symlink")
    resolved = raw.resolve()
    artifacts = (repo / "artifacts").resolve()
    if not resolved.is_relative_to(artifacts) or not resolved.is_file():
        raise MarketDataError("consumer projection materialized path mismatch")
    digest = _content_addressed_digest(resolved.name, PROJECTION_PREFIX)
    try:
        content = resolved.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise MarketDataError("consumer projection materialized bytes read failed") from exc
    expected = format_governance_fresh_current_operations_handoff_projection(projection).encode(
        "utf-8"
    )
    if content != expected:
        raise MarketDataError("consumer projection materialized bytes mismatch")
    actual = hashlib.sha256(content).hexdigest()
    if digest != actual:
        raise MarketDataError("consumer projection materialized identity mismatch")
    return resolved, actual


def _content_addressed_digest(filename: str, prefix: str) -> str:
    marker = f"{prefix}."
    if not filename.startswith(marker) or not filename.endswith(".json"):
        raise MarketDataError("consumer projection materialized filename mismatch")
    digest = filename[len(marker) : -len(".json")]
    if not _SHA256.fullmatch(digest):
        raise MarketDataError("consumer projection materialized filename mismatch")
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


def _canonical_receipt_bytes(receipt: Mapping[str, str]) -> bytes:
    if set(receipt) != {
        "receipt_version",
        "projection_sha256",
        "delivery_admission_identity",
        "handoff_delivery_identity",
        "current_operations_snapshot_identity",
        "epoch_closeout_rollover_identity",
    } or any(not isinstance(value, str) for value in receipt.values()):
        raise MarketDataError("consumer projection provenance receipt schema mismatch")
    return json.dumps(
        dict(receipt), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _repo_root(*paths: Path) -> Path:
    for path in (Path.cwd(), *paths):
        resolved = path.resolve()
        for parent in (resolved, *resolved.parents):
            if (parent / "pyproject.toml").is_file() and (
                parent / "src" / "crypto_bot"
            ).is_dir():
                return parent
    raise MarketDataError("consumer projection provenance receipt repo root missing")


def _artifact_output_dir(repo: Path, output_dir: str | Path) -> Path:
    artifacts_root = (repo / "artifacts").resolve()
    requested = Path(output_dir)
    output = (
        requested.resolve()
        if requested.is_absolute()
        else (repo / requested).resolve()
    )
    if not output.is_relative_to(artifacts_root):
        raise MarketDataError("consumer projection provenance receipt output path escape")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    native = os.path.abspath(str(path))
    if os.path.lexists(native):
        if os.path.islink(native) or not os.path.isfile(native):
            raise MarketDataError(f"consumer projection provenance receipt collision:{path.name}")
        if path.read_bytes() != content:
            raise MarketDataError(f"consumer projection provenance receipt collision:{path.name}")
        return

    with tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=".consumer-projection-receipt-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary_native = os.path.abspath(str(temporary))
    try:
        try:
            os.link(temporary_native, native)
        except FileExistsError:
            if (
                os.path.islink(native)
                or not os.path.isfile(native)
                or path.read_bytes() != content
            ):
                raise MarketDataError(
                    f"consumer projection provenance receipt collision:{path.name}"
                )
        else:
            os.unlink(temporary_native)
            return
    except OSError as exc:
        raise MarketDataError(
            "consumer projection provenance receipt atomic commit failed"
        ) from exc
    finally:
        try:
            os.unlink(temporary_native)
        except FileNotFoundError:
            pass
