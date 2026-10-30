# 任务书 C（给 gpt-6.1-sol / Codex）：工具目录、角色可见范围、沙箱命令行、ToolSearch

日期：2026-10-31。发包人：Claude 会话（SimplaHarness）。审核人：同一 Claude 会话。
你只看这一份任务书和它指向的文件就够了。**本任务书在任务书 B 合并之后才开工**，基线以发包时的 main 为准。

## 1. 背景（一段话）

Simple Harness SDK（Python 包 `agent_orchestrator` + `simple_harness`，源码在 `sdk/simple-harness-sdk/`）里，模型能用的工具分四块：SDK 内置（工作区读写列、知识库列读、跑测试、历史回读）、技能三件套、接入方注册的只读 `DomainTool`、连接器（有副作用，只在任务里经审批执行）。现状三个问题：主会话（`Session`，`host/session*.py`）连内置的工作区读都没有，只能聊和委派；没有命令行工具；工具名单按任务/会话冻结，工具说明文字是执行池身份，每加一个工具都要换池编号、旧数据起不来。本任务书做：一个工具目录、按角色给可见范围、主会话补内置只读工具、沙箱命令行 `sandbox_run`、ToolSearch（池身份只冻结少量元工具，其余工具按需搜索加载）。**不做**动作工具（连接器暴露成工具，任务书 F）、不做运行期加工具不重开宿主（阶段 3 后）。

## 2. 仓库与基线

- 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（私有，不推公开远端）。基线：发包时的 `main`（含任务书 A、B 的合并）。
- 工作树：`git worktree add ../simple_harness-sol-C -b sol/C-tool-catalog main`。提交到分支，不推送、不合并。
- 测试在 `sdk/simple-harness-sdk/` 下 `uv run --frozen python -m pytest <文件> -q -p no:cacheprovider`。只跑你改到的文件对应的测试，不跑全量。
- **改了源码后，真宿主测试会报 `taskgraph_deployed_source_unverified`**：在 `sdk/simple-harness-sdk/` 下先跑 `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json` 重生成清单（它改 `src/agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json`，**这个文件不要提交**，审核人合并时重生成）。
- 先读：
  - `src/agent_orchestrator/runtime/domain_tools.py`（`DomainTool`、`ToolCallContext`）
  - `src/agent_orchestrator/host/host.py`：`_checked_tools`、`_assemble_runtime` 里 `base_tools / skill_tools / tool_names / DeploymentPolicy` 那一段（第 680～730 行附近）、`sandboxed` 与 `run_tests` 的关系、`_build_native`
  - `src/agent_orchestrator/host/chat.py` 的 `_configuration`、`_native_plane`、`_agent`（工具名单在会话第一句冻结）；`src/agent_orchestrator/host/session_chat.py` 的 `_registered_tools`、`_configuration`、`_native_plane`、`COMMAND_SCHEMAS`
  - `src/agent_orchestrator/deployment/goal_world.py` 的 `WORKSPACE_TOOLS`；`src/agent_orchestrator/governance/policies.py` 的 `SKILL_TOOL_NAMES`、`DeploymentPolicy`
  - `src/agent_orchestrator/runtime/sandbox.py`（`SandboxSpec`）、`src/agent_orchestrator/host/script_runner.py`（现有沙箱执行器的用法：seatbelt / bwrap、无网络、rlimits、进程树清理、输出上限）
  - `src/simple_harness/agents/execution.py`：搜 `tool_catalog`、`tool_catalog_generation`、`tool_catalog_fingerprint`（执行层已有"工具目录代次与指纹"的概念），以及每次模型调用怎么拼工具说明
  - `src/simple_harness/agents/runtime.py` 的 `_DefaultToolCatalog`
  - 池身份怎么算：`src/agent_orchestrator/deployment/native_pools.py` 的 `native_profile_id`、`src/agent_orchestrator/runtime/assembly.py` 的 `_check_intent_contexts`（持久意图里冻结了什么）
  - 现有测试：`tests/orchestrator/host/test_custom_tools.py`、`test_domain_tool_runner.py`、`test_chat.py`、`test_session.py`、`test_planner_sees_capabilities.py`
- 设计依据：`plans/2026-10-30-agentos-main-session-contract/计划-长期主会话统筹子会话-含F1修复-2026-10-30.md` 第 8 节。

## 3. 车道（只能改这些文件）

- `src/agent_orchestrator/runtime/domain_tools.py`（加 `roles`）
- 新建 `src/agent_orchestrator/runtime/tool_catalog.py`（目录、角色、版本、搜索）
- 新建 `src/agent_orchestrator/runtime/sandbox_run.py`（命令行工具）
- `src/agent_orchestrator/host/host.py`：只改工具装配那一段与 `HostSettings` 读取处
- `src/agent_orchestrator/host/settings.py`：加两个字段（见 4.3、4.4）
- `src/agent_orchestrator/host/chat.py`、`src/agent_orchestrator/host/session_chat.py`：只改工具名单/授权/配置那几处（`_configuration`、`_native_plane`、`_registered_tools`）；`session_chat.py` 的 `COMMAND_SCHEMAS` **只许加** `tool_search`、`tool_schema` 两个元命令，提示词 `SESSION_INSTRUCTIONS` 只许追加一段"先搜工具再用"
- `src/simple_harness/agents/execution.py`、`src/simple_harness/agents/runtime.py`、`src/simple_harness/agents/config.py`：只改"每次模型调用暴露哪些工具说明"与"本回合已加载集合的持久化"
- `src/agent_orchestrator/public_api.py`、`src/agent_orchestrator/host/__init__.py` 只加导出
- 新建测试 `tests/orchestrator/host/test_tool_catalog.py`、`tests/orchestrator/host/test_sandbox_run.py`、`tests/unit/agents/test_tool_exposure.py`（目录名按现有测试布局放）
- `docs/orchestration-host.md` 加一节"工具层"，§4a 加 `roles` 说明

别的不碰。任务层（`orchestrator/`、`planning/`、`graph/`）、连接器协议、`session.py`/`delegation.py`/`inbox.py`/`session_tree.py`（任务书 D 的车道）一律不动。

## 4. 要做的事

### 4.1 一个工具目录（`runtime/tool_catalog.py`）

- `ToolCatalog`：把四种来源收进一张表——SDK 内置、技能三件套、应用 `DomainTool`、（预留）动作工具。每条有 `name, description, schema, source, roles, always_exposed: bool`。
- 角色固定三个：`"task"`（任务步骤）、`"main"`（根会话）、`"child"`（子会话）。`roles` 只缩不放：子会话 ⊆ 主会话声明；任务步骤 ⊆ 任务 ∩ 角色 ∩ 部署（现有规则不变）。
- `DomainTool` 加字段 `roles: tuple[str, ...] = ("task", "main", "child")`。`_checked_tools` 校验取值。
- 目录有版本：`catalog_version = digest(所有条目的 name+schema+roles+source)`。**这个版本不进池身份**（见 4.5）。
- `ToolCatalog.visible(role)`、`.search(role, query, limit=8)`（名字、说明里大小写不敏感子串匹配，只在该角色可见范围内搜）、`.schemas(role, names)`。

### 4.2 主会话与子会话补内置只读工具

- 主会话、子会话默认可见：`workspace_read_file`、`workspace_list`、`knowledge_list`、`knowledge_read`、`session_history_read`、`session_history_search`，加应用只读 `DomainTool`（按 `roles`）。**不给** `workspace_write_file`、`run_tests`（写和跑属任务）。
- 工作区：主会话已有 `chat-<会话号>` 工作区绑定（`chat.py` 的 `_bind`），工作区工具对它生效；子会话同理。
- 任务步骤的内置名单不变，加 4.3 的 `sandbox_run`。

### 4.3 沙箱命令行 `sandbox_run`（`runtime/sandbox_run.py`）

- 参数：`argv: list[str]`（≥1）、`stdin: str = ""`（≤64 KB）、`timeout_seconds: int`（1～120，默认 60）、`cwd: str = "."`（工作区内相对路径，不许 `..` 和绝对路径）。
- 复用现有沙箱执行器（`script_runner.py` 用的那一个）：macOS seatbelt / Linux bwrap、**无网络**、rlimits、超时杀整棵进程树。不是沙箱里裸跑。
- 返回 `{exit_code, stdout, stderr, truncated: bool, duration_ms, killed_by_timeout: bool}`；stdout/stderr 各截 64 KB，截断必须标出；结束无法确认的报 `unknown`，不当成功。
- 可见范围：任务步骤默认有（沙箱可用时，与 `run_tests` 同条件）；主会话/子会话默认**没有**，宿主 `HostSettings.main_session_sandbox_run: bool = False` 设 True 才有。沙箱不可用的机器上这个工具不出现，**不降级成裸跑**。
- 它是只读工具的语义：改宿主文件仍走连接器；它改的只是沙箱里的工作区副本（现有 `run_tests` 的工作区语义）。

### 4.4 ToolSearch

- 元命令两个，进 `COMMAND_SCHEMAS`（它们在池身份里，以后不再变）：
  - `tool_search {query: str, limit?: int}` → `[{name, description, source}]`，只在当前角色可见范围内搜。
  - `tool_schema {names: [str]}` → 返回这些工具的完整 schema，**并把它们加入本回合的"已加载集合"**；名单外的名字报 `not_found_or_forbidden`。
- 每次模型调用暴露的工具说明 = 始终暴露集合 + 本回合已加载集合。始终暴露集合：所有控制命令（委派、子会话、任务查询/取消、两个元命令）+ 历史回读两个。其余（内置工作区/知识、`sandbox_run`、应用 `DomainTool`、技能三件套）进"按需加载"。
- **授权不变**：`AgentConfig.tool_names` 仍是该角色的完整允许名单（只缩不放、冻结）；ToolSearch 只影响"模型看见哪些说明"，不影响"哪些能调"。模型直接调一个没加载过的允许名单内工具：照常执行（不要为了 ToolSearch 多设一道拒绝）。
- 已加载集合要**持久化到回合**（执行层 checkpoint 或 journal 里），崩溃恢复后同一回合暴露同样的集合，不靠内存。
- 开关：`HostSettings.tool_search: bool = True`（默认开，开发期一切默认开）。设 False 时全部工具全量下发，行为与现在一致。
- 技能三件套已有自己的"发现/加载"，不改它们；它们作为普通的按需加载工具进目录。

### 4.5 池身份

- 池身份（`native_profile_id` + `_check_intent_contexts` 冻结的意图）里只能有：始终暴露集合的说明文字 + 并发默认值（现有）。目录版本、按需加载工具的说明文字**不得**进池身份。
- 本任务书允许的池身份变更：**没有新的池编号变更**。A 已把聊天池升到 `chat-v3-c2-`；加两个元命令会改 `COMMAND_SCHEMAS`，这属于 v3 的内容（A、C 同一个开发期版本，旧库本来就起不来）。任务池：加 `sandbox_run` 到任务步骤名单会改任务池的工具名单——先查这会不会改任务池编号或持久意图；会的话在记录里写明，**不要**自己起新编号，交审核人定。
- 测试：换一个 `DomainTool` 的说明文字、再注册一个新 `DomainTool`，重开同一数据根：池编号不变、旧会话能打开、模型能搜到新工具。

### 4.6 提示词

`SESSION_INSTRUCTIONS` 追加一段（只追加）："你直接看到的只有常用命令。要用别的工具先 `tool_search` 找，再 `tool_schema` 拿到说明后调用。小而简单的事自己做；需要时间、多步骤、要审阅的交给 `delegate_task`。"任务池的提示词在任务层，不动。

## 5. 不变量（每条至少一个测试）

| # | 不变量 |
|---|---|
| T1 | 角色可见范围只缩不放：子会话看不到主会话没声明的工具；任务看不到角色外的工具；主会话看不到 `workspace_write_file`、`run_tests` |
| T2 | `sandbox_run`：无网络（沙箱内访问网络失败）、超时被杀且 `killed_by_timeout=True`、输出超 64 KB 截断且 `truncated=True`、`cwd` 越界被拒、主会话未声明时不可见、沙箱不可用时不出现 |
| T3 | `tool_search` 只在允许名单内搜到；`tool_schema` 名单外报 `not_found_or_forbidden`；加载后同回合可调 |
| T4 | 已加载集合持久：回合中途崩溃重开，下一次模型调用暴露同样的集合 |
| T5 | 目录换版本不改池编号；旧会话能开；`tool_search=False` 时全量下发 |
| T6 | 直接调允许名单内未加载的工具照常执行（ToolSearch 不加拒绝） |

## 6. 测试要求

- 先写测试看它红，再写代码；至少两次提交（测试、实现）。
- 新测试用现有 `FixtureWordCounter` + `ScriptedProvider`（`tests/orchestrator/host/test_chat.py` 的 `_chat_script` / `_device_tool` 可复用）；沙箱测试在没有沙箱的机器上要 `skip` 并写明原因，不能假绿。
- 现有测试只许改断言里"工具名单/说明"直接相关的那几处，改了的逐条列在记录里。
- 不跑全量回归。

## 7. 完成标准

- 第 6 节测试全绿；`test_custom_tools.py`、`test_domain_tool_runner.py`、`test_chat.py`、`test_session.py`、`test_planner_sees_capabilities.py` 全绿（真宿主测试前先重生成清单）；`tests/test_public_api.py` 绿；对本次改到的文件 `ruff check` / `ruff format --check` 绿。
- 记录 `plans/2026-10-30-agentos-main-session-contract/记录-任务书C-Sol.md`：改了什么、测试命令与通过数、任务池编号是否受影响（4.5）、没做的、拿不准的、分支与提交号。中文。

## 8. 禁区

- 不改任务层、连接器、`session.py` / `delegation.py` / `inbox.py` / `session_tree.py`。
- 不改并发默认值；不改现有工具的说明文字（加 `roles` 字段不算）；不起新池编号。
- 不加兼容分支、不留旧路径；注释中文写"为什么"，不写代号。
- 不为让测试绿改表或改断言；不提交部署清单。
- 不推送、不合并、不跑全量回归；不碰密钥文件。

## 9. 有问题怎么办

写进记录"拿不准的"，按最合理的做法先做完，不停下等。
