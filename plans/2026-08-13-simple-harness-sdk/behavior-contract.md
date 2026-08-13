# Behavior contract — Simple Harness SDK v0.1

> 状态：FROZEN with acceptance and `BC-SDK-IMPORTS` approval（2026-08-13）  
> 来源：[`source-request.md`](source-request.md)；验收：[`acceptance.md`](acceptance.md)。

## 术语与实体关系

| 术语 | 精确定义 | 关系 |
|---|---|---|
| Product Session | 消费者拥有的用户会话、消息历史与 UI 投影 | 不属于 SDK persistence；可关联多个 root Run |
| execution session identity | SDK 用于隔离/关联 Run 的稳定 identity | 不是 Product Session 数据库 |
| root Run | 一条顶层用户请求创建的 durable execution | 永远固定 Profile=`agent.general` |
| child Run | root Agent 通过 SDK control 显式启动的附属执行 | 只能消费 frozen、一次性 launch ticket |
| Profile | Agent 可见的职责/输入合同与 Host 可验证的 Driver/Workflow 绑定 | v0.1 有 `agent.general` + 三个官方 Workflow Profile |
| Driver | 执行算法；v0.1 为 ReAct 与 Native Workflow | Profile 绑定 Driver；文本/正则不能直接选 Driver |
| Workflow | 可 checkpoint/recover 的 graph/runtime 定义 | `capability_build` public Profile 复用 `durable_task` graph 并注入受治理 payload |
| Tool effect | 已准备、已授权、带稳定 `call_id/effect_id` 的宿主调用 | 由 ledger/fence/reconciliation 防止不确定重放 |
| Provider invocation | 一次模型出站调用 | 有独立 durable claim/handoff/outcome/unknown ledger |
| delivery | root terminal 后给消费者 Presenter/Artifact/UI 的 generic outbox item | terminal + outbox 原子提交，具体展示留在产品层 |

## Before / after

| 行为 | 当前 Simple Harness | SDK v0.1 + Simple Harness cutover 后 |
|---|---|---|
| 顶层选择 | 产品 composition 固定 `agent.general`；Kernel 类型仍保留 legacy router 入口 | SDK public composition 强制固定 `agent.general`，无 public classifier/router |
| Workflow 选择 | 主 Agent 选择大部分 Profile；Personal 另有前置模型 matcher；多文件任务有硬 `MUST durable_task` prompt | 同一个主 Agent 从 frozen descriptor catalog 选择 direct/tool/三个 Workflow；无前置 matcher/正则/关键词/产品旁路；多文件规则变为 descriptor 建议 |
| child authority | canonical 路径用 ticket，但 Kernel 仍有 ticketless child 兼容路径 | SDK public child API 只接受一次性 ticket，过期 generation/fingerprint fail closed |
| durable Kernel | 分布于 DeskPet Harness/execution/workflow/product modules | 完整 Kernel authority 在 SDK；产品只实现 Ports/Adapters |
| Provider | HTTP Adapter 混入 DeskPet dispatch/context/metrics/hook | SDK 提供单次调用 HTTP Adapter；durable dispatch ledger 属 SDK execution；产品 metrics 在 Adapter 外 |
| Tool | DeskPet ToolRegistry/permission/category 与 orchestration control 耦合 | SDK typed registry/control + Host Authorization/Tool/Reconciliation Ports；不内置 shell |
| `durable_task` | DeskPet native/code runtime 实现 | SDK 官方实现；消费者不重写 graph、HITL、repair/test/audit/output contract |
| `personal_v1` | 前置 matcher 冻结唯一 selection；单 Native execute node + EffectJournal | 主 Agent 选择 stable candidate ID；Host 绑定 frozen graph；保持当前 safe-replay Tool 与 checkpoint 边界 |
| `capability_build` | reserved control，绑定 `durable_task@v1` 并注入 builder payload | 同样作为官方受治理 `durable_task` 特化；模块/Ports 就绪即进入 catalog并默认启用 |
| persistence | `workflow.db` schema 29 混合产品/执行/权限/能力数据 | SDK clean schema v1 只拥有 execution/workflow/provider/effect/delivery；不迁移旧开发 Run |
| 历史数据 | 当前开发数据可读 | cutover 前精确定位并执行一次审计式 dev reset；不提供旧 Run兼容层 |
| Simple Harness 消费 | 核心代码内嵌在产品仓库 | 产品只经 SDK public API；最终从 exact wheel 安装，旧同源 Kernel/Workflow 删除 |
| AIPhone 消费 | 仅 Handoff 设想 | 获得 exact tag/commit/wheel hash、Port contract 与 install/conformance 结果；本 release 不改手机 |

## 保留、改变、删除

- 保留：现有用户可见的普通回答/Tool/长任务/HITL/cancel/recovery/child delivery 能力；三个指定 Profile；新 Run 的 idempotency、fence、unknown reconciliation。
- 改变：代码所有权与 import namespace；Personal selection authority；Profile 条件注册；Provider/Tool/权限依赖方向；开发数据库从 clean schema v1 开始。
- 删除：SDK public legacy classifier/router、ticketless child、Personal 前置 matcher、桌面端同源 Kernel/Workflow 副本、旧 Run/schema 兼容迁移。
- 不新增到 v0.1：DeepResearch、PPT、Mobile Host、Tauri/UI/FastAPI、语音、本地模型、浏览器/桌面自动化、默认 shell Tool。
