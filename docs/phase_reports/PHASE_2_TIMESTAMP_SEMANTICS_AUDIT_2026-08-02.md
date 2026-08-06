# Phase 2 OHLCV 时间戳语义审计与证据回灌 MVP（2026-08-02）

## 结论

已建立独立、可审计的 OHLCV `bar_open_time / bar_close_time / unverified` 证据链。
本节点把网络捕获与确定性审计严格拆开：第一条命令只调用 OKX public market candles
端点并冻结原始九列响应；第二条命令禁止联网，只消费同一个内容寻址 probe，评估 producer、
六个长期 dataset 和两个 Panel。

真实结果确认 OKX public candles producer 的 `ts` 为开盘时间，但现有长期 CSV 的 lineage
仍为混合、部分或未知，因此四个 dataset 为 `partial_unverified`、两个为 `unknown`，两个
Panel 均保持 `unverified`。Producer probe 不得反向升级历史 dataset。

本节点没有修改任何 OHLCV 内容、raw/canonical hash、Panel SHA、Rank IC research SHA、
HAC/Holm calibration SHA 或 OOS SHA；没有访问账户、密钥、private API 或 trading API；
也没有改变 readiness。

## ChatGPT 规划、冲突发现与修订

ChatGPT 在 OOS 稳定性节点后选择“OHLCV 时间戳语义审计与证据回灌 MVP”，优先理由是
扩资产和组合 PnL 都依赖信号可见时点，而当前 Panel 只能证明 UTC 值对齐，不能证明
bar-open/bar-close 口径一致。

仓库复核发现现有 recorder 的 `raw_rows` 来自 CCXT `fetch_ohlcv` 统一六列结果，不是
OKX 原始 `code/msg/data` 响应，缺少第九列 `confirm`。此外，若审计命令每次抓取 latest
candles，同时把 response hash 纳入身份，则换输出目录复跑必然变化。ChatGPT 接受以下修订：

- `capture-okx-timestamp-probe` 负责一次性 public 捕获，输出原始 response 和最终 probe manifest。
- `audit-timestamp-semantics` 只消费冻结 probe，严禁隐式联网，因此可确定性复跑。
- 语义 assessment 是独立身份，不回填旧 Panel/research 报告，也不重算旧 hash。
- 新测试沿用仓库扁平 `tests/test_*.py` 结构。
- 现有六个 dataset 和两个 Panel 的状态在实现前冻结为保守预期，producer 成功不构成升级理由。

## 官方契约与 public probe

仓库新增 `docs/evidence/okx_candles_contract_v1.json`，记录 OKX 官方 public contract：

- endpoint：`GET https://www.okx.com/api/v5/market/candles`
- response：`[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]`
- `ts`：Unix 毫秒 candlestick opening time
- `confirm=0`：未完成；`confirm=1`：已完成

契约证据文件 SHA-256：
`485161ed89c0e2ad025e8950924e62c7fd6f9cddfcdf172c5d89c607e94ddadb`。

捕获命令固定 `limit=5`，只允许已知 intraday bar；在内存中验证 HTTP 成功、`code=0`、
空 `msg`、九个字符串字段、数值有限、时间戳唯一且严格新到旧、时间网格、`confirm` 枚举，
并要求至少一个 `confirm=1` 且 `ts + timeframe <= received_at + 5s` 的已闭合 bar。

首次 Python 探针因 `urllib` 默认请求头被 OKX edge 返回 403；增加明确的只读客户端
`User-Agent` 和 `Accept: application/json` 后成功。该调整不是代理、认证或地区规避。

真实捕获：

| 字段 | 值 |
|---|---|
| Instrument / bar | `BTC-USDT` / `1m` |
| received_at | `2026-08-02T15:21:17.230606+00:00` |
| response rows / closed rows | 5 / 4 |
| closed timestamp range | `2026-08-02T15:17:00+00:00`–`15:20:00+00:00` |
| probe SHA-256 | `7bc6512daeddf92f8555a6a9e03bc5d3eeca44d185e0f80005efda6fae2ec801` |
| raw response file SHA-256 | `e7ae80dd394528f17e19e2806dc59632ef1daa566e2a4fd207e36e3b7d75ef42` |
| probe JSON file SHA-256 | `5e3112a1c2a39ea9668910b9e780aa02c6c50e26e564b0c05d73790369720cf0` |

捕获原子顺序为：完成网络与内存校验，计算 response/contract/probe hash，原子提交
content-addressed response，最后写 probe JSON commit marker。网络、地区、契约、结构、
confirm、grid 或写入失败均不生成最终 manifest。

## 离线语义审计

`config.timestamp-semantics.example.yaml` 只通过 `dataset_id`、producer ID、Registry 已登记的
repo-relative evidence 路径和固定 SHA-256 关联证据；不得重声明 CSV 路径或数据 hash。

状态规则：

- `verified_open_time/verified_close_time`：完整 lineage 和完整时间范围均有一致的已验证语义。
- `partial_unverified`：lineage 或时间范围只有部分证据，或完整 lineage 的语义仍未验证。
- `unknown`：没有适用语义证据。
- open/close 证据冲突：整项 fail closed，不生成 commit marker。
- Panel 只有所有 constituent 都为同一 verified 语义时才可 verified；任一 partial/unknown
  使其保持 unverified，verified open/close 冲突则整项失败。

不得根据文件名、整点分布、时间间隔或 CCXT 惯例推断语义；时间网格只能做一致性检查，
不能升级状态。

真实评估：

| 对象 | 状态 | 原因 |
|---|---|---|
| `okx_public_candles_v1` | `verified_open_time` | 官方 contract 与冻结 raw probe 一致 |
| `binance_data_vision_unverified_v1` | `unknown` | 未冻结适用 provider contract |
| `repository_unattributed_v1` | `unknown` | upstream provider 未证明 |
| `btc_usdt_1h_v1` | `partial_unverified` | Binance archive + OKX append config 的混合 lineage 未端到端覆盖 |
| `eth_usdt_1h_v1` | `unknown` | 无适用 provider 语义证据 |
| `sol_usdt_1h_v1` | `unknown` | 无适用 provider 语义证据 |
| `btc_usdt_4h_v1` | `partial_unverified` | 只有中间 archive/repository audit |
| `eth_usdt_4h_v1` | `partial_unverified` | 只有中间 archive/repository audit |
| `sol_usdt_4h_v1` | `partial_unverified` | 只有中间 archive/repository audit |
| `btc_eth_sol_1h_v1` | `unverified` | constituent 未全部 verified |
| `btc_eth_sol_4h_v1` | `unverified` | constituent 未全部 verified |

## 确定性产物

离线审计输出：

- `timestamp-semantics.<sha>.producers.csv`
- `timestamp-semantics.<sha>.datasets.csv`
- `timestamp-semantics.<sha>.panels.csv`
- `timestamp-semantics.<sha>.json`（最后提交的 commit marker）

`semantics_sha256` 绑定 schema/policy、规范化 evidence config、所有 dataset raw/canonical
hash、Panel SHA 和成员关系、全部 dataset evidence 与 producer contract artifact hash、冻结
probe/report hash、固定聚合政策以及三个 CSV hash；不绑定输出目录、绝对路径、当前时间、
PID 或耗时。

真实 semantics SHA-256：
`52877c340a0531381e26eaa5e45ed4e84d84b420b6c5324948df529acae5f408`。

产物文件 SHA-256：

- producers CSV：`f1aac4e5bb28541734eec3fc496a28f3e1f9354f16c9ae5a27fcb2e844f63cf0`
- datasets CSV：`df46a393571d684d21a4730f2c612c47e9fb574ffba2fb96dc929a7fa2494bf9`
- panels CSV：`6babf68a5de0316cd651c66182effff4e8ea274dd3e058c2f7b2de18f0c677e4`
- JSON：`1e5eada97a1b08b193442c43ab8328bb26efe54fdfe7892a76b49bd98fff0e19`

复用同一冻结 probe 在两个独立输出目录运行后，四个产物逐字节一致。

## 自动化门禁

- 新增时间戳语义测试：28 passed；新模块 branch coverage 87%。
- 全量测试与 branch coverage：282 passed，85.63%（门槛 85%）。
- Ruff 全仓：passed。
- mypy safety/research core：28 source files passed。
- live guard / source safety：3 passed。
- `git diff --check`：无内容错误；仅有 Windows 工作区既有 LF/CRLF 转换提示。

## 安全与研究边界

Probe 只证明本次 OKX public producer 响应与官方 contract 一致，不证明旧长期 CSV 的来源，
也不解决三资产 universe 的 selection/survivorship 风险。旧研究工件继续明确
`timestamp_semantics=unverified`；未来消费者若需要时间语义保证，必须同时绑定原数据/研究
hash 与独立 `semantics_sha256`。本节点不构成因子有效、组合可交易或稳定盈利证据。
