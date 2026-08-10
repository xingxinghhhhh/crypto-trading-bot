# Phase 2 — Direct-OKX six-asset 1h execution mapping feasibility

Date: 2026-08-09

## Scope and safety boundary

This node consumes the frozen Direct-OKX six-asset 1h migration and the public
response mutability audit. It verifies the structural and timestamp-semantic
prerequisite for a future PnL computation: signal timestamp `t`, completion at
`t+1h`, and execution price equal to the exact `open` at `t+2h`.

It does not compute returns, positions, turnover, fees, slippage, capacity,
equity, benchmark, or PnL. It does not authorize trading, profitability, factor
approval, or readiness. The current-live convenience membership and unresolved
survivorship limitations remain explicit.

## Frozen inputs

- Migration: `67fb338366f01f6ac03e0280f374fb8c6c5578029d7756286d5315ac1cc3b698`
- Migration marker SHA-256:
  `33374e95f13fabff61a4611fcbb44b46c027b6be271ff1975b71b772ea3376e6`
- Panel: `okx_btc_eth_sol_knc_swftc_bico_1h_direct_v1`
- Panel SHA-256: `59cb360a26c373c69407dac2f495b90fd3a848c7e364f464b2a0e295429c8d28`
- Public response mutability audit:
  `3ff4b3736a00eb55b34bcc49c87fcc4dd3bb0d70f741399c1112a3711a9dfb2c`
- Baseline capture: `ff289f634e1d2c258e5e3d0314d751f7dd3cb53f3e2f3ad69e5356e65ab0ee8b`
- Comparison capture: `5cba528b4210be98a72f92675f66da837f469a9102a6cf54c71508748cfc808e`

## Exact mapping evidence

The six-asset inner-exact Panel contains 40,191 hourly signal timestamps. The
audit emits 241,146 asset/timestamp rows:

- 241,134 rows are exact finite `open` values at `t+2h`;
- 12 rows are the fixed two final-panel-timestamp tail rows, disclosed as
  `tail_outside_common_panel`;
- internal missing rows: 0;
- no nearest lookup, fill, resample, carry, substitution, or tolerance is used.

The reusable mapping helper is shared with the legacy timestamp audit, so the
legacy audit's existing bytes and hash remain unchanged.

## Result

The content-addressed audit marker is:

- Audit identity:
  `48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb`
- Mapping CSV SHA-256:
  `03667e516d91a1913594a172214dd47b2a0b10a4f11019fd387874712446fc96`
- Marker JSON SHA-256:
  `39d0a719f1546c5249d57c11cc2422f0dc396a8e9a5f55f1742a60d321899074`

The report sets:

- `structural_common_next_open_mapping_verified=true`
- `timestamp_semantics_uniformly_verified=true`
- `execution_price_mapping_feasible=true`
- `pnl_prerequisite_timestamp_mapping_satisfied=true`
- `pnl_computation_authorized=false`

The next development node may address a separately scoped returns/PnL evidence
design only after planning approval; this node itself makes no profitability or
readiness claim.
