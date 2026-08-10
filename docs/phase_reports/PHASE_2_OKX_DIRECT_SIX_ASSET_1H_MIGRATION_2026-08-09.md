# Phase 2 — Direct-OKX six-asset 1h migration acceptance closure

Date: 2026-08-09

## Scope and safety boundary

This report closes the real-capture acceptance review for the Direct-OKX six-asset
1h migration node. It does not change the migration algorithm, the frozen
configuration, the first capture, the migrated Panel, or any legacy dataset. It
does not compute returns, positions, turnover, fees, slippage, capacity, equity,
benchmark, or PnL, and it does not change readiness or execution authorization.

The capture used only the public OKX `history-candles` endpoint through the already
configured local network path. No credential, private endpoint, account data, or
trading endpoint was used.

## Frozen inputs

- Migration configuration SHA-256:
  `bde8957ab593159e6f53ab978b1bf9509279eff2f2145c3ced7ea3666815961b`
- Source 1h promotion SHA-256:
  `36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e`
- Snapshot received at: `2026-08-02T15:49:19.356645Z`
- Assets: `BTC-USDT`, `ETH-USDT`, `SOL-USDT`
- Window: `2022-01-01T00:00:00Z` through `2026-08-02T14:00:00Z`
- Bar: `1H`; limit: `300`; expected per asset: `40191` bars / `134` pages
- The existing cursor, exclusive `after`, newest-to-oldest, nine-field,
  `confirm=1`, exact-grid, gap, duplicate, and collision policies were reused.

## Capture evidence

The first real capture remains the frozen source used by the migration:

- Capture SHA-256:
  `ff289f634e1d2c258e5e3d0314d751f7dd3cb53f3e2f3ad69e5356e65ab0ee8b`
- BTC bundle: `45e032e30daf782c80fff6b46d366f43de30cb7339cfc0dfeea8bf54878d3c75`
- ETH bundle: `d0b5ff4626b25a88444761f97d9daeb0f52d7d4833c6e427dee5fa07a1b333ed`
- SOL bundle: `0ef1e47943cbca76fde289e20d60a91836a57a9757cf63139d0be19f04f75adf`
- BTC CSV: `37d3af3a57ca833b84573ef3f91c453fd517320f6d6e90c06199432264cc9a9b`
- ETH CSV: `90983727ba098070a6c798725790d4907ef7d9e8eb6d7f59510f202bae49a036`
- SOL CSV: `0df16980c25528c82f6e5589619c04b93992b18f30801537a959b53389f381b7`

The second independent capture was written to a separate ignored output directory
and passed the public validator from raw response bodies:

- Capture SHA-256:
  `5cba528b4210be98a72f92675f66da837f469a9102a6cf54c71508748cfc808e`
- BTC bundle and CSV were byte-identical to the first capture.
- SOL bundle and CSV were byte-identical to the first capture.
- ETH bundle changed to:
  `163f451d423018b7ab227f0b8e4b9b13088733ab659c928b41801f6e99d4f452`
- ETH CSV changed to:
  `2dedbe1ec9dc91df1792d33a0685b068bd2e6a6a88edb8a66da254fdd94d897a`
- Second capture marker file SHA-256:
  `e3afa7ab89ee556e9112ae491e170d9eae99ff1ffd18dd52631ca60a7da4e932`
- Both captures reported `complete_direct_okx_anchor_1h_history`, with 3 assets,
  40191 bars each, and `private_api_used=false`.

## Exact observed difference

The two ETH JSONL histories contain the same 134 pages and the same request
parameters. Exactly one response page differs: page index `0`, row index `127`,
at timestamp `1785222000000` (`2026-07-28T07:00:00Z`). The OHLC fields and base
volume are unchanged, while the returned quote-volume fields differ:

```text
first:  ["1785222000000", "1888.51", "1889.02", "1880.66", "1883.97", "3719.417652", "7010896.57502503", "7010896.57502503", "1"]
second: ["1785222000000", "1888.51", "1889.02", "1880.66", "1883.97", "3719.417509", "7010896.30557013", "7010896.30557013", "1"]
```

This is a real public-history response difference, not a validator failure. The
implementation must not overwrite, normalize, or select one response to force a
matching hash.

## Migrated Panel identity remains unchanged

The previously frozen migration was not regenerated or modified:

- Migration SHA-256:
  `67fb338366f01f6ac03e0280f374fb8c6c5578029d7756286d5315ac1cc3b698`
- Panel SHA-256:
  `59cb360a26c373c69407dac2f495b90fd3a848c7e364f464b2a0e295429c8d28`
- Intersection = union = `40191`; coverage = `1.0`; no boundary or internal drops.
- The independent migration assessment remains six direct components with
  `verified_open_time` and `timestamp_semantics_uniform=true` for the new Panel
  identity only. It does not upgrade the legacy BTC/ETH/SOL identities.
- Membership remains a current-live convenience sample:
  `historical_point_in_time_membership=false`,
  `survivorship_bias_resolved=false`, and
  `delisted_assets_recovered=false`.

## Public response mutability policy

The original cross-network byte-for-byte criterion is not a valid requirement for
an evolving public historical endpoint. A dedicated audit therefore distinguishes
two forms of determinism:

- `capture_replay_deterministic=true`: each frozen raw capture is immutable and
  replays to its own CSV and identity deterministically.
- `network_recapture_byte_identical=false`: a later public recapture is not
  required to return identical bytes; any difference must be versioned and fully
  disclosed.

The mutability audit was run with the two validated captures and the pinned
migration/Panel identities. Its content-addressed outputs are:

- Audit SHA-256:
  `3ff4b3736a00eb55b34bcc49c87fcc4dd3bb0d70f741399c1112a3711a9dfb2c`
- Marker file SHA-256:
  `d6383dfcd6f385cb9f359edb789b67332ee8966db038c4d50972a0e02bfd2836`
- Captures CSV SHA-256:
  `8d3ea3a57b376b0f8f5b0a15add259f6e7e145ef7f5c6b1938a5f4944ba286a4`
- Differences CSV SHA-256:
  `1e63660ba894ddae877efd2644838729ccc04b1d90f6545c6c0dbb3f9c6715d1`
- Policy CSV SHA-256:
  `6944c002ea5121f47383b73dcbb495a5f5577aa7274495ec2e4844bf4791ede4`

The audit reports exactly three field differences, all on ETH page `0`, row
`127`, timestamp `1785222000000`: one `vol` change and two quote-volume changes.
BTC and SOL have zero differences. All nine fields are compared without tolerance,
rounding, field whitelists, or economic-equivalence rules.

The Node 6 acceptance is therefore **closed under the revised policy**: the
baseline migration remains pinned, each capture is replayable, and public response
mutability is an explicit evidence result rather than a hidden determinism failure.
The comparison capture does not replace the baseline and does not modify the
existing migration or Panel.

The following remain unchanged:

- No PnL or cost result is authorized.
- No variant is selected from IC/OOS results.
- No legacy semantics are upgraded.
- No paper/live/readiness path is enabled.
- No old CSV, Registry, Panel, semantics, research, OOS, or mechanism artifact is
  modified.

The reusable audit is implemented by
`src/crypto_bot/market/public_response_mutability.py`, configured by
`config.public-response-mutability.example.yaml`, and invoked with:

```bash
python -m crypto_bot.cli audit-okx-public-response-mutability \
  --baseline-capture reports/okx-direct-anchor-1h-capture/okx-direct-anchor-1h-capture.<baseline_sha>.json \
  --comparison-capture reports/okx-direct-anchor-1h-capture-replay/okx-direct-anchor-1h-capture.<comparison_sha>.json \
  --policy config.public-response-mutability.example.yaml \
  --output-dir reports/okx-public-response-mutability
```

The next planning decision must address the public-history mutability evidence
explicitly; it must not silently treat the second capture as deterministic.
