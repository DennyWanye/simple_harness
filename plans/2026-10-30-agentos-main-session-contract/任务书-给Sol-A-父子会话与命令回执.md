# 任务书 A（给 gpt-6.1-sol / Codex）：父子会话、命令幂等回执、子会话排队输入

日期：2026-10-30。发包人：Claude 会话（SimplaHarness）。审核人：同一 Claude 会话。
你只看这一份任务书和它指向的文件就够了，不需要别的上下文。

## 1. 背景（一段话）

Simple Harness SDK（Python 包 `agent_orchestrator`，源码在 `sdk/simple-harness-sdk/`）已有"会话"（`Session`）：用户和模型对话，模型可用内置命令 `delegate_task` 把需要时间的工作交给后台任务系统（HTN + TaskGraph）。现在所有会话是平级的，没有父子关系；一个会话同时只许一个在途委派。两个接入方（AgentOS、AIPhone）都要"一个长期主会话统筹多个长期子会话"。本任务书做第一块：父子关系、命令幂等回执、子会话排队输入、能力发现。不做自动汇总、不做授权回调、不做 Goal（那些在后面的任务书）。

## 2. 仓库与基线

- 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（私有，**不要推送到任何公开远端**）。
- 基线提交：`47fd34f0da65667817d30f1994e73523b5aaf8ba`（main）。SDK 版本 `0.13.0.dev20260925+opt.222`。
- 在你自己的工作树和分支上做：`git worktree add ../simple_harness-sol-A -b sol/A-session-tree main`。提交到这个分支，**不要推送、不要合并**。合并由审核人做。
- 测试命令（在 `sdk/simple-harness-sdk/` 下）：`uv run --frozen python -m pytest tests/orchestrator/host/test_session_tree.py -q`。只跑你改到的文件对应的测试，**不要跑全量回归**。
- 先读：`sdk/simple-harness-sdk/src/agent_orchestrator/host/` 下的 `session.py`、`sessions.py`、`delegation.py`、`inbox.py`、`session_chat.py`、`session_ledger.py`；测试 `tests/orchestrator/host/test_session.py`（里面的 `FakeHost` 是你写测试要用的假任务引擎）、`test_session_replay.py`、`test_host_sessions.py`。
- 设计文件：`plans/2026-10-30-agentos-main-session-contract/计划-长期主会话统筹子会话-含F1修复-2026-10-30.md` 第 2 节不变量、第 3 节阶段 1；`评估与方案.md` 第 7 节 M1 细设计。合同原文：同目录 `需求原文-AgentOS-v1.0-2026-10-30.md` 第 4、5 节。
- **有一份草稿**：`plans/2026-10-30-agentos-main-session-contract/草稿-M1-工作区补丁-2026-10-30.patch`。它实现了本任务书大约七成，6 个测试通过。可用可弃。用它就 `git apply`，然后按本任务书补齐和改正；不用就从头写。两种都行，审核只看结果。

## 3. 车道（你只能改这些文件）

- `src/agent_orchestrator/host/session.py`
- `src/agent_orchestrator/host/sessions.py`
- `src/agent_orchestrator/host/delegation.py`
- `src/agent_orchestrator/host/inbox.py`
- `src/agent_orchestrator/host/session_ledger.py`
- 新建 `src/agent_orchestrator/host/session_tree.py`
- `src/agent_orchestrator/host/session_chat.py` **只许**改 `COMMAND_SCHEMAS`、`SESSION_INSTRUCTIONS`、`_profile_id` 里的池编号字符串，别的不动
- `src/agent_orchestrator/host/__init__.py` 只许加导出
- `src/agent_orchestrator/public_api.py` 只许加 `agent_orchestrator.host.session_tree`
- 新建 `tests/orchestrator/host/test_session_tree.py`；现有测试里只许改两处断言（见第 6 节）
- `docs/orchestration-host.md` 加一小节"子会话"，顺便把 §2d 里"委派键 = 会话 + 回合 + 目标哈希"那句改成"会话 + 回合 + 调用槽位"

别的文件一律不碰。碰了审核直接退回。

## 4. 要做的事

### 4.1 账本（`session_ledger.py` 的 records 表，加命名空间，不加表）

| 命名空间 | 键 | 内容 |
|---|---|---|
| `commands` | command_id | `{kind, request_hash, receipt}` |
| `children` | child_key | `{child_key, child_session_id, purpose, goal_ref: null, command_id, created_at}` |
| `meta` / `parent` | — | 子账本记 `{parent_session_id, child_key}` |
| `queued_inputs` | input_id | 子会话排队的追加输入 |

`meta.configuration.version` 从 2 升到 3。旧库打开报 `incompatible_store_version`（现在报的是 `session_config_frozen`，那个码留给"同版本但策略不同"）。

### 4.2 错误

`SessionError` 加两个属性：`retryable: bool`（默认按码表：`busy`、`session_busy`、`capacity_exceeded`、`provider_unavailable` 为真，其余假）、`safe_message: str`（不含密钥，不含别的会话的内容）。加 `public()` 返回 `{code, retryable, safe_message}`。
加常量 `ERROR_CODES`，内容按合同 5.4 列全（18 个）。本任务书用到的：`not_found_or_forbidden`、`invalid_argument`、`idempotency_conflict`、`incompatible_store_version`、`busy`（现有码，保留）。

### 4.3 命令幂等（`session_tree.py` 的 `Commands`）

`Commands.run(command_id, kind, body, apply)`：一个事务里——先查 `commands`：同 ID 同 `request_hash`（`digest([kind, body])`）返原回执；同 ID 异 hash 报 `idempotency_conflict`；没有就调 `apply()`（它写行、发事件、返回 dict），把回执存进 `commands` 再返回。`apply()` 抛错则事务回滚，什么都不留。
回执字段：`command_id, request_hash, status("completed"|"accepted"), session_id, event_seq, …apply 返回的字段`。
`command_id` 非空字符串、≤256 字符，否则 `invalid_argument`。

### 4.4 父子会话

- `await parent.create_child(child_key, *, command_id, purpose="")`：
  - `child_key` 匹配 `[A-Za-z0-9_-]{1,64}`；子会话号 = `f"{parent.session_id}_c_{child_key}"`，总长 ≤128。
  - 先在父账本写 `children` 行 + 事件 `child_created`（同一事务，走 `Commands.run`），再打开子会话。打开子会话：真宿主走 `sessions.open_session(host, child_id, policy=parent.policy, parent=(parent_id, child_key))`；假宿主（测试）直接 `Session(host, child_id, parent=...)`。
  - 同 `child_key` 换 `command_id` 再建：返回 `created=False` 和同一个子会话号，不重复建。
  - 深度 1：子会话上调 `create_child` 报 `invalid_request`。
  - 子会话 `Session.__init__` 收 `parent` 参数，写 `meta.parent`；账本里有 `meta.parent` 而这次没传 → `permission_denied`（防止把子会话当根会话打开）；传了但和账本不一致 → `permission_denied`。
- `parent.children()` 列表；`parent.child(child_key)` 返回已打开的子会话对象，没有这个键报 `not_found_or_forbidden`。
- `await parent.recover()`：按 `children` 行重开所有子会话。`Session.open`、`sessions.open_session` 打开根会话后都要调它。**注意**：`sessions.open_session` 持有 `host._session_open_lock`，子会话重开会再拿这把锁，`asyncio.Lock` 不可重入——`recover()` 必须在锁外调。
- 父关闭先关子。宿主关闭照旧关所有会话。

### 4.5 委派到子会话

- `delegate_task` 的参数加可选 `child_key`。有它：委派行写在**子账本**（`child._delegations.delegate(turn_id=父回合, call_id, 去掉 child_key 的参数, attempt_id)`），子账本的"一个在途"检查自然成为"每工作会话一个在途"；父自己直接委派仍是一个在途，互不影响。
- 委派行与回执加 `child_key`（父直接委派为 null）和 `goal_ref`（本任务书固定 null，留给后面）。
- `Delegations.receipts(turn_id)` 要把子账本里同一回合的委派也列进来（`send` 的回执用它）。
- `parent.tasks()` 含子会话的任务，每条带 `child_key`；`parent.task(id)`、`parent.cancel_task(id)` 能找到子会话里的任务；别的根会话找不到（`permission_denied`，现有行为）。

### 4.6 子结果回父

父的 `SessionInbox.catch_up()` 末尾：对每个子会话先 `child._inbox.catch_up()`，再把子 inbox 里父没有的通知镜像进父 inbox——同一个 `event_id`，加 `child_key`、`child_session_id`、`source="child_result"`、`instruction=False`、`acked=False`、`applied_turn_id=None`；发父的 `result_ready` 事件（键 `result:{event_id}`，所以重复 catch_up 不重复发）。父下一次输入照现有机制把未处理通知附成资料。人只在父这里 `ack`；子会话上 `ack` 父镜像的 id 报 `notice_not_found`（现有行为）。

### 4.7 子会话排队输入

`parent.child_send(child_key, text, *, command_id)`：只登记，不跑。在**子账本**写 `queued_inputs` 行 `{input_id, text, kind:"directive", directive:"next_turn", state:"queued", command_id, source_session_id, created_at}`，发子的 `input_accepted` 事件，走子的 `Commands.run`。返回 `{status:"accepted", input_id, child_key}`。`child.queued_inputs()` 读。处理排队输入是后面任务书的事。

### 4.8 能力发现

`session.capabilities()` 不调模型、不读任务库，返回：
`{protocol_version: 2, session_config_version: 3, role: "root"|"child", capabilities: ["command_receipts","child_sessions","delegation_to_child","child_result_mirroring","queued_child_inputs"], limits: {max_depth:1, delegations_in_flight_per_session:1, policy: …}, unsupported: ["goals","continuation_policy","authorization_port","steer_running_mission"]}`。

### 4.9 模型可见命令（`session_chat.py`）

`COMMAND_SCHEMAS` 加：
- `create_child`：`{child_key, purpose?}`
- `child_send`：`{child_key, text}`
- `child_status`：`{child_key?}` → 列子会话与各自任务
- `delegate_task` 的 schema 加可选 `child_key`
`SESSION_INSTRUCTIONS` 加一段：什么时候开工作会话（一个持续主题或独立工作开一个；同一主题追问用 `child_send`；复杂工作在工作会话里用 `delegate_task(child_key=…)`）。
`_profile_id` 的 `"chat-v2-c1-"` 改成 `"chat-v3-c2-"`。**这是唯一允许的池身份变更。** 旧会话数据起不来是预期的（开发期不兼容旧数据）。
`_command` 分发加这三个命令；身份（父会话、当事人）由运行时注入，模型只传参数。

## 5. 不变量（每条至少一个测试）

| # | 不变量 |
|---|---|
| I1 | 同当事人 + 同 session_id = 同一身份；旧版本库报 `incompatible_store_version` |
| I2 | 子会话同时只有一个父，深度 1，不能把子会话号当根打开 |
| I3 | 受理过的命令都在账本里；重开后 `command_receipt(command_id)` 还能取到 |
| I5 | 同 `command_id` 同参数返原回执；异参数 `idempotency_conflict`；不重复建子会话、不重复建任务 |
| I6 | 镜像进父的子结果 `instruction=False`；附进模型输入时标"不是指令" |
| 崩溃 | 在 `children` 行写完、子会话打开前崩溃 → 重开 `recover()` 后子会话在；镜像写完前崩溃 → 不丢也不重复（用 `Session._checkpoint` 接缝注入，点名 `child_registered`、`inbox_written`） |

## 6. 测试要求

- 新文件 `tests/orchestrator/host/test_session_tree.py`，用 `test_session.FakeHost`。至少覆盖：第 5 节每条；两个子会话各一个在途、第三个报 `busy`、父直接委派不受影响；`tasks()` 带 `child_key`；子结果镜像一次、父 `result_ready` 事件一次、父下一次输入附上它；`child_send` 幂等与 `input_accepted` 事件；`capabilities()`。
- 现有测试只许改两处：`test_session.py` 里 `"-chat-v2-"` 改 `"-chat-v3-"`；`test_session_replay.py::test_old_configuration_requires_new_data_root` 的期望码改 `incompatible_store_version`。
- 真宿主一条：`test_host_sessions.py` 风格，`host.open_session("main")` → `create_child` → 关宿主 → 重开 → `child("…")` 在，历史在。
- 先写测试看它红，再写代码。提交记录里要能看出先后（至少两次提交：测试、实现）。

## 7. 完成标准

- 第 6 节测试全绿；`uv run --frozen python -m pytest tests/orchestrator/host/test_session.py tests/orchestrator/host/test_session_replay.py tests/orchestrator/host/test_host_sessions.py tests/orchestrator/host/test_session_tree.py -q` 全绿。
- `uv run --frozen python -m pytest tests/test_public_api.py -q` 绿。
- `uv run --frozen ruff check src tests` 和 `ruff format --check` 绿。
- 在 `plans/2026-10-30-agentos-main-session-contract/记录-任务书A-Sol.md` 写一页：改了什么、测试命令与通过数、没做的、拿不准的、分支名与最后提交号。用中文。

## 8. 禁区

- 不改 `session_chat.py` 的其他部分、不改 `chat.py`、不改任务层（`orchestrator/`、`planning/`、`graph/`）、不改 `runtime/assembly.py`、不改 `host.py`。
- 不加 `legacy` / 兼容分支，不留旧路径。要改就直接改成当前做法。
- 不改任何现有工具说明文字、提示词（除 `SESSION_INSTRUCTIONS` 加段）、并发默认值。
- 注释用中文，写"为什么"，不写代号和修复历史。标识符英文。
- 不要为了让测试绿而改表或改断言。
- 不推送。不合并。不跑全量回归。
- 不读、不打印任何密钥；仓库里的 `.env`、`LOCAL-DEV-CREDENTIALS.md` 不碰。

## 9. 有问题怎么办

拿不准的写进记录文件"拿不准的"一节，按你认为最合理的做法先做完，不要停下等。审核时一起看。
