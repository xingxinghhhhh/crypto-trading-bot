from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection import (
    JSONScalar,
    format_governance_fresh_current_operations_handoff_projection,
    project_governance_fresh_current_operations_handoff,
)


DEFAULT_OUTPUT_DIR = (
    "artifacts/prospective-evidence-operations-handoff-consumer-projection"
)
ARTIFACT_PREFIX = "prospective-evidence-operations-handoff-consumer-projection"


@dataclass(frozen=True, slots=True)
class ConsumerProjectionMaterializationResult:
    projection: dict[str, JSONScalar]
    projection_sha256: str
    projection_path: str


def materialize_governance_fresh_current_operations_handoff_projection(
    delivery_admission: str | Path,
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    epoch_closeout_rollover: str | Path,
    output_dir: str | Path,
) -> ConsumerProjectionMaterializationResult:
    """Persist one verified consumer projection as a content-addressed artifact."""

    # The existing projector owns every upstream validator and freshness check.
    # Do not create or touch an output directory until it has completed.
    projection = project_governance_fresh_current_operations_handoff(
        delivery_admission,
        handoff_delivery,
        current_operations_snapshot,
        epoch_closeout_rollover,
    )
    canonical = format_governance_fresh_current_operations_handoff_projection(
        projection
    )
    content = canonical.encode("utf-8")
    projection_sha256 = hashlib.sha256(content).hexdigest()
    repo = _repo_root(
        Path(delivery_admission),
        Path(handoff_delivery),
        Path(current_operations_snapshot),
        Path(epoch_closeout_rollover),
    )
    output = _artifact_output_dir(repo, output_dir)
    target = output / f"{ARTIFACT_PREFIX}.{projection_sha256}.json"
    _commit_bytes(target, content)
    return ConsumerProjectionMaterializationResult(
        projection=projection,
        projection_sha256=projection_sha256,
        projection_path=str(target),
    )


def _repo_root(*paths: Path) -> Path:
    candidates = [Path.cwd(), *paths]
    for path in candidates:
        resolved = path.resolve()
        for parent in (resolved, *resolved.parents):
            if (parent / "pyproject.toml").is_file() and (
                parent / "src" / "crypto_bot"
            ).is_dir():
                return parent
    raise MarketDataError("consumer projection repo root missing")


def _artifact_output_dir(repo: Path, output_dir: str | Path) -> Path:
    artifacts_root = (repo / "artifacts").resolve()
    requested = Path(output_dir)
    output = (
        requested.resolve()
        if requested.is_absolute()
        else (repo / requested).resolve()
    )
    if not output.is_relative_to(artifacts_root):
        raise MarketDataError("consumer projection output path escape")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    native = os.path.abspath(str(path))
    if os.path.lexists(native):
        if os.path.islink(native) or not os.path.isfile(native):
            raise MarketDataError(f"consumer projection content collision:{path.name}")
        if _read_bytes(path) != content:
            raise MarketDataError(f"consumer projection content collision:{path.name}")
        return

    with tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=".consumer-projection-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary_native = os.path.abspath(str(temporary))
    try:
        # A hard-link install is atomic and never replaces an existing target.
        # If another writer wins the race, inspect the winner and preserve the
        # same-bytes idempotency / different-bytes collision contract.
        try:
            os.link(temporary_native, native)
        except FileExistsError:
            if (
                os.path.islink(native)
                or not os.path.isfile(native)
                or _read_bytes(path) != content
            ):
                raise MarketDataError(
                    f"consumer projection content collision:{path.name}"
                )
        else:
            os.unlink(temporary_native)
            return
    except OSError as exc:
        raise MarketDataError("consumer projection atomic commit failed") from exc
    finally:
        try:
            os.unlink(temporary_native)
        except FileNotFoundError:
            pass


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read()
