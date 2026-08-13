# 验收标准草案：Simple Harness SDK v0.1 提取

> 状态：CONFIRMED / FROZEN（2026-08-13）；所有者已确认范围、仓库名、许可证、消费者、发布与迁移顺序。后续若改变 MUST acceptance，必须显式回到本阶段重新确认。
> 当前审计 HEAD：`122ec55989f8a77e023aeb44ba1b4dae1b694269`
> 原始 Handoff 基线：`9e53bb7924a8b0a2a7a7b98799d6288da2c41914`

## 范围

- 包含：从 DeskPet/Simple Harness 提取无 UI、可嵌入、可独立发布的 Python SDK；首版覆盖公共合同、ReAct Runtime、完整 durable RunKernel、原生 Workflow Engine、OpenAI-compatible Provider、通用 Tool Port、SQLite execution-session identity/Run ledger 和 conformance testing kit。产品会话消息、SessionDB 与 UI projection 仍由消费者拥有。
- 包含：SDK 官方维护并可直接使用的 `workflow.durable_task`、`workflow.personal_v1`、`workflow.capability_build` 三个 public Profile；消费者只实现宿主 Port/Adapter，不重新开发其 graph/特化逻辑、节点编排、恢复、重试、HITL 或交付协议。
- 包含：Simple Harness 桌面产品和 AIPhone 都作为 SDK 的正式消费者；Simple Harness 必须改用 SDK public API 并完成真实桌面对话与三个官方 Workflow E2E，AIPhone 本 release unit 只完成可安装性/消费合同与 Handoff，不修改或部署手机端。
- 包含：独立版本、构建、锁文件、API 文档、迁移指南、SBOM/第三方 notice、发布 tag/commit/hash，以及交付 AIPhone 的消费 Handoff。
- 明确不包含：AIPhone Mobile Host Adapter、手机部署、Tauri/React/FastAPI/WebSocket、语音、本地模型、浏览器/桌面自动化、完整长期 Memory、Deep Research、PPT、MCP marketplace、DeskPet 专属 team 实现和默认 shell Tool。通用 root/child Run、Profile/Driver、continuation/signal/cancel、Effect/UoW/fence、reconciliation、checkpoint、HITL 和恢复属于 SDK 首版范围。
- 明确不包含：兼容或迁移当前开发期 DeskPet 历史 Run/旧数据库；切换 SDK 时执行一次明确、可审计的开发数据重置，SDK 从干净的 schema v1 开始。SDK 自身后续 schema 升级仍必须版本化并测试。
- 明确不包含：本 release unit 不验收“清除桌面应用全部数据 -> 首次登录 -> 立刻直达 SDK 功能”的产品级冷启动路径。SDK 的 clean wheel 安装、纯净 import、显式 `build/start/close`、schema v1 首次创建和 close/reopen 仍是首版硬验收；Simple Harness 真机 E2E 使用已有有效开发登录态。产品级首次登录冷启动竞态按 SR-9 登记为独立 follow-up，不冒充已测试结果，也不阻塞本次 SDK 提取。
- 迁移原则：先冻结 public contracts 与消费者级 RED tests，再按垂直切片反转依赖；禁止整目录复制，禁止桌面端与 SDK 长期保留两份同源 Agent Loop。

## Program 级功能验收条款

> 本文是 program acceptance，不作为单个 release unit。实施 plan 必须拆成多个独立垂直 slice；每个 slice 遵守 `MUST AC <= 8`、高风险子系统不超过 3 个的门禁。

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| SDK-AC-1 | 独立发行与纯净导入 | SDK 位于独立仓库并有独立版本、锁文件、wheel/sdist 构建和测试；Python 3.11 的 Linux ARM64、macOS ARM64、Windows x64 支持矩阵有证据；`import simple_harness` 不联网、不创建目录、不启动线程/task、不扫描环境，且不导入 FastAPI、Tauri、Torch、Whisper、Playwright 或 DeskPet 产品模块。 | 必须 |
| SDK-AC-2 | 稳定公共合同 | 提供 immutable messages、Provider request/response、`ToolSpec/ToolCall/ToolResult`、typed events、JSON value、run/session/request correlation identity 和稳定错误码；API Key/canary 不进入日志、SQLite、trace、异常、Tool context 或模型消息。 | 必须 |
| SDK-AC-3 | Provider Port | Provider 只执行一次模型调用，不拥有 Agent/Session/Tool 状态；OpenAI-compatible HTTPS Adapter 支持自定义 base URL/model、structured tool calling、timeout/cancel 与认证/限流/服务端错误分类；受控 mock server 完成 tool-call E2E，接口保留 streaming 扩展空间。 | 必须 |
| SDK-AC-4 | Tool Port 与注册表 | 宿主可注册 typed Tool；参数在 handler 前严格校验额外字段、类型、长度和枚举；未知 Tool、malformed call、重复/迟到/cancel 后 result 都有确定语义；`ToolResult` 区分 `succeeded/partial/rejected/failed/unknown`，不会把原始异常、stderr、HTTP body 或私密数据直接返回模型；SDK 不内置 shell Tool。 | 必须 |
| SDK-AC-5 | 完整 durable Harness Runtime | SDK 提供完整 durable `RunKernel`、固定 root Profile、Profile/Driver Registry、root/child Run、admission/start snapshot/activation、continuation/signal/cancel、Provider dispatch ledger、Effect ledger/UoW/fence、checkpoint、generic terminal delivery outbox、HITL、crash recovery 和 reconciliation；fake Provider + fake Tool 可完成无 Tool、单 Tool、多轮/多 Tool 与 attached child 闭环；max turns、Tool 次数、wall clock、cost、连续同 Tool上限由系统代码硬约束；Provider/Tool 不确定副作用结果为 `unknown` 并由显式 reconciliation Port 处理，绝不盲目重放。 | 必须 |
| SDK-AC-6 | Agent 自主选择官方 Workflow | 所有新顶层请求无论文本、venue 或 mode 都固定进入 `agent.general`；SDK 把当前可用 Workflow 的自然语言职责、精确 key、输入合同和 catalog generation 提供给同一个主 Agent，由 Agent 决定继续直接回答/调用 Tool，或显式启动 `workflow.durable_task`、`workflow.personal_v1`、`workflow.capability_build`。`personal_v1` 不再由独立前置模型 matcher 预选；Host 只冻结安全候选目录，并在主 Agent 选择后绑定可信 graph/owner/version。生产入口不得使用正则、关键词、独立意图分类器、legacy router、ticketless child 或产品旁路替 Agent 选择 Workflow；Host 只验证可用性、权限、catalog、frozen selection 和 launch ticket，不做语义改写。 | 必须 |
| SDK-AC-7 | 三个官方 Workflow 与干净持久化 | `durable_task` 保留计划、HITL、工具循环、测试/审计/修复和输出合同；`personal_v1` 保留受信定义选择、冻结图，以及当前“Native execute checkpoint + EffectJournal”的恢复边界，并继续只允许可安全重放的 `idempotent_read`/`deterministic_reusable` Tool；`capability_build` 以官方 `workflow.capability_build` Profile 暴露，但明确实现为受治理的 `durable_task` 特化，保留能力缺口证明、隔离构建、测试、安装/激活边界。消费者通过标准 Port 注入所需能力。宿主显式注入 SQLite 路径；新 schema v1 可 close/reopen 与崩溃恢复；不兼容当前开发期历史 Run，切换时执行一次经确认的数据重置。 | 必须 |
| SDK-AC-8 | Conformance、Simple Harness 自用与发布 | 消费者可复用 Provider/Tool/Kernel/Driver/Workflow/Session persistence conformance suite；secret redaction、malformed call、duplicate/late result、restart-without-replay 有硬测试。桌面产品只通过 SDK public API 使用被提取能力，不保留第二份同源实现；原关键 Harness 回归及三个 Workflow 的真实 Agent 选择/执行 E2E 通过。两次 clean build 的 canonical 内容一致并记录 SHA-256；API reference、quickstart、migration/reset guide、changelog、SBOM、third-party notices、exact tag/commit/wheel hash 和 AIPhone Handoff 完成；许可证由所有者明确确认。 | 必须 |

## 非功能与边界

- 安全：Secret 只由宿主注入；Tool schema 只来自本地可信注册；Provider/Tool 错误默认最小披露；SDK Tool Registry 不替代产品权限系统或 AIPhone Permission Broker。
- 生命周期：Core 不启动服务器；全部资源由显式 `build/start/close` 管理并支持 async context manager。
- 依赖：Core 优先标准库；HTTP 可使用 `httpx`；SQLite 使用标准库或经审计的轻量 async wrapper；不得强制 Node、Rust、Tauri、PyTorch、Playwright 或系统浏览器。
- 兼容：Python `>=3.11`；public API 遵循语义化版本；消费者只依赖 exact tag/commit/hash，不依赖浮动 `main`。
- 恢复：v0.1 不自动重放未完成的有副作用 Tool；不确定执行结果 fail closed 为 `unknown`，由 SDK reconciliation 协议与宿主 Adapter 共同对账。
- 预算：Provider 必须返回可信 usage/cost、由 SDK 按冻结价格表计算上界，或在缺失时把 cost 标为 `unknown` 并按宿主配置拒绝继续；不得把缺失 usage 当作零成本绕过 hard cap。恢复后的累计值以 durable Provider invocation ledger 为 authority。
- 路由：Workflow 选择必须来自同一个主 Agent 的显式控制调用；确定性代码只负责 catalog/profile/schema/权限/票据校验。正则可用于 Workflow 内部的安全断言或参数规范化，但不能决定顶层使用哪个 Workflow。
- 证据：原始截图、日志、数据库、receipt、diagnostic archive 保存在 `.local-test-evidence/`，Git 只提交结论、命令、状态、索引和安全 hash。
- 许可证：所有者已确认通用 SDK 使用 Apache-2.0，Simple Harness 产品层继续 BUSL-1.1；仅版权持有人拥有或可重新许可的代码可以进入 Apache SDK，第三方代码与依赖须重新完成来源和许可证兼容审计。

## 测试场景矩阵

> 适用性决定（SR-9）：本 release unit 的 `stateful_init=false`。SDK Runtime 由消费者显式注入
> Provider、Tool/Profile catalog 与 SQLite 路径，不拥有登录或远程配置初始化；本次也不修改
> Simple Harness onboarding/auth。产品级“清数据后首次登录并立即使用”冷路径已移至
> [`../2026-08-13-simple-harness-sdk-cold-start-followup.md`](../2026-08-13-simple-harness-sdk-cold-start-followup.md)。
> `input_sensitive=true`、`llm_payload_driven=true` 不变，SDK-S1..S7 及对应真实 Provider/UI 门禁不变。

| scenario_id | input_class | exact_input | primary_risk | gate_type | required | manual_required | terminal_expectation | quality_bar |
|---|---|---|---|---|---|---|---|---|
| SDK-S1 | 纯回答、无需 Tool/Workflow | “用一句话解释什么是幂等性。” | Runtime 不应臆造 Tool 或 Workflow call | positive-value | 是 | 是（桌面自用阶段） | `agent.general` root `completed`，有非空中文回答，无 child/effect | 回答准确、简洁，无虚构执行状态 |
| SDK-S2 | 单个只读宿主 Tool | “读取当前项目摘要，然后用中文告诉我重点。” | structured call、参数验证、结果回填 | positive-value | 是 | 是（桌面自用阶段） | 一个受信 Tool 成功，root `completed`，不启动 Workflow | 最终回答忠实概括 `ToolResult`，不泄露原始私密字段 |
| SDK-S3 | durable 多步骤任务 | “分析这个项目的测试缺口，形成计划，执行获准的检查并给出可审计结论。” | 主 Agent 自主选择、root/child、计划/HITL/恢复 | positive-value | 是 | 是（桌面自用阶段） | 同一 `agent.general` 显式选择 `workflow.durable_task`；child 可追溯并交付 root | 使用计划与检查结果，终态/产物/receipt 一致，不重复 effect |
| SDK-S4 | personal 候选选择 | 宿主提供两个受信 personal candidate，用户提出只与其中一个职责匹配的长期个人任务 | 独立 matcher 残留、候选图被模型篡改 | positive-value | 是 | 是（桌面自用阶段） | 主 Agent 显式选择 `workflow.personal_v1` 的稳定 candidate ID；Host 绑定 frozen graph/owner/version | 不运行前置语义 matcher，不接受模型自造 graph/version |
| SDK-S5 | capability 缺口构建 | “完成一项当前 catalog 没有能力处理、且允许安装新能力的任务。” | 未搜索即构建、越权安装、隐藏默认关闭 | positive-value | 是 | 是（桌面自用阶段） | 主 Agent 在目录检索无结果后选择 `workflow.capability_build`；构建、测试、安装/激活可审计 | 安装前有缺口证据和授权；完成能力默认可用，不保留隐藏双轨 |
| SDK-S6 | malformed/unknown Tool/Workflow 对抗 | 受控 Provider 返回未知 Tool/Workflow、缺字段、错类型、过期 catalog generation 或重复 result | fail closed、错误稳定、无 handler/effect 副作用 | negative-safety | 是 | 否（自动化 fault injection） | 确定性拒绝或失败终态，无未注册执行或过期 ticket 启动 | 稳定错误码，secret/canary 不泄露，无自动重放 |
| SDK-S7 | 崩溃后不确定副作用 | Tool handler 已出站但提交 receipt 前强制终止，随后 reopen/reconcile | 双重副作用、错误重放 | negative-safety | 是 | 否（自动化 fault injection） | effect 为 `unknown`/待对账；同一 `effect_id` 不自动再次执行 | reconciliation 决议可追溯，终态与 ledger 一致 |

## LLM 行为变异清单

| 变异 | 可验证容错断言 |
|---|---|
| 乱序响应 | event/result correlation 必须按 `run_id + call_id` 绑定；乱序结果不得写到另一调用或提前终结 Run。 |
| 重复输出 | 同一 `call_id/effect_id` 的重复 Tool call/result 不得执行或提交两次；返回稳定 duplicate 语义。 |
| schema 违约 | 缺必填字段、额外字段、错误类型/枚举在 handler 前被拒绝，Run 进入可解释的重规划或失败出口。 |
| 超长/极端载荷 | 超长参数和 ToolResult 受长度/预算限制；不会无限增长 Context、日志或 SQLite，且保持可诊断终态。 |
| 拒不调用 Tool/跳过指令 | 在需要 Tool 的受控场景中，Runtime 受最大轮数与 termination gate 约束，最终失败或诚实降级，不无限循环或伪造结果。 |

## 建议的垂直切片（待 phase-1 定稿）

1. Slice A：独立仓库骨架、纯净 import、contracts/events/errors、RED conformance tests。
2. Slice B：SQLite Execution UoW、RunKernel、root/child、fence、reconciliation 与 crash recovery。
3. Slice C：budget/termination、Provider Port、Tool Port/registry 与 ReAct Driver。
4. Slice D：Profile Catalog、Agent 自主 Workflow 选择、Workflow Engine 与 Workflow Driver。
5. Slice E：`durable_task`、`personal_v1`、`capability_build` 官方模块及其宿主 Ports。
6. Slice F：Simple Harness Adapter 迁移、开发数据重置、自用回归与三个 Workflow 真实 E2E。
7. Slice G：跨平台构建、reproducible artifacts、SBOM/notices、docs、tag 与 AIPhone Handoff。

## 已确认的所有者决策

1. 独立仓库采用 `simple-harness-sdk`。
2. 通用 SDK 使用 Apache-2.0，Simple Harness 产品层继续 BUSL-1.1；提取过程必须建立逐文件来源与第三方许可证清单。
3. Simple Harness 和 AIPhone 都是正式消费者；Simple Harness 在本 program 内完成真实迁移，AIPhone 本 release unit 只交付消费合同与 Handoff、不修改或部署手机端。
4. 发布与迁移顺序固定为：独立 SDK 测试 -> Simple Harness 首个真实消费者（开发期可用本地 path dependency）-> 用最终 exact wheel 重新验收并删除旧同源 Kernel/Workflow -> 发布 exact tag/commit/wheel SHA-256 -> AIPhone Handoff。
5. SR-9 明确把产品级首次登录冷启动场景移至 follow-up；这不放宽 SDK clean-install/init/reopen，也不放宽已有登录态下的 Simple Harness 真实 E2E。

## 完成的定义

- SDK-AC-1 至 SDK-AC-8 均有 `AC -> slice/task -> code -> testcase -> result` 可追溯证据。
- 每个垂直 slice 有独立机器门账本与 receipt；program 完成不得用单元测试代替跨平台安装、桌面自用 E2E 或发布制品验证。
- Simple Harness 生产代码已使用 SDK public API，且不存在并行的同源核心实现。
- `ARCHITECTURE/`、SDK API 文档、迁移指南与 AIPhone Handoff 在通过测试的同一次交付中同步。
