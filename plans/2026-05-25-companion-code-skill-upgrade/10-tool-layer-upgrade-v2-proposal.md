# DeskPet 工具层升级方案 v2 — Multi-Agent Team + Slash Command UI

**日期**: 2026-05-25
**作者**: 调研主线程
**关联**: v1（commit `f6b932e`）已 ship；本文档 = v2 调研 + 设计，**未实施**
**调研源**:
- DeskPet 当前工具层架构（子代理 Explore 报告）
- Claude Code 泄露源码分析 (https://github.com/liuup/claude-code-analysis)
- WebSearch: Claude Code 2026-05 slash autocomplete 最佳实践

---

## 一、现状诊断 — DeskPet vs Claude Code 16 维差距

| 维度 | DeskPet 现状 | Claude Code 现状 | 差距 |
|------|------------|------------------|------|
| **Tool registry** | ToolRegistry 单例 + frozen dataclass | `assembleToolPool()` 动态组装 + 名称去重（内建优先于 MCP） | 🟡 中 |
| **Schema 校验** | 字典 `{name, description, parameters}` | Zod `safeParse` 硬校验 + 失败回传给模型 | 🟡 中 |
| **权限 Gate** | PermissionGate.check (allow/deny + UI popup) | PreToolUse Hooks (allow/deny/**ask**) + backfill 注入 | 🟢 小（基本对齐）|
| **dispatch 并发** | asyncio.gather 全并发 | `partitionToolCalls()` 按 `isConcurrencySafe` 分批 — 读并行/写串行 | 🔴 **大**（race 风险）|
| **执行器状态机** | execute_tool 单 try/except | `StreamingToolExecutor`: queued→executing→completed→yielded，中途异常可中断兄弟 | 🔴 大 |
| **错误 envelope** | `{ok, result, error}` 自由 | `tool_use_error` 标准化 + `normalizeMessagesForAPI()` | 🟢 小 |
| **Subagent** | `agent_tool`（串行 15 iter cap） | AgentTool 统一入口 + `subagent_type` 分流 + `run_in_background` | 🟡 中 |
| **Multi-agent 并发** | `agent_parallel` 2-4 hub-and-spoke | **Teammate Swarm**：共享 task list + claim/update + mailbox | 🔴 **大**（无 team 模型）|
| **Subagent 上下文继承** | Mock prompt 注入 | 3 模式：普通隔离 / Fork 复用父 prompt 字节（cache 命中）/ Teammate 强制协作工具 | 🔴 大 |
| **Recursion guard** | SubsetRegistryAdapter 剔除 agent/agent_parallel | "Teammate 不能再生 teammate" + in-process teammate 禁 background | 🟢 小 |
| **任务依赖** | 无 — agent_parallel 一次性 fire-and-forget | TaskCreate / TaskUpdate / TaskList 工具暴露给 LLM 自管 | 🔴 **大** |
| **通信** | 子代理结果聚合返回 | **Mailbox 文件通信 + transcript resume**（双轨容错） | 🔴 大 |
| **/ 命令解析** | 前端正则 + 后端 dispatch_slash_command（v1 已 ship） | Terminal: typing `/` → autocomplete dropdown + filterable | 🟡 中 |
| **/ 自动补全** | ❌ 无 | Tab/Arrow 接受 + argument-hint + progressive arg suggestions | 🔴 **大** |
| **/ 输入历史** | ❌ 无 | 上下键浏览历史 | 🔴 大 |
| **Slash Desktop UX** | InputBar 朴素 textarea | Desktop app **也没有 autocomplete**（Claude 自己的 issue） | ⚖️ **deskpet 可超越** |

**结论**：DeskPet 在 **基础 dispatch + 单 agent + slash 解析** 已对齐 Claude Code；在 **multi-agent team + slash UI** 两大块有显著差距。这两块就是 v2 的核心目标。

---

## 二、调研结论 — 5 条核心原则

### 原则 1：**Multi-agent 用 "共享 task list + claim" 模型，不是 fire-and-forget**

Claude Code 的 swarm 模式：teammate 不是被 coordinator 显式指派，而是从**共享任务池**自主 `claimTask()` → `updateTask()` → `markDone()`。Coordinator 只看 task list 状态，不直接控制 teammate。

**优势**：动态负载均衡 + 失败任务可重新 claim + 不需要 coordinator 写"派单"逻辑。

### 原则 2：**Subagent 上下文继承用 3 模式分流，不是单一 mock 注入**

- **普通 subagent**：隔离 context，但**保留父工具池引用**（避免重新构建）
- **Fork child**：**直接复用父已渲染的 prompt 字节** — 保证 prompt cache 命中（关键性能优化）
- **Teammate**：强制注入协作工具（SendMessage/TaskCreate/TaskList）

DeskPet 当前 `agent_parallel` 用 mock prompt 注入 — **缺 prompt cache 优化**。

### 原则 3：**并发 dispatch 必须按 `isConcurrencySafe` 分批**

读操作（read_file / grep / glob）→ 并行  
写操作（write_file / bash / edit）→ 串行

DeskPet 现状全 gather → write_file 并发可能 race 同一文件。**需加 partition flag**。

### 原则 4：**错误隔离 3 层**

1. **Context 隔离**：`AsyncLocalStorage`（in-process teammate）
2. **Permission 隔离**：teammate 权限请求**回流到 leader 的权限队列**或 mailbox 等待批准
3. **Communication 隔离**：mailbox + transcript resume **双轨**，任一失效可切换

DeskPet 现状仅 try/except 隔离 — **缺 permission 回流 + 通信容错**。

### 原则 5：**Slash UI 关键 UX：filterable dropdown + Tab 接受 + argument-hint**

Terminal 模型：
- 输入 `/` → 弹 dropdown 显示所有 skill
- 继续输入 `/ppt` → filter 到匹配项
- ↑/↓ 选择 → Tab 接受 → 空格触发参数提示（argument-hint）
- Enter 在空输入不应 auto-submit（Claude 2026-05 修复 bug）

DeskPet 现状：无 dropdown / 无 filter / 无 Tab — **用户体验差距最大**。

---

## 三、升级目标 (v2)

### G1 — Multi-agent Team 工作流（核心）

升级现 `agent_parallel` 为 **Team 模式**：
- 共享 task list（SQLite + 内存索引）
- Teammate 工具集（task_create / task_claim / task_update / task_list / send_message）
- Mailbox 通信（落盘 JSON 文件）
- Coordinator agent 只看 task list 不直接派单
- 错误隔离 3 层

### G2 — Slash Command UI（用户体验最大提升）

InputBar 升级：
- 输入 `/` → 显示 filterable dropdown（fetch `/api/skills/list` 缓存）
- ↑/↓ 选择
- Tab / Enter 接受
- argument-hint inline 提示（如 `/goal <text>` 显示 `<text>` placeholder）
- 上键浏览输入历史
- ESC 关闭 dropdown

### G3 — Tool Dispatch Partition（次优先，防 race）

`ToolSpec` 加 `concurrency_safe: bool = True`（写工具显式设 False）。`execute_tool` 改 `partitionToolCalls()` 策略。

### G4 — Subagent Prompt Cache 命中优化（性能）

`agent_parallel` 的 subagent 改 Fork 模式 — 复用父 prompt 字节而非重建。预计降 30-50% LLM cost。

---

## 四、详细设计

### A. Multi-Agent Team 工作流

#### A.1 数据模型

`backend/deskpet/agent/team/`（新模块）：

```python
@dataclass
class TeamTask:
    task_id: str          # uuid
    team_id: str          # team 标识
    description: str      # 任务描述（LLM 写）
    status: str           # "pending" | "claimed" | "in_progress" | "done" | "failed"
    claimed_by: Optional[str] = None  # teammate_id
    result: Optional[str] = None
    created_at: float
    claimed_at: Optional[float] = None
    done_at: Optional[float] = None

@dataclass
class TeamMailbox:
    team_id: str
    messages: list[dict]  # [{from, to, content, ts}]
    permission_queue: list[dict]  # waiting leader approval

class TeamStore:
    """SQLite + 内存索引；落盘到 user_data/teams/<team_id>.db"""
    async def create_task(self, team_id, description) -> str: ...
    async def claim_task(self, team_id, teammate_id) -> Optional[TeamTask]: ...
    async def update_task(self, task_id, status, result=None): ...
    async def list_tasks(self, team_id, status=None) -> list[TeamTask]: ...
    async def send_message(self, team_id, from_id, to_id, content): ...
    async def request_permission(self, team_id, teammate_id, action) -> Optional[bool]: ...
```

#### A.2 Teammate 工具集（暴露给 teammate LLM）

```
task_create(description) → task_id
task_claim() → 拿一个 pending 任务（失败返 None）
task_update(task_id, status, result) → ok
task_list(status="all") → [TeamTask]
send_message(to_id, content) → ok
request_permission(action) → 阻塞直到 leader 批准/拒绝
```

#### A.3 Team 启动流（替换现 `agent_parallel`）

`spawn_team(team_id, task_descriptions: list[str], num_teammates: int = 3)`：
1. TeamStore.create 多个 TeamTask
2. spawn N 个 teammate subagent（每个有 task_* + send_message 工具）
3. coordinator 主代理 poll team_status WS event
4. 所有 task done 或超时 → 收集 results 返回主代理

#### A.4 Recursion Guard + 错误隔离

- Teammate 工具集**不含** `agent_parallel` / `spawn_team`（防递归）
- Teammate try/except 单独捕获 → 失败 task 标 `status=failed` + log
- Permission 请求走 `request_permission` 工具 → 主代理 PermissionGate.check → result 写 mailbox
- Communication：mailbox 落盘（每次 send 即 fsync）+ task 状态走 SQLite WAL（崩溃可 resume）

#### A.5 实施代码量估算

- TeamStore + 5 工具 + spawn_team 函数：~800 行
- 测试：30+ 个
- 实施时间：6-8h（单人）

---

### B. Slash Command UI Autocomplete

#### B.1 状态机（前端 InputBar.tsx 改造）

```typescript
type SlashState =
  | { mode: "idle" }
  | { mode: "dropdown_open", filter: string, candidates: SkillSpec[], selected_idx: number }
  | { mode: "arg_hint", cmd: string, args_so_far: string[], arg_schema: ArgSchema }
```

**事件**：

| 输入 | 状态转移 |
|------|---------|
| 输入框为空 + 按 `/` | idle → dropdown_open(filter="", candidates=all_skills) |
| dropdown_open + 继续打字 | filter += char, candidates = filter_match(all_skills, filter) |
| dropdown_open + ↑↓ | selected_idx ± 1（环绕） |
| dropdown_open + Tab/Enter | 接受 selected → text = "/<name> " + 转 arg_hint |
| dropdown_open + ESC | → idle |
| dropdown_open + 空格 | 关 dropdown，转 arg_hint |
| arg_hint + Enter | 发 slash_command WS |
| 空输入 + ↑ | text = last_history_entry（历史浏览） |

#### B.2 后端 REST 加强

`/api/skills/list` **已有**（v1）。新加：

```
GET /api/commands/help → list of {name, description, args_schema}
GET /api/commands/<name>/schema → ArgSchema（参数列表 + 类型 + description）
```

**ArgSchema 来源**：SKILL.md 的 frontmatter `args` 字段；builtin 命令 hardcode（如 `/goal` 的 `<text>` 参数）。

#### B.3 argument-hint UI

输入 `/goal ` 后显示 placeholder：

```
> /goal <text — set a session-level goal>
        ^^^^^^ 灰色 placeholder 提示
```

继续输入 `/goal write a haiku`，placeholder 消失。Tab 触发"清空 placeholder + 进入纯输入"模式。

#### B.4 输入历史

`useSessionsStore` 加 `input_history: string[]`（max 50 entries）。每次 send 后 push。↑ 浏览。**只对包含 `/` 的输入存历史**（避免历史被普通聊天填满）。

#### B.5 实施代码量估算

- InputBar.tsx 重构：~300 行（状态机 + dropdown 组件 + arg_hint）
- 新建 SlashDropdown.tsx + ArgHintBar.tsx：~200 行
- 后端 2 个 REST endpoint：~80 行
- 测试 (vitest)：15+
- 实施时间：4-6h

---

### C. Tool Dispatch Partition

#### C.1 ToolSpec 加字段

```python
@dataclass(frozen=True)
class ToolSpec:
    # 现有字段...
    concurrency_safe: bool = True  # 默认安全；写工具显式设 False
```

#### C.2 partitionToolCalls 策略

```python
async def _partition_dispatch(self, calls: list[ToolCall]) -> list[Result]:
    safe = [c for c in calls if self._tools[c.name].concurrency_safe]
    unsafe = [c for c in calls if not self._tools[c.name].concurrency_safe]
    # 并发跑所有 safe
    safe_results = await asyncio.gather(*[self.execute_tool(c) for c in safe])
    # 串行跑所有 unsafe
    unsafe_results = []
    for c in unsafe:
        unsafe_results.append(await self.execute_tool(c))
    return _merge_by_index(safe_results, unsafe_results, calls)
```

#### C.3 标 unsafe 的工具

`write_file` / `bash_run` / `excel_create` / `ppt_create` / `docx_create` / `memory_write` — 全标 `concurrency_safe=False`。

实施时间：2h（含改 10 个工具注册 + 测试）

---

### D. Subagent Prompt Cache 命中（性能优化）

修改 `agent_parallel_tool.py:_build_sprint_contract`：
- 不重新构造 system prompt
- 复用父 agent 的 system message bytes（直接 reference）
- 子代理 prompt 只追加 Sprint Contract section（user msg 部分）

预期效果：LLM 调用 prompt_cache_hit 率 0% → 80%+。

实施时间：3h

---

## 五、工作项 (WI) + 排期

| WI | 内容 | 预估 | 依赖 |
|----|------|------|------|
| **WI-T2-A1** | TeamStore (SQLite + 5 工具) | 2.5h | 无 |
| **WI-T2-A2** | spawn_team + teammate runtime | 2.5h | A1 |
| **WI-T2-A3** | Mailbox + Permission queue | 1.5h | A1 |
| **WI-T2-A4** | Recursion guard + 错误隔离测试 | 1.5h | A2 |
| **WI-T2-B1** | InputBar 状态机重构 | 2h | 无 |
| **WI-T2-B2** | SlashDropdown + filter + arrow keys | 1.5h | B1 |
| **WI-T2-B3** | ArgHintBar + arg_schema 渲染 | 1h | B2 |
| **WI-T2-B4** | 输入历史 ↑ 浏览 | 0.5h | B1 |
| **WI-T2-B5** | 后端 REST `/api/commands/*/schema` | 1h | 无 |
| **WI-T2-C1** | ToolSpec.concurrency_safe + partition | 1.5h | 无 |
| **WI-T2-C2** | 标记 10 个写工具 unsafe | 0.5h | C1 |
| **WI-T2-D1** | Subagent prompt cache 复用 | 3h | A2（合作）|
| **WI-T2-E1** | 全套测试 (≥ 60 新增) | 3h | 全部 |
| **WI-T2-E2** | 人工测试 testcase + boot smoke | 2h | E1 |

**总计 ~25h** = 3-4 天单人 / 1.5-2 天多 agent 并行

**并行切分**：
- **Track 1**（multi-agent team）：A1+A2+A3+A4 → 派子代理 A
- **Track 2**（slash UI）：B1~B5 → 主线程亲做（前端 UI 不派子代理）
- **Track 3**（partition + cache）：C1+C2+D1 → 派子代理 B

---

## 六、风险 + Deferred

| 风险 | 缓解 |
|------|------|
| Team 模式 SQLite WAL 锁竞争 | per-team 独立 db 文件 + busy_timeout=5000 |
| Mailbox 文件 fsync 性能 | 限 send_message 10 msg/s 节流 |
| 前端 dropdown 在小屏 / IME 输入法冲突 | 复用 CodePanel splitToolError 测试经验 + IME isComposing 检测 |
| Prompt cache 复用破坏 subagent 上下文隔离 | 仅复用 system prompt bytes，user prompt 部分仍隔离 |
| concurrency_safe 标错（误标安全的写工具）| 写工具默认 False（白名单标 True 更安全），代码 review + 单测覆盖 |

**Deferred to v3**：
- Background teammate（in-process → out-of-process 子进程）
- Team 持久化跨 backend 重启（v2 是 per-session）
- Slash dropdown 全 SKILL.md frontmatter args_schema 真解析（v2 hardcode + 简化）
- Tool 输出 streaming（Claude Code 的 StreamingToolExecutor 全套）

---

## 七、为什么这样设计（关键决策依据）

### 7.1 为什么选 Team 模式而不是继续做 hub-and-spoke

Claude Code 5 月新模型 + 调研结论：**hub-and-spoke 的 coordinator 是 bottleneck**。
3 个子代理回结果给 coordinator → coordinator 再做综合决策 → 单点串行。
Team 模式让 teammate 自主 claim/update → 真正并发 + coordinator 只看状态。

### 7.2 为什么 slash UI 不复用现有 chat history

输入历史 ≠ 聊天历史。`/help` 这种命令应该独立堆栈 — 用户按 ↑ 找上次的 `/goal`，不应混入"你好"这种聊天文本。**独立 `input_history` array 上限 50**。

### 7.3 为什么 partition 默认 safe=True（不是 False）

历史兼容：现有 30+ 工具未声明 → 全标 False 会让单 agent loop 跑变串行。
**白名单显式标 False（10 个写工具）+ 默认 True** 是更小破坏路径。

### 7.4 为什么不做 git worktree（Claude Code 有）

DeskPet 是**消费级桌宠**，用户不一定在 git repo 里跑命令。worktree 假设有 git 上下文 → 不适合。
v2 用 SQLite team_id 隔离 = 等价能力 + 无 git 依赖。

---

## 八、关键 reference

- Claude Code multi-agent: https://github.com/liuup/claude-code-analysis/blob/main/analysis/04h-multi-agent.md
- Claude Code tool call: https://github.com/liuup/claude-code-analysis/blob/main/analysis/04b-tool-call-implementation.md
- Slash autocomplete bug + fix: https://github.com/anthropics/claude-code/issues/40413
- Bash completion script (社区参考): https://github.com/cldotdev/claude-bash-completion
- DeskPet v1 实施: `plans/2026-05-25-companion-code-skill-upgrade/00-PRD.md`

---

## 九、给用户的 TL;DR

**问题**：当前 DeskPet 工具层在两个维度跟 Claude Code 差距最大：
1. **多 agent 没 team 模型** — 只能 fire-and-forget 2-4 个并发，没共享 task list / claim / mailbox
2. **`/` 命令没 UI 反馈** — 输入 `/` 不弹候选；没 Tab 补全；没上下键历史

**方案**：v2 升级 ~25h 工作量，分 3 track 并行做：
1. **Team 模式**（核心）— TeamStore + 5 teammate 工具 + mailbox + permission queue
2. **Slash UI** — InputBar 状态机 + Dropdown + Tab/Arrow + argument-hint
3. **Partition + prompt cache** — concurrency_safe flag + Fork mode

**预期收益**：
- 用户能用 `/<任意 skill>` 一键触发 + Tab 自动补全 + ↑ 翻历史
- 复杂任务（"分析 5 篇论文"）真正并发，coordinator 不卡瓶颈
- LLM cost 降 30-50%（prompt cache 命中）

**等你确认是否启动 v2 实施**（不实施纯调研方案 = 本文档已交付）。
