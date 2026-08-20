# Phase 2 — Governance-fresh operations handoff consumer projection

## Scope

This node exposes the already validated governance-fresh operations handoff as
one stable, path-free, deterministic JSON consumer contract and a read-only
CLI. It does not add another evidence, currentness, or freshness gate.

## Contract

`project_governance_fresh_current_operations_handoff(...)` calls only
`load_governance_fresh_current_operations_handoff(...)` and mechanically maps
the returned frozen read model to exactly 20 fields. The projection version is
`verified_current_operations_handoff_consumer_projection_v1` and the freshness
flag is `governance_freshness_verified=true`.

`show-verified-current-operations-handoff` requires delivery admission,
handoff delivery, current operations snapshot, and epoch closeout/rollover paths
explicitly. Success emits one compact `sort_keys=True` JSON line and exits 0;
all validation/input failures emit no stdout and exit 2.

## Safety boundaries

- No second validator, parent-artifact read, identity/currentness/freshness
  comparison, latest discovery, wall clock, timestamp, PID, duration, or path
  metadata.
- No artifact/config/database/API/frontend changes; no network, capture, append,
  mutation, sample growth, economic/PnL, Paper, live, or trading behavior.
- `next_legal_action` remains display state only; no readiness or execution
  fields are introduced.

## Verification evidence

- Consumer projection targeted suite: 10 passed.
- CI report-backed fixtures restored with `python tests/restore_ci_report_fixtures.py`;
  restore status was complete.
- Complete joint chain from capture-window closeout through Consumer Projection:
  114 passed in 449.45s. This includes the `rollover_next_window` supersession
  fail-closed path, epoch assembly mismatch rejection, and transitive upstream
  parent-tamper rejection; the projection CLI's superseded error path asserts
  exit 2 with empty stdout.
- Ruff: passed.
- Mypy for the new projection module: no issues.

## Project-level status

The project remains in Phase 2 offline research-evidence hardening. Strategies
remain `not_ready`; this projection is a deterministic consumer boundary, not a
strategy recommendation, profitability claim, or execution authorization.
