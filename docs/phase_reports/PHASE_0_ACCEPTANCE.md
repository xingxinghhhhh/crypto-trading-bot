# Phase 0 验收记录

## 结论

Phase 0 本地工程门禁于 2026-07-21 验收通过。项目保持离线研究和 paper-only 状态，未增加真实下单、private API 或密钥读取能力。

GitHub Actions 工作流已建立，但远端 CI 仍需在分支推送后取得首次运行记录；在此之前，以相同命令完成的本地门禁作为当前验收证据。

## 交付物

- `PROJECT_PLAN.md`：项目执行基线、阶段门禁和实盘准入条件。
- `.github/workflows/ci.yml`：Python 3.11/3.12、Ruff、mypy、安全测试和覆盖率门禁。
- `pyproject.toml`：Python/依赖兼容范围、开发工具和 85% 分支覆盖率要求。
- `src/crypto_bot/storage/migrations.py`：SQLite schema version 和迁移入口。
- `docs/REQUIREMENTS_TRACEABILITY.md`：需求、实现和测试追踪。
- `docs/REFERENCE_SYSTEMS.md`：外部系统案例及可借鉴边界。
- `docs/REPOSITORY_AUDIT_2026-07-21.md`：代码、数据和报告资产审计与清理记录。
- `reports/dedup_manifest_20260721.json`：报告精确去重清单。

## 自动化证据

| 门禁 | 结果 |
|---|---|
| Python | 3.11.9 |
| pytest | 117 passed |
| branch coverage | 85.29%，门槛 85% |
| Ruff | passed |
| mypy safety core | passed |
| Live guard / source safety / paper approval | 4 passed |
| `git diff --check` | passed，仅有 Windows 行尾提示 |
| SQLite integrity | ok |
| SQLite schema version | 1 |

## 运行验证

- `python -m crypto_bot.cli backtest --config config.example.yaml`：退出码 0，输出中文回测指标。
- `python -m crypto_bot.cli paper --config config.example.yaml --once`：退出码 0；已处理 K 线被识别为 `duplicate_bar`，没有重复模拟成交。

## 清理结果

- 删除工具缓存、coverage 输出、egg-info、`tmp` 和未引用代码。
- 合并重复配置文件并更新引用。
- 删除 318 个 SHA-256 完全一致的旧报告副本，回收约 104.90 MB。
- 保留六份主历史数据、原始下载、唯一研究报告、最终冻结批次、SQLite 审计数据库和 `.venv`。

## 遗留与下一门禁

- 远端 GitHub Actions 首次运行证据尚未产生，推送分支后补录。
- `PaperExecutionEngine` 的 `risk_checked` 仍是调用方可构造的布尔值，Phase 1 必须升级为不可伪造的 `RiskApproval`。
- 当前策略全部保持 `not_ready`；Phase 1 只建设长期 paper 基础设施和真实公开行情 recorder，不进入 Demo 或 Live。
