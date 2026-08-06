# 需求与测试追踪表

## 1. 用途

本表将 `PROJECT_PLAN.md` 中的需求映射到实现、测试和阶段状态。状态只允许：`verified`、`partial`、`planned`、`blocked`。

## 2. 当前安全基线

| 需求 | 状态 | 实现 | 自动化验证 |
|---|---|---|---|
| 默认 paper / live_trading=false | verified | `crypto_bot.config` | `test_live_trading_guard.py` |
| LiveExecutionClient 无条件拒绝 | verified | `crypto_bot.execution.live_guard` | `test_live_trading_guard.py` |
| 源码禁止真实交易 API | verified | public provider / safety scan | `test_source_safety_scan.py` |
| 策略只生成 Signal | verified | `crypto_bot.strategy` | strategy tests / source safety scan |
| Paper 未经风控不得执行 | verified | `RiskApproval` + `PaperExecutionEngine` | `test_risk_approval.py`、`test_paper_engine.py` |
| 日志敏感字段脱敏 | verified | `crypto_bot.logging` | `test_logging_redaction.py` |

`risk_checked` 布尔值不构成执行授权；Paper 执行要求与订单摘要绑定、短期有效且只能消费一次的 `RiskApproval`。

## 3. Phase 0 追踪

| 工作项 | 状态 | 实现/配置 | 自动化验证 |
|---|---|---|---|
| Python 兼容范围 | verified | `pyproject.toml` | CI 3.11/3.12 matrix |
| 依赖兼容范围 | verified | `pyproject.toml` | editable install in CI |
| Ruff | verified | `pyproject.toml` | CI `ruff check` |
| 基础 mypy | verified | `pyproject.toml` | CI safety-core mypy |
| 分支覆盖率门槛 | verified | `pyproject.toml` | pytest-cov，最低 85% |
| GitHub Actions CI | partial | `.github/workflows/ci.yml` | 本地等价门禁通过；等待首次远端 push/pull_request 运行记录 |
| SQLite schema version | verified | `storage.migrations` | `test_storage_migrations.py` |
| 旧数据库无损采用 | verified | migration 1 | `test_storage_migrations.py` |
| 未来数据库版本拒绝 | verified | `initialize_schema` | `test_storage_migrations.py` |
| 项目计划书 | verified | `PROJECT_PLAN.md` | 文档评审 |
| 外部案例台账 | verified | `docs/REFERENCE_SYSTEMS.md` | 文档评审 |

## 4. Phase 1 关键需求

| 需求 | 状态 | 计划验证 |
|---|---|---|
| FR-DATA-005 closed-bar gate | verified | `test_realtime_bar_gate.py`：形成中 K 线、陈旧、缺口、时钟边界 |
| FR-DATA-009 实时数据 recorder | partial | `test_market_recorder.py`：原始/闭合数据归档、manifest、连续迭代和有界指数退避；长期运行证据及外部监督待完成 |
| FR-DATA-011 实时数据回放集 | verified | `test_market_archive_replay.py`：manifest 校验、重复/冲突/缺口处理、完整周期重采样、确定性 replay SHA-256 |
| FR-DATA-013 Live Shadow | verified | `test_live_shadow.py`：信号/风控留痕且订单、成交、余额始终为零 |
| FR-STR-006 统一策略工厂 | verified | `test_strategy_factory.py`：CLI、Paper、优化、benchmark 复用 |
| FR-RISK-003 每日最大亏损 | verified | `test_risk_manager.py` 和持久化日初权益查询 |
| FR-RISK-005 止损止盈 | verified | `test_risk_manager.py`：风险层保护性卖出和审批 |
| FR-RISK-007 每日交易次数 | verified | `test_paper_runtime_controls.py`：重启后限制不清零 |
| FR-RISK-011 RiskApproval | verified | `test_risk_approval.py`：伪造、篡改、过期、重复消费 |
| FR-PORT-005 状态恢复 | partial | 账户/持仓/已实现盈亏/峰值恢复和事务回滚已完成；完整事件重放待完成 |
| FR-RUN-005 kill switch | verified | `test_paper_runtime_controls.py`：跨连接持久化、阻断行情、人工恢复 |

## 5. Phase 2 关键需求

| 需求 | 状态 | 实现 | 自动化/真实数据验证 |
|---|---|---|---|
| FR-STR-011 因子注册与防前视 | verified | `crypto_bot.factors`：6 个价格/成交量因子、严格 OHLCV 契约、前瞻收益对齐 | `test_factor_research.py`：未来数据扰动不改变历史因子、收益标签严格位于因子时间之后 |
| FR-STR-012 因子预测与衰减诊断 | verified | `crypto_bot.factor_research`：Pearson IC、Rank IC、多周期衰减、分位收益和换手率 | 合成持久收益样本验证方向；真实 OKX 99 根 1m Replay 生成 18 组指标和独立衰减表 |
| FR-STR-013 相关性与成本压力 | verified | 因子相关矩阵、0/5/10 bps 净诊断收益 | `test_factor_research.py`：相关矩阵对角线和成本单调压力 |
| FR-STR-014 滚动样本外稳定性 | verified | 训练/测试边界剔除跨窗前瞻标签，输出窗口与稳定性摘要 | `test_factor_research.py`；真实 OKX Replay 生成滚动窗口证据 |
| FR-STR-015 因子研究可复现归档 | verified | `analyze-factors` CLI、确定性研究 SHA-256、JSON/CSV 原子导出 | 相同真实数据的单批与三批重叠归档产生相同 dataset/research SHA-256 |
| FR-STR-016 因子准入自动化 | planned | 当前固定 `readiness_changed=false`、`automatic_factor_approval=false` | 任何自动晋级在独立准入评审前禁止 |

## 6. 更新规则

- 每个开发工作项开始前增加或确认对应需求行。
- 状态改为 `verified` 前必须填写自动化测试或正式人工验收证据。
- 测试文件重命名或需求改变时同步更新本表。
- 任何 live、安全和风险需求不得因缺少测试而标记为 `verified`。
