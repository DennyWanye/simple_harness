# 主流程优化执行计划（2026-09-25）

状态：草案第 3 版。第 2 版按子代理挑战意见修改（附录 B）；第 3 版按用户 2026-09-25 两条决定修改：①会话数据永久保留、超 5 GB 在设置页提醒；②开发阶段不考虑旧数据兼容，只按最佳实践做。待用户 review 定稿
基线：Host main `e68b40a6`，内置 SDK `sdk/simple-harness-sdk`，版本 `0.13.0.dev20260924+arp.23`

## 0. 范围与判断标准

"主体任务"指：用户在应用里提交一个任务，系统拆解后交给 Agent 执行，经过质量检查，最后正确完成；模型用 DeepSeek，思考和不思考两种模式都能跑。

一条问题只要满足下面任意一条，就进入本轮：
- 会让正常任务失败、卡住，或者白跑几轮；
- 会让结果看起来对、其实是错的，比如检查没真做、记录写错；
- 出错时完全看不见。

用户另外指定加入三条：会话数据占用提醒、自动批准的记录身份、召回结果页超上限。

**两条总原则（用户 2026-09-25 决定）**
- **开发阶段，不考虑旧数据兼容。** 只做最符合最佳实践的架构和处理方法：一份当前契约，不留双分支、不留"为老任务保留字节"的代码。开发库里按旧契约建的任务不保证能继续跑，遇到时明确报错，不静默回落。
- **会话数据永久保留。** 聊天记录、检索索引、日志都是审计材料，不做自动回收、不做清理按钮。只做占用统计和超过 5 GB 的提醒。

**事实来源**：今天三个 session 各做了一次"代码对计划"的审计。之后又派了三个只读子代理，在**产品实际使用的内置 SDK**上重新核对（早上任务分解那份审计对照的是旧的独立仓库，有几条已经过时）。每条问题的文件和行号都来自这次重新核对，实施前要再确认一遍。

| # | 条目 | 模块 | 为什么进本轮 |
|---|---|---|---|
| 1 | 执行图面板的消息到不了后台 | Host | 任务页的"执行图"面板只会显示读取超时（新发现，已亲自确认） |
| 2 | 可选决定列表和解码器不一致 | 任务分解 | 9-23 有 7 局真实模型因此判"看不懂"，任务失败 |
| 3 | 要求测试的判据没跑到任何测试也算通过 | 质量保证 | 检查形同没做 |
| 4 | 自动批准被记录成"人批准" | 质量保证 | 用户指定 |
| 5 | 召回结果超过 16 页时整条模型请求失败 | Agent 运行层 | 用户指定 |
| 6 | 后台循环吞掉错误，而且一项出错整轮就停 | Agent 运行层 | 出错看不见，找回和清理会默默卡住 |
| 7 | 会话数据占用看不见 | Host + 前端 | 用户指定：数据永久保留供审计，超过 5 GB 在设置页提醒；实测一个任务留下 40 个会话、70MB |

**核对后不进本轮的**：见文末附录 A，包括"证书一签发就失效"的调查结论。

## 1. 执行方式

- 开一个 worktree，分支 `opt-0925`，从 main `e68b40a6` 拉出；SDK 就是仓库里的 `sdk/simple-harness-sdk`，所以只有一个仓库。
- 主代理亲手写代码；子代理只做最后一次只挡阻断问题的核验。
- 每条的顺序：先写会失败的测试，再改代码，然后跑本条列出的定向测试。
- 全量回归只跑两次：合并前一次，合并后一次。
- 真实模型只跑一局，放在最后的真机点击里。
- 不用 `git stash`（多个 worktree 共用一个 stash）。
- 后台长任务要配看门狗：日志 10 分钟没动静就通知。
- 顺序：1 → 2 → 3 → 4 → 5 → 6 → 7。各条之间没有依赖。

SDK 定向测试命令（在 `sdk/simple-harness-sdk` 目录下）：
`PYTHONPATH=src uv run --frozen --group dev --extra local-capacity pytest <文件> -q -x -p no:cacheprovider`

Host 定向测试：在 `backend/` 下用 `.venv/bin/python -m pytest <文件> -q`；前端用 `cd tauri-app && pnpm vitest run <文件>`。

---

## 2. 条目 1：执行图消息接通

**现状**
- 前端 `tauri-app/src/views/MissionTaskGraph.tsx:155` 发送的消息类型是 `taskgraph.snapshot` / `taskgraph.why_not_ready` / `taskgraph.diff` / `taskgraph.convergence`。
- 后台 `backend/main.py:14708` 只把 `mission_`、`orchestration_` 开头的消息转给 `deskpet.orchestration.handlers.handle`。
- `handlers.py:36-39,239-242` 已经注册了这 4 个类型，但消息根本到不了。

**改法**
- `main.py:14708` 的条件改成 `msg_type.startswith(("mission_", "orchestration_", "taskgraph."))`。
- 故意**不**放行 `agent_` 前缀。一放行，技能安装就会走通，而"技能按档位各存一份"的问题会立刻冒出来（附录 A-6）。

**接口契约（不变，写出来是为了测试对照）**
- 请求：`{type: "taskgraph.<kind>", request_id: str, payload: {mission_id: str, ...}}`
  - snapshot：`{revision?: int}`
  - why_not_ready：`{occurrence_id: str}`
  - diff：`{from_revision: int, to_revision: int}`
  - convergence：`{}`
- 响应：`{type: "taskgraph.<kind>_response", request_id, payload: {ok: bool, request_id, data?, error?, taskgraph_error?}}`（`handlers.py:61-68,112-115`）。
- 实施第一步先确认：响应里 `payload.request_id` 的位置和前端 `MissionTaskGraph.tsx:111` 对得上；对不上就以前端为准，改 handler。

**测试**
- 新增 `backend/tests/orchestration/test_ws_taskgraph_routing.py`：用 WebSocket 测试客户端发 `taskgraph.snapshot`，断言收到 `taskgraph.snapshot_response`，且 `request_id` 一致；再发一个 `agent_runtime_request`，断言仍然不转给编排 handler（没有响应或落到"未知类型"分支）。
- 真机点击时打开执行图面板，确认能显示。

---

## 3. 条目 2：可选决定列表与解码器对齐

**现状**
- `orchestrator/planner_views.py:180-202`：把内部启用矩阵 `H4_DECISION_ENABLEMENT` 里可执行的键直接交给模型。共 17 项，其中 9 项是 `REPAIR/<子类>`，单独的 `REPAIR` 不在里面。
- `contracts/planning_decisions.py:84-95`：解码器只认 9 个 `PlanningDecisionType` 值。模型写 `REPAIR/RETRY_SAME_METHOD` 会被判为 `DECISION_TYPE_UNKNOWN`。
- 提示词 v8 第 2 条（`runtime/role_templates.py:706`）要求"decision_type 只能取 enabled_decision_types 里的值"；v10（`:783-810`）又用 `REPAIR/X` 的写法描述用法。两处合在一起，就等于教模型写出非法值。
- 证据：`.local-test-evidence/2026-09-23/assurance-1.1-verify/host-real-model/` 下 run-7/8/9/10/13/14/19，共 7 局，Grok 每局都连写两次 `REPAIR/RETRY_SAME_METHOD`，任务失败。DeepSeek 在 9-25 写对了。

**根因**：内部的启用键（`决定类型/修复子类`，用来做准入和授权）被原样当成了给模型看的"决定类型"。这是两个不同的概念，混用了。

**设计原则（按"不考虑旧数据兼容"）**
- 给模型的契约只有一套：`decision_type` 取 `PlanningDecisionType` 的 9 个值；修复子类放 `payload.repair_kind`。请求包里用两个字段分别告诉模型哪些可用。
- 内部启用键不改名，也不再出现在任何给模型看的地方。内部键和对外字段之间只有一个转换函数，双向都走它。
- 当前包版本只有一个，代码里不再有"第 7 版这样、第 8 版那样"的分支。历史包版本 4/5/6/7 的选提示词分支和标签映射一并删除。
- 解码器不做"把 `REPAIR/X` 改写成 `REPAIR`"这种回落式纠错。

**改法**

1. `runtime/role_templates.py`
   - 新增 `PLANNER_HIERARCHICAL_V11_VERSION = "planner-hierarchical-v11"`，`instructions = V10.instructions + 规则`。规则原文：
     > "输出格式硬规则：decision_type 只写 enabled_decision_types 里列出的值，这些值都不含斜杠。上文所有 REPAIR/某子类 的写法，都表示 decision_type=REPAIR，并在 payload.repair_kind 写该子类；子类只能取 enabled_repair_kinds 里列出的值。绝对不要把 REPAIR/某子类 整体写进 decision_type。"
   - 加入 `HIERARCHICAL_PLANNER_VERSIONS`。
   - `PLANNING_DECISION_PACKAGE_VERSION = 8`；新增常量 `PLANNING_DECISION_PACKAGE_LABEL = "planner-package-hierarchical-v10"`（`-v9` 已被 `planner_package_v1.py:22` 占用）。标签字符串只在这一处定义。
   - `HIERARCHICAL_PLANNER_VERSIONS_BY_PACKAGE`：当前包只配 `{V11}`。4/5/6/7 的条目删除；`PLANNER_HIERARCHICAL_V8/V9/V10` 只被历史条目引用的就删掉，`_revise` 链路需要的才保留。
2. `orchestrator/planner_views.py:201`：单一路径，不按包版本分支。
   - `enabled_decision_types = sorted({key.split("/", 1)[0] for key, v in enablement.items() if v.executable})`
   - `enabled_repair_kinds = sorted({key.split("/", 1)[1] for key, v in enablement.items() if v.executable and key.startswith("REPAIR/")})`
   - 两者都放进 `result["planning_protocol"]`。
   - `:218` 的标签改用 `PLANNING_DECISION_PACKAGE_LABEL`。
3. `event_handler.py:4228-4229` 的反向映射：只保留 `PLANNING_DECISION_PACKAGE_LABEL → PLANNING_DECISION_PACKAGE_VERSION`，删除 `-v8`、`-v7` 等历史映射。
4. `event_handler.py:4781-4830` `_hierarchical_planner_template`：只剩两个分支——绑定是新协议且包版本等于当前版本 → V11；绑定不存在或是旧协议 → 旧协议提示词（不动）。包版本是别的值 → 抛 `ContractError("unsupported planning package version")`，不回落。
5. `planning_protocol_binding.py:30-33`：`prompt_version` 不再手写，取 `HIERARCHICAL_PLANNER_VERSIONS_BY_PACKAGE[PLANNING_DECISION_PACKAGE_VERSION]` 的唯一元素（模块加载时断言恰好一个），这样版本号和提示词不可能脱节。
6. **准入侧把对外字段还原成内部键**：`event_handler.py:4683-4690` 现在从请求包的 `enabled_decision_types` 读启用集合，`:4734` 的 `repair_allowed` 也按内部键判断。
   - 在 `contracts/planning_decisions.py` 新增一对纯函数：
     - `exposed_enablement(enablement: Mapping[str, DecisionEnablement]) -> tuple[list[str], list[str]]`（内部键 → 对外两个数组），第 2 步用它；
     - `internal_enablement_keys(decision_types: Iterable[str], repair_kinds: Iterable[str]) -> frozenset[str]`（对外两个数组 → 内部键：`REPAIR` 展开成 `REPAIR/<k>`，其余原样）。
   - `:4683` 改为调用 `internal_enablement_keys`，下游只看内部键。两个函数互为逆运算，用测试钉死。
7. **修复开关关闭时的过滤**（`event_handler.py:4196-4199`）：同时去掉 `REPAIR` 并清空 `enabled_repair_kinds`，不再按 `REPAIR/` 前缀过滤。
8. **重问时的纠正提示**：在解码失败、重问模型的地方（实施时搜 `DECISION_TYPE_UNKNOWN` 定位），如果原值含 `/` 且斜杠前是 `REPAIR`，反馈里加一句"应写 decision_type=REPAIR，payload.repair_kind=<斜杠后部分>"。只是给模型的反馈文字，不改解码结果。
9. **旧数据**：开发库里绑定在包 7 的任务，派发时会按第 4 步明确报错。实施记录里写一句：真机点击前换全新的数据目录。

**接口契约（请求包里给模型看的部分）**
```json
"planning_protocol": {
  "...原有字段不变...": "...",
  "package_version": "planner-package-hierarchical-v10",
  "enabled_decision_types": ["BIND_EXISTING_GOAL","DECLARE_BLOCKED","NO_CHANGE","PROPOSE_METHOD","REFINE","REPAIR","REQUEST_EVIDENCE","REQUEST_HUMAN","WAIT"],
  "enabled_repair_kinds": ["CANCEL_BRANCH","DECLARE_RUNTIME_BLOCKED","ESCALATE","PROPOSE_SUCCESSOR","REBIND_INPUT","REFINE_DEEPER","REPLACE_METHOD","REQUEST_COMPENSATION","RETRY_SAME_METHOD"]
}
```
- 约束一：`enabled_decision_types` 的每个值都是 `PlanningDecisionType` 成员。
- 约束二：`enabled_repair_kinds` 的每个值都是解码器接受的 `repair_kind` 枚举成员。
- 两个数组由启用矩阵算出，不手写；测试钉死两条约束。

**测试**（新增 `tests/orchestrator/full_target/test_planning_decision_enablement_contract.py`）
- 请求包视图：两个数组都没有含 `/` 的值，并满足两条约束。
- `exposed_enablement` 和 `internal_enablement_keys` 互逆：对 `H4_DECISION_ENABLEMENT` 来回一次得到同一个内部键集合。
- 新建任务的绑定行等于 `planning-decision-v1|8|planner-hierarchical-v11`；`hierarchical_planner_pairing_is_valid("planner-hierarchical-v11", 8)` 为真，v10 配 8 为假。
- 手工插入一条包 7 的绑定，派发时抛 `ContractError`，不回落。
- 回复 `decision_type=REPAIR, payload.repair_kind=RETRY_SAME_METHOD`：能解码、能通过准入，内部键算出 `REPAIR/RETRY_SAME_METHOD`。
- 回复 `decision_type="REPAIR/RETRY_SAME_METHOD"`：仍判 `DECISION_TYPE_UNKNOWN`，重问反馈里带纠正提示。
- 修复开关关闭时：数组里没有 `REPAIR`，`enabled_repair_kinds` 为空。
- 现有测试：`test_h4_package7_entry.py`、`test_planning_decision_package_v4.py` 等凡是钉旧包字节或旧标签的，按新契约改写或删除，不保留"旧字节不变"的断言。其余回归：`test_planning_decision_prompt_v8.py`、`test_h1h_wiring.py`、`test_planning_decision_codec.py`、`test_planning_protocol_switch.py`。

**风险**
- 实施第一步用 grep 把 `PLANNING_DECISION_PACKAGE_VERSION`、`planner-package-hierarchical-v`、`== 7`、`>= 7` 全部列出来逐个处理；`planner_views.py` 里 `h4 = ... >= 7` 这类判断改成"绑定是新协议"这一个条件。
- 授权行 `planning_lane_grants.allowed_decisions_json` 和 `taskgraph_policy_sources.py:78` 存的是内部键，不受影响。

---

## 4. 条目 3：要求测试的判据必须真跑到测试

**现状**
- `verification/deterministic_checks.py:198` 的 `code_test(...)`，在 `:222-223` 把 `pytest:` 后面的空目标折成 None；`:249-257` 在目标为 None 且退出码为 5（没收集到测试）时记为通过。
- `assurance/executor_checks.py:122-136` 也有同样的放行。
- 23 个真实跑局库里，只写 `pytest:`（不带目标）的判据一次都没出现过。零测试放行共 17 次，全部来自"没人写判据、系统顺带全量跑一次"的情况。

**用户已定规则**：文档类任务的代码测试没有可证明的内容时，不计入必过检查。

**改法**（只改 `deterministic_checks.py` 一处，不动任何参与哈希的文档）
1. 在 `code_test` 内部区分两类运行：
   - 判据要求的：目标来自 `pytest:` 判据。**只写 `pytest:`、后面为空也算这一类**。
   - 顺带的：没有任何 `pytest:` 判据时，系统顺带全量跑一次。
2. 只有"顺带的 + 退出码 5"才走原来的"无可证明内容 / `no_tests_collected`"路径，不计入必过，符合文档任务规则。
3. "判据要求的 + 退出码 5"（包括空目标、目标里一个测试都没有）→ FAIL，原因写"判据要求的测试一个都没跑到"，**不写 `no_tests_collected` 标记**。
4. 这样 `executor_checks.py:122-136` 读到的是 FAIL 或者没有 `no_tests_collected`，自然不会放行，**executor_checks 不用改**。
   - 那边的文档行是按白名单逐字段构造的（`:65-75`），加字段会引发 KeyError，还会改动参与哈希的文档。
5. 修正 `:222` 附近说放行范围的注释。
6. **不在规划阶段拦截**：`task_graph.py` 和 `changes.py` 那两处只覆盖旧的 DAG 路径；产品用的层次规划，判据来自需求修订版和任务级判据（`occurrence_tasks.py:278-285`），走不到那里。主流程只靠运行时这一道兜底。

**接口契约**
- 运行结果的数据结构不新增字段。
- 行为变化只有一条：判据要求的运行，没收集到测试时，由"通过"变成"失败"。

**测试**
- 扩展 `tests/orchestrator/test_code_test_no_tests_collected.py`，覆盖三种情况：
  - 判据 `pytest:tests/empty_dir`（目录里没有测试）→ FAIL，结果里没有 `no_tests_collected`；
  - 判据只写 `pytest:` → FAIL；
  - 没有判据的文档任务 → 仍然不计入必过。
- 回归：`tests/orchestrator/full_target/assurance_exec/test_executor_check_in_gate.py`。

---

## 5. 条目 4：自动批准如实记录

**现状**
- `orchestrator/assurance_check_policy.py:283-290` 批准时写的是 `actor_type="human", actor_id=principal.principal_id`，`:52` 的说明也写"人明确批准"。
- 实际调用方是 Host 的 `backend/deskpet/orchestration/assurance.py:154-237 project_check_policies()`（`:210`）自动批准。
- `actor_type` 是自由文本。下游没有任何代码要求它等于 human：`storage/assurance_reads.py:221` 只要求特定事件是 system，而这个事件不在那张表里（`event_kinds.py:4-11`）。

**改法**
- `approve_check_policy(...)` 新增仅限关键字的参数 `approval_source: Literal["HUMAN", "HOST_LOSSLESS_AUTO"] = "HUMAN"`。
  - `HUMAN`：写 `actor_type="human"`，和现在一样。
  - `HOST_LOSSLESS_AUTO`：写 `actor_type="system"`，`actor_id` 用 Host 的系统身份（实施时沿用 Host 已有的系统 actor 常量，没有就用 `"host:assurance-projector"`）。
  - 事件内容新增 `approval_source` 和 `on_behalf_of_principal_id`（原来的 principal_id）。
- `api/facade.py:143-167` 的对外入口：放行可选字段 `approval_source`，默认 `HUMAN`，其他值直接拒绝。
- Host 的 `project_check_policies()` 调用时传 `approval_source="HOST_LOSSLESS_AUTO"`。
- 修正 `:52` 的说明。

**接口契约**
- 事件 `AssuranceCheckPolicyApproved` 的内容新增两个字段：`approval_source: "HUMAN"|"HOST_LOSSLESS_AUTO"`、`on_behalf_of_principal_id: str`。
- **`approval_source` 不进入批准回执正文 `request`**（`:98-112`），否则同一命令重放时会报 `IMMUTABLE_IDENTITY_CONFLICT`。
- UI 本轮不改。

**测试**
- 在 `assurance_exec/test_check_policy_projector_port.py` 里加断言：经 Host 投影器批准的事件是 `actor_type="system"`、`approval_source="HOST_LOSSLESS_AUTO"`。
- 直接调用对外入口、不传该字段时，仍是 `human`。
- 同一 `command_id` 重放不冲突。
- 回归：`test_check_policy_lossless_mapping.py`、`backend/tests/orchestration/test_assurance_host_api.py`。

---

## 6. 条目 5：召回结果页超上限时改为跳过召回

**现状**
- `context/recall.py:365-405` 每一步取一页结果，但不检查已提交的页数。
- 到第 17 页时，`_cas` 的结构校验（`result_pages_committed` 上限 16）抛出 `ArpError`，最后变成 `ArpContextRejected(retryable=False)`（`composer.py:612-624`），整条模型请求失败。
- 计划要求：`REVIEW.zh-CN.md:58-62`、`CONTEXT-RECALL.md:63,103`，说明原因后停止召回。

**改法**：在 `_step` 取下一页之前加判断：
```python
if (point["phase"] == "FETCHING_RESULTS" and point["cursor_token"] is not None
        and int(point["result_pages_committed"]) >= MAX_RESULT_PAGES):
    return self._skipped(row, point, "RECALL_AGGREGATE_LIMIT", access)
```
- `MAX_RESULT_PAGES` 复用结构校验里的 16，从同一个常量读取，不另写一个数字。
- `_skipped`（`:491`）已经会产出 `outcome=SKIPPED, status=PARTIAL, skip_code`。装填器遇到非 READY 的结果，只是不放召回材料，其他照常，所以模型请求照常发出。

**接口契约**
- 新增 skip 码 `RECALL_AGGREGATE_LIMIT`。
- 召回回执：`outcome=SKIPPED`，`status=PARTIAL`。

**测试**（`tests/.../test_arp_index_recall.py`）
- 把 `page_items` 打补丁改成 2，放 40 条可命中的内容：第 17 页之前停下，回执是 SKIPPED/PARTIAL，码为 `RECALL_AGGREGATE_LIMIT`，模型请求照常构造。
- 刚好 16 页读完、游标为空时 → 正常 READY，不能误判为超限。

---

## 7. 条目 6：后台错误可见，并逐项隔离

**现状**
- `sdk/.../agents/runtime.py:494-513` 的 `_index_pump`：`run_once()` 出错时 `except Exception: settled=0`，`tick_async()` 出错时 `except Exception: pass`，都不写日志。
- `agents/arp/runtime.py:122-145` 的 `_tick_after` 依次做三件事：推进待销毁会话、内置工具健康探测、召回续跑。
- `session_lifecycle.py:653-666` 的 `drive_draining` 遇到不是 FILE_BUSY 的错误就抛出，这一轮后面的会话和召回全部不再处理，而且每一轮都会重复。
- 计划要求：非预期异常转人工处理，并在 15 分钟内报警（`JOBS-LIFECYCLE.md:79`）。

**改法**
1. SDK 新增 `agents/background_health.py`：
```python
@dataclass(frozen=True)
class BackgroundHealth:
    loop: Literal["index", "draining", "recall", "tool_probe", "reap"]
    consecutive_failures: int
    last_ok_at_ms: int | None
    last_error_at_ms: int | None
    last_error_code: str | None      # ArpError.code 或异常类名
    stuck_item: str | None           # session_id / job_id 等，出错那一项
```
   再加一个小的内存登记器 `BackgroundHealthBook`，提供 `ok(loop)`、`fail(loop, code, item)`、`snapshot() -> tuple[BackgroundHealth, ...]`。只在内存里，重启后清零。
2. 日志：用 `logging.getLogger("agent_orchestrator.agents.background")`，失败时 `warning(..., exc_info=True)`。同一个 `(loop, code)` 60 秒内只打一条完整堆栈，其余只计数。Host 的根记录器会自动把它写进 `backend.log`（`main.py:75-100`）。
3. 逐项隔离：
   - `drive_draining` 和召回续跑改成逐项 try：出错的那一项调用 `book.fail(...)` 后跳过，继续处理下一项。
   - `arp/runtime.py:125-126` 的内置工具健康探测也单独包一层 try，它出错不能打断后面的召回续跑。
   - 所有项都处理完才算一轮结束。
   - 能被正确分类的终态错误（原本就写进库的）行为不变。
4. `_index_pump` 的两个 except 改为 `book.fail(...)` 加日志，不再静默。
5. 对外：`AgentRuntime.background_health() -> tuple[BackgroundHealth, ...]`。
6. Host：
   - `HostNativePlane` 目前不保存 runtime。在它的 `after_build(runtime)` 钩子里（`native_plane.py:322`，由 `assembly.py:633` 调用）按池登记 runtime：`self._runtimes[profile_id] = runtime`。`_rebuild` 时覆盖登记。
   - `status()`（`:343-351`）返回的是 `profiles` 列表，不是 `pools`。在每个 profile 条目里加 `background: [BackgroundHealth 的字典形式]`，取自登记的 runtime；没有登记的池给空列表。
   - `service.status()`（`:936-943`）原样透传。
7. 前端：
   - `missionsStore.ts` 解析 `native_plane.profiles[*].background`。
   - `MissionsView.tsx` 的状态区只在某个循环 `consecutive_failures >= 3` 时显示一行，格式为 `后台整理出错：<循环中文名>，已连续 <N> 次（<错误码>）`。
   - 平时不显示任何东西。
   - 循环中文名：index=建索引，draining=清理会话，recall=找回旧内容，tool_probe=工具检查。

**接口契约**
- `orchestration_status_response.payload.data.native_plane.profiles[i].background: Array<{loop, consecutive_failures, last_ok_at_ms, last_error_at_ms, last_error_code, stuck_item}>`
- 旧前端忽略这个字段即可，向后兼容。

**测试**
- SDK：在 `test_arp_session_lifecycle.py` 里构造两个待销毁会话，第一个注入非 FILE_BUSY 异常。断言：第二个仍被推进；`background_health()` 里 draining 连续失败为 1，`stuck_item` 等于第一个会话；日志捕获到 warning。
- SDK：在 `test_arp_background_embedding.py` 里注入 `run_once` 异常，断言会计数、会写日志，泵不停。
- Host：在 `test_native_plane_host.py` 里断言 status 带有 `background` 字段。
- 前端：在 `MissionsView.test.tsx` 里测连续失败 3 次时显示那一行，0 次时不显示。

**风险**
- 泵每 0.05 到 0.25 秒转一圈，日志必须限频。
- 逐项隔离之后，原来靠抛错暴露的缺陷必须真的出现在状态里，不能变成另一种静默。

---

## 8. 条目 7：会话数据占用统计 + 超过 5 GB 在设置页提醒

**用户决定**：会话的聊天数据要一直保存，用于后续审计分析；不做自动回收，不做清理按钮。超过 5 GB 时在设置里提醒用户。

**现状**
- 每个 Agent 在所在池的执行库 `arp_agent_sessions` 表里有一行，另有分区目录 `<userdata>/data/agent-orchestrator/execution-<profile>.db.arp-root/sessions/session-<id>/`（`index.sqlite3` + `marker.json`）。实测 ui-mission-3 一个任务留下 40 个会话目录、70MB。
- 编排库、Assurance 库、SDK 产品状态库都在 `<userdata>/data/` 下（本机现状：`agent-orchestrator/` 2.3M，`simple-harness-sdk/` 41M，`state.db` 8M）。
- 设置页已有"数据目录"一节（`components/SettingsPanel.tsx:930-1100` `DataDirSection`），显示整个数据目录的大小，数字来自 Tauri 的 `get_data_dir_setting`（`src-tauri/src/user_data.rs:205-226` `dir_size_bytes`）。那是整个目录，包含模型文件等，不能直接拿来当"会话数据"的阈值。

**设计**
- 统计范围（"任务与会话数据"）：`<userdata>/data/agent-orchestrator/`（含各池执行库和 `.arp-root` 分区目录）+ `<userdata>/data/simple-harness-sdk/`。实施时核对这两个目录就是编排、Agent 会话、Assurance 的全部落盘位置；有遗漏就补进范围。模型文件、聊天主对话库不算。
- 统计由 Host 后端做，不放前端、不放 Tauri：目录遍历放线程池，不阻塞 socket 循环；结果带时间戳缓存。刷新时机：Host 启动后一次；之后借用 `service.py:527/569` 的周期唤醒，每 30 分钟最多一次；前端主动请求时若缓存超过 10 分钟也刷新。
- 阈值：新配置键 `[orchestration] storage_warn_bytes`，默认 `5 * 1024**3`。只提醒，不阻止任何操作。

**Host 新接口**（`orchestration_` 前缀，走现有转发通道；`handlers.py` 注册，`service.py` 实现）
- `orchestration_storage_get`
  - 请求 payload：`{refresh?: bool}`（true 表示忽略缓存立即重算）
  - 响应 `data`：
```json
{
  "bytes": 3435973836,
  "measured_at_ms": 1790000000000,
  "warn_bytes": 5368709120,
  "over_warn": false,
  "breakdown": [
    {"key": "agent_sessions", "label": "Agent 会话与检索索引", "bytes": 3200000000},
    {"key": "orchestration", "label": "任务编排记录", "bytes": 200000000},
    {"key": "assurance", "label": "质量检查记录", "bytes": 35973836}
  ]
}
```
  - `breakdown` 的 key 固定这三个；实施时按实际目录归类，归不进去的算进最接近的一类。
- `orchestration_status` 的响应里加一个小字段 `storage_over_warn: bool`（取缓存，不触发遍历），供设置页以外的地方以后用；本轮前端只用设置页。

**前端（按"简洁易懂"原则，只改设置页）**
- `SettingsPanel.tsx` 在"数据目录"之后新增一节 **"任务与会话数据"**（新组件 `StorageUsageSection`）：
  - 正常时一行：`任务与会话数据：3.2 GB（含聊天记录、检索索引和检查记录；用于审计，不会自动删除）`，下面三行小字是 breakdown。
  - 超过阈值时在上方加一条醒目提示：`已超过 5 GB。这些数据会一直保留，请到「数据目录」迁移到更大的磁盘。` 后面一个按钮"去数据目录"，点了滚动到 DataDirSection。
  - 一个"重新统计"小按钮，发 `{refresh: true}`；统计中显示"统计中…"。
- 不加任何删除入口。

**测试**
- Host：新增 `backend/tests/orchestration/test_storage_usage.py`：临时目录里造几个文件，断言 `bytes` 和 `breakdown` 之和一致；阈值设成很小时 `over_warn=true`；缓存 10 分钟内不重算、`refresh=true` 重算；遍历走 `run_in_executor`。
- Host：handler 路由测试覆盖 `orchestration_storage_get`。
- 前端：`SettingsPanel.test.tsx` 覆盖正常显示、超阈值提示、"重新统计"发出的消息。
- 真机点击：设置页能看到数字；把阈值临时改小，能看到提醒。

**风险**
- 数据目录很大时遍历慢：只在线程池跑，并且有 30 分钟节流；`refresh=true` 也要节流（10 秒内只允许一次）。
- 目录归类漏掉新落盘位置：测试里断言 `<userdata>/data/` 下所有属于编排/会话/检查的子目录都被覆盖到，新加目录时测试会提醒。

---

## 9. 收尾

1. 7 条都在 worktree 里完成，并跑过各自的定向测试后，做一次**合并前全量回归**（SDK 全套 + Host 后端 + 前端 vitest）。对照 main 上一次全量回归的已知失败清单，只看新增的失败。
2. 由一个独立子代理（opus，只读）做**一轮**核验，只报阻断级问题。核验不超过 2 轮。
3. SDK 版本号改为 `0.13.0.dev20260925+opt.1`（`agent_orchestrator/version.py`、`simple_harness/version.py`），Host 按现有方式钉版。
4. `git merge --no-ff` 合并到 main，推送 origin，然后做**合并后全量回归**。
5. 真机点击一局，用 DeepSeek 并打开思考模式，按现有真机点击流程（**全新的数据目录**——条目 2 不兼容旧绑定、调试版 .app）。检查这些点：
   - 新任务的规划绑定是第 8 版包 / v11；
   - 执行图面板能打开；
   - 任务正常完成；
   - 状态区没有后台错误提示；
   - 设置页“任务与会话数据”一节能看到占用；把阈值临时改小后能看到提醒。
6. 本计划末尾追加一节"实施记录"：每条对应的提交号和测试结果、真机点击证据的位置。证据放在 `.local-test-evidence/2026-09-25/opt/`（不入库）。

## 10. 验收标准（全部满足才算完成）

- 7 条的定向测试全部通过，并且每条都有"改之前会失败"的测试。
- 合并前后两次全量回归都没有新增失败。
- 独立核验没有未关闭的阻断问题。
- 真机点击的 5 个检查点全部通过。
- 代码里没有为旧包版本保留的分支或字节断言（由条目 2 的 grep 清单和测试保证）。

---

## 附录 A：核对后本轮不做的（暂缓清单）

| # | 条目 | 为什么不做 | 以后动它的前提或护栏 |
|---|---|---|---|
| A-1 | 证书一签发就被判"来源已变" | 核对结论：它只写观察记录，不会让正常任务判失败，也不释放资金。23 个库里没有一例因此失败或卡住 | **护栏：绝不能把"证书失效"接到 `goal_resolutions.validity` 列上**，否则会通过 `event_handler.py:14140-14215` → `commit_service.py:5585-5600` 直接判任务失败并释放资金。以后要做"完成前复查依据"，必须先把证书的读集粒度缩小（逐项重读 73 项读集），再补这道门 |
| A-2 | 任务共用一个纪元，并行子任务可能互相导致验收反复重做 | 真实数据里没出现过 | 出现"叶子验收反复重准备"时，再和 A-1 一起做 |
| A-3 | 默认开启早于 12 局验收 | 用户已决定验收后置 | —— |
| A-4 | 每局调用总次数（16/32）不强制；模型场景脚本缺失；18 条继承测试没对应上 | 不影响任务跑通 | —— |
| A-5 | 旧提示词 v7 教错两个操作；旧协议不按任务过滤方法 | 只影响旧协议的老任务，产品默认已经是新协议 | 旧提示词字节冻结，不改 |
| A-6 | 技能目录按档位各存一份 | 产品里 `agent_` 消息到不了后台，现在装不了技能，所以不会发生 | **前提：放行 `agent_` 前缀之前必须先做**，把所有档位改成共用一个目录（owner 库 + 各池只登记挂载） |
| A-7 | 编排 Agent 没按任务范围把关；按用途授权、选择执行环境只在测试里用；请求发出前不复查权限 | 属于权限和治理方面的加强，现有 12 局已经跑通 | —— |
| A-8 | 没有向量模型时悄悄降级 | 保持降级，保证能用 | —— |
| A-9 | 递归燃料不扣；方法准入第 4、5 步一直推后；试用次数不持久 | 有深度和节点数的硬上限兜底 | —— |
| A-10 | 准入里 `running_work` 字段恒为 False | 生产上走的是"先停再对账"的提交阶段，拒绝分支走不到 | 以后顺手删字段或加注释 |
| A-11 | 旧模式回归只覆盖 6/17 个测试目录 | 本轮合并前后跑全量回归时，会顺带覆盖 | —— |
| A-12 | 测试场景或未装层次部署时，任务静默走旧协议；提示词钉版不在包集合里就换成默认 | 前者是测试专用路径；后者是有意设计（给 DAG 模式的钉版不作用于层次模式） | —— |
| A-13 | 21 项变异测试 | 补证据类 | —— |

## 附录 B：子代理挑战记录（2026-09-25）

说明：第 3 版按用户决定重写了条目 2 和条目 7 之后，下表 #1–#5 的问题（历史版本双分支）随单版本设计一起消失，#10–#13（会话销毁）随取消删除一起消失；表保留作记录。

- 挑战者：独立子代理（opus，只读），逐条回到代码核对。
- 结论："修改后可执行"。一共 13 条：阻断 5 条（条目 2 占 4 条，条目 7 占 1 条），重要 8 条。**全部采纳**，已改进正文。

| # | 级别 | 问题 | 处理 |
|---|---|---|---|
| 1 | 阻断 | 包标签 `-v8` 映射到常量，常量改成 8 后老任务会被识别成第 8 版包 | §3 第 3 步：写死 v8→7，新增 v10→8 |
| 2 | 阻断 | 选提示词时按常量选 v10，常量一改，第 7 版包老任务就落到 v7 | §3 第 4 步：显式写 7→v10、8→v11 |
| 3 | 阻断 | 绑定里写死了 V10，新任务会绑成 8/v10 | §3 第 5 步：显式改成 V11 |
| 4 | 阻断 | 准入从包里读已启用集合，第 8 版包只有 `REPAIR`，修复决定会全部被拒 | §3 第 6 步：新增 `internal_enablement_keys` 还原成内部键 |
| 5 | 重要 | 关闭修复时，第 8 版包的 `REPAIR` 漏过滤 | §3 第 7 步 |
| 6 | 重要 | executor_checks 的文档行按白名单构造，加 `requested` 字段会 KeyError | §4 改为只改 deterministic_checks |
| 7 | 重要 | 规划阶段拦截只覆盖旧 DAG 路径，异常名也写错了 | §4 取消规划阶段拦截，主流程靠运行时兜底 |
| 8 | 重要 | status 返回的是 `profiles` 不是 `pools`，Host 也没保存 runtime | §7 第 6 步：在 after_build 里登记 |
| 9 | 重要 | 工具健康探测没隔离；`loop` 取值缺 `reap` | §7 第 3 步，并补上 `reap` |
| 10 | 阻断 | 销毁的 reason 只能是 OWNER_CLOSED/USER_DESTROY；retention_policy_ref 是 Pin 对象 | 第 3 版取消删除功能，作废 |
| 11 | 重要 | destroy 内部已会关闭 Agent；caller 需要受信调用方 | 第 3 版取消删除功能，作废 |
| 12 | 重要 | command_id 带 generation 不幂等；手动清理的结果没地方存 | 第 3 版取消删除功能，作废 |
| 13 | 重要 | missions 表没有结束时间字段 | 第 3 版取消删除功能，作废 |

## 11. 实施记录（2026-09-25）

分支 `opt-0925`（worktree `simple_harness-opt`），从 main `a2b6af2d`（计划提交）拉出。主代理亲手写代码；按用户要求，主流程完成前只做各条最小定向测试。

| 条目 | 提交 | 定向测试 |
|---|---|---|
| 1 执行图消息接通 | `cfec43b3` | Host `test_ws_taskgraph_routing.py` 1 通过 |
| 2 可选决定列表对齐（第 8 版包 + v11） | `72084941` | SDK 13 个相关文件 257 通过；2 个失败为 `plans/llm-native-htn/H0/prompt-digests.json` 不在仓库（main 同样缺，与本轮无关） |
| 3 判据要求的测试必须跑到 | `cd1566f0` | SDK `test_code_test_no_tests_collected.py`（新增裸 `pytest:` 用例）+ `test_executor_check_in_gate.py` 10 通过 |
| 4 自动批准如实记录 / 5 召回超页跳过 | `98a764a9` | SDK `test_check_policy_lossless_mapping.py`（新增 1）+ projector_port 7 通过；`test_arp_index_recall.py` 12 通过；Host `test_assurance_host_api.py` 5 通过 |
| 6 后台错误可见、逐项隔离 | `bc37ddf2` | SDK `tests/agents/test_background_health.py`（新增 2）+ session_lifecycle + background_embedding 17 通过；前端 `MissionsView.test.tsx` 88 通过 |
| 7 存储统计与 5 GB 提醒 | `1cd0…`（见 git log「条目7」） | Host `test_storage_usage.py`（新增 2）+ `test_handlers_contract.py` 12 通过；前端 `SettingsPanel.storage.test.tsx`（新增 2） |
| SDK 版本 / Host 钉版 | `8f7bf477` / `30fb409b` | 钉版后 Host 6 个相关文件 25 通过（含条目 6 的 `background` 断言） |

与计划第 3 版的偏差：
- 条目 2：历史包版本 4/5/6/7 在 `HIERARCHICAL_PLANNER_VERSIONS_BY_PACKAGE` 里作为配对数据保留（只是表项，不是运行分支），运行时的历史分支和标签映射已删；`>= 7` 的 H4 判断保留（对第 8 版包仍成立）。
- 条目 5：没有另写"每页少于 32 项"的构造测试（用户要求主流程前少测试），只跑了现有召回测试；改动只有一处判断。
- 条目 7：统计范围只有 `data/agent-orchestrator/`，不含 `data/simple-harness-sdk/`（那是主对话的执行库，用户确认不算）。

合并前全量回归、独立核验、合并、合并后全量回归、真机点击：见下文续记。

### 11.1 独立核验（2026-09-25，opus 只读，只报阻断）

结论"修后可合"，1 条阻断，已修：

| 问题 | 修法 | 提交 |
|---|---|---|
| 数据目录里只要有一个未结束、绑在第 7 版及更早包上的任务，一进规划就抛 `ContractError`，`_try_planner_intent` 不兜这类错误 → 整个编排循环每轮失败，5 次后 Host 进入 degraded，新任务也建不了 | 新增 `UnsupportedPlanningPackage(ContractError)`；两处抛错改用它；`_try_planner_intent` 捕获后只停这一个任务（`_stop_planning_round(reason="unsupported_planning_package", PLANNING_FAILED)`）；规划回复路径本来就在 `_collect_plan_hierarchical` 的 ContractError 兜底里；`internal_enablement_keys` 遇到历史拼写 `REPAIR/X` 不再抛 `ValueError` 而是这个错误。测试：绑到第 7 版包的任务 `_try_planner_intent` 返回 False 且任务 FAILED | 见 git log「核验修复」；SDK 升 opt.2 并重钉 |

其余 6 条核验为无阻断（请求包↔准入互逆、修复开关同步清空、标签/版本/提示词单点、重放校验、文档任务不受条目 3 影响、批准参数透传与重放身份、后台健康与 status 容错、存储统计不阻塞事件循环、执行图 request_id 匹配）。

### 11.2 合并前全量回归（2026-09-25 下午，代码 `a987e625` 起，最终含测试改写提交）

证据 `.local-test-evidence/2026-09-25/opt/full-regression-premerge/`（不入库）。基线名单取 main `d5ac7f10` 合并后全量回归（`host-post-fail.txt` 153 条 / `sdk-post-fail.txt` 206 条）。

| 部分 | 结果 | 与基线对照 |
|---|---|---|
| 前端 vitest | 885 通过 | — |
| Host 后端 | 7,574 通过 / 146 失败 / 5 错误（35 分 51 秒；跳过已知卡死用例 `test_delayed_old_heartbeat_cannot_renew_after_other_owner_reclaim`） | **新增 0**；基线里 6 条这次通过（含跳过的那条、`htn_jev_focus` 目录、旧版本降级 3 条） |
| SDK | 分两段并行：前 515 文件 7,965 通过 / 217 失败 / 19 错误；后 120 文件 876 通过 / 10 失败（跳过 `test_s2_04`、`test_s2_05_crash_after_result`，均为 `step02/test_recovery_matrix.py` 已知顺序干扰卡死） | 名单新增 55 条：**52 条为环境缺包**（`tiktoken`、`jsonschema`、`pyyaml` 等；装上后 57 个用例全部通过，与交接记录"SDK 主虚拟环境缺 jsonschema"一致）；**3 条是本轮有意的行为变化**——两个测试把任务故意绑到历史包 4/6，按"单一当前版本"口径改写/删除（见提交「测试：按单一当前包口径改写历史包用例」） |

过程记录：第一次全量在跑到一半时我改了 SDK 代码并重装了 Host venv（核验修复），结果混了新旧代码，作废重跑；重跑时 SDK 卡在 `test_s2_05`，中断后没有失败名单，只好分两段再跑一次。教训：全量回归期间不改代码、不动 venv；-q 模式要加 `--junit-xml` 以免中断丢名单。

**结论：合并前全量回归无新引入失败。**

### 11.3 合并后全量回归（2026-09-25 下午，main `0d248ae4`）

证据 `.local-test-evidence/2026-09-25/opt/full-regression-postmerge/`（不入库；main 的两个 venv 已同步到 SDK opt.2 并补装 tiktoken/jsonschema）。

| 部分 | 结果 | 对照 |
|---|---|---|
| Host 后端 | 7,574 通过 / 146 失败 / 5 错误（36 分 33 秒） | 失败集合与合并前完全相同；对 main 基线新增 0 |
| SDK | 前段 8,014 通过 / 163 失败 / 20 错误；后段 876 通过 / 10 失败（跳过两个已知卡死用例） | 对合并前新增 1 条（`p35` 冷恢复里一个杀进程时序的用例），单独重跑通过，属偶发；其余失败全在基线或环境缺包名单里 |
| 前端 | 885 通过 | 第一次跑时应用包构建正在重装 node_modules，3 个文件报模块找不到（记录保留为 `frontend-during-pnpm-install.log`），重跑全过 |

过程记录：SDK 第一次启动因文件清单里还有本轮删掉的测试文件而没跑起来，修正清单后重跑。

**结论：合并后全量回归无新引入失败。**
