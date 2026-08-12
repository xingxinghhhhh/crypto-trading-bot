# Phase 2 — Real-Evidence Append Authorization Gate

Date: 2026-08-12

## Purpose

The CAS preflight proves that a prepared post-image has an unchanged parent;
it does not prove that the candidate is real market evidence. This node adds a
separate, read-only qualification gate so a synthetic `preflight_ready` image
cannot cross into an authoritative append path.

The CLI is:

```text
freeze-prospective-direct-1h-segment-append-authorization
  --append-preflight <preflight.json>
  --segment-evidence <segment-evidence.json>
  --config config.prospective-direct-1h-segment-append-authorization.example.yaml
  --output-dir reports/prospective-direct-1h-segment-append-authorization
```

## Authorization predicates

The gate replays both parents using their existing validators. Eligibility
requires all of the following, with no CLI override:

```text
preflight_ready
AND exact candidate lineage
AND validated non-fixture evidence
AND validated market evidence
AND validated public-only source provenance
```

The existing segment-evidence validator derives `fixture_only` and
`market_evidence` from the capture/extension lineage. The new gate does not
invent or accept a manual provenance classification. Unknown, private,
authenticated, or fixture lineage fails closed.

## State machine

| State | Result |
| --- | --- |
| blocked preflight | `blocked_preflight_not_ready` |
| exact preflight but fixture/non-market evidence | `blocked_non_real_market_evidence` |
| candidate identity mismatch | `blocked_candidate_lineage_mismatch` |
| exact non-fixture market evidence without validated public provenance | `blocked_insufficient_validated_provenance` |
| future exact real public-only lineage | `append_authorization_ready` |

When preflight is not ready, the deterministic audit marker has
`authorization_materialized=false` and `provenance.csv` has zero rows. Once
preflight is ready, the gate materializes a qualification decision even when
it blocks a fixture or insufficient-provenance candidate. All states emit only
a qualification contract. `append_performed`,
`segment_appended`, and `chain_mutation_performed` remain false. The current
real chain is not modified.

## Artifacts and determinism

The content-addressed marker contains `authorization.csv`, `provenance.csv`,
`dependencies.csv`, and `constraints.csv`. Identity binds the validated parent
identities, derived provenance, policy, and artifact hashes, but never binds
output directories, absolute paths, temporary names, process IDs, wall-clock
values, or durations. Same inputs in two report directories produce identical
names and bytes; a different byte collision fails closed.

## Current real smoke expectation

The existing real preflight and blocked segment-evidence reports must produce
`blocked_preflight_not_ready`, `authorization_materialized=false`,
`append_authorization_eligible=false`, and
`authoritative_write_authorized=false`. The baseline remains 160/500 samples
with 340 remaining and zero new sample credit. No network, capture, raw data,
economic/PnL, readiness, frontend, paper/live, or trading work is performed.

Positive authorization logic is tested only as an in-memory pure predicate;
no persisted synthetic artifact may be labeled as real evidence.
