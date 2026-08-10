# Phase 2 — Prospective Capture Attempt Evidence Adapter & Receipt Materializer (2026-08-10)

## Planned scope

This node translates a local execution-evidence envelope into the already
frozen append-only receipt schema. It is deliberately offline: it does not
call OKX, create a real attempt, advance the zero-receipt chain, add samples,
or calculate economic/readiness values.

## Repository-validated assumptions

- `validate_future_universe_snapshot` is an existing public, pure local
  validator and is reused for the successful snapshot path.
- Existing capture wrappers expose generic transport errors and raw bytes but
  do not provide a stable adapter envelope. The adapter therefore freezes only
  three evidence kinds and stable local artifact fields; it does not invent
  exception bodies or duplicate snapshot validation logic.
- The parent receipt-chain report is validated through the existing public
  validator and must be the current appendable zero-receipt state.

## Implemented contract

- `transport_failure` maps to `transport_failed` with no response/snapshot
  hashes and `accepted=false`.
- `snapshot_validation_failure` hashes a local raw response artifact and maps
  to `validation_failed`.
- `snapshot_validation_pass` hashes the local response, replays the local
  canonical snapshot through the public snapshot validator, and maps to the
  first `validation_passed`/`accepted` receipt.
- Attempt number, parent identities, request policy, acceptance, outcome, and
  previous hash are derived or validated; caller overrides are rejected.
- The generated receipt is immediately replayed through the existing receipt
  chain auditor before marker-last content-addressed artifacts are committed.
- All reports retain `fixture_only=true`, `market_evidence=false`,
  `network_activity_performed=false`, `current_samples=160`, and
  `remaining_samples=340`.

## Artifacts and CLI

- `src/crypto_bot/market/prospective_capture_attempt_evidence_adapter.py`
- `config.prospective-capture-attempt-evidence-adapter.example.yaml`
- `tests/test_prospective_capture_attempt_evidence_adapter.py`
- `src/crypto_bot/cli.py`, `README.md`, and `.github/workflows/ci.yml`
- CLI: `materialize-prospective-capture-attempt-receipt --receipt-chain
  <receipt-chain.json> --attempt-evidence <evidence.json> --config <config.yaml>
  --output-dir <dir>`

## Explicit exclusions

No network request, Aug-16 capture, real receipt append, snapshot creation,
sample increment, turnover/cost/capacity/return/PnL, readiness, winner
selection, paper/live authorization, reset, clean, stage, commit, or push.

## Verification record

- Adapter tests: `30 passed`; adapter branch coverage: `100%`.
- Adapter/receipt/journal/admission/safety targeted set: `77 passed`; the
  separate adapter + safety/live guard check: `33 passed`.
- Fresh full suite: `569 passed`; branch coverage `85.36%` (project threshold
  `85%`).
- CI mypy list: `56` source files, `Success: no issues found`.
- Ruff `src tests`: all checks passed; `git diff --check` passed (only Windows
  line-ending notices).
- Real zero-receipt CLI recheck preserved chain identity
  `3fef210901ee2ba1db387ff8468da069809f4d0c0a6914f87bedaa699b89b538`,
  `receipt_count=0`, `network_activity_performed=false`, `160/500`, and
  `remaining_samples=340`.
- CLI E2E used only a local transport-failure fixture; validation-pass tests
  copied the existing canonical artifacts, changed only the synthetic fixture
  timestamp, and passed the public snapshot validator. No real network call or
  sample was created.
