# 自动交易系统参考案例研究台账

## 1. 目的

本文档用于持续研究公开自动交易系统、官方交易所接口和工程事故案例，为本项目提供设计证据和风险清单。

参考案例不是“复制代码”的来源。每项结论都需要结合本项目的低频现货、Python、paper-first、最小权限和长期可审计目标重新评估，并通过本项目测试后才能采用。

## 2. 研究方法

### 2.1 来源优先级

1. 交易所官方 API 文档、状态页、变更公告。
2. 项目官方 GitHub 仓库、文档、release 和 issue。
3. 有明确维护者、测试、许可证和发布记录的开源项目。
4. 公开事故复盘和学术研究。
5. GitHub 社区 issue、discussion 和 pull request。
6. X/Twitter、论坛和个人文章仅作为线索，关键结论必须由官方资料、源码或可复现实验交叉验证。

### 2.2 每个案例必须记录

- 项目名称、URL、版本或 commit、访问日期。
- 项目定位和适用交易类型。
- 数据、策略、风控、订单、持久化和运行架构。
- 值得采用的设计。
- 不适合本项目的设计。
- 已知问题、事故模式和安全风险。
- 许可证及代码复用限制。
- 对应本项目需求编号和验证实验。

### 2.3 采用流程

```text
发现案例
-> 阅读官方文档
-> 定位关键源码和测试
-> 查看相关 issue/release
-> 形成设计假设
-> 编写本项目实验或测试
-> 评审安全边界
-> 决定采用、修改采用或拒绝
```

## 3. 第一批重点案例

### 3.1 CCXT

| 项目 | 内容 |
|---|---|
| 官方地址 | https://github.com/ccxt/ccxt |
| 官方手册 | https://github.com/ccxt/ccxt/wiki/manual |
| 定位 | 多交易所统一接口库，不是完整交易系统 |
| 当前用途 | 公开 OHLCV 数据适配层 |

值得学习：

- 统一 exchange adapter，隔离交易所差异。
- 通过 `has`、timeframes 和 market metadata 检查能力。
- 明确区分网络错误、交易所错误、权限错误和订单错误。
- OHLCV 分页、限频和 UTC 毫秒时间语义。
- 官方明确提醒最后一根当前 K 线在收盘前可能不完整。
- 官方建议实时使用者持续抓取并自行归档历史数据。

本项目采用：

- 保留 ccxt 作为公开行情适配层。
- 增加 closed-bar gate，不将最后一根当前 K 线直接用于交易。
- 增加能力检查、限频、退避、数据缺口和交易所时间偏差测试。
- 将原始响应与标准化数据分开归档。

不直接采用：

- 不因为 CCXT 提供私有交易统一接口就提前开放实盘。
- 不假设所有交易所的统一字段完全一致。
- 不盲目重试订单类错误。

关联需求：`FR-DATA-002` 至 `FR-DATA-014`、`FR-EXEC-002` 至 `FR-EXEC-004`。

### 3.2 Freqtrade

| 项目 | 内容 |
|---|---|
| 官方地址 | https://github.com/freqtrade/freqtrade |
| 配置文档 | https://github.com/freqtrade/freqtrade/blob/develop/docs/configuration.md |
| 运行流程 | https://github.com/freqtrade/freqtrade/blob/develop/docs/bot-basics.md |
| 定位 | Python 加密货币策略、回测和 dry-run/live 系统 |

值得学习：

- dry-run 默认使用模拟钱包和持久化数据库。
- `process_only_new_candles` 防止同一根 K 线反复计算。
- 启动循环先恢复开放交易，再获取数据和更新开放订单。
- dry-run 和 production 使用不同数据库，避免账本混淆。
- 提供未成交订单超时、进程心跳和停止时订单处理配置。
- 官方明确建议先 dry-run，不把 sandbox 市场表现当成真实策略证据。

本项目采用：

- paper/demo/live 使用独立数据库和 run mode。
- 恢复顺序固定为状态恢复、对账、风控检查、再处理行情。
- 只处理闭合的新 K 线。
- 将真实公开行情驱动的 Live Paper 与交易所 Demo 分开评价。

不直接采用：

- 不复制其生产密钥配置方式；本项目未来使用 Secret Manager/环境注入。
- 不采用本项目范围外的杠杆、合约和复杂交易模式。
- 不把框架自带策略或优化结果视为有效因子证据。

关联需求：`FR-RUN-001` 至 `FR-RUN-008`、`FR-PORT-005`、`FR-DATA-007`。

### 3.3 NautilusTrader

| 项目 | 内容 |
|---|---|
| 官方地址 | https://github.com/nautechsystems/nautilus_trader |
| 定位 | 确定性、事件驱动、多市场研究和实时执行引擎 |
| 技术特点 | Rust 核心，Python 控制面，模块化 adapter |
| 许可证 | LGPL-3.0，采用代码前必须单独评估 |

值得学习：

- 研究、模拟和实时系统使用统一事件语义和时间模型。
- 数据、执行、策略和 venue adapter 模块化。
- 明确的环境类型，例如 LIVE、TESTNET、DEMO，而不是模糊布尔值。
- 订单、成交、持仓、账户和 cache 采用领域事件建模。
- matching engine 能模拟部分成交、流动性消耗和订单接受事件。
- release 中持续修复重连、时间、订单类型和 adapter 边界问题，说明这些是长期工程重点。

本项目采用：

- 逐步转向事件驱动的 OrderEvent/FillEvent/AccountEvent。
- 回测、paper、demo 使用相同策略和风控语义。
- 用显式枚举描述 execution environment。
- 为行情和执行 adapter 建立统一契约测试。

不直接采用：

- 当前低频 MVP 不引入 Rust、高频订单簿或多 venue 复杂度。
- 不迁移整个引擎；只提取适合现阶段的事件模型和测试思想。
- 不引入复杂订单类型、期权、衍生品和多账户交易。

关联需求：`FR-EXEC-001` 至 `FR-EXEC-008`、`NFR-003`、`NFR-006`。

### 3.4 QuantConnect LEAN

| 项目 | 内容 |
|---|---|
| 官方地址 | https://github.com/QuantConnect/Lean |
| 定位 | 模块化、事件驱动、多资产研究、回测和实时交易引擎 |
| 技术特点 | C# 核心，支持 Python 策略 |
| 许可证 | Apache-2.0 |

值得学习：

- 数据源、算法、交易处理、实时事件和结果处理可插拔。
- 同一算法模型可运行在回测和实时数据流中。
- Brokerage adapter 与算法逻辑隔离。
- 独立的 transaction handler 和 result handler 降低职责混杂。
- 配置和运行任务由引擎统一编排。

本项目采用：

- 保持 Strategy、RiskManager、OrderManager、ExecutionAdapter、ResultReporter 分离。
- Live Shadow 与历史回放共用策略代码，但数据时钟不同。
- 将交易所特定规则封装在 adapter/instrument metadata，不进入策略。

不直接采用：

- 不引入 LEAN 的多资产全功能复杂度。
- 不依赖云端平台作为本项目账本或风险控制的唯一来源。
- 不在当前阶段支持股票、期权、外汇等资产。

关联需求：`FR-STR-001`、`FR-EXEC-005`、`NFR-009`。

### 3.5 Hummingbot

| 项目 | 内容 |
|---|---|
| 官方地址 | https://github.com/hummingbot/hummingbot |
| 定位 | 模块化加密交易连接器和策略执行平台 |
| 主要价值 | connector、订单跟踪、实时行情和组件扩展边界 |

值得学习：

- 交易所 connector 与上层策略分离。
- 订单、成交和连接器状态需要持续跟踪，而不是一次请求结束。
- 市场数据、账户、订单历史和 performance analysis 分开提供。
- connector 是独立维护和测试的长期模块。

本项目采用：

- Demo/未来 Live adapter 单独实现并拥有独立契约测试。
- OrderTracker 作为长期状态组件，不把订单状态留在 CLI 调用栈中。
- 市场数据 recorder 与执行 connector 解耦。

不直接采用：

- Hummingbot 偏向做市、套利和高频场景，本项目不采用这些策略。
- 不引入 DEX、跨链、多连接器并发和复杂网络资产。

关联需求：`FR-EXEC-001` 至 `FR-EXEC-005`、`FR-DATA-009`。

### 3.6 VeighNa/vn.py

| 项目 | 内容 |
|---|---|
| 官方地址 | https://github.com/vnpy/vnpy |
| 官方英文说明 | https://github.com/vnpy/vnpy/blob/master/README_ENG.md |
| 定位 | Python 事件驱动量化交易平台 |

值得学习：

- 独立事件引擎作为行情、订单、成交和状态变化的路由中心。
- `data_recorder` 持续记录 Tick/K 线，用于回测和策略初始化。
- `risk_manager` 作为独立前置模块，覆盖流量、下单数、活动订单和撤单数。
- 数据库、RPC、WebSocket 和策略应用按模块拆分。

本项目采用：

- 将实时 recorder 作为正式子系统，而不是 paper runner 的副作用。
- 增加下单频率、活动订单数和异常撤单等运行级风险指标。
- 评估轻量事件总线，但不在需求不足时过度抽象。

不直接采用：

- 不复制桌面 GUI、国内期货接口和多进程复杂部署。
- 不在当前低频单实例阶段引入 RPC 服务。

关联需求：`FR-DATA-009` 至 `FR-DATA-012`、`FR-RISK-007`、`FR-RUN-004`。

## 4. 跨案例共同结论

第一批案例呈现出以下共同原则：

1. 回测、paper 和实时执行应尽量共享策略、风控和事件语义。
2. 数据适配器与执行适配器必须分离。
3. 当前 K 线、重复数据、时钟和市场状态是实时系统的基础风险。
4. 虚拟账户、开放交易和订单状态必须持久化。
5. 订单是长期状态机，不是一次 HTTP 调用。
6. Demo/dry-run 数据库不能与 live 数据库混用。
7. 连接器需要独立契约测试、重连、限频和故障分类。
8. 前置风控需要独立于策略，并覆盖交易频率和活动订单。
9. 实时 data recorder 是连接研究与运行的核心基础设施。
10. 模拟环境适合验证执行链路，但不必然反映真实市场流动性。

## 5. 对当前项目的直接行动

| 优先级 | 行动 | 来源启发 | 项目阶段 |
|---|---|---|---|
| P0 | closed-bar gate | CCXT/Freqtrade | Phase 1 |
| P0 | 实时公开行情 recorder | CCXT/vn.py | Phase 1 |
| P0 | paper 账户与风险状态恢复 | Freqtrade | Phase 1 |
| P0 | 统一策略工厂和 readiness gate | LEAN/NautilusTrader | Phase 1/2 |
| P0 | 不可伪造 RiskApproval | vn.py/NautilusTrader | Phase 1 |
| P1 | 事件化订单状态机 | NautilusTrader/Hummingbot | Phase 3 |
| P1 | client_order_id 和 UNKNOWN 熔断 | CCXT/成熟执行系统共识 | Phase 3 |
| P1 | Demo 与 Live Paper 分开评价 | Freqtrade/CCXT | Phase 2/3 |
| P1 | adapter 契约测试 | Hummingbot/NautilusTrader | Phase 3 |
| P2 | 轻量事件总线 | vn.py/LEAN/NautilusTrader | 需求验证后 |

## 6. 后续研究队列

- 深入阅读 Freqtrade 持久化、订单恢复和 dry-run 撮合源码及测试。
- 深入阅读 NautilusTrader order emulator、execution engine 和 reconciliation 设计。
- 深入阅读 Hummingbot connector/order tracker 的失败状态处理。
- 深入阅读 LEAN transaction handler、brokerage message 和 live result handling。
- 深入阅读 vn.py data recorder 和 risk manager 模块。
- 收集交易所超时、重复订单、部分成交和 WebSocket 断线的公开事故案例。
- 收集策略回测与实时结果偏离的可复现案例。
- 研究交易成本、延迟和 K 线闭合对低频策略的实际影响。
- 对 X/Twitter 上的案例只登记线索；找到官方状态页、源码 issue 或可复现实验后才形成结论。

## 7. 研究更新节奏

- 每个 Phase 开始前进行一次针对性案例调研。
- 遇到重大架构选择、异常订单或数据问题时立即补充研究。
- 每月检查关键依赖和参考项目 release/安全公告。
- 每个采用结论必须链接到需求、测试或 ADR。
- 已过时、无法验证或不适用的结论要明确标记，不静默删除。

## 8. 当前研究状态

| 日期 | 范围 | 结论 |
|---|---|---|
| 2026-07-21 | CCXT、Freqtrade、NautilusTrader、LEAN、Hummingbot、vn.py | 完成第一轮架构级梳理，后续进入源码和故障案例级研究 |

