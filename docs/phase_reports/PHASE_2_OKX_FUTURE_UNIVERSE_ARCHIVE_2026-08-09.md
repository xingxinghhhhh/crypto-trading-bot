# Phase 2 — Direct-OKX Future-Only Universe Snapshot Archive (2026-08-09)

## Objective

Preserve future-only public OKX instrument snapshots and make adjacent
eligibility changes auditable while keeping the 2026-08-02 baseline immutable.
This node is operational evidence only; it does not repair historical
point-in-time membership or authorize PnL.

## Frozen contract

- Policy: `okx_future_only_membership_archive_v1`.
- Eligibility reuses `config.okx-universe-intake.example.yaml` (SHA-256
  `6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84`).
- Baseline capture identity:
  `b96aa6011796c8e2f1e0d7826c0f95b9f5be15fbdf2cf38862493f3fc75da451`.
- Baseline report SHA-256:
  `f193b1f999843b5903d0934b6e6c3f6d758b3128101b9fa4bc1516a2bd4fea3d`.
- Tracked IDs: BTC-USDT, ETH-USDT, SOL-USDT, KNC-USDT, SWFTC-USDT, BICO-USDT.
- Exit signal: effective at `current_snapshot_received_at` only.
- Historical retroactive exit, membership carry, automatic replacement, and
  historical backfill are all false/prohibited.

## Real direct-OKX evidence

The public `GET /api/v5/public/instruments?instType=SPOT` snapshot was captured
at `2026-08-09T10:22:11.263565+00:00`. Its content-addressed snapshot identity is
`d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e` and the
instrument count is 1,336. The raw response is retained beside the normalized
eligible and tracked artifacts.

The adjacent baseline-to-current audit produced transition identity
`706ba3e03bfc199ba332da1fd23cec7554ad5279c44f895b8eb302d3bfe244ef`, status
`verified_okx_future_universe_transition`, and exactly one normalized change.
The fixed tracked set remains observable without replacement; readiness remains
false. A second output-directory replay of the same pinned artifacts must match
all report and CSV hashes, and it did: the replay directory matched the original
transition directory byte-for-byte. The transition report SHA-256 is
`e129f1e9754a1321b2e605996a8990418718a98be676a89970aea4ae94791f49`; changes,
tracked, and policy CSV hashes are respectively
`f2e01f0dfe934ee074e6ad31065f6e1051a0e6755f2447ec9d2228276f54e65d`,
`862a232a1a8d365f76c96004711352695b3415c7eaaa100dc9407afc1bf79ec3`, and
`5869edac6976237175f88ea557a5ffc5903b93d6082e95f123f015b7654ee488`.

## Evidence limitations

This archive is future-only. It does not provide historical point-in-time
membership, recover delisted instruments, remove survivorship bias, rank or
replace assets, select a variant, or calculate costs/turnover/returns/PnL. The
canonical market `volume` field remains the capacity baseline; mutable raw
`volCcy`/`volCcyQuote` fields are preserved but are not used to rewrite prior
history.

## Validation

The node has unit coverage for config pinning, snapshot tamper detection,
tracked-status policy, legacy boolean normalization, mocked capture, adjacent
transition replay, timezone validation, and output-path containment. Ruff and
targeted mypy pass; the full test/coverage gate is required before the next
planning handoff.
