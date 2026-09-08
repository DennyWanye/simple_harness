# 裁定 V：争议披露只挂在 memory_standalone 上，模型不问就永远不知道

- 日期：2026-09-09
- 工作树：`worktree-contest-notice`（分支 `worktree-contest-notice`，基线 `34dbc82a`）
- 事故：HM-TO-A6 第 9 次原生旅程（`deepseek-v4-flash`），**A6-8 后半 / NC-4 FAIL**
- 证据：`.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/`
- 装机 SDK：`simple-harness-memory-sdk 0.6.34`

---

## 1. 现象

T20 「更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。」→ 记忆
`proofreading_script_python_version` 前进到 revision 2（`amends`，值 `Python 3.13`）。
T21 「不过我印象里上周好像还是按 3.12 在跑的，你说呢？」→ 分析车道产出
`contest_semantic`，落库 revision 3（值 `3.12`，`conflict_status=contested`）＋
1 条 `cognitive_conflict_groups`。T22 「那你现在按哪个版本执行这套校对流程？」
得到 「按 Python 3.13 执行」，**没有 `conflict_notice`，没有要求确认**。
T23/T24 之后的人工重发同样答 3.13。

事件 O（`DECISION-CONTESTED-DISCLOSURE.md`）已经修过一次同名现象，尝试 5 通过；
本轮又 FAIL，所以必须先证明 O 的修复是不是失效了，而不是直接再改一遍。

---

## 2. 逐行取证（全部来自证据库，未做任何假设）

### 2.1 争议状态本身完全正常

`userdata/data/human_memory_v7.db`：

| 表 | 行 |
| --- | --- |
| `cognitive_memory_heads` | `cognitive-memory-84b96e0b…7ee5bf12`，`memory_type=semantic`，`current_revision=3`，`updated_at=1788905435.4341` |
| `cognitive_memory_revisions` r1 | `Python 3.12`，`uncontested`，`created_at=1788902150.9132` |
| `cognitive_memory_revisions` r2 | `Python 3.13`，`uncontested`，plan `host-analysis-plan-da69148c…`，op `op-revise-python-version`，`created_at=1788905260.85026`（06:07:40） |
| `cognitive_memory_revisions` r3 | `3.12`，**`conflict_status=contested`**，plan `host-analysis-plan-767e5204…`，`created_at=1788905435.4341`（06:10:35） |
| `cognitive_conflict_groups` | 1 行，`group_id=cognitive-conflict-group-cd9b2beb…51fe7b5`，`incumbent_revision=2`，`challenger_revision=3`，`created_at=1788905435.4341` |
| `cognitive_conflict_members` | 恰好 2 行（incumbent r2 / challenger r3），content_hash 与 revision 表逐字一致 |
| `cognitive_conflict_resolutions` | **0 行**（争议未裁决） |

三条 revision 的 `predicate` 都是 `proofreading_script_python_version`、
`qualifiers=["在做资料校对时"]`——**假设（2)「争议挂在别的血缘上」不成立**，
争议正挂在 T20 更正产生的那条 head 上。

### 2.2 T22 根本没有查过记忆

`userdata/data/state.db::context_route_tool_invocations` 全表 25 行，
按 `recorded_at` 排序后 **最后一次 `memory_standalone` 在 06:09:07（T21）**，
争议 06:10:35 落库之后只剩两行，都是 `direct_standalone`：

```
1788905502.0276  accepted  {"route":"direct_standalone","task_scope_id":null}   ← T22，06:11:42
1788905709.5397  accepted  {"route":"direct_standalone","task_scope_id":null}   ← 人工重发，06:15:09
```

对应 `simple-harness-sdk/execution-v6.sqlite3::execution_effects` 的效果
`effect-2abc513fe928b18113a7de4e66a4c47e85203376749d25fb309ddd514cb5aaf1`
（`prepared_at=1788905502.03`，`state=succeeded`），模型传的参数逐字是：

```json
{"route": "direct_standalone",
 "query": "用户问现在按哪个 Python 版本执行秋分资料整理的校对流程，依据本请求中刚给出的更正（统一用 3.13，不是 3.12）直接回答。"}
```

结果体只有 `context_route_receipt` 一个键：没有 `fragments`、没有
`conflict_notice`；`detail_json` 也只有 `{"route","task_scope_id"}`，没有
`recall_conflict`。

**模型自己写明了「依据本请求中刚给出的更正」**——它认为答案就在当前上下文里
（T20 的更正确实在对话正文里），于是选了「按现状作答、不查记忆」的路由。

### 2.3 时序不是原因

`cognitive_conflict_groups.created_at = 1788905435.4341`，
T22 路由效果 `prepared_at = 1788905502.0276`。争议比这一轮**早 66.6 秒**落库，
`a6-driver.log` 也显示驱动会等分析批次结束再发下一轮——**假设（4）不成立**。

### 2.4 SDK 0.6.34 的短路契约没有变（假设（3）不成立）

把证据库连同 `-wal`/`-shm` 复制出来，用装机 SDK 0.6.34 离线重放
`HumanMemoryV7Runtime.typed_recall`，时钟钉在 T22 的 `1788905502.0`：

第一次重放 4 条查询全部 `confirmation_groups=0`，看起来像 SDK 回归。
插桩 `_collect_typed_recall_confirmation` 后定位到真因：
`_resolve_suppression_unlocked` 对 incumbent 返回
`denied=True, directive_ids=('suppression-directive-1dba5ee79ab…',)`。
该指令是 **T23 在 UI 上的遗忘**，`suppression_directives.effective_at =
1788905616.85202`（06:13:36），比 T22 晚 115 秒——归档快照里的它污染了重放。

把这条晚于 T22 的指令剔除、还原 T22 当时的状态后重放（同一 SDK、同一库）：

| 查询 | outcome | items | confirmation_groups | Host 通知 |
| --- | --- | --- | --- | --- |
| T22 逐字原文（含「3.13/3.12」） | `needs_user_confirmation` | 0 | **1** | 完整通知，incumbent r2 `Python 3.13` / challenger r3 `3.12` |
| 「秋分资料整理校对流程所用的 Python 环境」 | `needs_user_confirmation` | 0 | **1** | 完整通知 |
| 「proofreading_script_python_version」 | `needs_user_confirmation` | 0 | **1** | 完整通知 |
| 「现在按哪个版本执行这套校对流程」（纯中文，不点名取值/谓词） | `recall` | 1 | 0 | 无 |
| 「今天天气不错，随便聊聊」（无关轮） | `recall` | 0 | 0 | 无 |

槽位文本插桩输出：

```
{"object_value":["Python 3.13","3.12"]}
{"predicate":"proofreading_script_python_version"}
```

结论：**0.6.34 的 contested 短路与 Host 的 `project_contested_confirmation`
都是好的**。只要 T22 走 `memory_standalone`，事件 O 的通知会原样出现。

---

## 3. 根因

> Host 的争议披露只写在 `context_route._memory_standalone` 里。
> 但「会不会拿争议值去执行」取决于**这一轮依赖不依赖那个值**，
> 而不取决于模型选了哪条路由。
> T22 选了 `direct_standalone`（契约上「no Memory query」），
> Host 于是一次都没问过记忆，66 秒前刚落库的未裁决冲突组全程无人查看，
> 争议值以「刚才对话里的事实」形态直达模型。

事件 O 修的是「问了，但投影丢了」；事件 V 是「压根没问」。
契约 HM-S3 「含糊时不选边，**依赖该值的任务**要求确认」的主语是任务，不是路由。

---

## 4. 修复

### 4.1 每条提交型路由都过同一道争议闸（`sdk_adapters/context_route.py`）

`handle_context_route` 在参数校验之后、**任何路由副作用之前**，对
`memory_standalone` **以外**的每条路由调用新增的 `_contested_notice()`，
结果以 `_ContestedProbe`（notice + status + 查询表面 + 降级码 + 准入表面）
显式传给 `_commit_receipt`／`_continue_active`／`_resume_existing`／`_create_new`，
**以及 `_reject`**（见 §4.6）。
放在派发之前是刻意的：`create_new` 的建域与绑定发生在 `_commit_receipt` 之前，
若把探测塞进 `_commit_receipt`，一次取消就可能留下「域已建、路由决策未落库」的残局。
探测本身：

- **查询是两条表面的确定性拼接**：模型自己的 `query` 在前、Host 自己认定的
  当轮用户原文在后，中间一个 `\n`（两条相同则只发一条）。为什么不能只取一条，
  见 §4.4；哪一条真正准入，记在 `contested_probe_admitted`。
  用户原文由新增的 `read_current_turn_text()` 只读该 Run 在世的
  `foreground_run_heads` + `foreground_turns.turn_json.payload.text`
  （`?mode=ro`，`LIVE_CLAIM_STATES`），读不到只损失一条表面，绝不影响路由。
- 走同一个 `_recall_executor`，四类 `memory_types`、`include_short_horizon=False`；
- **自带幂等命名空间**：`contested-probe:{run}:{turn}:{effect_id}`，见 §4.5；
- **只取 confirmation**：`project_contested_confirmation` 的结果并入 extras 的
  `conflict_notice`，`fragments` / `degradation_codes` / `recall_refs` 一概丢弃
  （degradation 只进审计）。因此 `direct_standalone` 对模型而言仍然
  「没有召回内容」，`ContextRouteReceipt` 一字未改；
- **相关性由 SDK 自己判**（0.6.31 的 `contested_slot_text` + 向量准入），
  Host 不加任何启发式，所以无关轮照旧零披露（最小必要）；
- **永不成为路由裁决**：executor 缺失 / 超时 / 异常 / 车道畸形一律降级，
  只在 `context_route_tool_invocations.detail_json` 写
  `contested_probe: "unavailable"`；两条表面都没有时写 `"no_query"`；
  问过而无争议写 `"clear"`（「问过、没有」与「压根没问」是两件事，
  本事件的验收正是关于「问没问」，所以干净路径的 detail **不再**逐字节不变，
  而是多出 `contested_probe:"clear"` 与可能的
  `contested_probe_degradation_codes`）。命中时照旧写事件 O 的
  `recall_conflict`（原因码 + group id + exact revisions，**不含候选值原文**）。

成本：装机 SDK 在证据库上热态 `typed_recall` 实测 **0.017–0.018 s**
（进程内首次 2.1 s，属一次性向量世代构建），可以接受。争议命中时另加一次
归因探测（只在少数争议轮发生）。

### 4.2 争议值永远不能以普通事实形态到达模型（`memory/human_memory_v7.py`）

新增 `_item_conflict_status(item)`。0.6.34 的普通车道
（`sqlite_v5.py::_cognitive_recall_state_allowed`，不带 `allow_contested`）
只放行 `uncontested|resolved`，所以今天恒为 `None`、投影逐字节不变。若日后 SDK
改成把 contested head 当普通 item 下发：

- `project_recall_fragments` 直接丢弃该 item（不做成 fragment）；
- `project_contested_confirmation` 用同一份隐私门把它渲染成单候选组
  （`role="head"`），产出**同一个** `conflict_notice`、同一条回执 detail、
  同一条 PERSONA 指令。该单候选**故意不带 `value`**（只有
  `revision`/`payload_hash`/`privacy_class`）：没有对手候选时唯一诚实的措辞是
  「这个值有争议、不要拿它执行」，把孤值印出来只会诱导模型采信它——正是 T22。

### 4.3 PERSONA（`execution/primary_context.py`）

「A conflict_notice on a **memory_standalone** result」改成「on **any
context_route** result」，并补一句它**覆盖对话里同一个值，包括用户刚给出的更正**
——这正是 T22 的失败措辞（模型写的是「依据本请求中刚给出的更正」）。

### 4.4 保证是「Host 一定会问」，不是「一定会报」

槽位插桩输出（§2.4）是 `{"object_value":["Python 3.13","3.12"]}` 与
`{"predicate":"proofreading_script_python_version"}`——**一个 CJK 字符都没有**，
而该记忆又没有向量世代（memory `CJK typed recall defect`）。所以 T22 的**真实
用户原句**「那你现在按哪个版本执行这套校对流程？」在 0.6.34 上
`confirmation_groups=0`，重放里之所以能出通知，只是因为**模型的转述**里逐字带了
「统一用 3.13，不是 3.12」。

由此三条结论，全部已落到实现与文档：

1. 本轮的结构性保证只能写成 **「每一条会执行的路由，Host 都一定会去问 Memory」**，
   不能写成「一定会报出争议」。能不能答由 SDK 的槽位准入决定，见 F-V-2。
   `context_route.py` 模块文档串与 ARCHITECTURE 条目均按此措辞改写。
2. **F-V-1 不落地**（评审否决）。它的做法是「用当轮用户原文**替换**模型转述」；
   在本事件里那恰好会删掉唯一能准入的词面（「3.13/3.12」），把已经能出的通知
   变成出不来。
3. 改为**两条都发**，并把「单独哪条能准入」测量下来：归因探测单独用**用户原文**
   再问一次，准入 ⇒ `contested_probe_admitted="user_turn"`（Host 站得住），
   不准入 ⇒ `"model_query"`（这次披露只是因为模型恰好复述了取值——即 T22 形状，
   生产中出现该值应视为缺陷报告而非统计量）。

### 4.5 探测不得占用模型自己的幂等键

`turn_ordinal` 是**每次 provider 响应**一个，同一批工具调用共享它；而 SDK 按
`RecallPlan.idempotency_key` 存一份持久请求，同键不同请求即
`MemoryIdempotencyConflict`（`test_model_recall_selection.py::
test_changed_selection_cannot_reuse_same_plan_result` 钉住）。原键
`context-route:{run}:{turn}` 因此是**每轮一次**而非每次调用一次：一旦每条路由都探测，
一次批次里「`direct_standalone` 探测 + `memory_standalone` 真召回」就会撞键，
把模型真正要的那条路由打成 `context_route_adjudication_failed`；两条非记忆路由
则第二条探测被吞成 `contested_probe="unavailable"`。

修法：`human_memory_v7.py` 提出模块级 `recall_idempotency_key(purpose, run_id,
turn_ordinal, scope)` 与 `DEFAULT_RECALL_PURPOSE`，`typed_recall` 新增
`idempotency_purpose` / `idempotency_scope` 两个参数（plan id 同步派生，
因为 plan 本身在 `request_json` 里）。于是：

| 车道 | 幂等键 |
| --- | --- |
| 模型自己的 `memory_standalone` 召回 | `context-route:{run}:{turn}:{effect_id}` |
| 争议探测 | `contested-probe:{run}:{turn}:{effect_id}` |
| 归因探测 | `contested-probe-attribution:{run}:{turn}:{effect_id}` |

`caller` 仍是 `foreground_recall` 一个值：`operation_audit/memory_attempts.py::
CALLERS` 是闭集，`quality/audit_coverage.py::check_typed_recall_foreground` 按
`caller=foreground_recall` + `idempotency_key NOT LIKE 'analysis-candidates-%'`
关联，探测键天然落在这条既有车道里；拆出新 caller 就要再注册第三个 check，
**故意不拆**（有专门用例钉住）。

### 4.6 被拒绝的路由也要交出已经查到的争议

探测在派发之前跑，所以「查到争议、但这条 proposal 随后路由校验失败」
（无活跃域 / 缺 `task_scope_id` / 缺 title / 绑定授权 / 血缘过期 …）
是真实存在的一类。原来 `_reject` 不接 `contested`，等于「问了 Memory、
被告知争议、却什么都不告诉模型」——正是 T22 的处境。现 `_reject` 接
`contested`：**稳定错误码一字不改**，通知挂在错误体里（`error.conflict_notice`），
审计同时记 `contested_probe` 与 `recall_conflict`。

### 4.7 非记忆路由上的结果也必须有哈希

`public_result_hash` 原来只在存在 typed carrier（即 `memory_standalone`）时才写。
非记忆路由不建 carrier，于是**争议取值随 extras 送到模型、却没有任何哈希覆盖**，
而 §7 曾据此写「同样被既有 `public_result_hash` 覆盖」——这句是错的，已改。
现在只要挂了通知，提交路径按 `result`、拒绝路径按错误体各算一次
`canonical_sha256` 写入 `detail_json`；没有通知的干净路径逐字不变（不写哈希）。

---

## 5. 控制

- 新增 `backend/tests/sdk_adapters/test_contested_route_guard.py`：**32 项**，
  以证据形状离线复现（T22 逐字 query + 证据里的 group id / r2 / r3 取值）。
  其中评审后新增的 19 项分别钉住：两条表面的确定性拼接与
  `contested_probe_query_sources`／`_admitted`（两种真实准入方向各一例）、
  用户原文读取失败只损失一条表面、同轮两次 `context_route` 的三条幂等键
  （探测 / 归因 / 模型自己的召回）、四条被拒路由仍交出通知且错误码不变、
  提交与拒绝两条路径的 `public_result_hash`、干净路径不写哈希、
  降级码不被当成 clear、被取消的探测不留下已建 TaskScope、
  `caller` 不拆车道；`continue_active` 也并入全路由参数化。
- `backend/tests/memory/test_contested_recall_disclosure.py`：9 → 15 项
  （新增 5 项 contested-item 前向兼容 + 1 项 resolved 状态不受影响；
  单候选组按评审改为断言**不带 `value`**）。
- 一次跑完评审指定的命令
  （`test_context_route_tool.py` + `test_contested_route_guard.py` +
  `test_contested_recall_disclosure.py` + `test_model_recall_selection.py` +
  `tests/quality/test_audit_coverage.py`，`-q -p no:randomly`）：
  **101 passed**（34 / 32 / 15 / 13 / 7）。
- 本轮机器刚发生过全机 OOM，按约束**全程只允许一个 pytest 进程**，
  因此只跑了上面这条指定命令，未做 `tests/sdk_adapters`/`tests/memory`/
  `tests/execution` 全目录的 FAILED 集合对比（事件 P 那种逐条比对本轮未做）。

---

## 6. Followup

- **F-V-1（Host）——评审否决，不落地。** 原提法是「proposal 不带 `query` 时
  改用当轮用户原文当探测查询」，若按其字面把用户原文**替换**模型转述，在本事件
  里恰好会删掉唯一能词面准入的项（「3.13/3.12」），把已经能出的通知变成出不来。
  取而代之的是 §4.4 的「两条都发 + 归因测量」，已在本轮落地。
  （原始动机——proposal 完全没有 `query` 时无相关性依据——由「用户原文也是一条
  表面」自然覆盖：两条都为空才记 `no_query`。）

- **F-V-2（SDK 0.6.34）——评审升级为本 AC 的阻断项。**
  Host 侧只能保证「每条会执行的路由都去问了 Memory」；「问了能不能得到答案」
  在 0.6.34 上对**纯中文提问**不成立，所以 A6-8 / NC-4 的验收
  （「依赖争议值的任务必须要求确认」）不能只靠 Host 侧成立。
  生产中 `contested_probe_admitted` 一旦记为 `model_query`，即表示该次披露
  只是因为模型恰好复述了争议取值——那是缺陷报告，不是统计量。

  给 SDK 侧 Agent 的逐字要求：

  > **需求**：`backends/sqlite_v5.py::_collect_typed_recall_confirmation` 目前把
  > 冲突组的**词面准入基底**收窄为 `contested_slot_text`（= 各候选之间的差异
  > 字段取值 + semantic 的 `predicate`，0.6.31 起「不再看与兄弟记忆共享的
  > `subject_entity`/`qualifiers`」）。请把冲突组准入的词面基底扩展为
  > **`contested_slot_text` ∪ 该 group 所属 head 当前 revision 的
  > `subject_entity` 与 `qualifiers` 文本**。
  >
  > **范围**：此扩展**只对冲突组（contested）准入生效**，普通 item 车道的词面
  > 准入不变。0.6.31 收窄的初衷是避免共享 `subject_entity`/`qualifiers` 的兄弟
  > 记忆互相污染；冲突组只有一个 head，不存在兄弟污染，收窄在这里没有收益。
  > 该 head 无向量世代时（CJK typed recall 缺陷）词面是唯一车道，因此扩展在
  > 无向量世代的情况下必须同样生效。
  >
  > **验收夹具**：`.local-test-evidence/2026-09-09/native-a6-run9/
  > primary-ui-8whts2lo/userdata/data/human_memory_v7.db`（连 `-wal`/`-shm`
  > 复制出来，剔除晚于 T22 的遗忘指令 `suppression-directive-1dba5ee…`
  > `effective_at=1788905616.85`，时钟钉 `1788905502.0`）。
  > 期望：查询「那你现在按哪个版本执行这套校对流程？」→
  > `outcome=needs_user_confirmation`、`items=0`、`confirmation_groups=1`
  > （今天是 `recall` / `items=1` / `0` 组）；
  > 回归不变：「今天天气不错，随便聊聊」仍为 **0** 组，
  > 「秋分资料整理校对流程所用的 Python 环境」与 T22 逐字原文仍为 **1** 组。
  >
  > **契约**：`S3-cognitive-systems-recall.md` §5.3 只要求「contested 候选**只能**
  > 走完整 group confirmation」，从未规定准入基底，因此这是 SDK 侧策略修订，
  > 不需要契约变更；若 SDK 侧希望把它写死，请在 §5.3 增补一句
  > 「冲突组的词面准入基底包含该 group 所属 head 的 `subject_entity`/
  > `qualifiers`」。
  >
  > **Host 侧不需要任何配合改动**：`contested_probe_admitted` 会自动从
  > `model_query` 翻成 `user_turn`，那就是本 followup 的验收信号。

- **F-V-3（验证）**：本轮是「Host 一定会去问」的结构性修复；「模型拿到
  `conflict_notice` 后会不会仍然采信对话里的旧值」需要真实模型逐字重放复验
  （事件 O 当时是 3/3 要求确认），本轮未做。

- **F-V-4（Host，残留）**：模型**一次 `context_route` 都不调**的那一轮，
  Host 记的是 `origin="no_recall"` 路由决策（`sdk_adapters/typed_context_use.py`
  的 `record_no_recall`），本轮的闸挂在 `context_route` 工具上，因此那条路径
  仍然一次都不问 Memory。T22 走的是 `direct_standalone`（有工具调用），
  所以本事件已被覆盖；`no_recall` 轮要不要同样探测，需要先确认它在契约上
  是否可能「依赖某个存储值执行」，未在本轮判定。

## 7. 未改动 / 边界

- `ContextRouteReceipt`、typed-use carrier、`recall_refs`、
  `context_route_decisions` 一字未改；争议通知只走 extras（与事件 O 同一位置）。
  **更正**：本文早先写过「同样被既有 `public_result_hash` 覆盖」——那是错的。
  该哈希原先只在有 typed carrier 时才写，非记忆路由上争议取值曾无任何哈希覆盖；
  见 §4.7，现已按结果体补写。
- 干净路径的 `detail_json` **不再**逐字节不变：多出 `contested_probe:"clear"`
  （以及探测自身的 `contested_probe_degradation_codes`）。这是刻意的——
  「问过、没有争议」与「压根没问」必须可区分。
- 稳定错误码集合未变；被拒绝路由新增的只有错误体里的 `conflict_notice`
  与 detail 里的 `recall_conflict`/`contested_probe`/`public_result_hash`。
- `caller` 仍只有 `foreground_recall`/`analysis_candidates` 两个值，
  `quality/audit_coverage.py` 与 `operation_audit/memory_attempts.py` 未改。
- S3 §5.2 的整组原子性、隐私门、抑制门全部沿用 SDK 与事件 O 的既有实现，
  本轮没有新增任何披露判据。
- 未触碰其它 Agent 正在编辑的文件（`analysis_proposal_v8.py`/`v9.py`、
  `scripts/native/a6_verify.py`、`sdk_adapters/wire_input_budget.py`、
  `llm/model_info.py`、`memory/semantic_correction.py`、`memory/procedure_runtime.py`）。

---

## 8. 评审（只读复核）处置

复核在 `main 34dbc82a` + 本工作树上进行，提出 4 条 MUST-FIX，全部采纳：

| # | 复核意见 | 处置 | 落点 |
| --- | --- | --- | --- |
| 1 | **闸关住的是那串字，不是那个类**：槽位文本无 CJK 且无向量世代，纯中文用户轮准入不了；重放能出通知只因模型转述复述了取值。要求 (a) 改正 §6/§7 与 ARCHITECTURE 的保证措辞，(b) 不要落 F-V-1，(c) 模型转述与 Host 已知的当轮用户原文**都发**并记录哪条准入，(d) F-V-2 升级为 AC 阻断项 | **全部采纳**。措辞统一改为「Host 一定会问」；F-V-1 记为否决；确定性拼接两条表面 + 归因探测把 `user_turn` / `model_query` 记进审计；F-V-2 升级并写出逐字 SDK 需求 | §4.4、§6、§7、`context_route.py` 模块串、ARCHITECTURE |
| 2 | **同轮两次 `context_route` 的幂等冲突**：`turn_ordinal` 是整批共享，探测会吃掉模型自己的键 | 采纳。抽出 `recall_idempotency_key()`，`typed_recall` 新增 `idempotency_purpose`/`idempotency_scope`，探测与归因各自命名空间，记忆车道也按 `effect_id` 分键；新增同轮两次调用的用例 | §4.5 |
| 3 | **路由被拒时丢弃已查到的争议** | 采纳。`contested` 贯穿 `_reject`，错误码不变、通知挂错误体、审计记 `recall_conflict`/`contested_probe`；四条拒绝路径各一用例 | §4.6 |
| 4 | **审计声明不实**：非记忆路由没有 `public_result_hash` | 采纳。挂了通知就按实际结果体（提交/拒绝各一条）写哈希；§7 的错误说法已更正 | §4.7、§7 |

评审的「便宜的 NICE-TO-HAVE」逐条：`continue_active` 已并入全路由参数化；
被取消的探测不留下已建 TaskScope（已有用例）；`contested_probe:"clear"` 与
`contested_probe_degradation_codes` 已记录；`context_route.py` 模块文档串
已改（`direct_standalone` 不再写作「no Memory query」）；`no_recall` 残留记为
F-V-4；`caller` 保持单一值并加用例钉住（拆车道要再注册第三个 check）；
前向兼容单候选组不带 `value`（§4.2）。
