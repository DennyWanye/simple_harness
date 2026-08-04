# Agent Harness R6 生产激活结果

> 日期：2026-07-21
> 状态：R6 生产激活通过；本文记录的是当时的阶段结果，最终 R7 状态见 [R7 证据](./evidence/r7-main-thread/results.md)。

## 结果

新的 Text、Voice、Code、DeepResearch、PPT、Subagent 和 Team-child 请求统一进入：

```text
Product Venue -> ProductTurnPreparer -> RunKernel
                                      -> ReAct Driver -> AgentLoop
                                      -> Workflow Driver
                 EffectBatchExecutor -> ToolRegistry V2
                 UoW / Delivery -> RunPresenter -> SessionDB / WS / TTS / UI
```

完整架构图见 [目标架构](./target-architecture.md)。

R6 删除了：

- AgentLoop 内旧工具 / Subagent 运行时
- 全局 Subagent registry / waiter
- DeepResearch / PPT 直接生产 starter
- Voice 私有 AgentLoop bridge
- AutoResume `_run_chat` 重新分发旁路

WebSocket 断线现在只解除展示订阅，不取消持久 Run。

## 正确性闭环

- Text 和 Voice 只有在 host 真正提出工具时才携带 `proposed_tools`，不能因全局 registry 存在而把普通对话误路由到 Code。
- Code 在启动前冻结 base/code/delivery session、epoch、Provider、模型和能力；恢复时重建并校验同一快照。
- 启动激活等待恢复上下文和 launcher 初始化完成后，才从 `activated` 进入 `open`。
- DeepResearch / PPT 选择和 Code payload 组装留在 `ProductTurnPreparer`，不进入通用 Kernel。
- 后端接收稳定 `client_request_id / client_turn_id`，相同身份重试不会重复追加用户消息。
- Context OS 持久化准确的请求级 `PreparedToolSet` 和 eligibility；Text/ReAct 与 Code 恢复时重建并校验。
- Child 在激活 owner 下使用窄的 v8 -> v9 schema migration：只有存在持久 parent command 的当前 generation child 才可写入。它没有引入第二 owner，也不重写历史。

## 自动化证据

| 门禁 | R6 阶段结果 |
|---|---:|
| Harness | `495 passed, 4 xfailed` |
| 后端零回归 | 最终 last-mile 通过 |
| Last-mile | `5/5 PASS`, `DECISION: SHIP` |
| 前端 Vitest | `80/80` 文件通过 |
| Rust | `cargo check` 通过 |
| Authority | legacy survivor `0`；run map / Presenter / Supervisor 各 `1`；starter `23`；DML authority `1` |
| Parity | `141/141`，未映射 `0` |
| Cutover | exact / similar / reference / reachability / live-stack 全部干净 |
| R6 LOC | raw `32,913`；adjusted `32,404`；core `5,949`；Kernel `900`；公开操作 `6`；unknown `0` |

机器证据：

- [R6 LOC](./r6-loc-final.json)
- [R6 last-mile](./r6-last-mile-acceptance.json)
- [R6 live stacks](./r6-live-stacks.json)
- [R6 cutover audit](./r6-cutover-audit.json)

## R6 真人生产证据

当时使用源码 Tauri 真机运行：

- 日志确认 `backend_dir=F:/projects/deskpet-harness-r55/backend`
- 在 startup complete 前出现 `product_harness_activated generation=1 phase=open`
- 从真实 `Desktop Pet` 窗口点击输入框并发送 `Please reply exactly: HARNESS-R6-FINAL-OK`
- UI 显示 `HARNESS-R6-FINAL-OK`
- 后端记录真实 relay HTTP 200 和唯一 final send
- 按精确 Tauri 根 PID 清理 20 个相关进程，释放约 `9,342.6 MB` private memory
- 清理后相关 PID 为 `0`，端口 `18100 / 15173` listener 均为 `0`

## 当时的 R7 剩余项

R6 文档生成时，完整主消息页 S-1～S-7 真人矩阵尚未完成，所以这里没有用单个 Text case 冒充全部 E2E。该剩余项随后已在 R7 完成，最终结果见 [R7 主消息线程真人验收](./evidence/r7-main-thread/results.md)。
