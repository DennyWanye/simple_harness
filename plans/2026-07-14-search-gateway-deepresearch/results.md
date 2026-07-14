# Search Gateway 与 DeepResearch v2 执行结果

> 执行日期：2026-07-14～2026-07-15
> 当前判定：**SHIP（功能与测试 DoD 完成）**
> 质量说明：真实 DeepResearch 固定三类样本完成率为 66.7%；WebGPU 样本因 Search Gateway cooldown 诚实返回 no-results。该风险已量化，不影响本轮恢复、隔离、投递与可视化契约验收。

## 1. 已交付

- 内置、默认 ON 的 SearXNG-like Search Gateway；DeskPet 不要求 Docker、外部 SearXNG 或付费 API。
- 百度、DuckDuckGo、Google CDP、Bing CDP 默认 provider 与可选 SearXNG provider，统一异步 contract、去重、排序、缓存、deadline、cooldown 和降级诊断。
- `web_search` 与默认 `deep_research/v2` 共用 Gateway；FetchExtractService 统一 Scrapling、httpx、CDP、显式 Jina 和正文抽取。
- immutable v2 固定六分支、有界并发、确定性 join、两轮补证、统一证据评分、语义/域名多样性重排与 claim support。
- 13 阶段 durable completed 事件与 checkpoint/outbox 原子提交；默认折叠 child bubble，总体卡始终可见，历史/重启/乱序/重复/双 run 均可恢复。
- durable Markdown Artifact 实时广播并按 event id 去重；隐藏工具轨迹时成功产物卡仍可见。
- `final_assistant` 使用独立业务 channel，不再派生无效 receipt；旧 assistant receipt 可兼容收敛且不生成 completion evidence。
- 中断 tool history 的 prompt 安全投影与显式 web lookup 意图优先级修复。

## 2. 自动化门禁

| 门禁 | 结果 | 证据 |
|---|---:|---|
| P1 final-assistant 聚焦 | PASS | DeepResearch v2、product delivery、checkpointer 共 `43 passed` |
| Search/Fetch/DeepResearch/Workflow 聚焦与相邻 | PASS | 新增与相邻范围 0 failure；恢复、并发、质量、投递和 UI reducer 边界均覆盖 |
| Frontend Vitest 全量 | PASS | `77 files / 800 tests` |
| TypeScript | PASS | `tsc --noEmit` 与 `tsc -b` |
| Vite production build | PASS | build 成功；仅保留既有 chunk/dynamic-import warning |
| Backend pytest 全量 | PASS（功能范围） | `4440 passed, 10 failed, 14 skipped, 9 deselected`，704.60s；相对旧基线 12 failures 无新增，2 个 config cache 失败已转绿 |

最终 10 个失败是旧基线集合的严格子集：agent parallel 严格计时、hybrid router 本机健康状态、image config fallback、2 个 model catalog、Ollama live roundtrip、git-repo verifier 环境判断，以及 3 个旧 MemoryComponent stub 契约。Search Gateway、FetchExtract、DeepResearch v2、workflow progress/final-assistant 与本轮前端范围均为 0 failure。全量 JUnit 仅作为本机生成证据保留，不纳入提交。

## 3. 真实网络与固定基准

### Search Gateway smoke / benchmark

- 中文政策与英文官方文档 quick search 均有真实结果；首选失败后能降级，诊断记录 engines tried/hit、错误与耗时。
- MDN WebGPU 正文由 `scrapling + trafilatura` 提取，canonical URL、hash 与 quality 字段完整。
- 12 秒 Search benchmark 成功率 100%、Recall@5 0.667、P95 5186ms；5 秒连续查询样本成功率 70%、空结果 30%、Recall@5 0.45、P95 4342ms。

### 固定三类 DeepResearch

完整机器可读结果见 `deepresearch-live-benchmark.json`。

| 类别 | run | 终态 | 耗时 | 独立域 | 引用 | support rate |
|---|---|---:|---:|---:|---:|---:|
| 官方技术文档 | `12f4fca62dac42ed8bb199fe8de8e841` | completed | 270.548s | 8 | 12 | 0.109 |
| 中国现行政策 | `e3f1a6a4d64b4b6680c95e76f1288c1a` | completed | 226.795s | 12 | 12 | 0.750 |
| Web 标准动态信息 | `d087a50e20bd4e01864a7194b89d5b58` | failed/no_results | 15.250s | 0 | 0 | 0.000 |

- 完成率 2/3；nearest-rank P95 270.548s；全部样本平均 support rate 0.286，成功样本平均 0.429。
- 政策类抓取国务院、网信办、司法部等真实来源，生成 13,280-byte Markdown Artifact。
- WebGPU 样本受持久化 cooldown 影响，失败仍完成 13 阶段、错误摘要和 Markdown 交付，未调用模型常识伪造结论。
- `synth_stage_claim_count` 与最终 Coverage claim 数统计时点不同；结果 JSON 分字段记录，报告不混写。

## 4. Windows 真机 W01～W05

完整步骤、坐标声明、run id 与日志信号见 `plans/manual-results-2026-07-14-search-gateway-deepresearch/RESULTS.md`。

| TC | 判定 | 关键证据 |
|---|---:|---|
| W01 快速搜索 | PASS | 真输入 Python 最新版本；日志分类为 `web_search` 并真实调用 Gateway；UI 返回三个 python.org 官方来源 |
| W02 逐步可视化 | PASS | 总体卡始终可见；13 个阶段默认折叠；Space/Enter 真键盘展开/收起 |
| W03 运行中重启 | PASS | cite 11/13 时重启；同一 run lease_epoch 1→3 后恢复并完成 13/13，无重复 run |
| W04 最终交付 | PASS | 报告、Coverage、引用、错误摘要与 Markdown 卡可见；打开/文件夹定位通过；新 Artifact 不刷新实时出现 |
| W05 双 run 隔离 | PASS | completed 与 no-results 两 run 独立分组，进度、诊断、Artifact 和终态互不覆盖 |

补充 live 证据：新 `final_assistant` 事件只产生 `session_message`/`websocket` delivery；历史错误 receipt 已收敛；补测结束时非 delivered outbox 为 0。

## 5. AC 矩阵

31 条 AC：**31 COMPLETE / 0 PARTIAL / 0 MISSING**。

- SG-01～SG-10：PASS；默认 ON、无外部服务硬依赖、provider/fallback/rank/cache/cooldown/cancel/diagnostics/SearXNG 可选路径齐全。
- FE-01～FE-03：PASS；生产入口共用 FetchExtractService，正文契约、去重与 2 MiB 门完整。
- DR-01～DR-06：PASS；Gateway 接线、固定 fan-out、确定性、预算/失败、证据质量和 no-results/Artifact 均闭环。
- UI-01～UI-07：PASS；总体/child、默认折叠、allowlist、终态、去重乱序、历史重启、ARIA/键盘和多 run 真机与自动化闭环。
- OBS-01～OBS-02：PASS；固定 metrics schema、Search benchmark 与真实三类别 DeepResearch completion/P95/support/domain 指标均落盘。
- TEST-01～TEST-03：PASS；聚焦/全量/真实网络/Windows Computer Use 分层证据完整。

## 6. 已知后续优化

1. 优先调优 research 突发流量的 cooldown：区分 provider 级退避与整条 Gateway 零候选，避免一个高压 run 让紧随其后的不同主题立即 no-results。
2. 提高引用支持率，尤其官方技术文档样本；当前产品已诚实披露 unsupported claims，但质量仍有提升空间。
3. 辅助 FactExtractor 仍会记录旧 relay key 的 401 warning；它未阻断本轮主聊天、Gateway 或 DeepResearch，但应单独收口 provider refresh。
4. 固定集仅 3 个真实样本，适合验收和回归信号，不应解释为统计充分的线上 SLA。
