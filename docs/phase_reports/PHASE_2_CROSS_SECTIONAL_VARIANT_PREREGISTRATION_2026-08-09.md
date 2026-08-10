# Phase 2 — Prospective cross-sectional variant family and multiplicity contract

Date: 2026-08-09

## Scope and safety boundary

This node freezes the complete 36-member factor × horizon × rank-direction
family for a future economic evaluation. It is deliberately downstream of the
validated Direct-OKX execution timestamp prerequisite but upstream of any
returns, costs, PnL, winner selection, paper/live execution, or readiness claim.

It is a prospective economic-evaluation preregistration after prior statistical
evidence, not global preregistration. Existing IC/HAC/OOS results are disclosed
and cannot be used to remove, flip, or reorder a family member.

## Frozen inputs and policies

- Mechanism identity: `dcc8e9364efd900487608090ba879194a8a84195cbeab2dc63d0266df97406d5`
- Mechanism marker SHA-256:
  `d55880b8cd702f91ee8579d5dd792bf84a37296181441c2b4991c4d6578dcead`
- Direct execution mapping identity:
  `48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb`
- Direct execution mapping marker SHA-256:
  `39d0a719f1546c5249d57c11cc2422f0dc396a8e9a5f55f1742a60d321899074`
- Family scope: `all_36_variants`
- Ordering: existing mechanism CSV order (factor, horizon 4/16/64, high/low)
- Direction policy: `evaluate_both_without_selection`
- Missing variant policy: `fail_family`
- Future multiple-testing method: `holm`
- Result-driven reconfiguration: prohibited

## Acceptance result

The CLI produced a content-addressed marker:

- Preregistration identity:
  `28afc8ec130aef5da62b857064f3a4f5aa98fcffb06d390a02430748fb5c6a68`
- Family size: `36`
- `selection_prohibited=true`
- `execution_price_mapping_feasible=true`
- `pnl_computation_authorized=false`
- `readiness_changed=false`

The output contains a fixed 36-row variants CSV and a deterministic policy CSV.
The variants CSV SHA-256 is
`34266923c342ee5f1382379d76ee66978f8a8d7cf2fc324d5b8157f20dc7cdc5`, the
policy CSV SHA-256 is
`1c13ff0a3397e7f5bd6f4bd5c8efcda6940c9b0d00410775d64674e4e9d01a4d`, and the
marker file SHA-256 is
`eabefd05c3eb286a9596589cd395257206c5324d5193a7353a7e241c9f66eb71`.
The marker binds both upstream identities, the exact family membership and
order, policy fields, bias/semantics claims, and both CSV hashes. Output
directory, process identity, and time are not part of the identity. Artifacts
are written atomically in order: variants CSV → policy CSV → JSON marker; same
name/different bytes fails closed.

## Remaining limits

The protocol does not make a variant profitable, does not authorize PnL, and
does not resolve current-live membership, point-in-time universe membership, or
survivorship bias. The next economic node still needs independently frozen
cost/spread/slippage/capacity evidence and must consume this family without
selection.
