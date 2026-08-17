# Phase 2 — Prospective Evidence Operations Handoff Snapshot (2026-08-17)

## Scope

This node adds a read-only, replayable operator handoff over the existing
prospective epoch assembly, sample-maturity, Direct-OKX append-authorization,
and economic-readiness contracts. It does not capture data, call the network,
append a segment, grant sample credit, compute economic/PnL results, or enable
paper/live execution.

## Contract

The frozen policy is
`prospective_evidence_operations_snapshot_v1`. The only CLI is:

```text
build-prospective-evidence-operations-snapshot
```

It accepts exactly four validated parent markers plus the frozen policy config.
Stage, blocker, next action, sample counts, and authorization values cannot be
provided by CLI or config. The output family is content-addressed:

```text
prospective-evidence-operations-snapshot.<sha>.status.csv
prospective-evidence-operations-snapshot.<sha>.dependencies.csv
prospective-evidence-operations-snapshot.<sha>.constraints.csv
prospective-evidence-operations-snapshot.<sha>.json
```

The public validator replays all four parents and rejects marker, artifact,
identity, projection, and invariant drift. Paper/live remain fail-closed because
the repository has no explicit paper/live authorization parent.

## Current real baseline

The validated current parents project to:

```text
governed_stage=awaiting_real_membership_epoch_progress
blocking_gate=membership_epoch_progress
next_legal_action=await_real_membership_epoch_progress
current_samples=160
sample_threshold=500
remaining_samples=340
append_authorization_ready=false
sample_maturity_met=false
economic_authorized=false
pnl_authorized=false
paper_authorized=false
live_authorized=false
state_changed=false
network_activity_performed=false
new_samples_counted=0
```

This is an operational projection only. It does not claim that a real capture,
append, economic evaluation, profitability result, or trading permission exists.

## Compatibility and rollback

The node adds only a new artifact family and minimal read-only validators for
the existing assembly, maturity, and readiness markers. Existing parent marker
schemas and identities are not rewritten. Rollback consists of stopping
consumption of the operations-snapshot artifact family.

## CI fixture closure

The clean-checkout replay contract is now explicit. The four pinned parent
markers and every artifact referenced by those markers are stored in
`tests/fixtures/ci-operations-reports.zip`. `tests/restore_ci_report_fixtures.py`
validates safe archive-relative paths, marker identity digests, referenced
artifact existence and SHA-256 bytes, then restores idempotently while refusing
to overwrite different existing bytes. The integrity tests also cover an empty
destination, missing parents, transitive-artifact tampering, and cross-platform
relative paths. No test creates a synthetic real-state report.
