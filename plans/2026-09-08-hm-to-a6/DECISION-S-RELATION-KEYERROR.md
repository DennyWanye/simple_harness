# 事件 S 决策记录：关系端点候选通道被自己的 run_id 打成 KeyError，`cognitive_relations` 恒为 0

> 义务：`HM-TO-A6` / 验收 A6-6（同一 plan 新建节点 + relation memory）
> 现象：原生 attempt 8（deepseek-v4-flash，Memory SDK 0.6.31，分析协议 v8 `a6c8b7c7`）
> 跑满 18 轮，`cognitive_relations` 0 行，`native.log` 里 20 条形状完全一样的告警。
> 分支：`worktree-rel-keyerror`（基线 `fb0b65da`）
> 证据（只读）：`.local-test-evidence/2026-09-09/native-a6-run8/primary-ui-a_tg7ppv/`

---

## 1. 复原：那个 UUID 是什么

告警本体：

```json
{"error_message": "'fb154920-9038-5bd4-8a4f-5c6875c2464a'", "error_type": "KeyError",
 "event": "memory.analysis_relation_candidates_unavailable key=…",
 "logger": "deskpet.memory.semantic_correction"}
```

20 个批次、20 个不同的 `evidence_set_key`，**同一个 UUID**。全库检索这个值：

| 位置 | 命中 |
|---|---|
| `human_memory_v7.db.analysis_batches.request_json` | `/run_id` 与 `/disclosure_context/run_id` |
| `evidence_envelopes` / `ingestion_receipts` / `llm_invocations` / `typed_recall_requests` | 同一个 run_id |
| 任何 head / 成员 / 关系表 | **无** |

所以它不是记忆 id、不是成员 key、不是 v7→v8 的键错配——它是**分析车道自己的 run_id**
（SDK 侧派生的 uuid5，跨批次逐字不变）。事故报告里「大概率是被 supersede/contest 的 head id」
的猜测与证据不符，此处按证据记账。

## 2. 根因

`semantic_correction._relation_candidates` 把 `request.run_id`（= 分析 run）交给
`ProcedureRuntime.current_fingerprints`，后者做 `SdkRunToolAuthorityRegistry.resolve(run_id)`。
这个注册表：

* 只装**活着的前台 Run**（`main.py:8657` 绑定）；
* `mark_terminal` 会把记录 `pop` 掉（`tool_authority.py:1343`）；
* 解析不到就 `raise KeyError(value)`——`value` 正是那个 UUID。

分析车道自己从来不在表里；而且它跑在前台 Run 终态**之后**：run 8 的第一轮
`foreground_run_transitions` COMPLETED 于 `1788896447.76`，对应
`post_turn_invocation_attempts.reserved_at` 是 `1788896448.24`（+0.48 s）。
换成前台 run_id 也一样是 `KeyError`。

`current_fingerprints` 只在**逐条 use 的循环内部**捕获 `KeyError`，`resolve` 在循环之外，
异常直接冒到 `_relation_candidates` 唯一那层 `except Exception`，于是：

**每一批分析都丢掉整条关系端点通道**（包括根本不依赖适用性指纹的 Prospective 端点），
审计里只剩 `relation_candidates_unavailable=True` 一个布尔值，没有任何理由。

模型这边的后果是确定的：v8 提示词写死「没有对应候选就不提这条关系」，
`procedure_candidates` 恒为 `[]` ⇒ 模型永不提关系 ⇒ `cognitive_relations` 恒为 0。

### 为什么既有测试看不见

`bind_tools` 只有生产装配调用。测试里 `self.authorities is None`，
`current_fingerprints` 走早退分支返回 `()`，`test_analysis_v8_existing_relation.py`
甚至把「`relation_candidates == []` 且 `unavailable is False`」钉成了通过条件。

## 3. 顺带挖出的两条更深的坑（都由本次正向用例量出来）

事件 L 的 §4.4 只在**空列表**上验证过这条通道。一旦它真的产出一条 Procedure 端点：

**坑一：`check()` 结构上不可能放行 Procedure。**
`check()` 把关系候选一起塞进 `manager.check_history_visibility`，而 SDK 在
`backends/history_visibility.py:342` 明确写死
`procedure_applicability_fingerprints=frozenset()`（注释：*"never reuse old runtime fingerprints"*），
于是 `_cognitive_recall_type_authority_allowed_unlocked` 对任何 Procedure 返回 False
→ `RECALL_AUTHORITY_STALE` → `history_source_stale` → 整批以
`analysis_candidate_no_longer_visible` 失败。实测：正向用例第一版三批全部 `dead_letter`。

**坑二：SDK 0.6.31 解析不了任何 Procedure 关系端点。**
Procedure 只有 `active`/`reinforced` 才进召回（`_cognitive_recall_state_allowed`），
而这需要三次独立成功观测；每次观测提交的新 revision 有 `cognitive_evidence_spans`，
**没有** `cognitive_classification_decisions` 行。
`_resolve_semantic_relation_payload_unlocked` 因此对 head 抛
`MemoryCorruptionError('relation endpoint classification is missing')`，**整批分析死掉**，
那一轮所有记忆（连 episode）全丢。实测：head revision 4，分类行只有 revision 1。

即：**放行 Procedure 端点比事故本身更糟**。这不是推断，是本分支正向用例跑出来的。

## 4. 修复

### 4.1 分析车道不再向「活着的 Run」提问

新增 `ProcedureRuntime.applied_use_fingerprints()`：读持久化的 `procedure_uses`，
只统计**观测已被 SDK 消费**（journal 阶段 `applied`）的那些 use 的
`applicability_fingerprint`。`runtime_composition` 把分析车道的
`procedure_fingerprints_getter` 改绑到它（签名由 `getter(run_id)` 收缩为 `getter()`）。

诚实记账，两半各说清楚：

* **保住**「从未被真实用过的 Procedure 不露面」——`ProcedureUseStore.bind` 是
  `procedure_uses` 的唯一写入者，且只算观测已消费的那些，仅绑定过或被 rejected 的都不算；
* **放弃**「而且它**此刻**仍然适用」——`current_fingerprints` 会用当前工具集+路由重算
  `current_snapshot` 并要求逐字相等，离开 Run 根本没有「当前工具集」可以重算。
  一个工具后来改名/换签/被撤的流程会退出前台召回，却仍然是分析车道的端点候选。
  代价可接受的理由：它只是**候选**，SDK 在 apply 时自己重解析每个端点，
  `check()` 还要再复核一次披露；且今天所有 Procedure 端点都被 §4.3 扣下。

`current_fingerprints` 本身也不再让 `resolve` 的 `KeyError` 冒出去（返回 `()`，fail closed），
但**加了一条 warning**：前台车道（`human_memory_v7.typed_recall`）的 run 本该是活的，
「Procedure 悄悄不再被召回」正是本事故要消灭的那类匿名失败。

### 4.2 两段式 + 具名理由码

`_relation_candidates` 拆成两段：

1. **适用性**：失败只花掉 Procedure 那一端（空集合正是 SDK 门读到的「没有 Procedure 合格」），
   召回照跑，Prospective 端点不受牵连；
2. **公开 typed recall**：只有它失败才把通道标记为 `unavailable`。

理由码写进候选快照的新键 `relation_candidate_reasons`（`bind_attempt` 会把整个快照哈希进 attempt）：

| 码 | 含义 |
|---|---|
| `relation_procedure_applicability_unavailable` | 指纹解析抛异常（`detail` 为异常类名） |
| `relation_procedure_applicability_absent` | 解析成功但没有可用指纹（F-L1 的显式化）；与上一条**互斥** |
| `relation_candidate_recall_unavailable` | 召回本身失败，通道降级 |
| `relation_candidate_result_truncated` / `..._confirmation_required` | 结果不可直接使用 |
| `relation_candidate_source_kind_unsupported` / `..._memory_type_unsupported` / `..._payload_unreadable` | 逐成员跳过（带 memory_id / revision） |
| `relation_candidate_endpoint_unverifiable` | 端点被扣下（`detail` 说明原因） |

成员渲染整段进了 per-member `try`：一个成员的 payload 坏掉不再终结整轮分析。

### 4.3 Procedure 端点按名扣下（§3 坑二）

`_endpoint_unverifiable` 对 Procedure 一律返回 `sdk_procedure_endpoint_unresolvable`，
在**下发之前**扣下，模型无从引用一个 SDK 解析不了的 key，代价只落在这一个端点上。
解除条件见 F-S1。

### 4.4 `check()` 失败而不是替换成一个假门

评审 MUST-FIX：原修复把 Procedure 候选从 `check_history_visibility` 的绑定里摘出来，
交给一个**什么都不读**的谓词。今天不可达，但 F-S1 一旦解除，
那个循环就变成 no-op，Procedure 端点会零可见性/披露/head/状态/哈希复核直接进
`authorize_plan`——一个装好引信的洞。改为：`check()` 里出现 Procedure 候选即
`analysis_candidate_no_longer_visible`（今天行为完全等价），
逼解除 F-S1 的人必须先设计真正的复核路径。

## 5. 测试

新增三个文件，**全部在基线 `fb0b65da` 上失败、在本分支通过**：

* `backend/tests/memory/test_analysis_relation_candidate_audit.py`（5 个）
  * 复现：补上生产才有的 `bind_tools`，主干侧得到
    `relation_candidates_unavailable=True` + `KeyError: '<uuid>'`（与 native.log 同形），
    修复后为 `False` + `[relation_procedure_applicability_absent]`；
  * 指纹解析失败被具名记录且不把通道标成不可用；
  * `current_fingerprints` 对未注册 Run 回答 `()` 而不是抛；
  * `applied_use_fingerprints` 只算 journal `applied` 的 use，仅绑定/被 rejected 的不算；
  * 被篡改的 `procedure_uses` 行 **不被吞**（`procedure_persisted_body_corrupt` 冒出去，
    由第一段审计成 `..._unavailable`）。
* `backend/tests/memory/test_analysis_relation_applied.py`（1 个，真实链路 ~12 s）
  真实前台 Run 三次独立成功执行流程 → `observe_group` 走公开 SDK → 流程进 `active`，
  `applied_use_fingerprints` 命中真实使用的指纹 → SDK 适用性门**确实放行**（快照里没有
  `..._absent`）→ Host 按名扣下（`relation_candidate_endpoint_unverifiable` /
  `sdk_procedure_endpoint_unresolvable`）→ **这一批照常 `applied`**。
* `backend/tests/memory/test_analysis_relation_prospective_applied.py`（1 个，~9 s）
  A6-6 在 0.6.31 上可达的那一半，走**同一条**通道与同一段修复代码：
  第一批建 Prospective → `prospective_lane.tick()` 落 accepted 调度登记 →
  第二批 Host 下发该端点，同时**注入指纹解析失败**（证明代价只落在 Procedure 那一端）→
  模型用 `source_operation_id` + `target_candidate_key` 提 `applies_to` →
  `cognitive_relations` 出现 1 行，指向已有记忆的 exact revision。

回归（主 venv，Memory SDK 0.6.31，pin 文件未动）：

| 套件 | 本分支 | 基线 `fb0b65da` |
|---|---|---|
| `tests/memory`（全量） | **703 通过 / 63 失败** | 696 通过 / 69 失败 |
| 失败集合差异 | 仅本次新增的 7 个用例（基线红、本分支绿） | — |
| analysis/semantic/recall 16 个文件 | 161 通过 / 4 失败 | **逐条相同** |
| procedure 13 个文件 | 72 通过 / 1 失败 | **逐条相同** |

既有红（两侧完全一致，均为已记录家族）：
`test_analysis_episode_time`（4 个，delayed/settled 参数化）、
`test_procedure_scope_runtime::test_three_real_scopes…`（F-L5 计时家族）、
s5b/s5c 迁移、prospective ack、short index、`/Users/denny` 路径、
`test_real_memory_only_forget_…`、`test_wemm_lazy`、`test_trusted_disclosure_races` 等共 63 条。
`deskpet` 全包导入 0 失败。

## 6. 独立评审（opus，只读）与处置

| 评审 MUST-FIX | 处置 |
|---|---|
| 1. `check()` 的 Procedure 旁路是「装好引信的洞」，且注释描述了不存在的代码 | **已改**，见 §4.4 |
| 2. `applied_use_fingerprints` 吞掉 `procedure_persisted_body_corrupt`，比它替代的函数更宽松 | **已改**：`_checked` 直接冒出去，由第一段审计；补用例 |
| 3. `sqlite_master` 守卫把 schema 纪元违规变成 `()` | **已删**。生产 state.db 必然有该表；harness DB 没有，就该被记成 `..._unavailable`（用例里改为先 `initialize_procedure_state_db`） |
| 4. 替代信号弱于 §4.4 boundary 3，且 docstring 说过头 | **已改**：改名 `observed_use_fingerprints` → `applied_use_fingerprints`，只算 journal `applied`；docstring 逐条写清保住哪一半、放弃哪一半（本备忘录 §4.1 同步） |
| 5. `current_fingerprints` 在前台车道静默丢弃 | **已改**：加 `memory.procedure_applicability_run_not_live` warning |
| 6. 「代价只落在 Procedure 那一端」没有真正被证明 | **已改**：在 prospective 用例里注入同一处失败，断言端点仍被下发并写进 `cognitive_relations` |

评审 NITS 一并处理：replay 稳定性说法改成「持久化快照才是重放来源」；
`ORDER BY created_at DESC LIMIT 128` 的 128 窗口不构成确定性，docstring 不再这么讲；
成员循环加 per-member `try`；`bodies[0]` → `bodies[-1]`；
`relation_procedure_applicability_absent` 与 `..._unavailable` 互斥；
`applied_use_fingerprints` 与兄弟函数一样先 `BEGIN`。
未采纳的一条：评审认为那次 `bind_tools` 现在是惰性的、用例名过强——保留为对主干的变更探测器，
已在 docstring 里注明它是惰性的。

## 7. 边界与未做

* **没有改 SDK**，没有 `uv sync`，没有构建 app，没有跑原生。
* A6-6 的 Procedure 形态在 SDK 0.6.31 上**仍然不可达**（F-S1）。本次把它从
  「匿名 KeyError + 0 关系」变成「具名扣下 + 通道其余部分可用 + Prospective 形态端到端可达」。
* `applied_use_fingerprints` 放弃了「此刻仍适用」这一半（§4.1），今天没有实际暴露面
  （所有 Procedure 端点都被扣下），F-S1 解除时**必须**连同它一起重新裁定。

## 8. Followups

* **F-S1（P0，SDK）**：观测提交的 Procedure revision 没有 `cognitive_classification_decisions` 行，
  导致 `_resolve_semantic_relation_payload_unlocked` 判定 head 损坏；同时
  `check_history_visibility` 对 Procedure 永远 stale（`history_visibility.py:342`）。
  两条都修掉，才谈得上放开 §4.3 的扣留；放开时必须同时给 `check()` 补真正的复核路径（§4.4）
  并重新裁定 §4.1 的取舍。这也是 F-L1 的真实深度。
* **F-S2**：`_relation_candidates` 用 `request.run_id` 构造 `RecallContext`，
  而指纹来自另一组事实（持久化使用）。今天自洽（SDK 只把指纹当集合用），
  但契约上「这个 run 的当前适用性」这个字段名已经不准确，S3 契约值得补一句离线车道的口径。
* **F-S3**：`test_analysis_relation_applied.py` 依赖「`create_draft` 的 fixture 证据没有
  outbox 血缘 ⇒ 第一批 `dead_letter`」这个次序。换 fixture 会静默改变批次编号。
* **F-S4**：`tests/sdk_adapters/s5b_closure_harness` 造出来的 state.db 不是生产启动纪元
  （缺 Procedure/S5c 域），三个新用例都要各自 `initialize_*_state_db` 补齐。
  harness 侧统一升级会让这类用例少一段噪声。
