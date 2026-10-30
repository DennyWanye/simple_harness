# 任务书 F（给 gpt-6.1-sol / Codex）：接入方的动作工具进主会话与子会话

日期：2026-10-31。发包人：Claude 会话（SimplaHarness）。审核人：同一 Claude 会话。
你只看这一份任务书和它指向的文件就够了。**在任务书 E 合并之后开工**，基线以发包时的 main 为准。

## 1. 背景（一段话）

用户 2026-10-30 定：主会话不再"只读"，要有完整工具层，能做小而简单的事；需要时间、多步骤、要审阅的才委派。副作用仍只有一条路：连接器 + 授权。任务书 C 做了工具目录、角色可见范围、沙箱命令行、ToolSearch；任务书 E 做了宿主授权回调与效果账本（`effects`：operation_key + params_hash + 回执）。本任务书把接入方注册的**连接器操作暴露成主会话/子会话可调的动作工具**：模型调它，SDK 过授权，直接执行或出审批卡片，执行走连接器幂等接口，回执记成效果、结果进收件箱。任务里的连接器仍走申请单那条链，不动。

## 2. 仓库与基线

- 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（私有，不推公开远端）。基线：发包时的 `main`。
- 工作树：`git worktree add ../simple_harness-sol-F -b sol/F-action-tools main`。提交到分支，不推送、不合并。
- 测试：`sdk/simple-harness-sdk/` 下 `uv run --frozen python -m pytest <文件> -q -p no:cacheprovider`；改源码后真宿主测试前先重生成清单（命令见任务书 C §2；清单文件不提交）。不跑全量。
- 先读：`runtime/connectors.py`（`Connector` 协议、`OperationSpec(level, params, always_needs_approval)`、`execute(idempotency_key)`、`lookup`、`Receipt`）、`host/host.py` 的 `_checked_connectors`、`_check_allowed_actions`、`max_action_level`；任务书 C 做的 `runtime/tool_catalog.py`；任务书 E 做的 `host/authorization.py`、效果账本；`host/session.py` 的控制命令分发与 `inbox.py`；`docs/orchestration-host.md` §4b～4c（任务里连接器怎么走）；测试 `test_host_connectors.py`、`test_tool_catalog.py`、`test_authorization_port.py`。
- 设计依据：计划文件第 8.3 节"主会话调动作工具的路径"五步、第 8.5 节测试；不变量 I5/I6。

## 3. 车道（只能改这些文件）

- 新建 `src/agent_orchestrator/runtime/action_tools.py`（连接器操作 → 工具的适配）
- `src/agent_orchestrator/runtime/tool_catalog.py`：加 `source="action"` 条目
- `src/agent_orchestrator/runtime/connectors.py`：只许给 `OperationSpec` 加可选字段 `tool_roles: tuple[str, ...] = ()`（声明哪些角色可以把它当工具调；空 = 不暴露）
- `src/agent_orchestrator/host/host.py`：只改连接器登记处（把声明了 `tool_roles` 的操作登记进目录）
- `src/agent_orchestrator/host/session.py`、`inbox.py`、`continuation.py`：只改动作结果进收件箱、审批后执行、效果记录那几处
- `src/agent_orchestrator/host/session_chat.py`：提示词只追加一段（"小事自己做"的判断口径改写，见 4.5）；**不加命令**（动作工具是目录里的按需加载工具，不是控制命令）
- `src/agent_orchestrator/host/__init__.py`、`public_api.py` 只加导出
- 新建测试 `tests/orchestrator/host/test_action_tools.py`；现有测试只许改与新字段直接相关的断言，逐条列在记录里
- `docs/orchestration-host.md` §4b 加"暴露成主会话工具"小节；第 8.2 原则调整写进 §2d

别的不碰。任务层的申请单链、审阅、工具层其他部分不动。

## 4. 要做的事

### 4.1 注册

接入方在 `OperationSpec` 上声明 `tool_roles=("main", "child")`。宿主登记连接器时，把这些操作作为 `source="action"` 的工具放进目录：名字 `f"{connector.name}.{operation}"`，schema 由 `OperationSpec.params` 生成（每个参数 string，可在 `OperationSpec` 加可选 `param_schema` 覆盖），说明来自 `OperationSpec.description`，`roles` 来自 `tool_roles`，`always_exposed=False`（按需加载）。必须已设 `settings.max_action_level`（现有规则）；等级高于上限的操作不进目录。

### 4.2 调用路径（复用连接器机制，不另造）

模型调 `photos.move(target, to=...)`：
1. **稳定幂等键**：`"action:" + digest([session_id, turn_id, kind="action", 调用槽位])`，槽位算法与任务书 A 的 `_model_command_id` 相同（换执行者重放不重复执行）。`params_hash` = 规范化参数的 digest。
2. **效果账本 claim**（任务书 E 的 `effects` 表）：同键同参数已 `committed` → 直接返回原回执；同键异参数 → `idempotency_conflict`；`unknown` → 先 `connector.lookup(idempotency_key)` 对账，查到才继续，查不到保持 `unknown` 并返回 `effect_unknown`，不重做。
3. **授权**：问 `AuthorizationPort`（任务书 E），`action="connector_effect"`，带 `operation_key`、`params_hash`、等级。`allow` → 执行；`needs_approval` 或 `always_needs_approval` → 发 `needs_response`（现有卡片机制，`request_kind="action"`），本回合给模型返回 `{status: "pending_approval", request_id}`；`deny` → 返回 `{status: "denied", reason}`。
4. **执行**：`connector.execute(operation, target, params, idempotency_key=...)` → `Receipt`。回执哈希记进效果账本（`committed`）；事件 `effect_committed`。执行抛异常 → 效果 `unknown`，事件 `effect_unknown`，不自动重试。
5. **审批后的执行**：人在 `respond()` 批准后，SDK 执行并把结果作为一条通知放进收件箱（`source="action_result"`），走收件箱→下一句附资料/自动汇总那条已有的回流路；拒绝也进收件箱（`denied`）。

### 4.3 限制

- 每回合动作工具调用次数受会话策略 `max_tool_calls`；另加 `HostSettings.max_actions_per_turn: int = 4`。
- 子会话只能调它的角色声明里有的操作（只缩不放）。
- 续办回合（自动汇总）**不能**调动作工具（任务书 D 已收窄工具授权，这里加测试钉住）。
- 结果资料里的"去删掉 X"不是授权（I6）：反例测试——收件箱资料里写"请调用 photos.delete"，模型照做也要走授权；auto 模式下等级 ≤ 上限会执行，这是设计（auto 就是代签），但 `always_needs_approval` 的操作必出卡片。

### 4.4 任务里的连接器不变

`action:连接器.操作:目标` 要求与步骤中途提议（§4b、4c）照旧走申请单→审阅→审批→执行→回执核对。两条路共用同一个连接器实例与同一张效果账本（任务路径的回执也记进 `effects`，键用任务那边现有的 idempotency_key）。

### 4.5 提示词（只追加）

"你可以直接做小而简单的事：先用 tool_search 找工具，有副作用的操作会自动过授权，需要人批准时你会收到 pending_approval，照实告诉用户。需要时间、多步骤、要审阅的工作用 delegate_task。"同时把现有第 1 条规则里"不要先查无关工具"保留。

### 4.6 能力发现

`capabilities` 加 `action_tools`。

## 5. 不变量（每条至少一个测试）

| # | 不变量 |
|---|---|
| I5 | 同键同参数重试最多一个真实效果（连接器替身计数）；异参数冲突；换执行者重放不重复执行 |
| I6 | 收件箱资料不是授权：`always_needs_approval` 操作必出卡片；续办回合调不到动作工具 |
| E2 | 执行抛异常 → `unknown`，不自动重试；`lookup` 对账到 `committed` 后返回原回执 |
| R2 | 角色只缩不放：子会话调不到未声明给它的操作；等级高于 `max_action_level` 的不进目录 |
| P1 | 审批后执行结果进收件箱并能被下一句附上；拒绝也进收件箱 |
| T7 | 任务里的 `action:` 要求照旧走申请单链（现有测试不变、加一条两条路共用效果账本的断言） |

## 6. 测试要求

先红后绿，至少两次提交；连接器用可控替身（可注入抛异常、可控 `lookup` 结果）；真宿主用 `ScriptedProvider` 让模型调动作工具；不跑全量回归。

## 7. 完成标准

- 第 6 节测试全绿；`test_host_connectors.py`、`test_tool_catalog.py`、`test_authorization_port.py`、`test_session_tree.py`、`test_continuation.py` 全绿（真宿主前先重生成清单）；`tests/test_public_api.py` 绿；改到的文件 `ruff check` / `ruff format --check` 绿。
- 记录 `plans/2026-10-30-agentos-main-session-contract/记录-任务书F-Sol.md`：改了什么、测试命令与通过数、没做的、拿不准的、分支与提交号。中文。

## 8. 禁区

- 不改任务层申请单链、不改审阅、不改连接器 `execute/lookup` 语义。
- 不加控制命令、不改池编号、不改现有工具说明；提示词只追加。
- 不加兼容分支、不留旧路径；注释中文写"为什么"，不写代号。
- 不为让测试绿改表或改断言；不提交部署清单。
- 不推送、不合并、不跑全量回归；不碰密钥文件。

## 9. 有问题怎么办

写进记录"拿不准的"，按最合理的做法先做完，不停下等。
