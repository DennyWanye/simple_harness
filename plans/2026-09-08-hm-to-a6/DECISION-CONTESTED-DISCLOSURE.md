# 裁决：争议值必须带着两个候选值和「先确认」指令进 Context（事件 O）

> 义务：`HM-TO-A6` A6-8 后半 / NC-4；契约 `acceptance.md` HM-S3
> 「含糊时不选边，依赖该值的任务要求确认」，`slices/S3-cognitive-systems-recall.md`
> §5.2 / §5.3（只读引用）。
> 证据：`.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1/userdata/data/`
> （`human_memory_v7.db`、`state.db`、`simple-harness-sdk/execution-v6.sqlite3`）。
> 工作树：`worktree-contested-disclosure`。日期：2026-09-08。

---

## 1. 现场：模型当时到底看见了什么

### 1.1 争议本身是真的落库了

`human_memory_v7.db`：

| revision | created_at | lifecycle_state | conflict_status | object_value |
|---:|---|---|---|---|
| 1 | 1788857654.12 | active | uncontested | Python 3.12 |
| 2 | 1788859022.26 | active | uncontested | Python 3.13 |
| 3 | 1788859083.95 | active | **contested** | 3.12 |

`cognitive_memory_heads.current_revision = 3`；`cognitive_conflict_groups` 1 行，
`incumbent_revision=2` / `challenger_revision=3`；`cognitive_relations` 有
`evolution/contests 3→2`。

> **口径更正**：任务书写的是「current revision 2 = Python 3.13 且 head contested」。
> 实际 head 是 **revision 3**（挑战值「3.12」），revision 2（「Python 3.13」）是
> incumbent。`cognitive_memory_heads` 表上**没有** `conflict_status` 列，
> `conflict_status` 是 `cognitive_memory_revisions` 的每-revision 列。

### 1.2 T22 那一刻，争议还没写进库

turn 22 的两次 provider 调用 `claimed_at` 分别是 `1788859048.80` / `1788859067.48`，
而 contest 操作 `op-contest-proofreading-python-version` 的 `created_at` 是
`1788859083.95` —— 比 T22 最后一次发送**晚 16.5 秒**。也就是说 T21 的分析车道在
T22 已经答完之后才结算。这是一条**独立**的时序缺陷（分析车道属主另有其人），
本裁决不处理，只记录（见 §6 F-O-2）。

### 1.3 那一次实际投影出去的东西

`provider_invocations.request_json`（`bbac7a4e…`）第 8 条 tool 消息里，7 个 fragment
的键集合逐字为：

```
bytes / history_binding / lane / memory_type / payload / payload_hash /
privacy_class / ref / score / source_task_scope_ids / tokens
```

第 1 个 fragment 的 payload 就是 `{"object_value": "Python 3.13", "predicate":
"proofreading_python_version", ...}`。

**没有 `conflict_status`，没有 `verification_state`，没有 revision 号，没有任何一句
关于「争议」的话；system prompt（`PERSONA`）里也一个字都没提 contested。**

模型的回答（`response_json`）第 4 段逐字：「**按 Python 3.13 执行这套校对流程。**」
第 3 段还说「我检索到的记忆里没有找到任何 3.12 的条目」。

### 1.4 争议落库之后会怎样：比「没告诉」更糟

拿 run4 的库副本 + 已安装 SDK（memory 0.6.28）跑一次真实 typed recall
（`memory_types=[semantic, episode, procedure]`，两条不同 query 各试一次）：

```
outcome: needs_user_confirmation
selected items: 0
confirmation groups: 1
  member rev 2  {"object_value": "Python 3.13", ...}
  member rev 3  {"object_value": "3.12", ...}
PROJECTED FRAGMENTS: 0
```

契约 S3 §5.3：「普通选择仅允许 `uncontested|resolved`；**contested 只能走完整
group confirmation**」。SDK 因此把**整次** typed recall 短路成
`build_host_confirmation_execution`（`core/recall.py:514-645`）：`items=()`，只回
`confirmation_groups`。Host 的 `project_recall_fragments` 只遍历
`execution.result.items`，于是**投影出 0 个 fragment**。

结论：争议一旦落库，模型收到的是一次**空召回**——它读作「你从来没存过这件事」，
连「存在冲突」都不知道。这比 T22 当时（争议未落库、拿到旧值）更危险，而且
**一个未解决的 group 会把整条 semantic/episode 召回车道全部黑洞掉**，与 query 无关
（两条不同 query 实测同样 0 fragment）。

---

## 2. 判定：这是 Host 缺陷

SDK 已经把该给的都给了：`RecallDecisionOutcome.NEEDS_USER_CONFIRMATION`、
原子 `RecallConfirmationGroupV4`（`recall_protocol_v4.py:305`）、每个成员的
`public_payload` / `effective_privacy_class` / exact revision，
以及 `RecallFragmentAuthorityBindingV1` 里现成的
`conflict_group_id`/`confirmation_hash`/`result_group_hash` 三个槽位
（`recall_protocol_v4.py:1631-1633`）。

Host 侧对 `confirmation_groups` 的引用全仓只有一处
（`backend/deskpet/memory/semantic_correction.py:313`，且只用来判空），
`project_recall_fragments` 从未读过它。**是 Host 把 SDK 已经交付的完整争议载体丢了。**

---

## 3. 方案取舍（本条为技术裁决，按 CLAUDE.md 用户口径自行判定，不上问）

| 方案 | 做法 | 结论 |
|---|---|---|
| A 只发通知不给值 | 只说「有争议」，不给两个候选值 | **否**。契约要求「不选边」+ 用户能被问到点上；不给值模型没法把问题问清楚，用户还得自己回忆两个版本分别是什么。 |
| B 把 confirmation member 投成 `fragments[]` 并接 typed-use 绑定链 | 每个成员一条 fragment，`item_id=member.item_id`、`item_hash=result_member_hash`，走 `page_typed_recall_result` 的 `RecallPageConfirmationGroupBindingV1` | **本轮否**（见下）。这是**长期正确**方向。 |
| C 由 Host 组装一条 `conflict_notice`，与 `fragments[]` 并列 | 通知里带 group id、两个候选值（角色 + exact revision + payload_hash）、双语「先确认」指令、稳定 reason code | **采纳** |

**B 为什么本轮不做**：已安装 memory SDK 0.6.28 的历史可见性车道解析不了
confirmation member。
`simple_harness_memory/backends/history_visibility.py:299`：

```python
item = next((x for x in result.items if x.selected_item.item_id == binding.item_id), None)
if item is None or item.result_item_hash != binding.item_hash:
    return "history_binding_mismatch", None
```

只查 `result.items`，不查 `result.confirmation_groups`。而
`primary_dependencies.read_run_dependencies` 有多个调用点**不带**
`consumed_occurrences`（`foreground_runtime.py:1844`、`primary_context_pages.py:249`、
`closure_request_guard.py:157/171`、`task_scope/mutation_disclosure.py:159`），
这些路径会把 fragment 的 `history_binding` 当普通 recall 依赖送进
`check_history_visibility` → 必然 `history_binding_mismatch` →
`primary_history_disclosure_rejected`，**下一轮请求直接被拒**。
context-use 授权车道倒是支持成员绑定（`sqlite_v5.py:4683-4712` 显式把
`member.member.item_id` 收进 `expected` 并强制整组供齐），所以 B 是「一半通、一半断」，
本轮做只会换一个更难查的红。记为 SDK followup（§6 F-O-1）。

**C 的披露安全性**：通知里的内容不是 Host 新造的。confirmation execution 在返回前
已经过 SDK 的完整资格/suppression/disclosure 门
（`sqlite_v5.py:4093-4106` 对每个成员跑
`_validate_recall_context_use_sources_unlocked`），是**已获授权披露给本轮这个
DisclosureContext** 的公开 payload。Host 在投影时**再查一次** privacy class
（`_ELIGIBLE_PRIVACY_CLASSES = {public, personal}`），并按 S3 §5.2
「任何一侧不可见…整组、双方、candidate count 与"存在冲突"均不泄露」做**整组原子**：
只要有一个成员不合格，整组连同「存在冲突」这件事一起不披露。
disclosure 规则（无 redacted 内容、audience/recipient 全由 Host 构造）一字未改。
先例：`trigger_local`、`procedure_hint` 同样是渲染在 SDK 公开 payload **旁边**的
Host 字段，payload 与 `payload_hash` 逐字不变。

---

## 4. 落地

### 4.1 `backend/deskpet/memory/human_memory_v7.py`

- 新增 `CONTESTED_DISCLOSURE_REASON = "recall_value_contested_requires_user_confirmation"`
  与双语 `CONTESTED_DISCLOSURE_MESSAGE`（human_memory_v7.py:645-680）。
- 新增 `project_contested_confirmation(lanes)`（human_memory_v7.py:682-733）：遍历
  `execution.result.confirmation_groups`，逐成员复查 privacy class，**任一不合格整组丢弃**；
  角色按 **exact revision** 判定（较小的是 incumbent，契约 §5.2 「恰好两个有序
  `cognitive_conflict_members`（incumbent=`rN`、challenger=`rN+1`）」）——刻意不按 ordinal，
  免得成员次序万一漂移就把两边说反；带 exact
  `revision`、`value`、`payload_hash`、`privacy_class`。无可披露 group 时返回
  `None`（未争议路径逐字不变）。
- `project_recall_fragments` 给认知车道 fragment 补
  `"conflict_status": "not_contested"`（human_memory_v7.py:795-797）。
  **不写 `"uncontested"`**：SDK 的普通门放行 `{uncontested, resolved}` 两种
  （`sqlite_v5.py:5856`），Host 只能断言「当前没有未决争议」，不能断言「从未被争议过」。
  短时域 chunk 不是 head，不带这个字段。

### 4.2 `backend/deskpet/sdk_adapters/context_route.py`

- `_memory_standalone` 调 `project_contested_confirmation`，有则把 `conflict_notice`
  并入 extras（context_route.py:466-497）。extras 参与既有的
  `public_result_hash = canonical_sha256(result)`，`ContextRouteReceipt` 与 typed-use
  carrier 一字未改。
- `_commit_receipt` 新增 `recall_conflict` 参数，把稳定 reason code + group id +
  memory_type + exact revisions 写进 `context_route_tool_invocations.detail_json`
  （context_route.py:238 / :293-302）。**审计只留归属，不留用户的争议值第二份拷贝。**

### 4.3 `backend/deskpet/execution/primary_context.py`

`PERSONA` 补一段（+约 430 字节，A6-3 预算项已有独立红，见 RUN-04-RESULT）：`conflict_notice` 的含义、两个候选都要报出来、先问用户、
不得采用任一值、不得给执行结论；并明确「空 fragments + conflict_notice ≠ 没存过」。

### 4.4 历史 / `context_page_in`

`primary_context_pages.project_history_group` 是逐字引用（>16 KiB 的 tool 消息才
被 `primary_tool_result_summary_v1` 摘要），`context_page_in` 分页的也是原始字节。
通知约 1.2 KiB，远低于 16 KiB 阈值，**随工具回执原样进历史，无需另改**。

---

## 5. 验证

### 5.1 真实库探针

用 run4 的 `human_memory_v7.db` 副本（`.local-test-evidence/` 只读，先复制）+ 装机
SDK 跑真实 `typed_recall`：新投影产出

```
fragments: 0
conflict_notice.reason = recall_value_contested_requires_user_confirmation
candidates = [incumbent rev2 "Python 3.13", challenger rev3 "3.12"]
```

### 5.2 T22 provider 请求真实重放（DeepSeek `deepseek-v4-pro`）

取 `provider_invocations.request_json`（`bbac7a4e…`）逐字重放，只替换
① `PERSONA`、② 那条 `context_route` 工具回执（由 §5.1 的真实召回结果生成），
其余消息/工具定义不动；每次最多 3 个 provider turn（模型若再发
`context_route`，回喂同一份 Host 投影——已实测争议开着时任何 query 结果相同）。
重放保真度的两点偏差，均**不利于**新投影（即真实链路只会更强）：① 重放的工具回执
没带 `procedure_hint`（真实 Host 在 `memory_types` 含 procedure 且无 procedure 项时会追加）；
② `max_tokens=4096`，原始请求未设上限。

| | 结果 |
|---|---|
| **对照（旧投影 + 旧 PERSONA）×3** | 复现事故：2/3 明确输出「**按 Python 3.13 执行**」，1 次 `finish=length` 未收敛。 |
| **新投影 ×3** | **3/3 要求确认**，全部报出两个候选（incumbent Python 3.13 rev2 / challenger 3.12 rev3），全部明说「在你确认之前我不下执行结论」，**0 次**给出执行结论。 |

### 5.3 单元/集成控制

- `backend/tests/memory/test_contested_recall_disclosure.py`（9 控）：争议披露两值、
  双语指令、**单侧不合格整组不披露**（两个方向各一）、兄弟 group 不被株连、
  不完整 group 不披露、未争议路径 `conflict_status=not_contested`、
  短时域不冒充 conflict、无 `confirmation_groups` 属性不抛。
- `backend/tests/sdk_adapters/test_context_route_tool.py` 新增 4 控：回执带
  `conflict_notice` 且不进 `ContextRouteReceipt`、审计 `recall_conflict` 带稳定
  reason code **且不含候选值原文**、未争议无通知无审计行、`PERSONA` 断言。

---

## 6. A6-7：是验证器缺陷，不是 SDK 缺陷

**裁决：`cognitive_memory_revisions.lifecycle_state` 是每-revision 的不可变快照；
「哪个 revision 生效」只由 `cognitive_memory_heads.current_revision` 表达。
`scripts/native/a6_verify.py` 的 A6-7 判据错了。**

依据：

1. `simple_harness_memory/backends/schema_v5.py:572-577` 挂着
   `cognitive_memory_revisions_immutable_update` / `_immutable_delete` 两个
   **无 WHEN 子句、无列限定** 的 `RAISE(ABORT)` 触发器（run4 库的 `sqlite_master`
   里实测存在）。SDK **在物理上不可能**回头把旧 revision 改成 `superseded`——那会
   直接 abort 整个 strict 事务。全包 grep `UPDATE/DELETE cognitive_memory_revisions`
   零命中。
2. 写路径一律 insert-only + head CAS：新 revision INSERT 在
   `sqlite_v5.py:8041-8075`（`lifecycle_state` 绑的是 `operation.lifecycle_state`，
   即**新** revision 自己的状态，:8062），head CAS 在 `sqlite_v5.py:8440-8456`。
3. 召回资格按 head 连接，不按旧 revision 的 lifecycle：
   `sqlite_v5.py:2915-2924`（`... AND r.revision=h.current_revision`）、
   :4816-4829、:4939-4973、:5391-5405、:6316-6320；
   `_cognitive_recall_state_allowed`（:5846-5882）只评估传进来的那一行，
   而调用方喂的一律是 head 行。关系端点更硬：
   `sqlite_v5.py:10312-10313` 直接 `relation_endpoint_revision_stale`。
4. 契约 `slices/S3-cognitive-systems-recall.md:152`：「所有长期普通候选先要求
   exact principal、**exact current head**」——head 身份是第一道门；
   同文件 :149「永不把 head 回滚到旧 revision」。
5. `superseded` 这个 lifecycle 确实在用，但它是 `SUPERSEDE` 写入的**新** head
   revision 自身的状态（`core/mutations.py:423-425`
   `mutation_supersede_requires_superseded`），而 `REVISE` 被禁止写终态
   （`core/mutations.py:435-436`）。run4 里 rev2 由 revise 产生（`amends` 边），
   rev3 由 contest 产生（`contests` 边），三行全 `active` + head=3 **正是契约形状**。

因此 `item_a6_7` 改判（`scripts/native/a6_verify.py:1128-1250`）：

- head 必须指向最新 revision；
- head revision 的 `lifecycle_state` 必须落在合法 head 终态集合内
  （active/amended/reinforced/superseded/pending/triggered/in_progress/rescheduled）；
- 每一次 head 前进（rN，N≥2）必须有一条 `evolution` 血缘边 `rN → rN-1`；
- 「图谱只显示新 revision 一条 active edge」归 A6-9 判，本项不越界。

在 run4 真实证据上：A6-7 由 **FAIL → PASS**
（`heads=13`、`memories_with_multiple_revisions=1`、`lifecycle_states_observed=["active"]`、
`evolution_relation_kinds=["amends","contests"]`）。
控制：`backend/tests/native/test_a6_verify_a6_7.py`（7 控，含 run4 真实形状、
head 未前进、缺血缘边、supersede 终态、非法 head lifecycle、单 revision
INCONCLUSIVE、`--selftest`）。

---

## 7. 遗留（followup，本轮不做）

- **F-O-1（SDK）**：`simple_harness_memory/backends/history_visibility.py:299`
  的 `_recall` 只在 `result.items` 里找 `HistoryRecallBinding.item_id`，不查
  `result.confirmation_groups`，导致 confirmation member 无法进入历史可见性车道。
  修掉之后 Host 才能把争议候选值投成正经的 `fragments[]` 并接完整绑定链（方案 B）。
- **F-O-2（Host 分析车道，非本工作树属主）**：T21 的 contest 在 T22 答完 16.5 秒后
  才落库。争议期的依赖问句必须等分析车道结算，或在同一轮内可见。
- **F-O-3（SDK/契约口径）**：一个未解决的 conflict group 会把**整次** typed recall
  短路成 confirmation-only，与 query 无关——同一轮里其它完全无关的语义/情节记忆
  （如「校对结果存到外接硬盘」）也一并召不回来。重放里 3/3 都因此答不出 T19 的
  存储位置。是否应按 memory/predicate 粒度短路，需契约明确。
