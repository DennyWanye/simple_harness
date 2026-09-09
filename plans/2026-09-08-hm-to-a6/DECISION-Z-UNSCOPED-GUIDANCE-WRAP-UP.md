# 事件 Z 决定备忘：`workspace_unscoped` 指引改造 + 预算收尾（wrap-up）

- 日期：2026-09-09
- 分支：`worktree-unscoped-guidance`（自 `main@43a8f835`）
- 证据：`.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-j5yjctfj/`
  （`native.log`、`userdata/data/simple-harness-sdk/execution-v6.sqlite3`
  Run `product-sdk-6ad6a40a2b48306fc97c1f42150bbfb00e98b964e699727ae8a23bb90f073d6d`，
  21 次 provider 调用），对照 `native-a6-run9/primary-ui-8whts2lo/` 第 9 次尝试。

---

## 一、事故复盘（第一手事实，不是转述）

HM-TO-A6 第 10 次尝试第 6 轮，用户说：

> 用 read_file 工具（不要用 shell 命令）一次读出 …/a6-fixture/qiufen-checklist-a.md 的全文，
> 先只告诉我它的标题和总行数。

`run_start_snapshots` 里这个 Run 的 `input.capability_snapshot.tools` 共 35 个，
**含** `write_file` / `edit_file` / `run_shell` / `workspace_prepare` / `process_start`，
**不含** `read_file` / `file_read` / `glob` / `grep` / `list_directory` / `doc_read`。

根因链（全部在 Run 起始一次性冻结）：

1. `ProductForegroundToolPort.freeze`：这个 Run 起始 `task_scope_id is None`
   （模型是在 Run 内才 `context_route continue_active` 的），于是
   `resolution_kind="projectless"`、`primary_route_capable=True`。
2. `filter_sdk_catalog_for_workspace(kind="projectless")` 的豁免只有两条：
   `projectless_admission == "safe"`，或
   `primary_route_capable and name in PROJECT_EFFECT_TOOL_NAMES`。
   写类文件工具全在 `PROJECT_EFFECT_TOOL_NAMES` 里 → 保留；
   读类文件工具是 `requires_project` 且**不在**该表 → 全部被打成 `workspace_unscoped`。
3. 因此这个 Run 里「能写、能跑 shell、不能读文件」。用户偏偏禁止用 shell。

真正致命的是**指引**。`tool_activate builtin:read_file` 被拒后，模型可见文本是：

> tool_activate rejected for builtin:read_file: not activatable in this Run
> (availability_reason=workspace_unscoped). Do not retry tool_activate for it.
> This capability is not bound to the Run workspace… **Use the built-in workspace
> file tools instead (for example builtin:read_file, builtin:list_directory, …)**
> via tool_describe -> tool_activate; project file effects such as
> edit_file/write_file require calling context_route first…

即：**让模型去激活刚被拒的那一个**，且没有一句是这个 Run 里真能执行的动作。
模型随后发了 16 次 `tool_search`（"read_file"、"file_read"、"Read UTF-8 text file …"）
和 8 次 `context_page_in`，最后装配预算爆掉：

```
sdk_context_budget_exceeded planned=28494 effective=26752 protected=7920
tool_schemas=6010 groups=1 ratio=1.65 protected_messages=1910
open_group=20574 full_trim=True
```

→ `react_termination_limits`，整轮丢失，用户一个字都没拿到。

**订正上游转述**：第 9 次尝试并不是「先 workspace_prepare 后 read_file 就成了」。
run9 的每一个 Run 的 `capability_snapshot` 里都没有 `read_file`；turn 6 的 Run
`product-sdk-fd4b0849…` 确实激活并调用了 `workspace_prepare`，但**从没调用过
read_file**。`workspace_prepare` 的 handler 只是 `workspace.mkdir()`，不改投影，
投影在 Run 起始冻结、Run 内无法变宽。所以「先 workspace_prepare 再激活 read_file」
这句话在本事故形态下是**假的**，不能写进模型可见文本。

---

## 二、决定 1：指引按 Run 自身冻结事实分支，绝不自指

改在 `backend/deskpet/sdk_adapters/tool_authority.py`：
`unavailable_capability_next_action(reason, *, capability_id, facts)`，
`facts` 是新的 `RunAvailabilityFacts(routed, workspace_bound, exposed_tool_names)`，
由 `SdkRuntimeCapabilityBridgeAdapter._availability_facts()` 从
`SdkRunToolAuthorityRegistry.resolve(run_id)` 现取（`task_work_context` +
`authority.specs`）。四条分支：

| 分支 | 条件 | 模型可见下一步 |
| --- | --- | --- |
| 同类替代 | 本 Run 还暴露着**同类**（读↔读、写↔写）文件工具 | 点名那几个 capability_id，走 `tool_describe -> tool_activate`；写类附「project effect 需先 context_route」 |
| 准备工作区 | 已绑定 TaskScope 但工作区根未准备 | 先 `tool_describe`/`tool_activate` `builtin:workspace_prepare`，**无参数调用一次**，再激活所需文件工具；若它也被拒 → 停手并告知用户此任务没有绑定项目目录 |
| 未绑定任务（事件 Z 本形态） | Run 起始未绑定 TaskScope | 说明投影在 Run 起始冻结、本 Run 内不会变；调用一次 `context_route`（create_new / resume_existing / continue_active），文件工具**从本会话的下一个 Run 起**可激活；本轮不要再 `tool_activate`、不要再 `tool_search`，直接用手上已有的信息回答并说明缺什么 |
| 不可达 | 其余 | 明确说不可达、停止搜索、直接作答 |

硬约束（有测试钉住）：

- `next_action` 里**永远不会出现被拒的那个 capability_id**（事件 Z 的自指句删除）。
- **读被拒时绝不推荐写工具**（`alternatives_for` 按读/写同类取交集，MCP 这种无产品类别的才取全集）。
- 「未绑定 / 不可达」两支显式写 `do not call tool_search`，直接关掉事件 Z 的 16 次搜索循环。

同一条文案同时供三面使用，模型翻到哪一面都读不到自指句：

- `tool_activate` 拒绝回执（`deskpet/tools/tool_search.py::_activation_rejection`，
  新增可选 `service` 形参，通过鸭子类型 `service.unavailable_next_action` 取 Run 感知文案；
  旧 `ToolCapabilityBridgeService` 没有这个钩子，自动退回 Run 无关文本，不破坏既有契约）。
- `tool_search` 每条命中的 `next_action` + 页级 `next_action`。
- `tool_describe` 的 `next_action`。

## 三、决定 2：`workspace_prepare` **不**做隐式/自动激活

问过目录契约后的结论：**不做，只改指引**。三条理由：

1. 它治不了本事故。`workspace_prepare` 只 `mkdir` 已绑定的 `context.workspace`，
   不改 Run 起始冻结的投影；读类工具被裁掉的原因是 `projectless_admission`，
   不是目录不存在。自动激活它，`read_file` 照样激活不了。
2. 它是 `PROJECT_EFFECT`（route/TaskScope 双 REQUIRED）。冻结 SDK 的激活链是
   `describe(nonce, capability_hash) -> activate` 三段哈希，隐式激活等于 Host 自造
   一次没有 describe 回执的激活，动的是设计冻结 §1/§7 的授权面，不是文案面。
3. 真正的不对称是「写类有 `primary_route_capable` 逃生门、读类没有」。给读类开同样的
   逃生门**不安全**：写类之所以可以先暴露后拒，是因为调用时还有 SDK react barrier +
   Host TaskExecutionEnvelope + EffectGate 三道闸；读类没有等价的调用时闸门，暴露即等于
   在没有工作区根的情况下读任意路径。

→ 记为 followup **F-Z1**：`projectless + primary_route_capable` 的 Run 里读类文件工具
永远不可用，而 `run_shell`/`write_file` 可用——这个不对称本身要么补一道读类调用时闸门
（读也走 TaskExecutionEnvelope，路径必须在绑定根内），要么在产品层面承认「未绑定任务
的会话不能读本地文件」并让指引把话说死（本轮采后者）。属授权面改动，不在本轮范围。

## 四、决定 3：预算收尾（wrap-up），第 5 级降级

改在 `backend/deskpet/sdk_adapters/context_authority.py`。

**触发规则**：只在既有降级链已经走到「强制分页 + 历史裁到 0 组」之后判定。
先用 `raise_on_overflow=False` 探一次，看 `budget_headroom`：

- `headroom >= 1200 token` → 与今天完全一致，正常出请求。
- `headroom < 1200 token`（含事件 Z 那种负余量）→ 注入收尾指令，**每个 Run 只注入一次**。
- 已经注入过一次 → 走原路，按今天的样子 `ContextBudgetExceeded` 失败关闭。
  这就是「模型收到收尾指令后仍然调工具 → 照今天失败」的落法。

**阈值 1200 的来历**（全部来自事件 Z 那条日志）：`open_group=20574` token 由 45 条组成
（20 条 assistant 回声 20.5 KB + 17 条 descriptor 9.6 KB + 8 条省略通知 5.3 KB），
约 457 token/条；一个 react 步 = 一次回声 + 它产生的结果 ≈ 915 token。
余量不足一个步，下一轮在物理上就不可能存在，所以那里就是该停的地方。
1200 是它上取整，并顺带覆盖收尾指令自身约 120 token 的开销。

**注入内容**（`CONTEXT_BUDGET_WRAP_UP_ID = "context_budget_wrap_up"`）：一条
Host 权威 SYSTEM 消息，形状与语义闭包指令一致（canonical JSON body +
`metadata={"source": "context_budget", "trust": "host_authority"}`），内容逐字节确定、
与 Run/时间无关，因此进入 `provider_request_fingerprint` 后重放性质不变。文案要点：
本 Run 已无预算再走一步工具，立刻停止调用任何工具（含 tool_search / tool_describe /
tool_activate / context_page_in），用手上已有的信息直接作答并说明哪一部分没做成、为什么。

**第 5 级降级动作**：`_plan_turn_messages(..., wrap_up_message=...)` 把收尾指令并入
protected，并解锁一次常规路径绝不允许的裁剪——把 open group 自己的因果链交出去，
**只保留该组开头的 USER 消息**。这样请求仍然因果合法（不会出现没有 tool 结果的
assistant tool_calls），用户这一轮问的话还在，交出去的是那些让本轮走不下去的
回声 / descriptor / 省略通知。回执（`source_revisions`）新增
`wrap_up_injected` 与 `open_group_items_dropped` 两个字段，另有
`sdk_context_budget_wrap_up run=… turn=… headroom=… threshold=… open_group_items_dropped=…`
一条 warning 日志。

**与 react 限额的配合**（事件 Q：`max_turns=25`、`max_tool_calls=50`、
`max_wall_seconds=600`）：收尾由装配预算触发，必然发生在预算真正耗尽的那一轮——
事件 Z 是第 21 轮、第 66 秒，远在 25 轮 / 600 秒之前；收尾后的请求只剩 protected +
用户消息 + 一条指令，必定装得下，所以「再走一轮把答案说出来」这件事一定在限额内完成。
限额仍是外层兜底：模型若无视指令继续调工具，第二轮走原路失败关闭，最坏情况仍被 25 轮 /
600 秒收住。

---

## 五、测试

新增：

- `backend/tests/sdk_adapters/test_unscoped_guidance_incident_z.py`（7 例）——
  用生产部件复现事件 Z 的 Run 形态（projectless + `primary_route_capable`，
  读类被裁、写类保留），钉住：自指句消失、未绑定分支点名 `context_route` 且说明
  「本 Run 投影已冻结、下一个 Run 才生效」、显式关掉 `tool_search` 循环、
  读被拒不推写工具、`tool_search` 命中与页级提示、`tool_describe` 提示。
- `backend/tests/execution/test_context_budget_wrap_up.py`（5 例）——
  用真估算器 + 真冻结预算（window 32000 → effective 26752，flash ratio 1.65）复现 T6 组成
  （20 回声 + 17 descriptor + 8 通知 = 一个不可裁的 open group），把受保护前缀标定到
  事件 Z 的 1742 token 超额。main 上是 raise（`wrap_up_message` 形参根本不存在 →
  红）；修复后注入收尾指令即装得下、用户消息仍在、因果链整条交出、指令确定且进请求指纹、
  无压力轮不受影响、阈值与「每 Run 一次」规则。

修改（契约变更，非假绿）：

- `backend/tests/execution/test_current_tool_pages.py` /
  `test_current_tool_megabyte.py` 的 4096 档：原来钉的是「Run 以
  `sdk_context_budget_exceeded` 失败关闭、用户什么也拿不到」。收尾路径生效后这一档
  **完成**并给出一句「做不了什么、为什么」的回答；断言改为记录 wrap-up 而不是 raise，
  「没有写批次、没有 TaskScope、没有闭包回执」这些保证一条不少。

结果（`backend/.venv`，一次一个 pytest 进程，`-p no:randomly`）：

| 集合 | 结果 |
| --- | --- |
| 点名集合（control_result_bound / descriptor_cost_bound / 两个新文件 / tool_activate_unavailable_disclosure / tool_authority / token_estimator_calibration / global_descriptor_authorization / dynamic_mcp_projection） | 142 passed |
| `tests/sdk_adapters`（排除 `test_composition.py`） | 58 failed / 703 passed，与 main@43a8f835 的 FAILED 集合**逐行相同**（全部先存红） |
| `tests/execution` | 分支 40 failed / 268 passed / 10 errors；main 42 failed / 261 passed / 10 errors。差集只有两条，且方向是**由红转绿**：`test_current_tool_megabyte.py::…[4096]`、`test_primary_create_new_runtime.py::test_first_tool_waits_for_durable_host_started_without_provider_delay[False]`。无新增红。 |

---

## 六、遗留

- **F-Z1**（见 §三）：`projectless + primary_route_capable` Run 里「能写不能读」的不对称。
- 收尾「每 Run 一次」的记号是进程内 `set`，重启会丢——方向是安全的（最多多收尾一次），
  且指令本身确定、随持久化请求一起走，不影响重放。若将来要跨重启严格一次，需要从
  快照回执把 `wrap_up_injected` 读回来。
- 本轮只做静态与单测验证，未跑原生旅程（依约束不启动原生应用）。事件 Z 的真人复验
  需要再跑一次 HM-TO-A6 第 6 轮。
