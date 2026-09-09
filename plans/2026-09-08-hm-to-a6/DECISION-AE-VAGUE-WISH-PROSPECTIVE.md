# 事件 AE 决策记录：一个「以后有机会」的愿望，凭什么变成了会响的日程

> 义务：`HM-TO-A6` / `00-PLAN.md` 第 14 轮（负控 **NC-3**：「以后有机会我想学画画。」→ 只进 Semantic Goal，
> 无 pending Prospective，不调度不提醒）
> 现象：第 11 次原生跑 T14 产出了一条 pending Prospective「有机会时开始学画画」，注册成功 2 条，
> 并在旅程内**真的触发**了。
> 分支：`worktree-vague-wish`（基线 `7d161678`）
> 证据：`.local-test-evidence/2026-09-09/native-a6-run11/primary-ui-9izlp1ao/userdata/data/`
> （`human_memory_v7.db`、`state.db`）

---

## 0. 第 11 次 T14 到底发生了什么（逐行复原）

| 环节 | 证据行 | 取值 |
|---|---|---|
| 请求 | `state.db.human_memory_evidence` → `analysis-attempt-input-0bf36e50…` | `prompt_version=host-analysis-prompt/v9`、`policy_version=host-analysis-policy/v9`、`result_schema_version=memory-analysis-proposal/v9`；`model_id=deepseek-v4-flash`；`max_output_tokens=6144`；`evidence_items` 只有一条「以后有机会我想学画画。」；`semantic_candidates: []`、`procedure_candidates: []`；**`now_iso: "2026-09-09T10:26:00+08:00"`** |
| 批次 | `human_memory_v7.db.analysis_batches` → `analysis-batch-045e61fd…` | `state=applied`，`base_revision=9 → committed_revision=10`，`created_at=1788920762.9` |
| 接受的 plan | `accepted_analysis_plans.plan_json`（同批次） | 两条 operation，全部被 Host 收下，`rejected` 为空 |
| op 1 | `op_prosp_learning_draw` | `memory_type=prospective`、`lifecycle_state=pending`、`reason_code=future_intention`、`payload.action="有机会时开始学画画"`、`payload.trigger={"trigger_kind":"time","trigger_at":1788920760.0,"timezone":"Asia/Shanghai"}`；`evidence_spans[0].exact_quote="以后有机会我想学画画。"`（整句，`start_byte=0`、`end_byte=33`、`support_kind=explicit_user_assertion`） |
| op 2 | `op_sem_learning_draw` | `user:self · interest_learn · "画画"`，`lifecycle_state=active` —— **这条是对的**，NC-3 期望的就是它 |
| 落库 | `prospective_records`（`cognitive-memory-654c6a54…`，revision 1 与 2） | `trigger_kind=time`、`due_at=1788920760.0` |
| 注册 | `prospective_scheduler_registrations` | 2 行同一 `scheduler_registration_ref`：`accepted @1788920763.30282`，随后 `invalidated @1788920766.74062` |
| 触发 | `prospective_trigger_events` | 1 行：`signal_kind=time_due`、`outcome=matched`、`reason_code=prospective_trigger_matched`、`occurred_at=1788920763.41721` |
| 记忆状态 | `cognitive_memory_revisions` revision 2 | `lifecycle=triggered` |

**关键一步：那个时间戳不是模型「估」出来的，它逐字等于请求体里的 `now_iso`。**
`datetime.fromisoformat("2026-09-09T10:26:00+08:00").timestamp() == 1788920760.0`。
所以这条提醒在被登记（`occurred_at=1788920763.3`）时**已经过期 3 秒**，下一拍就 `time_due/matched`。
它从来不是一个「几分钟后的提醒」，它是「立刻」。

第 8–10 次「NC-3 通过」不能算数：s5c cursor-version 回归（`48617f73` 已修）让那三次的每一条注册都失败，
所以第 11 次是这条负控的**第一次诚实测量**。

---

## 1. 根因：`trigger_at_iso` 是分析车道里唯一不受证据约束的字段

Host 的分析编译器（`deskpet/memory/analysis_proposal.py::compile_operation`，v3 起 v4..v9 全部继承）
对 prospective 只做两件事：

```python
at = datetime.fromisoformat(str(body["trigger_at_iso"]))
if at.tzinfo is None:
    raise AnalysisProposalRejected(PAYLOAD_INVALID, reason="trigger_offset_missing")
```

逐字纪律（`derive_span`）约束的是 `exact_quote` —— 它必须逐字节是证据文本的子串，Host 会重算
`quote_hash`。但 `trigger_at_iso` **不在任何一条引文规则的射程内**：模型可以引一句完全没有时间的话，
同时写任意一个时刻，Host 每一道检查都会放行。这就是「未来意图」这一类的准入缺口，
也是整条分析车道里**唯一一个模型可以凭空写、Host 完全不核对**的字段。

第二半根因在 schema：v3..v9 的 prospective 分支
`required: ["action", "trigger_at_iso", "timezone"]` —— **只有时间触发一种形状，没有事件触发分支**
（`ProspectiveEventTrigger` 只在 C04 夹具里由 SDK 公共 API 直接构造，分析车道从来发不出来）。
再加上 v3 起的策略文本一直是「未来意图/提醒用 prospective（给出首次到期的 ISO 时间和 IANA 时区）」，
从没说过「没有时间的愿望应该落到哪里」。于是模型面对一个没有时间的将来意图，只有两条路：
丢掉这句话，或者编一个时间。它选了后者，而且选了手边最现成的那个数——Host 自己递过去的 `now_iso`。

这不是模型的判断失误，是契约把它逼到这一步。

---

## 2. 修法：策略正面陈述（v10）+ 一条硬校验（`host-analysis-validator/v6`）

### 2.1 新协议 `host-analysis-prompt/v10`

`backend/deskpet/memory/analysis_proposal_v10.py`。提示体进 `bind_attempt` 的哈希，所以改词必须换协议
id，不能就地改 v9（否则已持久化的 v9 请求重放会报 `analysis_attempt_input_conflict`）。
线格式、tool schema、编译器仍是 v8 那**同一个对象**；v10 是策略版本 + 一条准入规则。
`analysis_protocol.protocol_for_request` 仍解析 v3/v4/v5/v5.1/v6/v7/v8/v9，全部一字未改。

新增的 `_PROSPECTIVE_INSTRUCTION` 是**正面陈述**，不是禁令（事件 L/T 已经反复测到：只写禁令会让模型
在预算里反复权衡，甚至 `finish_reason=length`）：

* ①有明确时间表达（「明天九点半」「下周三」「9月8日10:00」「两小时后」）或明确触发条件，才用
  prospective；`trigger_at_iso` 必须就是本句引文里那个时间解析出来的结果，不能自己估、
  **尤其不要把 `now_iso` 抄成 `trigger_at_iso`**（点名 T14 实测的那一步）；并告知 Host 会重新解析比对，
  对不上整条拒收；
* ②只是模糊的将来愿望、句中没有具体时间也没有具体条件：**不提 prospective**，改记一条 semantic
  （`subject_entity=user:self`，`predicate` 用 `interest_…`/`goal_…`，`object_value` 取本句原文），
  需要时再加一条 episode。

两个拒收码都写进了提示文本，模型才知道代价是**整条丢失**而不是「尽力而为」。

### 2.2 硬校验：`prospective_trigger_grounding.py`

新模块 `backend/deskpet/memory/prospective_trigger_grounding.py`，被 v10 通过
`v8._compile_validated_proposal(..., prospective_grounding=…)` 挂进编译链（这个 kwarg 对 v8/v9 是
`None`，两个协议的行为逐字不变——`test_analysis_proposal_v10` 复核了它们的线格式金值）。

两条规则，都是**单条 operation** 的拒收，同批的 semantic/episode 照常落库：

| 码 | 触发条件 |
|---|---|
| `analysis_prospective_vague_wish` | 引文里出现模糊将来标记（有机会 / 以后 / 今后 / 将来 / 哪天 / 某天 / 改天 / 有空 / 找时间 / 抽空 / 迟早 / 总有一天 / 有朝一日 / 得空 / someday）**而且**引文里解析不出任何时间表达 |
| `analysis_prospective_trigger_not_grounded` | 引文里有时间表达但没有一个能解析到模型给的 `trigger_at`（含容差）；或引文里既没有时间表达也没有模糊标记 |

判定的输入是**这条 operation 自己引用的证据跨度**（`span.exact_quote`），Host 已经逐字节证明过它是
被采纳证据文本的子串，所以「这句话里有没有时间表达」是一个 Host 能从耐久行独立复算的事实，
不是第二次模型判断。

### 2.3 那个「现成的时间表达解析器」并不存在

任务书假设可以复用「C04 oracle / `trigger_local` 渲染用的那个时间表达解析器」。**实测不存在**，这一点必须写下来：

* `deskpet/quality/corpus_c04.py` 的时间来自 `SPECS` 里手写的 anchor 字面量（`'2026-09-07T09:00'`），
  `precision_oracle` 只是把它们抄进评分记录；`episode_interval` 做的是「精度 → 有效时间区间」，不是文本解析；
* `human_memory_v7._prospective_trigger_local` / `render_episode_occurred_local` 是**渲染**方向
  （epoch + IANA → 本地字符串 + 中文星期），不是解析方向；
* `deskpet/companion/*` 的 `_parse_time` / `_parse_hhmm` / `_parse_datetime` 只吃 ISO / `HH:MM` 配置值。

全仓没有任何「中文时间短语 → 时间戳」的代码。所以接地判定的解析器是本轮**新写**的，
而 C04 的 anchor 文本（`9月7日09:00`、`2027年1月2日10:00`、`9月8日00:30`、`9月8日09:00 Europe/London`）
正好成了它的正样本语料——见 `test_analysis_proposal_v10.py` 的正控参数表。

解析器的设计取舍：

* **保守**。解析不出来 = 拒收。少认一个真实提醒的代价是模型下一轮重提；多认一个凭空时间的代价是
  用户被一个他从没约过的日程叫醒（本次事件）。
* **容差有界**。带钟点的表达 ±5 分钟（`CLOCK_TOLERANCE_SECONDS`，覆盖模型把「九点半」写成 `09:30:00`
  或差几十秒的抖动）；只有日期没有钟点的表达（「下周三」）给**那一整个本地日**——用户没说几点，
  模型选 09:00 还是 10:00 都是诚实的。
* **锚在证据采纳时刻**，不是分析时钟。分析是 post-turn job，崩溃恢复后可能晚很多才跑；
  「两小时后」必须锚在用户说话的那一刻（`item.occurred_at`），否则可接受区间会跟着分析延迟一起漂。
* **时区取触发器自己声明的 IANA 名**。声明一个解析不了的区（`Mars/Olympus`），接地自然失败，方向安全。
* **确定性**。同样的 `(text, reference, timezone)` 永远给同样的结果，全程不读系统时钟。

支持的文法：`2027年1月2日` / `9月8日` / `2026-09-08` / 今天·明天·后天·大后天 /
本周三·这周三·下周三·下下周三·裸「周三」（给本周与下周两个候选）/ `N天后`·`N周后`·`N个月后` /
`09:00`·`9点`·`九点`·`九点半`·`一刻`·`三刻`·`整` / 早上·上午·中午·下午·傍晚·晚上等时段前缀（12 小时→24 小时归一）/
`N分钟后`·`N小时后`·`半小时后`；中文数字 `〇零一二三四五六七八九十两` 到两位。

---

## 3. 为什么 NC-3 的正确出路是 semantic，而不是「事件触发的 Prospective」

`00-PLAN.md` 第 14 行的产品决定逐字是「**负控**：只进 Semantic Goal / 无 pending Prospective 行 / 不调度不提醒」。
本轮**没有**给分析车道开事件触发分支，理由三条：

1. 「以后有机会」不是一个条件，它没有可观测的边界。C04 的事件触发（`下一次归还成功`、`试印验收成功`）
   都是有明确观测点的事件；「有机会」没有任何东西能让它 `matched`。
2. 分析车道要发事件触发，就得自己编一个 resolver 命名空间。C04 夹具用的是
   `corpus:unobserved-event:<hash>`，并在注释里明说「没有安装任何 resolver/signal」——那是一条
   **永远不会触发**的记忆。从生产车道量产这种记忆，等于把「愿望」伪装成「待触发的提醒」，
   在 UI 上、在召回里、在 `prospective_current_reader` 里都会占着提醒的位置。
3. 用户的意图本来就已经被正确表达了：同一批的 `user:self · interest_learn · "画画"` 就是它该有的形状。
   T20 的纠正、T23 的遗忘、图谱、召回，全都在 semantic 这条链上工作。

事件触发本身仍是 F01（`CLAUDE.md` 2026-09-08 用户决定：本轮不做，方案 B 待定）。

---

## 4. 裁定：s5c 注册侧**不**加「trigger_at 距提案时刻不足 N 秒就拒绝注册」的纵深防御

任务要求就这条做出决定并记录。**决定：不加。** 理由：

1. **那条车道看不见证据。** `deskpet/memory/prospective_registration_source.py` 的整个契约是
   「把 Memory 自己发出的 outbox 命令逐字段绑到 Host 授权上」：它复核 `outbox_payload_hash`、
   `trigger_hash`、`target_source`、receipt 身份、cursor 版本，然后签发
   `ProspectiveSignalIntent`。它拿不到证据引文，也不该拿——一旦它开始按记忆**内容**做取舍，
   传输授权车道就变成了第二个策略车道，而且是一个没有输入的策略车道。
2. **「马上就到」是合法的产品行为。**「五分钟后提醒我」「等下十分钟叫我」都会产生一个距提案时刻只有几分钟
   （网络与 job 排队后甚至几十秒）的 `trigger_at`。任何 N 都会误伤真实提醒，而这类短提醒恰恰是最不该丢的。
3. **在那里拒绝的失败形态更糟。** Memory 侧已经写下了一条 `pending` 的 Prospective（`prospective_records`
   + `cognitive_memory_revisions`），注册被拒只会让调度器不知道它。结果是一条**永远 pending、永远不会响、
   也不会被任何东西收敛**的记忆，用户在记忆面板里看得见它却等不到它。响一次是可见的错误，
   静默地永远不响是不可见的错误。
4. **接地判定已经把这条路堵死在源头。** 分析车道是唯一从自由文本铸造 `trigger_at` 的地方
   （另一个入口是 SDK 公共 API，那是调用方自己的时间，不是模型编的）。v10 之后，一个没有时间表达的句子
   根本产不出 prospective operation，所以「凭空的近 now 触发」在注册车道上不会再出现。

**替代的纵深防御**（成本为零、方向正确）：接地判定天然拒绝一切「不是用户说出来的时刻」，包括 `now_iso` 本身
——因为 `now` 永远不会是引文里的一个时间表达，除非用户真的说了那个时刻。这条由
`test_the_fabricated_trigger_is_exactly_the_requests_own_now_iso` 钉住。

留一条 followup：**F-AE-1** —— s5c 侧目前也不记录「这条注册的 trigger 距 outbox 创建有多近」。
如果以后要观测（而不是拒绝）这一类，正确形态是在 `ProspectiveSourceJournal` 加一条 payload-free 的
审计计数，不是在授权路径上加判据。

---

## 5. 真实模型复算（`deepseek-v4-flash`）

驱动脚本提交为 `scripts/native/a6_replay_t14.py`（复用 `a6_replay_t15.py` 的 `call` / `load_credentials`，
凭据只在内存里用于 Authorization 头，不打印、不写进输出、不入库；样本仍留 scratchpad）。
输入是第 11 次那条 attempt-input 的**逐字重放**：同一段 `user_content`（含同一个 `now_iso`）、
同一个 `max_output_tokens=6144`、`temperature=None`；臂之间只换 system 提示词与工具 schema。
已核对：重放渲染出的 v9 system 文本与持久化请求里的那一份**逐字节相同**（3956 字符）。

### 5.1 NC-3 本句（「以后有机会我想学画画。」）

| 臂 | 样本 | 提了 prospective | `trigger_at_iso == now_iso` | Host 编译后落库的 pending Prospective | 提了 semantic | Host 全收 | `finish=length` | completion tokens 中位数 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| v9（对照） | 24 | **0/24** | 0/24 | **0** | 23/24 | 23/24 | 0/24 | 919 |
| **v10** | 8 | **0/8** | 0/8 | **0** | 8/8 | 8/8 | 0/8 | 571 |

**这张表最重要的一行不是差异，是 v9 那一行的 0/24。** 事件 AE 在隔离重放下**复现不出来**：
第 11 次原生跑观测到 1 次，24 次重放 0 次。所以它是一个**低频采样事件**，
不是 v9 提示词的稳定行为。由此得到本轮最硬的结论：

> **提示词改动无法被测量为「修好了」这个缺陷；真正关闭它的是硬校验。**
> 一个 1/N 的采样事件，任何提示词都只能把 N 变大，不能把它变成 0。
> `analysis_prospective_vague_wish` 才是那个 0：无论模型第几次抽到那条路径，Host 都不会收。

v10 提示词能被测量到的**是另一件事**，而且很干净——它把 semantic 的槽位命名收敛到了策略指定的形状：

| 臂 | `predicate` 分布 | 落在 `interest_*` / `goal_*` |
|---|---|---:|
| v9 | `wants_to_learn` ×16、`learning_interest` ×2、`future_learning_desire`、`learning_aspiration`、`aspires_to_learn`、`future_learning_goal`、`future_aspiration` 各 ×1 | **0/24** |
| v10 | `interest_learning_painting` ×6、`interest_learn_drawing` ×1、`interest_learn_painting` ×1 | **8/8** |

以及 completion tokens 中位数 919 → 571（判定终止得更早，与 v8/v9 的有序分支同源）。
槽位收敛本身有产品价值（同一个愿望在不同轮次落到同一个槽位，才谈得上以后被 supersede / 被遗忘），
但它不是本轮的验收项，记为观察。

### 5.2 正控（真实提醒，同一 `now_iso`，只换句子）

句子换成「明天上午九点半提醒我把修正版交出去。」（`now_iso` 仍是 `2026-09-09T10:26:00+08:00`）：

| 臂 | 样本 | 提了 prospective | `trigger_at_iso` | Host 拒收 | 落库 pending Prospective |
|---|---:|---:|---|---:|---:|
| v9 | 6 | 6/6 | `2026-09-10T09:30:00+08:00` ×6 | 0/6 | 6/6 |
| **v10** | 6 | 6/6 | `2026-09-10T09:30:00+08:00` ×6 | **0/6** | **6/6** |

即：**v10 的接地判定一次都没有误伤真实提醒**，模型给的时刻 6/6 被引文里的「明天上午九点半」接地。
这是这条硬规则最需要证明的一半。

---

## 6. 控制与影响面

| 项 | 结果 |
|---|---|
| 新增 `backend/tests/memory/test_analysis_proposal_v10.py` | **37 项通过**（协议身份 / v9 线格式金值 / 策略文本 / T14 红绿对照 / `now_iso` 回声 / 5 种模糊愿望 / 7 条正控时间表达 / 容差边界 / 引 span 而非整句 / 相对表达锚点 / 未知时区 fail-closed / 无偏移仍走 v3 诊断 / 解析器单元） |
| `test_analysis_proposal_v9.py` | 27 项通过（改 1 项：v9 不再是 current，断言改为「v10 是 current 且 v9 仍可解析」） |
| `test_analysis_proposal_v8.py` | 40 项通过（改 1 项：同上口径） |
| `test_analysis_v9_named_workflow_relation.py` | 通过（改 1 行：版本断言从 v9 字面量改为 `analysis_protocol.PROMPT_VERSION`——那一行本来测的是「车道当前协议」，钉字面量只会重复测协议表） |
| 分析车道全家 14 个文件 | **179 passed**（v5/v6/v7/v8/v9/v10 + output_budget + request_guard + response_unusable + v6_public_relation + v8_existing_relation + v9_named_workflow + procedure_adoption） |
| C04 语料 | `test_corpus_c04_payload_text.py` + `test_corpus_c04_prepare.py` **105 passed** |
| Prospective 全家 14 个文件 | 108 passed / 16 failed，失败集合与基线 `7d161678` **逐条一致**（在 `scratchpad/ae-baseline` 干净检出上复跑，同样 16 failed） |
| `test_analysis_episode_time.py` | 4 failed，基线同样 4 failed（既有红，与本轮无关） |
| `test_analysis_relation_prospective_applied.py` | 1 passed（**夹具被新规则抓到并修正**，见下） |
| `test_procedure_recovery_runtime.py` + `test_procedure_duplicate_recovery.py` + `test_analysis_relation_applied.py` + `test_analysis_relation_procedure_applied.py` + `test_analysis_relation_candidate_audit.py` | 23 passed（`execute()` 加了 `text=` 关键字参数，默认值一字未改） |
| `test_semantic_correction.py` + `test_semantic_correction_cue_and_contest.py` | 45 passed |
| `test_s5c_consumer_sdk.py` + `test_prospective_sources.py` + `test_s5b_acceptance_matrix.py` | 30 passed / 9 failed，与基线逐条一致 |

**新规则抓到的第一个真实存量问题**：`test_analysis_relation_prospective_applied.py` 的脚本化适配器
让用户说「执行记录和备份两步。」，却给出一个 `now + 86400` 的 `trigger_at_iso`——
**那正是 T14 的缺陷形状**，只是发生在夹具里。v10 按 `analysis_prospective_trigger_not_grounded` 拒收它。
处置不是给规则开口子，而是把夹具改成一个真实的提醒：`execute()` 新增关键字参数
`text=TURN_TEXT`（默认值与原来逐字相同，其余 6 个调用点不受影响），该用例传
「明天提醒我复查记录和备份。」。「明天」是日精度表达，可接受区间是次日**一整个本地日**，
`now+86400` 必落其中，所以这条断言不依赖「证据采纳时刻 vs 适配器时钟」的秒级偏差。

改动的文件：

* 新增 `backend/deskpet/memory/prospective_trigger_grounding.py`（接地判定 + 中文时间表达解析器）
* 新增 `backend/deskpet/memory/analysis_proposal_v10.py`（协议 v10）
* `backend/deskpet/memory/analysis_protocol.py`：注册 v10，current 前移
* `backend/deskpet/memory/analysis_proposal_v8.py`：`_compile_validated_proposal` 增加
  `prospective_grounding=None` 钩子（v8/v9 传 `None`，行为逐字不变）
* 新增 `backend/tests/memory/test_analysis_proposal_v10.py`
* `backend/tests/memory/test_analysis_proposal_v8.py` / `test_analysis_proposal_v9.py` /
  `test_analysis_v9_named_workflow_relation.py`：各改 1 处 current-协议断言
* `backend/tests/memory/test_procedure_recovery_runtime.py`：`execute()` 增加 `text=` 关键字参数（默认值不变）
* `backend/tests/memory/test_analysis_relation_prospective_applied.py`：夹具改成一个真实的（有时间的）提醒
* 新增 `scripts/native/a6_replay_t14.py`
* `ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`：新增事件 AE 条目
* 本文件

未改动（明确）：`prospective_registration_source.py` / `s5c_*.py` / `prospective_scheduler.py`
（§4 的裁定），`analysis_proposal.py` 的 prospective 编译分支（诊断码保持 v3 原样），
`analysis_proposal_v9.py`（一字未动，线格式金值 `8895dc9b…` 由 v10 用例钉住）。

---

## 7. Followup

* **F-AE-1**（§4）：s5c 侧对「trigger 距 outbox 创建过近」目前既不拒也不记。若要观测，
  正确位置是 `ProspectiveSourceJournal` 的 payload-free 审计计数，不是授权路径上的判据。
* **F-AE-2**：分析车道仍然发不出事件触发（schema 只有时间触发一种形状）。本轮按 §3 判定为
  **不应该**开，但如果 F01 落地后事件触发有了真实的 resolver 命名空间，这条要重新评估——
  届时 `_PROSPECTIVE_INSTRUCTION` 的「或明确的触发条件」才有对应的表达形状。
* **F-AE-3**：时间表达解析器目前只覆盖中文与阿拉伯数字。英文/混合输入（"remind me tomorrow at 9:30"）
  会解析不出表达 → `analysis_prospective_trigger_not_grounded`，即真实提醒被误拒。
  方向是安全的（保守），但对英文用户是功能缺失，需要在 UI 语言扩展前补。
* **F-AE-4**（观察，非缺陷）：v10 把 semantic 槽位收敛到 `interest_*`（8/8，v9 是 0/24）。
  这对「同一愿望跨轮落同一槽位 → 可被 supersede / 可被遗忘」有价值，但没有任何用例或验收项
  在守它，模型换一批仍可能漂。若要变成契约，得像饮品词表那样进 Host 词表，而不是靠提示词。
* **F-AE-5**：本轮把 v9 的线格式金值 `8895dc9b…` 钉在了 `test_analysis_proposal_v10.py` 里，
  而 v3..v8 的金值在 `test_analysis_proposal_v9.py` 的 `WIRE_GOLDENS`。下一个协议版本应当把这两处
  合成一个共享表，否则每加一版就多一处金值。
