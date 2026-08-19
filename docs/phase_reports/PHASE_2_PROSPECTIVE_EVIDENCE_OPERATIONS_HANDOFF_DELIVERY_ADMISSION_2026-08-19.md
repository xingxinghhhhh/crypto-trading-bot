# Phase 2 — Prospective evidence operations handoff delivery currentness admission

## Scope

This node adds a deterministic, offline, read-only admission gate for a
validated handoff delivery package. A package being internally valid is not
enough to prove that it is current. The gate therefore requires an explicit,
freshly validated operations snapshot and compares both the source snapshot
identity and the canonical source projection.

## Contract

- `current_delivery_admitted` is emitted only when identity and projection
  both match.
- `blocked_stale_delivery` is emitted for a valid package whose source
  snapshot identity is no longer current.
- An identity match with a projection mismatch fails closed as a contract
  error; it is not downgraded to ordinary staleness.
- No latest-artifact discovery, refresh, network activity, capture, append,
  economic/PnL, Paper, live, or state mutation is possible.
- The existing delivery validator keeps its legacy return shape. The new
  inspection helper exposes only package-derived normalized facts for this
  gate.

## Outputs

The CLI command
`freeze-prospective-evidence-operations-handoff-delivery-admission` emits
content-addressed admission, dependency, constraint, and JSON report files.
The JSON report is revalidated against the explicit delivery and snapshot
inputs before the command succeeds.

## Verification evidence

- Targeted delivery and admission regression suite: 18 passed.
- Joint snapshot → bundle → bundle admission → manifest → verification →
  delivery → delivery admission suite: 74 passed.
- Ruff: passed.
- Safety and live-trading guard tests: 3 passed.
- Mypy: passed for all 76 CI-listed source targets.

## Project-level status

The project remains in Phase 2 research-evidence and offline operational
handoff hardening. Strategies remain `not_ready`; no economic/PnL, Paper, or
live execution authority is granted. Frontend/dashboard work and stable
profitability validation are later milestones after the evidence and currentness
contracts are complete.

## Next node

After remote CI confirmation, continue with the next ChatGPT-approved research
evidence gate, keeping every node offline, deterministic, read-only, and
fail-closed until the project explicitly reaches later validation phases.
