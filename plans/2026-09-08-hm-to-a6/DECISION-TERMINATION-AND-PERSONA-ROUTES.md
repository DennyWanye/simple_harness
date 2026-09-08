# 决策：驱动墙钟限额层级 + PERSONA 五路由覆盖

日期：2026-09-08/09。分支 `worktree-termination-persona`（基线 `a6c8b7c7`）。
来源：语料 run-02 复核 `plans/2026-09-07-corpus-c01-local/RUN-C10-02-REVIEW.md` §三.1、
`RUN-REST-02-REVIEW.md` §四.2 提出的两个 Host 缺陷（Q1 / Q2）。
用户授权「不问、自行裁定并记录」，本文即裁定记录。

---

## 一、Q1：`TerminationLimits` 未显式配 `max_wall_seconds`

### 1.1 事实

- `backend/main.py:8226` 原为 `TerminationLimits(max_turns=25, max_tool_calls=50, max_consecutive_same_tool=10)`，
  **不含** `max_wall_seconds` → 静默回落已安装 SDK 默认 `900.0`
  （`simple_harness/runtime/termination.py:134`，
  `.local-test-evidence/2026-09-07/installed-h0710-m0631-s0313`）。
- 语料批次外部 deadline 亦为 900s（`scripts/run_corpus_batch.py` 的 `--seconds` 默认值，
  经 `run_resource_bounded.py` 施加 SIGTERM）。
- 两者**相等** → C10-13 在 900.167s 被外层杀掉，驱动来不及结算，
  连失败终态回执都没有 → `oracle_verdict=NO_PACKET`。
- SDK 只在**预留边界**采样墙钟（`TerminationState.before_provider` /
  `before_tool_batch` → `_check_common`）。已经进入 provider HTTP 调用的 Run
  只能等该调用返回才能发现超预算，所以最坏结算时延 = 一次 provider 传输超时。

### 1.2 裁定

确立一条**显式有序**的限额链，每层必须严格早于其外层，且只有驱动层写终态回执：

| 层 | 值 | 出处 | 语义 |
|---|---|---|---|
| provider 传输超时 | 240s | `deskpet/sdk_adapters/provider.py:1031` | 单次 HTTP 防挂死网 |
| **驱动 `max_wall_seconds`** | **600s** | `config.toml [agent].max_wall_seconds` | 单个 SDK Run 墙钟 → **失败终态回执** |
| 前台活跃执行预算 | 900s | `_chat_turn_timeout_s()`（`chat_turn_timeout_minutes=15`） | Run 授权的**可暂停**活跃时长 |
| 外部看门狗下限 | 900s | `600 + 240 + 60` | 低于此值就会抢在驱动结算之前 SIGTERM |
| 语料批次 `--seconds` | **1200s** | 下限 + 300s 整例余量 | 一个 case 内串行跑多个 Run |

取值理由（trade-off 裁定）：

1. **为什么是 600 而不是保留 900。**
   墙钟必须严格早于**可暂停**的活跃执行预算（900s）才能保证：一个持续执行、
   不等待用户的 Run 一定是被驱动自己掐断（写回执），而不是被 Run 授权过期
   （不写驱动回执）掐断。900=900 就没有这个保证。
   600s 对正常轮次仍宽裕：C10 run-02 trace 里单次 provider 回合 12.7–59.3s，
   多数 <25s；600s 容得下 25 轮预算下的绝大多数真实轮次。
   被 600s 截断的恰好是 C10-13 那种**病理循环**（17 轮全 200 OK 累计 900s，
   其中 11 次是 `tool_search`/`tool_describe`/`tool_activate` 能力发现空转）——
   截断它并留下失败回执，正是想要的行为。
2. **为什么外部 deadline 不是「刚好大于 600」。**
   墙钟只在预留边界采样，所以下限必须是 `600 + 240（一次在途 provider 调用）+ 60（结算余量）= 900`。
   批次默认再加 300s「整例余量」，因为一个语料 case 在**同一个进程**里串行跑
   setup 轮、被评分轮、followup 轮（多个 Run），下限只约束**最后一个** Run。
3. **可配置性。** 走已存在的 `[agent]` 段（`config.raw` 直读，`config.py`
   已把 `[agent]` 登记为 P4 raw 段），不新增 dataclass。
   非法/缺失值回落 600 而不是启动失败——这是运维安全网，
   config.toml 打错字绝不能让驱动变成「完全没有墙钟限额」。
   有效区间 `[300, 3600]`：低于 `240+60` 会让每个 Run 在第一次 provider 调用就超时。

### 1.3 实现

- 新增 `backend/deskpet/execution/termination_budget.py`：纯 stdlib、无重依赖，
  常量 + `resolve_max_wall_seconds()` / `external_deadline_floor_seconds()` /
  `corpus_batch_deadline_seconds()`。`main.py` 启动期读它，
  `scripts/run_corpus_batch.py` 用 `importlib` **按文件路径**加载它
  （不 import `deskpet.execution` 包，避免拉起整个前台运行时），
  从同一组数字派生自己的 deadline——不再各存一份硬编码。
- `main.py` 显式传 `max_wall_seconds=resolve_max_wall_seconds(config.raw)`，
  并打 `react_termination_limits` 日志（四个限额全量可观测）。
- `config.toml [agent]` 新增 `max_wall_seconds = 600` 并写明不变式。
- `run_corpus_batch.py`：`--seconds` 默认改为派生值（当前 1200），
  并在 `main()` 里加**硬校验**——`--seconds <= floor` 直接 `SystemExit`，
  杜绝再次出现「外层先赢」。

### 1.4 测试

`backend/tests/execution/test_termination_budget.py`（18 例），
按 S5b 一致性测试的做法，每一层都从**真实出处**读出来，不复述：

- `test_layer_ordering_invariant` — 240 < 600 < 900 ≤ floor < 批次默认；
  并显式断言 `corpus_batch_deadline_seconds() != 900`（C10-13 的原始形态）。
- `test_provider_transport_timeout_mirror_does_not_drift` — 用 `inspect.signature`
  读 `ProductProviderAdapter.__init__` 的 `timeout` 默认值，防止镜像常量漂移。
- `test_foreground_active_budget_mirror_does_not_drift` — 正则读 `main.py` 的
  `chat_turn_timeout_minutes` 默认值。
- `test_main_passes_an_explicit_wall_budget_to_termination_limits` — **用 `ast`
  解析 `main.py`**，断言每个 `TerminationLimits(...)` 调用都带 `max_wall_seconds`
  关键字。这是缺陷本体的回归钉。
- `test_shipped_config_declares_the_wall_budget_explicitly`、
  `resolve_max_wall_seconds` 的 11 组参数化（缺失/None/字符串/bool/0/NaN/越界钳位）。
- `test_corpus_batch_deadline_strictly_exceeds_the_host_floor`、
  `test_corpus_batch_rejects_a_deadline_that_races_the_driver`（传 `--seconds 900` 必须被拒）。

---

## 二、Q2：PERSONA 未枚举 `context_route` 的五条路由

### 2.1 事实

`context_route` 工具 schema 有五条路由，但 PERSONA 只提到 `memory_standalone`
与 `create_new`，`direct_standalone` / `continue_active` / `resume_existing`
**从未出现**。真实运行里模型因此各自「重新发现」路由面，出现三类错误：

| 现象 | 证据 |
|---|---|
| 纯文本改写被路由成 `create_new` | C10-17（run-02，`gpt-5.6-luna`）；任务未完成判 FAIL |
| 该找旧任务却不调 `task_scope_search` | C05-05 → `memory_standalone`；C05-06 → 零工具调用；C05-15 → `procedure_discover` |
| 已有活跃 scope 却新建重复 scope | native A6 attempt 4 turn 7 |

### 2.2 裁定

在 PERSONA 的 create_new 段之后补一段**极简、契约真实**的路由指引，
措辞逐条对齐工具描述（不发明新契约）：

> Choose the route by what the turn needs. direct_standalone answers the turn as it
> stands: chit-chat, and pure text work on words the user just gave you, such as
> rewriting or renaming them, which is not a new project task. continue_active
> continues this Run's own active task; an active scope needs no search. To find
> earlier work the user names by an old name, a project name or a year, call
> task_scope_search first: typed recall never returns task scopes. Then
> resume_existing with its exact task_scope_id.

契约对齐核对：

- 「an active scope needs no search」直接来自 `task_scope_search` 的工具描述
  「To continue the Run's current active task you do not need this tool at all:
  call context_route(route=continue_active) directly.」
- 「resume_existing with its exact task_scope_id」对齐
  「exact task_scope_id from a confirmed task_scope_search candidate」。
- 「typed recall never returns task scopes」是 S5a 事实：TaskScope 不是 Memory 类型，
  与 PERSONA 既有的「A TaskScope and its lifecycle state are not a stored Procedure」同源。
- 原来那句含糊的「For an existing task, use task_scope_search and the exact returned
  scope with context_route.」被这一段取代（信息被完整吸收且更具体）。

`HISTORY_SUFFIX` / 历史引文封框规则 / `context_page_in` 规则 / 提醒能力尾巴
（`REMINDER_CAPABILITY`）**一字未动**。

### 2.3 **PERSONA 长度是硬约束**（本轮踩到的真坑）

第一版补充写了 774 字符（净 +682），单测跑出真实回归：
`tests/execution/test_current_tool_megabyte.py::…[8192]` 由绿转红
（`planned=5422 > effective=5325`，超 97 token）。
该用例注释明确写着 8192 档「still page and complete」，是被保护的既有性质。
→ 裁定：**PERSONA 增量必须压回预算内**，不允许为了措辞漂亮牺牲小窗口档位。
最终版压到 481 字符（净 +389），PERSONA 总长 **4829 字符**，8192 档恢复绿；
4096 档仍红（基线即红，非本轮引入）。
`test_persona_enumerates_every_context_route_the_tool_accepts` 里钉了
`len(PERSONA) <= 4900` 并注明真正的闸门在 megabyte 用例。

### 2.4 测试

`backend/tests/sdk_adapters/test_context_route_tool.py` 新增
`test_persona_enumerates_every_context_route_the_tool_accepts`：
遍历 `context_route.ROUTES` 断言五个路由名全部出现在 PERSONA 里，
再钉三条行为措辞（`an active scope needs no search` /
`task_scope_search first` / `rewriting` + `not a new project task`）与长度上限。

---

## 三、回放证据（改前 / 改后各自的首轮路由）

方法：从 run-02 证据里取**真实的 provider 首轮请求**
（`observation-trace.json` 的 `providers[0].request_json`，含完整 12 个工具 schema），
只把 system 消息里改动的那一句替换成新 PERSONA 段，其余（trusted clock、
用户原话、温度、工具面）逐字不动，直发中转 `/chat/completions` 读 `tool_calls`。
脚本在 scratchpad，不入库；凭据只从文件读，从不打印。

### 3.1 DeepSeek `deepseek-v4-pro`（`.local-test-evidence/2026-09-07/credentials/deepseek.env`）

跑的是**第一版（长）**补充段，各 4 次：

| 例 | 改前首轮 | 改后首轮 |
|---|---|---|
| C10-17 | `task_scope_search` ×3、`continue_active` ×1 | **`direct_standalone` ×3**、1 次被 2048 max_tokens 截断（`finish=length`，回放脚本产物） |
| C05-05 | `task_scope_search` ×4 | `task_scope_search` ×4（DeepSeek 本来就对，语料里的错是 luna 犯的） |
| C05-06 | `memory_standalone` ×3、`task_scope_search` ×1 | **`task_scope_search` ×3**、`memory_standalone` ×1 |

**该凭据随后额度耗尽（HTTP 402 Insufficient Balance），最终版无法再用 DeepSeek 复跑**；
`.env` 里的 DeepSeek 官方 key 同样 402，CHINZY 那组 base_url 不是 API 端点。

### 3.2 中转 `gpt-5.6-luna`（语料 run-02 的**原始模型**，`.env`）

改用出问题的那个模型直接对照，证据力更强。各 4 次：

两轮：中间版（410 字符）与**最终版（481 字符）**各 4 次，合并计数如下。

| 例 | 改前首轮（8 次） | 改后首轮（最终版 4 次） |
|---|---|---|
| C10-17 | **`create_new` ×2**（复现语料失败）、`continue_active` ×5、`tool_search` ×1 | `continue_active` ×2、`task_scope_search` ×1、直接作答不调工具 ×1；**`create_new` 归零**（中间版 4 次里另出现 `direct_standalone` ×1） |
| C05-05 | `memory_standalone` ×8（复现语料失败） | **`task_scope_search` ×4（4/4）** |
| C05-06 | `memory_standalone` ×5、零工具 ×3 | `memory_standalone` ×1、`direct_standalone` ×1、零工具 ×2 —— **无改善** |

- C10-17 上「直接作答不调工具」不是退化：gold 要的就是把名称改成欢迎语，
  模型直接给出改写文本即完成任务（语料里 C10-17 判 FAIL 的理由正是「任务未完成」）。
  关键收益是 `create_new`（把纯文本改写升格成新建项目任务）在改后 8 次采样中一次都没再出现。
- C05-05 是本轮最干净的修复：4/4 从 `memory_standalone` 翻成 `task_scope_search`。
- C05-06 未修好，`direct_standalone` 还误中 1 次——见 §四遗留 1。

### 3.3 压缩过程中的两条决定性线索

第一轮压缩版（353 字符）为了把 8192 档压回绿，把「a year」与
「typed recall never returns task scopes」一起删了，结果 luna 上
C05-05/06 **完全没有改善**（仍 4/4 `memory_standalone`）。
把这两条线索加回（+50 字符，8192 档仍绿）后，C05-05 立刻变成 4/4 正确。
→ 记录：这两条不是修辞，是这一段里唯一起作用的部分；未来若再压缩 PERSONA，别动它们。

---

## 四、结论、影响面与遗留

- **改动文件**：`backend/main.py`、`backend/deskpet/execution/termination_budget.py`（新）、
  `backend/deskpet/execution/primary_context.py`、`config.toml`、
  `scripts/run_corpus_batch.py`、
  `backend/tests/execution/test_termination_budget.py`（新）、
  `backend/tests/sdk_adapters/test_context_route_tool.py`。
- **回归对照**（同一 venv、逐条 test id 比对，基线为独立 detached worktree `a6c8b7c7`）：
  - `tests/execution`：基线 52 红 → 改后 51 红，**无新增**（少的一条是已知抖动例
    `test_primary_none_routes_to_exact_task_and_writes_real_file[True]`）。
  - `tests/sdk_adapters`（排除 `test_composition.py`）：基线 58 红 → 改后 58 红，**逐条相同**。
  - 新增/改动用例：`test_termination_budget.py` 18 绿；
    `test_context_route_tool.py` 29 绿；`test_corpus_c05_prepare.py` + `test_corpus_supported_case_ids.py` 17 绿。
- **遗留 followup**：
  1. C05 类「该找任务范围却走了别的工具面」在 `gpt-5.6-luna` 上未被 PERSONA 单独修好。
     `RUN-REST-02-REVIEW.md` §四.2 已建议的结构化 `task_scope_hint`
     （对标 C06 的 `procedure_hint`，由 Host 在 `memory_standalone` 结果里回带）
     是更可靠的路径——PERSONA 文本受预算硬约束，不宜再堆字。
  2. `tests/execution/test_current_tool_megabyte.py::…[4096]` 基线即红，未在本轮处理。
  3. PERSONA 的 token 余量已经很薄（8192 档 ratio 接近 1.00）。
     下一次要加句子之前，先跑 megabyte 用例确认预算，或先把小窗口档的
     protected 预算本身重新审一遍。

### 附：本轮操作事故与教训

跑基线对照时用了 `git stash`——**在多 worktree 共享同一 `.git` 的环境里，
stash 栈是全局的**。另一个 agent（extra-type-rate）同时 stash，
我的 `git stash pop` 弹出了**它的**改动并 drop 了它的条目。
已用 `git stash store <dropped-sha>` 把它的 stash 复原、把它的改动从本 worktree 清除、
再从我自己的 stash 提交（含 `^3` 未跟踪父提交）取回本轮工作。
**结论：多 agent 并行时一律用 `git worktree add --detach <base>` 做基线对照，
禁止 `git stash`。** 本文 §四 的基线数据就是用独立 worktree 重跑的。
