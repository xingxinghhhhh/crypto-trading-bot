# Phase 2 多资产 Dataset Panel 对齐与审计（2026-08-02）

## 结论

已在 Dataset Registry 之上建立严格、可复现的多资产 Panel 对齐层。该节点只生成时间戳完全相等的 UTC
内连接 Panel，并审计来源身份、覆盖范围、边界裁剪、内部缺失和确定性哈希；不重采样、不填充、不平移时间戳，
也不计算收益、因子、IC、组合或盈利结论。

ChatGPT 规划阶段最初同时要求“所有来源必须通过连续性校验”和“缺一根中间 K 线仍成功生成 Panel”。结合现有
`validate_ohlcv_csv` 的严格语义，这两项要求冲突。经代码证据回传后，计划修正为：任何 gap、重复、schema
或质量失败均拒绝整个 Panel；缺中间 K 线测试只验证失败传播，不生成成功结果，也不增加 `allow_gaps`。

## 实现

- `crypto_bot.market.dataset_panel`
  - 校验 Panel 配置、唯一数据集、不同 symbol、统一 timeframe 和 `inner_exact` 对齐方式。
  - 对每个来源调用 Registry 审计；质量失败或预期哈希漂移时拒绝整个 Panel。
  - 对规范化 OHLCV 的 UTC 时间戳做精确交集，不进行 resample、fill、shift 或 return 计算。
  - 输出固定 symbol/字段顺序的宽表，以及 union、intersection、coverage、边界裁剪和内部缺失统计。
  - Panel SHA-256 同时绑定 schema、配置、来源 canonical hash 和规范化 Panel 内容。
  - 审计报告不含生成时间或绝对路径，可原子、字节确定性地写入 JSON。
- `dataset-panel-audit` CLI
  - 默认使用 `config.datasets.example.yaml` 与 `config.dataset-panels.example.yaml`。
  - 必须指定 `--panel-id`；可通过 `--export` 覆盖默认报告路径。
- `config.dataset-panels.example.yaml`
  - 注册 BTC/ETH/SOL 的 1h 与 4h 两个严格 Panel。
- CI
  - 将 `dataset_panel.py` 纳入安全和研究核心 mypy 门禁。

## 真实 Panel 审计

| panel_id | timeframe | intersection | union | coverage | 公共区间（UTC） | panel_sha256 |
|---|---|---:|---:|---:|---|---|
| `btc_eth_sol_1h_v1` | 1h | 20,424 | 20,784 | 0.982678983834 | 2024-01-01 00:00 至 2026-04-30 23:00 | `f1f83c73f11c46f66169f153e3784d91dc35fad5b03dfa0d6b41f9df97f61f33` |
| `btc_eth_sol_4h_v1` | 4h | 12,533 | 13,574 | 0.923309267718 | 2020-08-11 04:00 至 2026-04-30 20:00 | `e9e44663a21a30e60a13572d24d21f0503caa08203df3bacf68eff897a4c7ff8` |

1h Panel 中 BTC 在公共区间之后多 360 根；4h Panel 中 BTC 和 ETH 在公共区间之前各多 1,041 根。
两个真实 Panel 的所有来源在公共区间内均为零缺失、零丢弃。重复执行后 JSON 报告逐字节一致：

- 1h 报告 SHA-256：`50f70e29e1f1394ec597cbf317ebc00708fa8282ee3e20a32076de46cde24264`
- 4h 报告 SHA-256：`4fd3e331479f091c31c392d2082f6030dd89c5df3103b68477203ebbe17ba107`

## 自动化证据

- Panel、Registry、Factor Research 和 Archive Replay 定向回归：49 passed。
- 全量测试与 branch coverage：202 passed，85.21%（门槛 85%）。
- Ruff：passed。
- mypy 安全和研究核心：24 source files passed。
- live guard / source safety：3 passed。
- `git diff --check`：无内容错误；仅报告 Windows 工作区既有 LF/CRLF 转换提示。

## 研究与安全边界

当前只能确认 BTC 1h 的一份 Binance 来源证据采用 open-time 语义，不能据此推断其余文件，因此 Panel 报告统一将
timestamp semantics 标记为 `unverified`。本节点只提供数据对齐证据，`research_status` 为
`data_alignment_evidence_only`，不会自动批准因子或改变 readiness。所有现有策略仍为 `not_ready`；未连接
长期 Paper、Live、private API、RiskManager 或下单路径，也没有验证稳定盈利能力。
