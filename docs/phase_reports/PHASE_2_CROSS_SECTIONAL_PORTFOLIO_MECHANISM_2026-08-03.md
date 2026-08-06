# Phase 2 — Cross-sectional portfolio mechanism and timing feasibility gate

Date: 2026-08-03

## Outcome

A deterministic mechanism contract now freezes how future six-asset cross-sectional
portfolio research would turn a complete rank signal into long-only target weights.
It registers every fixed factor/horizon with both rank directions and deliberately
does not select between them. It also records the conservative signal-completion and
intended execution offsets while blocking any PnL computation because the current
Panel does not have uniformly verified timestamp semantics or a proven common
next-open mapping.

This node calculates no asset return, portfolio return, equity, turnover, fee,
slippage, benchmark, or PnL. It does not approve a factor, change readiness, or
connect to paper, risk, or execution code.

## Fully replayed source chain

- 1h evidence chain SHA-256:
  `06d1ec6bbb6bfc5222cb674227d3eaf926c2c970c1c2dcb8cc635a48f652a001`
- Chain marker file SHA-256:
  `6bf835ce8a466c325a7c36ab1dc3d5cd96d384ecfa63600e90e607b374395f26`
- Research SHA-256:
  `43e7b004b0302d20c1f8612f2b7cb77077fa8de9877325d0d86b6d18d6ef3eb3`
- Calibration SHA-256:
  `531dfd51af0d5738ec2370c2b315a929f2c589e3646142f19fc3d8cfb67be7cf`
- OOS analysis SHA-256:
  `150d6ffb685d2f31b894940751df21c4eb7b2061feb51e683e92d46e7a469e7a`
- Promotion SHA-256:
  `36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e`
- Panel SHA-256:
  `b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054`

The new public chain loader validates the marker and all eight dependencies, replays
the 1h promotion, validates the research IC evidence, and recomputes HAC/Holm and OOS
inside an isolated temporary directory before accepting the chain. It rejects path,
hash, identity, content, or replay drift.

## Frozen mechanism

- Config file SHA-256:
  `03b7e0a22a9fb3e66eae0d3d878ad3b9da46099e0659db8821faf895cec227a4`
- Portfolio type: long-only spot
- Variants: `6 factors × 3 horizons × 2 rank directions = 36`
- Directions: `high_rank_selected` and `low_rank_selected`, both mandatory and
  selection-prohibited
- Target: top 2, equal-weight 0.5/0.5
- Gross/net exposure: 1/1
- Leverage: 1
- Rebalance frequency: one bar
- Insufficient/non-finite/ambiguous-cutoff policy: all cash, no carry, fill, or
  substitution
- Signal stamped at `t`: conservatively complete at `t+1h`
- Intended execution anchor: common next-open at `t+2h`, subject to independent proof

The pure weight builder returns only non-negative weights. It requires exactly six
finite inputs and unique top-2 membership. A tie wholly inside the selected pair is
permitted when the selection boundary remains unique; a tie across the cutoff forces
all cash.

## Real audit result

- Mechanism SHA-256:
  `dcc8e9364efd900487608090ba879194a8a84195cbeab2dc63d0266df97406d5`
- Mechanism marker file SHA-256:
  `d55880b8cd702f91ee8579d5dd792bf84a37296181441c2b4991c4d6578dcead`
- Variants CSV SHA-256:
  `fb39a07ed9002c7c804098b259042399aa53c228a85167a43bc97c36274fe387`
- Constraints CSV SHA-256:
  `f9261b9401fec7fd48b3107d33a203a0cdc5aa207c95bf5bff6af241078f2809`
- Variant rows: 36
- Constraint rows: 16
- `signal_ranking_mechanism_feasible=true`
- `portfolio_weight_mechanism_feasible=true`
- `execution_price_mapping_feasible=false`
- `pnl_computation_authorized=false`

The execution blockers are:

- `timestamp_semantics_not_uniform`
- `legacy_components_not_verified_open_time`
- `common_next_open_mapping_unverified`

Two independent output directories produced the same three filenames and exact same
bytes. Variants and constraints are committed before the JSON marker; failure before
the last step leaves no accepted mechanism report.

## Bias and safety boundary

- Membership remains a static current-snapshot convenience sample.
- Historical point-in-time membership is not proven.
- Survivorship bias is not resolved.
- Delisted assets are not recovered.
- Prior related 4h and 1h results are explicitly disclosed.
- Neither rank direction is preferred, won, or approved.
- Shorting, short borrow, derivatives, and leverage above one are unsupported.
- Profitability evidence, readiness change, and automatic factor approval remain false.

## Quality gates

- Mechanism and chain-loader specialty tests: 14 passed.
- Full suite: 334 passed.
- Branch coverage: 85.09% (required minimum 85%).
- Ruff: passed for all source and test files.
- Mypy safety/research core: 34 source files passed.
- Source/live safety scan: 3 passed.
- No paper, risk, or execution import exists in the mechanism module.
- `git diff --check`: no content error; Windows CRLF conversion warnings only.
