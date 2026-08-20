# Phase 2 — Consumer projection provenance receipt MVP (2026-08-20)

## Scope

This node adds one small, deterministic lineage receipt for a projection that
has already been materialized and replay-verified. It does not modify the
existing 20-key projection or introduce another governance gate.

```text
existing projection verifier
  → materialized projection binding checks
  → validated rollover filename identity extraction
  → six-key canonical receipt
  → SHA-256 content-addressed artifact
```

The receipt makes explicit which delivery, snapshot, and epoch rollover
identities were used for the verified projection. It remains an offline
consumer provenance artifact and is not evidence of profitability or readiness.

## Frozen contract

- Python entry point:
  `materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(...)`.
- Result: frozen-slots `ConsumerProjectionProvenanceReceiptResult` with
  `receipt`, `receipt_sha256`, and `receipt_path`.
- Receipt body has exactly six string keys:
  `receipt_version`, `projection_sha256`, `delivery_admission_identity`,
  `handoff_delivery_identity`, `current_operations_snapshot_identity`, and
  `epoch_closeout_rollover_identity`.
- Canonical bytes are compact sorted-key JSON, UTF-8, without BOM or terminal
  newline. The receipt digest is the SHA-256 of those exact bytes.
- The projection must be a regular, non-symlink artifact under `artifacts/`,
  with the frozen projection prefix, filename digest, exact existing formatter
  bytes, and zero terminal newline. A canonical but shell-created file is not
  accepted.
- The verifier is the only upstream consumer binding call. The receipt layer
  does not call governance validators, the projector, or freshness internals
  directly. After verifier success, the rollover identity is extracted from
  its already-validated content-addressed filename.
- Receipt output is limited to `artifacts/` and uses atomic, idempotent,
  non-overwriting collision semantics. All checks finish before the output
  directory or receipt is committed.

## CLI

`materialize-verified-current-operations-handoff-projection-receipt` requires
the projection and four explicit evidence inputs, with optional `--output-dir`.
Success prints only the receipt SHA and exported path; failure prints no
stdout, a concise stderr message, and exits `2`.

## Explicit exclusions

No receipt verifier, second status/DTO artifact, projection schema change,
Read Model change, latest discovery, wall clock, network/capture, state
mutation, sample growth, economic/PnL, Paper/Live, API/frontend, trading, or
readiness promotion is included.

## Verification

Receipt targeted: **18 passed, 1 Windows symlink-permission skip**. Relevant
projection/freshness/read-model regression: **77 passed, 1 skip**. Complete
rollover-to-receipt joint chain: **160 passed, 1 skip**. The existing 82 fixed
mypy source targets remain mandatory; this module adds the 83rd source file.

Fresh full coverage, static, and remote CI results are recorded at node
closure.
