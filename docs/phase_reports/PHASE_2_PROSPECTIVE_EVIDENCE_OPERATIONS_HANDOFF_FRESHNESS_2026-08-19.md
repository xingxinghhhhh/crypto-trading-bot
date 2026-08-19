# Phase 2 — Verified current operations handoff governance freshness

## Scope

This node adds a pure Python read barrier around the immutable operations
handoff read model. It consumes an explicit, validated epoch closeout/rollover
report so an otherwise artifact-current snapshot cannot be presented as
governance-current after a validated epoch progression.

## Contract

`load_governance_fresh_current_operations_handoff(...)` first validates the
explicit operations snapshot and loads the existing
`VerifiedCurrentOperationsHandoff`. It then validates the explicit epoch
closeout/rollover and binds its `assembly_sha256` to the epoch-assembly parent
identity recorded by the current snapshot.

Only `action=hold` returns the existing read model unchanged. The validated
`transition_eligible` and `rollover_next_window` actions fail closed because
the old snapshot is superseded. Unknown actions and assembly mismatches also
fail closed.

## Safety boundaries

- No system-clock checks, latest-artifact discovery, `rglob`, or recomputation
  of capture, window, sample, or rollover policy state.
- No closeout/rollover creation, snapshot refresh, epoch advancement, network,
  capture, append, mutation, sample credit, economic/PnL, Paper, live, API,
  frontend, or trading behavior.
- Existing snapshot, delivery, admission, and read-model identities and fields
  remain unchanged.

## Verification evidence

- Targeted freshness suite: 6 passed.
- Ruff: passed.
- Mypy for the new source module: no issues.

## Project-level status

The project remains in Phase 2 offline research-evidence hardening. Strategies
remain `not_ready`; this gate is a governance freshness read barrier, not a
strategy recommendation, profitability claim, or execution authorization.
