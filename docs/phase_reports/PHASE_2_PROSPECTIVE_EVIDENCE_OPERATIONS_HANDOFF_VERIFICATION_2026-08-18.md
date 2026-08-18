# Phase 2 — Prospective Evidence Operations Handoff Verification Gate

## Scope

This node adds a stable, read-only consumption barrier above the validated
operations handoff manifest. It explicitly replays the manifest, currentness
admission, and portable bundle validators before allowing a future operator,
deployment script, API, or UI process to consume the manifest as state input.
It does not add an HTTP API, frontend, network access, capture, trading, or
business authorization.

## Contract

- `consumer_handoff_verified` with `safe_to_consume_read_only=true` is emitted
  only for a `handoff_manifest_ready` manifest whose bound parents replay and
  whose lineage and projection validate exactly.
- Tampered, missing, stale, blocked, or mismatched parents fail closed with a
  non-zero CLI exit code; no blocked manifest is reported as safely readable.
- The verification result is a small canonical JSON projection. It never
  searches for a latest artifact, refreshes a stale bundle, writes a parent,
  or executes `next_legal_action`.
- `consumer_action_authorized`, `state_mutation_authorized`,
  `append_authorization_ready`, `economic_authorized`, `pnl_authorized`,
  `paper_authorized`, and `live_authorized` remain false.

## CLI and tests

The command is:

`verify-prospective-evidence-operations-handoff-manifest`

It accepts explicit `--handoff-manifest`, `--bundle-admission`, and
`--operations-bundle` paths. Targeted tests cover current, tamper, stale,
lineage, missing-parent, canonical-output, and subprocess exit-code cases.

## Verification record

- Verification targeted: 10 passed.
- Operations joint chain: 56 passed.
- Fresh full suite: 781 passed; branch coverage 85.08%.
- Ruff, safety/live guards, `git diff --check`, and the complete CI mypy list
  (74 source files on Python 3.11) passed.
- Remote closure remains pending until the new GitHub Actions Python 3.11 and
  3.12 matrix is green.

This artifact is a read-barrier contract only; it is not a strategy-readiness,
profitability, paper-trading, or live-trading decision.
