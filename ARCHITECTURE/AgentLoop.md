# Agent Loop 架构调研档

> 本档回答一个问题：**桌宠收到一句话后，agent 是怎么把一个任务跑完的？**
> 聚焦后端 ReAct 执行引擎（P6 重构后现状）。细节散在各 `plans/` 与 `openspec/`，本档只做一页式骨架 + 关键代码引用。
>
> 最后更新：2026-07-29 ｜ 调研基线：读码核实（master）｜ 主入口 [`backend/agent/agent_loop.py`](../backend/agent/agent_loop.py)
>
> 配套实施记录见 [`plans/2026-06-20-agent-loop-optimization/00-PLAN.md`](../plans/2026-06-20-agent-loop-optimization/00-PLAN.md)（缺陷审计、落地路线与验证结果）。

> **当前边界**：`AgentLoop` 已不再是产品入口或工具/子代理 runtime；它只是 ReAct Driver 内部的 LLM、上下文与完成判断引擎。Text/Voice 统一进入 Product Venue → `RunKernel`；顶层固定 `agent.general + react`；模型面的 `workflow_spawn` 工具已于 2026-09-09 下线（删 workflow 线 Slice 1，见 `plans/2026-09-09-remove-workflow-line/`），生产装配从未注册 Workflow Driver，图引擎与启动装配待 Slice 2 清理。当前没有 Code/普通模式之分，多任务只是同一主 Session 下并行的多个顶层 Run。工具批次由 ReAct Driver 的 `EffectBatchExecutor` 执行。下文第 2～6 节保留 AgentLoop 内部算法说明，第 7 节是当前生产装配。

---

## 0. 一句话概括

普通对话和短任务由 **ReAct Driver** 驱动 `AgentLoop`：`LLM 出招 → Driver 执行工具批次 → 工具结果喂回去 → 再问 LLM`。长任务由 Workflow Driver 执行。`AgentLoop` 本身只保留 provider、上下文和完成守门，不再直接拥有 ToolRegistry 分发、子代理队列或产品路由。

---

## 1. 当前分层（P6 的 AgentLoop 内核继续复用）

P6 留下的 TerminationGate、ContextManager 与 provider 适配器继续复用，R6 把产品入口、路由、工具执行和投影从 AgentLoop 外围收敛到 Harness：

| 层 | 文件 | 职责 |
|---|---|---|
| **Product Venue / Preparer** | `backend/main.py`、`deskpet/agent/turn_preparer.py` | transport、产品上下文准备、冻结 provider/model/capability/product payload |
| **RunKernel + Router** | `backend/deskpet/harness/` | 可信 Run 身份、一次路由、生命周期、signal/cancel/recover/close |
| **ReAct Driver** | `backend/deskpet/harness/drivers/react.py` | 驱动 AgentLoop，把 `ToolBatchEvent` 交给唯一 EffectBatchExecutor，再把 outcome 回填 |
| **AgentLoop** | `backend/agent/agent_loop.py` | LLM/provider 循环、上下文预算和完成守门；产出 token/final/tool batch，不执行工具 |
| **TerminationGate** | `backend/agent/termination.py` | 所有「该不该继续」的硬上限裁决（轮数 / 墙钟 / 工具预算 / 花费 / per-tool 幻觉） |
| **ContextManager** | `backend/agent/context_manager.py` | 所有「哪些消息进 LLM」的决策（预算检查 / 压缩 / 工具结果截断 + ref-store） |
| **ProviderAdapter** | `backend/providers/openai_compatible.py` 等 | httpx 线缆层；coordinated dispatch 下不做 handoff 后内部重试 |

> 扩展规矩：新的终止原因只走 `gate.record_*()`，新的上下文优化只加到 `ContextManager` —— **不要**再往 `main.py`/`agent_loop.py` 里散逻辑（这正是 P6 要还的债）。

---

## 2. AgentLoop 主循环执行流程

`AgentLoop.run()` 是个 `async generator`，对 `range(1, max_iterations+1)` 迭代。当前
Product Harness 主链显式传入 `50`；`AgentLoop` 构造器的 `20` 和 `build_agent` 辅助函数
的 `16` 只是未覆盖时的代码默认值，不代表不同产品模式。yield 出 `AgentEvent` 流：
`assistant_message` / `assistant_delta`（流式 token）/ `tool_call` / `tool_result` /
`final` / `error` / `provider_chain_fallback`。

**循环前（一次性）**：若 session 有活跃目标，注入一条常驻 `[目标锚定]` system 消息（WI-4a always-on，永不被压缩、整轮恒 ≤1 条，防任务漂移）。见 [agent_loop.py:683-690](../backend/agent/agent_loop.py)。

**每一轮**（以函数名为准，行号会随收敛变化）：

```
1. gate.allows_call()          ── 硬上限预检（轮数/墙钟/花费超 → ErrorEvent + return）
2. ctx.check_budget()          ── token 预算守门（BLOCK→退出并记 gate；WARN→记一次日志）
   + compressor 压缩(可选)      ── 数 working_messages 真实 token，过阈值压历史中段
   |                              （压缩前可选 pre-flush 任务态进 L1 文件记忆，跨 session 记任务）
   + self-check 注入            ── 第 10/20/30 轮注入递进式「该收尾了」system 提醒
3. LLM 调用                    ── 单 provider OR provider_chain 逐个 walk
   |                              （transient 失败 yield ProviderChainFallbackEvent 切下一个；
   |                               全挂 → ALL_PROVIDERS_FAILED；stream 失败回落非流式）
4. gate.record_turn()          ── 轮数++、累加 cost；记录 relay 真实 prompt_tokens 喂下轮压缩判定
5. yield AssistantMessageEvent

   ┌─ 若 stop_reason != "tool_use"（模型想收尾）→ 走【第 4 节·四道守门】
   |    全过 → gate.record_final_answer() + yield FinalEvent + return  ✅
   |    任一不过 → 注入 system 提醒 + continue（重新迭代）
   |
   └─ 否则（要用工具）：
        6a. 签名重复检测          ── 连续 ≥3 次同名同参 → 抑制 batch + 注入 nudge
        6b. 冻结 canonical messages / iteration / feedback state
        6c. yield ToolBatchEvent  ── AgentLoop 在这里暂停，不直接调用 Registry
        6d. ReAct Driver 经 EffectBatchExecutor 执行并返回规范化 tool outcomes
        6e. AgentLoop 恢复，把结果写回 working_messages，再进入下一轮

循环耗尽 max_iterations → gate.record_error(HARD_MAX_TURNS) + ErrorEvent(max_iterations)
```

关键点：循环不碰 WS/SessionDB，也不直接执行工具。LLM 网络调用在 ProviderAdapter；工具安全并发/unsafe barrier 在共享 EffectBatchExecutor；事件投影在 RunPresenter。

---

## 3. 工具注册与分发（`backend/deskpet/tools/`）

### 3.1 注册：单例 ToolRegistry + auto-discovery

- `ToolRegistry` 是模块级单例（[registry.py](../backend/deskpet/tools/registry.py)），内部 `_tools: dict[str, ToolSpec]` + 线程锁。
- `ToolSpec`（frozen）字段：`name` / `toolset`（分组，如 file/web/memory/control）/ `schema`（OpenAI function 格式）/ `handler` / `permission_category`（7 类）/ `concurrency_safe` / `timeout_seconds`（默认 60s）/ `replace_allowed`。
- **auto-discovery**：[`tools/__init__.py`](../backend/deskpet/tools/__init__.py) 用 `pkgutil.iter_modules` 遍历包，逐个 import，各工具模块在顶层 `registry.register(...)` 自注册；单个失败不中断。
- 同名冲突且双方都没 opt-in `replace_allowed` → 抛 `ToolNameConflictError`。
- `schemas(enabled_toolsets=...)` 导出 OpenAI 格式 `[{"type":"function","function":{...}}]`，多层过滤：`requires_env` → `enabled_toolsets` 白名单 → `disabled_toolsets`（严格禁用）→ `disabled_toolsets_schema_only` → `dangerous_tools_allowlist`。ContextAssembler 每轮决定 `enabled_toolsets`，只有子集对 LLM 可见。

### 3.2 分发：唯一 EffectBatchExecutor → `execute_tool`（V2）

R6 新请求不再调用 legacy `dispatch()`。ReAct/Workflow Driver 都经 Effect 边界准备并执行 V2 call，返回规范化 outcome：

```
查工具 → disabled_toolsets 检查 → 熔断器 can_call → 权限 gate.check
  → 会话上下文合并(_project_root 等注入) → handler 执行(async 直接 await / sync 丢 executor)
  → asyncio.wait_for 超时保护 → 结果 JSON 序列化 → envelope 包装
  → (可选)artifact 信息注入 → (可选)emit_receipt 发凭证 → 熔断器 record_call
```

并发策略：EffectBatchExecutor 按原顺序划分连续 safe segment；segment 内并发，`concurrency_safe=false` 是严格 barrier，前后 segment 不越过它。结果最终恢复到原 call 顺序。

### 3.3 权限弹窗 gate（R6 durable decision）

- [`permissions/gate.py`](../backend/deskpet/permissions/gate.py) 的 `PermissionGate.check()` 五层决策栈：
  **auto-mode 短路** → 敏感路径升级（read_file 命中 .ssh/.env/cookies 等正则 → read_file_sensitive）→ config deny 列表 → default-allow（read_file 免弹）→ 会话缓存命中 → **用户弹窗**。
- 权限粒度：按 **category**（7 类：read_file / read_file_sensitive / write_file / desktop_write / shell / network / mcp_call / skill_install），缓存键含 **params 形状哈希**（不含值）——「允许 shell」后同形状操作免重复弹窗。
- **R6 IPC 机制**：ReAct Driver 先把完整 command boundary 与 `OpenDecision` 持久化，Presenter 再向 Tauri 发 `permission_request`。前端 `permission_response` 通过 `RunKernel.signal` 做 decision nonce/version CAS；成功后 Driver 从同一 boundary 恢复，不能由进程内 Future 绕过 durable owner。超时/拒绝均形成显式 decision outcome。
- `auto_mode`（一键全允）持久化到 `permissions_auto_mode.json`，启动时恢复。

### 3.4 错误分类 + 熔断 + 动态搜索

- [`error_classifier.py`](../backend/deskpet/tools/error_classifier.py)：ValueError/TypeError/KeyError 等程序员错误 → `retriable=False`（PermanentToolError）；ConnectionError/TimeoutError/OSError → `retriable=True`（TransientToolError）；未知默认可重试。AgentLoop 的 `_classify_tool_result` 据此决定退出还是让模型重试。
- 熔断器 [`circuit_breaker.py`](../backend/agent/circuit_breaker.py)：CLOSED→（连续失败 3）→OPEN→（冷却）→HALF_OPEN→（probe）→CLOSED。OPEN 时返回中文 hint + 备选工具。
- `tool_search` 元工具 [`tool_search.py`](../backend/deskpet/tools/tool_search.py)：初始 curated toolset 不够时，LLM 按关键词搜全注册表（含 env-gated 隐藏工具），返回匹配 schema 供后续调用。

---

## 4. 四道「防 LLM 假装完成」守门

这是桌宠区别于裸 ReAct 的核心 —— 模型说「我做完了」时**不直接信**，`stop_reason != "tool_use"` 后依次过守门，任一不过就注入 system 提醒 + `continue`。对应 CLAUDE.md 反复强调的「压制 LLM 短路径偏置」在代码层落地。

| 守门 | 代码位置 | LLM 调用 | 输入 → 判定 | 不过怎么办 | 预算 |
|---|---|---|---|---|---|
| **completion_probe** | agent_loop.py:1325 | ✗ 纯规则 | 查 SessionDB code todos，status ∉ {completed,cancelled} 即未完成 | 注入「还剩 N 项 todo」system | 2 次 nudge |
| **VerifyGate** | agent_loop.py:1402 | ✓ 仅 ephemeral 救援 | regex 从 assistant_text 抽 claim → 对 receipt ledger 严格对账 | 注入 D8-schema rebound；2 次失败/stagnation→ephemeral 子代理；再不过 `verify_exhausted` 强退 | 2 nudge + 1 ephemeral |
| **goal_checker** | agent_loop.py（goal 块） | ✓ 每次 1 调 | LLM-as-judge：goal_text + 最近 5 轮 assistant 摘要 → `{done, hint}` | 注入 hint system | SessionGoal.max_iterations(默认10) |
| **external_evaluator** | agent_loop.py:1775 | ✓ 高后果 1 调 | 仅 high-consequence goal 触发；跨人格 QA 评质量分 0-10 + verdict | verify_exhausted 前最后救援；revise 则拦 | 成本护栏（<10% 目标触发） |

补充细节：

- **VerifyGate**：`mode ∈ {off, shadow, strict}`，off=总 pass（BC）。claim 提取基线见 [`STAGE0-claim-baseline.md`](../plans/2026-05-23-tool-last-mile-upgrade/STAGE0-claim-baseline.md)，RegexExtractor 带 ReDoS 防护。对账规则：claim 的 `pattern_id` 反查 `tool_hint`，ledger 须有 `tool_name ∈ tool_hint 且 ok=True` 的 receipt。
- **N1 信任面**：[`receipt_store.py`](../backend/deskpet/tools/receipt_store.py) `load_session` 时对每条 receipt 强制 **HMAC 验签**，sig-invalid 整条剔除（防伪造凭据骗过对账）。按用户 2026-09-01 要求，receipt HMAC key 只存应用私有 `userdata/secrets/receipt_hmac.key`（POSIX 0600），生产代码不再读取、写入或探测 OS Keychain/keyring；已有系统钥匙串条目不查询、不迁移、不删除。
- **goal_checker** 与 **external_evaluator** 都是 safe-fail：LLM 异常/parse 失败 → 降级（goal_checker 返 skipped 不默认通过；external_evaluator `conservative_on_error=True` 高后果时返 revise 保守拦）。
- 守门相关源码：[`verify_gate.py`](../backend/deskpet/agent/verify_gate.py)、[`goal_checker.py`](../backend/deskpet/agent/goal_checker.py)、[`external_evaluator.py`](../backend/deskpet/agent/external_evaluator.py)、[`goal_store.py`](../backend/deskpet/agent/goal_store.py)。

---

## 5. TerminationGate 内部实现

[`termination.py`](../backend/agent/termination.py)。`TerminationReason` 是唯一退出枚举，任何想停循环的路径都得调 `gate.record_*()`，这样事后读 `gate.summary()` 拿到连贯原因。

**GateConfig 阈值**（注意默认值经多轮调参后基本「禁用上限、靠幻觉检测兜底」）：

| 字段 | 默认 | 含义 |
|---|---|---|
| `max_turns` | 10000 | 轮数硬上限（原意禁用，保留硬切手段） |
| `tool_budget_hard` | 10000 | 工具调用总数硬上限 |
| `wall_clock_seconds` | `None` | 墙钟上限，None=禁用（用户要长任务一直跑） |
| `max_budget_usd` | `None` | 花费上限，None=无限 |
| `per_tool_max_consecutive` | **8** | 同工具**同参数**连续上限 → 真死循环防御主力 |

**GateState 计数器**：`turns_used` / `tools_used` / `cost_usd` / `per_tool_consecutive`（dict）/ `_per_tool_last_sig`（args 16 字符 MD5）/ `started_at` / `terminated` / `terminated_reason`。

**核心方法**：
- `allows_call()`：LLM 调用前查 已终止 / max_turns / wall_clock / max_budget。
- `allows_tool(name)`：分发前查 已终止 / tool_budget_hard / per-tool 连续 ≥8 → `HALLUCINATION_DETECTED`。
- `record_tool_call(name, args)`：**args-aware** —— 同工具同 args 签名 → 计数++；不同 args/首次 → 重置为 1；**调任何其他工具 → 其余工具计数全清零**（LangGraph 教训：「读 5 个不同文件」不该被误判死循环，只有「读同一文件 8 次」才算）。
- `record_turn(cost_delta)` / `record_final_answer()`（=terminate SUCCESS）/ `record_error(reason)` / `terminate()`（幂等）/ `summary()`。

**TerminationReason 全枚举**：`SUCCESS` / `USER_INTERRUPTED` / `HARD_MAX_TURNS` / `HARD_TOOL_BUDGET` / `HARD_WALL_CLOCK` / `HARD_MAX_BUDGET_USD` / `PERMANENT_TOOL_ERROR` / `ALL_PROVIDERS_FAILED` / `CONTEXT_BUDGET_BLOCK` / `HALLUCINATION_DETECTED` / `CIRCUIT_BREAKER_OPEN`。

真死循环三层防御：① per-tool args-aware 连续 8 次 ② ContextManager token budget（上下文过大天然中止）③ supervisor watchdog（盯 running 但无事件的真卡死）。

---

## 6. ContextManager 内部实现

[`context_manager.py`](../backend/agent/context_manager.py)，把 B1/B2/B3/G1 四个上下文优化收在一个 facade 后。阈值多按 model 的 context_window 动态算（v2 模式）。

**关键配置**（默认）：`tool_result_head=2500` / `tool_result_tail=800` / `skip_truncation_for_tools={"fetch_tool_result"}`（**G1 fix**）/ `compact_keep_recent=12` / `budget_warn_pct=0.80` / `budget_block_pct=0.95`。动态属性：`compact_at_tokens = window×0.75`、`tool_result_threshold = clamp(window//60, 6k, 12k)`、`compact_message_threshold = max(20, window//10k)`。

**三个主方法**：
- `check_budget(messages, model)` → `BudgetCheckResult{verdict(OK/WARN/BLOCK), estimated_tokens, context_window, ratio, advice}`。CJK-aware token 估算（汉字≈1 token，ASCII≈3.5 char/token）；window 解析：显式注入 → BUILTIN per-model 表 → legacy 表 → 8192 兜底。`ratio≥0.95→BLOCK`、`≥0.80→WARN`。
- `record_tool_result(tool_name, result)` → `(content_for_history, ref_id|None)`。若 tool ∈ skip 白名单（fetch_tool_result）→ 原样返回不截断（**G1 fix**：否则 fetch 回来的全文又被截，无限循环）；否则超 `tool_result_threshold` 就 `head + [truncated N chars; ref_id=xxx — use fetch_tool_result] + tail`，全文进 ref-store。
- `maybe_compact(messages, llm_for_summarize)`：在 **AgentLoop 之前**于 `chat_prep.prepare_chat_messages_for_chain` 调一次。`should_compact`（消息>阈值 或 字符>阈值）→ 保留 system 头 + 最近 keep_recent 尾，中段用 LLM 压成一条中文摘要 system 消息（≤600 字）。失败 → 返回原列表（宁可长也不丢历史）。

**全局 ref-store**（[`tool_result_truncator.py`](../backend/agent/tool_result_truncator.py)）：`get_global_ref_store()` 模块单例（LRU 256 + 磁盘 spill 到 `<user_data>/cache/tool_refs/<ref>.txt`）。两个用方共享：`record_tool_result` 写入截断全文；`fetch_tool_result` 工具按 ref_id 读回（支持切片）。单例而非按 session 隔离 → fetch 工具无需 session 参数，随机 8 字符 ref_id 即足够安全。

---

## 7. 当前生产装配

`backend/main.py` 只保留 ingress/transport、服务装配和产品适配。`chat`、`chat_v2` 与 Voice 都先建立可信 request/turn/session 身份，再进入同一个 `ProductVenueRunSession`：

```text
WS / Voice / Tauri
  -> ProductTurnPreparer
       history · persona · memory · skill/MCP · attachments · problem pipeline
       frozen provider/model/capability/product payload
  -> RunKernel.start()
       one route decision
       ReAct Driver OR Workflow Driver
  -> CanonicalRunEventPresentationAdapter
  -> RunPresenter
       SessionDB · WebSocket · TTS · UI
```

所有普通顶层消息都固定进入 `agent.general` 的 ReAct Driver。Driver 调用 `AgentLoop` 获取
provider 结果；若产生 `ToolBatchEvent`，由 Driver 交给唯一 `EffectBatchExecutor`，再把
规范化 outcome 回填给 AgentLoop。**2026-09-09 删 workflow 线 Slice 1**：模型面的
`workflow_spawn` 工具（冻结清单条目、`SDK_DIRECT_TOOL_KERNEL` / `PRODUCT_TOOL_NAMES` /
`_CONTROL_TOOLS` / `core_names` 登记、`_inject_profile_catalog` 提示词、
`orchestration_controls` 的 spawn 段、`execution_profiles` 的 `WorkflowSpawnRequest` /
`ProfileLaunchTicket` 死壳、`companion/workflows.py`）已全部删除，冻结清单 77→76 并重签
（`MANIFEST_SHA256 = df979c0e…`）。实测交付版 43 次 provider 请求里该工具本就 0 次出现，
生产装配只注册 react driver 与 `agent.general`。DeepResearch / PPT / durable_task 图引擎、
`main.py` 旧 `WorkflowLauncher` 启动器、前端 workflow 面板留待 Slice 2；Harness SDK 内的
spawn 协议（表、checkpoint 字段、public API）留待 Slice 3 随编排大改处理，SDK 钉版保持 0.7.10。
三个拒绝集（`CORE_RESERVED_TOOL_NAMES`、`_SKILL_SCOPE_WIDENING_CONTROLS`）与 `deny_selectors`
仍含旧名字：去名是放宽不是删除，随 followup F-WF-1 由用户决定。

provider、model、capability、session/workspace 与产品配置在准备/首次 route 时冻结。Context
OS 把本轮 `PreparedToolSet` 与 eligibility 序列化进 Run；root ReAct 与 ticket child
恢复时都重新校验 identity、policy、visibility 与 schema fingerprint，不能退回全局
工具目录。所有 Effect 使用同一 trusted `ToolExecutionContext`，执行时补齐 stable
call/effect id。Workflow context 与 ReAct provider-loop 都完成重建后才开放服务，不能
出现“health 显示 ready、实际恢复尚未接线”的窗口。

Tauri 生产发送端会为逻辑消息生成稳定 `client_request_id/client_turn_id` 并在重试时复用；后端据此找回现有 Run，避免重复追加用户消息。WebSocket 断开只取消 presentation subscriber，不取消 durable Run。Harness 已无常驻 Supervisor；恢复、child、delivery 与 late-ready 由 durable 事件触发同一个短生命周期 `HarnessReconciler`，积压处理完立即退出。旧 AutoResume 重新派发 `_run_chat`、AgentLoop 子代理 runtime、全局 completion queue 与产品专用旁路已删除。

---

## 8. 关键文件速查

### 当前 provider 与 capability 恢复边界

stable `launch_operation_id` 与首次 route 冻结的 provider snapshot 会显式送到真实 HTTP
request。支持幂等的 adapter 只接受显式 `header:<name>` capability；不支持幂等时，
传输重试、stream→nonstream 重放和跨 provider fallback 都 fail closed。root/child
恢复从 durable Context OS snapshot 重建精确 `PreparedToolSet`，先校验
eligibility/schema，再按冻结名称裁剪可见 schema，并拒绝 provider 返回 snapshot 外的
工具。

Context OS scope 的 TTL 是 orphan 回收时间，不是 provider 回合 deadline。ReAct Driver
进入每一次 `AgentLoop.run()` 前持有该 scope，直到该轮 provider emission 完整结束后释放；
恢复时若内存记录已经回收，只能从当前 Run 的 durable snapshot 重建并重新校验。工具批次
跨物理执行边界时另由 `EffectBatchExecutor` pin。这样即使模型思考超过 300 秒，下一步
`tool_activate` 也不会因为 Harness 自己回收了仍在使用的 scope 而伪失败。

每次 coordinated provider 调用先由 `ProviderInvocationCoordinator` durable claim，再由
transport 发出唯一物理请求。handoff 以后发生 timeout/cancel/断连时，AgentLoop 收到
`provider_dispatch_unknown_after_handoff`；这表示“云端结果未知”，不是 provider 已明确
失败。OpenAI-compatible adapter 在这个边界内不做内部重试。workflow schema v23 会把
未来 unknown/failed 的 exact exception type、reason 和有界 message 写入不可变 audit；
主消息页左侧 Harness 面板只读展示这些账本事实。

| 关注点 | 文件 |
|---|---|
| ReAct 主循环 | [`backend/agent/agent_loop.py`](../backend/agent/agent_loop.py) |
| 终止裁决 | [`backend/agent/termination.py`](../backend/agent/termination.py) |
| 上下文管理 | [`backend/agent/context_manager.py`](../backend/agent/context_manager.py) ｜ [`token_budget.py`](../backend/agent/token_budget.py) ｜ [`tool_result_truncator.py`](../backend/agent/tool_result_truncator.py) ｜ [`history_compactor.py`](../backend/agent/history_compactor.py) |
| 工具注册/分发 | [`backend/deskpet/tools/registry.py`](../backend/deskpet/tools/registry.py) ｜ [`error_classifier.py`](../backend/deskpet/tools/error_classifier.py) ｜ [`tool_search.py`](../backend/deskpet/tools/tool_search.py) |
| 权限 gate | [`backend/deskpet/permissions/gate.py`](../backend/deskpet/permissions/gate.py) |
| 守门 | [`verify_gate.py`](../backend/deskpet/agent/verify_gate.py) ｜ [`goal_checker.py`](../backend/deskpet/agent/goal_checker.py) ｜ [`external_evaluator.py`](../backend/deskpet/agent/external_evaluator.py) ｜ [`receipt_store.py`](../backend/deskpet/tools/receipt_store.py) |
| 装配 + WS | `backend/main.py`（`_run_product_harness_chat` / `_build_product_harness_stack` / `_build_product_agent_loop` / provider 解析 / 事件转发） |
| 架构原文档 | [`docs/P6-agent-loop-architecture.md`](../docs/P6-agent-loop-architecture.md) ｜ [`docs/P6-migration-decisions.md`](../docs/P6-migration-decisions.md) |

> ⚠️ 第 2～6 节中的算法行号来自 2026-06-20 调研，只能作为 AgentLoop 内核定位；R6 生产装配必须以第 7 节和上述 `_run_product_harness_*` 入口为准。
