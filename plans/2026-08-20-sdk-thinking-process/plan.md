# Plan：SDK 公开思考过程与终态自动折叠

## 主要矛盾

- 决定成败的核心问题不是增加一个折叠箭头，而是让 SDK tool-turn 中“模型主动公开的工作叙述”
  与真实工具生命周期共享 canonical Run 身份，并在不暴露隐藏 CoT 的前提下形成可持久化、可恢复、
  可由终态驱动折叠的单一 UI 分组。
- 典型链路证据：`backend/deskpet/sdk_adapters/provider.py` 当前只返回 normalized
  `ProviderResponse`；`tools.py` 调用 `ProductDeliveryAdapter.present_tool_call/result`；
  `run_presenter.py::_present_tool_call` 已能把同 iteration 的公开 assistant content 转成
  `reasoning_summary`；`ChatView.tsx` 当前丢弃 summary/tool 的 `run_id`；
  `MessageStreamPanel.tsx::PublicProgressRow` 当前逐条平铺，无 Run 级折叠。

## 关联验收标准

- 覆盖 `acceptance.md` 的 AC-8、AC-9，以及既有 AC-2、AC-3、AC-4、AC-5、AC-6、AC-7。
- Feature policy：only-add；原有工具卡、最终回复、隐藏工具/进度开关和 Harness Inspector 保留。

## 本项目适配与方案取舍

- 采用参考图的“运行中展开、终态显示耗时并折叠、点击再展开”状态模型；本项目已有
  `run_projections` 终态 authority，直接复用，不另建状态机。
- 不照搬“显示原始 CoT”：本项目 assurance contract 的 `FAIL-4` 与既有架构明确禁止公开
  `reasoning_content`。只展示模型主动放入普通 assistant content 的工作叙述，以及已经公开投影的
  工具调用/结果。
- 不只做前端合并：若 SDK tool-turn 的公开 content 不进入 presenter，历史恢复仍为空。必须补齐
  Provider→Delivery 接线，并继续用 `context_visibility=exclude` 持久化公开摘要。
- 不把工具轨迹复制成第二份：同一 Run 的现有 tool cards 移入思考分组，避免页面同时出现一份
  折叠区和一份重复工具卡。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/sdk_adapters/provider.py` | OpenAI-compatible Provider bridge | 对含 tool calls 的公开 assistant content 做 best-effort Run delivery 捕获；忽略隐藏 reasoning |
| `backend/deskpet/sdk_adapters/delivery.py` | SDK→RunPresenter | 暂存公开叙述、分配 tool iteration、在 tool call 前投影 AssistantMessageEvent |
| `backend/tests/sdk_adapters/test_product_host_ports.py` | Provider/host port 契约 | 验证公开叙述接入且隐藏 reasoning 不进入公开路径 |
| `backend/tests/test_product_delivery_adapter.py` | Delivery 投影 | 验证 narration→tool call 顺序、iteration 和持久化触发 |
| `tauri-app/src/views/ChatView.tsx` | Session message→stream projection | 为 user/assistant/progress/tool 保留 `runId` |
| `tauri-app/src/components/MessageStreamPanel.tsx` | 主消息流 | 新增 Run 级思考分组、耗时、自动展开/折叠和手动 toggle |
| `tauri-app/src/components/MessageStreamPanel.workflow.test.tsx` | UI 行为契约 | 覆盖 running/terminal/history/点击/缺时间/重复摘要 |

真实 DeepSeek 载荷校正：工具 turn 可能只有公开 tool call/result 而没有公开 assistant narration。
这类 Run 仍按 canonical Run 形成思考分组，因为工具事件本身就是公开执行过程；只有 progress/tools
均为空或均被用户隐藏时才不渲染空组。隐藏 `reasoning_content` 不作为分组条件。

## Assurance / 信任与失败边界

- Profile：standard；绑定 `ASSET-1..3`、`TRUST-1..2`、`FAIL-1..5`。
- 入口链：DeepSeek `/chat/completions` → SDK Provider adapter → per-Run delivery adapter →
  RunPresenter → SessionDB/control WS → sessions store → ChatView → MessageStreamPanel。
- 数据边界：普通 assistant `content` 可成为 public narration；`reasoning_content`、reasoning token、
  provider 原始 payload 在 Provider adapter 停止追踪，不进入公开消息。
- 持久化：reasoning summary 继续是 `projection_kind=workflow_progress`、
  `context_visibility=exclude`；下一轮上下文装配不读取它。
- 最大影响：投影失败只能少显示思考区，不得影响 Provider 结算、工具执行、Run 终态或最终回复。

## 任务清单

### Task 1 — 接通 SDK 公开工作叙述 [AC-8]

- 在 Provider 响应含 `tool_calls` 且 `message.content.strip()` 非空时，从 request id 解析 SDK Run id，
  best-effort 调用已注册 delivery adapter 的 capture 方法；任何投影异常只记安全日志并返回原响应。
- Delivery adapter 按 Provider turn 暂存 narration 与本轮全部 call IDs；同一响应的多个 tool call 共享
  同一稳定 iteration，并各自保持 call/result 映射。首个实际 ToolCall 前只投影一次无 tool_calls 的
  `AssistantMessageEvent`，再投影全部 `ToolCallEvent`，复用 RunPresenter 既有 public sanitizer/持久化。
- 验证：Provider mock + Delivery AsyncMock；Delivery 单测断言事件顺序、同 turn 多 call/交错 result
  iteration、空 narration 不产出、下一 Provider turn 不与上一轮 iteration 冲突。

### Task 2 — Run 级思考分组 UI [AC-9]

- `ChatStreamMessage` 增加 `runId`；ChatView 从所有相关 Message 保留 canonical `run_id`。
- `buildRows` 按 runId 聚合 progress/tool，首次出现位置作为 anchor，保持分组间时间顺序；无 runId 的
  旧消息沿用平铺降级。
- 新 `ThinkingProcessGroup`：running/waiting/starting 默认展开并每秒更新耗时；completed/failed/
  cancelled 初始及状态切换时自动折叠；点击 header 可覆盖展开状态；Run 再次 running 时清除 override。
- body 渲染公开 narration 与原有 ToolCallCard/ToolResultCard；有界高度和内部滚动；空组不渲染。

### Task 3 — 恢复、变异与回归 [AC-8, AC-9]

- hydration 的 durable reasoning summary 与 tool messages 按 runId 重新聚合；terminal projection 缺失时
  以 completed summary 安全降级为终态，时间缺失时显示“已完成”。
- 测试 running 展开、terminal 自动折叠、点击再展开/收起、重复 summary 幂等、缺时间、隐藏工具/
  进度开关、旧无 runId 消息、最终回复顺序。
- fresh-run 历史组装必须显式排除 `context_visibility=exclude` 与非 conversation projection；用公开 summary
  和隐藏 reasoning canary 捕获下一轮 Provider messages，证明 UI 摘要、耗时和隐藏内容零回灌。
- 通过真实 control WS/store hydration fixture 验证同一 terminal Run 的晚到 running 不复活，failed/
  cancelled/completed 均默认折叠；真正重跑必须使用新的 canonical Run ID。
- 运行 backend 聚焦 pytest、前端 Vitest/typecheck、affected surface smoke；最后用 Computer Use
  真实发送多工具请求，分别截取运行中展开、终态折叠、点击再展开，并用日志/DB核对。

## 执行期 plan defect 回炉（2026-08-20）

- A2-1（contract-conflict）：原“暂存给下一个 tool call”的单槽方案没有定义一个 Provider response
  含多个 calls 的归属，导致后续 turn iteration 冲突。修订为 Provider turn→call IDs 显式映射。
- A2-2（owner-missing）：原计划假定既有 `context_visibility=exclude` 会被 fresh-run context assembler
  自动尊重；审计证明 `_assemble_sdk_messages` 只按 role 过滤。修订 Task 3，明确由该边界过滤并测试。
- 两项均不改变 SDK wheel、Provider authority 或 UI 产品范围，只补齐 AC-8 的既定隔离与稳定身份契约。

## 停止追踪点

- 不修改 SDK wheel 的 Provider 协议或 ReAct driver。
- 不显示/持久化原始 `reasoning_content`。
- 不改 Provider 选择、权限决策、工具执行 authority、Context assembler 或 Harness Inspector 状态机。
