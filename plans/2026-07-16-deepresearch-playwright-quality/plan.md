<!-- plan-status: finalized (plan-bs); scope-revision: win11-only user-approved 2026-07-16 -->
# Plan：DeepResearch v5 通用质量闭环与内置 Playwright

> 状态：已完成 7 轮 plan challenger 并最终 `VERDICT: PASS`；用户已于 2026-07-16 最终通过，并于同日明确将平台验收范围修订为仅 Windows 11 x64，交由 `plan-task` 实施与测试。
> 验收事实源：[`acceptance.md`](./acceptance.md)
> 架构基线：[`architecture-baseline.md`](./architecture-baseline.md)
> 当前代码基线：`0117ad764f593d018392332541d475a1d52a07ac` 加 2026-07-16 工作树中的 v4/Search Gateway/UI 改动

## 1. 主要矛盾

决定成败的核心问题不是“再接一个浏览器”，而是当前发布决策把“事实有引用”误当成“报告回答了问题”。固定教育样本以 100% support rate 发布，却没有形成教育现状、核心矛盾、政策方向与影响的综合结论。

本计划先把用户问题建模为可验证核心维度，再让检索、证据、分析、报告审计和补证共享同一份维度状态。Playwright 只是高价值动态页面的受控证据入口；若维度覆盖和报告质量不过门，系统只能交付 `partial` 或 `insufficient_evidence`，不能再用事实条数、引用数和正文长度投机通过。

这是结构层问题：generic 研究的质量状态没有 owner，Search Gateway 只知道单 query，v3/v4 support gate 只知道原子 claim，UI 只知道阶段。继续为教育、政策等 profile 增加 renderer 或 prompt 特例会让同类问题反复出现。范围可控的根治方式是新增 immutable `deep_research/v5` 通用质量协议，同时保留 v1～v4 历史恢复语义。

## 2. 最佳实践调研与 DeskPet 适配

### 2.1 Playwright 随包交付

Playwright 官方 Python 文档明确支持 `PLAYWRIGHT_BROWSERS_PATH=0` 安装 Chromium 后由 PyInstaller 打包，并提醒只打包实际使用的浏览器以控制体积；每个 Playwright 版本对应特定浏览器构建。来源：

- https://playwright.dev/python/docs/library
- https://playwright.dev/python/docs/browsers
- https://playwright.dev/python/docs/release-notes

适配判断：DeskPet 是 PyInstaller backend + Tauri/NSIS 两层打包，不是单个 `pyinstaller -F` 可执行文件；且已有 1.30 GB frozen backend 和 makensis mmap 历史约束。因此采用“固定 Python 版本 + 固定 Chromium revision + 构建期下载到 repo 外 build cache + PyInstaller COLLECT + Tauri resource/sidecar 路径诊断”的改造方案，只打 Chromium new-headless。开发 venv 当前的 Playwright 1.61.0 只是 spike 输入，必须经当前 Windows 11 x64 主机上的 frozen 构建、断网隔离安装与渲染验证后才锁版；Windows 10 认证延期。

### 2.2 Durable 循环与用户中途决策

LangGraph 官方持久化说明强调 checkpoint 在 graph super-step 边界形成，不会在一个 node 函数中间自动保存；恢复时 node 可能从头执行，副作用必须幂等。来源：

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://docs.langchain.com/oss/python/langgraph/interrupts

适配判断：DeskPet 已用自有 NativeWorkflowExecutable，不引入 LangGraph；但 checkpoint/重放原则相同。300～900 秒 rescue、report repair 和 `generate_now` 不能塞在一个 handler 内。v5 使用显式 `gap_evaluate -> gap_work -> gap_join` 与 `quality_audit -> repair_work -> repair_join` 条件回边，每次外部调用后提交 checkpoint；写入以 `(run_id, operation_id, item_id, attempt)` 幂等。

### 2.3 报告质量不能等同 citation support

RAGAS 把 faithfulness、answer relevance、context relevance 分开评估；DeepResearch Bench 又把报告生成质量与检索/引用可信度分开，并使用针对题目的自适应标准；DeepResearch Bench II 进一步把 expert report rubric 拆为信息召回、分析和表达三类原子可验证项。来源：

- https://aclanthology.org/2024.eacl-demo.16.pdf
- https://deepresearch-bench.github.io/static/papers/deepresearch-bench.pdf
- https://arxiv.org/abs/2601.08536

适配判断：DeskPet 不需要复制一个大型学术 benchmark，也不能让另一个自由 LLM judge 成为唯一发布者。采用小而确定的双门：claim support 继续由 deterministic passage gate 负责；题目相关性、核心维度覆盖、来源质量、综合推理、时效/不确定性、可读性由版本化 rubric 评分，其中核心维度缺失、unsupported key claim、无效页面、官方原文被次级来源替代是硬失败。LLM audit 只提供结构化候选，deterministic validator 最终裁决。

### 2.4 Token/成本按 provider usage 记账

成熟 agent SDK 只有在上游返回 usage 时才传播真实 input/output token；不能把固定调用次数当成本模型。来源：https://openai.github.io/openai-agents-python/usage/

适配判断：DeskPet 的 `OpenAICompatibleProvider.chat_with_tools()` 已返回 `usage`，但 `_resolve_default_llm_call()` 在 `research_tools.py:2037-2048` 把它丢成字符串。v5 不新增第二套 provider，而是增加 usage-bearing `ResearchLLMResult`/`ResearchLLMPortV2` adapter，保留旧字符串 port 给 v1～v4；ledger 使用上游 usage，只有 provider 不返回时标记 `usage_unknown` 并以本地估算做安全保留，不伪装成真实成本。

## 3. 不变约束与版本策略

- v1～v4 definition、nodes、manifest/hash、state factory、checkpoint 和历史消息解释不可变；新 run 完成接线后默认 v5。
- Search Gateway 继续是内置 SearXNG-like 默认搜索内核；真实 SearXNG 只作为可选 provider，不要求用户安装 Docker或付费 API。
- `browser-use` 继续属于 code E2E 工具，不进入 DeepResearch 默认链路；Playwright renderer 归 retrieval 层。
- 已完成能力在测试阶段默认 ON；只有 build spike 未过本轮 Windows 11 硬门时不能宣称完成或把假 fallback 当成默认能力。Windows 10 不在本轮完成声明范围内。
- 用户报告、机器质量审计、workflow diagnostics 分离；报告不再倾倒 Coverage JSON、provider attempt 列表或内部错误栈。
- 所有外部调用遵守 cancellation/deadline、隐私安全 diagnostics 和 idempotency；不持久化 prompt、网页正文、凭据或任意 URL 列表到聊天进度。

## 4. 文件影响清单

| 文件/目录 | 当前职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/workflows/definitions/deep_research_v5_contracts.py`（新） | 无 | ResearchBrief、DimensionCoverage、GapWorkItem、budget/LLM ledger、analysis/audit/delivery/lineage/control contracts |
| `backend/deskpet/workflows/definitions/deep_research_v5_policy.py`（新） | 无 | profile 模板、中文切词、coverage/source-family、lease/plateau、quality rubric 的确定性策略 |
| `backend/deskpet/workflows/definitions/deep_research_v5_nodes.py`（新） | 无 | v5 节点 handler、checkpointed rescue/repair loop、三态报告输入 |
| `backend/deskpet/workflows/definitions/deep_research_v5_report.py`（新） | 无 | 结论先行 renderer、专业结构、用户版/机器审计分离 |
| `backend/deskpet/workflows/definitions/v5/{__init__,deep_research}.py`（新） | 无 | immutable v5 graph、条件边、manifest、initial state、public stage projection |
| `backend/deskpet/workflows/{bootstrap.py,launcher.py,service.py,ipc.py,progress.py}` | 注册、启动、恢复、控制面、进度 | 注册 v5；action matrix；generate_now/continue/retry；v5 progress schema |
| `backend/deskpet/workflows/{native.py,terminal_projection.py（新）}` | engine 终态与 terminal intents | engine status 与 research delivery status 分层；按 workflow/version 注册 terminal-public validator |
| `backend/deskpet/workflows/{effects.py,store/schema.py,store/run_store.py}` | durable effect journal、workflow DB | v5 外部 read/LLM outcome journal、run control command、evidence snapshot pin/child-run 事务 |
| `backend/main.py` | context/state factory、默认版本、workflow WS | v5 adapter/ports/default；usage adapter；decision/continue IPC 接线 |
| `backend/deskpet/tools/research_tools.py` | live research LLM bridge | 新增保留 usage 的 v2 call；旧 `_LLMCall -> str` 不变 |
| `backend/deskpet/retrieval/{contracts.py,ranking.py,fetch_extract.py,runtime.py}` | 检索/抓取 contracts 和共享 runtime | dimension/source-family metadata、中文 relevance、分层 render budget、Playwright fallback |
| `backend/deskpet/retrieval/playwright_renderer.py`（新） | 无 | bundle resolver、browser pool、context/page 生命周期、只读动作白名单和结构化错误 |
| `backend/deskpet/retrieval/providers/*`、`search_gateway.py` | provider 搜索与共享 limiter/circuit | 保持 provider 语义；只扩展 dimension-safe observation/预算传递，不复制 Gateway |
| `backend/config.py`、`config.toml` | feature/runtime 配置 | v5 默认 ON；lease/quality/LLM/browser 配置与安全范围；旧配置 backfill |
| `backend/pyproject.toml`、锁文件、`deskpet-backend.spec` | Python 依赖与冻结 | Playwright 精确锁版、driver/browser COLLECT、许可证/诊断资源 |
| `scripts/build-*`、`tauri-app/src-tauri/tauri.conf.json` | backend/Tauri/NSIS 构建 | 构建期 browser install/校验、resource 映射、版本 mismatch 失败 |
| `tauri-app/src/types/messages.ts`、`stores/sessionsStore.ts` | WS types、消息 projection | v5 capability、dimension/quality/lease/control/lineage allowlist 与去重 |
| `tauri-app/src/components/workflow/WorkflowProgressGroup.tsx` | 聚合进度卡 | 总体覆盖/质量/预算/预计终态；默认折叠的逐步气泡；三种控制动作 |
| `tauri-app/src/message-panel/MessagePanelRoot.tsx`、`MessageStreamPanel.tsx` | IPC 与消息渲染 | generate now、continue、retry 的 pending/accepted/error 状态 |
| `scripts/acceptance/`、`backend/tests/`、前端测试、`testcase/` | 自动化/真机验收 | 固定质量 benchmark、dynamic fixtures、frozen/clean-machine/GUI E2E |
| `ARCHITECTURE/`、`PROJECT_STATUS.md` | 生产事实唯一来源 | 仅在实现与全部验收通过后回写 v5 当前事实、状态和证据；不写 STATUS |

## 5. 任务清单（按依赖排序）

### Task 0 — 锁定工作树基线与构建 spike `[AC-PW-01, AC-PW-02, AC-COMPAT-01]`

- 改动文件：本 plan 目录 `baseline.md`、`spikes/playwright-bundle/`（可丢弃输出，不进入产品代码）、构建日志目录。
- 现状：开发 venv 有 Playwright 1.61.0 和 driver，无 Chromium cache；frozen backend 约 1.30 GB；PyInstaller/Tauri 都未收集 browser。
- 修改方式：
  1. 先运行并记录相关 pytest/vitest/tsc/build 与已知失败，区分本轮回归和现有红项。
  2. Gate A 只锁定当前 Xiaomi/主机上的 Windows 11 x64 环境，记录 OS build、架构和安装基线。本轮不启用 Hyper-V、不创建 VM、不要求 ISO 或系统重启；Windows 10 兼容性与干净机认证延期到后续独立计划，且本轮结果不得表述为已支持 Windows 10。
  3. 枚举候选 Playwright 精确版本与对应 Chromium revision；只在 repo 外 build cache 安装 Chromium new-headless。
  4. 用现有 `deskpet-backend.spec` 派生一次 frozen spike，验证 import、driver、executable path、固定动态页 render、断网运行和退出清理。
  5. 在当前 Windows 11 主机使用隔离安装目录、隔离 `DESKPET_USER_DATA_DIR`、清空开发 browser cache 并断开外网，执行静默安装→首次启动→health/dynamic fixture→进程快照→更新→卸载；把 JSON/JUnit、事件日志、进程列表、网络连接快照和截图写入 `plans/.../evidence/win11/`。不依赖 Hyper-V、PowerShell Direct 或 VM checkpoint。
- 验证：输出 Windows 11 OS build、artifact hash、Playwright version/revision/path/hash/size 表与 spike 日志；不存在 CDN 请求；删除开发 browser cache 后 frozen artifact 仍能 render。当前 Windows 11 环境可直接进入该门，不再存在 Win10 外部前置阻塞。
- 依赖：无。Spike 只用于决策，不滚成业务实现。

### Task 1 — 冻结 v5 contracts、graph identity 与兼容矩阵 `[AC-INTENT-01, AC-INTENT-02, AC-DELIVERY-01, AC-DELIVERY-02, AC-DELIVERY-03, AC-DELIVERY-06, AC-COMPAT-01]`

- 改动文件：新建 `deep_research_v5_contracts.py`、`definitions/v5/*`；修改 `definitions/__init__.py`、`bootstrap.py`；新增 v5 fixture/tests。
- 现状：v4 state 以松散 dict 为主；v1～v4 已注册；retry adapter 硬编码 v4。
- 修改方式：
  1. 定义严格 `to_json/from_json/validate` contracts：`ResearchBrief`、`ResearchDimension`、`DimensionCoverage`、`EvidenceSourceFamily`、`GapWorkItem`、`ResearchBudgetLedger`、`ResearchLLMResult/LedgerEntry`、`DimensionAnalysis`、`ReportQualityAudit`、`DeliveryDecision`、`ResearchOperationLineage`、`ResearchControlCommand`、`ResearchEvidenceSnapshot`。
  2. 所有时间 ledger 保存 wall-clock anchor + accumulated active duration；运行时用 monotonic delta，跨进程恢复不比较不同进程 monotonic epoch。
  3. 注册 immutable `deep_research/v5` graph，显式节点/条件边包含 `gap_evaluate/work/join` 与 `quality_audit/repair/join`；public stage 仍聚合为现有用户可理解阶段。
  4. 固定 manifest/hash、initial state、state schema version；v1～v4 fixture 字节级不变。
  5. 明确双层终态：engine `terminal_status` 仍只使用 `completed/error/cancelled`；研究正常收敛（无论业务为 completed/partial/insufficient）都映射 engine completed，内部故障才映射 error。业务三态唯一 owner 为 `values.delivery_decision.status`，并投影为 `terminal_public.delivery_status`、report/card/receipt outcome；持久化 invariants 禁止 engine status 与 delivery status 互相冒充。
- 验证：contract roundtrip/property tests；非法状态 fail closed；v1～v5 registry/manifest/recovery fixture；旧 run 恢复不调用 v5。
- 依赖：Task 0 只提供 browser 决策，不阻断纯 contract 编写。

### Task 2 — 建立 usage-bearing Research LLM Port、durable call effect 与预算账本 `[AC-SG-06, AC-DELIVERY-03, AC-DELIVERY-05]`

- 改动文件：`research_tools.py`、`research_core.py`（只新增 V2 port，不改旧 port 语义）、`main.py` v5 context factory、`workflows/effects.py`/effect adapter、新 contracts/policy、provider adapter tests。
- 现状：`OpenAICompatibleProvider.chat_with_tools()` 已返回 `content/model/usage`；`_resolve_default_llm_call()` 只返回 content string，ResearchLLMPort 不知道 token。
- 修改方式：
  1. 新增 `_resolve_default_llm_call_v2()` 返回 `ResearchLLMResult(content, model, input_tokens, output_tokens, cache_tokens, usage_source, request_id)`；从 provider response 的 `usage` 归一 OpenAI/relay 字段。
  2. 旧 `_resolve_default_llm_call()` 与 v1～v4 port 不变；v5 context 注入 `ResearchLLMPortV2.complete(role, payload_ref, max_output_tokens, stable_call_id)` 和 `ResearchCallEffectPort`。
  3. provider 新增默认不变的 `chat_with_tools_at_most_once()` 公开入口：底层 HTTP transport retries=0，单次 stream attempt，禁止 400 修复重试、tool-choice 重试和 non-stream fallback；旧 `chat_with_tools()` 继续保持 resilient 重试语义。v5 resolver 只走 at-most-once 入口，避免 opaque effect 在 journal 外重复出站。
  4. `ResearchCallEffectPort.complete(...)` 必须显式接收 `execution_identity=context.identity`；缺失 identity 立即拒绝。由于 `NodeExecutionIdentity` 不携带 lease owner/epoch，v5 node context 必须注入严格的 `identity -> EffectExecutionContext` resolver，返回该 node 执行开始时已捕获的 `RunFence`；adapter 校验 run/workflow/version/node/checkpoint/task/attempt 等全部可用字段，任何缺失或不匹配立即拒绝。禁止从 workflow/session 猜唯一 active run，也禁止从 DB 查询“当前 lease”把旧 handler 升级为 takeover 后的新 fence。
  5. workflow.db v3 新增 `workflow_effect_budget_reservations(effect_id PK/FK, run_id, ledger_kind, input_reserved, output_reserved, cost_reserved_micros, input_actual, output_actual, cost_actual_micros, status, dispatch_state, upstream_started_at, created_at, updated_at)`；status=`reserved/committed/released/held_uncertain`，dispatch_state=`not_started/started` 且与 upstream_started_at 有 CHECK 约束。`EffectJournal.begin_with_budget()` 在同一个 `BEGIN IMMEDIATE` 中以 run fence 校验 active reservations sum < run capability snapshot 中的 token/cost cap，插入 prepared effect 与 reservation；并发节点不能先在内存判断再各自 begin。调用 provider 前用独立短事务 CAS `not_started -> started`；恢复时只有 not_started 可 release，started 且无 usage 必须 held_uncertain。
  6. Task 2 独占 `_migrate_v2_to_v3()`：一次创建预算 reservation、run control、research snapshot/pin/lineage 的完整 v3 DDL 骨架与全部索引；fresh DB 先建 v2 base 再走同一 migration loop，v1 走 v1→v2→v3。Task 11 只实现这些表的 repository/状态机/API，不再补同版本 DDL，避免先生成的 v3 DB 永久缺表。
  7. 为 search/fetch/browser 使用 `EffectKind.IDEMPOTENT_READ` 可复用 outcome；LLM 使用现有 `EffectKind.OPAQUE_MANUAL`/at-most-once policy。调用后把 content 写 RegisteredBlobStore、把 usage/model/result_ref 写 effect outcome，node checkpoint 只引用已 committed effect。
  8. 核销规则固定：未进入 provider transport 的 validation/budget拒绝将 reservation `released`；有 usage 的 success/failure/timeout 写 actual 并 `committed`、释放预留差额；已出站但无 usage 的 timeout/cancel/crash 为 `held_uncertain` 并按全部预留计入 cap，run terminal 时仍保留 charged reserved；调用前 crash 的 prepared effect由 startup reconcile 确认为 no-upstream 后 release。LLM 响应与 journal commit 之间崩溃不得静默重发同 stable_call_id；若 provider 将来支持幂等键，才可由新 policy reconcile。
  9. `LLMBudgetLedger` 的 authoritative entries 从 reservation/effect 表重建，checkpoint state 只是投影；每次 committed effect 记录 coverage/readiness/quality before-after。role 只允许 modeling/query_strategy/dimension_analysis/report_synthesis/quality_audit/targeted_repair。
- 验证：真实 provider-shaped fixtures、effect EXECUTE/REUSE/UNCERTAIN、并发 reservation、missing usage、crash-before-call/crash-after-response-before-commit/timeout/cancel、cache token、重启重建；证明没有固定调用次数上限、不会重复同一 opaque LLM effect、不会越 ledger。
- 依赖：Task 1。

### Task 3 — 通用 ResearchBrief、profile 与核心维度建模 `[AC-INTENT-01, AC-INTENT-02, AC-REPORT-04]`

- 改动文件：`deep_research_v5_policy.py/nodes.py`、profile fixture `generic_research/policy_education/technology_intelligence`、benchmark fixtures。
- 现状：v4 只识别 technology intelligence；generic 没有核心维度 owner。
- 修改方式：
  1. normalize 先用确定性语言/时点/地域/对象/比较意图识别，再调用一次 `modeling` role LLM 输出严格 JSON；validator 只接收已知字段和 3～8 个互斥、可核验核心维度。
  2. profile 提供模板和 source preference，不硬编码最终结论。`policy_education` 对固定教育题的 deterministic fallback 和 fixture 必须原样包含六个 core dimension id：`edu_current_state`（教育现状）、`edu_scale_trend`（规模/趋势）、`edu_equity_urban_rural_region`（城乡或区域均衡）、`edu_teacher_finance`（教师/财政）、`edu_double_reduction_after_school_burden`（双减/课后服务/负担）、`edu_national_next_plan`（国家下一步计划）。不得把双减泛化并入“质量”，也不得用一个 requested_dimensions 空数组通过；只有用户明确排除某维时才标 `not_applicable`并保存理由。
  3. LLM 失败时使用 profile deterministic fallback；不得回退成“原问题一个维度”。
  4. 将 dimension id 写入 branch work item、query、candidate/evidence/analysis/audit/progress 全链路。
- 验证：中文/英文/paraphrase/generic/technology fixture；SC-EDU-01 精确断言上述六个 dimension ids、core importance、source requirements 和初始 status，不只断言数量/非空；无 profile 也能得到通用 Who/What/State/Drivers/Outlook 模板。
- 依赖：Task 1、2。

### Task 4 — 缺口驱动 query 与中文相关性修复 `[AC-QUERY-01, AC-QUERY-02, AC-SG-01, AC-SG-02, AC-SG-03, AC-SG-04]`

- 改动文件：v5 policy/nodes；`retrieval/ranking.py` 新 scoring entry；固定新建 `retrieval/query_terms.py` 承载中英文 query term/tokenizer，避免继续把语言规则塞进 workflow node；Search Gateway contract observation 扩展。
- 现状：v3 expand 使用原问题 + official；连续中文会成为单个长 token；Gateway ranking 不知道 dimension。
- 修改方式：
  1. 每个 core dimension 至少生成四个必备 query family：`general`、`official`、`temporal_statistics`、`comparison`；comparison 按维度选择城乡/区域、时间趋势、政策阶段或同类对象对照，不能用 independent-analysis 代替。`independent_analysis` 可作为第五种 supporting family。query strategy 严格输出 `dimension_id/query_family/comparison_axis/source_target/query/freshness_window`，fixture 逐维断言四类齐全。
  2. 中文使用字符 n-gram + 领域词典/数字实体保留，英文保持 token；给 `rank_candidates` 新的可选 `query_terms/dimension_id` 输入，旧调用保持现状。
  3. 从当前 public `SearchGateway.search()` 抽出 `_execute_query(request, budget_lease, allocator)` 内核：它不再创建预算，只执行 cache/routing/provider/rescue/rank/hydrate；public quick `search()` 继续按现有配置创建一个 `SingleQueryBudgetAllocator` 后调用内核，外部返回契约不变。新 `search_dimension_batch()` 创建唯一 `DimensionBatchBudgetAllocator` 和父 deadline，再按 core round-robin 调同一内核，禁止外围循环 public `search()` 产生多份预算。
  4. 父 allocator 原子管理 `query_slots/provider_call_slots/result_slots/cdp_slots/hydrate_slots`；每个 core dimension 先 reserve 一轮 query 和 `min_results_per_core`，剩余配额才按优先级分配。精确 hook 固定为：cache lookup 命中时在返回前 commit query/result slots但不领 provider slot；miss 后 `_run_batch` 可创建并发 task，但不能预扣；`_run_provider_in_slot` 等 limiter、重新验证 availability/circuit permit 后、紧邻 `provider.search()` 前原子 `claim_provider_attempt(dimension, provider, generation)`，claim 失败返回 budget_skip且不出站。进入 transport 即把 attempt 标 `upstream_started`，之后 cancel/timeout不退款；未进入 transport 的 queue timeout/cancel 释放 reservation。provider 返回后在 allocator 同一 lock 下按该维剩余 result slots 截断 rows、commit result count/attempt outcome；hydrate/CDP 在各自真实调用前领取父 slot。rescue 走同一 claim path，并额外受每 query 一次 guard。最终 `ProviderAttempt.upstream_called` 必须与 allocator `upstream_started` 等价，coverage 等式校验不通过则整个 batch fail closed。
  5. 首轮 query 每维有硬上限；rescue 只从 uncovered/partial 维度创建 work item，并排除已执行 query fingerprint/source family。v5 rerank 再在 passage 层执行同样的 core-first 分配，形成 Gateway candidate 与 report passage 两道保障。
  6. 只在确定性策略无增益且 LLM ledger 可用时调用 `query_strategy` role；作用域固定为每个 run/operation 最多一次，durable ledger 保存 `query_strategy_effect_id/used_at/target_dimension_ids`，重启和重复 gap 直接拒绝第二次。continue child 是新 operation，可获得自己的单次机会。
- 验证：教育样本相关性不再全 0；固定总 candidate budget 下每个 core dimension 都先拿到 reserved slots；高产维度不能饿死双减维度；query 去重/时点/官方来源优先；Search Gateway provider limiter/circuit/cooldown 现有测试全绿；quick search 旧输出不变。
- 依赖：Task 2、3。

### Task 5 — Source family、证据准入与维度覆盖矩阵 `[AC-EVID-01, AC-EVID-02, AC-EVID-03, AC-INTENT-02]`

- 改动文件：v5 contracts/policy/nodes；`retrieval/contracts.py` 可选 metadata；新 quality tests。
- 现状：URL/content hash 去重存在，但百度百科与教育部原文可同时竞争，rerank 不知道“官方原文替代”关系。
- 修改方式：
  1. canonical URL/content hash 之上新增 source-family matcher（document title/issuing body/document number/date/normalized canonical target）；输出 `exact/strong/weak/none`、matched fields 与 reason codes。exact（同 canonical/content hash）和 strong（文号+机关，或规范化标题+日期+机关）才能折叠覆盖/独立来源；weak 仅提示可能转载，不消除独立来源，进入审计。
  2. 同一政策/公报存在官方原文时，secondary 只可作为独立解读，不能替代原文的事实 citation；CAPTCHA、登录、安全验证、app shell、低正文、过期镜像进 invalid evidence bucket。
  3. 新建并锁 hash 的 `EvidenceAdmissionPolicyV1`；判定顺序固定为 page invalid gate → passage admission → source-family folding → per-dimension coverage，不能由 runtime config 自由改语义。passage admission 必须同时满足：`invalid_reason=none`、正文规范化后 ≥300 字符、dimension relevance ≥0.55、非导航/搜索导流/登录墙/安全验证、存在可回指正文 span；否则只进 rejected bucket。`weak` family 不折叠，`exact/strong` 才视为同 family。
  4. applicable core dimension 的状态表固定为：`uncovered` = 没有 admitted passage；`partially_covered` = 至少 1 条 admitted passage，但未满足 covered 全部条件；`covered` = 至少 2 条 admitted passages、来自至少 2 个 strong-distinct source families、winning passage relevance ≥0.65、该维 fetched candidates 的 exact/strong-family duplicate ratio ≤0.50、invalid-page ratio ≤0.40，并且 `requires_first_party=true` 时至少 1 个 winning family 为第一方。任一条件不满足只能 partial。supporting dimension 使用相同 admission gate，但 covered 只要求 1 个 strong family；它不参与业务三态的 core 门。
  5. profile 只可静态声明 core/supporting、importance、`requires_first_party` 与 recency window；V1 不允许降低 relevance、family、duplicate 或 invalid 阈值。`policy_education` 的六个固定 core 中，现状、规模趋势、教师财政、双减、国家下一步计划均要求第一方；城乡/区域均衡要求至少一方为第一方统计，若只有可信独立研究则保持 partial。generic/technology 由 dimension source expectation 确定第一方要求，不能在 run 中临时放宽。
  6. 每维记录 winning evidence ids、admission/rejection reason、family ids 和 coverage transition；不把全部引用数算覆盖。策略 schema、常量、profile 声明与 hash 一起进入 manifest/golden，阈值变化必须新增 policy version。
- 验证：百度百科/教育部替代 fixture、同文转载去重、CAPTCHA 拒绝、跨维 evidence 不串；对 0/1/2 passages、1/2 families、0.54/0.55/0.64/0.65 relevance、0.40/0.41 invalid、0.50/0.51 duplicate、第一方有/无的逐边界表驱动与 property tests。
- 依赖：Task 3、4。

### Task 6 — 固定 Playwright/Chromium 并完成两层离线打包 `[AC-PW-01, AC-PW-02, AC-PW-05]`

- 改动文件：`backend/pyproject.toml`/lock、`deskpet-backend.spec`、Tauri resources、构建脚本、licenses/notices、diagnostic command/tests。
- 现状：Playwright 是 browser-use 间接依赖；browser 未安装/收集；正式包依赖系统 Edge fallback。
- 修改方式：
  1. 采用 Task 0 在 Windows 11 通过的精确 `playwright==X.Y.Z` 与对应 Chromium revision；构建脚本设置 isolated `PLAYWRIGHT_BROWSERS_PATH`，只装 Chromium/new-headless，并校验 `browsers.json`/executable hash。
  2. 最终资源只由 PyInstaller onedir 的 `backend/playwright-browsers/` 拥有；Tauri resources 只整体搬运 frozen backend 目录，不再单独复制 Chromium。`BrowserBundleResolver` 优先级固定为显式 test override → PyInstaller `_MEIPASS`/onedir sibling → Tauri sidecar resource root → dev build cache；发现多个不同 revision 时 fail closed，不择一猜测。Updater 以整个 versioned browser root 原子替换，卸载删除同一 owner tree。
  3. runtime diagnostic 输出 package version、expected/actual revision、driver/browser path、hash、launch status，不输出用户路径敏感片段；mismatch/缺文件构建失败。
  4. 收集 Playwright/Chromium notices；更新/卸载覆盖同一 resource tree。
- 验证：当前 Windows 11 主机断网隔离安装/首启/render；无 `%LOCALAPPDATA%/ms-playwright` 依赖；Process Monitor/网络日志无 CDN；记录体积和 updater delta。Windows 10 留待后续认证。
- 依赖：Task 0 通过。

### Task 7 — Retrieval-owned PlaywrightRendererPool 与 Fetch fallback `[AC-PW-03, AC-PW-04, AC-PW-05, AC-PW-06, AC-EVID-03]`

- 改动文件：新 `playwright_renderer.py`；`fetch_extract.py`、`retrieval/runtime.py/contracts.py`、backend lifecycle/shutdown 接线、fixture server/tests。
- 现状：static → Edge CDP → Jina；只有一个 `claim_cdp()` quota；Edge 依赖系统安装。
- 修改方式：
  1. `BrowserBundleResolver` 解析 dev/frozen/Tauri paths；`PlaywrightRendererPool` 使用一个可重建 browser，async semaphore=2，每任务独立 incognito BrowserContext，默认禁持久 cookie/cache/service worker/download。
  2. 只读 action enum：goto、wait_for_body、bounded_scroll、expand_read_more、next_page；域跳转白名单同 origin/allowlisted canonical，最多动作/页数/时间固定；拦截 download、upload、form submit、login 和非 GET/HEAD 导航。`expand_read_more` 仅匹配 button/link 的 accessible name 精确落入 versioned locale allowlist（如“展开/阅读全文/显示更多/read more/show more”），且不在 form 内；`next_page` 只允许 `rel=next` 或 pagination landmark 中精确“下一页/next”控件。不得接受任意 CSS selector、LLM selector 或模糊页面文本。分页保持 root canonical URL，按 `(page_index, final_url, content_hash)` 记录 page part；正文按 page_index 合并并做段落 hash 去重，EvidencePassage 保存 `page_part_id + DOM/text offset`，citation 仍指 root source并可反查实际 page URL/anchor。
  3. `FetchRenderBudget` 分开 static/playwright/edge attempts；v5 顺序严格为 static → Playwright → Edge → 明确失败，Edge 后禁止 Jina。现有 Jina 只保留给显式旧调用/v1～v4兼容，v5 FetchRequest 固定 `allow_jina=false`，不能被 config 静默翻开。Playwright crash 可重建一次；每层结果/耗时/quality code 可审计。
  4. finally 关闭 page/context；shutdown 关闭 browser/driver；cancel 不转换成 provider failure。渲染后仍走统一 content quality/CAPTCHA gate。
- 验证：静态页不启动 browser；动态页成功；expand/分页；跨域/提交拒绝；CAPTCHA 拒绝；timeout/cancel/crash/rebuild；并发≤2；backend exit 无 orphan Chromium/node。
- 依赖：Task 5、6。

### Task 8 — Checkpointed adaptive rescue、软检查点与 plateau `[AC-SG-05, AC-SG-06, AC-DELIVERY-02, AC-UI-01]`

- 改动文件：v5 graph/nodes/policy/contracts；native workflow conditional-edge fixtures；progress template。
- 现状：v4 technology 强制 60～300 秒 hard deadline；v3 gap 是单 handler；无法在长循环中 durable 续租或即时收敛。
- 修改方式：
  1. `gap_evaluate` 是纯函数：读取 coverage/ledger/control fence，生成最多一个 `GapWorkItem` 或 route=synth；300 秒是首次 soft checkpoint，之后每次最多 120 秒 lease，累计自动 active time 上限 900 秒。
  2. `gap_work` 一次只执行一个有界 query/source target/search/fetch unit；完成后 checkpoint；`gap_join` 合并 evidence、重新算 coverage/first-party/evidence-readiness gain 并更新 plateau count。
  3. synthesis 前不使用 report quality。新增并锁 hash 的 `EvidenceReadinessV1`：50×加权 coverage ratio（covered=1/partial=.5/uncovered=0） + 20×满足 first-party requirement 的 core 权重比 + 15×每个 covered/partial core 至少两个 strong-distinct source family 的权重比 + 15×admitted passages / fetched non-duplicate passages（cap 1）；任一比例分母为 0 时该比例固定为 0，最终用 Decimal ROUND_HALF_UP 到0.1。pre-synth gain 固定为新增 covered dimension、首次满足某维 first-party requirement或 readiness 严格提高；完成首轮 synth/audit 后才可把 `ReportQualityRubricV1` score/hard-failure 集合改善作为 repair gain。连续两轮对应 gain=0 停止。
  4. `EvidenceAdmissionPolicyV1` 产出唯一 synthesis route，优先级固定且边界包含等号：`full_synthesis` = 所有 applicable core 均 covered 且 readiness ≥80.0；`partial_synthesis` = 不满足 full，但 covered/partial 的 core importance 权重比 ≥0.50、readiness ≥35.0，且每个进入综合的维度至少有 1 个带正文 span 的 supported direct-answer candidate；其余为 `insufficient_summary`，不得调用完整 report synthesis，只生成确定性证据缺口/已确认事实摘要。`generate_now`、900 秒 cap 或 plateau 只能提前触发这张表，不能绕过或提升 route；quality audit 最终仍可把 full/partial synthesis 降级为 partial/insufficient，不能反向绕过 evidence route 升级。
  5. `generate_now` fence 优先级高于续租；恢复时从最新 ledger/checkpoint 继续，不重跑已 commit work item。
  6. v5 graph manifest 明确两个 cycle：`gap_join -> gap_evaluate` 绑定 `loop_budgets[gap_work_iterations]=64`，counter 只由 `gap_join` 在成功 commit/合并一个 work item 后 `+1`；`repair_join -> quality_audit` 绑定 `loop_budgets[repair_iterations]=16`，counter 只由 `repair_join` 在一次 repair outcome commit 后 `+1`。动态时间/质量/plateau 通常先退出；静态 counter 只防损坏状态无限循环，不替代 300/120/900 语义。
  7. manifest 预留 `max_supersteps=384`、`recursion_limit=512`（严格大于 max），覆盖 baseline nodes + 最坏 64 个三节点 gap cycle + 16 个三节点 repair cycle；compile test 计算实际最长路径并断言不先于 loop budget。若实现节点数变化，构建脚本从 definition 重新计算最小需求并要求显式更新 manifest/hash，不能运行时临时放宽。
- 验证：definition compile、edge-to-budget binding、counter single writer/每轮恰增1、fake clock跨重启、静态 loop exhaustion、max-superstep precedence；0/35.0/79.9/80.0 readiness、0.49/0.50 core weight、all-covered/one-partial 的 route table；SC-LEASE/PLATEAU/CAP/NOW；幂等 item；900 秒不是单 handler sleep；cancel/settle；无额外无角色 LLM。
- 依赖：Task 1～5、7。

### Task 9 — 维度平衡 rerank 与 per-dimension analysis `[AC-EVID-01, AC-EVID-02, AC-REPORT-01, AC-REPORT-03, AC-REPORT-04]`

- 改动文件：v5 policy/nodes；复用 v3 passage/support helpers 的只读稳定接口；analysis fixtures。
- 现状：v3 rerank 先域多样性再 URL 多样性；synth 直接生成事实 claims，分析为“无”。
- 修改方式：
  1. rerank 先为每个 core dimension/source-family 选 winning passage，再按 authority/recency/relevance/depth 补余量；不让高产维度挤掉缺口维度。
  2. `dimension_analysis` role LLM 每次只处理一个或小批维度，结构化输出 `finding/current_state/driver_or_change/impact/uncertainty/counterevidence/citation_ids`；validator 保留被 passage 支持的事实，推断与事实分开。
  3. generic、education、technology 共用 universal analysis contract；profile 只增加排序和字段要求。
  4. 对缺证维度输出 explicit gap，不生成空洞政策口号或文件发布日期充结论。
- 验证：教育 fixture 覆盖现状/矛盾/政策/影响；维度平衡 property；unsupported/跨 passage 数字继续 fail closed；v3 support tests 不变。
- 依赖：Task 5、8。

### Task 10 — 专业 report renderer、六维质量门与 checkpointed repair `[AC-REPORT-01, AC-REPORT-02, AC-REPORT-03, AC-REPORT-04, AC-REPORT-05, AC-REPORT-06, AC-DELIVERY-04, AC-DELIVERY-05]`

- 改动文件：新 v5 report/policy/nodes；`scripts/acceptance/deepresearch_report_quality_v5.py`；golden/rubric fixtures。
- 现状：generic v3 renderer 是事实 bullet + Coverage JSON；候选评分偏支持事实/引用/域/字节。
- 修改方式：
  1. renderer 固定为：结论先行 3～7 条 → 分维度现状/原因/影响/下一步 → 风险与不确定性 → 来源方法；机器 audit/coverage 保存独立 JSON artifact，不进入普通报告。
  2. 新建并锁 hash 的 `ReportQualityRubricV1`，输入只来自 validated ResearchBrief/DimensionCoverage/DimensionAnalysis/claims/source families/render lint；所有比例按 core importance weight 计算，空分母为 0（仅 `not_applicable` 被排除），每项 clamp 后 `Decimal` ROUND_HALF_UP 到 0.1，最后求和再到 0.1：
     - 相关性 25 = 15×“有 direct_answer 且 dimension relevance≥0.65 的 applicable core 权重比” + 10×“首屏 key judgment 映射到用户 intent token/entity 且绑定 core dimension 的比例”。
     - 覆盖度 25 = 20×加权 coverage ratio（covered=1、partial=0.5、uncovered=0） + 5×“有结论与 winning evidence 双绑定的 applicable core 权重比”。
     - 来源质量 20 = 10×“满足该维第一方要求的 core 权重比” + 5×“每个 covered core 至少两个 strong-distinct source family 的权重比” + 5×“winning sources 全部非 invalid/aggregator replacement 的比例”。
     - 综合推理 15 = 各 5×“covered/partial core 分析中具有 current_state、driver/change、impact 的权重比”；字段必须含 supported fact refs，推断另标 inference。
     - 时效/不确定性 10 = 5×“有时间要求的 core 中 winning evidence 落窗或明确无新证据的权重比” + 5×“forecast/partial/冲突维度明确 uncertainty/counterevidence 的权重比”；完全无这类维度时后一项记满。
     - 可读性 5 = 2 分结构必备章节且顺序正确 + 2 分首屏 3～7 条、无元数据伪结论、段落长度合规 + 1 分无 Coverage/raw diagnostics/重复标题/悬空引用/明显成文错误；每个子项是固定布尔 lint。
  3. hard failures 在总分前判定且不可被高分抵消：任一 applicable core uncovered（completed）、unsupported key claim、invalid page/captcha citation、官方原文已存在却由 secondary 替代主引用、citation dimension mismatch、内部诊断泄漏。completed=`score>=80 + all applicable core covered + no hard failure`；partial=`score>=60 + supported key claims + covered_core>=max(1,ceil(applicable_core*0.5)) + no invalid/unsupported hard failure`；否则 insufficient。
  4. `quality_audit` 先运行 deterministic checks，再允许 `quality_audit` role LLM返回 atomic failure candidates；validator 只接收可定位 section/dimension/rule。
  5. 每个 `repair_work` 只做一种定向动作：补一个维度分析、替换次级引用、收缩 unsupported claim、补不确定性、重写可读性；`repair_join` 重算分数。预算耗尽或连续两轮无 hard-failure 集合缩小且 score 未严格提高即停止。
  6. 原始证据和事实不可被 repair 新增；引用重新走 passage support。最终 Markdown 做 citation mapping、重复/空标题/双句号/内部诊断泄漏检查。rubric schema+公式+decision table+golden expected scores 写入 fixture，policy manifest 记录 rubric hash；hash 变化必须新版本/显式 fixture review。
- 验证：SC-EDU/AI golden + adversarial bad report；固定失败样本必须不再 completed；逐分项公式边界、舍入、空分母、not_applicable、hard failure precedence；mutation tests（堆引用/正文/重复来源不能抬过门）；人工专业可读性 checklist。
- 依赖：Task 2、3、5、9。

### Task 11 — workflow.db v3、版本化终态投影、run control 与 snapshot lineage `[AC-DELIVERY-01, AC-DELIVERY-04, AC-DELIVERY-06, AC-UI-03, AC-UI-04, AC-COMPAT-01]`

- 改动文件：v5 nodes/contracts；`workflows/native.py`、新 `terminal_projection.py`、`launcher/service/ipc`、`store/schema.py`、`store/run_store.py`；`main.py`；product delivery；workflow DB migrations/tests。
- 现状：v4 失败只支持 `retry_from_start`；launcher/service 硬编码 failed v4；没有 generate now 或证据续研。
- 修改方式：
  1. 新建 `TerminalProjectionRegistry`，以 `(workflow_name, workflow_version, capability)` 注册 validator/projector，并注入 `NativeWorkflowExecutable`；v4 继续使用字节等价的现有 terminal_public validator，v5 validator 接受 delivery status、quality/coverage summary 与 action matrix。`native.py` 不再硬编码 “reserved for v4”，但未知 schema/version 仍 fail closed。
  2. engine `terminal_status=completed` 表示图正常收敛；`terminal_public.delivery_status` 表示 completed/partial/insufficient。`workflow.final.status` 保持 engine status，新增 `delivery_status` 给 UI；product receipt/report/artifact 只依据 validated delivery status。内部 error/cancel 不伪造成业务 insufficient。
  3. 将 action 支持改为版本化矩阵：v4 保持 failed/retry_from_start；v5 支持 `generate_now`（仅 `ResearchBrief` 与 dimensions checkpoint 已 committed 且尚未 terminal 的 running run）、`continue_research`（partial/insufficient terminal）、`retry_from_start`（failed）。brief checkpoint 前 terminal_public 不投影 generate capability，UI 不显示按钮；brief 已提交但零 admitted evidence 时 generate_now 确定性进入 `insufficient_summary`。未知 version/action fail closed。
  4. 使用 Task 2 已原子创建的 workflow.db v3 control/snapshot/pin/lineage 表骨架；本任务只实现 repository、事务、状态机和 retention API，不再拥有 schema version 或补做 `_migrate_v2_to_v3()`。继续覆盖 v1/v2 fixture、future-version拒绝和中断迁移重启测试。
  5. 新建 run control 状态机 `open -> accepted -> observed -> settled -> consumed`，另有 `rejected/expired`；service 接收 `generate_now` 时用 run-head/terminal/brief-committed CAS：只有已提交 ResearchBrief/dimensions 且 non-terminal 的 v5 run 可 accepted，并写 `settle_deadline=accepted_at+30s`。gap/search/fetch/analysis/synthesis/audit/repair 在每个原子 effect 前和完成后读取 accepted command，observed 后禁止新 upstream，settled 后条件边依据最后 committed evidence/candidate 进入 evidence route 或确定性 insufficient summary；finalize consumed。brief 未提交、cancel 或 terminal 获胜则 rejected/consumed，重启按表恢复。
  6. 若 30 秒内未进入 synth/quality/delivery checkpoint，terminal progress 投影当前收敛阶段、超时原因和 `cancel_settle` capability。`cancel_settle` 是 v5 版本化 control action，只在同 run 有 accepted/observed generate_now 且仍 nonterminal 时可用；同样用 run-head/idempotency CAS。`ResearchCallEffectPort` 以 in-process `WorkflowControlSignalHub` + durable command polling 将所有在途 upstream effect（search provider、static fetch、Playwright/Edge，以及六种 LLM role：modeling、query_strategy、dimension_analysis、report_synthesis、quality_audit、targeted_repair）与 cancel signal race；也即所有 `ResearchCallEffectPort` effect 无条件受同一 fence。取消前未出站则 release reservation；已出站则 cancel task、effect/reservation 记 `held_uncertain`，拒绝 late outcome 写入当前 run，node 以 `settle_cancelled` checkpoint 后按 `EvidenceAdmissionPolicyV1` route 到 synth/insufficient summary。重启时先读 durable command，不重启被取消 effect。该动作不取消整个 workflow，只强制用最后已 commit 证据完成三态交付；同一 fence/late-outcome 规则对 LLM 和浏览器完全一致。
  7. 定义 content-addressed `ResearchEvidenceSnapshot` manifest：dimension state、winning EvidencePassage blob refs、source-family records、query fingerprints、budget summary 和 schema/hash；正文只存 RegisteredBlobStore refs。partial/insufficient finalize 阶段就 materialize snapshot、创建 parent pin并设置 `continue_until=ended_at+terminal_retention`，不是等用户点击才生成；terminal_public 只有在 snapshot 已 pin 且未过期时才投影 continue capability，过期后 UI 恢复不显示按钮。
  8. `WorkflowRunStore.create_child_from_snapshot()` 在一个 workflow.db 事务中验证父 delivery status/continue_until、pin snapshot、创建 child run/lineage/start payload；事务失败的未引用 blob 由 GC 清理。child initial state读取 snapshot并获得新 lease；父 report/artifact/receipt 永不更新。snapshot retention 覆盖所有 reachable child 和 terminal retention，不依赖 Gateway cache。
  9. 重构 `WorkflowRetentionManager` 为 lineage-aware reachability 的唯一 owner：在现有 ordered cleanup 前新增 expired-control 与 lineage-reachability 阶段；保护 nonterminal/open control/undelivered run、未过期 continue parent、所有 protected child 的 ancestors 和 snapshot pins。确定性清理顺序为过期/已消费 command → 计算 protected graph → deliveries/events → tombstone/checkpoint/effects → child/parent lineage + snapshot pins → snapshot manifest/blob refs → unreachable run → orphan blob。每轮从 pins 重算 refcount/可达性，不信任可漂移缓存计数；parent deletion、child deletion、continue expiry、orphan pin、missing blob 都有 dry-run/report/reconcile tests。非 research run 的现有阶段与结果保持兼容。
  10. delivery rules：completed/partial 保存用户报告；insufficient 只保存安全 summary + snapshot/checkpoint，不创建伪完整 Artifact。每个 run 只允许一次 terminal/report/artifact/final assistant。
- 验证：schema 0/1/2→3 migration、rollback/idempotency；retention reachability/dry-run/reconcile；terminal registry v4 byte compatibility；business/engine truth table；brief 前 capability 不可见/命令被拒绝、brief 后零证据 insufficient、restart race、double-click、run-head CAS、accepted-but-not-observed restart、30秒 watchdog、六种 LLM role 与 search/browser 的 cancel-settle before/after upstream、audit/repair late outcome fence、terminal/cancel race、old v4 retry、snapshot pin/expiry/GC/parent deletion/child atomic creation、lineage immutability、delivery cardinality、SC-NOW/CONTINUE。
- 依赖：Task 1、8、10。

### Task 12 — 后端 v5 progress schema 与默认折叠逐步气泡 `[AC-UI-01, AC-UI-02, AC-UI-03, AC-UI-04]`

- 改动文件：`workflows/progress.py`、v5 graph/node stage projection、workflow delivery/message allowlist、tests。
- 现状：已有 action/result/result_code/diagnostic_codes 和隐藏 child bubbles，但没有维度/质量/lease/control字段。
- 修改方式：
  1. 新增 capability `deep_research_progress_v5` 与严格安全 payload：dimension counts/status changes、valid/first-party sources、active gap item、elapsed/checkpoint/lease reason、quality score/hard failures、predicted delivery、control action/status、parent operation；每个阶段摘要固定为 `action/result/discarded/remaining_gap/next_step` 五字段，均为服务端生成的枚举/计数安全投影，无内容时传枚举 `none`，不得省略字段。
  2. 每个 committed node/child operation 发一条 stable completed bubble；`stage_instance_id` 必须从 `NodeExecutionIdentity.activation_id/invocation_key`（含 loop iteration/work item identity）派生，不能只用 node id。started/waiting/failed/terminal summary 与 child reducer 保持幂等。
  3. 白名单限制字符串长度/枚举/计数；不传 query、URL、正文、prompt、token cost明细或 secret。token 只显示 budget 使用比例。
- 验证：schema/allowlist/reconnect/out-of-order/terminal-late/historical v4 fixtures；每一步完成恰一条 durable bubble。
- 依赖：Task 1、8、10、11。

### Task 13 — 前端总体覆盖/质量进度与三种操作 `[AC-UI-01, AC-UI-02, AC-UI-03, AC-UI-04, AC-COMPAT-01]`

- 改动文件：`types/messages.ts`、`sessionsStore.ts`、`WorkflowProgressGroup.tsx`、`MessageStreamPanel.tsx`、`MessagePanelRoot.tsx`、CSS 与 tests。
- 现状：默认一张聚合卡，展开阶段详情；retry 类型只允许 `retry_from_start`。
- 修改方式：
  1. store 按 workflow/version/capability 解析 v5 字段；stable key=`run:stage_instance`，历史 v4 不推断维度。
  2. compact card 始终显示：覆盖维度/总数、有效/第一方来源、当前缺口工作、质量分/硬失败、已用时/软检查点/续租原因、预计终态。
  3. 每步 child bubble 默认折叠；严格按 `action/result/discarded/remaining_gap/next_step` 显示“做了什么/得到什么/舍弃什么/仍缺什么/下一步”，值为 `none` 时显示简短“无”，不直接展示 raw diagnostics。
  4. running 仅在后端投影 `generate_now` capability（brief/dimensions 已 committed）后显示“立即用现有证据生成”，建模早期不猜测能力也不显示按钮；accepted 后显示当前收敛阶段和 30 秒倒计时，超时显示“停止当前收敛并生成”（cancel_settle）；partial/insufficient 显示“继续补充调研”；failed 显示“从头重试”。按钮有 pending/accepted/observed/settle_timeout/error、禁重复、Enter/Space、`aria-expanded/controls`、live region。
- 验证：vitest reducer/component/a11y；重连/重启/多 run/历史 v4；Xiaomi 实机截图能一眼看到每步结果和总体缺口。
- 依赖：Task 12。

### Task 14 — 默认接线、配置 backfill 与完整兼容 `[AC-SG-01, AC-PW-03, AC-DELIVERY-02, AC-DELIVERY-03, AC-COMPAT-01]`

- 改动文件：`backend/config.py`、`config.toml`、`main.py`、bootstrap/launcher、config tests。
- 现状：默认 v4；v4 technology runtime 强制 300 秒；所有 version 共用 v2 context factory。
- 修改方式：
  1. v5 完整通过自动化后，把 `deep_research_version` dataclass/config/backfill/default allowlist 改为 v5；注册独立 v5 context/state factory，注入 LLM V2、Search Gateway、Fetch+Playwright、artifact/blob/native policy/control ports。
  2. 新 `[research_v5]` 配置只承载运行预算：soft_checkpoint=300、lease=120、auto_cap=900、plateau=2、LLM token/cost budget；parser clamp 防非法值。`EvidenceAdmissionPolicyV1`、`EvidenceReadinessV1`、`ReportQualityRubricV1` 的阈值/公式属于带 hash 的版本化产品协议，不开放 runtime config，也不能被旧配置 backfill 改写。旧 v4 config 保持只读兼容。
  3. `[search_gateway] enabled=true` 和 Playwright renderer 默认 ON；实际 SearXNG endpoint 仍可空。browser-use flag 不联动。
- 验证：config backfill/idempotency、default wiring、flag ON、v4 explicit override、old persisted run recovery、context port identity。
- 依赖：Task 1～13 均通过聚焦门。

### Task 15 — 自动化、质量 benchmark、冻结包与真机 E2E 总门 `[全部 AC]`

- 改动文件：`backend/tests/test_workflow_deep_research_v5*.py`、retrieval/packaging tests、前端 tests、`scripts/acceptance/*v5*`、`testcase/2026-07-16-deepresearch-playwright-quality/`、evidence/results。
- 修改方式：
  1. 单元/属性：contracts、中文 relevance、source family、coverage、budget/LLM ledger、quality rubric、delivery/action/lineage、Playwright lifecycle。
  2. 集成：真实 Search Gateway → static/Playwright/Edge → evidence → v5 graph → quality gate → delivery；故障注入 timeout/cooldown/CAPTCHA/browser crash/cancel/restart。
  3. 固定 benchmark：SC-EDU-01、SC-AI-01、SC-PW/BLOCK/RESCUE/LEASE/PLATEAU/CAP/NOW/CONTINUE；保存机器 audit 和人工 review，不只看 pass 数。
  4. 全量 pytest/vitest/tsc/build/cargo/NSIS；与 Task 0 baseline 对比，不接受新增已知失败。
  5. 当前 Windows 11 主机使用隔离安装目录与用户数据完成断网安装/更新/卸载；验证 bundle、版本、动态页、无 orphan。
  6. 按 AGENTS.md 在 Xiaomi 屏幕启动 Tauri，真人输入教育和 AI 原问题；testcase 逐项绑定 AC-UI-01～04，包含运行中总体卡、逐步气泡展开、重复 generate-now、accepted-but-not-observed 后强杀重启、终态 continue、WebSocket重连/历史加载；每 case 真坐标动作前声明并保存截图 + backend/workflow DB/log，验证进度、控制动作和报告专业性。
  7. 全部通过后同一次交付更新 `ARCHITECTURE/DeepResearch.md`（v5 模块生产链路/三态/质量边界）、`ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`（Gateway/Playwright/进度链路）、`ARCHITECTURE/ARCHITECTURE.md`（全局 long-running workflow 版本摘要）与 `ARCHITECTURE/PROJECT_STATUS.md`（模块状态/里程碑/证据）顶部日期；同步本 plan results、`testcase/index.md` 与 testcase 结果，`STATUS/` 不写正文。
- 验证：DoD 全部证据归档；任何 Windows 11 安装验证、真实 UI、报告人工 review 或 architecture 回写缺失都不算完成。Windows 10 认证明确不属于本轮 DoD。
- 依赖：Task 14。

## 6. 分阶段门禁与并行边界

| 阶段 | 可并行工作 | 阶段出口 |
|---|---|---|
| Gate A 契约/可行性 | Task 0 bundle spike；Task 1 contracts；Task 2 usage adapter | Windows 11 可行版本锁定；v5 schema/usage tests 绿 |
| Gate B 研究质量内核 | Task 3/4（建模/query）与 Task 5（source family）在 contracts 冻结后分文件并行 | SC-EDU fixture 能形成可信维度矩阵和定向证据 |
| Gate C 浏览器/循环 | Task 6 packaging 与 Task 8 graph loop 可并行；Task 7 待 Task 6 artifact | frozen dynamic page + checkpoint/lease tests 绿 |
| Gate D 报告/控制 | Task 9/10 串行；Task 11 在 delivery contract 冻结后 | 三态、generate now、continue、quality gate 集成绿 |
| Gate E UI/默认接线 | Task 12 后 Task 13；Task 14 最后翻默认 v5 | 历史 v4 + v5 前后端契约全绿 |
| Gate F 发布验收 | Task 15 | 全量、Windows 11 隔离安装、Xiaomi 真机、人工报告 review、ARCHITECTURE 回写 |

同一文件不得多代理并行改。`deep_research_v5_nodes.py`、`main.py`、`sessionsStore.ts`、`WorkflowProgressGroup.tsx` 是串行 owner 文件；其他任务以 contracts 冻结后的独立文件为并行单位。

## 7. AC 逐条追溯矩阵

下表是实现阶段的强制验收索引；每条 AC 都有唯一实现 owner 和可落盘的证据类型，不能用组级“整体已覆盖”代替逐条判定。

| AC | 实现任务 | 必须产出的验证证据 |
|---|---|---|
| AC-INTENT-01 | T1, T3 | `ResearchBrief` contract/fixture 单测；SC-EDU trace 中六个固定 `dimension_id` |
| AC-INTENT-02 | T1, T5, T10 | coverage 状态机属性测试；核心维度未披露缺口时 report gate 失败测试 |
| AC-QUERY-01 | T3, T4 | 四类 query family fixture；中文分词、教育部/政府定向查询单测 |
| AC-QUERY-02 | T4, T8 | rescue trace 集成测试，证明只消费未覆盖维度且记录覆盖 delta |
| AC-SG-01 | T4, T9 | 父级公平预算 + dimension-aware rerank 固定 passage 预算测试 |
| AC-SG-02 | T4 | 中文相关/无关候选严格排序单测与 SC-EDU 非零相关性断言 |
| AC-SG-03 | T5, T9 | 官方原文替代次级来源 fixture 与 source-selection reason 快照 |
| AC-SG-04 | T5 | 同源公报官网/转载/镜像 family 去重、覆盖不重复计分属性测试 |
| AC-SG-05 | T8 | 300 秒检查、120 秒续租、900 秒封顶、连续两轮无增益的虚拟时钟测试 |
| AC-SG-06 | T2, T4, T8 | deterministic-first、策略 LLM 触发门、role/usage/coverage delta ledger 测试 |
| AC-PW-01 | T0, T6 | Playwright 版本/revision/hash 一致性构建测试；不匹配时构建失败 |
| AC-PW-02 | T0, T6, T15 | frozen/NSIS 资源清单；Windows 11 断网隔离安装动态页渲染证据 |
| AC-PW-03 | T7 | 静态→bundled Chromium→Edge CDP→失败顺序及静态页不启动浏览器测试 |
| AC-PW-04 | T7, T15 | 并发上限、context 隔离、成功/失败/取消/重启清理测试与真机无 orphan 证据 |
| AC-PW-05 | T7 | Chromium/driver crash 注入、单次重启、已完成证据不回滚集成测试 |
| AC-PW-06 | T7 | 语义 locator allowlist、动作/域名/时限上限及禁止写操作测试 |
| AC-EVID-01 | T5, T7 | CAPTCHA/登录墙/导流/空壳/乱码 fixture 拒绝测试，含百度安全验证样本 |
| AC-EVID-02 | T5, T9 | EvidencePassage schema round-trip 与 claim→passage→URL 反查测试 |
| AC-EVID-03 | T5, T8 | EvidenceReadinessV1 边界测试；严重不足不进入 full synthesis |
| AC-REPORT-01 | T9, T10 | 三 profile golden contract；generic 不再完整委托 v3 报告的调用链测试 |
| AC-REPORT-02 | T10 | 3～7 条首屏判断 renderer/golden；日期、标题、口号不得充当结论的负例 |
| AC-REPORT-03 | T3, T9, T10 | policy_education 章节、事实/分析/未来推断分层 golden 与 SC-EDU 人工评审 |
| AC-REPORT-04 | T9, T10 | claim-support/citation-dimension 精确匹配测试；unsupported key claim 硬失败 |
| AC-REPORT-05 | T10 | ReportQualityRubricV1 公式、舍入、权重、阈值、硬失败与 rubric hash 快照 |
| AC-REPORT-06 | T10 | 用户 Markdown 不泄露内部诊断的 golden；trace 保留完整诊断的集成测试 |
| AC-DELIVERY-01 | T1, T10, T11 | engine terminal→delivery 三态投影表驱动测试；insufficient 不产伪完整报告 |
| AC-DELIVERY-02 | T8, T11 | 单调时钟、租约、在途收敛、安全 checkpoint 与恢复测试 |
| AC-DELIVERY-03 | T2 | LLMBudgetLedger 事务预留/结算/恢复/拒绝、provider usage 透传测试 |
| AC-DELIVERY-04 | T8, T11, T13 | `generate_now` 幂等、30 秒 settle/cancel、重连不重复报告及真点击证据 |
| AC-DELIVERY-05 | T10 | 缺陷定向章节修复、无新知识、两轮无增益停止的确定性集成测试 |
| AC-DELIVERY-06 | T11, T13 | lineage/snapshot/retention 测试；继续研究仅补缺口且原 run/report 不变 |
| AC-UI-01 | T8, T12, T13 | progress schema reducer 测试；折叠卡快照与 Xiaomi 真机持续进度证据 |
| AC-UI-02 | T12, T13 | 阶段气泡五字段投影、默认折叠、waiting/终态主流可见组件测试 |
| AC-UI-03 | T12, T13 | allowlist/redaction 测试，证明 raw query/URL/Cookie/token/stack 不进 UI |
| AC-UI-04 | T11, T12, T13 | app 重启/WebSocket 重连/历史加载/重复事件 reducer + 真机恢复证据 |
| AC-COMPAT-01 | T0, T1, T11, T13, T14, T15 | v1～v4 checkpoint/session/artifact/progress fixture；新 run v5 默认 ON；全量回归 |

## 8. DoD

- `acceptance.md` 36 条 AC 均有自动化或真机证据，不以“支持率 100%/引用够多”替代报告质量。
- 固定教育失败样本不再可能以原结构 `completed`；若证据够，给出清晰专业结论；不够则诚实 partial/insufficient 并说明缺口。
- 300 秒健康进展不被强杀；900 秒安全 checkpoint；没有固定 3 次 LLM 上限；所有生成调用受 usage ledger 和质量增益约束。
- Chromium 与匹配 Playwright 随安装包在 Windows 11 离线可用，无首次下载、无 orphan、验证码不入证据；Windows 10 认证延期。
- 进度卡无需展开即可看总体覆盖/质量/缺口/预算；每步完成有默认折叠气泡，展开可看动作和结果。
- v1～v4 历史恢复/消息/重试兼容；新功能默认 ON；全量测试无新增失败。
- Xiaomi 真机 E2E、最终 Markdown 人工专业 review、冻结/NSIS/干净机证据与 `ARCHITECTURE/` 回写全部完成。
