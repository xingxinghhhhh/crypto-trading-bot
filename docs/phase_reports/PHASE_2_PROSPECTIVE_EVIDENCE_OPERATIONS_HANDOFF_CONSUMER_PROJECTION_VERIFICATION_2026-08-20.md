# Phase 2 — Consumer projection provenance verification gate

## Scope

This node adds a read-only provenance barrier for a projection that has already
been persisted or transferred. It accepts that projection plus the four
explicit governance inputs, replays the existing freshness-gated projection,
and requires exact equality before returning the unchanged 20-key projection.
It does not create a new evidence reducer, verification DTO, read model, or
authorization state.

## Contract

`verify_governance_fresh_current_operations_handoff_projection(...)` loads the
persisted projection, accepts the canonical JSON body with zero or one terminal
newline, validates the existing schema/format, and calls only
`project_governance_fresh_current_operations_handoff(...)`. A value, identity,
schema, or upstream provenance mismatch fails closed.

`verify-current-operations-handoff-projection` requires the projection and all
four upstream paths explicitly. Success prints the unchanged canonical
projection with one CLI newline and exits `0`; malformed, non-canonical,
tampered, stale, or upstream-invalid inputs print no stdout and exit `2`.

## Safety boundaries

- No direct snapshot, delivery, admission, rollover, Read Model, or freshness
  validator calls in the verification module; all governance replay remains in
  the existing projector chain.
- No latest discovery, input mutation, projection regeneration, wall clock,
  reports/artifacts writes, network/capture/append, sample growth, economic/PnL,
  Paper, live, or trading behavior.
- Verification success returns only the existing projection; it grants no
  execution or economic authority.

## Verification evidence

- Verification targeted suite: 21 passed.
- Projection + verification + freshness + Read Model + delivery verification
  regression: 45 passed.
- Complete governance joint chain through persisted projection verification:
  135 passed in 568.93s. This includes fresh projection replay and rejection
  after superseded/assembly-mismatch progression, as well as upstream parent
  tamper rejection.
- Ruff: passed.
- Mypy for the new verification module: no issues.
- `git diff --check`: passed.

## Project-level status

The project remains in Phase 2 offline research-evidence hardening. Strategies
remain `not_ready`; this gate proves persisted projection provenance only and is
not a strategy recommendation, profitability claim, or execution authorization.
