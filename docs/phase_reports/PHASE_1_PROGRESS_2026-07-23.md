# Phase 1 开发进度（2026-07-23）

## 当前结论

Phase 1 的核心安全与运行基础设施已经形成第一条可测试闭环，但 Phase 1 尚未整体验收完成。

研究冻结结论保持不变：

- 所有内置策略仍为 `not_ready`。
- `not_ready` 策略只能执行单次 Paper 冒烟或无订单 Live Shadow。
- 长期 Paper 需要代码级 `paper_ready` 门禁。
- Demo、private API、API Key 和真实下单仍然禁止。

## 本轮已完成

- 统一策略工厂，CLI、Paper、优化、benchmark 和 filter comparison 复用同一构造路径。
- Paper 从 SQLite 恢复现金、持仓均价、账户/持仓已实现盈亏和峰值权益。
- SQLite schema migration 升级到 version 5。
- 每根已处理 K 线的信号、风控、订单、成交、余额、状态和 processed-bar checkpoint 使用单事务提交。
- checkpoint 写入失败时整轮数据库变更回滚。
- `RiskApproval` 使用 HMAC 签名，绑定完整订单摘要，具有有效期且只能消费一次。
- `risk_checked=true` 布尔值不再构成执行授权。
- Paper 接入止损、止盈、持久化每日交易次数和每日亏损。
- 增加持久化 kill switch、人工 release 和 CLI 操作入口。
- 增加 closed-bar、stale-data、clock-skew 和 gap gate。
- 增加公共行情 recorder：原始响应、闭合 K 线、SHA-256 manifest。
- recorder 增加有界指数退避、尝试次数和重试次数证据。
- 增加 recorder 每日完整性、延迟、重复和缺口汇总。
- 增加正式 Replay CLI：校验 manifest、合并相同重复数据、拒绝冲突和缺口。
- 增加 `1m` 到完整 UTC 对齐 `15m`、`1h`、`4h` K 线的确定性重采样。
- 增加数据集 SHA-256 和排除运行时随机 UUID 的策略行为 Replay SHA-256。
- 增加 Live Shadow，明确断言不写订单、成交或余额。
- 增加 Paper/Shadow heartbeat 和 SQLite health snapshot。

## 自动化证据

- `tests/test_strategy_factory.py`
- `tests/test_storage_migrations.py`
- `tests/test_paper_runtime_controls.py`
- `tests/test_risk_approval.py`
- `tests/test_risk_manager.py`
- `tests/test_realtime_bar_gate.py`
- `tests/test_market_recorder.py`
- `tests/test_market_archive_replay.py`
- `tests/test_live_shadow.py`

完整门禁仍以 CI 中的 pytest、branch coverage、Ruff、mypy 和安全扫描为准。

本轮本地全量结果：

- pytest：160 passed。
- branch coverage：85.08%，门槛 85%。
- Ruff：passed。
- 扩展 mypy（20 个核心/新增运行模块）：passed。
- live guard 和 source safety 回归：3 passed。
- `git diff --check`：passed，仅有 Windows 行尾转换提示。

真实 OKX 公共行情适配已增加显式 `market_data.use_environment_proxy` 开关；默认关闭，
OKX recorder/Shadow 示例显式启用。CCXT 现可继承运行环境中的标准代理变量。

本轮真实冒烟已成功：

- recorder 通过 OKX 公共 `fetch_ohlcv` 获取 `BTC/USDT` 的 100 根 `1m` K 线。
- recorder 成功生成原始 JSON、闭合 K 线 CSV 和 SHA-256 manifest。
- Shadow 使用真实公开行情完成一次观察，写入 1 条 observation。
- Shadow 的订单、成交和余额记录均保持为 0。
- 真实 recorder 归档已通过 Replay CLI 聚合为完整 `15m` 数据。
- 对同一真实归档重复 Replay，两次 dataset SHA-256 和 replay SHA-256 完全一致。
- 三轮短时连续 recorder 冒烟完成：3 次尝试、0 次重试、3 个有效 manifest。
- 三批重叠归档共 297 行，Replay 严格合并为 99 根唯一 `1m` K 线并生成 6 根完整 `15m` K 线。
- 未使用 API Key、private API、账户信息或真实下单。

该结果只证明公开行情与 Shadow 数据链路可用，不证明任何 KYC 地区、账户权限或真实执行资格。
连续行情运行证据仍需长期 recorder 补录。

## Phase 1 剩余工作

- recorder 和 Shadow 已支持固定间隔连续迭代与失败后继续；recorder 已增加指数退避，外部进程监督待完成。
- 持续积累跨日真实 recorder 归档，并形成长期延迟、缺口和重试证据。
- 多 symbol Paper 账户恢复和组合级风险状态。
- 更多崩溃点注入，包括订单记录、成交记录和磁盘/数据库锁异常。
- heartbeat 超时判定、告警出口和运行手册。
- 金额与数量从 float 迁移到明确的 Decimal/交易所精度规则。
- 连续真实公共行情运行证据；不能只依赖 mock 和静态历史数据。

在以上项目完成并取得连续运行证据前，不签署 Phase 1 最终验收。
