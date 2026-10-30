# 任务书 C 执行记录（Sol / Codex）

日期：2026-10-31。状态：**BLOCKED，已完成车道内实现与专项验证，不宣称任务书全部完成。**

## 范围、基线与提交

- 已先切到 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`，读取任务书后执行原定命令：
  `git worktree add ../simple_harness-sol-C -b sol/C-tool-catalog main`。
- 基线：`53c1ac54c`（当前 main，已含 A、B 合并）；工作树：
  `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-C`；分支：`sol/C-tool-catalog`。
- `ff8cda011`：先提交三个新测试文件，源码尚未实现，均先出现缺少模块/类的预期收集错误。
- `96733cee3`：实现、补充回归和接入文档。后续记录提交只写本文件，不改变被测代码。
- 不推送、不合并、不发版，不改任务层、连接器协议、Session/委派/收件箱/会话树实现，未碰密钥。
- 使用 plan-task；验收事实源直接采用任务书 §5 的 T1–T6。按用户的严格车道和 §7 唯一记录要求，
  未另建 acceptance/baseline/journal/retro 文件、未改 ARCHITECTURE、未扩展测试到 UI/全量回归。
  实现集中处理装配与目录；独立子任务处理沙箱及执行层，独立只读 reviewer 审查正确性和覆盖。

## 改了什么

1. `DomainTool.roles` 默认 task/main/child；`_checked_tools` 校验角色子集、重复及保留工具名。
   任务配置只装配含 task 的应用工具；主/子会话按角色过滤，child 还须同时声明 main。
2. `ToolCatalog` / `CatalogEntry`：内置、技能、应用、预留动作来源；不可变 schema；确定性目录版本；
   大小写不敏感子串搜索、角色过滤、完整 schema 读取。新增公开导出，目录版本不送入池编号。
3. 主/子会话补充工作区读/列、知识列/读、两个历史工具与应用只读工具；没有工作区写、run_tests。
   工作区命令绑定各自聊天工作区。**知识命令目前只可见、调用仍不可用**，原因见下面的车道冲突。
4. `sandbox_run` 作为 SDK 自建只读 DomainTool，经已证明隔离的原 executor 执行；
   仅操作一次性工作区副本。argv/stdin/cwd/1–120 秒限制、双流各 64 KiB、超时整树清理、
   未确认终态 unknown。无 proven sandbox 时不出现；main/child 必须显式打开对应设置。
   使用同沙箱 Python wrapper 分流、送 stdin，不在宿主裸跑。真实网关参数需 thaw_json 后再校验。
5. 两个 ToolSearch 元工具：任务走 SDK DomainTool，聊天走 ControlCommand；目录与当前冻结允许名单
   取交集。名单外返回 not_found_or_forbidden；schema 返回完整条目且原子检查所有名字。
6. 执行层复用既有 RunToolExposurePort、工具回执观察和 checkpoint 的 tool_exposure_state，
   持久化本回合已加载集合；同回合恢复、下一回合清空。控制命令与历史工具始终暴露；其余按需。
   未加载的允许工具仍照常过原授权执行，不新增拒绝。没有改 AgentConfig 的完整允许名单合同。
7. `HostSettings.tool_search=True`、`main_session_sandbox_run=False` 及配置读取、布尔校验。
   SESSION_INSTRUCTIONS 仅追加任务书指定的一段；COMMAND_SCHEMAS 仅加两个元命令。
8. `docs/orchestration-host.md` 增加“工具层”，§4a 示例说明 roles，并明确尚未完成的边界。

## 接口取舍（按 §9 自主处理）

- 网关的工具结果必须是 JSON 对象，不能裸返回数组。元工具分别返回
  `{tools: [...], catalog_version}` / `{schemas: [...], catalog_version}`。
- 不改 assembly/ports 的车道外配置接口；新的 tool_search schema 用合法 title 标记
  `tool-search:lazy` / `tool-search:all` 传递开关，执行层发模型前去除此新内部标记。
  现有工具说明文字、schema 均未改动。
- 用户显式缩小工具名单但遗漏任一元工具时，全量下发该收窄名单的说明，避免模型没有搜索入口。
  不自动扩充授权名单；这不是旧路径兼容分支，是当前曝光规则对不可搜索名单的必要定义。
- 工具 schema 的输出结构保留完整 schema，包括参数说明；catalog_version 随元工具回执进入既有账本。
- sandbox_run 外层 DomainTool 的 succeeded 表示“回执交付”，命令终态看内部 status；
  status=unknown 时 exit_code=null。不能据外层 succeeded 声称命令成功。
- 技能三件套的既有发现/装载/执行机制未改；只将它们的说明列为按需加载。
- 搜索 limit 上限 256，与当前 AgentConfig 的完整工具名单上限相同，默认 8。

## 任务池与冻结身份（§4.5）

**任务池编号未改变，没有新起编号。** 新 sandbox/meta 以 SDK DomainTool 进入既有部署和角色交集，
会改变新部署/新 Mission/Task 的冻结工具名单与政策快照；已有名单仍冻结，不自动加入新工具。
`native_profile_id` 不读这个目录；未改并发默认值，也未改它或 assembly 的冻结检查。

应用 DomainTool 改说明、增加一个工具后，同数据根的旧 legacy Chat 池可以重开，池号保持，
旧 Agent 保持旧允许名单，新聊天能搜到新工具。该正向测试真实核对搜索结果含新工具。
这不等于旧 `Session` 的重开问题已解决，见下一节。

## 拿不准的 / 硬冲突 / 没做的

按 §9 不停下询问，也不越过 §3/§8 禁区或伪装冻结数据。

1. **旧 Session 新注册工具后不能重开。** 禁改的 `host/session.py:77–91` 在构造时比较
   `tools=list(host.tools)`，新工具名导致 `session_config_frozen`，早于聊天配置钩子。
   不能在获准钩子中正常修复；没有伪装 host.tools、改写旧账本或新增 Session 兼容路径。
   决定性复现：`test_known_session_catalog_freeze_constraint_is_reproducible`。
2. **池身份仍冻结全部内置说明。** 车道外 `runtime/model_router.py:107–119` 对
   全部 read_tool_schemas(large=True) 做哈希；assembly 的既有检查比较该 runtime_context。
   临时变更按需 workspace_read_file 说明后，池号虽相同，持久意图仍报
   `context identity differs from a persisted dispatch intent`。没有篡改冻结对象或绕过检查。
   决定性复现：`test_known_builtin_schema_identity_constraint_is_reproducible`。
   因而“只冻结始终暴露说明”的完整 §4.5 尚未完成。
3. **聊天知识命令没有有效作用域。** 现有 gateway 在 mission_id=None 时直接拒绝知识命令；
   聊天绑定就是 None。只加允许名单不能使其可用；同名控制命令或 DomainTool 也被网关拒绝。
   没有编造 Mission、跨任务读知识或伪造空知识库。需审核人裁定知识作用域并开放正常接线位置。
   决定性复现：`test_known_chat_knowledge_scope_constraint_is_reproducible`。
4. **完整文件 Ruff 不全绿，均为已核对的基线问题。** host/chat.py 原 logger/返回表达式两处 E501，
   host/host.py 原 native_profile_ids docstring 一处 E501；formatter 另要求重排原 open_session
   签名及上述聊天表达式。这些都在本任务允许修改的函数区域之外，未改。
   基线归档与当前 Ruff 的三处诊断内容完全相同，仅随新增行偏移；其他 12 个修改的 Python
   文件 check、format --check 全绿。没有通过改 Ruff 配置、加 ignore/noqa 规避。
5. 未做动作工具、宿主运行中热注册；未动任务提示词/调度语义；未发起真实收费模型调用。
   三个 known_* 测试证明限制确实存在，**它们的 PASS 不代表对应功能验收通过**。

## T1–T6 对账

| 不变量 | 实现/证据 | 结论 |
|---|---|---|
| T1 | 目录角色过滤、真实 task main-only 拒绝、真实 parent/child 收窄与子会话首回合、主会话无写/run_tests | PASS |
| T2 | 22 条真实 Seatbelt 沙箱测试；host 开关和探测不可用测试；无降级/无 skip | PASS |
| T3 | legacy Chat 搜索→schema→调用；真实任务搜索与名单外 schema；原子拒绝；schema 回执驱动加载 | PASS |
| T4 | checkpoint 序列化；真实在途回合工具完成→provider UNKNOWN→关闭→同 SQLite 新 runtime→reconcile/recover→同 turn 暴露集合不变；下一 turn 清空 | PASS（执行层实际恢复） |
| T5 | 应用说明/新工具变化不换 legacy Chat 池、旧 Agent 可继续、新聊天搜到新工具；False 全量 | PARTIAL；旧 Session、内置 schema 身份分离仍 FAIL |
| T6 | 允许但未加载的应用工具直接成功；实际任务未加载的工作区写仍成功；受限名单缺 meta 时仍能看见并执行 | PASS |

## 测试与原始证据

所有原始日志只留在本机 `.local-test-evidence/2026-10-31/sol-C/`（Git ignored）；不提交原始日志。
SDK 命令工作目录均为 `sdk/simple-harness-sdk/`。

清单命令（每次源码变更后、真宿主测试前运行；审核合并时须重新运行）：

```bash
uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-10-05/f2-upstream/evidence.json
```

采用主树同一份 upstream，只读；新工作树没有那份 ignored evidence。
生成清单含 851 项，清单自身不提交。

先红：以下三个命令各产生预期的缺少实现收集错误，之后才提交测试 ff8cda011、编写实现。

```bash
uv run --frozen python -m pytest tests/orchestrator/host/test_tool_catalog.py -q -p no:cacheprovider
uv run --frozen python -m pytest tests/orchestrator/host/test_sandbox_run.py -q -p no:cacheprovider
uv run --frozen python -m pytest tests/agents/test_tool_exposure.py -q -p no:cacheprovider
```

执行层测试采用现有目录 tests/agents，不建立不存在的 tests/unit/agents（任务书允许按现有布局）。

基线：首次并行测试受到稍后子进程读取新源码影响，不能当有效基线；已另用
`git archive 53c1ac54c` 将原 src/tests/pyproject 只读导出到 ignored 证据目录，并用绝对
PYTHONPATH 与同一 Python 解释器重新跑以下六文件。**168 passed，1 个既有 warning，171.12 秒。**
证据：`baseline-tests.log`。没有将污染的一轮当作基线绿。

指定六文件命令：

```bash
uv run --frozen python -m pytest tests/orchestrator/host/test_custom_tools.py tests/orchestrator/host/test_domain_tool_runner.py tests/orchestrator/host/test_chat.py tests/orchestrator/host/test_session.py tests/orchestrator/host/test_planner_sees_capabilities.py tests/test_public_api.py -q -p no:cacheprovider --tb=short
```

第一轮 168 passed / 2 failed（公开导出顺序、规划器工具名单）。修正后两文件专项
**108 passed，12.66 秒**（`fixed-regressions.log`）。其余原通过 62 项未新增语义变化。
提交 96733cee3 后再次完整跑上述六文件：**170 passed，1 个基线已有 warning，166.85 秒**，
`regression-tests-final.log`，exit 0。相比基线多 2 项，是新增两个公开模块的导出合同参数用例。

新测试最终命令：

```bash
uv run --frozen python -m pytest tests/orchestrator/host/test_tool_catalog.py tests/orchestrator/host/test_sandbox_run.py tests/agents/test_tool_exposure.py -q -p no:cacheprovider --tb=short
```

提交前 **42 passed，46.00 秒**（`new-tests-final.log`）；增强新工具搜索断言后单测 1 passed，
4.72 秒。提交 96733cee3 后整组再跑：**42 passed，45.94 秒**，`new-tests-head.log`，exit 0。
两组去重共 212 条；其中 3 条是已知约束复现，不能把它们计为对应功能验收通过。

沙箱新增的 wrapper 被杀与 Unicode 诊断超长回归又单独先红 2 failed，再修后 22 passed，3.50 秒。
沙箱为本机真实 macOS Seatbelt，0 skip；在无沙箱机器上测试会明确 skip 并附原因。

唯一修改的既有测试断言：`test_planner_sees_capabilities.py` 的
`test_planner_package_lists_registered_tools_and_proposable_actions`：
保留 photo_search 的原名字/说明与全部动作断言，新增元工具必须存在、只允许额外 sandbox_run、名单排序。
没有改动作/任务结果断言，没有改 tests/test_public_api.py。

Ruff 命令：对本次修改的 14 个 Python 文件逐个传路径运行
`uv run --frozen ruff check <文件列表>` 和 `uv run --frozen ruff format --check <文件列表>`。
完整 exit 1（3 个基线 E501 / 2 个基线格式文件），日志 `ruff-check.log` / `ruff-format.log`；
其余 12 文件分别 exit 0 / exit 0。`git diff --check` 通过。

## 独立审查与复验

独立 reviewer 发现两项正确性问题并已修复：任务 schema 拒绝错误被 DomainTool 异常封装吞掉；
受限名单无元工具时全部说明被藏起。两项均有真实入口决定性回归。
另补 wrapper 被杀不得误报终态、Unicode 诊断输出按字节截断两条先红后绿回归。
审查最终不外推上述三个已知车道冲突；不以多个局部 PASS 稀释 T5 和知识可用性的未完成。

自我批评：首轮基线与源码编辑并行，使子进程读到不同版本，浪费了一轮验证；后改为只读原提交归档
核基线并在源码稳定、清单更新后复验。独立 review 真正拦住了授权说明边界问题。

独立终审基于 `53c1ac54c..96733cee3` 核对 15 个实现提交文件与 §6 唯一既有测试例外，
无越界文件、无新增池号/并发改动，无新增可复现正确性阻断。一次最小化审查结论
`ALREADY_MINIMAL`。本文件为第 16 个交付文件，由 §7 显式授权。

## 最终证据索引与 DoD

被测源码 HEAD：`96733cee3`。以下文件均在 `.local-test-evidence/2026-10-31/sol-C/`，
仅本机保留，表内是 SHA-256。

| 文件 | SHA-256 |
|---|---|
| `baseline-tests.log` | `1469bc3d8825c7cb07730a52ea99607fd9d3d3e2366ee6a2f3fb458b79d37198` |
| `new-tests-head.log` | `d645ed3272c72ee827e8ea50ccdb77b838dc7be0c0aafcb216fac7a9a37376b6` |
| `regression-tests-final.log` | `9692dc41e73eab56f31815806b1a3a74a83ba7b1c4e40533d2a691bdda704a8a` |
| `fixed-regressions.log` | `7a995cf7982982d5bd90e2a22158a138c1e02e163c9cc598a951f41272fcbcad` |
| `ruff-check.log` | `9e5dfbb8bf98409ece90907035ece65524643eb55228015767fe65622a4e4ea6` |
| `ruff-format.log` | `b5201509f9783f28f4d79be765f6c0a078bb17ca15f2cf844e2418825d1501db` |
| `generated-manifest.json` | `55d25c1cc5190c29250746d9ad692d8aca71a98437a1df3a0e204e04642eacf4` |

- 已满足：红测试提交→实现提交；车道核对；指定六文件与新增专项测试；独立审阅；本记录与接入文档；
  不推送/不合并/不提交清单。生成清单已另存本机证据后恢复仓库版本，审核真宿主测试前须重生成。
- 未满足：§4.5 全部身份解耦、旧 Session 新工具重开、§4.2 聊天知识实际调用、完整文件 Ruff 全绿。
  对应失败和原因均已复现；没有用新数据根或固定报错测试冒充已修复。
- 不生成机器 receipt；完成度以任务书和本记录逐条对账，不声称 100%。

VERDICT: BLOCKED — 车道内实现已提交并经 212 条专项/指定测试核验；三处禁改位置契约冲突与基线 Ruff 未闭合 — 2026-10-31 — 96733cee3

## C2 补修

日期：2026-10-31。依据主树 `任务书-给Sol-C2-补修.md` 的明确裁定继续。
本节记录取代上文 C 时点的未完成结论；上文保留作为过程历史。
最终状态：**C2 补修完成；本任务书指定范围全部核验通过，未推送、未合并、未发版。**

### 范围、提交与基线

- 按要求先切到 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-C/sdk/simple-harness-sdk`。
  同一工作树、同一分支 `sol/C-tool-catalog`，基线 `f386650d3`。
- 开工时唯一已有改动是审核重生成的部署清单，先保存其原始字节到本机证据目录；不将其纳入提交。
- `912c33668`：先提交 C2 红测试（同号 Session、身份、目录配置、语音、精确恢复和 schema 2 消费者）。
- `4493e32cb`：补修实现、旧测试夹具同步、文档。
- `23d965643`：修复本轮完整指定核验发现的两处既有测试预期（不改生产代码）。
  本节记录另作提交，不改变被测源码。
- C 已核验基线（212 条专项/指定测试）和审核结论沿用；本轮先跑新增预期为红，未将旧失败约束
  测试的 PASS 当成 C2 功能通过。原记录两处“最终结果下补”在 C 交付时已填为 170/42，通过数已复核。

### 按裁定补了什么

1. Session 账本配置只保留 version/policy，取消应用工具名冻结；策略变化仍拒绝。
   `capabilities()` 返回宿主目录的 catalog_version，没有目录时为 None。
   正向测试覆盖：同号 Session 新注册工具后重开、旧 Agent 继续、目录版本更新；原策略/父子身份
   不可重绑测试保持原样。
2. 池身份 schema 升 2，tool_names_hash 只哈希排序后的内置工具名。
   改内置说明后同数据根、同池号、持久意图校验与旧 Agent 继续均通过；新增内置名会改变身份并拒开。
   **身份 schema 1 的旧数据根不能再打开，开发期需新根；不加兼容分支，不新增池编号。**
3. 主/子会话移除知识两工具，任务继续保留。会话知识作用域作为当前不支持项写入接入文档，
   这是本轮批准的范围裁定，不再作为未修缺口。
4. 始终暴露规则只有目录一处：commands + HISTORY_TOOLS。宿主从目录条目生成 frozenset，
   经 OrchestratorConfig → AgentRuntimePorts → runtime → driver 传递；False 传 None，全量下发。
   Session 补齐最终控制命令后重算。新任意控制命令自动可见，无硬编码名字与 schema title 标记。
   显式收窄名单不含元工具时不再保留 C 的特殊全量分支；集合仍只决定说明，直接调用允许工具照常执行。
5. ToolSearch 的 execution_policy 返回 None，不再伪造非项目效果策略；授权仍走原注册表与执行器。
   新字段在政策快照中明确 excluded（非授权合同），字段分类测试同步。
6. 恢复测试加允许但未加载的 other_status，逐次精确比较恢复前后完整工具说明名字 tuple，
   既证明 device_status 仍在，也证明 other_status 仍不在。
7. 语音目录只包含实际支持的 delegate_task/task_status/task_cancel，以及 main 可见的只读应用工具。
   通过 catalog_for(host.tools).visible("main") 正常构建目录，排除所有内置/元工具；
   无 tool_catalog 的测试宿主也走同一目录规则。已有用户响应绑定下调用 create_child 返回
   permission_denied，非 KeyError，未创建子会话。
8. 元工具越界都抛异常走原有失败回执，任务不再把 {error: ...} 包进成功结果。
   原任务测试同步检查既有 domain 工具失败封套的 tool_failed；聊天仍走既有控制命令错误回执。
9. 整体格式化 host/chat.py、host/host.py，三处 E501 与原 formatter 问题全部修复；没有新增 ignore/noqa。

### 调查发现的两处必要同步（按“不停下问”处理）

- 任务书称 react_loop 已支持 None，实际 `_preflight_tool_batch` 会无条件读 None.effect_class。
  在 `simple_harness/runtime/drivers/react_loop.py` 加 6 行必要分支：无强制上下文授权时跳过额外策略；
  authority_required=True 时仍按原规则硬拒绝。两条决定性测试先红 AttributeError，修后通过。
  不在执行层伪造策略，也不绕过授权。
- 改为 schema 2 后，除了获准修改的 assembly 读取器，`runtime/first_request_budget.py` 仍只认 1，
  会让真实新任务的首请求预算退为 runtime_context_schema_unknown。仅同步该读器为 2，
  同步 `tests/orchestrator/p35/test_first_request_budget.py` 的身份 fixture/预期，保留预算 cap 协议
  自己的 schema_version=1。该测试额外纳入本轮指定范围。

这两处是兑现已批准 None/schema 2 语义所必需的最小生产接线修复；未改任务层、连接器、委派、
收件箱、会话树、并发默认值或现有工具说明文字。独立 reviewer 确认必要性和最小范围。

### 测试准备与先红后绿

证据均在本机 ignored 目录 `.local-test-evidence/2026-10-31/sol-C2/`，不提交原始日志。

- 宿主新增预期先红：6 failed / 1 passed（red-host.log）；说明搜索允许名单强测在旧实现上原已通过。
- 新执行层/None 预检四条先红：4 failed；schema 2 首请求/快照分类先红：6 failed / 6 passed
  （red-contract.log）。然后才进入实现提交。
- 首轮定向 94 passed / 4 failed（focused-first.log）。除旧 task 错误封套预期外，另修两处旧测试夹具：
  语音历史注入测试用 __new__ 跳过构造，缺已存在的完成结果读取端口；补齐 uow/turn_id_for 测试端口，
  原历史注入与相同输入断言不变。首请求 fixture 的 context-v2 输出预期同步，cap schema_version 不变。
- 语音 Service SDK 合同测试需已锁定的 realtime extra。用 `uv run --frozen --extra realtime` 安装/运行
  锁中的 Service SDK 0.3.20，没有改 pyproject/lock，也没有替换成伪 SDK；所有调用仍是离线脚本/测试客户端。
- 修后夹具/Service 合同/首请求专项 9 passed（fixed-focused.log）；任务、语音与执行层决定性复验
  13 passed（decisive.log）。
- 实现提交后价值冒烟 16 passed / 27.28 秒（committed-smoke.log），覆盖同号重开、池身份稳定、任意新
  控制命令、搜索允许名单、语音拒绝以及全部执行层曝光/恢复测试。
- 恢复后全量下发的进程内变异：1 failed，严格 tuple 相等断言捕获多出的 other_status，符合预期
  （exposure-recovery-full-mutation.log）。没有修改源码文件。
- 去掉 narrowed 的进程内变异：1 failed，搜索结果泄出 device_status 被集成断言捕获，符合预期
  （search-allowlist-mutation.log）。没有源码变异遗留。
- 全部 22 个修改的 Python 文件 Ruff check 与 format --check 均 exit 0；包含本轮允许整理的
  host/chat.py、host/host.py。证据 ruff-check.log、ruff-format.log；git diff --check 通过。

### 完整指定核验与交付

工作目录：`sdk/simple-harness-sdk/`。源码变更后已先重生成清单：

```bash
uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-10-05/f2-upstream/evidence.json
```

本轮运行 21 个指定/相关文件；由任务书必跑文件、`rg -l model_router tests` 的十文件去重，
再加必要首请求预算测试组成，没有跑全量回归。精确命令（提供已核对 SHA-256 的本机固定分词器）：

```bash
SH_TOKENIZER_PATH=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-10-26/session-real-click/models-opt219/deepseek-v41/tokenizer.json \
uv run --frozen --extra realtime python -m pytest \
  tests/agents/arp/test_arp_orchestrator_pool.py \
  tests/agents/test_tool_exposure.py \
  tests/orchestrator/full_target/test_progress_idle_verdict.py \
  tests/orchestrator/host/test_chat.py \
  tests/orchestrator/host/test_custom_tools.py \
  tests/orchestrator/host/test_domain_tool_runner.py \
  tests/orchestrator/host/test_planner_sees_capabilities.py \
  tests/orchestrator/host/test_realtime_bridge.py \
  tests/orchestrator/host/test_sandbox_run.py \
  tests/orchestrator/host/test_session.py \
  tests/orchestrator/host/test_session_tree.py \
  tests/orchestrator/host/test_tool_catalog.py \
  tests/orchestrator/p33/test_g_large_read_context.py \
  tests/orchestrator/p34/test_strict_runtime_profile.py \
  tests/orchestrator/p35/test_first_request_budget.py \
  tests/orchestrator/p35/test_multi_profile_load.py \
  tests/orchestrator/step06/test_model_router.py \
  tests/orchestrator/step06/test_step06_review_fixes.py \
  tests/orchestrator/step08/test_policy_snapshot.py \
  tests/orchestrator/step09/test_policy_library.py \
  tests/test_public_api.py \
  -q -p no:cacheprovider --tb=short
```

Ruff 精确输入另存 `changed-python.txt`（22 项），运行
`uv run --frozen ruff check <这22个文件>` / `uv run --frozen ruff format --check <这22个文件>`；
两者 exit 0。

首轮完整指定核验：**388 passed / 1 failed / 3 skipped，263.19 秒**（all-selected.log）。
唯一失败为政策测试的固定调用次数断言；三个 skip 为未提供固定分词器。均已调查，不算通过。

补充处理的既有测试预期（均属本任务书要求运行的 model_router 相关文件）：

- `test_policy_library.py` 原先要求模板调用恰好一次，但基线 f386 和本轮 HEAD 中，准入和执行两处
  均已通过 Mission 模板选择器。只读复制基线测试重跑也红：1 failed（policy-baseline-repro.log）。
  没有将 1 改成 2，而改为 AST 逐一要求所有 role_for_task 调用都被 self._template(..., mission.id)
  包裹且集合非空；原配置白名单、router 与共享 selector 断言保留。移除任一处绑定的变异都会红，
  独立 reviewer 还分别验证了两个分支。任务层源码未改。
- 供应固定分词器后，`test_strict_runtime_profile.py` 又暴露一个基线错误：它把旧 0f06… 指纹和
  prior-output=True 断言放在当前类上。独立构造 f386 与 HEAD 两类，实测当前类均为 f29fec… /
  wire-only-v2 / False，显式 Legacy 类均为 0f06… / plus-prior-v1 / True；生产计数器文件逐字节相同。
  这源自 2026-09-24 的既有实现，非 C2 改变。测试保留原历史指纹常量并绑定显式 Legacy 类，
  另精确固定当前类指纹及当前/strict 的 False；原严格 HTTP/计数一致、roundtrip 和 mismatch 拒绝
  断言全部保留。没有改计数器源码、指纹算法、分词器 SHA 或资产。
- 上述两文件 **11 passed，2.84 秒**（baseline-oracles-fixed.log）。证据还包括
  counter-baseline-identities.log、strict-profile.log 与 policy-binding-mutation.log。
  独立终审和最小化审查通过，无 P0/P1，结论 ALREADY_MINIMAL。

最终从提交态 `23d965643` 重跑：**392 passed / 0 failed / 0 skipped，292.54 秒**，exit 0
（all-selected-final.log）。另有 1 个原有 RuntimeWarning：test_domain_tool_runner.py 的无效返回值
测试创建的 sleep 协程未 await；基线已存在，非本轮引入，不扩大修改范围。
392 为本次 21 文件的去重用例数，不叠加前述冒烟、专项或预期失败变异。

| C2 要求 | 最终证据 | 结论 |
|---|---|---|
| Session 工具目录变化可重开，策略/父子身份不放宽 | 真实 Session 正向测试；原 identity_and_parent_cannot_be_rebound 原样通过 | PASS |
| 身份只固定内置名，说明变化不换池，名字变化拒开 | 同数据根/持久意图/旧 Agent 集成测试；schema 2 两个读取入口 | PASS |
| 会话移除知识、任务保留 | 实际可见集合、父子收窄与任务名单 | PASS |
| 始终集合唯一来源、显式配置接线、False 全量 | 任意新控制命令自动暴露、开关、完整冻结许可和直接调用测试 | PASS |
| 恢复前后完整集合相等、未加载工具仍隐藏 | other_status 负向断言；恢复全量变异捕获 | PASS |
| 语音只暴露可执行命令与 main 应用工具 | 精确目录断言；create_child 由有效响应上下文调用仍 permission_denied | PASS |
| 元工具异常失败路径、搜索不能越过冻结名单 | 真实 task/chat 回执、去掉 narrowed 的变异捕获 | PASS |
| 指定全部文件、相关 model_router 文件、Ruff | 21 文件 392 通过，无 skip；22 Python 文件 check/format 通过 | PASS |

### C2 最终证据索引

以下 SHA-256 对应本机 ignored 目录 `.local-test-evidence/2026-10-31/sol-C2/`，
原始日志、分词器与生成清单均不提交。

| 文件 | SHA-256 |
|---|---|
| `red-host.log` | `cd5afbe7c4a2b73ca64836a040b5ab0c744b971a1bce5873b08e39fa6746c066` |
| `red-contract.log` | `5d537c2a15caecc3776c6b036cded50253a645447f98e68308448f8bca38e3ed` |
| `all-selected.log` | `da523540b11687feb622e30bcb0ec0d5079b9a03f992c49a1204752ec129c35f` |
| `all-selected-final.log` | `abeca76c96a301c194681415da8fa1abbbb49f1ec5438da345815a361209c488` |
| `selected-tests.txt` | `e2e8ec5f7a7eb6a0d069e9457cee428a50beff8ebefec7e93a6e501245cb2194` |
| `committed-smoke.log` | `0c3d7f197a75bc7a5395d757fd94a836915f91217a8b2e9e8c44fddfcff59c2c` |
| `ruff-check.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `ruff-format.log` | `7b4549ac2e9820f695aa60c4695796689a11c0dce1a49f0b01a6250eca9f55aa` |
| `changed-python.txt` | `e945b9d11636d332570f9a7e4bb3e00c3cac7d91443b7f433375142e53767cb1` |
| `exposure-recovery-full-mutation.log` | `14fd4ec9f1b6723a06a81e7d73b4b3f6e1a6f5007877ed691280848b98a0ebfc` |
| `search-allowlist-mutation.log` | `2b35cf8188bdaa9313c24bfa2e07a8c745ade50b056fc6346f43cd2925f514b1` |
| `policy-baseline-repro.log` | `584710026879de6266688f04a935d4faf135a010c7219b7661c69a23f8c27fbd` |
| `policy-binding-mutation.log` | `d3ebcfb24d835d737247483d805eec7c9b6fd706cdcc7572a77433475042252a` |
| `counter-baseline-identities.log` | `cf8993b117b2332a1a910f020e1f229856e213c940ecfbb1ef43e3859fe93537` |
| `baseline-oracles-fixed.log` | `8dec04d853636d40b4718089f90d3ddd7ed7eda1090b7aeecea0e7732fdfde42` |
| `generated-manifest.json` | `aee54c61fb15171e9879b237d7305179a42a8e05aee9cf4b069c86dd2f85c5e3` |
| `initial-manifest.json` | `55d25c1cc5190c29250746d9ad692d8aca71a98437a1df3a0e204e04642eacf4` |

最终生成清单与被测源码匹配，仍未提交；开工前审核清单的原始字节另存 initial-manifest.json。
清单是工作树唯一预期保留的未提交生成产物。原记录前 199 行逐字节保留，本节为追加。
独立正确性审查、测试合同审查和最小化审查通过，无待修 P0/P1；结论 ALREADY_MINIMAL。
没有改任务层、连接器、delegation/inbox/session_tree、计数器源码/固定资产或池编号；未推送/合并。
会话知识作用域、动作工具与热注册为批准范围外；没有未完成的 C2 MUST。

自我批评：应在整组核验前先供应固定分词器，避免 skip 遮住历史测试预期错误；本轮通过
基线字节核对、独立历史身份实测和变异检查修正测试合同，没有为绿色修改生产语义。

VERDICT: SHIPPED — 任务书 C2 指定源码补修与 21 文件/392 项核验完成，无机器 receipt，未发版 — 2026-10-31 — 23d965643
