# Phase 2 因子研究证据 MVP（2026-07-23）

## 结论

已完成可复现的单资产价格/成交量因子研究闭环：从校验通过的 Replay
数据构造因子和严格前瞻收益标签，计算预测、衰减、分层、换手、相关性、
成本压力和滚动样本外稳定性证据，并导出 JSON/CSV 报告。

该成果只扩展离线研究能力。所有内置策略继续保持 `not_ready`，报告固定
`readiness_changed=false` 和 `automatic_factor_approval=false`，没有新增
private API、账户访问或真实下单能力。

## 本轮实现

- `crypto_bot.factors`
  - 初始注册 6 个因子：4/12 周期动量、20 周期均值回归、低波动、
    成交量动量和趋势质量。
  - 输入要求为按时间升序、时间戳唯一、字段完整的 OHLCV。
  - 因子只读取当前及过去 K 线；前瞻收益通过未来收盘价单独构造。
- `crypto_bot.factor_research`
  - Pearson IC 和 Rank IC。
  - 多预测周期 IC/Rank IC 保留率及符号保持情况。
  - 滚动历史百分位的顶部/底部分位收益、方向收益和换手率。
  - 0/5/10 bps 成本压力。
  - 因子相关矩阵。
  - 滚动训练/测试窗口；每个窗口剔除会跨越边界的前瞻标签。
  - 确定性研究 SHA-256 和原子 JSON/CSV 导出。
- `analyze-factors` CLI
  - 可配置 Replay 周期、预测周期、成本、分位数和滚动窗口。
  - 输出摘要、指标、衰减、相关性、滚动窗口、稳定性和因子值文件。

## 自动化证据

`tests/test_factor_research.py` 验证：

- 1/2 周期前瞻收益严格与未来收盘价对齐。
- 只修改未来 K 线不会改变此前已形成的因子值。
- 合成持久收益样本上的短期动量 IC 和 Rank IC 为正。
- 成本上升不会改善同一诊断收益。
- 因子相关矩阵对角线为 1。
- 基准预测周期的 IC 衰减保留率为 1。
- 滚动稳定性和全部 JSON/CSV 导出存在。
- 输出目录变化不影响研究 SHA-256 和研究结果。

## 真实 OKX 公共行情验证

输入为 Phase 1 真实 OKX `BTC/USDT` 公共行情 recorder 归档：

- 99 根唯一、闭合、校验通过的 `1m` K 线。
- 单批归档与三批重叠归档均归一化为相同数据集。
- 三批归档的 297 行输入包含 198 行完全相同的重叠记录，Replay 严格合并为
  99 根唯一 K 线。
- 两种归档均生成 6 个因子、18 组因子/预测周期指标和滚动样本外证据。
- 两种归档的 dataset SHA-256 和 research SHA-256 完全一致，证明研究结果
  不受 recorder 批次划分影响。
- dataset SHA-256：
  `aa67376457da021c7a431303710ca8bfe234203a886c3468a15e2a1f5247ae05`。
- research SHA-256：
  `c88a7e2860652f745308965c8e650d0f6992ab7237020dfecd39faf84164148f`。

短归档被明确标记为 `short_history_under_1000_bars` 和
`fewer_than_3_rolling_windows`。这次验证只证明真实数据链路、计算和
可复现性，不足以判断因子盈利能力或稳定性。

## 限制与后续

- 当前 IC 是单资产时间序列 IC，不是多资产横截面 IC。
- 分位收益和成本压力是诊断量，不是包含完整订单、成交、滑点和容量约束的
  可执行 PnL。
- 99 根 1m K 线远不足以支持因子选择或策略准入。
- 下一步应持续积累跨市场状态的长历史，增加多资产横截面研究、现金/买入持有
  基准、收益集中和随机化稳健性，并将通过正式评审的证据接入 readiness，
  而不是自动晋级。

## 最终本地门禁

- pytest：171 passed。
- branch coverage：85.23%，门槛 85%。
- 新因子研究定向测试：11 passed，模块覆盖率 93.48%。
- Ruff：passed。
- 扩展 mypy（安全核心、因子研究和 Replay，共 21 个源文件）：passed。
- live guard / source safety：3 passed。
- `git diff --check`：passed；仅有 Windows 行尾转换提示。
