# Context OS V1 挑战迭代记录

> 只记录本轮 plan-only 阶段的审查与闭环；没有实现/测试结果。

## 架构挑战

### Architecture Round 1 — FAIL

阻断项与处理：

| 问题 | 处理 |
|---|---|
| 误把 BudgetAllocator 类默认 200K 当生产值 | 基线修正为生产 `32K × 0.6`，并把与实际 provider/model 脱节列为缺口 |
| 误把跨轮 L2 工具组当完整存量能力 | AC-CTX-3 改为新增完整 `assistant.tool_calls + tool results` round-trip |
| 漏掉 Assembler 后和 AgentLoop 内 context producers | 补齐调用链、producer inventory 与 prefix/transcript/control 三种 placement |
| compressor 按 system role 整体前置 | 在 AC 与 Task 3.2 增加 causal placement/compact 顺序验收 |
| Assembler schemas 不等于实际出站 schemas | report owner 移至即时 provider attempt 边界 |
| provider chain window/usage 未按 attempt 绑定 | 公共裁剪用最小安全窗，每次 attempt 重算并用 request/attempt id 回填 usage |
| budget BLOCK 早于 compressor | gate 顺序改为 estimate → pruning → preflush/compact → re-estimate → final BLOCK |
| flush 失败且已超预算时语义冲突 | 明确先可逆 pruning，仍超限则返回可恢复 budget error，不静默 compact/超限发送 |
| 把 code/web/research 当独立 venue | 基线矩阵修正：text/code 共用主入口，web/research 是 loop 工具 scope，voice 才是第二入口 |

### Architecture Round 2 — FAIL

| 剩余问题 | 处理 |
|---|---|
| capability gate 在 Assembler 前可调用 LLM 并短路，auxiliary provider calls 边界未定义 | report 增加 `purpose`；所有 call 有基础 attempt report，用户响应 call 另带完整 context 因果链 |
| Persona frozen 文本含 model/base URL/project root | Task 1.1 拆稳定身份、attempt diagnostics 与 task/path fragment |
| 只盘点 producers，未给收口任务 | 新增 Task 0.3，统一 prefix/transcript/control metadata 与 wire stripping |
| Task 3.3 要求 report，但 report 到 Task 4.3 才实现 | Task 3.3 只建立 attempt-planner seam；Task 4.3 显式依赖 3.3 并实现 report |

### Architecture Round 3 — PASS

Round 2 四项均关闭：辅助 provider call 分栏、Persona 稳定拆分、producer placement/causal 收口、
attempt-planner 与 report 依赖顺序均已进入验收和可执行 Task。

## 计划挑战

### Plan Round 1 — FAIL

| 问题 | 处理 |
|---|---|
| context metadata 载体留到 spike 决定 | 固定 message 顶层 `__deskpet_context` + wire deep-copy stripping + ContextVar attempt options |
| `task_scope_id` 和普通任务 producer 未定义 | 固定 workflow→goal→effective session precedence；只认 explicit new/结构化证据，不做语义自动切题 |
| reserve 可能重复扣，附件无合同 | 固定唯一预算公式；附件先归一化为 message block，ref 只归因不二次计数 |
| attempt report 无 store/state/retention | 固定 `ContextAttemptStore` 状态机、matching usage、32 条/30 分钟 ring、session purge |
| 总回退开关太晚且各 Task 可能绕过 | Task 0.2 先建未完成期 OFF 总闸；每个行为 Task 有 OFF golden，Task 5.1 验收后一次翻 ON |
| 文件清单和性能验收不闭合 | 换成真实文件/函数；新增 legacy/on 同脚本 P95 基线与 ≤20ms 判据 |
| Task 0.3 过大 | 拆为 0.3A prefix/wire 合同与 0.3B transcript/control causality |

### Plan Round 2 — FAIL

| 剩余问题 | 处理 |
|---|---|
| purpose ContextVar 缺每个辅助调用的设置点 | 列出 capability gate/classifier/planner/compressor/agent/force-finish 的真实函数与 scope |
| Task 4.1 remount 未依赖 3.2 compact 改造 | 正文与依赖图均增加 Task 3.2 |
| Task 3.1/2.3 wiring 文件不准确 | 修正到 assembler `build_default_assembler`、main module construction、`build_agent`、AgentLoop、voice wiring |
| 前端命令从无 package.json 的仓库根运行 | 固定为 `npm --prefix tauri-app ...` |
| 8K fixture 可能被 8192 output reserve 压成负预算 | 正常 fixture 固定 `max_tokens=1024`；8192 组合单独验 final BLOCK |

### Plan Round 3 — PASS

无剩余 Blocker/Major。两条 Minor 已吸收：Task 0.1 明确把双 owner 记为 red baseline；provider seam 的
`purpose=unclassified` contract test 是权威护栏，`rg` 仅作辅助 inventory。

## 用户 Review（2026-07-13）

五项全部按推荐方案通过：持久化 snapshot、允许结构化证据驱动的普通任务 snapshot、Wave 4
code/workspace 路径规则、单一总回滚开关、8K 强制 compact + 默认窗口复测。计划进入“review 已锁定、
等待明确执行授权”状态。

### Review 补充决策 6/7

用户追加确认：压缩模型可在设置中选择且默认 `follow_session`；单 Session 能放下则全量 raw，超窗后
必须由 coverage tree + raw tail + 当前 Session page-in reference 无盲区覆盖。新增 AC-CTX-15/16，
计划重新打开一轮范围复审，仍未授权实现。

### AC-CTX-15/16 补充范围复审 — PASS

本轮针对新增范围完成合同审计并关闭以下问题：

- 冻结 `follow_session` 与显式专用模型的不同 fallback 语义，消除“未来成功的 response attempt”时序悖论。
- coverage 只覆盖 eligible 原始 transcript rows，排除旧派生 summary，current row 按 message id 精确去重。
- reference 不能冒充内容覆盖；每条消息必须由 raw 或有效 summary 恰好覆盖一次，summary 再携带 page-in。
- 固定 level-0 raw index、分层相邻 summary、source hash/stale/CAS、tokenizer estimate cache 和 Session 越权边界。
- planner 只产出不重叠 compaction jobs，单一 compressor owner 可在一个 cycle 执行多个 segment jobs，
  避免与“单一有损 owner”冲突和 planner/compressor 循环依赖。
- 旧大 tool payload 的 outcome/ref 折叠也纳入 summary coverage，禁止 prune 后形成历史盲区。
- 自动化、ContextTrace 与 Windows E2E 已新增全量 raw、超窗 gap=0、page-in、模型选择/失败证据。

结论：无剩余 Blocker/Major；计划仍处于“review 已锁定、等待明确执行授权”。

## 工具能力平面补充挑战（AC-CTX-17～19）

### Tool Plane Round 1 — FAIL

| 阻断问题 | 关闭方式 |
|---|---|
| bridge handler 没有安全 scope 传递接口 | 冻结 kw-only `ToolExecutionContext` + host-only ContextVar；sync executor 用 `copy_context().run`，scope 不进入 LLM params |
| activation 的 local set/scope/result/snapshot 无原子边界 | 增加 per-scope lock + CAS，冻结 candidate 重预算、active-task snapshot CAS、scope/local commit、最后 append result 的顺序与崩溃语义 |
| 初始 resolve/activation 不一定写入 snapshot | active task 在 initial prepare 和每次成功 activation 都 CAS 写工具摘要；闲聊不创建空 snapshot，只写 attempt report |
| describe/activate 只比 hash，动态 eligibility 可失效 | describe、proposal、commit、每次 provider attempt、execute 前均重跑同一 eligibility/deny/mode/provider 复核；stale fail closed |
| compact/replan 可能第二次调用 Resolver | 拆成 `prepare_initial()` 与 `replan(existing_tool_set=...)`；后者只验证/预算，不重新选择 catalog |
| ON 仍可能退 legacy `dispatch()` | AgentLoop ON 构造与 dispatch 都要求 capability-aware execute contract；缺失直接 fail closed，OFF 才保留 legacy fallback |
| 当前 AgentLoop 并发 gather 使 activation batch 语义不成立 | 采用 exclusive-turn invariant：含 `tool_activate` 的 assistant response 必须只有一个 call，混批在 dispatch 前整体拒绝 |

新增 Minor 同步关闭：Task 4.3 依赖 4.0；direct MCP 每 attempt stale 校验；bridge 服从 deny/global policy；
Task 5.3 完成信号扩为 AC-CTX-14～19。

### Tool Plane Round 2 — FAIL

| 剩余问题 | 关闭方式 |
|---|---|
| PreparedToolSet 只有 names，无法 immutable 出站/复核 | 增加 `PreparedToolCapability(ref + exact schema)`；direct/activated 都持 exact copy，deferred 才只存 ref |
| history 是否需要 page-in 与 schema cost 形成预算环 | 一次 catalog resolve 产 `ResolvedToolDraft(base + conditional page-in)`；先算 fixed/base tools，再按 full-raw fit 用同一 draft finalize，绝不二次读 catalog |
| Task 3.1 引用了尚未创建的 ContextAttemptStore | Task 3.1 仅把 canonical facts 留在 PreparedContext/active snapshot；Task 4.3 才在 provider attempt seam 创建 report/store |
| 删除 `_bundle_tool_names` 会破坏短追问图片拦截 | 改读 finalized `PreparedContext.tool_set.has_direct("generate_image")` |
| ToolComponent 特殊语义可能丢失 | 冻结 forced deepresearch intent、短追问 generate_image deny、web_search conditional grounding 及 decisions 字段 |
| snapshot 在 fallback 前不知道 actual adapter | initial/activation 先写 canonical set 且 adapter 为空；Task 4.3 每个 sent attempt 前 CAS 回填 matching adapter/wire hash，失败不出站 |

额外收紧 provider-neutral canonical set 与 adapter-specific `PreparedToolPayload`：真实 request builder、预算与
report 消费同一 payload，adapter 不得静默过滤/改名。

### Tool Plane Round 3 — FAIL

| 剩余问题 | 关闭方式 |
|---|---|
| `visible_when` 仍是无参全局谓词；A Session 的 active goal 可让 B Session 看见 goal tools | 新增冻结的 `ToolEligibilityContext(session_id, request_id, task_type, mode)` 与 `visibility_scope`；session predicate 全链使用同一 context。复读实现后确认现有 `get_active_goal_context(session_id)` 在 miss 时仍全局回退，因此新增 strict `get_active_goal_context_for_session()`，并增加 A/B 隔离测试 |
| Resolver/describe/activate 仍提到 provider restriction，与 provider-neutral PreparedToolSet 冲突 | 责任重划：Resolver/bridge 只处理 host/session eligibility；provider adapter 只等价表达 canonical set，不支持时淘汰当前 attempt，logical set/scope 不变 |
| 当前 tools config provider 异常会 warn 后继续，Context OS ON 形成 fail-open | 新增 strict immutable `ToolPolicySnapshot`；resolve、describe/activate、execute 读取失败返回 `tool_policy_unavailable`，fingerprint/eligibility 变化 stale；仅 OFF 保留 legacy warn-and-continue |

同步关闭 Minor：实施图补入 `4.0 → 4.3`；selector 的 MCP/plugin discoverable 明确为两个 tuple entry；
E2E 新增已 direct/activated MCP 在下一 provider attempt 前 unregister/replace 的 fail-closed case；active task
首次 snapshot 使用 revision=0 的 create-or-CAS 完整 projection，不再假定 row 已存在。

### Tool Plane Round 4 — FAIL

Round 3 指定项已全部关闭，但最终一致性审查发现一个新的 Major 与一个 Minor：

| 剩余问题 | 关闭方式 |
|---|---|
| 原地把 legacy `tools` 迁移成 direct/discoverable 后，OFF 无法无歧义恢复旧 names/order | 改为并列双字段：原 `tools` 原样保留且仅供 OFF；新 `tool_exposure` 仅供 ON。八类 task 增加迁移前 names/order golden，禁止从新结构反推旧集合 |
| activation 的 DB CAS → scope CAS 与“任一步失败均不留状态”冲突 | scope lock 内先完成 revision/policy/budget/serialization 等全部可失败工作；DB CAS 后只允许无 await、无二次校验的 `commit_prevalidated()`/local assignment。接受硬崩溃留下非权威 diagnostic row，并用 fault-injection/spy 固定无可恢复失败区间 |

本轮后继续进入 Round 5；只有无 Blocker/Major 才更新总状态为 PASS。

### Tool Plane Round 5 — FAIL

Round 4 两项已关闭，但 AC-CTX-19 的异步持久化协议仍有两个 Major：

| 剩余问题 | 关闭方式 |
|---|---|
| Task 4.3 把 capability `scope_revision` 当成 snapshot DB `expected_revision`，且没有传递 CAS 返回的新 row revision | 新增独立 `ContextSnapshotHandle`/`SnapshotWriteReceipt`；DB API keyword-only 接收 `expected_row_revision`，scope record 持有并逐次更新 handle；tool scope revision 只作为摘要字段，永不参与 DB CAS |
| DB 已 commit、CAS coroutine 尚未 return 时收到 cancellation，会留下 outcome unknown | 所有 snapshot write 统一经 `await_snapshot_commit_ack()`：shield 底层 task，取消时先等待 terminal 并取得 receipt/rollback outcome；已提交但未激活/未发送则记录 diagnostic-ahead/prepared + cancelled 状态，再传播取消；增加 commit 前/后/重复 cancel fault tests |

本轮同时纠正真实基线：现有 `get_active_goal_context(session_id)` 在本 session miss 时仍全局 fallback，故 ON
改用新增的 strict `get_active_goal_context_for_session()`，旧方法仅留 OFF。进入最大轮次 Round 6 最终判定。

### Tool Plane Round 6 — PASS

无 Blocker/Major。最终核查确认：

- capability revision 与 DB row revision 已完整分域，keyword-only CAS、handle/receipt 传递和 concurrent
  projector conflict 路径闭环；
- initial/activation/attempt 都有 commit-ack settlement 与重复 cancellation 测试，持久化 diagnostic 不会恢复
  capability scope；
- snapshot 的 canonical/prepared 与 ContextAttemptStore actual sent 分离；
- legacy `tools`/ON `tool_exposure`、strict session goal lookup、provider-neutral payload、strict policy fail-closed、
  MCP stale E2E 均闭环。

唯一 Minor 已吸收：`sent` 固定在公共 transport coroutine 取得控制权后的第一条同步语句，并新增 adapter CAS 后、
transport marker 前取消以及 marker 后取消的边界测试。最终 verdict：`VERDICT: PASS`。
