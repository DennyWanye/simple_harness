# DeskPet Context OS V1：第一版实施计划

> 状态：**历史计划已获执行授权；Context OS 核心实现与 E2E-CTX-01～11 PASS，E2E-CTX-12 因 relay VR-0 BLOCKED，整体 NOT COMPLETE**  
> 基线：`architecture-baseline.md`；验收：`acceptance.md`  
> 计划日期：2026-07-13；校准 HEAD：`e4a3bdc7528066e7d6a290004eeeb2eee5e5edf6`  
> 历史边界：本文最初为 plan-only 交付；后续已按用户授权进入实现与测试，当前执行结果见 `results.md`，STATUS 只记录部分完成与阻塞，不晋升为整体 complete。

## 0. 一句话决策

在现有 `ContextAssembler → ContextManager → AgentLoop → provider` 链路上建立统一的
**Context Fragment + Prepared Request + PreparedToolSet** 合同：Assembler 负责“选择与编排”，
`ToolCapabilityResolver` 负责一次性冻结 direct/deferred/denied 工具能力，一个共享预算器负责
“整请求计量与裁剪”，`ContextCompressor` 独占“有损压缩”，现有 Goal/Workflow/Receipt/Artifact
继续作为事实源，新增的 task snapshot 只做带 revision 的派生投影和压缩前写回。

这不是另起一套 Agent runtime、记忆库或工作流引擎。

## 1. 要解决的核心矛盾

当前系统已经分别具备 L1/L2/L3、Skill 渐进披露、历史压缩、工具结果裁剪、Goal/Workflow、
provider prompt cache 与 ContextTrace，但“拼装、预算、压缩、任务真相、缓存边界”没有共享同一合同：

1. `MemoryComponent` 把稳定 L1 和本轮 L3 合成一个 Slice，稳定前缀失真。
2. `ContextManager` preflight 与 `ContextCompressor` reactive path 都可能做有损摘要。
3. 预算初始值硬编码为 32K，且首轮没有完整纳入 tool schemas / reserve / attachments。
4. 压缩前只把 goal + last user 追加进全局 `MEMORY.md`，既不够结构化又污染长期记忆。
5. Goal、GoalTask、Workflow、Receipt、Artifact 能表达任务真相，但没有统一的只读投影供 context 使用。
6. 诊断主要重算近似值，无法解释“这次真实请求为何加载/裁掉了什么”。

因此 V1 的判断标准不是“多塞一些 prompt”，而是：**同一份上下文在生命周期、来源、预算、
压缩、恢复、缓存、诊断上都能被精确解释，并且长期任务在压缩和重启后不丢 pending/decision/artifact。**

## 2. 借鉴边界

| 来源 | 吸收 | 不照搬 |
|---|---|---|
| Hermes | stable/context/volatile 分层；稳定前缀缓存；压缩后重挂载；policy-filtered Tool Search 与 compact descriptors | 不引入第二套 Hermes prompt assembler；V1 不引入 `execute_code` RPC 旁路 |
| OpenClaw | context/memory 分责；pre-compaction flush；effective tool catalog；真实调用回到 policy/approval/logging 内核 | 不把 session snapshot 当全局长期记忆持续追加；V1 不扩成第三方 hook SDK |
| Codex | AGENTS/Skills 渐进加载；工具 schema 计入上下文；sandbox/approval 分层；子任务能力隔离 | 不把桌宠对话强制变成 code-agent / subagent 模式，不替换 DeskPet PermissionGate |
| Claude Code | 项目规则分层；优先裁工具结果；deny-first 与 subagent scoped tools | 不把所有路径规则常驻普通聊天 prompt；V1 只冻结内部 typed seam，不实现完整插件 hooks |

来源：

- Hermes Prompt Assembly: <https://hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly>
- Hermes Compression and Caching: <https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching/>
- OpenClaw Context / Memory: <https://docs.openclaw.ai/concepts/context>、<https://docs.openclaw.ai/concepts/memory>
- Codex Manual: <https://developers.openai.com/codex/codex-manual.md>
- Claude Code Context / Memory: <https://code.claude.com/docs/en/context-window>、<https://code.claude.com/docs/en/memory>

## 3. 目标架构

```text
authoritative state
  SessionGoal / goal_tasks / Workflow checkpoint / ProposalState
  Receipt / Artifact / Session messages / Memory V2
                 │
                 ▼
       Context components (read only)
                 │ emit
                 ▼
 ContextFragment[]
 source + lifetime + priority + trim_policy + protected + reason
                 │
                 ▼
 ContextAssembler / ContextRequestPlanner
 common safe budget + candidate tool names + attachments + output reserve
                 │
                 ├── PreparedContext.messages / transcript groups
                 ├── PreparedContext.tool_set / exact wire schemas
                 ├── stable_prefix_boundary/fingerprint
                 └── upstream assembly decisions
                               │
                               ▼
     post-assembly + in-loop context producers
                AgentLoop reactive growth control
             prune tool payloads → preflush snapshot
                     → single ContextCompressor
                               │
                               ▼
 provider attempt planner
 actual provider/model/window/messages/tools-or-none/reserve
                 ├── PreparedContextReport(request_id, attempt_id)
                 └── provider request + matching usage
                               │
                               ▼
                  ContextTrace / context_usage
```

### 3.1 所有权

| Concern | 唯一 owner | 兼容层 |
|---|---|---|
| 组件发现与顺序 | `ContextAssembler` | 旧 `Slice.bucket` 映射为 fragment |
| 工具 catalog 过滤与 schema 冻结 | `ToolCapabilityResolver` | `ToolRegistry.schemas()` 退化为 legacy adapter；AgentLoop 不再重建 |
| 同 run 工具激活状态 | `ToolCapabilityScopeStore` + AgentLoop immutable revision | scope 只保存 capability refs，不保存 handler/凭据 |
| 工具真实执行与副作用审批 | `ToolRegistry.execute_tool` + PermissionGate | PreparedToolSet 是授权前置条件，不替换 permission/receipt/verify |
| 整请求计量与裁剪策略 | `ContextRequestPlanner` | `BudgetAllocator` 暂为适配器，不再独立决策 |
| 运行中工具结果增长 | `ContextManager` 的 size-aware helpers，由 AgentLoop 调用 | 保留现有 tool group/sanitizer |
| 有损历史压缩 | `ContextCompressor` | reactive flag 关闭时才允许 legacy preflight fallback |
| 任务事实 | Goal/GoalTask/Workflow/Receipt/Artifact stores | snapshot 只读投影，不反向覆盖事实源 |
| 压缩前持久化 | `ContextSnapshotStore` | 禁止继续追加普通任务状态到全局 `MEMORY.md` |
| provider cache 标记 | provider adapter，读取 prepared cache boundary | 无 hint 时保持旧行为作为短期兼容 |
| 可观测报告 | 每次即时 provider attempt 边界冻结，按 request/attempt id 回填 usage | Assembler decision 只作上游输入，UI 不再自行猜测 |

### 3.2 核心合同

建议在 `backend/deskpet/agent/assembler/bundle.py` 引入：

```python
@dataclass(frozen=True)
class ContextFragment:
    fragment_id: str
    source: str
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    lifetime: Literal["platform", "stable", "task", "retrieved", "history", "current"]
    placement: Literal["prefix", "transcript", "control"]
    priority: int
    trim_policy: Literal["never", "truncate", "drop", "summarize", "page_in"]
    protected: bool = False
    reason: str = ""
    cache_scope: str | None = None
    causal_group_id: str | None = None
    anchor_after: str | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

@dataclass
class PreparedContext:
    messages: list[dict[str, Any]]
    tool_set: "PreparedToolSet"
    stable_prefix_boundary: int | None
    stable_prefix_fingerprint: str | None
    assembly_decisions: list[ContextDecision]
```

最终 `PreparedContextReport` 不在这里提前生成；它必须等 AgentLoop 选定本次 provider/model、从
`PreparedToolSet` 导出实际 `tools` 或 `None` 并完成 attempt 预算后，在 provider call 前冻结。AgentLoop
不得再从 registry 按名称重建 schema。迁移方式：一个 component
仍返回一个 `Slice`，但 `Slice.fragments` 可包含多个 fragment；Assembler
优先消费 `fragments`，否则把旧 `bucket/text_content/meta` 映射为一个 legacy fragment。这样先解决
L1/L3 两种生命周期而不破坏 `ComponentRegistry.fanout()` 协议。

### 3.3 工具能力平面合同

新增 `backend/deskpet/tools/capabilities.py`，核心对象固定为：

```python
@dataclass(frozen=True)
class ToolExposureIntent:
    direct_selectors: tuple[str, ...]
    discoverable_selectors: tuple[str, ...]
    deny_selectors: tuple[str, ...]
    required_direct_names: tuple[str, ...] = ()

@dataclass(frozen=True)
class ToolEligibilityContext:
    session_id: str
    request_id: str
    task_type: str
    mode: str                    # host/session facts only; never contains provider id

@dataclass(frozen=True)
class ToolPolicySnapshot:
    disabled_toolsets: frozenset[str]
    schema_only_toolsets: frozenset[str]
    dangerous_allowlist: frozenset[str]
    fingerprint: str            # canonical config hash; immutable for this request

@dataclass(frozen=True)
class ToolCapabilityRef:
    capability_id: str             # canonical: source + qualified name
    name: str
    toolset: str
    source: str
    description: str               # bounded descriptor, not full schema
    schema_hash: str
    spec_version: str
    permission_policy_version: str
    permission_category: str
    dangerous: bool

@dataclass(frozen=True)
class PreparedToolCapability:
    ref: ToolCapabilityRef
    canonical_schema: Mapping[str, Any]  # immutable deep copy; never re-read by name

@dataclass(frozen=True)
class ResolvedToolDraft:
    registry_revision: int
    direct: tuple[PreparedToolCapability, ...]
    deferred: tuple[ToolCapabilityRef, ...]
    conditional_direct: tuple[PreparedToolCapability, ...]
    denied_names: tuple[str, ...]
    policy_fingerprint: str
    decisions: tuple[ToolSelectionDecision, ...]

    def finalize(self, *, required_conditional_names: tuple[str, ...]) -> "PreparedToolSet": ...

@dataclass(frozen=True)
class PreparedToolSet:
    scope_id: str                   # request-scoped UUID; bound to session/request
    revision: int                   # activation returns a new immutable revision
    registry_revision: int
    direct: tuple[PreparedToolCapability, ...]
    deferred: tuple[ToolCapabilityRef, ...]
    activated: tuple[PreparedToolCapability, ...]
    denied_names: tuple[str, ...]
    policy_fingerprint: str
    schema_fingerprint: str
    decisions: tuple[ToolSelectionDecision, ...]

    def logical_schemas(self) -> tuple[dict[str, Any], ...]: ...  # direct + activated exact copies

@dataclass(frozen=True)
class PreparedToolPayload:
    logical_schema_fingerprint: str
    adapter_id: str
    adapter_version: str
    tools: Any                       # exact object consumed by provider request builder
    wire_payload_hash: str
    wire_tokens: int
    estimate_method: str

@dataclass(frozen=True)
class ContextSnapshotHandle:
    session_id: str
    task_scope_id: str
    row_revision: int              # DB row CAS revision; never equals/inferred from tool scope revision
    snapshot_hash: str

@dataclass(frozen=True)
class SnapshotWriteReceipt:
    previous_row_revision: int
    new_handle: ContextSnapshotHandle
    persisted_tool_scope_revision: int
    attempt_id: str | None
```

冻结规则：

1. `ToolRegistry.catalog_snapshot()` 在锁内复制不可变 `ToolSpec` 列表和单调 `registry_revision`；
   `register/unregister/replace` 每次成功提交后 revision +1。Resolver 只消费这一份 snapshot。
2. `AssemblyPolicy` **保留原 `tools: list[str]` 作为 OFF 的 byte/ordering-compatible legacy 字段**，并新增独立
   `tool_exposure: ToolExposurePolicy(direct, discoverable, deny)` 只供 ON 消费。默认 policy YAML 同时保存原
   `tools` 值/顺序与新 `tool_exposure`；不得原地覆盖旧字段。第三方/旧 YAML 缺 `tool_exposure` 时，ON loader
   才确定性映射为 `direct=tools, discoverable=[], deny=[]`，OFF 始终只读原 `tools`，因此回滚不需反推。
   `deny` 永远优先。Resolver 只把 direct/discoverable 与 host/session eligibility 求交集：env、session-aware
   `visible_when`、disabled toolsets、dangerous allowlist 和 session mode；**provider 表达能力不参与逻辑集合选择**。
   每个 request 在 resolve 前严格读取一次不可变 `ToolPolicySnapshot`；读取失败返回
   `tool_policy_unavailable`，不得用空配置继续。
   ToolComponent 只产出这份 `ToolExposureIntent`，不在并行 fan-out 内冻结 schema；`ContextRequestPlanner`
   汇总组件给出的固定 protected direct requirements 后，调用 Resolver 一次生成 `ResolvedToolDraft`；
   `session_history_page_in` 作为 exact-schema `conditional_direct` 同时冻结。Planner 再根据全 raw 是否 fit，
   用同一 draft 的 `finalize()` 选择空条件或 page-in 条件，生成唯一初始 `PreparedToolSet`，避免 Tool/Memory
   并行组件形成隐藏先后依赖和第二次 catalog 读取。
   selector grammar 冻结为：未带前缀字符串=精确工具名，`*`=全部，`toolset:<id>`、`source:builtin`、
   `source:mcp:*`、`source:plugin:*`；未知前缀配置加载失败，不做模糊猜测。默认 YAML 迁移为：
   chat/task/command 的现有 `*` 改成 direct=`source:builtin`、
   discoverable=(`source:mcp:*`, `source:plugin:*`)；
   recall/code/web_search/plan 保留当前显式 direct names；code/web_search 的 discoverable 为
   (`source:mcp:*`, `source:plugin:*`)，recall/plan 的 discoverable 为空；emotion direct/discoverable 均空且
   deny=`*`。reserved bridge names 不参与 selector，只有 host 在 deferred
   非空时添加，避免 `source:builtin` 让 bridge 常驻。
3. deferred 不携带完整 schema；只有 direct schemas 和三个 bridge schemas 支付常驻 token。deferred 非空时
   自动把 `tool_search/tool_describe/tool_activate` 加入 direct；为空时不暴露 bridge。
4. `ToolCapabilityScopeStore` 是 `service_context` 中的 bounded 内存 store，key 为 `scope_id`，同时校验
   session_id/request_id，并保存初始冻结的 `ToolEligibilityContext`；执行时 authorizer 从 scope 取该对象并与
   `ToolExecutionContext` 的 ids 对账，不能由模型参数重建 task_type/mode。active task scope record 另保存最新
   `ContextSnapshotHandle`；`PreparedToolSet.revision` 是 capability revision，handle.row_revision 是 DB row CAS
   revision，两者永不互换或互相推导。TTL=当前 run + 5 分钟，run 结束或
   session 删除立即 purge。持久化 snapshot 只写
   refs/hash/revision/决策摘要，不允许在重启后直接恢复旧 scope；重启后必须对当前 registry 重新 resolve。
   初始 scope open 使用 insert-if-absent；activation 使用 per-scope `asyncio.Lock`，在 DB 前完成 expected-revision
   校验，DB 成功后只做 `commit_prevalidated()`，不再执行可能失败的第二次 CAS。
   `tool_activate` 采用与 accepted-async 相同的 exclusive-turn invariant：包含它的 assistant response 必须只有
   一个 tool call；若混有目标工具或多个 activation，AgentLoop 在 dispatch 前不执行任何一个并返回
   `tool_activation_requires_exclusive_turn`，要求下一模型迭代单独激活。不能依赖 Registry 的
   `concurrency_safe`，因为当前 AgentLoop 是自行 `asyncio.gather(_dispatch_tool...)`。
5. `ToolSpec.visible_when` 演进为 context-aware predicate：新增 `visibility_scope="global"|"session"`；
   `global` 才允许显式兼容旧无参 predicate，`session` 在 Context OS ON 时必须接收同一
   `ToolEligibilityContext`，缺 context/签名错误一律隐藏。`eligible_specs(context, policy_snapshot)` 是唯一过滤
   owner。goal tools 注册为 session scope，并用 `context.session_id` 调新增的 strict
   `SessionGoalStore.get_active_goal_context_for_session(session_id)`；现有 `get_active_goal_context(session_id=None)`
   即使传 session id 也可能全局回退，只允许 OFF legacy adapter 使用，
   ON 绝不读取“最近其他 session 的 active goal”。
6. AgentLoop tool dispatch 通过新增的 kw-only `ToolExecutionContext(scope_id, session_id, request_id,
   origin="agent")` 传给 Registry；执行顺序冻结为 lookup → global disabled/env/visible eligibility →
   capability authorizer → plan read-only → breaker → PermissionGate → handler。
   Context OS ON 且 origin=agent 时，缺 scope、scope/session 不匹配、工具不在 direct/activated 都返回
   `capability_denied`；在放行前还要把当前 ToolSpec 的 schema/spec/permission-policy hash、env、visible/config
   eligibility 与 scope ref 对账，不匹配返回 `capability_stale`。`execute_prepared()` 继续走 durable
   authorization，不复用该普通 Agent scope。
   execution context 不合并进 LLM params；Registry 用 host-only `ContextVar` 在 handler 生命周期内暴露只读
   context，并在 finally reset。同步 handler 的 `run_in_executor` 必须通过 `contextvars.copy_context().run`
   显式传播，防止 bridge 读不到 scope，也防止模型用 `_scope_id` 参数伪造宿主身份。
   Context OS ON 的 Registry 在 resolve、describe/activate 和 execute 边界都通过 strict policy provider 取得
   当前 `ToolPolicySnapshot`；每个 host operation 只读一次并通过 host-only execution context 传给 bridge
   handler，禁止 Registry 与 handler 在同一边界各读一次形成 TOCTOU。activation proposal 与 commit 是两个边界，
   commit 必须再读一次。读取失败返回 `tool_policy_unavailable`，fingerprint/eligibility 与 scope 不一致返回
   `capability_stale`。仅 OFF legacy `schemas()/execute_tool()` 保留当前 warn-and-continue 行为。
7. bridge 仍通过 Registry 正常 dispatch。`tool_search` 只返回 bounded descriptors；`tool_describe` 只允许
   当前 deferred id 并返回 exact schema/hash；`tool_activate` 返回 host-recognized activation proposal。
   AgentLoop 用 proposal 生成新的 immutable `PreparedToolSet`，重新执行整请求预算；预算成功才提交 scope
   revision 并让下一次 provider attempt 看见该 schema，失败则保留旧 revision 并返回
   `tool_activation_budget_exceeded`。不得直接从 bridge 绕过 Registry 调真实 handler。
   describe/proposal/commit 只复核 host/session eligibility 与 policy snapshot，不读取 provider 限制。
8. 每次普通 provider attempt 的 logical tools 必须来自 `PreparedToolSet.logical_schemas()`；force-finish
   在 attempt planner 层固定生成 `PreparedToolPayload(tools=None)`。
   对 Context OS 请求，如果此前已经真实执行过工具，则 `max_iterations` 的最后一个 iteration 必须保留给
   tool-free force-finish；也就是工具循环最多使用前 `max_iterations - 1` 轮，最后一轮固定
   `tools=None + tool_choice=none`。非 Context OS legacy 路径保持原有耗尽语义。这样不会出现“最后一轮仍调用
   工具，随后直接 max_iterations/stop-loss，用户拿不到诚实收尾”的边界空洞。
   `PreparedToolSet.schema_fingerprint` 是 provider-neutral OpenAI canonical schema 集合 hash；provider adapter
   可以做格式转换，但必须返回 `adapter_id/version + wire_tool_payload_hash + wire_tool_tokens`，不得静默过滤、
   改名或添加工具。预算/report 使用本 attempt 的 wire hash/tokens，snapshot 同时保存 canonical hash 与最后
   adapter/wire hash。adapter 无法表达某 schema 时该 attempt 以 `tool_schema_unsupported` 失败并按既有 chain
   fallback，PreparedToolSet 不变；禁止各层自行重建出不同逻辑集合。
9. activation 原子边界固定为：在尚未 append/emit activation tool result 时，以当前 local/scope revision 构造
   candidate → 预构造 model-visible result → 用 candidate schemas 的完整 request 重预算 → 获取 scope lock 后
   完成 scope revision、strict policy、snapshot expected revision 与所有序列化/写入参数的**全部可失败校验** →
   若有 active task snapshot，使用 scope record 的 `snapshot_handle.row_revision` 执行
   `ContextSnapshotStore.update_tool_context_cas(expected_row_revision=...)` 并取得 `SnapshotWriteReceipt` → 紧接着
   调用锁内、无 await、无二次校验的 `ToolCapabilityScopeStore.commit_prevalidated(candidate, receipt.new_handle)`
   并替换 AgentLoop local set → 最后
   append/emit 已预构造的 `activated` result。DB CAS 前任一步失败都不更新 DB/scope/local/result；DB CAS 成功后
   禁止任何可恢复的失败点，scope commit 不是第二次 CAS。DB await 通过 §4.2 的 commit-ack helper 执行；若
   cancellation 落在“DB 已提交、await 尚未确认”的窗口，先结算 write task。已提交则记录
   `cancelled_after_snapshot_commit` 并留下非权威 diagnostic-ahead row，不激活 scope/local/result，随后传播取消；
   未提交则保持全不变再传播。进程在 DB 成功与内存赋值之间硬崩溃同样只留下领先 diagnostic row；重启只把
   它视为 stale diagnostic 并重新 resolve，不从持久化记录复活 scope。
   无 active task snapshot 时跳过 DB 步骤，但 request report 仍记录 candidate/commit。snapshot CAS/DB 失败
   返回 `tool_activation_persist_failed`，不允许“内存已激活但持久化仍旧”。
10. 初始 resolve 只发生在 `ContextRequestPlanner.prepare_initial()`；compact、provider fallback、tool-result growth
   和普通 replan 必须调用 `replan(existing_tool_set=...)`，只复用/验证当前 immutable set，不再次读取 catalog
   重新选择。每次 provider attempt 只做 per-direct-ref hash/eligibility validation；无关 catalog revision 变化
   不改变 schema，direct ref stale 则终止当前 run 并返回可恢复 `tool_catalog_stale`，下一 request 再 resolve。
11. 初始预算算法冻结为无环两阶段：先计算不含 history 的 fixed messages/attachments/reserve，调用 Resolver
    一次得到 draft 并计算 base-direct wire cost；若“fixed + base tools + 全 raw history”fit，则
    `finalize(required_conditional_names=())`；否则
    `finalize(required_conditional_names=("session_history_page_in",))`，把该 exact schema 成本扣除后再让
    SessionHistoryPlanner 选择 summary cover + raw tail。conditional schema 已来自同一 catalog snapshot，
    finalize 不调用 Registry/Resolver；最小 root 仍不 fit 才 final BLOCK。

## 4. 数据模型决策

### 4.1 `TaskContextSnapshot` 是派生缓存，不是新事实源

字段优先级固定：

1. Workflow checkpoint / `ProposalStateV1` 给出 active step、todo、tool commit 状态。
2. `goal_tasks` 给出 pending/running/completed task；`session_goals` 给出显式目标。
3. Receipt/Artifact store 给出真实 outcome、artifact identity 与 pending/accepted 状态。
4. 历史/压缩摘要只能补充 narrative，不得把 pending 推断成 completed。

普通长任务没有显式 Goal/Workflow 时，允许生成 session-scoped derived snapshot，但它不能创建或
修改 Goal/Workflow；没有可证明的 active task 时不注入空 task block。

### 4.2 新表

新增 `backend/deskpet/memory/migrations/010_context_os_v18.sql`，同时创建 task snapshot 与 Session
coverage tree：

```sql
CREATE TABLE session_context_snapshots (
  session_id TEXT NOT NULL,
  task_scope_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  source_revisions_json TEXT NOT NULL DEFAULT '{}',
  objective TEXT NOT NULL DEFAULT '',
  decisions_json TEXT NOT NULL DEFAULT '[]',
  completed_json TEXT NOT NULL DEFAULT '[]',
  pending_json TEXT NOT NULL DEFAULT '[]',
  artifacts_json TEXT NOT NULL DEFAULT '[]',
  blockers_json TEXT NOT NULL DEFAULT '[]',
  narrative_summary TEXT NOT NULL DEFAULT '',
  last_prepared_toolset_json TEXT NOT NULL DEFAULT '{}',
  tool_policy_fingerprint TEXT,
  tool_registry_revision INTEGER,
  last_compaction_cycle_id TEXT,
  snapshot_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (session_id, task_scope_id)
);

CREATE TABLE session_context_segments (
  segment_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  level INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('raw_index', 'summary')),
  first_message_id INTEGER NOT NULL,
  last_message_id INTEGER NOT NULL,
  message_count INTEGER NOT NULL,
  source_hash TEXT NOT NULL,
  child_segment_ids_json TEXT NOT NULL DEFAULT '[]',
  summary_text TEXT NOT NULL DEFAULT '',
  token_estimates_json TEXT NOT NULL DEFAULT '{}',
  provider_id TEXT,
  model_id TEXT,
  revision INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'valid',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(session_id, level, first_message_id, last_message_id, source_hash)
);

CREATE INDEX idx_context_segments_cover
  ON session_context_segments(session_id, status, first_message_id, last_message_id, level);
```

要求：

- `revision` 使用 compare-and-swap；迟到写不能覆盖新状态。
- `(session_id, task_scope_id, last_compaction_cycle_id)` 逻辑幂等，同一 compact cycle 最多提交一次。
- `snapshot_hash` 对 canonical JSON 计算，用于无变化跳过与诊断。
- `last_prepared_toolset_json` 只保存 direct/activated names、per-tool schema hash、选择原因、schema token
  总数、selected provider adapter id、attempt id 与 `adapter_state=canonical|prepared`；不保存完整 schema、
  handler、凭据或完整工具参数。读取时若
  registry revision/spec/policy hash 不匹配，只能标 stale 并重新 resolve，不能复活旧 capability scope。
  initial resolve/activation 先写 canonical set 且把 last adapter/wire hash 置空；Task 4.3 在每个 selected
  attempt send 前用独立 `expected_row_revision` CAS 回填 adapter id/version、wire hash/tokens/attempt id，状态为
  `prepared`。是否真实进入 provider transport 只由 matching ContextAttemptStore 的 `planned→sent→terminal`
  状态判定；取消/失败在 sent 前不得把 snapshot 的 prepared adapter 冒充 actual send。
- capability revision（内存字段 `PreparedToolSet.revision`，持久化摘要字段
  `persisted_tool_scope_revision`）与 snapshot row `revision` 是两个独立并发域；所有 store API 只接受
  `expected_row_revision`，成功返回 `SnapshotWriteReceipt.new_handle.row_revision`。
  禁止把 scope revision 作为 DB expected revision。initial create receipt 写入 `PreparedContext`/scope record；
  activation receipt 随 `commit_prevalidated` 更新 handle；attempt adapter write receipt 在同一 scope lock 内用
  `advance_snapshot_handle_prevalidated()` 更新 handle，但不增加 capability revision。
- 所有 snapshot async CAS 统一由 `await_snapshot_commit_ack(write_task)` 包装：用 shield 保护底层本地 DB task；
  外层收到 cancellation 时仍必须等 write task terminal 并取得 receipt/明确 rollback，再传播 cancellation。
  若已提交，记录 `cancelled_after_snapshot_commit` 与 receipt，保持 scope capability/local/result 未激活并在 run
  finally purge；若未提交则全不变。重复 cancellation 也不能丢失 commit outcome。该 diagnostic row 从不作为
  scope 权威，下一 request 重新 resolve。
- migration 必须同步升级 `migrator.py::TARGET_SCHEMA_VERSION`，扩展当前只特殊处理 V17 的逻辑，
  并添加 V17→V18、空库→V18、重复运行、事务失败回滚测试；禁止 lazy CREATE TABLE 偿债。
- segment 的 `[first_message_id,last_message_id]` 表示“该 Session 内、按 id 排序落在边界内的消息集合”；
  `message_count + source_hash` 校验删除/漂移。跨 Session 穿插的全局 id 不属于该 segment。

### 4.3 `task_scope_id` 与普通任务创建算法

`task_scope_id` 只由现有结构化状态生成，不用 LLM 语义相似度猜：

1. 有 active workflow run：`workflow:{workflow_run_id}`。
2. 否则有 active explicit goal：`goal:{goal_id}`。
3. 否则：`session:{effective_sid}`；`effective_sid` 直接复用
   `backend/deskpet/session/task_scope.py::TaskScopeDecision.effective_sid`。

普通任务 snapshot 的确定性创建条件：

- `/new` 或 payload `new_session=true` 创建的新 effective session，以 stripped first request 为 objective；或
- 当前 effective session 首次产生结构化 plan/todo、tool call、Receipt 或 Artifact 时，以触发该证据的
  `request_id + user text` 建立 snapshot。

默认闲聊、仅 assistant prose、仅 L3 命中都不创建 snapshot。普通任务复用同一 effective session 的
snapshot，直到用户显式 `/new`/新 session；不做自动语义切题。Workflow/Goal 结束后 snapshot 保留只读，
再次显式 resume 使用同一 scope id；Session 删除时级联删除 `session:*` snapshot。`completed/pending/artifacts`
只接受 GoalTask/Workflow/Receipt/Artifact/typed todo event；未结构化的模型文字只能进入
`narrative_summary`，不能改变完成态。

### 4.4 冻结预算公式与附件合同

每个实际 provider attempt 使用：

```text
window = resolved ModelInfo.context_window
policy_reserve = ceil(window × (1 - effective_pct))
generation_reserve = requested max_tokens
safety_floor = max(1024, ceil(window × 0.01))
total_reserve = max(policy_reserve, generation_reserve + safety_floor)
effective_input_budget = window - total_reserve
estimated_input = tokens(final wire messages) + tokens(actual wire tools or 0)
```

`generation_reserve` 已包含 provider 计入 completion 的 hidden reasoning，不再额外重复扣 reasoning reserve。
若某 provider 明确把 reasoning 单列在 context 外，再通过 capability override 修改公式并留测试证据。
公共可逆裁剪以 chain 所有候选 attempt 的最小 `effective_input_budget` 为安全线；实际 attempt 再重算。

附件必须先归一化成 provider message content blocks；`AttachmentRef(fragment_id, message_index,
content_index, media_type, byte_size)` 只用于归因，不能再次加 token，避免与 message 序列化双算。
图片/音频等非文本 block 由 provider/model-specific estimator 计一次；未知类型用保守上界并标记
`estimate_method=conservative_unknown_media`。

### 4.5 report 状态机与保留策略

`backend/agent/context_report.py::ContextAttemptStore` 注册到 `service_context`，采用内存 bounded ring：

- key：`(session_id_or_global, request_id, attempt_id)`；状态只允许
  `planned → sent → succeeded|failed|cancelled` 单向迁移。
- `sent` 的唯一转换点在 provider 公共 transport coroutine **取得发送控制权后的第一条同步语句**：它发生在
  adapter/snapshot CAS/最终 cancellation checkpoint 之后、第一次网络 await/write 之前。transport task 尚未执行
  该语句即被取消时只能记 `cancelled_before_send`；一旦执行过则属于 sent，后续取消记 sent→cancelled。
- logical provider/chain attempt 对应一个 `attempt_id`；provider 内部 HTTP retry 只累加
  `transport_retry_count`，不伪造新的逻辑 attempt。
- `contextvars.ContextVar` 传 `purpose/session/request`；OpenAICompatibleProvider 的 stream/non-stream
  公共出站 seam 自动补 `attempt_id`，因此 capability gate、classifier、compressor 等直调也覆盖。
- usage 仅能更新 matching key；无 key/迟到/重复终态更新记录 orphan diagnostic，不覆盖最新一轮。
- 每 session 保留最近 32 attempts、TTL 30 分钟；session 删除立即 purge。store 不持久化完整 messages，
  只保留 canonical hash、token counts、脱敏 preview 与 decisions；最终 wire payload 仅在调用栈内短暂存在。

前端 `context_usage` 展示当前 request 的 attempts，辅助 purpose 与 `agent_response` 分栏；应用重启后 ring
为空是预期行为，durable task state 由 snapshot store 负责，不混入诊断库。

### 4.6 单一回滚不变量

Task 0.2 先在 `backend/config.py::FeaturesConfig` 增加未完成期默认 OFF 的 `context_os_v1`，每个后续
行为变更都必须在同一 flag 内分支：OFF 走 legacy Slice/Persona/messages/preflight/provider payload；
ON 才走 fragment/snapshot/planner/report。每个 Task 同时添加 OFF golden test。Task 5.1 在所有自动化
验收通过后把出厂默认翻 ON；这是“半成品例外 → 完成即点亮”，不是灰度。数据库 V18 migration 可在
OFF 时存在，但 OFF 不读写新表。禁止为各子能力增加可组合的长期开关。

### 4.7 压缩模型选择合同

```toml
[context.compaction]
model = "follow_session"
```

- `follow_session`：复用本轮已解析的 Session provider chain 及其既有 fallback 顺序，是出厂默认；
  compression call 实际由哪个 candidate 成功就记录哪个，后续 user-response attempt 不要求必然同一 candidate。
- 显式 model id：先在当前 Session provider chain 中找精确 model；否则仅当当前 relay/provider catalog
  明确提供该 model 且可复用同一 account credential 时克隆 provider。多匹配时沿 Session chain 顺序。
- 无法解析、鉴权不可用或调用失败：不静默回退到别的模型，不提交新有损 summary；保留 raw/既有有效
  coverage 并发诊断，仍超预算则返回可恢复 budget error。
- `CompressionModelResolution` 与 compact attempt 一起记录 requested/resolved provider/model/source。
- 设置页使用现有 provider catalog 下拉，显示“跟随当前会话模型”与可用 model；不允许任意自由文本。

### 4.8 单 Session coverage tree

SessionDB 原始 messages 永远是权威。coverage eligibility 固定为未删除的 `user/assistant/tool` rows
（保留 assistant tool_calls/tool_call_id group），排除 `is_summary=1` 等旧派生 summary/system rows，避免把
旧摘要再当原始事实重复覆盖。`SessionCoverageStore` 维护派生的连续区间树：

1. Level-0 `raw_index` 节点按最多 32 条完整 causal groups 建立，只缓存 token estimate/hash，不复制正文。
2. Level-1 `summary` 覆盖相邻 raw nodes；更高 level 只合并相邻、无 gap/overlap 的 child summaries。
3. 每个 summary 保存 first/last message id、message count、canonical source hash、child ids、实际压缩模型。
   raw/summary node 的 `token_estimates_json` 按 target provider tokenizer id 缓存；缺 tokenizer 时使用
   `deskpet-conservative-v1`，最终 wire request 仍由 attempt planner 重算并校准。
   旧大 tool result 的确定性 outcome/receipt/artifact/ref 折叠也作为 `summary` kind 节点，必须有正文
   outcome + source hash + page-in ref；裸 ref 仍不能独立满足 coverage。
4. 新消息只 append/封口当前 raw leaf；旧消息删除或 hash 不匹配使所有祖先标 stale，不能继续出站。
5. `SessionHistoryPlanner` 先计算 protected prefix/tools/current/reserve 的成本，再处理 transcript：
   当前 user row 已在 assembly 前写入 SessionDB 时，以 `current_message_id` 标记为 current fragment coverage，
   从 history raw/summary 输入精确排除，禁止文本去重与双注入。
   - 全部 raw messages 能放入剩余预算：按时间顺序全量无损装载；固定 `l2_top_k` 不参与新路径。
   - 不能放入：保留最近完整 raw causal groups，旧区间选择能完整覆盖且成本最低的有效 summary nodes；
     每个 node 同时给出 `session_history_page_in(segment_id)` reference。
   - summary cover + raw tail 仍放不下：逐层选择更高 summary；最小 root 仍放不下则 final BLOCK。
6. 出站前生成 `SessionCoverageReport`：所有 eligible 未删除 message id 必须恰好由 raw 或 summary 覆盖一次；
   summary 携带 page-in ref，但 ref 不单独计作内容覆盖。gap、重复覆盖、stale hash、断裂 tool group
   均阻止有损提交/出站。
7. `session_history_page_in` 只允许读取当前 session、目标 segment 的权威 SessionDB 原文，受本轮预算
   限制并按完整 causal group 分页；不能借 segment id 越权读取其他 session。

## 5. 分阶段实施

以下 Task 的勾选项是执行阶段的原子步骤；每个 Task 只有通过其测试才能进入下一个依赖 Task。

### Wave 0 — 冻结事实与契约

#### Task 0.1：补上下文调用链契约测试

**文件**

- 新增 `backend/tests/test_context_os_callsite_contract.py`
- 新增 `scripts/perf/context_os_bench.py`
- 执行阶段生成本目录 `context-perf-baseline.json`
- 只读校准 `backend/main.py`
- 只读校准 `backend/pipeline/voice_pipeline.py`

**步骤**

- [ ] 添加现状探针：记录 text chat 当前可能同时经过 preflight/reactive 两个有损 owner，作为明确的
      red baseline；只在 Task 3.2 后把断言收紧为一次，禁止误报基线已满足。
- [ ] 固定 `tool_schemas` 在进入 provider 前可被整请求预算器看到。
- [ ] 把 ToolComponent 已选 schemas → main 降级为 names → AgentLoop 再调 Registry 重建记录为 red baseline；
      同时证明当前 `tool_search` 搜索 `all_specs()`、结果不会同 run hydrate、task policy 不在 execute 前复核。
      这些断言在 Task 1.3/3.3/4.0 后分别收紧，禁止把现状误报为渐进式工具加载已完成。
- [ ] 把当前入口矩阵锁为事实：text/code 共用 `_run_chat`；web/research 是父 AgentLoop 内工具 scope，
      不是独立 venue；voice 是 `backend/pipeline/voice_pipeline.py` 的第二入口且存在裸 loop fallback。
- [ ] 分别断言每个 scope 是否经过 Assembler/ContextManager/AgentLoop、schemas/history/compact owner；
      目标行为测试在对应 Task 内按 TDD 新增，不用长期 xfail 掩盖。
- [ ] 盘点所有 provider call purpose：capability gate、classifier、planner、compressor、agent response、
      force-finish；标出哪些调用会直接结束用户请求，哪些会修改后续 context。
- [ ] 把毛选七步流水线当前缺少 `not _in_code_mode` 的事实记为 red baseline；目标合同固定为
      `companion.control`，code scope 不得产生其 pre-loop/in-loop context 或观测事件。
- [ ] 固定 current user message 必须存在且只出现一次。
- [ ] 用 legacy/OFF 路径跑 500 次无 L3/无磁盘规则/无 LLM 的短聊天 assembly，记录 warmup、median、
      P95、Python/CPU/commit；同一脚本供 Task 5.2 新路径复测。
- [ ] 运行该文件，记录现有失败为本计划待关闭的基线，而不是伪装成绿线。

**完成信号**：测试能准确区分 preflight/reactive compaction 与静态/动态工具集合，且失败项逐一映射
AC-CTX-6/8/12/17/18/19。

#### Task 0.2：定义 fragment / prepared request 类型并建立总回滚闸，不改变输出字节

**文件**

- 修改 `backend/deskpet/agent/assembler/bundle.py`
- 修改 `backend/deskpet/agent/assembler/assembler.py`
- 修改 `backend/deskpet/agent/assembler/__init__.py`
- 修改 `backend/config.py::FeaturesConfig`
- 新增 `backend/tests/test_context_fragments_compat.py`

**步骤**

- [ ] 添加 `ContextFragment`、`ContextDecision`、`PreparedContext`；attempt report 类型在 Task 4.3
      放到独立 `context_report.py`，避免 Assembler 冒充最终请求 owner。
- [ ] 添加 `features.context_os_v1=false`（实现未完成期例外）；OFF 保持旧路径，Task 5.1 才翻默认 ON。
- [ ] 给 `Slice` 添加默认空 `fragments`，保持现有构造调用兼容。
- [ ] 实现 legacy Slice→fragment 的纯函数适配，禁止 component 双注入。
- [ ] `ContextBundle.build_messages()` 保留为兼容 wrapper；新增 `prepare_request()`，初始版本必须与旧输出
      serialized bytes 等价。
- [ ] 对 prefix/transcript/control placement、role/tool_call_id/reasoning_content、late nudge、L2 newest-tail
      写 golden tests；control event 必须保留 causal anchor，不能按 role 前置。

**完成信号**：现有 assembler 测试全绿；兼容测试证明未启用新 component 时消息字节不变。

#### Task 0.3A：冻结内部消息元数据载体并收口 prefix producers

**依赖**：Task 0.2  
**文件**

- 新增 `backend/agent/context_messages.py`
- 修改 `backend/providers/openai_compatible.py` 的 stream/non-stream 公共 payload 构造 seam
- 修改 `backend/main.py` 的 post-assembly injectors / ProblemHandlingPipeline / plan 注入调用点
- 新增 `backend/tests/test_context_producer_inventory.py`

**步骤**

- [ ] 定义 `ContextMessageMeta(placement, lifetime, source, protected, trim_policy, causal_group_id, anchor_after)`。
- [ ] 固定使用 message 顶层内部键 `__deskpet_context`；`tag_message()` 复制原 dict 后加 canonical metadata，
      deepcopy/slice/retry 自然保留；SessionDB 显式字段写入不持久化该键。
- [ ] `split_wire_messages()` 返回“剥离顶层保留键的 wire deep-copy + 对齐 metadata”；所有 provider
      serialization/cache fingerprint 只能消费 wire copy，并断言 wire 中不存在该键。
- [ ] 同模块定义 `ProviderAttemptOptions` 与 `context_attempt_scope()` ContextVar；AgentLoop 无需给
      `chat_with_tools` 增加新 kwarg，fake/第三方 provider 的签名保持兼容。
- [ ] 统一提供 `append_prefix()`、`append_transcript()`、`append_control()`，迁移 main 中 prefix/
      post-assembly producers；
      没有 source/placement 的新增 system 注入令 contract test 失败。
- [ ] prefix 可按稳定层排序；transcript/tool group 保持原序；control 保持 `anchor_after` 因果位置。
- [ ] 在唯一 provider-attempt serializer 中剥离所有内部 metadata，wire payload 不得出现 DeskPet 私有键。
- [ ] 先做字节兼容测试：除内部 sidecar 外，未启用新排序/裁剪时 provider messages 与当前行为一致。
- [ ] `context_os_v1=false` 下完全不打键，serialized bytes 与 HEAD golden 相同。

**完成信号**：main 的 prefix/post-assembly producer inventory 100% 有 placement/source；wire payload 无内部字段。

#### Task 0.3B：收口 transcript / in-loop control producers

**依赖**：Task 0.3A  
**文件**

- 修改 `backend/agent/agent_loop.py`
- 修改 `backend/deskpet/agent/context_compressor.py`
- 修改 `backend/tests/test_agent_loop_compaction_wiring.py`
- 新增 `backend/tests/test_context_control_causality.py`

**步骤**

- [ ] tool call + 全部 results 用同一 `causal_group_id` 标记为 transcript，不可拆分/重排。
- [ ] goal/todo/subagent/self-check/evidence/completion nudges 全部经 `append_control()`，记录触发消息 id
      为 `anchor_after`。
- [ ] compressor 的 partition/rebuild 保留 metadata 与 causal anchor；暂不启用新压缩策略。
- [ ] retry/deepcopy/compact round-trip 后 metadata 不丢；wire stripping 后 role/content/tool fields 不变。
- [ ] OFF golden test 证明仍走原始 list append 和旧 compressor 字节。

**完成信号**：所有 AgentLoop context producer 已收口，compact 前后 causal order 测试可判定。

### Wave 1 — 生命周期分层与稳定缓存边界

#### Task 1.1：拆开 L1/L3 fragment

**依赖**：Task 0.3A  
**文件**

- 修改 `backend/deskpet/agent/assembler/components/memory.py`
- 修改 `backend/deskpet/agent/assembler/components/persona.py`
- 修改 `backend/deskpet/agent/assembler/assembler.py`
- 修改 `backend/tests/test_deskpet_context_assembler.py`
- 修改 `backend/tests/test_l2_page_in.py`

**步骤**

- [ ] `MemoryComponent` 在同一 `Slice.fragments` 中分别发出 `memory.l1` stable 与 `memory.l3` retrieved。
- [ ] `PersonaComponent` 的稳定 fragment 只保留身份/行为约束；model/base URL 移到非 prompt attempt
      diagnostics，project root 移到 task/path fragment，不能继续污染 stable fingerprint。
- [ ] 修复跨轮 L2 渲染：保留空 content 但有 `tool_calls` 的 assistant，复制完整 `tool_calls`，接纳
      `tool` role 及 `tool_call_id`，按 assistant call + 全部结果作为不可拆 `causal_group_id`。
- [ ] 为 L3 timeout/empty/error 添加独立 safe-fail，证明不会删除 L1/L2。
- [ ] 对相同 L1、不同 query/L3 的两轮请求计算 fingerprint，断言 stable fingerprint 不变。
- [ ] 对同一内容同时来自 L1/L3 的情况按 canonical source id 去重并记录 reason。
- [ ] OFF golden test 证明 Memory/Persona 仍渲染 legacy combined/frozen 文本。

**完成信号**：AC-CTX-1/2/3 的 unit tests 通过，现有 L2 测试无回归。

#### Task 1.2：把 cache boundary 从“最后一个 system”改为显式 hint

**依赖**：Task 1.1  
**文件**

- 修改 `backend/providers/openai_compatible.py`
- 修改 `backend/llm/anthropic_adapter.py`
- 修改 `backend/providers/openai_compatible.py::chat_with_tools`、`_legacy_chat_with_tools_nonstream`
- 修改 `backend/tests/test_prompt_cache.py`
- 修改 `backend/tests/test_deskpet_llm_adapters.py`

**步骤**

- [ ] `PreparedContext` 计算 canonical stable prefix bytes、boundary index、SHA-256 fingerprint。
- [ ] AgentLoop 在 `context_attempt_scope(ProviderAttemptOptions(cache_boundary=...))` 内调用 provider；
      OpenAICompatibleProvider 从 ContextVar 读取，不扩展 `chat_with_tools` 公共签名。
- [ ] Anthropic adapter 在显式稳定边界落 `cache_control`，不再默认标记动态末尾 system。
- [ ] OpenAI-compatible provider 保持不支持显式 breakpoint 的端点兼容，仍记录 fingerprint/cache usage。
- [ ] fake/第三方 provider 不读取 ContextVar 时保持原行为；添加无签名变更的兼容测试。
- [ ] OFF golden test 不传 cache boundary/report metadata，沿用旧 `_stamp_cache_control` 行为。
- [ ] 添加 persona/config/L1 变化会改 fingerprint，time/L3/current request 变化不会改早期 prefix 的测试。

**完成信号**：AC-CTX-2；Anthropic 与 OpenAI-compatible cache tests 全绿，未知 provider 不因 metadata 崩溃。

#### Task 1.3：建立单一 `PreparedToolSet` 与可执行 capability scope

**依赖**：Task 0.2  
**文件**

- 新增 `backend/deskpet/tools/capabilities.py`
- 修改 `backend/deskpet/tools/registry.py`
- 修改 `backend/main.py` 的 `goal_task_*` session-aware visibility/handler resolver wiring
- 修改 `backend/deskpet/agent/assembler/bundle.py`
- 修改 `backend/deskpet/agent/assembler/components/base.py`
- 修改 `backend/deskpet/agent/assembler/components/tool.py`
- 修改 `backend/deskpet/agent/assembler/assembler.py`
- 修改 `backend/deskpet/agent/assembler/policy.py`
- 修改 `backend/deskpet/agent/assembler/policies/default.yaml`
- 修改 `backend/deskpet/agent/assembler/__init__.py::build_default_assembler`
- 修改 `backend/deskpet/agent/goal_store.py`，新增 strict `get_active_goal_context_for_session(session_id)`
- 修改 `backend/main.py` 的 `service_context`、assembler 与 `build_agent` wiring
- 修改 `backend/agent/agent_loop.py` 的 Registry capability contract/fallback guard
- 新增 `backend/tests/test_tool_capability_resolver.py`
- 新增 `backend/tests/test_prepared_tool_set_contract.py`
- 修改 `backend/tests/test_deskpet_context_assembler.py`
- 修改 `backend/tests/test_deskpet_tools_registry.py`

**步骤**

- [ ] 在 Registry 增加锁内单调 `catalog_revision` 与 `catalog_snapshot()`；register、合法 replace、unregister
      成功后各自只增加一次，失败注册/未知 unregister 不增加。snapshot 深拷贝 schema，禁止后续 mutation。
- [ ] 把 `schemas()` 当前 env/visible/toolset/config/dangerous 过滤提取为唯一的
      `eligible_specs(eligibility_context, policy_snapshot)` 纯路径；Context OS ON 必须传冻结的
      `ToolEligibilityContext(session_id, request_id, task_type, mode)`，对象中禁止 provider 字段。
      legacy `schemas()` 只负责通过显式 legacy adapter 包装同一结果，避免 Resolver 复制过滤逻辑。
- [ ] `ToolSpec/register()` 增加 `visibility_scope="global"|"session"`。global scope 可显式适配历史无参
      `visible_when`；session scope 的新 predicate 必须接收 `ToolEligibilityContext`，ON 路径缺 context、签名错误
      或 predicate 异常均 fail closed。把 `main.py` 的 `goal_task_*` 注册改为 session scope，predicate 用
      `ctx.session_id` 调新增 `SessionGoalStore.get_active_goal_context_for_session(session_id)`；该方法在本 session
      无 active goal 时直接返回 None，绝不调用现有 fallback。handler resolver 同样从 host-only execution
      context 取 session id 并调用 strict 方法。OFF adapter 才允许保留当前 `get_active_goal_context()` 全局回退。
- [ ] 新增 strict `ToolPolicyProvider.snapshot() -> ToolPolicySnapshot`，canonicalize disabled/schema-only/
      dangerous allowlist 并生成 fingerprint。Context OS ON 的初始 resolve 若 config provider 抛错，立即返回
      `tool_policy_unavailable`，不开 scope、不写 snapshot、不调用 provider；OFF 保持现有 warn-and-continue golden。
- [ ] 实现 §3.3 的 `ToolExposureIntent/ToolCapabilityRef/PreparedToolCapability/ResolvedToolDraft/PreparedToolSet/
      ToolSelectionDecision/ToolExecutionContext`，canonical
      schema bytes 使用 sorted-key UTF-8 JSON；direct schema name、registry spec name、hash 不一致时 fail closed。
- [ ] `AssemblyPolicy` 保留 legacy `tools: list[str]`，新增独立
      `tool_exposure: ToolExposurePolicy(direct, discoverable, deny)`；`default.yaml` 八类 task policy 保留迁移前
      `tools` 的 names/order 原值，同时显式新增 ON 的 direct/discoverable/deny。ON 缺新字段时才把旧 list 映射为
      direct-only；OFF 永远只读旧字段，禁止从 exposure 反推 legacy 集合。`deny` 优先且不能被 direct 覆盖。
- [ ] 保留 ToolComponent 三个现有语义：显式 DeepResearch 请求把 intent 覆盖为 direct=`deepresearch` 且
      discoverable 为空；短上下文追问把 `generate_image` 加入本 request deny；web-search grounding 改成
      `ConditionalContextFragment(requires_direct_tool="web_search")`。Planner 在 fixed-cost 阶段按“可能包含”
      保守计费，finalize 后仅当 web_search 真 direct 才装载；对应 decisions 继续输出
      `forced_deep_research/image_tool_filtered`，不能因重构静默丢失。
- [ ] `ToolCapabilityResolver.prepare()` 只读一次 catalog snapshot 与一次 strict policy snapshot，依次应用
      exposure policy → env/session-visible/config → mode，产出 direct/deferred/conditional-direct/denied 与逐工具
      reason；provider restriction 不属于 Resolver，也不得改变 provider-neutral logical set；direct 与
      conditional 携带 immutable exact schema，deferred 只保留 bounded descriptor。`finalize()` 只能从 draft
      移动 conditional capability，不允许按名字读 Registry。
- [ ] `Slice/ContextBundle` 增加 `tool_exposure_intent`；ToolComponent 成为唯一 intent producer，Assembler
      检测第二个 producer 立即报 contract error。Context OS ON 时并行 fan-out 不生成最终 schema；
      `PreparedContext` 的 `tool_set` 由 Task 3.1 在汇总 protected direct requirements 后一次冻结。
      OFF 保持当前 `tool_schemas` list 行为。
- [ ] `ToolCapabilityScopeStore` 注册到 `service_context`，实现 session/request 绑定、immutable activation revision、
      冻结 `ToolEligibilityContext`、TTL、run-finally/session-delete purge；authorizer 必须从 scope 取 task_type/mode，
      不从 tool params 或全局“最近 session”推断。禁止持久化对象中出现 handler、gate、credential 或原始 params。
- [ ] Registry 增加可选 capability authorizer并按 §3.3 固定顺序接线；ON + `origin=agent` 时在
      PermissionGate/handler 前，用 execution context 对应的 session eligibility 和 strict current policy snapshot
      校验 direct/activated scope；policy 读取失败返回 `tool_policy_unavailable`，policy fingerprint 或 eligibility
      变化返回 `capability_stale`。同一 execute/describe 边界 strict policy 只读一次并放入 host-only context 供
      bridge 复用，activation commit 作为新边界再读一次。OFF、legacy tests 和 `execute_prepared()` 字节/授权
      语义不变。
- [ ] `execute_tool(..., *, execution_context=None)` 只以 kw-only host 参数接收 scope；新增
      `current_tool_execution_context()` ContextVar accessor，handler params 永不出现 scope。async handler 直接
      继承，sync executor 使用 `copy_context().run`，所有异常/timeout/cancel 路径 finally reset。
- [ ] Context OS ON 时 AgentLoop 构造阶段要求 registry 同时支持 capability-aware `execute_tool` 和 authorizer；
      缺任一能力立即返回 `tool_capability_runtime_unavailable`，不得退回 legacy `dispatch()`。OFF 保持当前
      `_supports_execute_tool` fallback，durable workflow 不受此 guard 影响。
- [ ] 为 `default.yaml` 八类 task 建 migration golden：OFF 的 names **及顺序**与校准 HEAD 完全一致；ON 才读取
      `tool_exposure`。测试分别覆盖仅旧字段、双字段和非法新 selector，证明总回滚不依赖有损逆变换。
- [ ] 增加 MCP replace/unregister、session-aware `visible_when` 翻转、Session A 有 goal/Session B 无 goal 的隔离、
      config disabled、dangerous allowlist、跨 session scope、TTL、catalog revision 和 policy deny-first 测试；
      config provider 在 resolve/execute 抛错时断言 fail closed。旧 fake registry 不实现新协议时 ON 明确 fail、
      OFF 兼容。

**完成信号**：AC-CTX-17/19 的 catalog snapshot、policy intent 与执行授权基础成立；初始最终 schema 冻结
在 Task 3.1 接线，AgentLoop 动态激活留给 Task 4.0。

### Wave 2 — 统一任务投影与压缩前写回

#### Task 2.1：实现只读 `TaskContextProjector`

**依赖**：Task 0.2  
**文件**

- 新增 `backend/deskpet/agent/context_task.py`
- 修改 `backend/deskpet/session/task_scope.py`（只复用/暴露稳定 effective sid，不加语义切题）
- 修改 `backend/deskpet/agent/goal_store.py`（仅增加稳定读取接口，禁止改事实语义）
- 复用 `backend/deskpet/agent/task_graph.py`
- 复用 `backend/deskpet/workflows/proposal_state.py`
- 新增 `backend/tests/test_task_context_projector.py`

**步骤**

- [ ] 定义不可变 `TaskContextSnapshot` 与 `SourceRevision`。
- [ ] 按 §4.3 固定 `workflow:{run_id} → goal:{goal_id} → session:{effective_sid}` precedence；
      `/new`/payload new session 是普通任务唯一显式切换，禁止 LLM 自动换 scope。
- [ ] 建立 Goal/GoalTask/Workflow/Receipt/Artifact 的字段 precedence 表并编码为确定性合并函数。
- [ ] 明确 receipt 状态映射：accepted/pending 不得进入 completed。
- [ ] 显式 Goal、Workflow、普通长任务、闲聊四类 fixture 全覆盖。
- [ ] 普通 task row 只在 explicit new 或首次结构化 plan/todo/tool/receipt/artifact 证据时创建；
      assistant prose/L3 不创建也不改 completed/pending。
- [ ] 冲突时保留高权威值并在 `conflicts/reason` 记录低权威来源，禁止静默覆盖。
- [ ] 输出一个 protected task fragment；无 active task 返回 `None`。

**完成信号**：AC-CTX-4/7；相同事实源顺序变化不改变 canonical snapshot。

#### Task 2.2：新增 V18 Context OS migration 与 snapshot store

**依赖**：Task 2.1  
**文件**

- 新增 `backend/deskpet/memory/migrations/010_context_os_v18.sql`
- 修改 `backend/deskpet/memory/migrator.py`
- 修改 `backend/deskpet/memory/memory_v2_schema.py` 的 canonical table/schema 清单，不添加 lazy ensure
- 新增 `backend/deskpet/memory/context_snapshot_store.py`
- 新增 `backend/tests/test_memory_v18_context_os_migration.py`
- 新增 `backend/tests/test_context_snapshot_store.py`

**步骤**

- [ ] 用真实 migrator 测空库、V17 旧库、重复迁移、事务失败回滚，同时验证 snapshots/segments/index 与
      tool capability 摘要列。
- [ ] 实现 `get()`、`compare_and_swap()`、`flush_once(cycle_id)`、
      `persist_projection_with_tool_context_cas(projected_snapshot, *, expected_row_revision, prepared_toolset_summary)` 与
      `update_tool_context_cas(*, expected_row_revision, prepared_toolset_summary)`。前者负责 active task 首次 row 不存在时
      以 expected revision=0 原子创建“完整投影 + 工具摘要”，或对已有 row 以当前 revision 原子整体更新；后者只
      更新已存在 row 的工具摘要。两者与 task state 使用同一 row revision/hash 规则，不允许独立
      last-write-wins 覆盖并发 projector/compaction 更新。
- [ ] 两个 CAS 方法统一返回 `SnapshotWriteReceipt(previous_row_revision,
      new_handle, persisted_tool_scope_revision, attempt_id)`；调用方必须保存 receipt 的新 handle，禁止把
      `PreparedToolSet.revision/scope_revision` 传到该参数。增加类型/contract test，刻意传相同数值也要按字段来源
      断言，不允许位置参数混淆（API 使用 keyword-only）。
- [ ] 实现 `await_snapshot_commit_ack()`：底层 DB CAS 单独 task + shield；cancellation 时等待 task terminal，
      committed/rolled-back outcome 可观测后才重新 raise。故障注入覆盖 commit 前 cancel、commit 后 return 前
      cancel、重复 cancel 与 DB exception，断言无“未知是否提交”的状态。
- [ ] CAS 失败时重新读取并重新投影，不用 last-write-wins。
- [ ] `update_tool_context_cas` 只 merge 工具摘要列；若 row 不存在或 expected row revision 已变化，返回
      typed missing/conflict，不覆盖 objective/pending/artifacts。initial prepare 使用完整 projection 方法，CAS
      冲突时可重新读取事实源、重新投影并完整 replan 一次；activation 阶段不在
      旧 messages 上盲重试，直接返回 `context_snapshot_conflict` 且 scope/local set 保持旧 revision。
- [ ] 相同 hash 不增 revision；相同 cycle id 不重复写入。
- [ ] 所有 JSON 先 canonicalize/validate，损坏 row safe-fail 并发诊断事件。
- [ ] `last_prepared_toolset_json` 写入前按 §4.2 白名单裁成 names/hashes/reasons/token/provider adapter 摘要；
      读取后只做诊断/重新 resolve 输入，永远不直接恢复 scope 或完整 schema。
- [ ] session 删除时级联清理 `session:{effective_sid}` row；goal/workflow row 随事实源保留可恢复投影。
- [ ] OFF 路径不读写 snapshot 表。

**完成信号**：AC-CTX-5；`PRAGMA user_version=18`，迁移前数据保留，竞争测试无丢更新。

#### Task 2.3：替换 pre-compaction `MEMORY.md` append

**依赖**：Task 0.3B、Task 1.3、Task 2.2  
**文件**

- 修改 `backend/agent/agent_loop.py`
- 修改 `backend/deskpet/agent/context_compressor.py`
- 修改 `backend/main.py::build_agent`
- 修改 `backend/agent/agent_loop.py::AgentLoop.__init__` 与 `run`
- 修改 `backend/pipeline/voice_pipeline.py::_run_with_tools` 的 `build_agent` wiring
- 新增 `backend/tests/test_context_precompact_flush.py`
- 修改 `backend/tests/test_agent_loop_compaction_wiring.py`

**步骤**

- [ ] 在每次 compact 生成唯一 `compaction_cycle_id`。
- [ ] 先从权威 stores 投影并 `flush_once`，commit 成功后才调用 compressor。
- [ ] 传入真实 pending tasks、decisions、artifacts，而不是只传 goal + last user。
- [ ] 同一 flush 关联本 request 最新已提交 `PreparedToolSet` 摘要；activation 尚未提交或 schema/policy 已
      stale 时不写成 active，记录 `stale_reason` 并由下一 request 重新 resolve。
- [ ] 删除/禁用当前向全局 `MEMORY.md` 追加 `[task-state]` 的默认路径。
- [ ] flush 失败：先做可逆 pruning、保留 current/protected task/最近完整 tool group 并发诊断事件；
      若重算后仍超预算，返回可恢复 budget error，禁止在未写回时静默 compact 或超限出站。
- [ ] 模拟 DB locked、CAS 冲突、进程在 flush 后/compact 前退出，验证可恢复与幂等。
- [ ] OFF golden test 证明仍走 legacy `MEMORY.md` preflush，直到 Task 5.1 整体翻 ON。

**完成信号**：AC-CTX-5/10；压缩前后 snapshot 任务字段一致，全局 memory 文件不增长 task-state 垃圾。

#### Task 2.4：实现单 Session coverage tree 与精确 page-in

**依赖**：Task 0.3B、Task 1.3、Task 2.2  
**文件**

- 新增 `backend/deskpet/memory/context_segment_store.py`
- 新增 `backend/deskpet/agent/session_history_planner.py`
- 新增 `backend/deskpet/tools/session_history_tools.py`
- 修改 `backend/deskpet/memory/session_db.py`
- 修改 `backend/main.py` 的 memory/session tool bind 与 Session 删除路径
- 新增 `backend/tests/test_context_segment_store.py`
- 新增 `backend/tests/test_session_history_planner.py`
- 新增 `backend/tests/test_session_history_page_in.py`

**步骤**

- [ ] `SessionDB` 增加按 session + message-id boundary 的分页读取、count/hash 读取；返回完整
      role/content/reasoning/tool_calls/tool_call_id，不拆 causal group。
- [ ] coverage planner 接收 `current_message_id`，把该 row 计入 current coverage 但不再作为 history 注入；
      重复文本的不同 message ids 仍分别保留。
- [ ] `ContextSegmentStore` 实现 level-0 raw index append/seal、相邻节点 merge、CAS revision、stale
      invalidation 与删除清理；禁止 overlap children。
- [ ] summary node 只能在 child source hash 仍有效且 `SessionCoverageReport` 无 gap/overlap 后 commit。
- [ ] `SessionHistoryPlanner` 严格实现 §4.8：全量 raw 能放下则全量；否则动态 raw tail + 最低成本完整
      tree cover；每个 summary cover 携带 ref 但 coverage 只计 summary；固定 `l2_top_k` 仅保留在 OFF legacy path。
- [ ] summary nodes 尚未齐备时，planner 返回互不重叠的 bounded `CoverageCompactionJob[]`，而不是静默
      drop；Task 3.2 完成 jobs 后必须重新 plan/re-budget。
- [ ] `session_history_page_in` schema 不接受 session id，只接受 segment id/cursor；handler 从 tool runtime
      `_session_id` 解析当前 session，并按本轮 budget 返回完整 causal groups，跨 session id 返回拒绝。
- [ ] `SessionHistoryPlanner` 不直接调用 Resolver。Task 3.1 的一次 resolve draft 总是冻结
      `session_history_page_in` 为 conditional exact capability；只有 summary path 才通过 draft.finalize 把它
      加入同一 `PreparedToolSet`。全量 raw 路径不暴露该工具、也不支付其 schema tokens，不允许 planner
      私自 append schema 或二次读 Registry。
- [ ] 新消息 append 只更新开放 raw leaf；Session 删除 purge；消息删除/内容 hash 变化使相关祖先 stale。
- [ ] 覆盖空 Session、200K history/1M window 全量 raw、2M history/1M window 分层 cover、交错全局
      message ids、删除、并发 append/merge、summary 失败、最小 root 仍超限 final BLOCK。
- [ ] OFF golden test 保持当前 policy `l2_top_k` 与工具集合，不创建/读取 segment rows。

**完成信号**：AC-CTX-16；每个出站 request 的 coverage report 对全部未删除 Session 消息无 gap/overlap，
且旧细节可由当前 session 的 reference 精确 page-in。

### Wave 3 — 单一压缩 owner 与整请求预算

#### Task 3.0：让压缩模型跟随 Session 或由设置选择

**依赖**：Task 0.3A、Task 1.2  
**文件**

- 修改 `backend/config.py`：新增 `ContextCompactionConfig` 与 `[context.compaction]` loader
- 修改根目录 `config.toml`
- 新增 `backend/deskpet/agent/compression_model_resolver.py`
- 修改 `backend/p4_ipc.py`：新增 `context_compaction_get/set`
- 修改 `tauri-app/src/components/ModelContextCard.tsx`
- 修改 `tauri-app/src/types/messages.ts`
- 新增 `backend/tests/test_compression_model_resolver.py`
- 新增 `backend/tests/test_context_compaction_ipc.py`
- 修改 `tauri-app/src/components/ModelContextCard.test.tsx`

**步骤**

- [ ] 配置默认 `model="follow_session"`；设置 IPC 只接受该 sentinel 或当前 provider catalog 中的 model id，
      原子写入用户 config，禁止自由文本与未知 model。
- [ ] `CompressionModelResolver` 严格实现 §4.7：`follow_session` 返回同一 ordered chain；显式 model 返回
      单一 candidate（只允许 transport retry，不跨 model fallback）；均记录 requested/resolved/actual source。
- [ ] 设置卡增加“上下文压缩模型”下拉：跟随当前会话 + 可用模型；显示最近 compact 实际 model。
- [ ] 移除 `ContextCompressor.model="claude-haiku-4-5"` 的误导性默认；compressor 接收已经解析好的
      provider shim/model，不允许 shim 再忽略一个名义 model 参数。
- [ ] 显式模型 unavailable/auth/call failure 时不提交 summary、不静默 follow_session；产生
      `compression_model_unavailable` 并交回 planner 决定 raw/既有 cover/final BLOCK。
- [ ] OFF golden test 保持当前 `local_llm or cloud_llm` shim 与 UI payload，不读取新配置。

**完成信号**：AC-CTX-15；设置选择、实际出站模型、attempt report 三者一致，失败路径无静默换模。

#### Task 3.1：实现 `ContextRequestPlanner`

**依赖**：Task 0.2、Task 1.3、Task 2.4、Task 3.0  
**文件**

- 新增 `backend/deskpet/agent/context_budget.py`
- 修改 `backend/deskpet/agent/assembler/budget.py`
- 修改 `backend/deskpet/agent/assembler/assembler.py`
- 修改 `backend/agent/token_budget.py`
- 修改 `backend/agent/tool_use_shim.py`
- 修改 `backend/providers/openai_compatible.py`
- 修改 `backend/llm/anthropic_adapter.py`
- 修改 `backend/deskpet/agent/assembler/__init__.py::build_default_assembler`
- 修改 `backend/main.py` 中 `_assembler = build_default_assembler(...)` 的模块级构造块
- 修改 `backend/deskpet/memory/context_snapshot_store.py`
- 新增 `backend/tests/test_context_request_planner.py`

**步骤**

- [ ] 从本轮 resolved provider/model capability 读取 context window，移除 main 的硬编码 32K 事实源。
- [ ] 在 `PreparedContext` 增加 `attachment_refs: list[AttachmentRef]`；附件先成为 message content block，
      refs 只归因不二次计费。
- [ ] 严格实现 §4.4 公式：`total_reserve=max(policy_reserve,generation_reserve+safety_floor)`；
      hidden reasoning 包含在 generation reserve，禁止再扣第二次。
- [ ] 定义 provider `prepare_tool_payload(prepared_tool_set)` seam：OpenAI-compatible identity 使用最终 request
      builder 的 canonical serializer；Anthropic 抽取当前 `input_schema` 转换为纯函数；未知 provider 只有显式
      声明 identity capability 才可使用，否则 ON attempt 返回 `tool_schema_adapter_unavailable`。返回
      `PreparedToolPayload`，provider call 必须直接消费其 `tools` 对象，不得在发送前再转换第二次。
- [ ] `estimated_input` 只计算 final wire messages + 本 attempt `PreparedToolPayload.wire_tokens`；
      current/L2/attachments/tool results
      已在 messages 中时不得按分类再次相加，分类 tokens 仅做和为总数的 attribution。
- [ ] 先给 protected prefix/tools/current/reserve 计费，再把 transcript 剩余预算交给
      `SessionHistoryPlanner`；完整 Session raw 能放下时必须全量装载，禁止 policy top-k 提前截断。
- [ ] 超窗时只接受 `SessionCoverageReport.valid=True` 的 summary cover + raw tail + page-in refs；coverage
      成本也进入同一 request estimate，不能用“数据库里还有”替代 prompt 预算。
- [ ] 首轮没有 provider usage 时也完整计 schema；之后 authoritative usage 只作为 tokenizer 校准 floor，
      不能掩盖本轮新增内容。
- [ ] 裁剪顺序固定：重复/旧 tool payload → 低优先 retrieved/path rules → 非 protected history → compact；
      platform/current/protected task/最近完整 tool group 永不直接丢弃。
- [ ] 对 Session transcript 内的旧 tool payload 做 prune 时同步生成/选择确定性 summary node；任何直接
      truncate/drop 而未更新 coverage 的结果令 request plan 无效。
- [ ] provider chain 的公共可逆裁剪按最小 effective input budget；每个 actual attempt 在选定
      provider/model 和实际 `tools`/`None` 后重新预算，不能复用 manager 早先固化的 model_info。
- [ ] Tool activation proposal 必须走同一 planner：以候选 immutable tool-set revision 重新计算 schema tokens，
      允许正常 pruning/preflush/compact；fit 才返回 commit token，仍超限则撤销候选并产生
      `tool_activation_budget_exceeded`，不得让 scope 与下一次 provider payload 分叉。
- [ ] 严格实现 §3.3 无环两阶段：先冻结 fixed cost，收集 `ToolExposureIntent` 与固定 typed requirements，
      调用 `ToolCapabilityResolver.resolve_draft()` 恰好一次；用 base-direct cost 判断 full raw，随后仅调用
      draft.finalize(empty/page-in) 生成初始 set，再完成 history cover。budget attribution 与 provider payload
      均使用 finalized exact schemas；任何后置 producer 试图 append schema 或二次 resolve 都触发 contract error。
- [ ] `prepare_initial()` 成功后，若本轮存在 active `TaskContextSnapshot`，立即调用
      `persist_projection_with_tool_context_cas()`：row 不存在时以 expected revision=0 原子创建完整 task
      projection + 初始 PreparedToolSet 摘要，row 已存在时按 projector 读取的 revision CAS 更新；不得对未创建
      row 调只支持 merge 的 `update_tool_context_cas()`。不等待 compact；无 active task 时只把 canonical tool
      facts 留在 `PreparedContext`，不得因此创建空 snapshot。成功 receipt 的 `new_handle` 必须写入
      `PreparedContext.active_snapshot_handle` 与新建 scope record，后续绝不从 tool scope revision 猜 row revision。
      Task 4.3 在真正 provider attempt 边界创建/写入
      ContextAttemptStore，不是 Task 3.1 的依赖。`replan(existing_tool_set=...)` 永不重复持久化无变化 hash，也不
      重新调用 Resolver。
- [ ] 调整 gate 顺序为 estimate → reversible pruning → 必要时 preflush + compact → re-estimate →
      final BLOCK，保证 compressor 有自救机会且最终绝不超限。
- [ ] planner 若返回 coverage jobs，本阶段只产出 plan，不直接调用 LLM；由 Task 3.2 的单一 compressor
      owner 执行后再次调用 planner，避免 budget planner 与 compressor 循环拥有彼此。
- [ ] `BudgetAllocator` 改成 planner 的 component-share 适配器，不保留另一套 window 决策。
- [ ] 用 8K（`requested_max_tokens=1024`）/32K/200K fixture、巨大 schema、附件、多字节中文、
      未知 tokenizer 测 effective input budget；另测 8K + `max_tokens=8192` 明确 final BLOCK。
- [ ] OFF golden test 仍使用 legacy 32K assembler + 当前 token gate，不创建 planner report。

**完成信号**：AC-CTX-7/8/16；每个测试中 serialized request estimate + reserve ≤ effective window，且
Session 覆盖无 gap/overlap。

#### Task 3.2：收敛到单一有损压缩 owner

**依赖**：Task 0.3B、Task 1.3、Task 2.3、Task 3.1  
**文件**

- 修改 `backend/agent/context_manager.py`
- 修改 `backend/main.py` 的 `prepare_chat_messages_for_chain` 路径
- 修改 `backend/agent/agent_loop.py`
- 修改 `backend/tests/test_context_manager.py`
- 修改 `backend/tests/test_p6_context_manager.py`
- 修改 `backend/tests/test_agent_harness_main_callsite_contract.py`

**步骤**

- [ ] 新路径开启时，preflight 只做结构校验/确定性 trim，不调用 `history_compactor` 有损摘要。
- [ ] runtime 预算越界仅调用 `ContextCompressor`；一次 cycle 可执行多个互不重叠 segment jobs，但 spy
      必须证明 owner invocation/cycle id 唯一、同一 source range 最多提交一次且没有 legacy summarizer。
- [ ] 在 raw transcript 接近 `compact_at_pct` 且仍能安全调用 summary model 时预构建下一层 segments；
      不在每轮固定调用 LLM，未达到 watermark 时零额外 compression call。
- [ ] compact 前由 `CompressionModelResolver` 解析本次 provider/model；summary 成功后先校验 source hash/
      coverage，再原子提交 segment，最后才替换 working transcript。
- [ ] rollback flag 关闭 reactive path 时，legacy preflight 可恢复，但两者互斥。
- [ ] compact 输出后重挂载 task snapshot、稳定规则、active Skill body、最近完整 tail。
- [ ] 修改 `ContextCompressor._partition()`：按 fragment placement/lifetime 重建，禁止把 transcript/control
      中所有 `role=system` 无条件提升到 prefix；验证 current-request、plan、self-check、evidence、
      completion nudge 与触发 turn 的相对顺序。
- [ ] provider 错误重试使用同一 prepared snapshot/fingerprint，除非有明确的新 tool result。
- [ ] compact 完成后的 replan 必须传入 `existing_tool_set`，只验证 direct refs 并重新预算；禁止再次调用
      Resolver、改变 direct/deferred 选择或重新读取完整 catalog。provider fallback 同样复用该 set，
      仅按 candidate adapter 重新计算 schema tokens/表达能力；adapter 无法表达任一 schema 时只令该 provider
      attempt 返回 `tool_schema_unsupported` 并进入既有 fallback，logical set 不变，不得标成 capability stale。
- [ ] dedicated compaction model 失败时不回退 main model；保留 raw/旧 valid cover，重新预算后必要时 BLOCK。
- [ ] OFF golden test 证明 legacy preflight/reactive wiring 与当前 call count 一致。

**完成信号**：AC-CTX-6/10/13/15/16；默认 flag ON，测试证明不存在双摘要、静默换模或 coverage gap。

#### Task 3.3：所有入口采用同一 prepared contract

**依赖**：Task 1.3、Task 3.2  
**文件**

- 修改 `backend/main.py`
- 修改 `backend/agent/agent_loop.py`
- 修改 `backend/pipeline/voice_pipeline.py`
- 修改 `backend/tests/test_agent_harness_main_callsite_contract.py`
- 修改 `backend/tests/test_voice_task_scope.py`
- 修改 `backend/tests/test_p4s20_chat_with_tools.py`

**步骤**

- [ ] text chat 直接把 `PreparedContext` 的 messages/`PreparedToolSet`/cache hint/assembly decisions 传入
      AgentLoop；删除 `_bundle_tool_names` 提取和 `tool_names_filter` 生产调用。
- [ ] 把 `_buffer_short_followup_stream` 的 `generate_image not in _bundle_tool_names` 改为读取
      `PreparedContext.tool_set.has_direct("generate_image")`；该判断必须使用 finalized/activated current set，
      不能回查 Registry，也不能因删除 names 临时变量丢掉图片假完成拦截。
- [ ] `AgentLoop.run/_run_impl` 增加 `prepared_tool_set` 参数；Context OS ON 时禁止调用
      `self.tools.schemas()`，每个普通 attempt 使用当前 immutable revision 的 `logical_schemas()`；force-finish
      唯一例外为 `tools=None`。OFF 保留 `tools_filter/tool_names_filter` 兼容测试路径。
- [ ] 每个 chain candidate 先生成其 `PreparedToolPayload` 并交 planner 预算，再把同一 payload.tools 传给
      shim/provider；logical fingerprint 不变、adapter wire hash 可不同。adapter unsupported 作为该 attempt 的
      permanent capability failure 进入下一个 provider，不触发 Resolver 或改写 tool set。
- [ ] ON 路径禁止 `_dispatch_tool` 退回 `dispatch()`；每个 tool call 必须构造
      `ToolExecutionContext(scope/session/request/origin)`。fake/third-party registry 若不实现 capability contract
      只能在 OFF tests 使用，ON contract test 必须 fail closed。
- [ ] voice 共享 fragment/budget/compaction contract，TTS/voice prompt 仍由 voice 层追加专属 fragment。
- [ ] text 与 voice 对同一 session 使用同一个 `SessionHistoryPlanner/ContextSegmentStore`；不得分别生成
      不一致的 coverage tree 或让 voice 回退 top-k 后宣称拥有全 Session。
- [ ] code mode 和 web/research tool scopes 只注册各自 producer/tool set，不复制 planner/compressor。
- [ ] 收敛 voice 裸 `_AgentLoop` fallback：Context OS 开启时 build_agent 失败应 safe-fail 为无工具回答或
      明确错误，但不能悄悄绕过预算/压缩/report 合同。
- [ ] optional component 失败时记录 omitted reason，仍保留 current + recent tail。
- [ ] 更新 Task 0.1 topology contract，证明所有 user-response provider call 都经过统一 attempt-planner seam；
      完整 report 在 Task 4.3 接入该 seam。
- [ ] OFF golden test 证明 text/voice 均保持现有入口与裸 voice fallback 语义。

**完成信号**：AC-CTX-12 的 fragment/budget 合同；text/code/web/research scope 与 voice venue 测试通过。

### Wave 4 — 渐进披露与真实诊断

#### Task 4.0：把现有 `tool_search` 升级为同轮渐进式 capability hydration

**依赖**：Task 1.3、Task 3.1、Task 3.3  
**文件**

- 修改 `backend/deskpet/tools/tool_search.py`
- 修改 `backend/deskpet/tools/capabilities.py`
- 修改 `backend/deskpet/tools/registry.py`
- 修改 `backend/deskpet/agent/context_budget.py`
- 修改 `backend/deskpet/memory/context_snapshot_store.py`
- 修改 `backend/agent/agent_loop.py::_dispatch_tool` 与 provider-iteration 边界
- 修改 `backend/main.py::build_agent`
- 修改 `backend/tests/test_deskpet_tools_search.py`
- 新增 `backend/tests/test_tool_capability_hydration.py`
- 新增 `backend/tests/test_tool_capability_execution_guard.py`
- 修改 `backend/tests/test_deskpet_agent_loop.py`

**步骤**

- [ ] 保留工具名 `tool_search` 兼容现有 prompt，但 ON 路径不再调用 `registry.all_specs()`；新增
      `tool_describe`、`tool_activate`。三个 handler 都从 host-only ContextVar 的
      `ToolExecutionContext.scope_id` 查
      `ToolCapabilityScopeStore`，用户参数 schema 不暴露 session/request/scope id。
- [ ] 冻结注册方式：模块级现有 legacy `tool_search` 保留给 OFF；新增
      `register_capability_bridge_tools(registry, service)` 只在 `main.py` 启动读取
      `context_os_v1=true` 时调用，以 `replace_allowed=True` 显式替换 tool_search 并注册 describe/activate。
      OFF 启动不注册新名字、也不替换旧 handler；切换总回滚开关要求重启，与其他启动期 wiring 一致。
      测试覆盖 ON/OFF 重复启动、合法 replace 与 name-conflict，不允许 auto-discovery 偶然覆盖。
- [ ] `tool_search(query, limit<=10, cursor?)` 只搜索 scope.deferred bounded descriptors，采用稳定排序并返回
      capability_id/name/description/toolset/source/permission_category/dangerous/schema_hash；不返回完整 schema，
      不返回 denied/env-hidden/disabled/其他 session 项。
- [ ] `tool_describe(capability_id)` 只读取同 scope deferred ref，重新核对当前 Registry spec 的
      schema_hash/spec_version/permission_policy_version，并通过与初始 resolve 相同的 `eligible_specs()`、
      deny-first、同一 `ToolEligibilityContext` 和 strict current `ToolPolicySnapshot` 检查；这里不做 provider
      restriction。policy provider 读取失败返回 `tool_policy_unavailable`；fingerprint、spec 或 session eligibility
      不匹配返回 `capability_stale` 并要求下轮重新 resolve；匹配才返回 exact schema 和一次性 describe nonce。
- [ ] `tool_activate(capability_id, schema_hash, describe_nonce)` 只生成 host-only
      `ToolActivationProposal(prepared_capability=exact_schema_copy, base_scope_revision, nonce)`，不直接改 scope、
      也不调用目标 handler。nonce 单次使用、绑定 scope/revision/
      capability/hash，且在 proposal 和 commit 两个边界再次运行同一 session eligibility/strict policy snapshot
      检查；policy 读取失败为 `tool_policy_unavailable`，跨 session、重复使用、未 describe、过期、policy hash
      或条件翻转均 fail closed；provider 表达能力只在后续 attempt adapter 处理。
- [ ] 把 `_dispatch_tool` 内部返回值扩成 `ToolDispatchOutcome(model_result, control_directive=None)`；普通工具
      适配为原 JSON 字符串，只有 `tool_activate` 可以产生 typed directive。directive 不序列化给模型，避免
      任意 tool result 伪造 host 控制；Registry/PermissionGate/timeout/breaker 仍完整经过一次。
- [ ] AgentLoop 收到 proposal 后调用 `PreparedToolSet.activate()` 产生 candidate revision，再交 Task 3.1 planner
      重预算并严格执行 §3.3 activation 提交顺序；在 scope lock 内先完成所有可失败校验/序列化，active-task
      DB CAS 成功后只允许无 await 的 `commit_prevalidated()` 与 local assignment；成功时更新
      snapshot/scope/local set、把模型可见
      tool result 改为 `activated`，下一 provider attempt 直接带 exact schema；失败时旧 revision 不变并返回
      结构化 budget/stale/persist 错误。
- [ ] 目标工具下一次 direct call 必须再次经过 Registry capability authorizer、PermissionGate、handler、
      Artifact/Receipt/VerifyGate；bridge 不能代理 `tool_call` 或隐藏真实工具名，保证既有审计闭环连续。
- [ ] run finally、cancel、timeout、session delete 清 scope；MCP register/unregister/replace 后旧 descriptor
      describe/activate 失败，但同 request 已 direct 且 hash 未变的工具不因无关 catalog revision 误失效。
- [ ] 每次 provider attempt 和每次真实 execute 前验证所有相关 direct/activated refs 的 current hash/eligibility；
      已 direct 的 MCP 工具若在两次 attempt 间 unregister/replace，当前 run 返回 `tool_catalog_stale`，不继续把
      旧 schema 发送给 provider，也不自动用同名新 spec 替换。
- [ ] reserved bridges 只有 deferred 非空且 `deny`/global disabled/session mode policy 均允许时才加入；任一
      bridge 被 deny 时关闭整条 discovery surface 并记录 reason，不能留下 search 可见但 activate 不可用的半套。
- [ ] 在 AgentLoop 当前构造 `tool_coros` 之前新增 exclusive-turn 检查：只要本 response 含 `tool_activate` 且
      tool-call count != 1，就不 dispatch 整个 batch，并按每个原 call id 写入结构化结果，提示下一轮单独
      activate；单独 activation 才进入 proposal/CAS。此规则保留 OpenAI tool_call/result 配对，不产生半批
      已执行副作用，也不错误依赖当前未接线的 `partition_dispatch()`。
- [ ] 用 scripted fake provider 真跑四次迭代：search → describe → activate → target direct call；断言 provider
      payload 的 schema 数/hash 在 activation 后恰好变化一次，target receipt 使用真实工具名。另覆盖空 deferred
      不暴露 bridge、denied 不可搜索、Session A 有 goal/Session B 无 goal、跨 session、nonce replay、
      strict policy provider 在 describe/activate/execute 抛错、超预算 rollback、force-finish None；并分别覆盖
      deferred describe→activate 间 MCP disconnect，以及已 direct/activated MCP 在下一 provider attempt 前
      unregister/replace，二者都不得继续发送旧 schema 或调用 handler。
- [ ] activation fault-injection 覆盖 budget/policy/snapshot serialize/DB CAS 等每个可失败点，断言它们都发生在
      DB commit 前且 scope/local/result 不变；静态/spy contract 断言 DB CAS 后到 `commit_prevalidated()`/local
      assignment/result append 之间除 commit-ack settlement 外无 await、provider/config/serialization 调用，也不
      存在第二次 scope CAS。另在 DB commit 后、CAS coroutine return 前注入 cancellation：helper 必须先取得
      receipt、记录 diagnostic-ahead、保持 capability scope/local/result 未激活并 purge run scope，再传播取消。
- [ ] OFF golden test 保持当前 `tool_search` substring/schema 返回行为和 AgentLoop 静态 schemas，不注册新
      bridge；Task 5.1 翻 ON 后，旧行为只作为总回滚路径存在。

**完成信号**：AC-CTX-18/19；同一 AgentLoop run 可在授权边界内发现并激活工具，实际 handler 调用没有
旁路，schema token、scope revision、provider payload、receipt 四者可对账。

#### Task 4.1：L3 与 Skill 的 page-in 合同

**依赖**：Task 1.1、Task 2.4、Task 3.1、Task 3.2  
**文件**

- 修改 `backend/deskpet/agent/assembler/components/memory.py`
- 修改 `backend/deskpet/agent/assembler/components/skill.py`
- 修改 `backend/deskpet/tools/memory_tools.py::_memory_read_handle`、`_memory_search_handle`
- 修改 `backend/deskpet/tools/session_history_tools.py` 的 page-in handler
- 修改 `backend/tests/test_deskpet_skill_remount_after_compaction.py`
- 新增 `backend/tests/test_context_progressive_disclosure.py`

**步骤**

- [ ] L3 自动注入 bounded 摘要、source id、score、detail ref；正文通过现有 memory tool 展开。
- [ ] 保留普通聊天自动连续性，不改成完全手动检索。
- [ ] Skill 常驻紧凑索引，正文只在显式/语义命中时加载，compact 后 active body 重挂载。
- [ ] page-in 的正文进入 request planner，禁止绕过预算。
- [ ] Session history reference 展开后替换对应 summary 的细节窗口，不与原 summary/raw 重复计入；
      tool result 标注 segment/source hash，下一轮可验证仍属当前 Session。
- [ ] 对 prompt injection 风险只标来源/边界，不改变现有 permission/receipt gate。

**完成信号**：AC-CTX-9/10/16；未命中正文不占预算，命中后可追溯、可裁剪、可重挂载且不越权。

#### Task 4.2：按路径加载项目规则

**依赖**：Task 4.1  
**文件**

- 新增 `backend/deskpet/agent/assembler/components/project_rules.py`
- 修改 `backend/deskpet/agent/assembler/__init__.py::build_default_assembler`
- 新增 `backend/tests/test_project_rules_component.py`

**步骤**

- [ ] 仅在 code/workspace scope 启用；普通聊天不扫描磁盘。
- [ ] 从 workspace root 到 active path 逐层解析 AGENTS/rules，越近路径优先，记录来源与 hash。
- [ ] active path 只来自已验证 workspace/tool path，不从任意 prompt 字符串直接信任路径。
- [ ] 规则按 budget 截断并输出命中/未命中 reason；文件变化后下一轮重读。
- [ ] compact 后若路径仍 active，规则重新挂载；离开路径则卸载。

**完成信号**：AC-CTX-9/10；嵌套优先级、越界路径、符号链接/Windows path、普通聊天零扫描测试通过。

#### Task 4.3：让 ContextTrace 展示 prepared request 的事实

**依赖**：Task 0.3B、Task 1.2、Task 3.1、Task 3.3、Task 4.0  
**文件**

- 新增 `backend/agent/context_report.py`
- 修改 `backend/providers/openai_compatible.py::chat_with_tools`、`_legacy_chat_with_tools_nonstream` 与 stream 公共出站 seam
- 修改 `backend/main.py::_compute_context_breakdown` 与 `context_usage` event
- 修改 `backend/main.py::_make_str_llm_call` 为辅助调用设置 purpose context
- 修改 `backend/agent/agent_loop.py`、`backend/agent/tool_use_shim.py`、`backend/agent/plan.py`、`backend/agent/context_manager.py`
- 修改 `backend/deskpet/agent/assembler/classifier.py::_llm_tier`
- 修改 `backend/deskpet/agent/context_compressor.py::compress`
- 修改 `backend/deskpet/memory/summarizer.py`、`backend/deskpet/tools/research_tools.py` 的 runtime provider call purpose
- 修改 `tauri-app/src/stores/sessionsStore.ts::ContextUsageSnapshot`
- 修改 `tauri-app/src/App.tsx` 的 `context_usage` event 分发
- 修改 `tauri-app/src/components/ContextTracePanel.tsx`
- 新增 `backend/tests/test_context_attempt_store.py`
- 新增 `backend/tests/test_context_usage_event.py`
- 新增 `tauri-app/src/components/ContextTracePanel.context-os.test.tsx`

**步骤**

- [ ] 为所有 provider call 生成 `purpose + request_id + attempt_id`；capability gate、classifier、planner、
      compressor、agent response、force-finish 分栏，不把辅助 usage 算成用户响应。
- [ ] `backend/main.py` 在 `await _cap_classify(...)` 外设置 `purpose=capability_gate`；
      `TaskClassifier._llm_tier` 设置 `classifier`；`agent.plan` 设置 `planner`；
      `ContextCompressor.compress` 设置 `compressor`；AgentLoop 调 shim 前设置 `agent_response/force_finish`。
- [ ] 对 `rg "await .*chat_with_tools" backend -g "*.py"` 的 runtime 清单逐项标 purpose；排除 tests/scripts
      后不得出现 `purpose=unclassified`。`rg` 只辅助盘点；权威护栏是在 provider 公共 stream/non-stream/
      fallback seam 注入 unclassified sentinel，并用 contract test 令任何 runtime sentinel 失败。
- [ ] 实现 §4.5 `planned→sent→terminal` 状态机、matching usage 回填、orphan diagnostic、每 session 32 条/
      30 分钟 TTL、session-delete purge；`sent` 只由公共 transport coroutine 的首条同步 marker 转换，provider 内
      transport retry 只增计数。
- [ ] 每个 provider attempt 在调用前冻结实际 provider/model/window、最终 messages、实际 schemas 或
      `tools=None`、reserve；agent response/force-finish 额外关联 compact/prune/fragments 因果链。
- [ ] report 从当前 `PreparedToolSet` 读取 scope/revision/registry revision、direct/activated/deferred counts、
      policy/schema fingerprint、逐 schema tokens 与 selection reason；actual wire schema hash 必须与
      Provider payload 相同，禁止从 Registry 或工具名重新推导。
- [ ] tool search/describe/activate 记录独立 typed lifecycle event（query 仅脱敏 hash/preview）；activation
      success/budget-reject/stale/cross-session 与目标工具 receipt 用同一 request/scope id 对账。
- [ ] active task 的 provider attempt 在状态 `planned→sent` 之前调用
      `update_tool_context_cas(expected_row_revision=scope_record.snapshot_handle.row_revision, ...)`；summary 内另写
      `persisted_tool_scope_revision=current_prepared_tool_set.revision`，两者不得互换。成功 receipt 通过锁内
      `advance_snapshot_handle_prevalidated(receipt.new_handle)` 更新 scope record 的 DB handle，但不增加 capability
      revision。CAS 冲突只允许重新读取后按相同 task scope + logical set hash + tool scope revision merge 一次，
      仍冲突或 DB 失败则不发送 request，返回可恢复 `tool_context_persist_failed`。
- [ ] attempt adapter CAS 也走 `await_snapshot_commit_ack()`；若 cancellation 在 DB commit 后、return 前发生，
      先保存新 handle/receipt，并把 matching attempt 标成 `cancelled_before_send`，随后传播取消，provider transport
      不得启动。initial/activation row 的 adapter 字段为空是合法状态；attempt row 的 adapter_state 先为
      `prepared`，是否 actual sent 只看 matching ContextAttemptStore，不得靠 snapshot 猜。无 active task 仍只写
      bounded attempt report。
- [ ] 增加边界测试：adapter CAS 已返回但 transport task 创建前取消、transport task 创建但首条 marker 执行前
      取消，均为 `cancelled_before_send`；marker 执行后取消才允许 `sent→cancelled`，并断言三者都没有错误 usage。
- [ ] report 记录每个 fragment 的 estimated tokens、loaded/trimmed/omitted、reason、cache scope/hash。
- [ ] 单列实际 tool schema tokens、attachments、reserve、planned total 与 matching provider usage。
- [ ] compact attempt 单列 requested/resolved compression provider/model/source/failure；Session coverage 单列
      total/raw/summary message counts、page-in ref count、tree levels、covered ranges 与 gap/overlap/stale verdict。
- [ ] 删除 `tool_count * 60` 作为事实值；若 tokenizer 不可用，明确标 `estimate_method`。
- [ ] provider 回包后只按同一 request/attempt id 回填实际 usage/cache hit，不重写当时 planner 决策；
      chain fallback、force-finish `tools=None`、compact 后重试各自产生独立 report。
- [ ] UI 只显示脱敏 preview/hash，不显示凭据、完整参数或敏感文件正文。
- [ ] OFF golden test 不注册 attempt store/observer，`context_usage` 维持当前 payload shape。

**完成信号**：AC-CTX-11/15～19；UI 总数与 report 一致，provider usage、压缩模型、Session coverage、
PreparedToolSet revision 与实际 provider tool payload 均绑定同一 request/attempt。

### Wave 5 — 默认启用、回归与真实 E2E

#### Task 5.1：配置与回滚

**依赖**：Wave 1–4  
**文件**

- 修改 `backend/config.py::FeaturesConfig` 及 `backend/tests/test_config.py`
- 修改根目录 `config.toml` 的 `[features]` 默认值
- 更新 `docs/beta-feature-flags.md`

**步骤**

- [x] `context_os_v1=true` 出厂默认 ON，符合测试阶段“不灰度”。
- [x] 保留单一 `context_os_v1=false` 兼容回退；它恢复旧 assembly/preflight，不允许新旧双跑。
- [x] 分项调试开关只用于测试，不形成长期组合爆炸。
- [x] 启动日志输出 active owner、resolved window、migration version，不输出正文。
- [x] 跑全套 OFF golden tests 后再翻默认 ON；翻转提交不得夹带实现改动。

**完成信号**：AC-CTX-13；默认测试为 ON，rollback contract test 证明互斥。

#### Task 5.2：自动化验证矩阵

**依赖**：Task 5.1  
**固定命令**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_context_fragments_compat.py `
  backend/tests/test_task_context_projector.py `
  backend/tests/test_context_snapshot_store.py `
  backend/tests/test_context_segment_store.py `
  backend/tests/test_session_history_planner.py `
  backend/tests/test_session_history_page_in.py `
  backend/tests/test_compression_model_resolver.py `
  backend/tests/test_context_request_planner.py `
  backend/tests/test_context_precompact_flush.py `
  backend/tests/test_tool_capability_resolver.py `
  backend/tests/test_prepared_tool_set_contract.py `
  backend/tests/test_tool_capability_hydration.py `
  backend/tests/test_tool_capability_execution_guard.py `
  backend/tests/test_deskpet_tools_registry.py `
  backend/tests/test_prompt_cache.py -q

backend\.venv\Scripts\python.exe -m pytest backend/tests -q
npm --prefix tauri-app test -- --run
npm --prefix tauri-app run typecheck
```

**步骤**

- [x] 先跑聚焦测试；每个失败回到最小 Task 修正，不批量掩盖。
- [x] 跑全量非 live backend 回归、前端 Vitest、TypeScript。
- [x] 单独报告与本变更无关的已知红线，不能把 focused green 冒充全绿。
- [x] 产出 AC→test→result 证据表。
- [x] 同一机器/解释器运行 `scripts/perf/context_os_bench.py --mode on --compare context-perf-baseline.json`；
      short-chat assembly P95 增量必须 ≤20ms，且 trace 证明没有新增辅助 LLM call。
- [x] 扩展 bench 构造 500 个 deferred ToolSpec：断言初始 payload 不含其 full schemas，schema bytes 相对
      legacy 全量直传下降至少 80%；无 deferred 命中时没有额外 provider 迭代，搜索/激活后的新增 bytes 与
      PreparedToolSet/report attribution 完全一致。

**完成信号**：所有聚焦测试与无既知红线的全量测试绿色。

#### Task 5.3：Windows Computer Use 真 E2E

**依赖**：Task 5.2  

> **当前阻断（2026-07-13）**：fixture、launcher 与自动化自检已经完成，但本机缺少可用 `cargo`，
> Tauri 无法进入真实 Windows Computer Use 测试栈。以下 case 保持未勾；不得用 fixture/脚本结果代替真 E2E。

**启动纪律**

- 只启动 Tauri，让它自己 spawn backend 与唯一 Vite。
- worktree 测试必须设置 `DESKPET_BACKEND_DIR` / `DESKPET_PYTHON`，日志确认 `Dev python=...`；
  看到 `Bundled exe=...` 视为无效测试。
- 使用真实 UI 点击/输入、截图和 Tauri/backend 日志；不得用 websocket 直注或脚本回放替代。

**case**

- [ ] 普通多轮承接：不触发工具时仍记得最近对话，L3 失败不丢 L2。
- [ ] 全量 raw 路径：在默认大窗口中通过 UI 建立可数的多轮 Session；ContextTrace 显示全部未删除
      messages 均为 raw coverage，逐条追问早期细节无需 memory/page-in tool。
- [ ] 8K/12K 测试窗口 + 20 个以上真实 tool calls：每次触发只有一个 compaction cycle/owner；允许该
      cycle 生成多个不重叠 segment summaries，tool pairs 完整且 source range 不重复提交。
- [ ] 大工具目录渐进披露：启动一个只在当前测试 session 授权的本地 MCP fixture，UI 发出需要其能力的任务；
      日志/ContextTrace 证明初始 provider payload 只有 direct+bridge schemas，随后真实发生
      `tool_search → tool_describe → tool_activate → mcp_<server>_<tool>`，激活后 schema hash 只变化一次，
      MCP handler/permission/receipt 均走真实运行栈。另开新 session 证明 capability scope 不泄漏；在 A session
      设置 goal、B session 不设置 goal，确认 B 的 payload/ContextTrace 都没有 `goal_task_*`。
- [ ] 工具 catalog 失效：在 describe 后、activate 前断开 MCP fixture；UI 收到可恢复 stale 结果，目标 handler
      未执行、没有伪成功 receipt，下一 request 重新 resolve 后可恢复。
- [ ] 已暴露工具失效：让 MCP 工具先成为 direct 或完成 activate，再在下一次 provider attempt 前断开/热替换
      fixture；ContextTrace 证明旧 schema 未出站，run 返回 `tool_catalog_stale`，旧/新 handler 均未被旁路调用。
- [ ] 超窗 coverage 路径：ContextTrace 显示 raw+summary+reference 覆盖消息总数且 gap=0/overlap=0；
      追问最早区间的精确细节时真实调用 `session_history_page_in` 并回答正确。
- [ ] 设置页选择专用压缩模型后触发 compact，UI/report/log 的 requested/resolved/actual model 一致；
      再构造不可用模型状态，验证不静默换成主模型且历史不丢。
- [ ] 显式 goal/workflow：compact 前后 objective/pending/decision/artifact identity 一致。
- [ ] 任务切换：新任务不漂回旧任务；切回时从 snapshot/事实源恢复。
- [ ] 应用重启：pending 未变 completed，artifact 可定位，下一步正确。
- [ ] ContextTrace：可见真实 loaded/trimmed reason、schema/reserve/cache fingerprint，无敏感正文。

**完成信号**：AC-CTX-14～19 全部有“动作前声明坐标 → 截图 → 真动作 → 截图 → 日志”证据；工具类
case 额外提供 scope revision、provider schema hash、真实 handler receipt 三方对账。

#### Task 5.4：文档和状态收尾

**依赖**：Task 5.3  

> **部分完成**：本轮仅同步 plan/STATUS 的真实状态；Task 5.3 与最终 DoD 因 `cargo` 缺失保持未完成。
**文件**

- 更新 `ARCHITECTURE/ARCHITECTURE.md`
- 更新 `STATUS/status.md`
- 在本目录新增 `results.md`、测试证据索引和决策记录

**步骤**

- [ ] 把实际落地调用链、owner、数据表与 rollback 写入架构文档。
- [ ] 只有自动化和真 E2E 均通过后，按 AGENTS.md 更新 STATUS 日期/模块/里程碑。
- [ ] 记录遗留问题、已知成本与后续 V2 候选；不把未实现项标完成。

**完成信号**：代码、测试证据、架构与 STATUS 四者一致。

## 6. AC 追踪矩阵

| AC | 主要 Task | 自动化证据 | 真机证据 |
|---|---|---|---|
| AC-CTX-1 | 0.2, 1.1 | fragment compatibility/lifecycle | ContextTrace 分层 |
| AC-CTX-2 | 1.1, 1.2 | cache fingerprint/adapter | 两轮 cache 观测 |
| AC-CTX-3 | 1.1 | L1/L2/L3 failure isolation | L3 故障仍连续 |
| AC-CTX-4 | 2.1 | projector precedence | goal/workflow 追问 |
| AC-CTX-5 | 2.2, 2.3 | migration/CAS/flush recovery | compact + restart |
| AC-CTX-6 | 3.2 | single owner/cycle + source-range-once | 日志单一 cycle/owner |
| AC-CTX-7 | 2.1, 3.1 | group/receipt state tests | 20+ tool calls |
| AC-CTX-8 | 3.1 | whole-request budget | 缩小 window |
| AC-CTX-9 | 4.1, 4.2 | page-in/path rules | code 与普通聊对照 |
| AC-CTX-10 | 2.3, 3.2, 4.1 | remount consistency | compact 后追问 |
| AC-CTX-11 | 4.3 | report/event/UI tests | ContextTrace 截图 |
| AC-CTX-12 | 0.1, 3.3 | scope/venue contract | text/code/web/research + voice 场景 |
| AC-CTX-13 | 5.1 | defaults/rollback mutual exclusion | 默认启动日志 |
| AC-CTX-14 | 5.2, 5.3 | regression | 全部真机 cases |
| AC-CTX-15 | 3.0, 3.2, 4.3, 5.3 | resolver/IPC/failure tests | 设置选择 + 实际 compact model |
| AC-CTX-16 | 2.4, 3.1, 3.2, 4.1, 4.3, 5.3 | tree/coverage/page-in tests | 全量 raw + 超窗无 gap 追问 |
| AC-CTX-17 | 1.3, 3.1, 3.3, 4.3 | resolver/prepared-set/provider-payload hash parity | ContextTrace schema fingerprint 对账 |
| AC-CTX-18 | 4.0, 5.2, 5.3 | search/describe/activate/direct-call scripted loop | 本地 MCP 同轮激活真调用 |
| AC-CTX-19 | 1.3, 2.2, 2.3, 4.0, 4.3, 5.3 | scope guard/stale/snapshot/receipt tests | 跨 session 与 MCP disconnect fail-closed |

## 7. 风险与护栏

| 风险 | 早期信号 | 护栏 / 回滚 |
|---|---|---|
| fragment 迁移改变消息字节 | golden test diff | legacy adapter 先字节等价，再逐 component 迁移 |
| provider 参数扩展破坏 fake/第三方实现 | `unexpected keyword` | optional request metadata/capability gate；更新 protocol doubles |
| snapshot 成为第二事实源 | projector 与 workflow 冲突 | precedence + source revision；snapshot 禁止反写 authority |
| DB migration 损坏用户库 | V17 fixture/transaction test fail | 启动前备份、事务 migration、失败不提升 user_version |
| 预算器低估 schema/附件 | provider context length error | canonical serialized estimate + margin + authoritative calibration |
| 缓存边界把动态内容纳入 | fingerprint 每轮漂移 | stable-only canonical bytes + fragment diff 诊断 |
| flush 卡住聊天 | DB timeout/lock | bounded timeout；保留 tail；事件可见；不得吞 current request |
| 新旧压缩同时运行 | call count > 1 | 单总开关互斥，测试 spy，启动日志声明 owner |
| 专用压缩模型名义与实际不一致 | report requested≠resolved/actual | resolver 单 owner；设置只列 catalog；失败不静默换模 |
| coverage tree 有 gap/overlap/stale | coverage verdict 非 valid | commit/outbound 双校验；保留 raw/旧 valid cover；必要时 BLOCK |
| 全 Session raw 重算拖慢每轮 | assembly P95/CPU 增长 | level-0 token/hash 增量索引；append/merge，不每轮重 tokenization |
| 渐进披露损害普通聊天连续性 | L3 正文不在 prompt | bounded auto摘要 + detail ref，而非全手动检索 |
| PreparedToolSet 与 Registry/Provider 漂移 | schema fingerprint 不一致 | 单次 catalog snapshot；provider 只消费 prepared schemas；attempt 前 hash assertion |
| tool_search 泄露或激活越权能力 | denied/env-hidden 出现在结果 | request-scoped scope + deny-first + describe nonce + Registry 执行前二次授权 |
| 激活 schema 使请求突然超窗 | activation 后 planner BLOCK | candidate revision 先重预算，fit 才原子提交，失败回旧 revision |
| MCP 热卸载复活旧能力 | descriptor hash/version stale | per-tool revalidate；scope/session 绑定；stale fail closed 并要求重新 resolve |
| dirty worktree 覆盖用户修改 | diff 出现计划外文件 | 每 Task 前后 scoped diff；提交只 stage 本 Task 文件 |

## 8. 明确拒绝的方案

1. **只重排六段 system prompt**：不能解决 tool schema 预算、双压缩、任务写回和缓存边界。
2. **再建一个 authoritative Task Ledger**：会与 GoalTask/Workflow/Receipt 形成多主写入。
3. **把 L3 改成完全手动 memory_get**：会损害桌宠普通对话的自然连续性。
4. **继续把 task snapshot 追加到全局 `MEMORY.md`**：生命周期错误且无限污染长期记忆。
5. **保留 preflight + reactive 两套摘要“互相兜底”**：无法证明哪份摘要是真相，且重复耗 token。
6. **永久双写/灰度**：当前测试阶段要求完成即默认 ON；只保留互斥 rollback。
7. **一次性重写 Assembler/AgentLoop/Memory V2**：回归面过大，也浪费已完成的 L2、compressor、Skill、tool governance。
8. **大窗口仍固定只取最近 2～10 条**：浪费 1M+ window，也不能声称 Agent 了解整个 Session。
9. **专用压缩模型失败后静默换主模型**：成本、质量和审计语义不可预测；失败必须显式。
10. **继续让 AgentLoop 按工具名重建 schema**：会保留双 owner 与 TOCTOU，PreparedToolSet 必须直接进入 provider。
11. **让 tool_search 遍历 `all_specs()` 或 bridge 直接代理目标 handler**：前者泄露 denied/env-hidden inventory，
    后者绕过真实工具名、预算、Permission/Receipt/Verify 因果链。
12. **把完整 schema/handler/凭据持久化进 session snapshot**：只保存 capability refs/hash/决策摘要；当前
    Registry 不匹配时重新 resolve。
13. **在 Context OS V1 同时实现通用第三方 Tool Hook SDK 或 Hermes `execute_code` RPC**：两者会新增插件信任、
    代码执行和中间结果隔离边界。本计划只冻结内部 typed lifecycle/capability seam，待 V1 真实验收后单独立项。

## 9. 实施顺序与并行边界

```text
0.1 ─────────────────────────────────────────────┐
0.2 ─ 0.3A ─ 0.3B ──────────────────────────────┤
       ├─ 1.1 ─ 1.2 ─ 3.0 ───────────┐          │
       └─ 1.3 ────────────────────────┼──────────┤
0.2 ───── 2.1 ─ 2.2 ─┬─ 2.3 ────────┼─ 3.2 ─ 3.3
                      └─ 2.4 ─ 3.1 ──┘
                 1.3 + 3.1 + 3.3 ───── 4.0
                 1.1 + 2.4 + 3.1 + 3.2 ─ 4.1 ─ 4.2
                 0.3B + 1.2 + 3.1 + 3.3 + 4.0 ─ 4.3
                                       │
                                       ▼
                              5.1 → 5.2 → 5.3 → 5.4
```

- 可并行：0.2 后 1.3 可与 0.3A/1.1、2.1 推进；2.3/2.4 在 2.2 后可并行；3.0 可与 2.x 并行。
- 不可提前：3.1 必须等 1.3+2.4+3.0；3.2 必须等 0.3B+2.3+3.1；4.0 必须等 1.3+3.1+3.3。
- 不可并行合并：2.3 与 3.2 都触碰 AgentLoop，必须先 rebase/集成后再改，避免双 owner 漂移。
- 不可并行合并：1.3/3.3/4.0 都触碰 ToolComponent/AgentLoop/tool dispatch；按 1.3 → 3.3 → 4.0 顺序
  集成，每次都先跑 PreparedToolSet/provider payload hash contract。
- 4.3 前端与 report 后端可并行，但 event schema 先冻结。
- 每个 worktree 只提交本 Task；合并前跑其聚焦测试，最终由主树跑全矩阵与真 E2E。

## 10. 用户 Review 决策（2026-07-13 已锁定）

1. V1 新增并使用持久化 `session_context_snapshots` 表。
2. 允许普通长任务创建 derived snapshot，但严格遵守 §4.3 的结构化证据条件，闲聊不创建。
3. 路径规则（AGENTS 等）纳入 Wave 4，仅在 code/workspace scope 启用。
4. 只保留一个 `context_os_v1` 总回滚开关，不形成子开关组合爆炸。
5. 真机验收先用 8K 强制触发 compact，再用默认窗口复测日常表现。
6. 压缩模型可在设置中选择，默认 `follow_session`；显式模型失败时不静默换模。
7. 单 Session 能放下时全量无损装载；超窗后使用无 gap/overlap coverage tree、最近 raw tail 和
   当前 Session 精确 page-in reference，固定 `l2_top_k` 仅保留给 OFF legacy path。

## 11. Review Gate

本计划的原 Context OS 设计 review gate 已通过；工具能力补充已通过 challenger gate，现提交用户 review。
**challenger PASS 与用户 review 都不等于授权执行**；进入实现前仍需用户明确说“按计划执行”。当前状态：

- acceptance 的 AC-CTX-1～16 已确认；AC-CTX-17～19 为本轮待 review 增量；
- 第 10 节七个决策已锁定；
- `plan-test` 原架构/三轮计划挑战与工具能力平面六轮补充挑战最终均 PASS；
- 所有 challenger 的 Blocker/Major 已关闭；Round 6 的 transport `sent` Minor 也已吸收；待用户确认工具补充，
  之后仍需单独执行授权。

## 12. 执行期增量：Voice Relay Only（2026-07-14，用户已锁定）

### 12.1 不可退让的产品合同

- Voice 默认且唯一正式路径使用中转站托管的语音模型；不得加载 Faster-Whisper、Silero VAD、
  CosyVoice 等本地语音模型，也不得在 relay 故障时静默退回本地模型或 Edge TTS。
- 必须支持自然连续聊天、服务端 turn detection、用户随时打断、旧音频 late-frame fencing、断线重连，
  并把 final transcript/assistant turn 幂等写入与文字入口相同的 SessionDB/Context OS。
- 语音模型的 function call 必须回到 DeskPet 的 `PreparedToolSet -> PermissionGate -> Registry -> Receipt`
  执行核；不得因采用原生 speech-to-speech 绕过工具权限、task scope、snapshot 或审计链。
- relay 不可用时显式展示可恢复错误与重试状态；“无声失败”和“偷偷换成本地模型”都算验收失败。

### 12.2 VR-0：Relay Voice Capability Contract（当前 HARD BLOCKED）

当前实时探测证据（2026-07-14）：

- 鉴权后的 `GET https://chinzy.com/v1/models` 为 200，共 146 个 alias，但 voice/audio/realtime/
  whisper/tts/transcription 过滤结果为空。
- `POST /v1/realtime/client_secrets`、`POST /v1/audio/speech`、
  `POST /v1/audio/transcriptions` 均返回 `404 NOT_FOUND`。
- `gpt-audio` 经现有 `/v1/chat/completions` 探测未形成可用音频响应；中转站公开文档只承诺
  OpenAI Chat Completions 与 Anthropic Messages，模型目录也未发布语音 alias。

因此，在中转站发布并实测以下契约前，禁止把 client/provider scaffold 冒充“语音已实现”：

- [ ] 明确 Realtime WebRTC（优先）或 WebSocket endpoint、正式 voice model alias 与现有 `tsk_*` 鉴权方式。
- [ ] WebRTC 路径提供 `/v1/realtime/calls` unified SDP proxy 或 `/v1/realtime/client_secrets` ephemeral token；
      标准 key 只留在 DeskPet backend，不下发给 WebView。
- [ ] 冻结 session/event schema：`session.created/update`、speech started/stopped、input transcript final、
      audio/text delta/done、function call、error、rate limit、cancel/truncate。
- [ ] 冻结 `semantic_vad`（推荐 `eagerness=auto`、`create_response=true`、
      `interrupt_response=true`）以及 response cancel / unplayed audio truncate 语义。
- [ ] 冻结 codec/sample rate、单 session 最大时长、并发/限流、计费、upstream failover、key 轮换与
      reconnect/resume 规则。

**VR-0 完成信号**：用真实 relay key 建立一次 Realtime session，完成两轮中文语音、一次说话中打断，
且拿到 transcript、response terminal 与 cancel/truncate 的可对账事件。否则本 slice 保持 BLOCKED。

### 12.3 VR-1：DeskPet Realtime Voice Coordinator（VR-0 通过后实施）

**后端**

- [ ] 新增 relay voice capability resolver；从当前 `relay-cloud` provider 读取 base URL/key/model，
      不复制密钥、不依赖启动时旧 env 快照。
- [ ] 新增只签发 ephemeral token/转发 SDP 的受保护 endpoint；绑定 session id、匿名 safety id、
      voice/model 与短 TTL，响应和日志永不含长期 key。
- [ ] 新增 `VoiceTurnCoordinator`：把 Realtime function calls 适配到当前 request-scoped
      `PreparedToolSet`，回填 `function_call_output`，并复用 ContextRequestPlanner/ContextTrace。
- [ ] transcript final 使用 `(session_id, realtime_session_id, item_id)` 幂等键写 SessionDB；partial
      只供 UI 展示；cancelled/unplayed 内容不得作为完整 assistant turn 持久化。
- [ ] 每轮使用 `generation_id/response_id/item_id/seq` 隔离；打断后丢弃旧 generation 的任何晚到音频或 delta。
- [ ] relay/auth/rate-limit/network 错误进入显式可重试状态；指数退避+jitter；只从已提交 final 边界恢复，
      不重放 partial audio，不制造重复 turn。

**WebView/Tauri**

- [ ] 以 `RTCPeerConnection` 替换现有 16k PCM `/ws/audio` 正式路径，启用浏览器 echo cancellation、
      noise suppression、auto gain；这些是采集/播放处理，不是本地推理模型。
- [ ] remote media stream 直接播放；data channel 承载 typed Realtime events；active session 切换必须重建
      voice binding，不能继续固定写入 `default`。
- [ ] speech started 时立即更新 UI 为 listening/interrupting；服务器自动取消后停止旧播放，并把
      truncate/cancel terminal 与当前 response 对账。
- [ ] UI 展示 connecting/listening/thinking/speaking/reconnecting/error；麦克风权限拒绝与 relay 不可用
      都有明确恢复入口。

**移除本地正式路径**

- [ ] 默认启动不 import/构造/preload `SileroVAD`、`FasterWhisperASR`、`CosyVoice2Provider` 或
      `EdgeTTSProvider`；permission narration 也走同一 relay voice session 或可访问 UI 提示。
- [ ] 旧 `/ws/audio` 仅在明确测试 fixture 中可用，产品配置无 local fallback 开关；打包清单移除对应权重，
      并用 cold-start 日志/测试证明没有本地语音模型加载。

### 12.4 VR-2：自动化与真人 E2E

- [ ] 合同测试：relay capability absent 必须 fail-visible；endpoint/model/schema drift 必须 fail closed。
- [ ] 单测：chat exactly-once、partial/final 幂等、function-call tool path、cancel once、late-frame fencing、
      active session binding、reconnect boundary、key rotation、60 分钟 session rollover。
- [ ] 前端测试：WebRTC lifecycle、permission denied、remote track、data-channel reconnect、状态 UI、停止播放。
- [ ] 静态/启动测试：relay-only 模式不得 import/load/download 本地 ASR/VAD/TTS 模型，不得请求 Edge TTS。
- [ ] 真机 E2E：至少 5 轮中文连续对话；assistant 说话中用户自然插话 3 次；每次旧音频在可感知范围内停止，
      新回答基于插话继续；再断网/恢复一次、重启一次、切换 session 一次，并核对 SessionDB/ContextTrace。
- [ ] 质量门：连续 20 轮无重复 turn、无跨 session 污染、无旧音频复活、无静默 fallback；失败可见且可恢复。

### 12.5 Challenger 结论

本增量已做一轮只读代码挑战。挑战确认当前生产路径是本地 Silero + Faster-Whisper + AgentLoop +
Edge/CosyVoice，relay registry 只有文本 provider 字段；并指出 active session 固定 `default`、无自动重连、
本地 cancel 未传播上游、partial/final 无稳定 id 等风险。以上均已吸收到 VR-0..VR-2。

由于用户明确要求“不再用本地模型”，没有采纳“保留本地 Silero VAD 作为低延迟 fallback”的建议；
turn detection 与 interruption 必须由 relay Realtime 服务端语义承担。
