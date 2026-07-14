# Search Gateway 与 DeepResearch v2 执行结果

> 执行日期：2026-07-14
> 当前判定：**PARTIAL / NO-SHIP（完整 DoD）**
> 说明：检索、抓取、DeepResearch v2、durable progress 和前端折叠投影的实现及聚焦自动化已完成；真实 DeepResearch 网络报告与 Windows 进度/重启/Artifact 闭环被当前 relay `401 INVALID_TOKEN` 阻塞，不能标成完成。

## 1. 已交付实现

- 默认开启进程内 SearXNG-like Search Gateway；默认不依赖 Docker、SearXNG 或付费 API。
- `web_search` 与 `deep_research/v2` 共用异步 Gateway contract；真正的 SearXNG 仅在配置 URL 后注册。
- Google/Bing Edge CDP、百度、DuckDuckGo、SearXNG provider 均可独立测试；request-local diagnostics 与共享 cache/cooldown 分离。
- FetchExtractService 收口 Scrapling、httpx、CDP、显式 Jina 和 Trafilatura/Selectolax；所有入口执行 2 MiB 内容门。
- 新 DeepResearch 默认启动 immutable `deep_research/v2`；v1 manifest/checkpoint fixture 保持不变。
- v2 使用固定六分支、有界并发、确定性 join、两轮 deep gap、统一证据评分、语义/多样性重排和 claim support 检查。
- 13 阶段 completed 事件与 checkpoint/outbox 原子提交；启动时恢复遗留 `delivering` claim，SessionDB 与 websocket 可分别重试。
- 消息流始终保留总体进度；completed stage 作为默认折叠的 child bubble，支持历史回放、乱序/重复事件、terminal 后迟到 child 和多 run 隔离；running elapsed 实时增长。

## 2. 自动化结果

| 门禁 | 结果 | 证据 |
|---|---:|---|
| 新增恢复/provider/metrics 聚焦 | PASS | 首轮 `39 passed`；第二轮补齐 crash/reducer/route 后最终跨层 `91 passed` |
| Search/Fetch/DeepResearch/Workflow 相邻回归 | PASS | 本轮及前序聚焦累计 `214 passed`，新增范围 0 failure |
| Backend 全量 | BASELINE-FAIL | `4430 passed, 12 failed, 14 skipped, 9 deselected`，786.68s；本地生成 JUnit `backend-full-junit.xml`，该 0.5 MiB generated artifact 不纳入提交 |
| Frontend 全量 Vitest | PASS | `76 files / 795 tests` |
| TypeScript | PASS | `tsc -b --pretty false` |
| Vite production build | PASS | 1394 modules transformed；只有既有 chunk/dynamic-import warning |

Backend 全量的 12 个失败不位于本计划改动范围：

1. `test_agent_parallel.py::test_two_subagents_run_concurrently`（严格计时 flaky）；
2. `test_hybrid_router.py::test_router_with_real_providers_routes_to_local_when_healthy`；
3. `test_image_config_section.py::test_llm_base_url_fallback_is_read`；
4. `test_model_catalog.py::test_model_context_window_is_per_model_not_uniform`；
5. `test_model_catalog.py::test_build_catalog_carries_context_window`；
6. `test_openai_compatible.py::test_integration_ollama_v1_roundtrip`；
7. `test_outcome_verifier.py::test_t10_4_git_diff_skipped_when_not_git_repo`；
8. `test_p4s21_context_bundle_history.py::test_memory_component_promotes_l2_to_meta_l2_history`；
9. `test_p4s21_context_bundle_history.py::test_memory_component_skips_empty_or_unknown_role_rows`；
10. `test_p4s21_context_bundle_history.py::test_memory_component_text_block_no_longer_includes_l2`；
11. `test_v3_remaining_wi.py::test_t5_1_cache_invalidated_on_mtime_change`；
12. `test_v3_remaining_wi.py::test_t5_1_cache_invalidated_on_path_change`。

此前同一仓库全量基线为 21 failures；本轮功能聚焦和全量失败列表均未出现 Search Gateway、FetchExtract、DeepResearch v2、workflow progress/outbox 或新前端组件。

## 3. 真实网络结果

### 3.1 Smoke

`search-gateway-smoke-latest.json`：**PASS**。

- 中文政策查询：8 条，DuckDuckGo 命中，Google/Bing CDP timeout 后降级，4250ms；前三条为 gov.cn、moj.gov.cn、cac.gov.cn。
- 英文 Rust 官方文档查询：8 条，DuckDuckGo 命中，Google CDP timeout 后降级，2000ms。
- MDN WebGPU 正文：408 chars，`scrapling + trafilatura`，canonical URL/hash/quality fields 完整。

### 3.2 Search benchmark

| 样本 | timeout | success | empty | mean Recall@5 | P95 |
|---|---:|---:|---:|---:|---:|
| `benchmark-final.json` | 12s | 100% | 0% | 0.667 | 5186ms |
| `benchmark-latest.json` | 5s | 70% | 30% | 0.45 | 4342ms |

结论：P95 能受 5 秒硬预算约束，但连续查询中 CDP timeout 与 provider cooldown 会放大空结果率；当前 quick-search 可用且会明确 degraded，不代表网络可靠性已经稳定。下一轮应把 benchmark case 隔离/冷却语义与生产突发查询策略一并校准。

真实 DeepResearch benchmark 未执行：当前 relay token 对真实 LLM 请求返回 401，不能用本地 mock 冒充 AC-OBS-02/AC-TEST-02 的真实报告。

## 4. Windows 真机结果

详见 `plans/manual-results-2026-07-14-search-gateway-deepresearch/RESULTS.md`。

- 源码 Tauri 启动、正确 backend 目录、Gateway 默认 ON：PASS。
- 真输入 quick search 与真实来源返回：行为 PASS；仓库截图证据未完整落盘，故完整 TC-W01 记 PARTIAL。
- 历史 Session/投递恢复观察：PARTIAL；启动日志曾恢复 12 条 due deliveries，新 outbox 自动化覆盖了分 delivery crash。
- DeepResearch 逐步可视化、运行中重启、最终 Artifact、双 run：BLOCKED（401）。

## 5. AC 结果矩阵

严格按 31 条 AC 逐项追踪：**27 COMPLETE / 4 PARTIAL / 0 MISSING**。

| AC | 判定 | 主要证据/缺口 |
|---|---|---|
| SG-01 | PASS | `web_search` 与 v2 通用检索均 await SearchGateway |
| SG-02 | PASS | Baidu/DDG/Google CDP/Bing CDP/SearXNG 独立 adapter contract tests |
| SG-03 | PASS | 默认无 SearXNG/Key/Docker；真实中英文 smoke 有结果 |
| SG-04 | PASS | language waves、timeout/empty/blocked/cooldown fallback tests |
| SG-05 | PASS | canonical URL/fingerprint dedupe 与 recency/domain/diversity ranking tests |
| SG-06 | PASS | TTL/LRU、cooldown、deadline、CDP/hydrate budget 和 cancel tests |
| SG-07 | PASS | typed response/request-local diagnostics 和敏感字段负向测试 |
| SG-08 | PASS | `hydrate_top` 有界增强及 partial failure tests |
| SG-09 | PASS | SearXNG 仅 URL 有效时注册，JSON/不可达降级 tests |
| SG-10 | PASS | 默认 ON；工具名、参数与主要 JSON shape 向后兼容 |
| FE-01 | PASS | web/research/scrapling/hydrate 统一共享 FetchExtractService |
| FE-02 | PASS | Scrapling→httpx→shell-gated CDP→explicit Jina；真实 MDN fetch |
| FE-03 | PASS | canonical/title/date/hash/quality fields、去重与 2 MiB gates |
| DR-01 | PASS | v2 search nodes 走 Gateway；direct sources 归一进入相同 evidence contract |
| DR-02 | PASS | fixed 6-slot native fan-out、parallel cap、inactive no-op tests |
| DR-03 | PASS | stable branch/content ordering、restart 和 citation determinism tests |
| DR-04 | PASS | branch budgets、deadline/retry、partial failure/error summary tests |
| DR-05 | PASS | source packs、recency、2-round gap、统一评分、semantic/diversity、claim support tests |
| DR-06 | PASS（自动化） | report/inference/limitations/citations/no-results/artifact tests；真实报告归 TEST-02 |
| UI-01 | PASS | overall summary、13-stage count、live elapsed tests |
| UI-02 | PASS | completed child 持久化、默认折叠与逐阶段 append tests |
| UI-03 | PASS | summary/metrics allowlist 与 traceback/secret 负向 tests |
| UI-04 | PASS | waiting/failure/cancel/final 可见和 degraded warning tests |
| UI-05 | PASS | event/seq 去重、乱序、terminal-late-child tests |
| UI-06 | PARTIAL | 自动化历史/outbox/restart 收敛 PASS；真实运行中重启 UI 闭环受 401 阻塞 |
| UI-07 | PASS | 多 run group、ARIA/keyboard、scroll anchor tests |
| OBS-01 | PASS | gateway/fetch/stage metrics 与固定 detail 白名单 tests |
| OBS-02 | PARTIAL | Search benchmark 已有；真实 DeepResearch coverage/support/domain/P95 缺失 |
| TEST-01 | PASS（功能范围） | 最终跨层 `91 passed`；补齐 task-result/frontier、frontier/dispatcher、分 delivery 三边界及 reducer/route 零 completed；全仓仍有 12 个既有失败 |
| TEST-02 | PARTIAL | quick search + fetch PASS；N03 真实 DeepResearch 被 401 阻塞 |
| TEST-03 | PARTIAL | 真界面启动/quick-search 行为已观察；W01 截图证据不全，W02～W05 未闭环 |

## 6. 剩余收口条件

1. 用户重新登录或提供有效 relay 会话后，用真实 LLM/网络完成 TC-N03。
2. 同一有效会话按 Computer Use 完成 W01 证据补存、W02 逐步展开、W03 运行中重启、W04 Artifact、W05 双 run，并保存每 case 截图与日志摘要。
3. 用真实报告补齐引用覆盖、claim support、独立域名、完成率和 P95。
4. 评估 5 秒 benchmark 的 cooldown/批量隔离策略；若产品目标要求突发十连查也保持高成功率，再调整 provider wave/cooldown，而不是绕过 CAPTCHA/限流。
5. 上述门禁通过后再把 DeepResearch v2 模块与完整计划状态改为 ✅ SHIP。
