# Phase 2 — Consumer projection provenance audit summary MVP (2026-08-21)

## Scope

This node adds a read-only operator summary after the persisted consumer
projection provenance receipt has passed its existing verification barrier. It
does not create a summary artifact and does not change the projection or
receipt contracts.

```text
explicit receipt/projection paths
  → existing provenance receipt verifier (once)
  → read the explicitly supplied verified projection JSON
  → parse the already verified receipt filename identity
  → mechanical 23-key audit summary
```

The summary is deterministic and zero-write. It does not discover artifacts,
read parent files, recalculate hashes, recompare identities, call another
validator, perform capture/append, calculate economic/PnL results, promote
readiness, or authorize Paper/Live/API/frontend/trading actions.

## Frozen contract

- Python entry point:
  `build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(...)`.
- It calls the existing provenance receipt verifier exactly once and returns a
  `dict[str, JSONScalar]`.
- The exact 23-key summary contains the audit-summary version, receipt and
  projection identities, the existing `source_operations_snapshot_identity`,
  governance state, sample counts, and all existing authorization booleans.
- `receipt_sha256` is parsed from the explicit, already verified receipt
  filename; no receipt or projection bytes are re-hashed.
- The projection body is read only from the explicit projection path after
  verifier success. No parent artifact is opened and no identity is
  rediscovered.

## CLI

`show-current-operations-handoff-provenance-audit-summary` requires all six
explicit receipt/projection/evidence paths. Success emits one canonical JSON
line and exits `0`; malformed, stale, tampered, unsafe, or schema-invalid
inputs emit empty stdout and exit `2`. The command has no output directory and
cannot write artifacts or reports.

## Explicit exclusions

No summary artifact, second verification gate, schema migration, new
authority/status, latest/discovery/rglob, hash recomputation, parent traversal,
network/capture, sample growth, economic/PnL, readiness promotion, Paper/Live,
HTTP API, frontend, database, or trading execution is included.

## Verification

Targeted audit-summary tests: **15 passed**. The evidence-chain joint
regression (projection through receipt verification and summary) completed with
**123 passed, 2 skipped** in 15:48. The fresh full suite completed with
**933 passed, 2 skipped** and **85.10% line/branch-gated coverage**. Ruff,
Safety/source-safety, `git diff --check`, and fixed-list mypy completed
successfully for **85 source files**. Remote CI is still required before node
closure.
