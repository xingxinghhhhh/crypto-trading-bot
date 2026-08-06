# Phase 2 横截面 Rank IC 证据 MVP（2026-08-02）

## 结论

已在冻结的 Registry-backed Dataset Panel 上建立最小横截面因子研究闭环：每个资产先在自己的完整、
已验证历史上计算现有因子和 `t → t+h` future return，再限制到 Panel 公共 timestamp；只有全部三个资产
同时存在有限配对观察时，才计算该时点的横截面 Spearman Rank IC。

该节点只证明横截面计算、身份绑定和人工复算链路。它不构造组合、持仓、换手、成本或 PnL，不执行显著性、
bootstrap、permutation 或 multiple-testing，也不进行因子筛选或 readiness 晋级。

## ChatGPT 规划校正

ChatGPT 选择的单一节点为“冻结 Panel 上的最小横截面 Rank IC 证据”。仓库复核后协商并接受三项修订：

- 研究模块放在现有包根 `crypto_bot.cross_sectional_factor_research`，不为单一文件建立平行 `research` 包。
- Research config 只声明 schema、factor specs 和 horizons；`panel_id` 仅由 CLI 提供，同一配置可原样用于 1h/4h。
- 三个产物使用 `research_sha256` 内容寻址；先提交两个全新 CSV，最后提交 JSON。失败不会覆盖既有完整报告，
  已存在的同名内容若不一致则 fail closed。

## 计算语义

- 复用现有 `compute_factor_frame` 和 `compute_forward_returns`，不复制因子或标签公式。
- 六个因子与既有单资产研究一致；horizons 固定为 1、4、16 根对应 timeframe 的 K 线。
- `required_asset_count` 固定等于 Panel constituent 总数，当前为 3；不提供 2-of-3 或可调 `min-assets`。
- 横截面 factor/return 均升序排名，tie 使用 average rank，不随机、不按 symbol 打破。
- 状态优先级固定为 insufficient assets、constant both、constant factor、constant forward return、valid；
  常量向量不写为零 IC。
- 有效 Rank IC 为 factor average rank 与 forward-return average rank 的显式 Pearson correlation。
- 每个 factor/horizon 汇总有效数量、均值、中位数、样本标准差、正值比例和唯一 IC 值数量，不设置正收益门槛。

## 确定性审计产物

每次研究输出三个 content-addressed 文件：

- JSON 主报告：Panel/component 身份、计算 policy、计数、汇总、artifact hash 和安全状态。
- IC time-series CSV：包含所有候选 timestamp，包括明确的跳过状态。
- Valid observations CSV：每个有效 IC timestamp 恰好三行，可人工复算全部数值 IC。

`research_sha256` 绑定 schema、Panel ID/hash、alignment、timestamp semantics、component dataset ID 与双哈希、
规范化 FactorSpec/horizons、全部计算 policy 和两个 CSV 内容哈希；不包含输出路径、时间、PID 或耗时。

## 真实 1h/4h 研究证据

| Panel | Panel SHA-256 | Research SHA-256 | IC rows | Valid observation rows | 每组合有效 IC 范围 | mean IC 范围 |
|---|---|---|---:|---:|---:|---:|
| `btc_eth_sol_1h_v1` | `f1f83c73f11c46f66169f153e3784d91dc35fad5b03dfa0d6b41f9df97f61f33` | `1213661b9a3e1ae1bb6e3de99a5248fb54ae5c66dc51b29f606df24f9bd82408` | 367,632 | 1,101,672 | 20,388–20,419 | -0.036436652138–0.054100451246 |
| `btc_eth_sol_4h_v1` | `e9e44663a21a30e60a13572d24d21f0503caa08203df3bacf68eff897a4c7ff8` | `fb706b87b822e40a2d8b3d6f6553f507c5604b73579c28864fcc93f00eb9805c` | 225,594 | 675,558 | 12,497–12,528 | -0.030684909586–0.084580299272 |

两个 Panel 均有 6 factors × 3 horizons = 18 个汇总组合；全部组合至少有一个有效 IC，全部满足候选数等于
valid 与各跳过状态之和。相同输出目录的第二次执行得到相同 research hash、文件名和内容，既有内容寻址文件
通过碰撞检查后按幂等成功处理。

Artifact 内容哈希：

- 1h IC CSV：`8e0a43489b95b8028da3ead3943f86c62b6d165451ab3fe4cea78a457b2a742d`
- 1h observations CSV：`5b51aec08775c74c16b7da44db46e4e6c2c582c3d99697076c567ea0b5143900`
- 4h IC CSV：`6316612b04314b042595744ee28ad1269abfbe603a553702f8712220ba1c687a`
- 4h observations CSV：`0c0c1734b094c7a799af30ad2dc4a2fdbc1f32c0f1f6581991db6c633c44faf3`

## 自动化门禁

- 横截面、Panel、Registry 和单资产 Factor Research 定向回归：63 passed。
- 横截面模块定向 branch coverage：92.33%。
- 全量测试与 branch coverage：223 passed，85.57%（门槛 85%）。
- Ruff：passed。
- mypy safety/research core：25 source files passed。
- live guard / source safety：3 passed。
- `git diff --check`：无内容错误；仅有 Windows 工作区既有 LF/CRLF 转换提示。

## 研究与安全边界

BTC/ETH/SOL 只有三个固定资产，单时点 Rank IC 取值高度离散，并存在 universe selection、survivorship 和
大市值资产共振风险。大量时间点也不代表统计独立性。上游 timestamp open/close 语义仍无法统一证明，报告继续
传播 `unverified` 警告。真实结果有正有负，不构成显著性、稳定盈利、可执行组合或策略通过证据。

报告固定为 `research_status=cross_sectional_rank_ic_evidence_only`、`readiness_changed=false`、
`automatic_factor_approval=false`。所有策略仍为 `not_ready`，没有修改 RiskManager、Paper、Live、
private API、订单或账户路径。
