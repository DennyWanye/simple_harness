# 决策备忘：语料 run-01e 的 prospective / procedure 零召回根因与最小修复

> **独立子代理分析，主代理复核后执行。** 2026-09-08。Host 仓 `simple_harness` main `be4a6325`（run-01e 采集时为 `030222cc+`），Memory SDK `simple-harness-memory-sdk` main `0ee3095a`（0.6.25）。本文只读代码与 run-01e 证据，未改源码、未运行原生应用、未调用模型。

## 0. 结论（先说三句）

1. **C04 prospective 零召回的门不是时间窗、不是 lifecycle、不是词面/向量**，而是 SDK 类型权限门要求「该 prospective 必须有一条 `state='accepted'` 且 `trigger_hash` 匹配的 `prospective_scheduler_registrations` 行」。语料 runner 在评分前把 `MemoryAnalysisLane` 关掉了，连带关掉了产出这些注册行的 `ProspectiveRuntimeLane`，于是**除 C04-12 外没有任何一条 pending 提醒拿得到注册**。**这是跑道缺陷，不是 Host 路由缺陷。**
2. **C06 procedure 零召回是「不放宽指纹门」这一既定设计的直接后果**：revision=1 的 procedure 指纹恒为 `UNBOUND`，且 `memory_standalone` 路由下 Host `current_fingerprints()` 恒返回 `()`。`DECISION-PROCEDURE-USE-CHAIN.md` 已把这条口径写死。**C06 的 gold「按类型召回 procedure」与该设计冲突，应改跑道，不应改门。**
3. 90/90 次 `NO_ACTIVE_GENERATION` 与上面第 1 条**同一个根因**（同一行 `_memory_analysis_lane.close()`），也是跑道问题：短时域世代由同一条 lane 里的 `PrimaryShortIndexWorker` 产出。run-01e 报告 §5.1.4 把它列为「疑似同源的产品缺陷」，可以就此结案为跑道。

因此 run-01e 报告 §6 的「【产品 P0】把 prospective 与 procedure 接进 `context_route` 的召回」**结论不成立**：`context_route` 的类型分发没有漏 lane，两条 lane 都进了 SQL（`sqlite_v5.py:5203-5212` 的 `memory_type IN (?)` 用的就是模型请求的类型），是资格门按设计挡下的。

---

## 1. C04 prospective：确切的门，与 C04-12 为何命中

### 1.1 逐层排除

| 候选门 | 是否成立 | 证据 |
|---|---|---|
| 时间窗（trigger 太远/太近） | **否** | Host 传 `earliest_occurred_at=None, latest_occurred_at=None`（`backend/deskpet/memory/human_memory_v7.py:404-405` 对应位置传 `context.earliest/latest`，两者均为 `None`）；候选 SQL 只按 `valid_from/valid_to` 过滤（`sqlite_v5.py:5205-5207`）。C04-03 的 P 在 2027-01-02（离场景钟 4 个月）、C04-07 的 P 在 9 月 9 日（离场景钟 3 天），**两者同样零召回**，直接证伪时间窗假说 |
| lifecycle 状态 | **否** | `_cognitive_recall_state_allowed`（`sqlite_v5.py:5658-5690`）对 prospective 允许 `{pending, triggered, in_progress, rescheduled}`，C04 种子全是 `pending`，在白名单内 |
| epistemic/verification | **否** | 同函数末尾要求 `explicit_user` + `{source_bound, user_confirmed}`；C04 fixture 与 C03/C11 用同一套（C03/C11 全部召回正常） |
| 词面/向量文本不含关键词 | **否** | 类型权限门在**打分之前**执行：`_collect_typed_recall_candidates` 的循环顺序是 state → **type_authority** → lineage → suppression → 才轮到 lexical/vector（`sqlite_v5.py:5217-5232`）。门失败即 `continue`，词面命中与否根本没机会生效 |
| **类型权限门：缺 scheduler registration** | **是** | `_cognitive_recall_type_authority_allowed_unlocked`（`sqlite_v5.py:5520-5592`）：prospective 分支在 lifecycle 非 `triggered/in_progress` 时，查 `prospective_scheduler_registrations`，要求 `registration is not None and state=='accepted' and trigger_hash==trigger_hash`（`:5583-5591`）。**没有注册行 ⇒ 直接 False** |

### 1.2 注册行为什么没有

- 生产路径：`backend/deskpet/memory/runtime_composition.py:78-79` 装配 `ProspectiveRuntimeLane`；该 lane 的 `tick()` 跑 `ProspectiveRegistrationConsumer.run_once()`（`backend/deskpet/memory/prospective_runtime.py:78-83`），这才写出 accepted 注册。lane 只由 `MemoryAnalysisLane.start()` 拉起（`backend/deskpet/memory/memory_ingestion_outbox.py:480-484`）。
- 语料路径：`backend/deskpet/quality/corpus_scoring_session.py:406-407`
  ```
  # Isolate foreground scoring from unrelated background model analysis.
  await main._memory_analysis_lane.close()
  ```
  `MemoryAnalysisLane.close()` 会 `await self.prospective_lane.close()` 并置 `_closing=True`（`memory_ingestion_outbox.py:510-517, 481`），此后 `start()` 直接 return。**评分阶段整个 Run 内注册消费者一次都不会跑。**
- 于是 C04 的 15 例已评分样本里，只有在 **setup 阶段自己手工跑过消费者** 的那一例能通过门。

### 1.3 C04-12 为何唯一命中

`backend/deskpet/quality/corpus_c04_prepare.py:75-80` 是全语料唯一的 case 特判：

```python
if batch.case_id == 'C04-12':
    await initialize_s5c_terminal_state_db(path)
    registrations = S5cStore(path, principal)
    signal_options['prospective_signal_authority'] = HostProspectiveSignalAuthority(path, principal)
```

随后 `:127-134` 与 `:143-155` 在 setup 里显式 `await consumer.run_once()` 两次，并断言旧注册被 ack（`c04_old_registration_not_actually_acked`）、新 `rescheduled` 注册被 ack（`c04_new_rescheduled_registration_not_acked`）。也就是说 **C04-12 的库里真的写进了 accepted 注册行**，门自然放行——run-01e 里它取回 `{"action":"确认新场地","trigger":{"trigger_at":1788917400.0,...}}`（`run-01e/C04-12/scoring/C04-12/observation-route_audit.json`）。

对照证据（`run-01e/<case>/scoring/<case>/observation-route_audit.json`）：

| 例 | 种子 P | 模型请求类型 | 返回 |
|---|---|---|---|
| C04-03 | 2027-01-02 10:00 pending | 第 1 次 `[episode, prospective]`；第 2 次**单请求** `[prospective]` | 第 1 次只有 episode；第 2 次 `fragments=[]` |
| C04-07 | 09-09 14:00 pending | `[episode, prospective, semantic]`；再单请求 `[prospective]` | 只有 episode；单请求空 |
| C04-12 | 09-09 09:30 **rescheduled**（+已 ack 注册） | `[episode, prospective, semantic]` | **prospective + episode 都拿到** |

补充说明：C04-12 的 lifecycle 是 `rescheduled` 而非 `pending`，但 `rescheduled` 与 `pending` 在状态门里等价（`sqlite_v5.py:5663`），**差别只在注册行**，不在 lifecycle。

### 1.4 一句话定性

**C04 的 14 例 FAIL 是语料跑道自伤**：为了隔离后台模型分析而关掉整条 lane，顺手关掉了 prospective 的必要前置写入。真实产品（原生 app）里 lane 是常开的，pending 提醒在几秒内就会拿到 accepted 注册。**没有证据表明 Host `context_route` 或 SDK 召回面对 prospective 有缺陷。**

---

## 2. C06 procedure：确切的门，与 acceptance 口径

### 2.1 门是双重的，都在「指纹」上

`_cognitive_recall_type_authority_allowed_unlocked` 的 procedure 分支（`sqlite_v5.py:5545-5557`）：

```python
return bool(procedure is not None
    and str(procedure[0]) != UNBOUND_PROCEDURE_APPLICABILITY
    and str(procedure[0]) in procedure_applicability_fingerprints)
```

两个条件在 C06 都必然为假：

1. **`applicability_fingerprint` 恒为 `UNBOUND`。** 写入侧 `sqlite_v5.py:17660-17665`：`if new_procedure_epoch or revision == 1: applicability_fingerprint = UNBOUND_PROCEDURE_APPLICABILITY`。C06 fixture 全部是 `revision=1` 的 CREATE（`backend/deskpet/quality/corpus_c06_prepare.py:46-50`，`read_c06_preparation` 还断言 `node.revision == 1`，`:148`）。只有一次真实的 `procedure_observation` 才会把 UNBOUND 绑定成本次指纹（`sqlite_v5.py:6331-6335`，`reason_code="procedure_applicability_bound"`）。
2. **Host 送进来的 `current_fingerprints` 恒为空。** `backend/deskpet/memory/procedure_runtime.py:97-121`：先 `resolve_procedure_route(...)`，`memory_standalone` 路由下没有 active TaskScope 会抛 `ProcedureUseRejected` → `return ()`；即使不抛，也要有 `procedure_uses` 行才能产出指纹，而 C06 一次 `procedure_use` 都没有。Host 侧 `human_memory_v7.py:426-429` 只是把这个 `()` 塞进 `RecallContext`。

对照证据：C06-05 请求 `[semantic, procedure]` 只拿回 semantic `{"predicate":"外出素材预览"}`；C06-03 请求 `[semantic, procedure, episode]` 只拿回 semantic `{"predicate":"金额精度"}`。而 **C06-02 唯一 PASS 是因为模型额外调了 `procedure_discover`**，走的是完全另一条查询面（`backends/procedure_discovery.py`，0.6.25 已把 lifecycle 白名单扩到 active/reinforced，`:44` + `DISCOVERABLE_LIFECYCLE_STATES`），拿到 `{name: 排日程, steps:[先核时区, 再核日期重叠]}`（`run-01e/C06-02/scoring/C06-02/observation-transcript.json`）。

（注：任务书举的 C06-09 实为 NOT_SCORED——`approval-001.json` = `no_exact_tool_decision / BLOCKED`，`observation-route_audit.json` 为空数组，没有可用的路由证据；本文以 C06-03 顶替作第二个 FAIL 对照。）

### 2.2 acceptance 要求的是哪一种

逐字读 `simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/acceptance.md`：

- **HM-AC-4（:69）** 只说「五天短时域与长期认知召回由 `RecallPlan` **按类型、状态、隐私、时间和预算执行**」。它要求类型化召回**存在且受资格门约束**，没有承诺「请求 procedure 就一定能召回任一 active procedure」。
- **HM-AC-5（:70）** 对 procedure 的原文是「…并**持续检查工具/环境/版本适用性**」。这正是指纹门的语义来源：**召回面 = 已绑定且当前适用**，即「适用性召回」，**不是**「按记忆类型化召回所有 active procedure」。
- **HM-AC-8（:73）** 才给出数字门槛：「主模型 required-memory-type recall ≥90%」。这是**指标口径**，它约束的是「模型请求的必需类型是否被召回」。

结论：**HM-AC-4/5 要求的是「适用性召回」，不是「类型化召回任意 procedure」**；`DECISION-PROCEDURE-USE-CHAIN.md` §3 开头「不放宽 typed recall 的指纹门（S3 设计『applicable recall』语义正确，SDK `test_typed_recall_v6.py:944` 已冻结）」是与 acceptance 一致的正确设计口径。

**但语料 C06 的 gold 期望的是前者。** `run-01e/C06-05/scoring/C06-05/oracle.json` 的 `labels.required_types = ["semantic","procedure"]`，评分方式是「模型是否通过类型化召回拿到 procedure」。这就是冲突所在：**gold 把「必需类型」等同于「必须出现在 typed recall 的 fragments 里」，而设计上 procedure 在首次使用前根本不该出现在那里。** 于是 HM-AC-8 的 `required-memory-type recall` 指标在 C06 上量了一个设计上注定为 0 的量。

---

## 3. 唯一推荐的最小修复

### 3.0 修复原则

- **不动 SDK 的两个资格门。** prospective 的注册门是 S5c 的越权保护（HM-AC-5「SDK 可靠地产生触发候选及状态审计，但不越权执行」），procedure 的指纹门是 HM-AC-5「持续检查适用性」。放宽任一个都是拿产品语义换指标。
- **两条 FAIL 的性质不同**：C04 是**跑道 bug**（修跑道即可全部转 PASS），C06 是**gold 口径错**（修 gold 与跑道，不修产品）。

### 3.1 推荐修复（按优先级）

| # | 层 | 改动 | 量级 | 预期效果 |
|---|---|---|---|---|
| **A** | **语料跑道**（Host `backend/deskpet/quality/corpus_scoring_session.py:406-407`） | 不再整条 `close()`。改为：只停「模型分析」这一步（把 `MemoryAnalysisLane` 拆出 `pause_analysis()`，或在评分前显式 `await cognitive.prospective_lane.tick()` 一次并断言 `last_registration_error is None`）。最小写法是在 `close()` 之后、`_activate_human_memory_host_ports` 之前插入一次显式 tick，绕过 `_closing` | **源码 ~10 行 + 断言 ~5 行**；另加 1 个跑道自检（seed 后 `prospective_scheduler_registrations` 行数 == 种子 P 数） | C04 的 14 例 FAIL 中绝大多数直接转 PASS（run-01e 已证明这些例的实体消歧、跨年判断、边界表述都是对的）；同时消掉 90/90 的 `NO_ACTIVE_GENERATION`（短时域世代由同一条 lane 的 `PrimaryShortIndexWorker` 产出） |
| **B** | **语料 gold / 跑道**（C06） | 见 §3.2 | oracle 19 例改 1 字段 + 评分器 ~15 行 | C06 从「量一个注定为 0 的指标」变成「量真实的两步使用链」 |
| **C** | **提示词**（Host `backend/deskpet/execution/primary_context.py`） | 已在 `dda154d6` 加了 TaskScope↔Procedure 区分句；本轮再补一句「需要复用你保存过的流程时，先 `procedure_discover` 找到候选，不要指望它出现在 `context_route` 的记忆片段里」 | **2 行** | 把 C06-02 的偶发正确行为变成稳定行为 |
| — | SDK | **不改** | 0 | — |
| — | Host `context_route` / `human_memory_v7.py` | **不改** | 0 | 类型分发没有 bug，两条 lane 都进了 SQL |

**唯一推荐 = A + B + C 一起做**，其中 A 是 P0（单独做就能回收 14 例），B 是必须做否则 C06 会一直假 FAIL，C 是 2 行的顺手项。

### 3.2 若「不放宽指纹门」是正确设计，C06 的 gold / 跑道该怎么改

采用「**发现面 ≠ 召回面**」的二分（`DECISION-PROCEDURE-USE-CHAIN.md` §3 已确立），把 C06 的判定从「typed recall 出现 procedure」改为「模型走对了发现链」：

1. **oracle 字段拆分。** `oracle.json` 的 `labels.required_types` 里删掉 `procedure`（保留 `semantic`），新增一个并列标签 `required_procedure_access: "procedure_discover"`。这样 HM-AC-8 的 `required-memory-type recall` 指标回到它真正定义的对象（typed recall 的必需类型），不再被一个设计上不可达的类型污染。
2. **评分器新增一条判定。** C06 判 PASS 需同时满足：(a) `semantic` 经 typed recall 召回；(b) transcript 里存在一次成功的 `procedure_discover` 且其 `candidates` 命中种子 procedure 的 `memory_id`；(c) 终答复述的步骤与 `steps` 一致且未越权执行。C06-02 的现有证据正好是这条判定的黄金样本。
3. **对「只描述不做」类（C06-05/09 这种 `只描述不做`）**：`procedure_discover` 的返回带 `execution_authorized: false`，可以直接作为「不得执行」的硬断言，比现在的语义判读更可验证。
4. **若将来要覆盖「适用性召回」本身**（即指纹门放行的正例），需要另起一类跑道：先 `context_route(create_new)` 建 TaskScope → `procedure_use` 绑定 → 真实工具调用 → worker `observe_group` 绑定指纹 → **同一个 Run 内**再 typed recall 请求 procedure。这条链正是原生 r10 步 5 卡住的地方（`plans/2026-09-07-native-main-journey/NATIVE-R10-PROCEDURE-CHAIN.md`），**语料批次不适合承载它**，应留在原生 r11+。

### 3.3 顺带确认的两条「非缺陷」

- **`NO_ACTIVE_GENERATION` 不是产品缺陷。** 它是短时域（5 天会话索引）世代未激活的降级码（`sqlite_v5.py:3478-3484`），世代由 `PrimaryShortIndexWorker` 在同一条被关掉的 lane 里产出（`memory_ingestion_outbox.py:428-430, 442-444`）。修 A 之后应自动归零；修 A 后仍恒亮才需要单独立案。
- **多提 `episode` 类型（15.7%）** 在 C06 上很可能是模型对 procedure 拿不到东西的补偿性扩面。修 A/B 后必须重测该指标，否则会掩盖真实的类型选择偏差（与 run-01e 报告次级建议一致）。

---

## 4. 关键文件:行索引

| 事实 | 位置 |
|---|---|
| prospective 类型权限门（要求 accepted 注册 + trigger_hash 匹配） | `simple-harness-memory-sdk/src/simple_harness_memory/backends/sqlite_v5.py:5568-5591` |
| procedure 类型权限门（非 UNBOUND 且 ∈ Host 指纹集） | 同上 `:5545-5557` |
| 类型权限门在词面/向量打分之前执行 | 同上 `:5217-5232`（`_collect_typed_recall_candidates` 循环） |
| lifecycle 白名单（prospective 含 pending/rescheduled） | 同上 `:5658-5666` |
| revision=1 procedure 指纹恒为 UNBOUND | 同上 `:17656-17665` |
| 首次观察绑定指纹 | 同上 `:6331-6335` |
| `NO_ACTIVE_GENERATION` 定义（短时域世代） | 同上 `:3478-3484` |
| Host 把 `current_fingerprints` 注入 RecallContext | `simple_harness/backend/deskpet/memory/human_memory_v7.py:426-429` |
| `current_fingerprints` 在无 active scope 时返回 `()` | `simple_harness/backend/deskpet/memory/procedure_runtime.py:97-121` |
| 生产装配 `ProspectiveRuntimeLane` | `simple_harness/backend/deskpet/memory/runtime_composition.py:78-79` |
| 注册消费者只在 lane tick 里跑 | `simple_harness/backend/deskpet/memory/prospective_runtime.py:78-83` |
| lane 由 `MemoryAnalysisLane.start()` 拉起 / `close()` 关停且不可重启 | `simple_harness/backend/deskpet/memory/memory_ingestion_outbox.py:480-484, 481, 510-517` |
| **语料评分前关掉整条 lane（本文第一根因）** | `simple_harness/backend/deskpet/quality/corpus_scoring_session.py:406-407` |
| C04-12 唯一的 S5c 注册特判 | `simple_harness/backend/deskpet/quality/corpus_c04_prepare.py:75-80, 124-134, 143-155` |
| C06 procedure 全部为 revision=1 CREATE | `simple_harness/backend/deskpet/quality/corpus_c06_prepare.py:46-50, 148` |
| `procedure_discover` 生命周期白名单（0.6.25 已扩到 active/reinforced） | `simple-harness-memory-sdk/src/simple_harness_memory/backends/procedure_discovery.py:44` |
| HM-AC-4 原文（类型化召回按状态/隐私/时间/预算执行） | `simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/acceptance.md:69` |
| HM-AC-5 原文（持续检查工具/环境/版本适用性） | 同上 `:70` |
| HM-AC-8 原文（required-memory-type recall ≥90%） | 同上 `:73` |
| 「不放宽指纹门」设计口径 | `simple_harness/plans/2026-09-07-native-main-journey/DECISION-PROCEDURE-USE-CHAIN.md` §3 |
| 原生 r10 procedure 使用链仍未通过 | `simple_harness/plans/2026-09-07-native-main-journey/NATIVE-R10-PROCEDURE-CHAIN.md` |

## 5. 证据样本

| 例 | 判定 | 关键证据文件 |
|---|---|---|
| C04-03 | FAIL | `.local-test-evidence/2026-09-07/corpus-batch/run-01e/C04-03/scoring/C04-03/observation-route_audit.json`（单请求 `[prospective]` → `fragments: []`）、`setup.json`（P=2027-01-02 pending） |
| C04-07 | FAIL | 同路径 C04-07（P=09-09 14:00 pending，同样零返回 → 证伪时间窗假说） |
| C04-12 | PASS | 同路径 C04-12（唯一有 accepted 注册，prospective fragment 带 `trigger_at=1788917400.0`） |
| C06-05 | FAIL | 同路径 C06-05（请求 `[semantic, procedure]` → 只有 semantic） |
| C06-03 | FAIL | 同路径 C06-03（请求 `[semantic, procedure, episode]` → 只有 semantic）。**替代任务书列的 C06-09**——后者为 NOT_SCORED（`approval-001.json` = `no_exact_tool_decision / BLOCKED`，route_audit 为空） |
| C06-02 | PASS | 同路径 C06-02 的 `observation-transcript.json`（`procedure_discover` 返回 `procedure_draft_preview` + `execution_authorized: false`） |

## 6. 待主代理决定的事项

1. §3.1-A 的具体实现形式：是给 `MemoryAnalysisLane` 加 `pause_analysis()`，还是在 `corpus_scoring_session.py` 里插一次显式 `prospective_lane.tick()`。前者干净、后者改动更小（本文倾向后者，并配一条 seed 后断言）。
2. §3.2 的 oracle 改动会改变 C06 全部 19 例的 gold 语义，属于**语料口径变更**，需要在 `RESULTS.md` 留一条口径迁移记录，并重跑 C06 全类别。
3. 修 A 之后 C04 若仍有个别 FAIL，需单独复核（run-01e 已识别的模型侧行为：空召回重试、检索缺口处自拟提醒——C04-09/C04-11），那部分不在本备忘范围。
