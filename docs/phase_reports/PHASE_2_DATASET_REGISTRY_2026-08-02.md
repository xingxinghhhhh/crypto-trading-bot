# Phase 2 Benchmark 研究数据集注册与审计（2026-08-02）

## 结论

已为长期历史 CSV 建立可审计的数据身份层。`benchmark-strategies` 现在可通过
`dataset_id` 从 `config.datasets.example.yaml` 解析数据，并在结果中记录原始文件
SHA-256 与规范化内容 SHA-256。该节点不改变策略、因子、readiness、Paper、Live、
private API 或订单路径。

## 实现

- `crypto_bot.market.dataset_registry`
  - 校验 schema version、唯一 dataset ID、symbol/timeframe、来源三态和证据文件。
  - 拒绝绝对路径、目录逃逸、缺失的来源证据和非法 expected hash。
  - 复用 `validate_ohlcv_csv`，不复制 OHLCV 质量规则。
  - 计算 raw SHA-256 和固定列顺序、UTC timestamp、时间排序、数值格式的 canonical SHA-256。
  - 对 expected 行数、首尾时间和双哈希执行漂移检测，原子导出确定性 JSON。
- `dataset-registry-audit` CLI
  - 默认读取 `config.datasets.example.yaml`。
  - 默认输出 `reports/dataset_registry_audit.json`。
- `benchmark-strategies`
  - 注册表启用时必须使用 `dataset_id`。
  - CSV/JSON row 与 matrix dataset summary 写入 `dataset_id`、`raw_sha256`、`canonical_sha256`。
  - 质量或 identity 漂移时生成 error row，不运行该数据集的策略研究。

## 六份真实数据审计

| dataset_id | bars | 时间范围（UTC） | duplicate | gap | missing bars |
|---|---:|---|---:|---:|---:|
| btc_usdt_1h_v1 | 20,784 | 2024-01-01 00:00 — 2026-05-15 23:00 | 0 | 0 | 0 |
| eth_usdt_1h_v1 | 20,424 | 2024-01-01 00:00 — 2026-04-30 23:00 | 0 | 0 | 0 |
| sol_usdt_1h_v1 | 20,424 | 2024-01-01 00:00 — 2026-04-30 23:00 | 0 | 0 | 0 |
| btc_usdt_4h_v1 | 13,574 | 2020-02-19 16:00 — 2026-04-30 20:00 | 0 | 0 | 0 |
| eth_usdt_4h_v1 | 13,574 | 2020-02-19 16:00 — 2026-04-30 20:00 | 0 | 0 | 0 |
| sol_usdt_4h_v1 | 12,533 | 2020-08-11 04:00 — 2026-04-30 20:00 | 0 | 0 | 0 |

审计结果为 6/6 valid。BTC 1h 仅标记为 `partial`，因为仓库同时保留 Binance
Data Vision 来源文件和 OKX 公共历史导入配置，不能证明 canonical 文件端到端来自单一
provider。ETH/SOL 1h 标记为 `unknown`；4h 仅确认仓库内中间版本，provider 未被推断。

## 自动化证据

- dataset registry、benchmark 和 data-quality 定向回归：25 passed。
- 六份 canonical CSV 审计：6/6 valid，均为零重复、零 gap、零 missing bar。
- benchmark 配置解析：6/6 registry valid，6/6 双哈希存在。
- Ruff：passed。
- dataset registry 与 benchmark mypy：passed。
- 全量测试与 branch coverage：187 passed，85.17%（门槛 85%）。

## 研究边界

本节点只解决 benchmark 长期历史数据的可追溯性。Recorder/Replay 的 99 根 OKX 1m
样本仍只用于实时数据管线和因子计算可复现性验证，不与长期历史 CSV 合并。本节点没有
验证盈利能力，也没有让任何策略从 `not_ready` 晋级。
