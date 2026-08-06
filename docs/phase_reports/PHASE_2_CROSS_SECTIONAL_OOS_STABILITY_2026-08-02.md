# Phase 2 横截面 Rank IC Purged Expanding-Window OOS 稳定性（2026-08-02）

## 结论

已在两套冻结的横截面 Rank IC 源证据上完成固定的、事后时间序列 OOS
稳定性分析。该节点不选择因子、不改变方向、不调参，也不读取统计校准结果；它直接复用
公共的源证据校验接口，并对源 JSON、IC CSV、observations CSV、内容哈希、行数、
内容寻址文件名、可重算 `research_sha256`、共同候选时间轴和有效区间连续性执行 fail-closed
校验。

该节点只提供描述性稳定性证据，不运行零假设检验、HAC、bootstrap 或 permutation，
不构造组合、不计算成本或 PnL、不证明可交易性或盈利能力，并保持
`readiness_changed=false` 与 `automatic_factor_approval=false`。

## ChatGPT 规划与仓库复核

ChatGPT 在校准节点完成后选择了“冻结横截面 Rank IC 的 Purged Expanding-Window OOS
稳定性证据”作为唯一下一开发节点。仓库复核发现源 IC 的面板起点存在因子 warm-up
形成的结构性无效前缀，因此向 ChatGPT 提交了冲突说明。双方冻结的修正规则为：

- 候选时间轴、50% 初始历史和 10 个 OOS 折均按全部共同候选时间戳计算，不压缩、不填充。
- 每个 `factor × horizon` 允许从 Panel 起点到首个有效 IC 的单一连续结构性无效前缀。
- 首个有效值之后直到全局 forward-return 无效尾部之前不得出现内部断点；发现断点则整项失败。
- OOS 折在持有期清洗后必须全部有效；全局无效尾部不得进入 OOS 指标。
- 训练与 OOS 最小样本门槛按实际有效 IC 数计算，而不是原始候选数计算。
- 源证据校验与加载接口提升为 `cross_sectional_ic_calibration.py` 的公共只读接口，校准和
  OOS 两个消费者共用同一套规则，不复制验证实现。

## 固定折分与清洗语义

候选数为 `T`、持有期为 `h` 时：

1. 前 `floor(T/2)` 个候选时间戳构成首折之前的初始历史。
2. 后半段按时间顺序切成 10 个连续、不重叠的 OOS 折；余数依次分配给最早的折。
3. 第 `k` 折的训练窗口从 Panel 起点扩展到该折起点之前。
4. 训练窗口删除最后 `h` 个候选时间戳，保证最后一个训练观测的 `t+h` 严格早于测试起点。
5. 每个 OOS 折删除最后 `h` 个候选时间戳，保证所有测试观测的 `t+h` 仍在该折内。
6. 每折训练有效数必须至少为 1000，OOS 有效数必须至少为 500。

每折固定输出训练与 OOS 的 mean、median、sample std、positive ratio 以及
`OOS - train` 差值。每个因子/持有期摘要固定输出 pooled OOS 指标、10 个折均值的
mean/median/sample std、正均值折比例、最好/最差折均值，以及相邻严格正负号切换次数。
零值不计为正负号切换。

## 确定性产物

每次运行生成三个共享 `analysis_sha256` 的内容寻址文件：

- `cross-sectional-oos-stability.<sha>.folds.csv`：每个源假设 10 行，真实 Panel 共 180 行。
- `cross-sectional-oos-stability.<sha>.summaries.csv`：每个源假设一行，共 18 行。
- `cross-sectional-oos-stability.<sha>.json`：绑定源报告与源 CSV 哈希、Panel/component
  身份、因子定义、持有期、候选数、全部折分/清洗/样本/指标政策和两个输出 CSV 哈希；
  JSON 最后写入并作为 commit marker。

身份不包含输出目录、绝对路径、当前时间、PID、耗时或 Git 状态。相同源证据在不同输出
目录运行时，三个产物必须逐字节相同；同名内容寻址文件若字节不一致则 fail closed。

## 真实 1h/4h OOS 证据

| Panel | Source research SHA-256 | OOS analysis SHA-256 | T | 折行 | 摘要行 | pooled OOS 有效数（h=1/4/16） |
|---|---|---|---:|---:|---:|---|
| `btc_eth_sol_1h_v1` | `1213661b9a3e1ae1bb6e3de99a5248fb54ae5c66dc51b29f606df24f9bd82408` | `caa4eec43af5a1dcb4465c0b67a1975eb04c8b8e37fc5a15d0a4a1bd211f372f` | 20424 | 180 | 18 | 10202 / 10172 / 10052 |
| `btc_eth_sol_4h_v1` | `fb706b87b822e40a2d8b3d6f6553f507c5604b73579c28864fcc93f00eb9805c` | `2c361201e53a6ad26c89aaf47aa6e99955c0c26986cd001e78521e6971ed48fe` | 12533 | 180 | 18 | 6257 / 6227 / 6107 |

1h 后半段 10212 个候选时间戳的前两折各 1022 个，其余各 1021 个；4h 后半段
6267 个候选时间戳的前七折各 627 个，后三折各 626 个，与冻结政策完全一致。

描述性结果中，1h 有 7/18 个因子/持有期的 pooled OOS 均值为正，均值范围为
`-0.056134486827` 到 `0.057600477517`；4h 为 10/18，范围为
`-0.025135090879` 到 `0.061988116268`。正均值折比例和相邻符号切换显示稳定性并不一致，
因此这些结果不能当作因子批准或盈利结论。

产物文件 SHA-256：

- 1h folds CSV：`5ff50d21a431bf0c13ba3661af539196771e32e4b5a940c3553a4dfab80562c7`
- 1h summaries CSV：`a478dc3f58269987e0f2132a313d6b543b5151e61efcffc3ea1bfbc3cf15e4d3`
- 1h JSON：`32ead338ae1a423a82f820a48feac260decdbed589e2be0c86e478318fafc707`
- 4h folds CSV：`6ba89a630932327d815dd840c4e40e2db94457cdc1690cb19304bac5b52cc881`
- 4h summaries CSV：`c655714ee8e5979e813b05b891a232f0eb11ede992a659bc1cdd7be23b09bd81`
- 4h JSON：`b99bece89b0bab756f8c5dba18a6875ab309edb5f0d7128d22b8580db5b42a63`

两套 Panel 均在独立输出目录重复运行，所有 JSON/CSV 逐字节一致。

## 自动化门禁

- 新增 OOS 稳定性测试：11 passed；新模块 branch coverage 86%。
- 全量测试与 branch coverage：254 passed，85.60%（门槛 85%）。
- Ruff 全仓：passed。
- mypy safety/research core：27 source files passed。
- live guard / source safety：3 passed。
- `git diff --check`：无内容错误；仅有 Windows 工作区既有 LF/CRLF 转换提示。

## 研究与安全边界

三个固定资产仍构成统计能力有限且有 selection/survivorship/同质性风险的横截面；
分析是对已观察数据的事后切分，不是预注册实验。每折独立清洗会有意丢弃折边界候选时间戳，
而 timestamp semantics 仍为 `unverified`。本节点没有修改 RiskManager、Paper、Live、
private API、账户、订单或策略准入路径。
