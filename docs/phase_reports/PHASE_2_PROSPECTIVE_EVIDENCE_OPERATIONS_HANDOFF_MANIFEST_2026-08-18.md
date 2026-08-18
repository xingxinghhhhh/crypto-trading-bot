# Phase 2 — Prospective Evidence Operations Handoff Consumption Manifest

## Scope

This node adds the first stable consumer contract above the validated
operations snapshot, portable bundle, and bundle-currentness admission chain.
It accepts a replay-validated currentness admission and its bound portable
bundle, then emits a small deterministic read-only manifest. It does not add an
HTTP API, frontend, network access, capture, trading, or authorization.

## Contract

- `current_bundle_admitted` plus an exact bundle identity match produces
  `handoff_manifest_ready` and `consumer_readable=true`.
- `blocked_stale_bundle` produces `blocked_bundle_not_current`,
  `consumer_readable=false`, and an empty status CSV; stale business projection
  is never presented as a current handoff.
- Admission and bundle validators are replayed before projection. Every
  displayed field is derived from the validated bundle source projection, not
  trusted from a manifest self-report.
- `consumer_action_authorized` and `state_mutation_authorized` are always
  false. Existing append, economic/PnL, Paper, live, network, and state-change
  flags remain unchanged.

## Artifacts and CLI

The CLI command is:

`build-prospective-evidence-operations-handoff-manifest`

It accepts `--bundle-admission`, `--operations-bundle`, `--config`, and
`--output-dir`, with output outside `reports/`. The artifact family contains a
content-addressed JSON marker plus status, dependency, and policy sidecars. It
does not copy the bundle payload.

## Verification record

The targeted, joint, full, static, and remote verification results for this
node are recorded in the closing progress handoff after implementation. This
artifact is a consumer-state contract only; it is not a strategy-readiness or
profitability decision.
