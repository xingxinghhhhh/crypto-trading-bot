# Phase 2 — Prospective Evidence Operations Bundle Currentness Admission

## Scope

This node adds a read-only freshness gate for the portable operations bundle.
It compares a fully replay-validated bundle with a fully replay-validated
current operations snapshot. It does not update the bundle or alter any
governed state.

## Contract

- Exact `source_operations_snapshot_identity == current snapshot_sha256` is
  required for `current_bundle_admitted`.
- Source and current projections are compared independently; an identity match
  with a projection mismatch fails closed.
- A valid identity mismatch is represented as `blocked_stale_bundle`, never as
  an automatically refreshed bundle.
- The admission artifact fixes `state_changed`, `bundle_mutated`,
  `authoritative_action_authorized`, `network_activity_performed`, and
  `new_samples_counted` to false/zero.
- The admission config never stores the current snapshot identity, and output
  remains outside the source `reports/` tree.

## CLI

`freeze-prospective-evidence-operations-bundle-admission` accepts a bundle,
current operations snapshot, frozen config, and output directory. The public
validator can revalidate the artifact together with both replay inputs; both
inputs are required together so consumers cannot accidentally skip currentness
replay.

## Verification record

- Targeted admission suite: `python -m pytest tests/test_prospective_evidence_operations_bundle_admission.py -q` — **9 passed**.
- Current and stale fixtures both remain read-only; the stale fixture only adds
  a non-business identity nonce to prove exact-match semantics and is removed
  after the test.

- Joint bundle/currentness/snapshot and governed-parent suite — **134 passed**.
- Full suite: `python -m pytest tests -q --cov=crypto_bot --cov-report=term --cov-report=xml` — **757 passed**, total branch coverage **85.02%**.
- `python -m ruff check src tests` — passed; CI mypy list including this module
  — **72 source files passed**; safety/live guard — **6 passed**; `git diff --check` — passed.
- CLI smoke produced `status=current_bundle_admitted` with identical source and
  current snapshot identities; all mutation/authority/network flags remained
  false.

Remote GitHub Actions and the final commit/push remain release steps for this
node. This is a freshness safety gate, not a strategy-profitability or launch-readiness
decision.
