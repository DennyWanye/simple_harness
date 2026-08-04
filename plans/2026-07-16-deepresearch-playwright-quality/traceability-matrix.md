# DeepResearch v5 AC 追溯矩阵（初始 Gate F 版）

> 日期：2026-07-16
> 平台：**Windows 11 x64 only**
> 结果源：`testcase/2026-07-16-deepresearch-playwright-quality/RESULTS.md`
> 当前判定（2026-07-17 质量修复复验）：**核心质量故障已关闭，TC-UI-NOW 单次立即生成主链 PASS，Gate F 总体 PARTIAL。** AI、官方统计、产品比较三类真实 UI root 已恢复有效证据与正确三态交付；安装、bundled Playwright、durable/fail-closed 继续 PASS。尚未执行的固定场景和发布操作仍为 PENDING。证据见 `results-gate-f.md` 与 `evidence/win11/manual-matrix-20260717/results.md`。

Windows 10、Hyper-V、VM、ISO、Windows Sandbox 与系统重启均不在本轮范围或依赖。

## 质量修复追溯增量

| 缺陷 / AC | code | 自动化 | 真实 UI 结果 |
|---|---|---|---|
| AI Top-N 建模与价值排序（AC-INTENT-01, AC-REPORT-01/02） | `deep_research_v5_policy.py`, `deep_research_v5_report.py` | v5 核心回归 `254 passed` | `9670a357...`：3 个核心维度无 uncovered，5 个有效来源，质量 75，`partial` |
| 中文统计官方检索（AC-QUERY-01, AC-SG-02/03, AC-EVID-02） | `deep_research_v5_queries.py` | query/brief 聚焦 `25 passed`，包含中文统计类型与 240 字符上限 | `51781063...`：4/4 核心、11/5 有效/第一方来源、质量 80、`completed` |
| 产品比较有界补证（AC-QUERY-01/02, AC-DELIVERY-01） | `deep_research_v5_queries.py`, `deep_research_v5_delivery.py` | 产品广域初始探针回归 + v5 delivery/report 回归 | `2b79cd88...`：9/1 来源、1 完整 + 4 部分核心、质量 60、诚实 `partial` |
| 安全投影与时间（AC-UI-01/03） | `deep_research_v5_progress.py`, terminal/public projection | 前端 `77 files / 816 tests`；tsc/build PASS | 三条 run 的 DB/UI elapsed 误差 <2s；终态卡无 raw query/URL |
| 立即生成控制（AC-DELIVERY-02/04, AC-UI-04） | `sessionsStore.ts`, `service.py`, `deep_research_v5_nodes.py`, `research_runtime.py` | 控制/adapter `50 passed`；v5/control/terminal `226 passed` | `6ea6a3a4...`：真实点击后 0.227s observed，deadline 后唯一 `insufficient_evidence` 终态，全部节点 succeeded、硬失败 0 |

下方逐 AC 表保留初始 Gate F 审计状态；没有被上表或既有 Gate F 证据明确覆盖的项仍不得推断为 PASS。

| AC | Task | 生产 code owner | 自动化/持久化 testcase | 当前 result / 待补证 |
|---|---|---|---|---|
| AC-INTENT-01 | T1,T3,T15 | `deep_research_v5_contracts.py`, `deep_research_v5_policy.py` | `test_deep_research_v5_brief_policy.py`; TC-AUTO-EDU | PENDING：补最终聚焦命令、SC-EDU trace 六个 dimension id |
| AC-INTENT-02 | T1,T5,T10,T15 | `deep_research_v5_contracts.py`, `deep_research_v5_evidence.py`, `deep_research_v5_report.py` | brief/evidence/report tests; TC-AUTO-EDU | PENDING：补核心维度未披露时硬失败及最终报告证据 |
| AC-QUERY-01 | T3,T4,T15 | `deep_research_v5_queries.py`, `retrieval/query_terms.py` | `test_deep_research_v5_queries.py`; TC-AUTO-EDU | PENDING：补四类 query family 与教育部/政府定向 query trace |
| AC-QUERY-02 | T4,T8,T15 | `deep_research_v5_queries.py`, `deep_research_v5_nodes.py` | query/loop tests; TC-AUTO-RESCUE | PENDING：补只消费缺口维度的 round trace |
| AC-SG-01 | T4,T9,T14,T15 | `deep_research_v5_queries.py`, `deep_research_v5_analysis.py`, `retrieval/ranking.py` | query/analysis tests; TC-AUTO-EDU | PENDING：补固定 passage budget 公平分配与默认接线 |
| AC-SG-02 | T4,T15 | `retrieval/query_terms.py`, `retrieval/ranking.py` | `test_deep_research_v5_queries.py`; TC-AUTO-EDU | PENDING：补中文相关>无关、真实教育 run 非零 relevance |
| AC-SG-03 | T4,T5,T9,T15 | `deep_research_v5_evidence.py`, `deep_research_v5_analysis.py` | evidence/analysis tests; TC-AUTO-EDU | PENDING：补官方原文优先 selection reason 与报告人工 review |
| AC-SG-04 | T4,T5,T15 | `deep_research_v5_evidence.py`, `retrieval/contracts.py` | `test_deep_research_v5_evidence.py`; TC-AUTO-EDU | PENDING：补 family property tests 结果与 source trace |
| AC-SG-05 | T8,T15 | `deep_research_v5_loop_policy.py`, `deep_research_v5_nodes.py` | loop policy/graph tests; TC-AUTO-LEASE/PLATEAU/CAP | Gate C 聚焦旁证 PASS；Gate F 固定场景和机器 trace PENDING |
| AC-SG-06 | T2,T4,T8,T15 | `workflows/effects.py`, v5 queries/loop nodes | effects/query/loop tests; TC-AUTO-RESCUE/PLATEAU | PENDING：补 deterministic-first、role/usage/delta 与无自由循环证据 |
| AC-PW-01 | T0,T6,T15 | `playwright_bundle.py`, `deskpet-backend.spec`, lock/build scripts | `test_playwright_bundle.py`; TC-INSTALL-01 | Gate C version/revision/hash 旁证 PASS；完整 Gate F build manifest PENDING |
| AC-PW-02 | T0,T6,T15 | bundle/spec/Tauri resources/licenses | bundle tests; TC-INSTALL-01/02 | Gate C frozen render 旁证 PASS；NSIS 断网隔离安装 PENDING |
| AC-PW-03 | T7,T14,T15 | `retrieval/fetch_extract.py`, `retrieval/playwright_renderer.py`, `retrieval/runtime.py` | renderer/fetch tests; TC-AUTO-PW, TC-INSTALL-02 | Gate C 聚焦旁证 PASS；默认生产接线与安装链 PENDING |
| AC-PW-04 | T7,T15 | `retrieval/playwright_renderer.py`, backend lifecycle | `test_playwright_renderer_pool.py`; TC-AUTO-PW, TC-INSTALL-04 | Gate C pool/orphan 旁证 PASS；安装应用退出/取消真机 PENDING |
| AC-PW-05 | T6,T7,T15 | `playwright_bundle.py`, `retrieval/playwright_renderer.py` | bundle/renderer crash tests; TC-AUTO-PW | Gate C crash 单次重启旁证 PASS；完整集成故障注入 PENDING |
| AC-PW-06 | T7,T15 | `retrieval/playwright_renderer.py` | renderer allowlist tests; TC-AUTO-BLOCK | Gate C 动作/域/时限旁证 PASS；Gate F 聚合结果 PENDING |
| AC-EVID-01 | T5,T7,T9,T15 | `deep_research_v5_evidence.py`, `playwright_renderer.py`, `deep_research_v5_analysis.py` | evidence/renderer tests; TC-AUTO-BLOCK | PENDING：补百度安全验证 fixture 全链路拒绝与最终报告负向证据 |
| AC-EVID-02 | T5,T9,T15 | `deep_research_v5_contracts.py`, evidence/analysis modules | evidence/analysis tests; TC-AUTO-EDU/AI | PENDING：补 claim→passage→URL round-trip 与 report review |
| AC-EVID-03 | T5,T7,T8,T15 | `deep_research_v5_evidence.py`, loop nodes, fetch renderer | evidence/loop/fetch tests; TC-AUTO-BLOCK/EDU | PENDING：补 readiness 边界与严重不足跳过 full synthesis |
| AC-REPORT-01 | T9,T10,T15 | `deep_research_v5_analysis.py`, `deep_research_v5_report.py` | analysis/report golden; TC-AUTO-EDU/AI | PENDING：补三 profile golden 与 generic 不委托 v3 链路 |
| AC-REPORT-02 | T10,T15 | `deep_research_v5_report.py` | report golden/adversarial tests; TC-AUTO-EDU/AI, TC-UI-EDU/AI | PENDING：补 3～7 判断与负例、真人首屏截图 |
| AC-REPORT-03 | T3,T9,T10,T15 | policy/analysis/report modules | brief/analysis/report tests; TC-AUTO-EDU, TC-UI-EDU | PENDING：补事实/分析/未来分层 golden 与人工 review |
| AC-REPORT-04 | T3,T9,T10,T15 | analysis/report/evidence modules | support/dimension tests; TC-AUTO-EDU/AI | PENDING：补 unsupported key claim 硬失败和就近引用人工 review |
| AC-REPORT-05 | T10,T15 | `deep_research_v5_report.py` | `test_deep_research_v5_report.py`; TC-AUTO-EDU/AI | PENDING：补公式边界/hash/mutation 及真实 report audit |
| AC-REPORT-06 | T10,T15 | `deep_research_v5_report.py`, progress safe projection | report/progress redaction tests; TC-AUTO-EDU, TC-UI-EDU | PENDING：补 Markdown/trace 分离 golden 与真人 UI 泄漏检查 |
| AC-DELIVERY-01 | T1,T10,T11,T15 | contracts/report/`workflows/terminal_projection.py` | terminal/delivery tests; TC-AUTO-EDU/NOW | Task 11 后端旁证 PASS；真实三态产品交付 PENDING |
| AC-DELIVERY-02 | T1,T8,T11,T14,T15 | loop policy/nodes, terminal/control, config/main | loop/control/config tests; TC-AUTO-LEASE/CAP/NOW | PASS：源码默认接线下真实 run `6ea6a3a4...` 在长 fetch 中观察 control，并在 settle fence 后形成唯一业务终态 |
| AC-DELIVERY-03 | T1,T2,T14,T15 | `workflows/effects.py`, LLM V2 port/ledger, config/main | usage/effect/config tests; TC-AUTO-RESCUE/PLATEAU | PENDING：补事务 reservation/recovery、真实 usage 透传、默认预算 |
| AC-DELIVERY-04 | T8,T10,T11,T13,T15 | loop/report nodes, terminal/service/ipc, frontend workflow components | control/report/frontend tests; TC-AUTO-NOW, TC-UI-NOW | PARTIAL：单次真点击 accept→observe→settle→consume 与唯一终态 PASS；重复点击/重连/强杀重启仍 PENDING |
| AC-DELIVERY-05 | T2,T10,T15 | effects/ledger, `deep_research_v5_report.py` | report repair/effect tests; TC-AUTO-EDU/AI/PLATEAU | PENDING：补定向章节修复、无新知识、两轮无增益停止 |
| AC-DELIVERY-06 | T1,T11,T13,T15 | `store/research_repository.py`, terminal/service/ipc, frontend components | repository/control/frontend tests; TC-AUTO-CONTINUE, TC-UI-CONTINUE | Task 11 后端旁证 PASS；真人 continue/旧报告不可变 PENDING |
| AC-UI-01 | T8,T12,T13,T15 | `deep_research_v5_progress.py`, `workflows/progress.py`, `sessionsStore.ts`, `WorkflowProgressGroup.tsx` | backend/frontend progress tests; TC-UI-EDU/AI | PENDING：补 reducer/component/a11y 与 Xiaomi 持续进度截图 |
| AC-UI-02 | T12,T13,T15 | v5 progress projection, `WorkflowProgressGroup.tsx`, message panels | progress/component tests; TC-UI-EDU/AI | PENDING：补五字段、默认折叠、waiting/terminal 可见真人证据 |
| AC-UI-03 | T11,T12,T13,T15 | terminal/progress allowlist, frontend types/components | allowlist/redaction tests; TC-UI-EDU/NOW | PENDING：补 UI 无 raw query/URL/Cookie/token/stack 截图与 payload audit |
| AC-UI-04 | T11,T12,T13,T15 | repository/control/progress, `sessionsStore.ts`, workflow components | reconnect/history/reducer tests; TC-UI-NOW/RECONNECT/CONTINUE | Task 11 后端旁证 PASS；WebSocket 重连、重启、历史加载真人证据 PENDING |
| AC-COMPAT-01 | T0,T1,T11,T13,T14,T15 | definitions/bootstrap/native/terminal/repository/config/main/frontend store | historical fixtures/config/full regression; TC-AUTO-COMPAT, TC-UI-HISTORY, TC-INSTALL-03 | PENDING：补 v1～v4 fixtures、v5 default ON、全量回归与更新恢复 |

## 固定场景覆盖检查

| 固定场景 | testcase | Gate F 结果 |
|---|---|---|
| SC-EDU-01 | TC-AUTO-EDU + TC-UI-EDU | PENDING |
| SC-AI-01 | TC-AUTO-AI + TC-UI-AI | PENDING |
| SC-PW-01 | TC-AUTO-PW + TC-INSTALL-02 | PENDING（Gate C frozen render 有旁证） |
| SC-BLOCK-01 | TC-AUTO-BLOCK | PENDING |
| SC-RESCUE-01 | TC-AUTO-RESCUE | PENDING |
| SC-LEASE-01 | TC-AUTO-LEASE + TC-UI-EDU | PENDING（Gate C fake clock 有旁证） |
| SC-PLATEAU-01 | TC-AUTO-PLATEAU | PENDING（Gate C loop 有旁证） |
| SC-CAP-01 | TC-AUTO-CAP + TC-UI-CONTINUE | PENDING（Gate C loop 有旁证） |
| SC-NOW-01 | TC-AUTO-NOW + TC-UI-NOW | PARTIAL/PASS 主链：真实 run `6ea6a3a4...` 单次点击完整闭环 PASS；重复点击/重连/强杀分支 PENDING |
| SC-CONTINUE-01 | TC-AUTO-CONTINUE + TC-UI-CONTINUE | PENDING（Task 11 后端有旁证） |

## 待补证总表

1. Task 12/13/14 最终生产接线和聚焦测试名/结果。
2. 完成全量 pytest 与 NSIS 重新构建对比；本轮 v5 `254 passed`、联合 `559 passed + 2 isolated rerun passed`、Vitest `816 passed`、tsc/build/cargo 已通过。
3. 当前 Windows 11 主机隔离目录的断网安装、更新、卸载和无 orphan。
4. 补 TC-UI-NOW 重复点击/重连/强杀分支与完整 v4 history；单次立即生成主链、AI/统计/产品三类源码 UI、原教育/继续调研/重启已有证据。
5. SC-EDU/AI 最终 Markdown 的人工专业 review。
6. 幂等审查表每行最终 `file:line` 与对应测试签字。
