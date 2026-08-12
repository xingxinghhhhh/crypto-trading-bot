# Phase 2 — Prospective Direct-OKX 1h Segment Append Preflight

Date: 2026-08-12
Status: implemented as a no-mutation CAS preflight; authoritative append remains prohibited.

## Scope

This node closes the preflight gap between a validated non-authoritative
prepared image and any future writer. It accepts a prepared report and the
current authoritative segment-chain marker, independently replays both, and
produces a deterministic content-addressed contract. It does not write the
authoritative chain.

The CLI is:

```text
preflight-prospective-direct-1h-segment-append
  --prepared <prepared.json>
  --segment-chain <current-chain.json>
  --config config.prospective-direct-1h-segment-append-preflight.example.yaml
  --output-dir reports/prospective-direct-1h-segment-append-preflight
```

## State machine

Only these outcomes are permitted:

| State | Meaning | Post image | Write-set |
| --- | --- | --- | --- |
| `blocked_prepared_chain_not_ready` | prepared lineage is blocked | none | 0 rows |
| `blocked_authoritative_chain_drift` | current parent identity differs | none | 0 rows |
| `preflight_ready` | prepared is non-authoritative and the exact parent still matches | independently recomputed | 3 logical rows |

Even `preflight_ready` keeps `commit_authorized=false`,
`authoritative_write_authorized=false`, `chain_mutation_performed=false`, and
`segment_appended=false`.

## Deterministic artifacts

Each result contains content-addressed `cas.csv`, `write-set.csv`,
`dependencies.csv`, `constraints.csv`, and a JSON marker. The identity binds
the frozen policy, validated parent identities, candidate/post image fields,
and artifact hashes. It does not bind output directories, absolute paths,
temporary names, process IDs, wall-clock values, or durations. Re-running the
same parent and prepared input in another output directory therefore produces
the same names and bytes; an existing name with different bytes fails closed.

The logical write-set only describes preserving existing references, appending
one candidate exactly once, and materializing the future marker last. It does
not contain an authoritative destination path.

## Governance invariants

The real baseline remains `160/500` samples with `340` remaining and zero new
sample credit. The preflight performs no network activity, capture, raw-data
creation, economic/PnL computation, readiness change, promotion, paper/live
execution, or trading. Existing chain, evidence, admission, plan, and prepared
markers are read-only dependencies and are not rewritten.

## Verification required for this node

- targeted preflight tests, including blocked, exact-parent, parent-drift,
  post-identity tamper, deterministic output, collision, policy, and artifact
  tamper cases;
- joint prepared/plan/admission/chain regression tests;
- fresh full pytest with branch coverage at least 85%;
- Ruff, the CI mypy source list, safety tests, and `git diff --check`;
- real blocked smoke using the existing prepared and authoritative chain
  reports, proving zero post-state, zero write-set, and zero mutation.

This report intentionally does not claim a real append, sample maturity,
economic evidence, strategy profitability, or frontend readiness.
