# Phase 2 — Prospective Spread Application Semantics (2026-08-09)

## Outcome

The repository now has a separate, content-addressed policy marker that resolves
the accounting contract's spread-unit blocker without changing the frozen cost or
accounting artifacts. The existing `[0, 5, 10]` stress tiers are explicitly a
prospective, one-way execution friction per asset trade notional, with multiplier
`1`. This is a policy definition, not historical order-book evidence.

CLI:

```powershell
python -m crypto_bot.cli freeze-spread-application-semantics `
  --accounting-contract reports/prospective-economic-accounting/prospective-economic-accounting.<accounting_sha>.json `
  --cost-evidence reports/execution-cost-evidence/execution-cost-evidence.<cost_sha>.json `
  --config config.spread-application-semantics.example.yaml `
  --output-dir reports/spread-application-semantics
```

## Frozen semantics

- `application_semantics=one_way_execution_friction_per_asset_trade_notional`
- `application_multiplier=1`
- `half_spread_conversion=false`; `full_spread_conversion=false`
- `historical_spread_directly_observed=false`
- `quoted_spread_width_claim=false`
- tiers remain exactly `[0, 5, 10]` bps
- all return, turnover, cost-amount, PnL, profitability, and readiness flags remain false

The formula is recorded for a future evaluator only:
`spread_cost_fraction = asset_trade_notional_fraction * spread_bps * 1e-4`.
No real traded notional is substituted and no economic result is emitted.

## Integrity and acceptance

The marker binds accounting identity `a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2`, cost identity `5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21`, their report/dependent artifact hashes, and the two output CSV hashes. Output is atomic, content-addressed, idempotent, collision-safe, and independent of output directory. Source drift, quoted-spread claims, `/2` or `×2` conversions, non-unit multipliers, variant-specific semantics, and any authorization of economic rows fail closed.

This node does not select variants, calculate returns, or authorize paper/live/readiness. The next node must be planned from the resulting repository state.
