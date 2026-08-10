# Phase 2 — Prospective Economic Cost Scenarios (2026-08-09)

## Outcome

The repository now freezes the complete cost-scenario topology and capacity
application policy without calculating any real economic value.

```powershell
python -m crypto_bot.cli freeze-prospective-economic-cost-scenarios `
  --accounting-contract reports/prospective-economic-accounting/prospective-economic-accounting.<accounting_sha>.json `
  --cost-evidence reports/execution-cost-evidence/execution-cost-evidence.<cost_sha>.json `
  --spread-semantics reports/spread-application-semantics/spread-application-semantics.<semantics_sha>.json `
  --config config.prospective-economic-cost-scenarios.example.yaml `
  --output-dir reports/prospective-economic-cost-scenarios
```

## Frozen policy

- Full Cartesian product: `spread=[0,5,10] × slippage=[0,5,10]`, fee always `100` bps.
- Exactly nine mandatory scenarios; the unique primary is `100 + 10 + 10 = 120` symbolic bps.
- All three friction components are one-way per `asset_trade_notional_fraction`, multiplier `1`, combined additively, with no intra-execution compounding.
- Capacity is an independent `hard_scale_constraint_not_cost` using canonical base volume, `1%` participation, execution-open anchoring, and per-asset/per-execution enforcement with no cross-asset netting.

The symbolic formulas are contract fixtures only. No real trade fraction, fee,
spread, slippage, capacity pass/fail, return, or PnL row is emitted.

## Integrity and fail-closed behavior

The content-addressed marker binds the accounting identity
`a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2`, cost
identity `5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21`,
and spread-semantics identity
`6622da645ef1b665fcb49357faefecc483ac61c2d2b1faf89a9f1249f17e8380`, plus
their dependent artifact hashes and four output CSV hashes. It rejects source
drift, incomplete or duplicate Cartesian scenarios, non-primary 10/10 policy,
non-one-way components, capacity-as-cost, variant-specific policies, and any
economic authorization. Outputs are atomic, idempotent, collision-safe, and
independent of output directory.

This node does not select a strategy variant, calculate economics, or authorize
paper/live/readiness.
