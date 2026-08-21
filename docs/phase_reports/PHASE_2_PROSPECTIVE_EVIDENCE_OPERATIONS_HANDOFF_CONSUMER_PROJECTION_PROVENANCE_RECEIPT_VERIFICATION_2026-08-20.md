# Phase 2 — Consumer projection provenance receipt verification gate MVP (2026-08-20)

## Scope

This node adds a read-only barrier for an already materialized,
content-addressed provenance receipt. It does not change the 20-key projection
or six-key receipt contracts and does not create a second verification artifact.

```text
persisted receipt bytes
  → receipt path/canonical/content-address checks
  → existing projection verifier
  → materialized projection binding checks
  → validated rollover filename identity
  → exact six-key equality
  → return persisted receipt unchanged
```

The verifier is offline and deterministic. It performs no writes, discovery,
network activity, capture, state mutation, economic/PnL calculation, readiness
promotion, Paper/Live action, API/frontend work, or trading.

## Frozen contract

- Python entry point:
  `verify_governance_fresh_current_operations_handoff_projection_provenance_receipt(...)`.
- Return value: the original exact `dict[str, str]` receipt.
- Receipt input must be a regular, non-symlink file under `artifacts/` named
  `prospective-evidence-operations-handoff-consumer-projection-provenance-receipt.<sha256>.json`.
- Exact UTF-8 canonical bytes are required: compact sorted-key JSON, no BOM,
  no leading/trailing whitespace, no CR/LF, exactly six string keys, and the
  frozen v1 version. The filename digest must match the exact bytes.
- The existing projection verifier is called once. The projection must then
  be a regular, non-symlink materialized artifact under `artifacts/`, with the
  frozen filename digest, exact formatter bytes, and no terminal newline.
- The receipt is rebuilt mechanically from verified projection identities,
  exact materialized projection bytes, and the explicit validated rollover
  filename digest. Persisted and expected receipts must compare exactly.
- The closed Receipt producer remains unchanged; the verifier does not call
  it or any governance validator, projector, freshness loader, Read Model
  loader, or business reducer directly.

## CLI

`verify-current-operations-handoff-projection-receipt` requires all six
explicit paths (`--receipt`, `--projection`, `--delivery-admission`,
`--handoff-delivery`, `--current-operations-snapshot`, and
`--epoch-closeout-rollover`). It has no output directory because it is strictly
zero-write. Success emits one canonical six-key JSON line; malformed, stale,
tampered, mismatched, or unsafe inputs emit empty stdout and exit `2`.

## Verification

Targeted Receipt Verification tests: **38 passed, 1 Windows symlink-permission
skip** in 7:33. The fresh full suite completed with **918 passed, 2 skipped**
and **88.48% line coverage**. Ruff, Safety/source-safety guards,
`git diff --check`, and the fixed mypy gate completed successfully; mypy still
covers the prior 83 source targets plus this verifier as target 84. The
remaining acceptance step is the GitHub Actions run, where Linux executes the
symlink rejection case that is permission-skipped on Windows.
