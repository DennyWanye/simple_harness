# simple_harness Agent Harness 生命周期

> Developer keyword: Harness lifecycle.

本文是 [`ARCHITECTURE/AGENT_HARNESS.md`](../ARCHITECTURE/AGENT_HARNESS.md)
的开发者速查版。生产中的 Text、Voice 和自动恢复入口现在都进入同一个
Harness 装配。

## 生命周期

```text
产品入口 -> ProductTurnPreparer -> RunKernel -> 按已绑定 Profile 进入一个 Driver
                                              |-> ReAct Driver (`agent.general`)
                                              |-> Workflow Driver（模型委派的 child）
Driver -> EffectBatchExecutor -> ToolRegistry V2
Driver 事件 -> RunPresenter -> UI / SessionDB / TTS
DelegateRun -> 持久 ChildRun -> RunKernel
```

`RunKernel` 是活动运行身份、Profile 绑定、取消、signal、父子关系和事件流的唯一
owner。每个 Run 只绑定一次 Driver。根任务固定为 `agent.general`；模型需要长任务
能力时，通过 `workflow_spawn` 明确选择 child profile，Kernel 只校验 Profile
Catalog 并读取该 profile 的 `driver_kind`，不再用正则或关键词做语义路由。
Driver 只产生 typed event，不直接写 WebSocket 或 UI payload。

## 服务注册

`ServiceContext.register()` 和 `ServiceContext.get()` 提供产品依赖，但不拥有
Run 生命周期状态。进程内 decision waiter、Subagent registry 和 completion
queue 不再是生产服务。

## 产品准备

`ProductTurnPreparer` 保留旧 Text handler 周边的产品语义，包括：

- `ContextAssembler`
- 历史、persona、memory 和 skills
- 附件
- Problem Pipeline 输出
- plan 状态
- supervisor hint

它返回 Kernel 使用的准备后输入，不自行选择第二条执行路径。

## 两类 Driver

ReAct Driver 把 `AgentLoop` 接入 Harness 事件协议，用于根 Agent 的模型判断与工具循环。
Workflow Driver 接入有 checkpoint、可恢复的 DeepResearch、PPT 和通用 durable task。
两者共用 Kernel、Effect executor、ToolRegistry V2 和 Presenter，但保留不同的内部
状态机。

`AgentLoop` 继续负责：

- ReAct completion gate
- 上下文预算
- 工具结果记录
- Provider fallback
- typed loop event

它不再拥有 WebSocket 投影、持久 Child 状态或进程内 Subagent queue。
`build_agent` 仍是产品装配为每个 Run 构建 loop runtime 的 host factory。

工具或 child 失败后，失败证据以结构化结果回到同一个模型。稳定 `TaskGoal` 下新建
`PlanVersion` / `Attempt`，模型读取失败原因后继续规划；同一错误指纹由宿主执行有界
循环守门。能力修复成功后，宿主用新 ToolSpec 重新 prepare 原始参数并自动重试，
不要求用户重复请求。

## ChildRun

委派工具返回 typed `DelegateRun` 或 `OpenDecision` command。Kernel 创建持久化
`ChildRun`，其中携带 session、root、parent 和 capability 身份。Child 结果通过
统一 Run 协议返回，不再依赖全局 `SubagentRegistry` 或 `completion_queue`。

## Trace 与恢复

- 持久状态变更统一经过 `SqliteExecutionUnitOfWork`。
- Execution delivery dispatcher 负责持久 delivery 和重试。
- `HarnessSupervisor` 有界管理 recovery、child、delivery 和 late-effect worker
  的启动与关闭。
- 结构化 trace 按 Run 和 task 身份关联。
- `RunPresenter` 只负责输出适配，不决定运行状态。

## 测试路由

- Kernel、Driver、UoW、ToolRegistry、生命周期和 typed Child 契约：pytest 与验收脚本。
- 事件映射、ArtifactCard、权限和桌面交互：使用 `windows-mcp` 规则执行真实
  Windows UI E2E，并保存截图和 backend 日志。
- 用户可见行为改变时，协议层 WebSocket 注入不能代替真实 UI 测试。
