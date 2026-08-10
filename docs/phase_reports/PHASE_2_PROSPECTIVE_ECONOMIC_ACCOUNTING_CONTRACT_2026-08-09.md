# Phase 2 — Prospective Economic Accounting Contract (2026-08-09)

## Scope

This node freezes the future evaluator's accounting semantics without reading
prices or producing returns, turnover values, cost amounts, benchmark returns,
or PnL. The existing prospective portfolio ledger and execution-cost evidence
are replay-checked and content-addressed.

## Frozen real offline result

- Accounting identity: `a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2`
- Target states: 161
- Closed execution-to-execution intervals: 160
- Terminal unscored targets: 1
- Family policy rows: 36
- Benchmark rows: 6 (each weight `1/6`)
- Intervals CSV rows: 161
- `spread_application_semantics_resolved=false`
- `economic_cost_application_ready=false`
- `return_rows=0`, `turnover_value_rows=0`, `cost_amount_rows=0`, `pnl_rows=0`
- `return_computation_authorized=false`
- `turnover_computation_authorized=false`
- `cost_amount_computation_authorized=false`
- `pnl_computation_authorized=false`
- `profitability_evidence=false`
- `readiness_changed=false`

The first execution anchor is `2026-08-02T18:00:00Z`, the last is
`2026-08-09T10:00:00Z`; only the first 160 targets have a next execution
anchor. Horizon 4/16/64 remains a research-family label and the economic
holding period is one bar. The final target is explicitly terminal-unscored;
no future bar is requested or filled.

## Cost blocker

The frozen cost schema contains spread stress `[0,5,10]` bps but does not say
whether those values are one-way friction or full quoted spread. The contract
records this ambiguity as a successful explicit blocker and never divides or
multiplies it. Fee (100 bps taker-conservative), slippage tiers, 1% canonical
volume participation, and uniformity are bound to the existing cost marker but
no cost amount is calculated.

Artifacts are in `reports/prospective-economic-accounting/` and are emitted in
intervals → family-policy → cost-policy → benchmark → constraints → marker
order. The marker binds both upstream markers, transitive identities, schedule
counts, all formulas/policies, artifact hashes, bias flags, and authorization
flags; output directory and runtime metadata are excluded from identity.

