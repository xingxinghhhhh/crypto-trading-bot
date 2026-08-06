# Phase 2 OKX 4h Convenience Universe Intake 与 Lineage 冻结 MVP（2026-08-02）

## 结论

已完成一个严格区分联网捕获与离线审计的 OKX 4h 扩展资产输入节点。系统从一次冻结的 OKX
当前 live SPOT 快照出发，按固定且不可人工覆盖的 eligibility/哈希排序规则，选择 BTC、ETH、SOL
之外的 3 个资产；随后保存每一页 OKX 九列原始响应，生成规范化 OHLCV CSV，并在完全离线的
audit 中从 raw bundle 重建和复核 CSV。

本节点只建立 `current_okx_live_convenience_snapshot`。它不重建 2022 年以来的历史成员资格，
不解决幸存者偏差，不自动写入 Registry，不构建 Panel、因子、组合或 PnL，也不构成策略批准、
稳定盈利或 readiness 升级证据。

## ChatGPT 规划与仓库修订

ChatGPT 在时间戳语义节点之后选择了“OKX 4h Convenience Universe Intake 与 Lineage 冻结
MVP”，优先于在原三资产上构造 PnL。仓库和官方 OKX 合约复核后冻结了以下修订：

- 现有 recorder/history 经 CCXT 归一化，不可冒充 OKX 九列 raw lineage；新模块独立使用
  public instruments 与 history-candles endpoint。
- `history-candles` 固定 `bar=4H`、`limit=300`；第一页 `after=end_open+4h`，后续页
  `after=上一页最旧 ts`，利用官方“严格早于 cursor”语义向历史推进。
- 对 call-auction/pre-open 品种，连续交易起点使用非空 `contTdSwTime`，否则使用 `listTime`。
- raw JSONL 每页保存精确 request params、实际 response body 的可逆 UTF-8 字符串与 body hash；
  离线 audit 不信任摘要，必须重新解析、分页校验并重建 CSV。
- 三个候选在下载前一次性冻结；任一失败不得补选第 4 个候选，且不得生成最终 marker。

ChatGPT 已明确接受并冻结上述修订。

## 冻结政策

Policy 文件为 `config.okx-universe-intake.example.yaml`：

- `target_count=3`
- `history_start=2022-01-01T00:00:00Z`
- `timeframe=4h` / OKX `bar=4H`
- `instType=SPOT`、`quoteCcy=USDT`、`state=live`、`ruleType=normal`、`instCategory=1`
- `effective_continuous_start <= history_start`
- 固定 stablecoin base denylist、锚定 leveraged-token patterns、BTC/ETH/SOL 排除
- 选择键：`SHA256("okx-convenience-universe-v1|" + instId)` 升序
- future snapshot 退出只停止未来 intake，不删除或回写已冻结数据

官方合约证据为 `docs/evidence/okx_public_instruments_history_contract_v1.json`，SHA-256：
`d489567506255463b84ee941708ffd0ad944045f8e1d2c84ce43fdf146146966`。

## 两个 CLI 与原子边界

联网 capture：

```text
capture-okx-universe-intake --registry <datasets.yaml> --policy <policy.yaml>
  --semantics-report <timestamp-semantics.json> --output-dir <dir>
```

它先在内存完成 snapshot、三份全历史、CSV 和全部质量验证，再按 snapshot raw、三份 history
bundle、三份 CSV、最终 capture JSON marker 的顺序提交。网络、地区、限流、contract、游标、
confirm、grid、gap、质量或碰撞失败均不生成最终 marker。

离线 audit：

```text
audit-okx-universe-intake --capture-report <frozen-capture.json> --output-dir <dir>
```

它禁止联网，从 frozen capture 的 sibling artifacts 重放全部 raw page，输出 eligibility CSV、
datasets CSV、独立 registry-candidates YAML，最后写 intake JSON marker。同名同字节幂等成功，
同名不同内容 fail closed。

## 真实 OKX 验收

Snapshot received_at：`2026-08-02T15:49:19.356645+00:00`；共 1335 个 instruments。
固定哈希规则选择：`KNC-USDT`、`SWFTC-USDT`、`BICO-USDT`。

历史闭区间为 `2022-01-01T00:00:00+00:00` 至 `2026-08-02T08:00:00+00:00`。

| asset | bars | pages | history bundle SHA-256 | CSV raw SHA-256 | CSV canonical SHA-256 |
|---|---:|---:|---|---|---|
| KNC-USDT | 10047 | 34 | `2427aa62a091dcd3d1d8f1f48c56dd51ac4cc83d2c55e32f59ed238a41d9862b` | `90adec15128c4502f18d241e8704c8ed51d024ae93891a43e85d9657889b9ee9` | `90adec15128c4502f18d241e8704c8ed51d024ae93891a43e85d9657889b9ee9` |
| SWFTC-USDT | 10047 | 34 | `7f7c7d639d37e9599f4a95f71ea3639080d6eeef078d34c406c21420c79cca68` | `418f6b4d75e65b4c66d0ac47593b31ce8bcae58fde8fc6ed80adf7c56bea1e4e` | `990d5abdf89d155cc0b34fa11d3c8b8371fb806aa47d77ccb243699643ecc89d` |
| BICO-USDT | 10047 | 34 | `bfc6380070e2bf4524494ae7993c4801c000ce113332adc643438692aa455d9b` | `8340d7e461ea13e55b181deeb96312368d07b0a48f0a92f8066f33b2d9a39195` | `8340d7e461ea13e55b181deeb96312368d07b0a48f0a92f8066f33b2d9a39195` |

所有数据均为 exact 4h grid、0 duplicate、0 gap、0 missing，且 `confirm=1`、
`verified_open_time`、`complete_direct_okx_public`。

- snapshot raw SHA-256：`89993a7e97c1c8928d1b86de2821ca39a6b7c557812a39eb5f75670b189043f7`
- capture SHA-256：`b96aa6011796c8e2f1e0d7826c0f95b9f5be15fbdf2cf38862493f3fc75da451`
- intake SHA-256：`d39c44ea177cc735330e0cea5071bc59527846402de397df6458f1ef65725c03`
- Registry 原文件 SHA-256 前后均为：
  `15f2ac5e77a812196394d9d7811ee2ce66a391d6b41d83187e050fdbb40ce509`

同一 capture 在两个输出目录离线运行，eligibility、datasets、registry candidates 和 JSON
四个产物的文件名、identity hash 和 bytes 全部一致。三个候选只存在于独立 YAML，现有 Registry
没有任何写入。

## 自动化门禁

- 专项测试：17 passed；包含 capture/audit 端到端、确定性、原子失败、哈希漂移、路径逃逸、
  eligibility、连续交易起点、分页/confirm/grid 与 CLI 离线消费。
- 全量：299 passed；总 branch coverage 85.16%（门槛 85%）。
- Ruff：passed；mypy safety/research core：29 source files passed；source/live safety：3 passed。
- `git diff --check`：无内容错误；仅保留 Windows 工作区既有 LF/CRLF 转换提示。
- 同时修复已有内容寻址模块在深层 Windows 路径下把长正式文件名再次拼入临时名导致的
  `MAX_PATH` 风险；原子临时名改为同目录短 UUID，正式文件名、内容和所有 identity hash 不变。

## 研究边界

这三份数据具有本次直接 OKX public 的完整 lineage 和已验证 open-time 语义，但资产名单来自
2026-08-02 的当前 live snapshot。完整 OHLCV 覆盖不能证明这些资产在所有历史时点都满足资格，
也不能消除已退市资产缺失造成的幸存者偏差。后续若构建扩展 Panel，必须显式绑定 capture/intake
身份并继续携带 convenience-sample 警告；在此之前不得把它称为无偏 universe 或盈利证据。
