# Phase 2 — Future-Only Membership Epoch × 1h Bar Eligibility Gate (2026-08-09)

## Objective

Convert the frozen future-only universe transition into a deterministic,
prospective membership contract aligned with the existing `t → t+1h` completion
and exact `t+2h` execution timing. This is an eligibility evidence gate only;
it does not calculate PnL.

## Frozen inputs and policy

- Policy: `prospective_membership_1h_bar_gate_v1`.
- Baseline capture identity:
  `b96aa6011796c8e2f1e0d7826c0f95b9f5be15fbdf2cf38862493f3fc75da451`.
- Baseline report SHA-256:
  `f193b1f999843b5903d0934b6e6c3f6d758b3128101b9fa4bc1516a2bd4fea3d`.
- Current snapshot identity:
  `d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e`.
- Future transition identity:
  `706ba3e03bfc199ba332da1fd23cec7554ad5279c44f895b8eb302d3bfe244ef`.
- Transition report SHA-256:
  `e129f1e9754a1321b2e605996a8990418718a98be676a89970aea4ae94791f49`.
- Direct execution mapping identity:
  `48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb`.
- Fixed order: BTC-USDT, ETH-USDT, SOL-USDT, KNC-USDT, SWFTC-USDT, BICO-USDT.
- Membership policy: previous snapshot state, piecewise constant between
  snapshots; no retroactive change and no automatic replacement.

## Deterministic epoch

The previous snapshot arrived at `2026-08-02T15:49:19.356645Z`, so the first
complete signal bar is `2026-08-02T16:00:00Z`; the 15:00 bar is excluded because
it had already started. The current snapshot arrived at
`2026-08-09T10:22:11.263565Z`; execution anchors must be strictly earlier, so
the final signal is `2026-08-09T08:00:00Z` and final execution is
`2026-08-09T10:00:00Z`.

The closed epoch contains 161 hourly signal timestamps and 966 rows (161 × 6).
Each row records signal, completion, execution timestamps, previous membership,
current transition status, `eligible_for_closed_epoch`, and
`retroactive_change_applied=false`. It contains no price or return fields.

## Real acceptance

The CLI produced gate identity
`737e3da3a1e2db42b87709a23bc5f754bab2ae2a38af36210b28282cfb3d9f48` with status
`verified_prospective_membership_1h_bar_gate`, 161 signals, and 966 eligibility
rows. The current transition state is recorded as evidence only; it does not
change the closed epoch's previous-snapshot membership or delete rows.

## Claims and limitations

`future_only_membership_evidence=true`,
`membership_policy_piecewise_constant_between_snapshots=true`,
`continuous_membership_directly_observed=false`,
`historical_point_in_time_membership=false`,
`historical_survivorship_bias_resolved=false`,
`pnl_computation_authorized=false`, and `readiness_changed=false`. The gate does
not add instruments, reconstruct delistings, fix survivorship bias, select a
variant, or authorize economic evaluation.

## Validation

The implementation adds config/identity pinning, transition and mapping
replay checks, strict grid boundary tests, fixed-shape real acceptance, asset
state fail-closed checks, tamper detection, and content-addressed atomic writes.
The full repository test, coverage, Ruff, mypy, source/live safety, and
`git diff --check` gates are required before the next planning handoff.
