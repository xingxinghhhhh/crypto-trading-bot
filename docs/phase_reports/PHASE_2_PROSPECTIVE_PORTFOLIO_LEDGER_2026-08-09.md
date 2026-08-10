# Phase 2 — Prospective Closed-Epoch Portfolio Ledger (2026-08-09)

## Scope

This node binds the frozen six-asset Direct-OKX 1h baseline plus prospective
append, the future-only membership bar gate, the exact execution mapping, the
existing six `FactorSpec`s, and the preregistered 36-family variant set. It
stops before forward returns, turnover, costs, and PnL.

For every signal timestamp `t`, each asset is truncated to `timestamp <= t`
before `compute_factor_frame` is called. The existing
`build_long_only_target_weights` helper supplies the fixed long-only top-2,
equal-weight, high/low direction, cutoff-tie, and all-cash semantics. No
variant is selected or removed.

## Frozen real offline result

- Ledger identity: `010881f9749218f129fab363ed9ebfc313182df2c4459ddafdbbed5465a2e65c`
- Signal timestamps: 161 (`2026-08-02T16:00:00Z` through `2026-08-09T08:00:00Z`)
- Family members: 36 (six factors × three horizons × two directions)
- Factor-score rows: 5,796
- Decision rows: 5,796
- Asset-weight rows: 34,776
- All-cash decisions: 0 in this frozen slice; all-cash remains a retained
  mechanical outcome and is covered by unit tests.
- `return_computation_authorized=false`
- `turnover_computation_authorized=false`
- `cost_application_authorized=false`
- `pnl_computation_authorized=false`
- `profitability_evidence=false`
- `readiness_changed=false`

Artifacts are in `reports/prospective-portfolio-ledger/` and include
factor-scores, decisions, weights, constraints, and the final marker. The
ledger marker binds the preregistration, mechanism, membership gate,
prospective extension, execution mapping, six baseline/append hashes, factor
specs, 161 timestamps, 36-family order, policies, artifact hashes, and bias /
authorization flags. Output directory, process id, wall-clock time, and
duration are excluded from identity.

An independent replay in `reports/prospective-portfolio-ledger-replay/`
produced the same ledger identity and byte-for-byte identical artifacts.

## Guardrails and tests

The new regression tests cover future-row perturbation, complete family
emission, cutoff ties retaining all-cash rows, top-2 weight conservation, and
fixed factor order. The CLI rejects upstream identity/path/hash drift,
incomplete signal/family grids, dataset discontinuity, and claims that would
authorize economic computation.

