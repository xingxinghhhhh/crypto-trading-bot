# Phase 2 横截面 Rank IC 依赖感知校准（2026-08-02）

## 结论

已在冻结的横截面 Rank IC 证据上建立独立的零假设校准节点。该节点完整验证源 JSON、IC CSV、
observations CSV、内容哈希、内容寻址文件名和可重算的 `research_sha256`，然后对每个
`factor × horizon` 的连续有效 IC 序列执行固定 Newey–West/Bartlett HAC 均值检验，并在同一源研究
报告的全部 18 个假设内执行 Holm 调整。

该节点只校准 `H0: mean_rank_ic = 0` 的渐近统计证据。它不选择因子、不构造组合、不计算成本或 PnL，
也不修改策略 readiness。统计显著性、正均值或置信区间不等同于经济价值、可交易性或稳定盈利。

## ChatGPT 规划与仓库复核

ChatGPT 在“提高统计可信度、扩大资产 universe、构造组合/PnL”之间选择了“依赖感知的横截面
Rank IC 零假设校准”：当前冻结证据可以直接消费；扩大 universe 需要先定义上市、存续和纳入政策，
而组合会提前引入仓位映射、再平衡、换手、成本和成交语义。

仓库复核确认：

- 现有源报告字段足以重算 `research_sha256`，无需修改上游研究 schema。
- 两份源制品均使用同一 research hash 的相对文件名，可严格禁止绝对路径和 path escape。
- 1h/4h 共 36 条真实 IC 序列均只有 warm-up 前缀和 forward-return 尾部缺失，没有有效区间内部断点。
- 新模块沿用现有包根布局 `crypto_bot.cross_sectional_ic_calibration`，不建立平行 `research/` 包。
- 统计政策完全冻结且无随机数，不增加 bandwidth、alpha、seed 或多重检验 CLI 参数。

## 统计语义

对长度为 `n`、horizon 为 `h` 的有效 IC 序列：

- 自动 bandwidth：`L_auto = floor(4 × (n / 100)^(2/9))`。
- 重叠收益下限：`L_overlap = h - 1`。
- 实际 bandwidth：`L = max(L_auto, L_overlap)`。
- 样本门槛：`L < n` 且 `n >= max(500, 10 × (L + 1))`。
- 自协方差分母固定为 `n`；Bartlett 权重为 `1 - k/(L+1)`。
- `LRV = gamma_0 + 2 × sum(weight_k × gamma_k)`，`SE_HAC = sqrt(LRV/n)`。
- 双侧原始 p 值使用标准库 `erfc(abs(z)/sqrt(2))`；95% 渐近区间使用固定正态临界值。
- 一个源研究报告是一个 family；原始 p 值按 `(p, factor_name, horizon)` 确定性排序后执行 Holm step-down。

任一源 hash、row count、身份、文件名、路径、timestamp 顺序、有效区间连续性、源均值、样本数、LRV、
标准误或假设 family 完整性不满足要求，整个任务失败且不生成 JSON commit marker。

## 确定性产物

每次输出两个相同 `calibration_sha256` 的内容寻址文件：

- `cross-sectional-ic-calibration.<sha>.hypotheses.csv`：每个假设一行，包含样本边界、bandwidth、
  LRV、HAC SE、z、raw/Holm p、95% CI、naive SE 和 HAC/naive 比率。
- `cross-sectional-ic-calibration.<sha>.json`：绑定源报告与两份源 CSV 哈希、Panel/component 身份、
  timestamp semantics、固定统计政策、hypothesis CSV 哈希和安全边界；JSON 最后写入并作为 commit marker。

`calibration_sha256` 不包含输出目录、绝对路径、当前时间、PID、耗时或 Git 状态。已存在的同名制品必须
逐字节相同，否则 fail closed。

## 真实 1h/4h 校准证据

| Panel | Source research SHA-256 | Calibration SHA-256 | Hypotheses | Bandwidth | HAC SE 范围 | Raw p 范围 | Holm p 范围 |
|---|---|---|---:|---|---|---|---|
| `btc_eth_sol_1h_v1` | `1213661b9a3e1ae1bb6e3de99a5248fb54ae5c66dc51b29f606df24f9bd82408` | `e67b17c84d3db75e7f99379b471fbd78817fb2fedcd1675289a15b2bd4ec0334` | 18 | 13、15 | 0.004731281343–0.014254025528 | 2.832779e-13–0.861124 | 5.099003e-12–1.0 |
| `btc_eth_sol_4h_v1` | `fb706b87b822e40a2d8b3d6f6553f507c5604b73579c28864fcc93f00eb9805c` | `c91c3f2d0693e80f6e0d4eccc882849e437a90886fd339ab3c1b0ad32db227b2` | 18 | 11、15 | 0.006160193653–0.018864113755 | 1.488224e-11–0.914967 | 2.678803e-10–1.0 |

描述性核验中，1h 有 12 个 raw p 小于 0.05、11 个 Holm p 小于 0.05；4h 分别为 7 和 5。
这些计数不构成预注册实验、跨 Panel/历史实验的全局 multiple-testing 控制，也不用于因子批准。

Artifact 内容哈希：

- 1h hypothesis CSV：`436fa177ef646ccb2aec212bff3e0dcfd0094540dce4ca73494e167f4fe417b3`
- 1h JSON：`cdee9e14971e0707d725a1eda56efa75eddf5f6c8968045c1aefba3ec684c9b7`
- 4h hypothesis CSV：`811fac5290c55676d3ddd8af20010ae45c66b4d8425c6b8cc1dbe8fb6cb0400e`
- 4h JSON：`0e034abf2edfff6d19028536a6e3cfe372171513c5c3dddf28828ca86b72ab73`

4h 使用另一个输出目录重复运行后，calibration hash、CSV 和 JSON 均逐字节一致。

## 自动化门禁

- 新增校准测试：20 passed；新模块 branch coverage 85.33%。
- 横截面校准、研究、Panel、Registry 与单资产因子研究定向回归：83 passed。
- 全量测试与 branch coverage：243 passed，85.54%（门槛 85%）。
- Ruff 全仓：passed。
- mypy safety/research core：26 source files passed。
- live guard / source safety：3 passed。
- `git diff --check`：无内容错误；仅有 Windows 工作区既有 LF/CRLF 转换提示。

## 研究与安全边界

三资产横截面的统计能力仍很有限，固定资产池仍有 selection、survivorship 和同质性风险；HAC 只能处理
所采用模型下的时序依赖，结果仍是渐近近似。Holm family 只覆盖单个源报告的 18 项，不覆盖另一个 timeframe、
既往实验或未来探索。上游 timestamp semantics 继续为 `unverified`。

报告固定为 `statistical_status=dependence_aware_mean_rank_ic_calibration_only`、
`readiness_changed=false`、`automatic_factor_approval=false`。本节点没有修改 RiskManager、Paper、Live、
private API、账户、订单或策略准入路径。
