# HTN H1 接线与 NanoJev Shadow 执行 Workplan

plan-status: finalized（用户于 2026-09-19 明确授权按本 workplan 开始执行）

日期：2026-09-19

## 目标与范围

本 workplan 以 V2 执行计划和裁定补遗为唯一规格来源，先完成 H1-H/H1-I，再把 `NanoJevAdd.md` 作为独立增量接入。H1 接线与 NanoJev 不合并成一个巨型改动。

当前事实：Host 为 `/Users/denny/projects/simple_harness`；SDK 为 `/Users/denny/projects/simple-harness-sdk`；SDK `main == origin/main == 5ac3f05`。Claude CLI 已安装并验证 4 路并发成功；Grok CLI 禁用。

## 并发与编排规则

- 同时最多运行 4 个 Claude CLI 工作槽，第 5 个日卡槽由另一台电脑使用。
- 每个可写任务使用独立 worktree；实现者与核验者使用不同 Claude CLI 会话。
- 同一热文件同时只允许一个写入者。
- 只读审计、测试设计、NanoJev 契约和模型运行时调查可以并行。
- 不重复实现已经合入的 H1 零件；不为填满并发重复测试。
- Grok 永不调用。
- 记录每个槽的任务、模型别名、耗时、token、实际 diff、测试结果和返工次数。

## 阶段 0：事实与入口审计

四路只读任务：

1. H1-H 热文件、事件流、HTTP 协议字段入口审计。
2. H1-H 最小行为测试与现有测试映射。
3. NanoJev 当前 allocator、retry、event、replay 接入点审计。
4. NanoJev 模型文件、运行时、依赖和本地可用性调查。

阶段 0 不修改业务代码，不运行全量回归。

## 阶段 1：H1-H 测试先行

先写失败行为测试，覆盖：

- 新旧协议双分支；
- HTTP `planning_protocol_version` 合法、未知、缺省三种行为；
- 新协议同时发旧规划拒绝事件和 `PlanningDecisionEvaluated`；
- 旧协议不发新的决定类事件；
- WAIT/NO_CHANGE 只持久化、不编译、不产生计划修订；
- BIND_EXISTING_GOAL/PROPOSE_SUCCESSOR 可解码但本阶段拒绝执行；
- `attempt_ordinal` 从 0 起，复用既有格式重试，最多重试一次；
- 旧协议行为、字节、事件和默认值保持不变。

## 阶段 2：H1-H 实现与独立核验

只让一个实现会话修改 `hierarchical_dispatch.py`、`event_handler.py`、`api/missions.py` 及明确 allowlist 内文件。实现只接线，不新增编译器或状态语义。

另一 Claude CLI 会话只读核验 diff、行为测试、重启/replay、旧路径隔离和至少 12 个行为可区分 mutation。核验者不写生产代码、不提交、不推送。

## 阶段 3：H1-H 完整门禁

开发中先跑接线专项；合入前和合入主干后各跑一次必要完整门禁：

- SDK `full_target` 全量；
- 旧模式 560 条；
- 基线 0 新失败、legacy 0 新失败；
- sentinel ≤22；
- mutation ≥12 全部 killed；
- 独立核验可合。

## 阶段 4：NanoJev Shadow

按以下小片实施，保持 HTN 结构规划不变：

1. 强类型 `DecisionRequest/Result/Provider/Policy`；
2. 包装现有确定性 allocator/retry 为 `ExistingDecisionProvider`；
3. additive Decision 事件与 replay；
4. Fake Provider 驱动的 Shadow Service；
5. optional NanoJev Runtime（MPS/CPU、模型常驻、不提交权重）；
6. NanoJev Provider 转换层；
7. 只接 `READY_TASK_PRIORITY` 与 `RETRY_OR_ESCALATE` 的真实 Shadow。

硬约束：0/1 候选不调用模型；MaxRuns 是 Runtime 硬边界；NanoJev 不能改 DAG；Shadow 返回 Existing 生产结果；Shadow 失败不影响生产；H1 PlanningDecision ID 与 NanoJev `decision_id` 分离。

## 阶段 5：NanoJev Primary（后续门）

Shadow 样本按 DecisionType 分开统计 agreement、high-confidence agreement、verifier pass、retry rate、latency 和 error rate。只有真实数据证明不劣于 Existing baseline 后，才允许 `mode=nanojev`；低置信度、非法输出、超时或异常全部回退 Existing。

## 测试节流原则

- 不在每个小片重复全量回归；只跑与变更和验收门直接相关的测试。
- 热文件接线的合入前/后完整门禁是计划要求，不能省略。
- NanoJev 先跑契约、Shadow failure、0/1 候选、MaxRuns、事件 replay 和一条集成链；共享 Runtime 接线后再跑一次必要回归。
- 原始日志和收据放 `.local-test-evidence/`；Git 只保存结论、命令、状态、索引和哈希。

## 停止条件

遇到计划与源码冲突、模型身份/路径不明、超出 allowlist、旧协议出现新失败，立即停止当前片并记录 blocker；不得用放宽验证、静默 fallback 或删除字段解决。

## 执行记录（2026-09-19）

- 阶段 0：四路 Claude CLI 并发探测成功；宽范围审计任务因边界过大触发最大轮次，未产生业务代码。随后缩小为 H1-H 入口审计、NanoJev 入口调查、契约切片和定向测试任务。
- 阶段 0 结论：H1-H 的真实入口为 `api/missions.py::spec_from_request`、`event_handler.py::_collect_plan`、`hierarchical_dispatch.py::apply_planner_reply` 与 `_record_refusal`；NanoJev 的确定性选择入口为 SDK `scheduling/allocator.py` 的 `frontier`/`allocate` 及 v2 对应函数。
- NanoJev-1 当前产物位于 SDK 独立 worktree `codex/nanojev-contract`：新增 `agent_orchestrator/decision/{types,provider,policy,__init__}.py` 和契约测试；38 个定向 pytest 通过，ruff 通过。
- 独立审阅结论：契约切片可落地；“0/1 候选不调用模型”和“Shadow 不影响生产”必须由后续 DecisionService/Shadow 切片分别实现并测试，不能在契约片宣称已覆盖；嵌套 `Mapping` 的深层不可变、跨请求 `decision_id` 一致性属于后续服务层或 provider 边界的待办。
- NanoJev-1 已提交 `79e5bde`；NanoJev-2 在独立 worktree `codex/nanojev-existing` 基于该提交加入 `ExistingDecisionProvider`，提交 `def698d`。两片合计 45 个定向 pytest 通过，ruff 通过；未接入生产调度器。
- H1-H 行为测试提交在 `codex/h1h-tests`：`0073371` 钉住 HTTP 协议字段行为，`af56b90` 修复 `api/missions.py` 的真实请求映射；协议切换文件 19 个测试通过。一次独立热文件接线任务在 20 轮内没有产生 diff，故 `event_handler.py` / `hierarchical_dispatch.py` 仍未修改，也没有宣称 H1-H 完成。
- 当前状态：NanoJev-1/2 是可审阅提交候选；H1-H 仅完成 HTTP 入口子片，接线主链、双事件、格式重试、WAIT/NO_CHANGE 与完整门禁仍未完成。下一步应先补一份 H1-H 字段映射任务书，再由单一实现会话继续，或由用户决定是否先审阅这两个 NanoJev 提交。

## 执行记录（2026-09-20）

- 专项测试计划已评审：T01–T09 作为局部 HTN + Jev 验收保留；R01/R02 在没有明确 NanoJev checkpoint 时记 `BLOCKED_ENV`，不以 DeepSeek 或 Claude 替代；H1-H 原有完整门禁仍独立保留。
- 已将用户提供的专项测试原文归档为 `HTN-Jev专项测试需求-2026-09-19.md`（SHA-256：`e483e8d1ee4e76e8ca246e6aee3000cadf40c1a3246eb3fcb2cce87650b9a607`），作为本轮测试输入；文档中的执行指令不会覆盖 H1-H 原计划的完整门禁。
- NanoJev 在 `codex/nanojev-existing` 增加了非阻塞 Shadow service 与定向边界测试，候选提交为 `4c608ad`、`b4018b6`，已由主线重放为 SDK `main` 的 `4e17085`、`7ab2630`；随后 SDK `main` 增加了不绑定具体 checkpoint 的 Runtime/Provider 边界 `51dbed2`。覆盖正式结果先返回、异常/超时隔离、请求上下文快照、单候选不调用 Shadow、候选集合与概率归一校验，以及 Runtime 输入输出转换。SDK main 上相关定向 pytest 54 PASS，ruff PASS；尚未接入 HTN，也没有真实模型验证。
- H1-H 重新按任务书核对后确认：当前生产入口没有 `PlanningRequestBinding` 的创建/持久化调用，也没有逐字段构造 `AdmissionContext` 的权威 builder。调查记录见 `H1-H-blocker-AdmissionContext-2026-09-20.md`；这不是总 blocker，接线继续逐字段绑定。只有某个字段确认没有权威来源时，才单独记录该字段 blocker；不得用默认空值或直接复用旧 `apply_planner_reply` 绕过新协议。
- 两轮 Claude CLI 实现任务均在阅读阶段达到最大轮次，没有产生可接受 diff；后续只派发有明确字段映射和文件范围的短任务，避免继续消耗日卡。

## 执行记录（2026-09-20，H1-H 接线推进）

- SDK H1-H worktree 已完成并提交 `a80526c`、`102ad3d`：新协议入口不再直接调用旧 `apply_planner_reply`，而是接入 codec → `PlanningDecisionStore` → admission → adapter → `apply_plan_proposal`；旧协议继续走原路径；第二个提交钉住新事件的固定载荷和 ordinal=0。
- `hierarchical_dispatch.py` 增加 `apply_plan_proposal()`，只复用既有 compile/commit 安全链，不改变旧编译器语义。
- 新协议格式不可读的行为测试已钉住：同时发 `PlanningDecisionEvaluated` 与旧 `PlanningRejected`，新事件载荷包含 `decision_id/request_id/attempt_ordinal/decision_type/status/rejection_codes/canonical_hash`；测试文件新增 1 条，H1-H 请求绑定专项共 3 PASS。
- 相关定向测试：H1-H 请求绑定、协议切换、decision package/store、hierarchical event flow、inflight planning 合计 **248 PASS**；ruff、compileall、`git diff --check` 通过。
- DeepSeeker 只读复核确认三个独立 blocker：planning authorization 没有权威 producer；HtnStore operation 只有 identity/binding，没有 UNKNOWN/reconciled 状态映射；没有 candidate proposal → `PlanShapeView` 的准入前预检。SDK 接线对此 fail-closed，提交 `H1-H-blocker-AdmissionContext-2026-09-20.md` 的补充记录，Host commit `91e40caa`。
- 因上述 blocker，新协议的 admitted/compiled/committed 主链和格式重试的同 request ordinal 仍未宣称完成；完整 H1-H 门禁（full_target、旧模式 560、mutation、独立核验）必须等字段来源补齐后执行。

## 执行记录（2026-09-20，主编排补充裁定：受约束换绑）

- 用户明确要求继续完成并授权按架构最佳实践自行决策。主编排就"格式重试同 request_id、新 intent_id、绑定表记录实际 intent"作出补充裁定，记录在 `任务书-H1-2026-09-19/h1h-impl.md`。
- 裁定内容：允许新增 `PlanningDecisionStore` 的事务化受约束换绑方法；本片白名单扩展 `src/agent_orchestrator/storage/planning_decision_store.py` 及必要的 store focused tests（已写入 `h1h-allow.txt`）。
- 原因：请求身份（含 `intent_id`）在首次插入时冻结，而重试必须在同一 `request_id` 下产生新 `intent_id`；现有 `insert_planning_request` 只有"全等幂等返回 / 不同内容 StoreConflict"两条路径，缺"同一 request 身份、绑定表记录实际 intent"的表达。若不新增，实施者只能伪造 `intent_id` 或新建第二个 `request_id`，两者都会破坏 §34/§35 身份语义。
- 边界（不放宽）：不改 schema/DDL/列集合；不改旧 `insert_planning_request` 幂等与 `StoreConflict` 语义；不改旧协议默认值/旧协议字节/H1-A–G 既有合同；`event_handler.py` 不得直接写 SQL。
- 所需行为验收：①真实梯子——不可读 → 带 repair hint 重问 → 第二次评价复用既有格式重试（最多 1 次），ordinal 0→1，不新建第二套 retry loop；②`created_at` 差异——换绑后保留首次 `created_at`，且 `intent_id` 必变，两条断言都可区分；③冲突——不存在的 request、越序/终态后换绑必须 `StoreConflict`，拒绝后行逐字节原样；④回放——同 `(request_id, attempt_ordinal, raw_output_hash)` 返回既有行、不重复 compile/事件，同 ordinal 不同 raw 仍是 identity conflict，换绑本身幂等；⑤事务性——与同事务决定行写入全落或全滚，store 不自开连接。
- 状态：仅计划记录与白名单更新。**未宣称实现完成**——实现由另一 Claude CLI 代理在 SDK 独立 worktree 执行，核验由独立会话执行；完整 H1-H 门禁（full_target、旧模式 560、sentinel ≤22、mutation ≥12 killed、独立核验）保持原样，仍须在实现落地后执行。

## 执行记录（2026-09-20，当前收口）

本节为收口记录，只追加事实与状态，不修改以上任何旧正文。

**证据目录**：`.local-test-evidence/2026-09-20/continuation-store/`（原始 receipt 与 stream 日志；Git 只保存结论与索引）

- 受约束换绑实现已落地并提交，H1-H 白名单正式扩展 Store writer：`src/agent_orchestrator/storage/planning_decision_store.py` 与 `src/agent_orchestrator/orchestrator/event_handler.py` 进入 `任务书-H1-2026-09-19/h1h-allow.txt` 白名单；新增 focused 测试 `tests/orchestrator/full_target/test_h1h_wiring.py`、`tests/orchestrator/full_target/test_planning_decision_store_rebind.py`。实现语义严格单列：从不插入（无行即 `StoreConflict`）、从不臆造身份、幂等重放先行返回、只 `UPDATE intent_id`（`created_at` 等冻结列不写）、单事务单时钟。
- H1-H focused 门禁：**461 passed / 3 skipped**（3 个 skip 均为测试内声明式跳过）。
- `full_target`：**3699 passed / 5 skipped / 0 failed**（5 个 skip 全为环境/开关性跳过）。
- legacy hot-file 回归：**1960 passed / 7 failed / 20 skipped**。7 个失败已在 clean detached HEAD（`102ad3d`，不含未提交改动）**复现为完全相同结果**，与本次改动无关：6 个为 **p33 tiktoken 缺失**（`ModuleNotFoundError: No module named 'tiktoken'`，位于 `tokenizer.py:51`），1 个为 **AST hash 基线**漂移（`test_p33_source_dependencies.py:505`，`KnowledgeIndex.check` 的 AST dump 与冻结常量 `BASELINE_CHECK_AST` 不符）。`git diff --name-only` 仅 `event_handler.py` + store，未触及 p33 与 tokenizer。
- 有效 mutation：**20 killed / 0 invalid / 4 survived**（目标 ≥12 有效 killed 已达标）。4 个 survivor 如实记录为 gap，不计数：**M14 malformed package_version coverage gap** 是唯一真实覆盖缺口——没有任何测试把畸形 `package_version`（非 int / bool / 未知字符串）喂给 `_planning_decision_package_version`，该拒绝分支未被钉住（源码行为本身正确）；M3/M17/M22 为弱 mutation，非行为缺口。
- NanoJev gate/service slice：**78 passed**（新增 44 + 既有 34），ruff passed；SDK 侧 `full_target` **3748 passed / 5 skipped**。非 full_target 的 **1 个失败为 pre-existing local-capacity failure**（`test_deployment_capacity.py`，缺 SDK `local-capacity` extra），已实证在干净基线上同一用例以同一报错失败，与本改动无关。
- PR-3 decision events + `Store.append_event` replay：**102 passed**，ruff passed。复用既有 `events` 表持久化，按 `idempotency_key` 幂等，additive 自定义 event type，不改任何封闭枚举、不新增表或 migration；同 key 不同 payload fail-closed，H1 `pd-` 前缀 `decision_id` 被拒。
- **无真实 checkpoint**：本轮全部为 fake / shadow / runtime contract 与 Fake Provider，未加载、未下载任何模型权重。**禁止宣称真实模型结果或 Promotion Gate 通过**；`0.80 / 0.20` 是计划先验而非校准结果。NanoJev 生产编排未接线，且这是当前正确状态。
- **下一项**：补 M14 行为钉子（为畸形 `package_version` 拒绝分支加行为测试），并**保持 NanoJev runtime / 真实模型接入 blocked**，直到真实 checkpoint 与校准证据就位。H1-H 主链 admitted/compiled/committed 与格式重试同 request ordinal 的完整宣称、以及旧模式 560、sentinel ≤22、独立核验仍维持原口径待执行。

## 执行记录（2026-09-20，SDK NanoJev Shadow slice 完成）

本节为追加事实记录，只写已完成的事实与状态，不修改以上任何旧正文。

**证据**：SDK `.local-test-evidence/2026-09-20/sdk-nanojev-integration/real/r02_suite.json`（同目录另有 `r02_fixture_freeze.json`）。fixture digest `f77397a8c3ba647bbfafefde1dc939c7bc7d3ec4faa291ca9d981cf30613a3bd`（正文简称 `f77397...`）。Git 只保存结论、路径与哈希，原始 receipt 不入库。

- **本片已完成的三个组件**：
  1. `LocalNanoJevRuntime`（`agent_orchestrator.decision.local_runtime`）——本机 PyTorch MPS 真实推理运行时，模型常驻，权重不入 repo；
  2. acceptance adapter——把 `DecisionRequest/Result` 与 NanoJev 输入输出互转的验收适配层；
  3. real checkpoint MPS runner——用真实 checkpoint 驱动的 MPS 运行器（证据中 `device=mps`、`checkpoint_dir=/Users/denny/.cache/nanojev-install/checkpoint`、`implementation_dir=/Users/denny/.cache/nanojev-install/upstream`）。
- **模型**：`Qwen/Qwen3-0.6B`（真实 checkpoint，非 fake；加载耗时约 11.4 s）。运行环境：macOS 26.4.1 arm64、Python 3.12.13。
- **R02 套件规模与结果**：12 个 fixture + 4 个候选顺序重放。
  - valid returns **12/12**；
  - adapter errors **0**；
  - shadow observations completed **12/12**（`status=completed`，`error=None`，每条都带 `existing_selected` 与 `shadow_selected`）；
  - shadow_production_unchanged **12/12**（生产选择与 Shadow 前逐条一致）；
  - order stable **4/4**（反转候选顺序后答案不变，`changed_answers` 为空）；
  - definite hits **2/8**（按类型：`ready_task_priority` 1/4、`retry_or_escalate` 1/4）；
  - ambiguous **4/4 accepted**（落在 `accepted_set` 内）；
  - 参考量：与 existing 一致 5 次；mean top1 约 0.632、mean margin 约 0.302；latency p50 ≈ 118 ms / p95 ≈ 119 ms（20 次采样，非产品 SLA，p99 未报告）。
- **仅此而已的边界**：本片只有 Shadow。**未进入 Primary，未做任何 promotion**；`mode=shadow`，生产结果仍由 existing provider 返回，Shadow 不获得执行权。证据文件自身的 caveat 也是同一口径（样本量 12+6，不构成生产就绪结论，不得读作 Primary promotion 结果）。
- **模型质量 2/8 是发现，不是调参目标**。它记录的是当前 checkpoint 在“明确偏好”组上的现状，属于待后续数据解释的观察，本片不据此调参、不据此改 gate 阈值；`0.80 / 0.20` 仍是计划先验而非校准结果。证据中 agreement 与 definite hits 度量的是不同事情，命中不等于 production readiness。
- **仍未完成**：HTN/H1-H 与 Primary gate 仍未完成。H1-H 主链 admitted/compiled/committed、格式重试同 request ordinal、旧模式 560、sentinel ≤22、mutation ≥12 killed、独立核验维持原口径待执行；NanoJev 生产编排亦未接线。

## 执行记录（2026-09-20，PR-7 Shadow 接入：Host 接线）

本节为追加事实记录，只写已完成的事实与状态，不修改以上任何旧正文。

**任务书**：`任务书-PR7-2026-09-20/pr7-impl.md`（含白名单 `pr7-allow.txt`）。**SDK 侧 blocker 的裁决**：`plans/2026-09-20-nanojev-pr7-shadow/PR7-BLOCKER.md` 提出的 B-1（无权威配置宿主）与 B-2（无生产 caller/seam）由主编排裁定如下，本片按此执行：

1. **Host 拥有 `decision.mode`**，解析后以**显式 typed policy** 传给 SDK；SDK 不新增配置文件/字段/环境变量，默认仍是 `EXISTING`。
2. **`READY_TASK_PRIORITY` 只做 Shadow 观测**：观测 `frontier()` 的确定性顺序，**分配器授权集合保持唯一权威**。
3. **`RETRY_OR_ESCALATE` 延后**：`RetryAction` 有六个值，本轮无映射裁定，不接线。
4. **`decision_id` 用新确定性命名空间**，永不复用 `pd-`。
5. 禁止：ad-hoc 环境变量、Primary、改 legacy 行为、改 checkpoint、打包发布。

**落地内容**

- SDK 新增 `src/agent_orchestrator/decision/host_integration.py`：`compute_ready_task_decision_id`（`njr-` + `\x1f` 分隔的确定性 id）、`frontier_priority_candidates` / `frontier_priority_candidate_set`、`build_ready_task_priority_request`、`shadow_observation_enabled`、`build_decision_service`、`observe_frontier_priority`、`ready_task_priority_decision`（算 plan → 可选观测 → 返回未改动的 plan）。`decision/__init__.py` re-export；`scheduling/allocator.py` **零改动**。
- Host 新增 `backend/deskpet/orchestration/decision.py`（`DecisionSeam` / `DecisionSeamStatus` / `build_decision_seam` / `seam_available`）；`settings.py` 增 `decision_mode`（白名单 `existing`｜`shadow`）与 `decision_shadow_timeout_seconds`（正数、上限 60s）；`service.py` 在 `_open()` 末尾 `_install_decision_seam()`，journal 复用编排器自己的 Store（`DecisionEventJournal`，写既有 `events` 表，**无新表、无 migration**），`status()["decision"]` 出只读投影；`config.toml` 增 `[orchestration]` 段并把该键注释清楚。

**测试与门禁**

- SDK `tests/orchestrator/full_target`：**3866 PASS / 5 SKIP / 0 FAIL**（128.57s）。新增 `test_decision_host_integration.py` 30 条；决策+分配器相邻套件合计 **205 PASS**。
- Host 新增 `tests/orchestration/test_decision_shadow.py`：**56 PASS**（SDK 源码环境）／**41 PASS / 15 SKIP**（vendored wheel 0.12.2；该 wheel 早于 decision 包，skip 是显式"合同缺席"跳过，不是静默通过）。默认 `EXISTING`、shadow 失败隔离/不阻塞、确定性 id、frontier 观测且授权不变、无 retry 接线五组行为全部钉住。
- Host `tests/orchestration` 全量：**293 PASS / 97 FAIL**，与 `git stash` 干净基线（**237 PASS / 97 FAIL**）**逐条一致**——增量恰为本片 +56 PASS，97 项为既有 SDK pin/环境失败，**0 新失败**。
- mutation：SDK 接缝 6 个行为可区分 mutation 全部 killed（M5"去掉字段分隔符"首轮 survived，据此补强测试后 killed；M7"用 frontier 顺序替换 allocate 授权"被 plan 相等断言杀死）。ruff 本片文件全绿。

**如实记录的新发现（与 SDK 既有实现一致，非本片引入）**

- `SHADOW` 模式下若候选 ≥ 2，`DecisionService` 只写 `DecisionRequested` + `ShadowDecisionProduced`，**不写 `DecisionProduced`**（生产答案在返回时尚未终局，观测是非阻塞的）。测试按实际行为钉住，不按事件名想当然。
- `frontier()` 顺序 ≠ `allocate()` 授权顺序：前者恒按 `(-priority, ordinal)`；后者在 Task 有 §29.3 分数时按分数排。本片按 §57 口径观测 frontier 顺序，**不声称两者一致**。

**仍未完成 / 边界**

- 本片观测是**结构性的、不是模型驱动的**：无真实 NanoJev checkpoint，未加载任何权重。**不得**读作模型质量、shadow 收益或 Primary 就绪。
- 事件落库路径只在接缝层验证过（真实 journal + 真实 Store），未在真实 Mission 驱动循环里跑端到端。
- Primary、`RETRY_OR_ESCALATE`、真实 checkpoint 观测、H1-H 主链 admitted/compiled/committed 与旧模式 560/sentinel ≤22 门禁维持原口径待执行。未打包、未发布。

## 执行记录（2026-09-20，PR-7 本地真实 runtime closure）

用户随后授权把 source-level seam 收口到本地真实 Host runtime；扩展记录在
`任务书-PR7-2026-09-20/pr7-impl.md` 与 `pr7-allow.txt`。扩展只允许真实 SDK caller、
本地 editable-source runtime 和最小 Mission 证据，不允许公开发布、Primary、checkpoint、
retry 映射或 legacy 行为改变。

- 首次生产探针确认 backend/.venv 实际是 vendored `simple_harness_sdk 0.11.1`，且
  `agent_orchestrator.decision` 不存在；Host pin 是 `0.12.2`。该版本 mismatch 先被
  Host manifest 正确拒绝，证据保留在 `.local-test-evidence/2026-09-20/pr7-production-closure/`。
- SDK `Orchestrator._decide()` 的 legacy `allocate()` 分支现在在计算完 `AllocationPlan`
  后调用可选 Host observer；observer 无权修改 plan，异常被隔离到 progress log。
- Host `DecisionSeam` 增加 async live callback；`OrchestrationService` 把它注入真实
  Orchestrator，并保留可选的 test-only `FakeShadowProvider`。
- 本地 backend/.venv 按明确 `editable-source` 身份接入 SDK `0.12.2`，source commit
  `51dbed2`，443 个生产输入、source attestation digest
  `d17399316412bf1470c3f7c043ab64e7e91d718fe46b2eeacfc97384ab59fa74`；未替换或发布
  vendored wheel。
- 最小真实 Mission（2 个 READY Task、真实 `Orchestrator.run()`、真实 Store/events 表、
  FakeShadowProvider）通过：`decision.observations=1`、`failures=0`，落库
  `DecisionRequested` + `ShadowDecisionProduced`，id 为 `njr-...`，授权计划仍由
  allocator 保持权威。SDK 决策相关定向测试 **42 passed**；Host focused suite
  source runtime **56 passed**。
- 仍未完成：vendored release wheel 本身没有 decision 包；真实 NanoJev checkpoint、
  模型质量数据、Primary、`RETRY_OR_ESCALATE`、H1-H 完整门禁均保持原状态。

### PR-7 wheel runtime closure follow-up（2026-09-20）

- 按授权将本地 runtime 从 editable source 切换为唯一开发候选 wheel：
  `simple_harness_sdk-0.13.0.dev20260920-py3-none-any.whl`；未覆盖不可变 `0.12.2`，未发布。
- wheel SHA-256：`9687d023c3fc9bc00c990c35c2827e035c3e56fbda49659e91acefc7fcfd2ef1`；candidate manifest SHA-256：`0fe8c0b5692e3d841d7ddcc5ab1e20fcf4d6cb4b2f54e2c39a87e5d203dad2a8`。
- provenance：基准 commit `51dbed2eaf81d3225bcb15911c33838a79c1fcd3`，工作树 dirty，443 个生产输入，snapshot digest `0324aa4cc27cefbc72386344a2f52050aa0f7f47e2ad6251fc503536dd7e7b77`，`release_published=false`。
- `verify_sdk_candidate` 在 backend/.venv 通过；无 editable 安装、无 SDK checkout `PYTHONPATH`。Host wheel runtime 真实 Mission：EXISTING=COMPLETED/0 observation/0 decision event；SHADOW=COMPLETED/1 observation/0 failure，SQLite 落库 `DecisionRequested` + `ShadowDecisionProduced`；均使用 FakeShadowProvider，无 checkpoint。
- caller 热路径已改为 `ensure_future`，慢 shadow 不阻塞 allocator dispatch；同步 seam 在 event loop 中不再调用 `asyncio.run`，fail-open 返回原 allocator plan。DecisionService journal/drain 异常隔离已加固。
- focused：Host wheel PR7+candidate 64 passed；SDK decision/observer 45 passed。既有 `test_start_mode_driver_composition.py` 的 1 个导入错误与本次变更无关，未扩大回归。
- 未完成边界：hierarchical `allocate_v2` 未接 observer；Primary、真实 checkpoint、RETRY_OR_ESCALATE 仍按原计划冻结。

## 执行范围变更（2026-09-21）

本节是对前文执行记录的追加裁定，不改写前文已经发生的 NanoJev/PR-7 历史事实。依据
`V1.4范围变更-2026-09-21-移出NanoJev.zh-CN.md`，NanoJev runtime、checkpoint、真实模型、Shadow、Primary、PR-7、HTN+Jev 专项和 `RETRY_OR_ESCALATE` 从 V1.4 当前交付中移出并延期。

从现在起，本 workplan 的 active path 只剩：

1. H1-H AdmissionContext 三条真实来源链；
2. H1-H 独立核验、mutation、recovery 和 legacy 隔离；
3. H1-I GPT-5.6 真实模型专项；
4. 完整 H1 门禁和审计包。

前文 NanoJev/PR-7 的测试结果、候选 wheel、checkpoint 审计和 Shadow 收据保留为历史材料，不计入任何 H1 通过证据，也不再派发后续实现任务。原始文档不删除，代码工作树不 reset/clean。

原 H1-H 验收矩阵 I08 在当前执行中改为“无外部 Shadow 独立性”：在没有 NanoJev/Shadow provider、配置和 checkpoint 时，H1 三个 producer 仍必须真实运行，Commit 权限不得依赖外部 Shadow。I01–I08 总数量不变，详见范围变更补遗。
