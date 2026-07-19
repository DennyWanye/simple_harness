# 架构基线：DeepResearch 质量闭环与内置 Playwright

> 校准日期：2026-07-16
> 范围修订：2026-07-16 经用户确认，本轮平台验收仅覆盖 Windows 11 x64；Windows 10 认证延期，不引入 Hyper-V/ISO 依赖。
> 代码基线：`0117ad764f593d018392332541d475a1d52a07ac` 加当前工作树中尚未提交的 DeepResearch v4、Search Gateway、workflow UI 改动
> 验收事实源：[`acceptance.md`](./acceptance.md)
> 全局事实源：[`../../ARCHITECTURE/index.md`](../../ARCHITECTURE/index.md)、[`../../ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`](../../ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md)
> 边界：本文只校准当前事实和目标改动边界，不代表功能已实现，也不改写 v1～v4 历史 checkpoint 语义。

## 1. 主要矛盾

当前系统的发布门验证的是“抽取出的原子事实能否被某个证据段落支持”，而用户需要的是“报告是否完整、直接、有结论地回答研究问题”。两者并不等价。

固定失败样本 `DeepResearch/帮我调研一下，现在中国小学现在的教育现状和国家下一步计划-1454d11734354b5fae10db8c565646e9.md` 以 16 条事实、11 个引用、8 个独立域、100% support rate 通过并发布，但正文没有教育现状的综合判断、核心矛盾、城乡/区域差异、教师与财政压力、政策优先级或下一步影响分析；反而把公报发布日期、国家周年、术语定义和政策口号作为核心结论。报告自己的 `分析与推断` 仍是“无”。这证明当前 `completed` 是一个可重复的质量误判，不是单次文风问题。

因此本轮计划不能只增加 Playwright，也不能只提高引用数。目标架构必须把“问题建模 → 核心维度 → 缺口驱动检索 → 证据准入 → 分维度分析 → 报告综合 → 质量审计/定向修复 → 三态交付”变成同一条可恢复质量闭环。

## 2. 当前生产调用链（解剖麻雀）

以“现在中国小学教育现状和国家下一步计划”为代表链路：

```text
聊天中的 deepresearch tool
  -> backend/main.py::_start_deepresearch_graph
     -> config.workflows.deep_research_version，默认 v4
     -> WorkflowLauncher.launch(deep_research/v4)
  -> v4 normalize
     -> classify_intent(topic)
     -> 非 technology_intelligence 归为 generic
  -> v4 plan
     -> generic 的问题建模/分支规划直接委托 v3 plan
  -> v3/v4 fixed branch fan-out
     -> expand -> SearchGateway.search -> direct -> FetchExtractService -> score
  -> gap -> rerank
  -> v4 rerank
     -> 所有 profile 共享第一方来源优先重选
  -> v4 synth
     -> generic 的内容综合直接委托 v3 synth
  -> v4 cite
     -> 先运行 v3 cite；generic 直接返回 v3 质量结果
     -> 所有 profile 共享 zero-candidate / insufficient-evidence 失败交付外壳
  -> persist -> finalize -> report/Artifact/final assistant
  -> workflow outbox/SessionDB
  -> sessionsStore -> WorkflowProgressGroup
```

代码证据：

- `backend/main.py:3675-3698`：新 run 默认选择 `v4`，并仍复用 `_deep_v2_context_factory` 注入端口。
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py:255-256`：v4 normalize 写入 `intent_profile`。
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py:246-256`：所有 profile 共享 structured synthesis、最低引用配置和 intent profile 外壳。
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py:281-282`：generic 的问题建模/plan 委托 v3。
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py:1058-1108`：所有 profile 的 rerank 都在 v3 之后执行第一方来源优先重选。
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py:1111-1115`：generic 的内容综合委托 v3。
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py:1447-1453`：generic cite 共享 v3 support/publish gate，但不运行 technology 专业报告门；v4 仍在图路由和 finalize 中提供 zero-candidate、insufficient-evidence 与安全失败交付。
- `backend/deskpet/workflows/definitions/deep_research_v3_nodes.py:419-435`：generic query expansion 主要是原问题、`official` 后缀与已有 source pack，不以缺失研究维度为输入。
- `backend/deskpet/workflows/definitions/deep_research_v3_nodes.py:347-363`：证据相关性通过空格切词；连续中文问题通常成为一个长 token，导致真实样本的 evidence relevance 全部为 0。
- `backend/deskpet/workflows/definitions/deep_research_v3_nodes.py:718-779`：rerank 优先综合分、域名和 URL 多样性，不保证核心维度、source family 或第一方原文覆盖。
- `backend/deskpet/workflows/definitions/deep_research_v3_nodes.py:895-920`：综合提示要求 8～16 条独立事实，目标仍是事实枚举而不是回答研究问题。
- `backend/deskpet/workflows/definitions/deep_research_v3_nodes.py:798-839`：候选目标依次偏好 gate passed、支持事实数、引用数、域数、支持率、正文长度，没有相关性、核心维度覆盖、综合推理或可读性目标。
- `backend/deskpet/workflows/definitions/deep_research_v3_report.py:60-89`：generic renderer 把 Coverage JSON 和内部错误列表直接倾倒进用户报告。

这条链路的通用结论是：v4 为所有主题增加了结构化配置、第一方来源优先重选和安全失败交付外壳，但只为 `technology_intelligence` 增加了领域专用问题建模与专业报告门。generic 的 query、综合和报告质量核心仍是 v3 的“搜到段落 → 生成可引用事实 → 数量门发布”。要解决教育、政策、产业和通用调研质量，必须在 v5 新版本中建立通用质量协议，而不是继续给 v4 generic 分支打特例或重复实现已有的 v4 失败交付能力。

## 3. 当前检索与抓取边界

### 3.1 Search Gateway

`SearchGateway` 已经是 quick search 和 DeepResearch 的共享检索内核，拥有 provider routing、provider 级 limiter/circuit、请求级预算、缓存、去重、排序、empty-aware rescue 与安全 diagnostics。现有 provider 的主要顺序由 `backend/deskpet/retrieval/routing.py:6-18` 决定：中文请求优先 SearXNG（若配置）和 Baidu，随后 Google/Bing CDP，再补配置中的其余 provider。

当前排序仍是“单条 query 的候选排序”：`backend/deskpet/retrieval/ranking.py:49-68` 用 query token relevance、provider rank、freshness、域名质量和 provider diversity 打分。它不知道研究核心维度，也无法判断多个 query 合并后是否遗漏某个用户问题。

固定教育样本的持久化报告显示 Search Gateway 共取得 35 candidates，最终 11 个引用、8 个独立域；所以该失败不是简单的“完全没搜到”。真正缺口是：

1. query 没有围绕核心维度展开；
2. 中文相关性失真；
3. 排序不保证每个核心维度和第一方 source family；
4. 官方教育部原文存在时，百度百科仍能被选为主要引用；
5. Search Gateway 没有收到报告审计产生的结构化缺口，无法进行定向补证。

### 3.2 FetchExtractService

当前唯一抓取策略在 `backend/deskpet/retrieval/fetch_extract.py:93-171`：

```text
cache
  -> static fetch_raw + extract
  -> 若 always 或 app-shell：系统 Edge CDP render（最多一次 request budget）
  -> 正文仍少于 300 字且允许：Jina fallback
  -> quality flags / content hash / cache
```

系统 Edge 渲染器由 `backend/deskpet/tools/research_cdp_edge.py` 自行寻找已安装 Edge、管理 CDP target 和临时 profile。它是兼容回退，不是可随安装包保证版本、行为和可用性的产品依赖。验证码页可以被浏览器打开，但必须由内容质量门拒绝，不能进入 evidence。

目标顺序应是：静态抓取 → 随包 Playwright/Chromium → 系统 Edge CDP 兼容回退 → 明确失败。Playwright 只负责高价值动态页的有限渲染/展开，不承担研究规划或逐页自主浏览。

## 4. Playwright 与安装包当前事实

- `backend/pyproject.toml:20-24` 直接依赖 `browser-use>=0.1.40`，但没有为 DeepResearch 固定 Playwright/Chromium 版本。
- 当前开发虚拟环境实测为 `playwright=1.61.0`、`browser-use=0.11.13`，Playwright driver 位于 site-packages；本机不存在 `%LOCALAPPDATA%/ms-playwright`、`backend/.local-browsers` 或 `backend/ms-playwright` 浏览器目录。
- `backend/deskpet/tools/browser_use_tool.py:346-383` 只在 code E2E 工具运行时懒加载 browser-use；失败提示仍要求安装后再下载浏览器。它不是 DeepResearch 抓取服务，也不应成为该服务。
- `backend/deskpet-backend.spec` 当前没有 Playwright browser resource 收集规则；`tauri-app/src-tauri/tauri.conf.json:87-89` 也没有 Chromium/Playwright resource 映射。
- 当前 `F:/deskpet-build/dist/deskpet-backend` 实测约 1,302,310,485 bytes；新增 Chromium 必须记录冻结 backend 与最终 NSIS 的体积、首开耗时、峰值 RSS 和退出清理，而不能只证明开发环境能运行。

结论：项目里已有“间接安装的 Playwright Python 包”，但没有“可离线交付、版本匹配、运行时可定位、可清理”的内置浏览器产品能力。可行的实现边界是新建 retrieval-owned renderer adapter 与 bundle resolver；不能复用 `browser_use_tool.py` 的 LLM Agent/job 模型。

## 5. 当前进度 UI 边界

现有 durable workflow 已把 public stage 通过 outbox 和 SessionDB 投影到消息系统；前端 `tauri-app/src/stores/sessionsStore.ts:459-491` 将 completed child 转为隐藏的 `workflow_stage` 消息，并允许 `action/result/result_code/diagnostic_codes` 等白名单字段。`WorkflowProgressGroup` 聚合这些事件，默认显示总卡、展开后显示阶段详情。

现有 UI 能回答“执行到哪个阶段、阶段有多少候选/引用”，但不能持续回答：

- 当前研究问题被拆成哪些核心维度；
- 哪些维度 covered/partial/uncovered，证据和结论分别如何；
- 当前补证为什么发生、针对哪个缺口、取得什么增益；
- 300 秒后为何续租、还剩多少自动安全预算；
- 预计会交付 completed、partial 还是 insufficient；
- 报告审计失败了什么，定向修复后改善了什么。

目标仍遵守用户已确认的交互：每完成一步生成一条可持久化聊天气泡，默认折叠；总体卡始终显示覆盖、质量、缺口和预算摘要，展开后才查看每步动作、结果与失败/降级原因。

## 6. 目标架构边界

### 6.1 版本边界

- 新能力注册为 immutable `deep_research/v5`，只供新 run 使用；这只保证“不改坏旧 checkpoint”，并不自动完成接线。
- v1～v4 definition、manifest、state factory、恢复 fixture 和历史消息解析保持不变。
- v5 必须显式扩展 definition/manifest、state factory、bootstrap registry、launcher adapter、`main.py` 默认版本 allowlist、retry/recovery action matrix 和兼容 fixture；当前 `backend/deskpet/workflows/launcher.py:61` 与 `service.py:679-716` 把 retry 锁在 failed `deep_research/v4 + retry_from_start`，不能原样复用。
- Search Gateway、FetchExtractService 和 workflow service 是共享基础设施；共享改动必须保持 quick search 与旧 workflow 的兼容契约。
- 新进度字段只能通过 allowlist 扩展，历史 session 无需迁移或伪造维度状态。

### 6.2 新的领域对象

建议由 v5 contracts 明确拥有以下版本化对象，避免继续把核心质量状态塞进松散 `dict`：

- `ResearchBrief`：用户问题、研究 profile、时点/地域/对象、核心维度、期望决策与不适用条件。
- `ResearchDimension` / `DimensionCoverage`：维度 id、重要性、查询目标、证据要求、`covered/partially_covered/uncovered/not_applicable`、缺口原因和 winning evidence。
- `EvidenceSourceFamily`：canonical source、第一方/二级来源、官方原文替代约束、去重族和页面质量。
- `GapWorkItem`：只针对未覆盖维度，记录策略、query、source target、预期增益、实际增益和停止原因。
- `ResearchBudgetLedger`：单调时钟基准、300 秒软检查点、120 秒 lease、900 秒自动安全上限、static/Playwright/Edge 分层预算、upstream 预算与连续无增益周期；每轮完成后随 checkpoint 持久化。
- `LLMBudgetLedger`：role、模型、input/output token、耗时、成本、触发原因、质量/覆盖增益；不以固定调用次数表达。需要新增带 usage 的 versioned LLM result/port，由 provider 返回的权威 usage 驱动，失败/取消调用也按实际可得 usage 记账；现有只返回字符串的 `ResearchLLMPort.complete()` 不能直接满足该契约。
- `DimensionAnalysis`：该维度的现状、变化/原因、影响、反证/不确定性和 winning citations。
- `ReportQualityAudit`：六维评分、硬失败、缺口、允许的定向修复动作和前后分数。
- `DeliveryDecision`：`completed/partial/insufficient_evidence`、原因、可继续的 checkpoint/reference。
- `ResearchOperationLineage`：parent run/operation、复用证据快照、原报告/Artifact immutable fence、新预算 lease 与新 run id。
- `ResearchControlDecision`：`generate_now` 的 prepared/open/resolved 状态、幂等键、停止启动新 upstream 的 fence、当前原子操作收敛状态和恢复投影。

### 6.3 节点职责

目标 v5 对外仍可投影为用户可理解的阶段，但不能把 300～900 秒的 rescue 或多轮 report repair 塞入一个 handler。每个会触发网络、浏览器或 LLM 的 gap work item/repair round 都必须成为独立可 checkpoint node（或具有等价持久化语义的 durable child operation），由条件边逐轮推进：

```text
normalize/model -> ResearchBrief + dimensions
plan/expand     -> per-dimension query/source targets
search/direct   -> Search Gateway results tagged by dimension
fetch/score     -> source-family-aware evidence admission
gap_evaluate    -> coverage/plateau/lease decision（纯决策并 checkpoint）
gap_search_n    -> 一个有界 GapWorkItem（完成后 checkpoint）
gap_join        -> 合并证据并更新 coverage/ledger（checkpoint 后条件回边）
rerank          -> dimension/source-family balanced evidence set
synth           -> per-dimension analysis + draft report（checkpoint）
quality_audit   -> claim support + report rubric（纯决策并 checkpoint）
repair_n        -> 一个有界定向修复动作（完成后 checkpoint）
repair_join     -> 更新分数/ledger，条件回边或进入 delivery
persist         -> immutable report/checkpoint
finalize        -> three-state delivery + continue/now actions
```

关键规则：原子事实 support gate 继续保留，但降级为报告质量的必要条件之一，不再独自决定 `completed`。

`generate_now` 不是前端本地取消：workflow service 必须创建 durable decision/action，使用 `(run_id, action_id, idempotency_key)` 幂等消费；决议后立刻设置“禁止新 upstream” fence，允许已经开始的单个 provider/page/LLM 原子操作在有界时间内收敛，随后从最新 checkpoint 进入 synth/audit/delivery。重启时 decision 与 fence 必须恢复并继续投影。

`continue_research` 也不是原 run 原地继续：它创建带 parent reference 的新 operation/run，复制旧 run 已确认的 dimension/evidence/source-family 快照并分配新 lease，只为缺口产生工作项；旧 report、Artifact、delivery receipt 和 run state 永久不可变。

### 6.4 Playwright ownership

```text
FetchExtractService
  -> StaticFetcher
  -> RenderPolicy decides high-value dynamic fallback
  -> PlaywrightRendererPool
       -> BrowserBundleResolver
       -> <= 1 browser process / <= 2 contexts
       -> isolated BrowserContext per fetch/run
       -> allowlisted read-only actions
  -> EdgeCdpRenderer compatibility fallback
  -> ContentQualityGate
```

- `PlaywrightRendererPool` 属于 retrieval 层，由 backend lifecycle 启停；不能由 workflow node 或 browser-use job 各自起浏览器。
- browser executable 与 Playwright driver 路径必须由 bundle resolver 显式解析，开发、PyInstaller frozen、Tauri resource 三种环境使用同一诊断契约。
- page/context 在 success、timeout、cancel、crash 和 backend shutdown 下都必须 finally 清理；browser 崩溃后允许一次有界重建。
- CAPTCHA、登录页、robots/策略拒绝和交互越界返回结构化失败，不以“浏览器成功打开”为 fetch success。
- render budget 必须分开记录 `static_attempts / playwright_attempts / edge_attempts` 及各自结果；Playwright 失败后是否允许 Edge 由显式 fallback policy 和剩余 Edge quota 决定，不能沿用一个 `claim_cdp()` 布尔额度让首个 renderer 吃掉全部回退机会。

### 6.5 进度与控制面的跨层 owner

- 后端以 v5 progress schema/template 为唯一 owner，定义 dimension matrix、gap work item、lease、quality audit、predicted delivery、decision 与 lineage 的安全字段和 capability version。
- workflow outbox/message envelope 只传 allowlisted projection；SessionDB 保存 durable child bubble 和 summary，不保存任意 prompt/网页正文。
- `sessionsStore` 按 workflow/version/capability 解析并去重；`WorkflowProgressGroup` 只渲染，不自行推断 coverage 或终态。
- `MessagePanelRoot`/IPC 承担 `generate_now`、`continue_research` 和 retry 的请求/accepted/error 状态；当前只支持 `retry_from_start` 的联合类型必须扩展为 action matrix，且 v4 历史消息仍按旧语义工作。

## 7. 需要根治的架构债

1. **profile 特例化**：technology path 专业化、generic path 仍事实清单。继续增加 education 特例会复发；应抽出 universal quality contract，再让 profile 只定义维度模板、来源优先级和报告附加要求。
2. **质量状态无主**：维度、覆盖、报告审计和补证增益目前没有版本化 owner；应由 v5 contracts + deterministic policies 统一拥有，UI 只投影。
3. **抓取回退与产品打包脱节**：当前 CDP Edge 能力依赖系统浏览器，browser-use 又是 code E2E 的自主 Agent；应新增 retrieval-owned Playwright adapter，不污染二者职责。
4. **固定硬 deadline 与质量目标冲突**：technology v4 在 `deep_research_v4_nodes.py:313-316` 把 runtime 强制夹在 60～300 秒并写 hard deadline。v5 必须改为软检查点、lease 与安全上限，且旧 v4 不改。
5. **报告 renderer 暴露内部诊断**：generic v3 把 Coverage JSON/错误列表塞入报告。v5 的用户报告和机器审计应是两个 artifact/view，消息 UI 只展示安全摘要。
6. **长节点破坏 durable 语义**：如果 900 秒 rescue/repair 在一个 handler 内完成，崩溃时整轮丢失，也无法可靠处理立即生成；必须用 checkpointed loop/child operation 根治。
7. **控制动作硬编码 v4 retry**：当前 launcher/service/UI action contract 只认识 failed v4 的 `retry_from_start`；v5 必须扩展版本化 action matrix，而不是在前端临时加两个按钮。

## 8. 测试与可观测性边界

实现计划必须至少建立四层证据：

1. 单元/属性测试：contracts、中文 query/relevance、dimension coverage、source family、预算 ledger、质量评分、三态决策、Playwright policy/lifecycle。
2. 集成测试：Search Gateway → static/Playwright/Edge → evidence → dimension analysis → audit/repair → delivery；包含 timeout、CAPTCHA、crash、cancel、checkpoint/resume。
3. 冻结构建与 Windows 11 隔离安装：PyInstaller/Tauri/NSIS 内浏览器路径、断网首次启动、版本匹配、无 CDN 下载、无孤儿进程；本轮只以当前 Windows 11 x64 主机为硬门，Windows 10 认证延期。
4. 真机 E2E：从 DeskPet UI 真输入教育与 AI 场景，验证每步气泡、总体覆盖/质量进度、“立即生成”和“继续调研”，并人工 review 最终 Markdown 的专业性。测试通过后才按 AGENTS.md 更新 `ARCHITECTURE/` 与 `PROJECT_STATUS.md`。

隐私边界沿用现有安全 diagnostics：进度和 ledger 不持久化完整 prompt、正文、凭据或任意 URL 列表；用稳定 id、计数、role、reason code 与安全摘要审计。

## 9. 未闭环项（进入实现计划前必须给出验证任务）

- 精确固定哪个 Playwright 版本及 Chromium revision，需要用项目构建链做 frozen spike，并在当前 Windows 11 x64 主机完成断网隔离安装与实机验证；开发 venv 当前的 1.61.0 不是自动接受的产品版本。Windows 10 兼容性留给后续独立认证计划。
- Chromium 加入 backend/Tauri 后的最终 NSIS 体积、makensis mmap 风险、更新包增量和冷启动开销尚无实测，必须设置 build spike 和硬证据门。
- 900 秒默认安全上限、120 秒 lease、LLM token/cost 预算的具体配置值需要由固定 benchmark 校准；验收只锁定语义，不允许实现时重新退回固定调用次数。
- Playwright 有限交互白名单需要用固定动态 fixture 和真实公开页面分别验证；不能用只适配测试页的选择器宣布完成。
- `partial` 的最低可用结论线和六维质量评分细则需要落成机器可读 rubric，并与 SC-EDU-01、SC-AI-01 的人工 review 对齐，避免新门再次被数量指标投机通过。

## 10. 架构基线结论

验收需求在技术上可行，但不是在现有 v4 generic pipeline 上增加一个浏览器 fallback 就能完成。可靠边界是：保留 Search Gateway 和 durable workflow 基础设施，新建 immutable v5 通用质量闭环；将 Playwright 作为 FetchExtractService 的受控、可打包渲染器；把维度覆盖、预算增益、报告审计和三态交付作为版本化生产状态，并完整投影到现有默认折叠的聊天进度 UI。
