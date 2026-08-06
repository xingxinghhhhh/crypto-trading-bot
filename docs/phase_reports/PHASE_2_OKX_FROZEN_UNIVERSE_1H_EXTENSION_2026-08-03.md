# Phase 2 — Frozen convenience universe 1h history extension

Date: 2026-08-03

## Outcome

The exact KNC/SWFTC/BICO selection frozen by the 2026-08-02 OKX convenience snapshot
was extended with direct public 1h history. No instruments endpoint was called, no
asset was reselected, and no current-time boundary was used. The histories were
promoted into an independent BTC/ETH/SOL/KNC/SWFTC/BICO 1h Registry and exact Panel.

This is a data prerequisite for a later fixed OOS run. It is not Rank IC, HAC/Holm,
OOS, portfolio, PnL, profitability, factor approval, or readiness evidence.

## Frozen input and capture

- Source 4h promotion SHA-256:
  `3c7cbbbd5b3fbf5420a67df7e877c9899d8c83b97823150dde9747ee414e57f9`
- Original snapshot received at: `2026-08-02T15:49:19.356645+00:00`
- Fixed assets, in order: `KNC-USDT`, `SWFTC-USDT`, `BICO-USDT`
- Fixed window: `2022-01-01T00:00:00Z..2026-08-02T14:00:00Z`
- Bar/limit: `1H`, 300
- Rows/pages per asset: 40,191 / 134
- Capture SHA-256:
  `a15090b348e61bd6bc1453dcb67c2f9ad07986958c81e054d3f82841ee390461`
- Capture report file SHA-256:
  `4fef070744ca2b994edfa3b76427baacd503c00525d0483cb9cae5c5fe58c583`

CSV raw/canonical SHA-256:

- KNC: `e8cc7e87078afeba7d372bf901c3c31b51dccd96a9cb9cfa3eefb5af5be7e27f` /
  `650ea225d3ad7f28ac7aba842dba12df7f3c2a8df4127aaec7a316c2c7e14526`
- SWFTC: `51f4347d7b9f11d84dbdf7aa060bb39015acaa8d12f7d46d9aca7ca01bb2c78c` /
  `9586d251f953b00e85f2966bad031b1fe596eb29e839cd3b384950cc22487cbe`
- BICO: `2dac269642ec23f5dbd170ada3e0a7951ad0aa1e06404a8f29b2deb6108c9bc0` /
  `2dac269642ec23f5dbd170ada3e0a7951ad0aa1e06404a8f29b2deb6108c9bc0`

Every raw bundle was independently replayed. All pages use an exclusive decreasing
`after` cursor, every response row has nine string fields and `confirm=1`, and every
normalized history has no duplicate, conflict, missing, or off-grid timestamp.

## Independent Registry and Panel

- Promoted Registry SHA-256:
  `51ebbf3f97658fa8f36e5cd8f37742047727887957b347ba0399459b9de22975`
- Promoted Panel config SHA-256:
  `9e5a03f60327ce77a417471f513c256b740bf440e2bfa950d953a937ca068ff0`
- Panel SHA-256:
  `b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054`
- 1h promotion SHA-256:
  `36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e`
- Common window: `2024-01-01T00:00:00Z..2026-04-30T23:00:00Z`
- Intersection / union / coverage: 20,424 / 40,191 / `0.508173471673`
- New-asset head/tail drops: 17,520 / 2,247 each
- BTC head/tail drops: 0 / 360
- ETH/SOL head/tail drops: 0 / 0
- Missing inside common window: 0 for every component

Two independent promotion output directories produced identical filenames and bytes.
The public promotion validator also replays the capture, Registry audit, Panel audit,
lineage manifests, stable CSV hashes, and exact alignment.

## Preserved identities and limitations

- Original Registry SHA-256 remains
  `15f2ac5e77a812196394d9d7811ee2ce66a391d6b41d83187e050fdbb40ce509`.
- Original Panel config SHA-256 remains
  `c29cca91009a27d0faad0a3fd9f2d216b9df7cdf10ca18161c33b0f5baee8d22`.
- Original 4h promotion marker file SHA-256 remains
  `f1970eb81825911827b984566e0dd7476285a31ee94056216628b421899fb39e`.

The universe is still a current-live convenience selection, historical point-in-time
membership is not proven, survivorship bias is not resolved, and timestamp semantics
are not uniform. Those limitations must remain bound into any future research output.

## Quality gates

- New extension specialty tests: 8 passed.
- Full suite: 319 passed.
- Branch coverage: 85.05% (required minimum 85%).
- Ruff: passed.
- Mypy safety/research core: 32 source files passed.
- Source/live safety scan: 3 passed.
- `git diff --check`: no content error; Windows CRLF conversion warnings only.
