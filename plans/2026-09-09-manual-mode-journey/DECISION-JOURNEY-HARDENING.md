# 裁定：旅程加固三件（Manual 驱动硬判据 / F-MMD-1 注册白名单 / F-NC1 自改写路由）

- 日期：2026-09-09
- 分支：`worktree-journey-hardening`（工作树 `.claude/worktrees/journey-hardening`，基线 `43a8f835`，**不合并**）
- 上游：`plans/2026-09-09-manual-mode-journey/DECISION-MM-D1-D2.md`「旅程驱动与计划必须改的地方」、
  `plans/2026-09-08-hm-to-a6/00-PLAN.md` NC-1、
  `plans/2026-09-08-hm-to-a6/DECISION-TERMINATION-AND-PERSONA-ROUTES.md` §二
- 三件事彼此独立，没有共享代码路径；放在同一分支只是为了一次交付。
- 本轮**没有启动原生应用**，也没有跑真实模型；全部结论来自静态追踪与单元用例。

---

## 一、Manual 模式旅程驱动加固（MM-D2 的第 2/3/4/5 条）

### 1.1 问题

run3 整趟 15 轮跑完，才发现底部「项目目录授权」卡片里的 `允许本次绑定` **一次都没有被答成**
（`task_grants(source='user')=94`，而 `task_workspace_manual_decisions=0`、
`binding_grants(source='manual')=0`）。原因不是计数器缺失——`manual_decisions_allow` /
`binding_grants_manual` 全程都在记，一直是 0——而是**驱动没有把它们当判据**，
只看 `last_run_state` 与 `task_grants`，于是「用户全程只答了工具卡」这件事被一路带到终点。

### 1.2 裁定与实现（`scripts/native/manual_driver.sh`）

| 项 | 改动 |
|---|---|
| (a) 硬判据 | 新增 `BINDING_TURNS=" 4 7 8 "` 与 `is_binding_turn`。这三轮开始前记 `BASE_DEC_ALLOW`/`BASE_GRANT_MANUAL`，轮末重取计数：`manual_decisions_allow` 未增 → 该轮 `outcome=failed`、note `binding_not_decided`；已增而 `binding_grants_manual` 未增 → note `binding_not_granted`。两者都**写完 progress 行后立即 `exit 3`**，不再往下跑 |
| (b) challenge_ref | 新增 `print_binding_challenges <since> <label>` / `await_binding_challenge`：从 `state.db.context_route_tool_invocations` 取 `verdict='rejected'` 且 `json_extract(detail_json,'$.code')='context_route_binding_authorization_required'` 的行，把 `json_extract(detail_json,'$.binding_challenge.challenge_ref')` 连同 `effect_id`/`sdk_run_id` 打进驱动日志。绑定轮在**提示真人应答之前**最多等 30 s 让挑战落库先打一次，轮末再打一次 |
| (c) 超时 | `A6_TIMEOUT` 默认 **360 → 600**。理由见 MM-D2 第 4 条：两张卡串起来的实际应答窗口超过 360 s。另按同条要求，**超时前先打一次** `manual_decisions_allow` / `binding_grants_manual` 计数快照，用来区分「卡没答」与「答了但 Run 没恢复」 |
| (d) 操作提示 | 绑定轮在 `read` 之前显式打印「本轮同时出现两张卡，两张都要答，各自 300 s 独立计时」 |

**两个 reason code 而不是一个**：MM-D2 只点名了 `binding_not_decided`，但绑定卡有一个真实的
中间态 `allow_recorded`（「允许已记录，目录绑定尚未完成」，按钮改名为 `重试完成已允许的绑定`）。
决定已落库而 grant 未落库时把它也报成 `binding_not_decided` 会指错方向，因此分成
`binding_not_decided`（决定都没有）与 `binding_not_granted`（决定有、绑定没完成，去点重试按钮）。
两者都是硬失败、都立即停。

**没有做**：`send_failed` 分支仍是「记录并继续」。它是另一类失败（Run 根本没起来），
与「卡没答」不是同一件事，本轮不改其语义。

### 1.3 `00-PLAN.md`

- T4/T7/T8 三行：操作列写明**同时出现两张卡、两张都要答**（底部 `允许本次绑定` + 弹窗 `允许一次`，
  各自 300 s 独立窗口，先答哪张都行），PASS 列标注 **硬判据**；T7 补一句列目录的工具卡可能再弹一次。
- §3 新增 4.1 / 4.2 两条：4.1 用一张表把「绑定卡 vs 工具卡」的位置、按钮、落库表并排列出，
  写明 **Manual 模式下工具卡按工具调用逐次重弹（每轮可能不止一次），绑定卡是一效应一张**；
  4.2 写明 T4/T7/T8 是硬判据轮、失败 reason code 与 `exit 3`、重跑用 `--start`。
  §3 第 4 条同时记下 600 s 与超时前的计数快照。
- 顺带更正 §0.1 / §1 MM-1 / 抬头证据清单 / §5 归档清单里「策略表在 `sdk-product-state.db`」的旧说法
  —— MM-D1 已裁定唯一权威是 `workflow.db.authorization_policy_state`，
  A6 尝试 5 当时读到的就是那条 DDL 残留种子行。

### 1.4 `scripts/native/manual_verify.py`

校验器的 `policy_state()` 原本从 `sdk-product-state.db` 读 `authorization_policy_state`，
与驱动犯的是同一个错（run3 记录失真的根因）。现改为：

- `Evidence` 新挂 `workflow.db`（`self.workflow`），`paths` 里加一项（SHA-256 归档也随之覆盖它）；
- `policy_state()` 只读 `workflow.db`，缺表/列直接 `SchemaMissing`，**绝不回落**读残留表
  —— 那张表恒为 `auto/0/factory_default`，回落会把 MM-1 的 FAIL 洗成 INCONCLUSIVE 甚至 PASS；
- `task_grants` / `authorization_sagas` 仍读 `sdk-product-state.db`（它们本来就在那）。

**自检形状同时被加强**：`--selftest` 现在建**两个**都有 `authorization_policy_state` 的库，
内容相反——`workflow.db` 是真轨迹（`auto/2/user_explicit/receipt-abc`），
`sdk-product-state.db` 是残留种子行（`auto/0/factory_default/NULL`）。
校验器一旦读错库，MM-1 会立刻因 `generation<2` 退化成 INCONCLUSIVE，自检就红。
`--selftest` 实测 **16/16 PASS**。

驱动本身没有自检入口（只有 `bash -n`）；`bash -n` 通过，新增的两条 SQL 在临时 sqlite 上实测能
正确取出 `challenge_ref`。

---

## 二、F-MMD-1：`main.py` 的 service 注册名必须在白名单里

### 2.1 问题形状

`ServiceContext.register()` 对白名单外的名字抛 `ValueError`，而 lifespan 的启动块外面包着
`try/except` → 漏登记只表现为一条 boot warning，真正的症状要到运行期 `service_context.get(...)`
才炸（`context.py:48` 的 `session_goal_store`、`:57` 的 `context_compressor` 两条注释已经记过两次
同样的事故）。MM-D2 新增 `memory_display_invalidation` 时又走了同一条路径。

### 2.2 用例（新增 `backend/tests/test_main_service_registrations.py`，4 例全绿）

用 `ast` 解析 `backend/main.py` 源码（**不 import** —— 导入会拉起整个 FastAPI 应用），
取所有 `service_context.register(<name>, …)` 的第一个实参。`context.py` 里注册入口只有
`register()`（没有 `set()`），并另有一条用例用 AST 读 `ServiceContext` 的公开方法名，
防止将来新增写入口后本文件的匹配静默失效。

名字来源三种，三种都必须能静态解析，否则用例直接失败（动态名字会绕过这道防线）：

1. 字符串字面量（绝大多数）；
2. `names = ("a", …)` + `for name in names: register(name, None)`
   （`_activate_human_memory_host_ports` 的 4 个 `human_memory_*`）；
3. `for k, v in <字面量 dict>.items(): register(k, …)`
   （companion growth 的 5 个 `Unavailable…` 占位服务）。

`tool_registry.register(...)` 这类同名方法按接收者名过滤掉。

| 用例 | 钉住什么 |
|---|---|
| `test_main_registers_a_meaningful_number_of_services` | 解析结果 ≥80 个名字，防止解析规则失效后下条断言因空集合假绿 |
| `test_every_registered_service_is_whitelisted` | **缺陷本体**：注册名 ⊆ `_VALID_SERVICES` |
| `test_the_mm_d2_display_invalidation_slot_is_actually_covered` | 自证：`memory_display_invalidation` 确实走在被检查的路径上 |
| `test_registration_entrypoint_name_has_not_drifted` | `ServiceContext` 的写入口仍只有 `register` |

**反向验证**：把 `memory_display_invalidation` 从 `_VALID_SERVICES` 临时删掉，
`test_every_registered_service_is_whitelisted` 与自证用例**双双由绿转红**；恢复后 4 例全绿
（`git diff` 对 `backend/context.py` 为空，确认已完整还原）。

### 2.3 明确没做

原本还写了一条「白名单里每个名字都要有对应的 dataclass 槽位」，实测 `context_compressor` /
`image_worker` / `memory_display_invalidation` 三个都没有槽位——但 `register()` 走 `setattr`、
`get()`/`snapshot()` 走 `getattr(..., None)`，缺槽位无害，属既有形状，不在本轮范围，故删掉该用例，
不引入一条与本轮无关的红。

---

## 三、F-NC1：改写用户自己上一句话不得触发 context 工具

### 3.1 问题

HM-TO-A6 的 NC-1 要求 T3「把我上一句话改得更简洁一点。」以 `context_route_decisions.origin='no_recall'`
作答（不查长期库），但尝试 9 / 10 里 `deepseek-v4-flash` 仍然调了 `context_route`，
origin 变成 `context_tool`，负控不成立。

原 PERSONA 的路由段只说了这类改写「不是新建项目任务」
（`direct_standalone answers the turn as it stands: … such as rewriting or renaming them,
which is not a new project task.`）——那是 `DECISION-TERMINATION-AND-PERSONA-ROUTES.md` §二
为了把 C10-17 从 `create_new` 拉回 `direct_standalone` 而写的，**它把模型从"错的工具调用"
引到了"对的工具调用"，但从没说过这一步根本不需要调工具**。待改写的文本本来就在当前上下文里。

### 3.2 裁定：新增的**一句**（逐字）

```
Rewriting or shortening the user's own words needs no context tool or recall.
```

位置：紧跟 `… which is not a new project task. ` 之后、`continue_active …` 之前，
即和既有 `direct_standalone` 指引同段，作为它的收敛（先说"不是新建任务"，再说"连工具都不用调"）。
措辞选择：`shortening` 覆盖 NC-1 的"更简洁"，`the user's own words` 限定为**用户自己刚说过的话**
（不涉及存储事实/偏好/旧任务），`no context tool or recall` 同时关掉工具面和召回面
—— 观察到的缺陷正是 `context_route` 调用把 origin 从 `no_recall` 变成 `context_tool`。

**PERSONA 被哈希进回执**，故本次改动逐字记录在此。PERSONA 全长 **4419 → 4482 字符**。

### 3.3 为它腾出的 token 预算（必须记）

`DECISION-TERMINATION-AND-PERSONA-ROUTES.md` §2.3 已经把「PERSONA 长度是硬约束」写成裁定：
真正的闸门是 `tests/sdk_adapters/test_token_estimator_calibration.py::
test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`
（`tests/sdk_adapters/test_context_route_tool.py` 里的 `len(PERSONA) <= 4900` 只是副闸）。

改动前实测该档余量只有 **19 token（可加 77 个 ASCII 字符，实测探针）**，而新句连句尾空格共 **78 字符**，
正好比余量多 1 个字符。因此同时做了一处**同义压缩**换出余量：

```
- each candidate, picks no side, and keeps a contested value out of fragments,
+ each candidate, picks no side, and keeps it out of fragments,
```

`it` 指代同句开头的 `means that value is contested` 的 that value，语义零损失；
`conflict_notice` 段被用例钉住的四处措辞（`A conflict_notice on any context_route result`、
`conflict_notice`、`contested`、`ask the user which one applies`、
`including a correction the user just gave`）**一字未动**。

结果：8192 档 `planned 5306 → 5322`，`effective 5325`，**余量 19 → 3 token**。
`REMINDER_CAPABILITY` / `HISTORY_SUFFIX` / `context_page_in` 规则 / 引文封框规则未动。

> 给下一个要改 PERSONA 的人：余量只剩 3 token。加任何一句之前先跑上面那条 calibration 用例，
> 并且**不要动** `a year` 与 `typed recall never returns task scopes`
> （§三.3 已证明这两条是路由段里唯一起作用的部分）。

### 3.4 用例

- `tests/sdk_adapters/test_context_route_tool.py` 新增
  `test_persona_tells_the_model_a_self_rewrite_needs_no_context_tool`：逐字钉住新句，
  并断言与既有 `not a new project task` 同段共存。**34 → 35 例全绿**。
- `tests/sdk_adapters/test_token_estimator_calibration.py`：**54 例全绿**（含 8192 档闸门）。
- `tests/sdk_adapters/test_contested_route_guard.py`：**32 例全绿**（压缩过的那句所在段）。
- `tests/execution/test_current_tool_megabyte.py`：3 例中 **8192 档绿、4096 档红**。
  4096 档**基线即红**——已用 `git checkout` 把 `primary_context.py` 还原到 `43a8f835` 后
  单独重跑该参数化确认，改动前后同红，非本轮引入
  （与 `DECISION-TERMINATION-AND-PERSONA-ROUTES.md` §四遗留 2、F-C04-1 记录一致）。

### 3.5 没做

- 没有改 `context_route` 的 schema / ROUTES / 错误码，也没有加 Host 侧的拦截
  —— NC-1 是模型行为负控，本轮只给一句有界的文本指引。
- 没有真实模型回放验证（本轮不启动原生应用、不跑 provider）。
  **下一次 A6 跑之前不得声称 NC-1 已修**，需要在 flash 上重跑 T3 看 `origin`。

---

## 四、改动文件

| 文件 | 件 |
|---|---|
| `scripts/native/manual_driver.sh` | 一 |
| `scripts/native/manual_verify.py` | 一 |
| `plans/2026-09-09-manual-mode-journey/00-PLAN.md` | 一 |
| `backend/tests/test_main_service_registrations.py`（新） | 二 |
| `backend/deskpet/execution/primary_context.py` | 三 |
| `backend/tests/sdk_adapters/test_context_route_tool.py` | 三 |
| `ARCHITECTURE/PROJECT_STATUS.md`、本文（新） | 记录 |

用例合计：新增 4（F-MMD-1）+ 1（F-NC1 钉字）= **5 例**；
回归 `test_context_route_tool`(35) + `test_token_estimator_calibration`(54) +
`test_contested_route_guard`(32) + `test_current_tool_megabyte`(2 绿 / 1 既有红) +
`manual_verify.py --selftest`(16/16) 。
