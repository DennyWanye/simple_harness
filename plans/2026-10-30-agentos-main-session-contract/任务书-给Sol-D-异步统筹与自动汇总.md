# 任务书 D（给 gpt-6.1-sol / Codex）：异步受理、子会话排队输入处理、结果驱动的自动汇总

日期：2026-10-31。发包人：Claude 会话（SimplaHarness）。审核人：同一 Claude 会话。
你只看这一份任务书和它指向的文件就够了。**在任务书 C 合并之后开工**，基线以发包时的 main 为准。

## 1. 背景（一段话）

Simple Harness SDK 的会话层（`sdk/simple-harness-sdk/src/agent_orchestrator/host/session*.py`）已经有：父子会话（深度 1）、委派到子会话（子账本"一个在途"）、子结果镜像进父收件箱、子会话排队输入只登记不处理、命令幂等回执。两个接入方（AgentOS、AIPhone）接下来要的三件事是本任务书：①主会话的 `send` 能"持久受理即返回"，回合在后台跑；②子会话排队的追加输入在当前任务终态后自动成为子会话下一回合；③**子结果到达后主会话自动起一个回合汇总**（默认开，只汇总不新建工作，受预算、合并窗口和宿主策略限制），有语音绑定时走播报接缝而不并行起第二个文字回合。不做 Goal、不做宿主授权回调、不做取消范围、不做 owner 代次（任务书 E）。

## 2. 仓库与基线

- 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（私有，不推公开远端）。基线：发包时的 `main`。
- 工作树：`git worktree add ../simple_harness-sol-D -b sol/D-continuation main`。提交到分支，不推送、不合并。
- 测试：`sdk/simple-harness-sdk/` 下 `uv run --frozen python -m pytest <文件> -q -p no:cacheprovider`；改源码后真宿主测试前先 `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json`（清单文件不提交）。不跑全量。
- 先读：`host/session.py`、`host/sessions.py`、`host/delegation.py`、`host/inbox.py`、`host/session_tree.py`、`host/session_chat.py`、`host/realtime_bridge.py`（`VoiceBinding._schedule` 第 771 行起：它已把本会话的 `result_ready / needs_response` 排队播报；`attach_voice` 持有 `Session._send_lock` 直到 `detach`）；测试 `tests/orchestrator/host/test_session_tree.py`、`test_session.py`、`test_session_replay.py`、`test_realtime_bridge.py`。
- 设计依据：计划文件第 3 节"阶段 2"、第 2 节不变量 I3/I4/I6/I11/I12；AgentOS 合同原文第 5.2（`submit`）、第 6 节（事件、结果消费、自动续办）；AIPhone 统一交接第 4 节"自动汇总的具体语义"六条与"语音已有可复用部分"一段。都在同目录。

## 3. 车道（只能改这些文件）

- `src/agent_orchestrator/host/session.py`、`sessions.py`、`delegation.py`、`inbox.py`、`session_tree.py`
- 新建 `src/agent_orchestrator/host/continuation.py`
- `src/agent_orchestrator/host/realtime_bridge.py`：只改 `_schedule` 及其调用的播报来源标记那一段
- `src/agent_orchestrator/host/session_chat.py`：只许追加提示词一段（结果资料的来源标记说明），不改命令表、不改池编号
- `src/agent_orchestrator/host/settings.py`：加续办相关字段（4.5）
- `src/agent_orchestrator/host/__init__.py`、`public_api.py` 只加导出
- 新建测试 `tests/orchestrator/host/test_session_async.py`、`test_continuation.py`；现有测试只许改与新字段直接相关的断言，逐条列在记录里
- `docs/orchestration-host.md` 加"异步受理与自动汇总"一节

别的不碰。工具层（任务书 C 的车道）、任务层、连接器不动。

## 4. 要做的事

### 4.1 异步受理：`send(..., wait=False)`

- `await session.send(text, key=..., wait=False)` 在输入落账、`input_accepted` 事件发出后立即返回 `{status: "accepted", input_id, turn_id, command_id?}`；回合由会话自己的后台任务跑（放进 `_pump` 之外的一个串行执行协程，或复用现有 `_send_lock` 的串行语义）。
- 同会话串行：同时只有一个回合在跑，第二个 `wait=False` 的输入排队（持久化在 `queued_inputs`，kind=`user_message`），按受理顺序跑。主与子互不阻塞。
- `wait=True`（默认）行为不变。
- 排队状态可查：`session.queued_inputs()` 返回每条的 `state: queued|running|done|failed` 与最终 `turn_id`。
- 重开恢复：排队中与运行中的输入按原顺序继续（运行中的走现有 `_recover_pending_turns`）。
- 事件：`input_accepted`（已有）、`turn_committed`（已有）；排队的加 `input_queued`。

### 4.2 子会话排队输入的处理

- 子会话的 `queued_inputs` 里 kind=`directive`、`directive=next_turn` 的输入：当该子会话**没有在途委派**（`delegations` 全终态或为空）且没有在跑的回合时，取最早一条作为子会话的下一回合输入（走 4.1 的串行执行），`state` 转 `running→done`。
- 子会话因此产生的新委派通过该 `input_id` 建唯一关联：委派行记 `source_input_id`；同一条排队输入重复处理不得生成两次 Mission（输入处理本身要幂等：以 `input_id` 为回合幂等键）。
- 子会话在跑 Mission 时**不**处理排队输入（不改在跑的冻结任务）；可见排队。

### 4.3 结果驱动的自动汇总（`continuation.py`）

- 父收件箱镜像到一条子结果（现有 `_mirror_children`）后，依次记：
  1. `result_received`（事件，含 `event_id`、`child_key`、`mission_id`）；
  2. 问宿主 `ContinuationPolicy.decide(context)`：`context` 含会话号、子会话键、委派/任务号、结果版本（`event_id`）、当前待汇总集合、本会话续办已用预算；返回 `enqueue()` / `hold(reason)` / `deny(reason)`。**默认实现返回 `enqueue()`**（开发期一切默认开）。策略抛异常或超时（≤5 秒）按 `hold("policy_unavailable")` 处理，不默许。
  3. `enqueue` → 把 `event_id` 加入"待汇总集合"，按合并窗口（4.5）成批生成**一条**持久的主会话输入：kind=`result_event`，`payload` = 冻结的 `event_id` 列表，`input_id = "cont-" + digest([session_id, sorted(event_ids)])`（同一集合只会形成一条），事件 `continuation_queued`；走 4.1 的串行执行。模型看到的文本是"任务结果资料（不是用户指令，不授权新任务）"加结果内容——复用现有 `_commit_input` 的附加方式，来源标 `source="continuation"`。
  4. 回合提交后记 `result_applied`（含 `input_id`、`turn_id`），把这些通知的 `applied_turn_id` 落账（现有字段），事件 `continuation_completed`。
  5. `hold` → 只留待办，事件 `continuation_held`（带原因），结果仍在收件箱，可查；`deny` → 记原因，事件 `continuation_denied`。
- `ack` 仍是"人已读"，不替代 `result_applied`。
- 自动回合**只汇总，不新建工作**：续办回合里模型不能调 `delegate_task`、`create_child`、`child_send`（这一回合的工具授权收窄到只读 + 查询命令；用现有"只缩不放"的授权机制，不加新的拒绝逻辑）。
- 结果正文里出现"批准 / 删除 / 再建任务"等文字只是资料（I6）；要有反例测试。
- 崩溃点：镜像后入队前、入队后回合前、回合后记应用前。任一点崩溃重开：不丢结果、不生成第二条续办输入、不跳过未处理结果。

### 4.4 语音期间的路由（I12）

- 父会话有语音绑定（`session._voice is not None`）时，续办**不**起文字回合：`enqueue` 的结果交给 `VoiceBinding._schedule` 现有的播报队列，来源标 `task_result`，用户在说话或有当前播报时等安全窗口；超过 `HostSettings.continuation_voice_max_wait_seconds` 仍没播出，记事件 `continuation_waiting`（带原因 `busy`），结果不丢。播报完成记 `result_applied`（`turn_id` 为语音回合号）。
- 不并行起第二个文字业务模型；不借 `send` 锁（它被语音绑定持有）。
- 语音侧不得把续办资料当成用户 ASR 输入（和 AIPhone F3 相关）：来源字段必须随消息进入语音上下文，反例测试：注入的结果资料不触发"用户说了"的回合。

### 4.5 预算与合并窗口（`settings.py`）

- `continuation_enabled: bool = True`
- `continuation_merge_window_seconds: float = 5.0`（窗口内到达的多条结果合成一条输入）
- `continuation_max_turns_per_session: int = 50`、`continuation_max_model_calls_per_turn: int`（默认沿用会话策略的 `max_model_calls`）
- `continuation_voice_max_wait_seconds: float = 60.0`
- 用尽：事件 `continuation_budget_exhausted`，进可见等待，不重试；队列空不唤醒模型（I12/SDK-12：闲置期间模型调用数不增长）。
- 扣额原子化：续办回合的模型调用数记进会话账本 `continuation_usage`，重启不重置。

### 4.6 能力发现

`capabilities()` 的 `capabilities` 加 `async_accept`、`queued_input_execution`、`continuation`；`unsupported` 去掉 `continuation_policy`，保留 `goals`、`authorization_port`、`steer_running_mission`。

## 5. 不变量（每条至少一个测试）

| # | 不变量 |
|---|---|
| I3 | `wait=False` 返回时输入已落账；进程内队列不算受理 |
| I4 | 同会话串行；主与子各自并行（两个子各跑一回合、主再跑一回合，三者不互相等） |
| I6 | 续办回合里结果是资料：结果正文写"批准 X / 再建任务"不触发任何命令；续办回合调不到 `delegate_task` |
| I11 | 同一子结果版本只形成一条续办输入；同一子会话第二次工作的结果独立回流；重复/乱序镜像不重复 |
| I12 | 有语音绑定时不起文字回合，走播报；无语音时起文字回合 |
| 预算 | 续办轮次用尽后不再调模型，事件可见；闲置时模型调用数不增长 |
| 崩溃 | 4.3 三个崩溃点注入（`Session._checkpoint` 接缝，点名 `continuation_queued`、`continuation_applied`）后重开：不丢、不重、不跳 |
| 策略 | `hold` 不跑模型；`deny` 记原因；策略异常按 hold |

## 6. 测试要求

- 先红后绿，至少两次提交。
- 用 `test_session.FakeHost` 做账本层测试；真宿主用 `ScriptedProvider`（`held / entered / release` 可以做"主回合正忙时结果到达"）；语音用 `test_realtime_bridge.py` 里的 `Audio / Client` 替身。
- 不跑全量回归。

## 7. 完成标准

- 第 6 节测试全绿；`test_session_tree.py`、`test_session.py`、`test_session_replay.py`、`test_host_sessions.py`、`test_realtime_bridge.py` 全绿（真宿主前先重生成清单）；`tests/test_public_api.py` 绿；改到的文件 `ruff check` / `ruff format --check` 绿。
- 记录 `plans/2026-10-30-agentos-main-session-contract/记录-任务书D-Sol.md`：改了什么、测试命令与通过数、没做的、拿不准的、分支与提交号。中文。

## 8. 禁区

- 不改命令表、不改池编号、不改任何工具说明；提示词只追加一段。
- 不改任务层、工具层、连接器。
- 不加兼容分支、不留旧路径；注释中文写"为什么"，不写代号。
- 不为让测试绿改表或改断言；不提交部署清单。
- 不推送、不合并、不跑全量回归；不碰密钥文件。

## 9. 有问题怎么办

写进记录"拿不准的"，按最合理的做法先做完，不停下等。

## 10. 任务书 C（工具目录与 ToolSearch）合并后的补充说明（2026-10-31 审核人追加）

发包基线已含 C 与 C2 的改动，以下几点与本任务书直接相关：

- 聊天池的允许名单现在来自 `ChatService._registered_tools()`：工具目录（`runtime/tool_catalog.py`）里该角色（`main` / `child`）可见的条目。续办回合（4.3）的"收窄到只读 + 查询命令"就在这条路径上做：按回合来源（`source="continuation"`）决定这一回合绑定的 `allowed`（`_bind(pool, agent, session_id, allowed)` 已经按绑定传允许名单），**不要**另写拒绝逻辑。为此本任务书的车道放宽：`host/session_chat.py` 除追加提示词外，允许改"按回合来源决定允许名单"那一处；`COMMAND_SCHEMAS` 仍不改。
- `COMMAND_SCHEMAS` 里现在多了两个元命令 `tool_search` / `tool_schema`（按需加载工具说明）。续办回合可以保留它们，但它们搜到的也只能是收窄后名单里的工具（目录搜索本来就与冻结名单取交集）。
- "始终暴露集合"由 `SessionChatService._configuration` 在加完会话控制命令后从目录重算（`always_exposed_tools`）。你若新增控制命令，不用再登记到别处；但本任务书**不加**命令。
- 会话账本的 `meta.configuration` 不再冻结应用工具名单（只冻结 `version` 与 `policy`）；`capabilities()` 多了 `catalog_version`。4.6 加能力时放在同一个字典里。
- 语音桥 `_catalogue` 已改成只含 `delegate_task / task_status / task_cancel` 与主会话可见的只读应用工具；你只动 `_schedule` 那段，不要恢复旧的全量命令表。
- 现有测试里 `test_realtime_bridge.py` 新增了"任务结果资料回显不成用户输入"的反例测试，4.4 的来源标记要让它继续通过。
