# 任务书 C2（给 gpt-6.1-sol / Codex）：任务书 C 的补修

日期：2026-10-31。发包人与审核人：Claude 会话。在 **同一工作树** `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-C`、同一分支 `sol/C-tool-catalog` 上继续，基线是你的 `f386650d3`。先 `cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-C/sdk/simple-harness-sdk`。

## 0. 审核结论（先看这个）

任务书 C 车道内的实现审核通过：沙箱命令行、目录、角色过滤、按需加载的授权边界都对。两个独立审阅各报了一条阻断级，加上你自报的三处车道外冲突，审核人已裁定，全部在本任务书里补。**这次放宽车道**：下面第 2 节列出的文件都可以改。

## 1. 三处车道外冲突的裁定

| 冲突 | 裁定 | 怎么改 |
|---|---|---|
| 旧 Session 新注册工具后不能重开（`host/session.py` 冻结 `tools`） | **会话账本不再冻结应用工具名单**。只冻结 `version` 与 `policy`。 | `configuration` 里删掉 `"tools"` 键；`capabilities()` 返回里加 `catalog_version`（取 `getattr(host, "tool_catalog", None)`，没有就 None）。把你的 `test_known_session_catalog_freeze_constraint_is_reproducible` 改成正向：新注册一个工具后同会话号能重开、`session_config_frozen` 只对策略变化抛（`test_session_tree.py::test_identity_and_parent_cannot_be_rebound` 那条保持不变）。 |
| 池身份哈希全部内置说明（`runtime/model_router.py` 的 `tool_schema_hash`） | **池身份里只留内置工具的名字，不留说明文字和 schema**。 | `tool_schema_hash` 改为对 `sorted(read_tool_schemas(large=True))`（只有名字的列表）做哈希，键名改 `tool_names_hash`，`"schema": 1` 改 2。把 `test_known_builtin_schema_identity_constraint_is_reproducible` 改成正向：改一条内置说明文字后同数据根重开，池编号不变、持久意图校验通过、旧 Agent 能继续；再加一条：内置工具名单变了（多一个名字）→ 身份变、拒开。这会让所有旧数据根起不来，开发期口径，记录里写明即可。 |
| 聊天知识命令没有作用域 | **先从主/子会话可见集合里拿掉 `knowledge_list`、`knowledge_read`**，等会话有知识作用域再加（记入不支持项，不是你的事）。 | `MAIN_READ_TOOLS` 去掉这两个；相关断言同步（`test_tool_catalog.py` 主会话可见集合、`test_tool_exposure.py::test_tool_search_false_sends_every_allowed_schema` 用 `workspace_read_file`/`device_status` 代替 `knowledge_list`）；`test_known_chat_knowledge_scope_constraint_is_reproducible` 改成"主会话看不到知识两件、任务仍看得到"。文档"工具层"一节相应改。 |

## 2. 两条阻断级与配套改法

### 2.1 "始终暴露集合"只能有一个来源，且由宿主从目录算出来（规范轴阻断）

现状：`simple_harness/agents/execution.py` 写死 `ALWAYS_EXPOSED_TOOLS`，目录里的 `always_exposed` 没人读；任务书 E 要加 `cancel` 命令，合并后会被藏起来。开关用 schema `title` 夹带也要去掉。

改法（把配置从宿主一路传到执行层，不再用 `title`）：
- `simple_harness/agents/execution.py`：`AgentExecutionDriver` 与 `build_agent_execution_driver` 加参数 `always_exposed_tools: frozenset[str] | None = None`。`None` = 不做按需加载（全量下发，不建 `TurnToolExposure`）；非 None = 建 `TurnToolExposure(tools, turn_id=..., always_exposed=这个集合)`。删掉 `ALWAYS_EXPOSED_TOOLS`、`_public_spec`、读 `title` 的逻辑。`execution_policy` 返回 `None`（协议 `RunToolExposurePort.execution_policy` 的返回类型放宽成 `ToolExecutionPolicy | None`；`react_loop.py` 已经按 `policy is not None` 分支，不用改），这样未加载/名单外工具的执行判定与"没有曝光口"时完全一样。
- `simple_harness/agents/runtime.py`：装配时把 `ports` 上的新字段传给 `build_agent_execution_driver`。`ports` 的类型（看 `AgentRuntimePorts` 或同名 dataclass 在哪定义）加字段 `always_exposed_tools: frozenset[str] | None = None`。
- `agent_orchestrator/runtime/assembly.py`：`OrchestratorConfig` 加字段 `always_exposed_tools: frozenset[str] | None = None`（`kw_only=True, compare=False, repr=False`，与 `shared_provider_admission` 同款），组装 `ports` 时传下去；`governance/policies.py` 的 `SNAPSHOT_FIELDS` 登记为 `"excluded: ..."`（看 `control_commands` 那条怎么写），`tests/orchestrator/step08/test_policy_snapshot.py` 的排除集合同步。
- `host/host.py` 与 `host/chat.py`：宿主算 `frozenset(e.name for e in catalog 全部条目 if e.always_exposed)`（任务池用 `self.tool_catalog`，聊天池用该服务的 `_tool_catalog`），`settings.tool_search` 为 True 时传这个集合，False 时传 None。删掉 `search_schema`、`title` 相关代码；`META_SCHEMAS["tool_search"]` 就是最终 schema。
- `runtime/tool_catalog.py`：`always_exposed` 的规则写成一处：所有控制命令（`commands` 参数里的）+ `HISTORY_TOOLS` 为 True，其余 False；`catalog_for` 不再按名字判断 META。
- 测试：`test_tool_exposure.py` 里凡是靠 `title` 的改掉；加一条"宿主注册一个新的控制命令（任意名字）后它自动始终暴露"，证明不再靠写死名单。

### 2.2 恢复测试要证明"未加载的仍然看不见"（任务书轴阻断）

`tests/agents/test_tool_exposure.py` 的 `test_inflight_turn_reopens_from_sqlite_with_same_loaded_tools`（约 214、293 行）：注册一个允许但从未加载的工具（如 `other_status`），断言恢复后每次模型请求的说明名单与崩溃前**完全相等**，且 `other_status` 不在里面。审阅者实测：把恢复后改成全量下发，现有断言仍绿——补这条后必须红。

## 3. 其他要一起改的（非阻断但一起做）

1. 语音桥 `host/realtime_bridge.py` 的 `_catalogue` 现在把 `COMMAND_SCHEMAS` 全量给语音模型，但 `_dispatch` 只会执行 `delegate_task / task_status / task_cancel`，其余（`create_child`、`child_send`、`child_status`、`tool_search`、`tool_schema`）会落到 `host.tools[name]` 抛 KeyError。改为：`_catalogue` 只收 `_dispatch` 真能执行的三个命令 + 主会话角色可见的只读应用工具（用目录 `visible("main")`，不含元工具与内置）。加一条测试：语音模型调 `create_child` 得到 `permission_denied` 而不是 KeyError。
2. 元工具出错的返回统一：任务路径与聊天路径都用"抛异常 → 走现有工具失败回执"那一条，不要一边 `{"error": ...}` 一边抛。
3. `test_tool_catalog.py` 加一条集成断言：允许名单外的工具即使在目录里也**搜不到**（去掉 `invoke_meta` 里的 `narrowed` 这条要红）。
4. `host/chat.py`、`host/host.py` 的三处基线 E501 与 formatter 重排：这次允许改，把这两个文件整体 `ruff check` / `ruff format --check` 调绿。
5. 记录里"最终结果下补"的两处补上实际数字。

## 4. 不动的

任务层、连接器、`delegation.py` / `inbox.py` / `session_tree.py`、现有工具说明文字、并发默认值、池编号。不加兼容分支。不提交部署清单。不推送、不合并、不跑全量回归。

## 5. 完成标准

- 以下文件全绿（改源码后先重生成清单，命令同任务书 C）：`tests/orchestrator/host/test_tool_catalog.py`、`test_sandbox_run.py`、`tests/agents/test_tool_exposure.py`、`tests/orchestrator/host/test_custom_tools.py`、`test_domain_tool_runner.py`、`test_chat.py`、`test_session.py`、`test_session_tree.py`、`test_planner_sees_capabilities.py`、`test_realtime_bridge.py`、`tests/orchestrator/step08/test_policy_snapshot.py`、`tests/test_public_api.py`，以及 `model_router` 相关的现有测试（`grep -rl model_router tests` 找出来跑）。
- 改到的文件 `ruff check` / `ruff format --check` 绿。
- 记录追加到 `记录-任务书C-Sol.md` 末尾一节"C2 补修"：改了什么、测试命令与通过数、提交号。中文。拿不准的按最合理做法做完，不停下问。
