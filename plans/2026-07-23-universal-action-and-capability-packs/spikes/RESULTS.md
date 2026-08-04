# Capability Platform Spikes

> 状态：五项关键假设验证完成  
> 日期：2026-07-24  
> 所有脚本仅位于本 plan 的 `spikes/`，未修改生产模块。

## SPIKE-A：同 run ReAct capability 重新绑定

命令：

```powershell
.\backend\.venv\Scripts\python.exe `
  .\plans\2026-07-23-universal-action-and-capability-packs\spikes\spike_a_react_refresh_seam.py
```

结果：**部分通过，并发现一个必须修复的 durable gap**。

- `AgentLoopCollaborator.start()` 每次都会重新读取 `active.driver_runtime` 中的 options。
- 同一个 `run_id` 下，将 prepared context 从 revision 1 换成 revision 2，并将 capability snapshot 从 `old.tool` 换成 `old.tool + spike.echo` 后，下一次启动能收到新 context 和新 `tool_names_filter`。
- 因此“batch settle 后重新绑定 live prepared context，再继续同一 root run”的热路径 seam 可用。
- 但 `ReactCommandBoundary.to_start()` 当前返回空 `request_payload`；原 `context_os` 只在第一次 `DriverStart` 中存在。仅改内存会导致 backend restart/recovery 无法从 durable boundary 重建新 tool set。

计划修正要求：

1. refresh 不得只改 live tuple；
2. `ReactCommandBoundary` 必须持久化 canonical `request_payload`，或持久化一个真的可解引用的 prepared/tool-set snapshot ref；
3. refresh 必须在同一 continuation CAS 中写入新 snapshot ref、capability snapshot 和 generation；
4. recovery 测试必须先丢弃整个 live index，再从 SQLite 重建并调用新工具。

## SPIKE-B：Windows JSON 子进程与精确进程树清理

命令：

```powershell
.\backend\.venv\Scripts\python.exe `
  .\plans\2026-07-23-universal-action-and-capability-packs\spikes\spike_b_local_runtime.py
```

结果：**通过**。

- `create_subprocess_exec + communicate()` 成功完成单 JSON request/response。
- stdout 与 stderr 可分离；request id 可核对。
- 非 JSON stdout 能稳定分类为 malformed。
- 被取消 helper 创建了 descendant；按记录的 parent PID + creation time 精确枚举并清理目标树。
- 另一个运行同一 helper 文件的无关进程在目标清理后仍存活，证明未按进程名广泛结束。
- 目标树匹配 PID 全部归零。
- 首次目标树清理报告释放 private memory：`20,283,392 bytes`（约 `19.35 MiB`）；
  2026-07-24 复跑释放 `20,275,200 bytes`（约 `19.34 MiB`），差异来自运行时页，
  两次匹配进程都归零。
- spike finally 又按自身记录的 identity 清理无关 fixture；结束后再次查询，没有匹配的 Python spike 进程残留。

对生产计划的约束：

1. runtime lease 至少保存 PID、creation time、command line、root run、effect id；
2. 子进程树清理只能从该 identity 向下枚举；
3. 测试必须同时运行一个同名无关进程作为 negative control；
4. timeout、cancel、backend shutdown 都复用同一 cleanup primitive。

## SPIKE-C：Auto decision 不投影 waiting、仍走原子 decision/grant

命令：

```powershell
.\backend\.venv\Scripts\python.exe `
  .\plans\2026-07-23-universal-action-and-capability-packs\spikes\spike_c_auto_decision_seam.py
```

结果：**通过，并把 Auto 的职责收窄为 decision policy interceptor**。

- manual 路径仍投影 `decision / waiting`。
- Auto interceptor 在 waiting live event 投影前命中规则，所以没有弹出授权 UI。
- 它没有绕过已有 durable 语义：decision 从 `open` 变为 `allowed`，并创建绑定该
  decision 和同一 nonce 的 exact grant。
- 丢弃旧 driver 后，新 driver 能从持久化事件恢复，继续得到
  `persisted_event / execute_tools`。

对生产计划的约束：

1. Auto 只自动解决授权类 decision，不吞澄清、登录、验证码、支付或 UAC；
2. manual/auto 共用 decision、grant、nonce 和 fenced signal；
3. crash 落在 open 与 signal 之间时，由 reconciler 幂等补齐；
4. 只有 UI waiting projection 被抑制，审计事件不能省略。

## SPIKE-D：动态 MCP server 生命周期

命令：

```powershell
.\backend\.venv\Scripts\python.exe `
  .\plans\2026-07-23-universal-action-and-capability-packs\spikes\spike_d_dynamic_mcp.py
```

结果：**底层动态 add/call/remove 通过，同时发现并定位当前 teardown 的真实缺陷**。

- 真实 FastMCP stdio server 动态注册 2 个工具，echo 调用成功。
- 移除后 server 与工具都消失；ToolRegistry revision 单调变化：
  `0 → 2 → 4 → 6 → 8`。
- 第二个 server 在 tool call 中自行退出时，调用返回结构化
  `mcp_call_failed`，manager 进入 `reconnecting`，死 server 的工具立即下线，
  spike/backend 本身继续运行。
- 第一次直接调用当前 `MCPManager._teardown_runtime()` 失败：
  `asyncio.wait_for(exit_stack.aclose())` 创建了不同 Task，AnyIO 报
  `Attempted to exit cancel scope in a different task than it was entered in`。
- 改用 spike-only 的同 Task teardown 后，正常 remove 和 crash cleanup 都无错误。
- 运行结束后按精确 command line 查询，没有
  `spike_d_mcp_server.py` Python 进程残留；未进行广泛进程名结束，也没有需要
  强制释放的残留 private memory。

对生产计划的约束：

1. 每个 MCP server 由一个 lifecycle-owner task 创建、关闭 transport/session；
2. add/start/stop/reconnect 通过命令 queue 驱动 owner，不跨 Task 退出 AnyIO context；
3. ToolRegistry register/unregister 和 lease/refcount 必须跟 lifecycle phase 对齐；
4. 专门测试 enter/exit task identity，以及 stop/crash/reconnect/backend shutdown。

## SPIKE-E：失败恢复 seam 的 gap characterization

命令：

```powershell
.\backend\.venv\Scripts\python.exe `
  .\plans\2026-07-23-universal-action-and-capability-packs\spikes\spike_e_failure_replan_seam.py
```

结果：**只完成当前 seam 的 gap characterization；普通工具路径可复用，child
failure 暴露必须修复的 provider backfill gap**。

- 普通 `run_shell` 失败已经由 `ReActDriver._tool_messages()` 生成 canonical
  `role=tool`，并保留原 `tool_call_id`。同一 AgentLoop 因而可以读取真实错误并改变
  下一批工具调用。
- 当前 child terminal failure 仍由 `_apply_child_inbox()` 追加
  `role=system/type=host_child_response`；该消息没有原 provider tool call id。
- 当前代码在同一 ack 中清除 `pending_delegate`。因此如果直接把专用 Workflow
  挂到模型的 `workflow_spawn` tool call，失败后既没有合法 terminal tool result，也
  无法证明重启时只回填一次。
- 这验证了 v0.3 的 `PreparedControlDelegate + TaskFailureReport +
  ControlDelegateBoundary` 是必要缺口：必须保存 assistant tool-call、原 call id、
  child failure/checkpoint/evidence，并在父继续前恰好一次回填。
- spike 不启动外部 worker；运行结束后按精确 command line 查询，
  `spike_e_failure_replan_seam.py` 匹配进程为 0，无需释放额外 private memory。

已执行范围仅为 `ReActDriver._tool_messages()`、`_apply_child_inbox()` 与最小 FakeUow。
没有运行真实 Product composition，没有让 provider 接收失败后返回第二个 action batch，
也没有验证新 Attempt、SQLite crash recovery、provider ambiguity 或 loop guard。因此
本结果不能作为目标 exactly-once 恢复协议通过的证据；这些属于 WI-1R 强制验收。

对生产计划的约束：

1. 普通工具失败继续复用现有 canonical tool-result resume，不另建旁路。
2. `workflow_spawn` 必须先成为正式 PreparedControlDelegate，不能沿用普通
   `DelegateRun/host_child_response`。
3. child failure ack、FailureReport 和 pending provider backfill 必须形成 durable
   exactly-once 状态；重启先补 backfill，再请求模型。
4. 新 Attempt 必须由模型实际收到 FailureReport 后产生，测试不能直接注入“第二次成功”。

## 总结

SPIKE-A～D 完成各自声明的 disposable 验证；SPIKE-E 只完成 gap
characterization。SPIKE-A、SPIKE-D 与 SPIKE-E 发现的 durable request payload gap、
AnyIO lifecycle-owner gap 和 child failure provider backfill gap 已反向写入主计划；
spike 文件只用于证据，不作为生产实现复用。2026-07-24 最终按本 plan 目录 +
`spike_*` 精确 command line 复查，匹配 Python 进程为 0。
