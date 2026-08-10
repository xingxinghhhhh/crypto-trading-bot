# Phase 2 — Prospective Closed-Epoch Direct-OKX 1h Market-Data Extension (2026-08-09)

## Objective

Extend, without rewriting, the six frozen Direct-OKX datasets so the first
closed future-only membership epoch has public 1h market-data evidence through
its final exact execution anchor. No economic result is calculated.

## Frozen inputs

- Migration identity: `67fb338366f01f6ac03e0280f374fb8c6c5578029d7756286d5315ac1cc3b698`.
- Migration report SHA-256:
  `33374e95f13fabff61a4611fcbb44b46c027b6be271ff1975b71b772ea3376e6`.
- Membership gate identity:
  `737e3da3a1e2db42b87709a23bc5f754bab2ae2a38af36210b28282cfb3d9f48`.
- Membership gate marker SHA-256:
  `1d3a38e9fb1d8b1f1b32d31d866ae6bf2f02ad4f6eb25cf10a4ff5daa533a0e2`.
- Exact execution mapping identity:
  `48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb`.
- Fixed order: BTC-USDT, ETH-USDT, SOL-USDT, KNC-USDT, SWFTC-USDT, BICO-USDT.

The existing migration validator was reused unchanged. All six baseline CSVs
contain 40,191 rows and end at `2026-08-02T14:00:00+00:00`.

## Direct public capture

The existing `download_okx_public_history` helper was reused for each asset with
`bar=1H`, `limit=300`, `confirm=1`, and an exact canonical window of
`2026-08-02T15:00:00Z..2026-08-09T10:00:00Z`. Raw JSONL pages are immutable
evidence; canonical CSVs contain exactly 164 rows per asset. The capture marker
identity is:

`12e2022e6d0abb6fdb36ea9757fe9c2b32bdf5cdb93f9a0ad85981aa21b37629`.

No second network capture is required for acceptance.

## Offline audit

The audit replayed all six raw bundles to canonical rows, validated OHLCV
quality and hashes, and covered all 966 membership-gate rows. It produced:

- Extension identity:
  `b0cbcb119a24ee786a81a6aed7adf4ddb8b11cf93891c19ca1fb21c4d3848146`.
- Append rows: 984 total (164 × 6).
- Gate coverage rows: 966 (161 × 6).
- Status: `verified_prospective_direct_okx_1h_extension`.

The independent offline audit output directory matched the original audit
directory byte-for-byte and hash-for-hash.

## Information visibility and claims

For each gate row, signal, completion, and execution timestamps are required to
be exact present with no fill or substitution. Execution authorizes only the
execution bar `open`; that bar's high, low, close, and volume are not authorized
as information available at the open. The baseline is never replaced by a
40,355-row rewrite. This remains future-only evidence:
`historical_point_in_time_membership=false`,
`survivorship_bias_resolved=false`, `profitability_evidence=false`,
`pnl_computation_authorized=false`, and `readiness_changed=false`.

## Validation

The node includes capture/replay identity checks, strict tail continuity,
window/shape checks, raw/canonical hash verification, exact gate coverage, and
content-addressed atomic artifacts. Full pytest/coverage, Ruff, CI mypy,
source/live safety, and `git diff --check` are required before the next planning
handoff.
