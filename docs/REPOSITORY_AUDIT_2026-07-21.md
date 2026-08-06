# 项目代码与数据资产审计

## 1. 审计信息

| 项目 | 内容 |
|---|---|
| 审计日期 | 2026-07-21 |
| 审计方式 | 盘点、引用搜索、SHA-256 重复检查、数据完整性检查、SQLite integrity check、完整回归 |
| 删除操作 | 已按用户批准执行；详见清理结果与 manifest |
| 当前 Git 基线 | `research-freeze-20260520` / `cadeb2f` |

### 清理执行结果

用户批准后已完成 Batch A、Batch B 和报告精确去重部分：

- 删除可再生成的缓存、coverage 输出、egg-info 和 `tmp`。
- 删除未引用 `Bar` 模型和 `run_csv_paper_session` 包装函数。
- 修复 ruff 发现的未使用变量和 import。
- 合并重复的 BTC 1h 配置，统一保留 `config.long.btc.1h.example.yaml`。
- 按 SHA-256 删除 318 个旧报告副本，回收约 104.90 MB。
- 清理 manifest：`reports/dedup_manifest_20260721.json`，状态为 `completed`。
- 主历史数据、原始下载、唯一报告、最终冻结批次和 SQLite 数据库均未删除。

## 2. 总体结论

项目不适合执行“全部清空后重新开发”。当前历史行情、研究报告和 SQLite 记录构成研究与运行证据，应保留或带清单归档。

可以清理的内容主要是工具缓存、coverage 临时文件、`tmp` 样例和报告目录中的完全重复副本。代码层只有少量明确候选，删除前仍应完成引用更新和回归测试。

## 3. 目录资产

| 目录 | 文件数 | 大小 | 分类 | 处理建议 |
|---|---:|---:|---|---|
| `.venv` | 约 12,101 | 311.05 MB | 本地运行环境 | 保留；损坏时才重建 |
| `reports` | 782 | 111.89 MB | 研究证据与可生成输出混合 | 含去重 manifest；最终批次及唯一历史证据已保留 |
| `data` | 16 | 15.94 MB | 主历史数据、历史版本、SQLite | 主数据与数据库保留，旧版本归档 |
| `downloads` | 322 | 10.18 MB | 原始下载来源 | 保留为数据溯源，后续压缩归档 |
| `src` | 约 55 个 Python 文件 | 0.74 MB | 产品代码 | 逐模块审计，不批量删除 |
| `tests` | 约 30 个 Python 文件 | 0.59 MB | 自动化证据 | 保留；增加缺口测试 |
| `.mypy_cache` | 4 | 2.38 MB | 可再生成缓存 | 可清理 |
| `.pytest_cache` | 5 | 约 0.01 MB | 可再生成缓存 | 可清理 |
| `.ruff_cache` | 4 | 约 0.01 MB | 可再生成缓存 | 可清理 |
| `tmp` | 3 | 994 bytes | 旧规范化样例 | 无引用，可清理 |

## 4. 必须保留

### 4.1 产品代码与测试

- `src/crypto_bot`：当前回测、paper、研究和安全边界实现。
- `tests`：当前 117 项测试，其中包括数据库迁移和 daily summary 持久化测试。
- `pyproject.toml`、配置模板、README、PROJECT_PLAN 和研究台账。
- `.github/workflows/ci.yml`：Phase 0 新增 CI，尚待质量门槛校准完成。

### 4.2 主历史数据

以下六份数据全部通过基本完整性检查：无重复 timestamp、无缺失行。

| 文件 | 行数 | 开始时间 | 结束时间 |
|---|---:|---|---|
| `BTC_USDT_1h.csv` | 20,784 | 2024-01-01 00:00 UTC | 2026-05-15 23:00 UTC |
| `BTC_USDT_4h.csv` | 13,574 | 2020-02-19 16:00 UTC | 2026-04-30 20:00 UTC |
| `ETH_USDT_1h.csv` | 20,424 | 2024-01-01 00:00 UTC | 2026-04-30 23:00 UTC |
| `ETH_USDT_4h.csv` | 13,574 | 2020-02-19 16:00 UTC | 2026-04-30 20:00 UTC |
| `SOL_USDT_1h.csv` | 20,424 | 2024-01-01 00:00 UTC | 2026-04-30 23:00 UTC |
| `SOL_USDT_4h.csv` | 12,533 | 2020-08-11 04:00 UTC | 2026-04-30 20:00 UTC |

这些文件是当前 benchmark 和研究冻结结论的输入，不能删除。

### 4.3 SQLite 审计数据库

`data/trading.db` 的 `PRAGMA integrity_check` 结果为 `ok`。

| 表 | 行数 |
|---|---:|
| signals | 61 |
| risk_events | 60 |
| orders | 10 |
| fills | 10 |
| balance_snapshots | 61 |
| paper_state_snapshots | 4 |
| processed_bars | 1 |
| daily_summaries | 2 |

该数据库包含 paper 审计证据。应先备份和生成 manifest，不应当作缓存删除。

### 4.4 最终研究冻结证据

`20260520T085132Z` 最终研究批次包含 375 个文件、约 82.59 MB。核心入口包括：

- `research_freeze_report_20260520T085132Z.md/.json`
- `strategy_benchmark_20260520T085132Z.csv/.json`
- `strategy_benchmark_matrix_20260520T085132Z.csv/.json`
- `strategy_benchmark_dashboard_20260520T085132Z.html`
- `benchmark_decision_report_20260520T085132Z.json`
- `watchlist_diagnosis_20260520T085132Z.json`
- 同批次各策略/数据集的 backtest、optimization、walk-forward、readiness 和 diagnosis 文件。

在建立完整引用 manifest 前，不拆散该批次。

## 5. 可直接清理候选

以下内容没有业务事实，均可由工具重新生成，现已清理。

| 候选 | 原因 | 风险 |
|---|---|---|
| `.mypy_cache` | mypy 缓存 | 无，下一次检查重建 |
| `.pytest_cache` | pytest 缓存 | 无，下一次测试重建 |
| `.ruff_cache` | ruff 缓存 | 无，下一次检查重建 |
| 所有 `__pycache__` | Python 字节码缓存 | 无 |
| `src/crypto_trading_bot.egg-info` | editable install 生成物 | 重新安装会重建 |
| `.coverage` | 本次 coverage 数据文件 | 无，应加入 `.gitignore` |
| `coverage.xml` | 本次 CI 风格覆盖率输出 | 无，应加入 `.gitignore` |
| `tmp` 下 3 个旧样例 | 无代码、配置、README 引用 | 低；删除前可记录文件名 |

预计主要节省约 2.5 MB；价值主要是减少噪音，不是释放大量磁盘。

明确不在本组：`.venv`。它虽可重建，但当前环境正常，删除只会增加恢复成本。

## 6. 精确重复报告候选

对 `reports` 中 1,099 个文件执行 SHA-256 检查：

- 完全重复内容分组：164 组。
- 涉及重复文件：483 个。
- 保留每组一个副本后理论可回收：约 104.90 MB。

示例：同一 BTC 1h MA equity curve 在多个 benchmark 时间戳下内容完全一致，最多出现 7 份。最终 `20260520T085132Z` 批次中已有对应副本。

建议处理规则：

1. 优先保留最终 `20260520T085132Z` 文件。
2. 对每个 SHA-256 只清理旧批次中的重复副本。
3. 生成 `reports/archive_manifest.json`，记录被清理路径、保留路径、hash、大小和原始时间。
4. 不根据文件名相似判断重复，只按内容 hash。
5. 不删除旧批次中内容唯一的报告；先压缩归档。

## 7. 建议归档而非直接删除

### 7.1 旧研究报告

- 2026-05-16：91 个，17.63 MB。
- 2026-05-17：17 个，0.03 MB。
- 2026-05-19：231 个，54.34 MB。
- 2026-05-20：758 个，144.64 MB，包含最终冻结批次。
- 2026-05-21：2 个最终冻结入口文件。

旧报告中可能存在阶段性结论和调试证据。建议先按研究批次压缩到 `reports/archive/`，保留 manifest，再考虑删除非最终批次。

### 7.2 原始下载

`downloads` 约 10.18 MB，包含 Binance Data Vision 月度/日度压缩包和合并 CSV。它们是主数据来源证据，虽然不参与运行，但应保留来源 URL、hash、时间范围和标准化结果后再压缩归档。

### 7.3 历史数据版本

`data/archive` 保存 BTC/ETH/SOL 4h 数据的中间版本。主数据没有内容重复，但旧版本可在生成 manifest 后压缩归档。不能仅按“文件更旧”直接删除，因为它们可能用于解释数据更新前后的回测差异。

## 8. 配置候选

审计时 `config.long.btc.1h.yaml` 与 `config.long.btc.1h.example.yaml` 的 SHA-256 完全相同。

当前引用：

- benchmark 使用 `config.long.btc.1h.yaml`。
- README 示例主要使用 `.example.yaml`，filter comparison 仍引用非 example 文件。
- 测试 fixture 中也出现 `config.long.btc.1h.yaml`。

现已统一保留 `.example.yaml`，benchmark、README 和测试引用均已更新，重复文件已删除。

## 9. 源码候选

### 9.1 明确未引用

- `src/crypto_bot/market/models.py` 中的 `Bar`：没有任何源码或测试导入。
- `run_csv_paper_session`：只有定义，没有调用；它只是 `run_paper_session` 的一层包装。

上述两个未引用项现已删除，并已通过 Phase 0 完整测试和 CLI 冒烟确认。

### 9.2 静态检查发现

- `benchmark_decision.py`：局部变量 `strong_rows` 未使用。
- `optimization/engine.py`：`Path` import 未使用。
- `test_paper_runner_ccxt_public.py`：`datetime`、`timezone` import 未使用。

这些低风险代码清理项已通过 `apply_patch` 移除，并由 ruff 验证通过。

### 9.3 暂不删除

- `assert_live_trading_allowed` 当前主要由安全测试使用，属于多重实盘保护设计，保留。
- `redact_sensitive` 当前没有外部调用，但属于配置安全工具；先增加测试或在后续配置日志中接入，不立即删除。
- 所有 `__init__.py` 保留，维持包结构和导入稳定性。

## 10. 质量基线发现

新增工具首次运行曾发现：

- pytest：116 passed。
- 全项目 branch coverage：73.90%，低于计划目标 85%。
- ruff：4 个未使用项。
- mypy safety core：5 个问题，包括 PyYAML stub 和 Optional cash 类型收窄。

没有通过降低门槛规避问题。修复类型问题、安装 PyYAML 类型声明、启用 subprocess coverage，并补充 daily summary 持久化测试后，最终结果为：

- pytest：117 passed。
- 全项目 branch coverage：85.29%，达到 85% 门槛。
- ruff：通过。
- mypy safety core：通过。
- backtest CLI：通过，输出中文回测指标。
- paper `--once`：通过，重复 K 线被安全跳过。
- Live guard、源码禁用 API 扫描和 Paper 风控门禁测试：4 passed。
- SQLite：`integrity_check=ok`，schema version 为 1。

## 11. 推荐清理批次

### Batch A：低风险生成物

- 清理工具缓存、`__pycache__`、egg-info、`.coverage`、`coverage.xml` 和 `tmp`。
- 更新 `.gitignore`。
- 不动 `.venv`、数据、报告和数据库。

### Batch B：代码与配置去重

- 修复 ruff 未使用项。
- 删除未引用 `Bar` 和 `run_csv_paper_session`。
- 合并重复 BTC 1h 配置并更新引用。
- 运行完整测试、mypy、ruff、CLI 冒烟和安全扫描。

### Batch C：报告去重与归档

- 生成 SHA-256 manifest。
- 保留最终研究冻结批次。
- 清理旧批次的精确重复副本，预计回收约 104.90 MB。
- 将剩余旧批次压缩归档，不立即删除唯一报告。

### Batch D：数据来源归档

- 为 downloads 和 data/archive 生成来源/范围/hash manifest。
- 压缩归档原始文件。
- 主六份数据和 trading.db 继续保留。

## 12. 审计决策

Batch A、Batch B 和 Batch C 的精确重复部分已完成。Batch D 未执行，继续等待实时 data recorder 的数据治理设计；旧报告中内容唯一的文件也仍然保留。
