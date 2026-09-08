# 事故 F 决策备忘：`RECALL_AUTHORITY_STALE` 击杀整个 Run

> 日期：2026-09-08 ｜ 义务：HM-TO-A6 原生真实模型验收
> 相关 SDK：`simple_harness_sdk 0.7.10` / `simple_harness_memory_sdk 0.6.26`
> （安装快照 `.local-test-evidence/2026-09-07/installed-h0710-m0626-s0313`，与 venv 内容逐字节一致）

---

## 1. 证据

- `.local-test-evidence/2026-09-08/native-a6-b3682fe1/primary-ui-hv9k7ncq/native.log`
  - `05:40:44.545 provider_attempt.succeeded`（模型返回 1 个 tool call）
  - `05:40:44.571 tool_attempt.started` → 中间是短时程/认知向量的 embedding 批次
  - `05:40:46.066 tool.effect_settled` / `tool_attempt.succeeded` —— **工具是成功的**
  - `05:40:46.222 sdk_run_driver_failed error_type=MemoryValidationError error_message=RECALL_AUTHORITY_STALE`
  - `05:40:46.222 run.fail` → `run.terminal`
- 触发轮：HM-TO-A6 turn 15「记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。」
  该轮模型走 `context_route(route=memory_standalone)` 做定型召回，与 turn 14 异步分析车道的
  记忆落库并发。
- 用户数据佐证 `.local-test-evidence/2026-09-08/native-a6-b3682fe1/primary-ui-xmqudtzt/userdata/data/human_memory_v7.db`：
  `recall_authority_events` 已累计 26 个 epoch，事件类型混杂
  `cognitive_memory_changed` / `cognitive_vector_generation_changed` /
  `short_horizon_projection_changed` / `short_horizon_generation_changed`
  —— 说明 epoch 在一次会话里被多条车道高频推进，不是偶发。
  同库 `typed_recall_results` 18 行、`recall_context_use_receipts` 只有 1 行，
  也侧面说明"召回做了很多、真正被用途围栏放行的很少"。

## 2. 根因（两个同码不同址的围栏）

`RECALL_AUTHORITY_STALE` 这个字符串在 Memory SDK 里有两类抛出点，必须分开处理：

### F-A 召回执行内竞态（工具内）

`simple_harness_memory/backends/sqlite_v5.py`
- `:3863` 采集前读 `(epoch, policy_hash)`
- `:3902`、`:4007` 在写锁内再读一次，若变了就 `raise MemoryValidationError("RECALL_AUTHORITY_STALE")`
- `:3922`、`:4022` 还会调用 `_validate_recall_context_use_sources_unlocked`，
  该函数在 `:4669/4676/4700/4716/4722/4729/4737` 也抛同一个码

Host 调用链：
`backend/deskpet/memory/human_memory_v7.py:460`（`typed_recall`）
→ `backend/deskpet/operation_audit/memory_attempts.py:249`（`MemoryAttemptJournal.execute_typed_recall`）
→ SDK `execute_typed_recall`。

**修复前行为**：异常冒泡到 `backend/deskpet/sdk_adapters/context_route.py:360-365` 的宽泛
`except Exception`，被折成 `context_route_adjudication_failed` 工具级拒绝。
不失败 Run，但**召回直接丢失、没有任何重试**，模型只会看到一个通用失败码。

### F-B 用途围栏（工具之后，本次事故的实际杀手）

`simple_harness_memory/backends/sqlite_v5.py:4505-4513`
`authorize_recall_context_use` 读当前 `(epoch, policy_hash)`，与**已落库的召回结果**
`result.authority_epoch / result.policy_hash` 比对，不等（或已过期）即抛
`RECALL_AUTHORITY_STALE`。

Host 调用链：
`backend/deskpet/sdk_adapters/typed_context_use.py:253`（`ProductTypedContextUseAuthority.authorize_recall_context_use`）
← Harness SDK `simple_harness/execution/dispatch.py:442`（`_authorize_context_use`，在 `dispatch.py:387`
被 `invoke` 调用，组装下一次 provider 请求时执行）
← `simple_harness/runtime/drivers/react_loop.py:521` `services.provider.invoke`（**无任何 except**）
← `simple_harness/runtime/kernel.py:2963` → `RunState.FAILED`。

时间线完全吻合：工具 46.066 返回 → 46.222 组装下一次 provider 请求时授权用途 → 抛错 → `run.fail`。
中间那 ~160ms 里，turn 14 的分析车道/短时程索引把 epoch 推进了一格。

**结论**：F-B 是本次 Run 失败的直接原因；F-A 是同一竞态的另一半，目前虽不杀 Run 但会静默丢召回。

## 3. 方案比较与裁定

| 方案 | 适用 | 裁定 |
|---|---|---|
| **A. 有界重采（re-collect）** | F-A | **采纳**。SDK `_admit_typed_recall_request`（`sqlite_v5.py:5039`）以 `plan.idempotency_key` 为键：请求行已存在、`request_hash` 一致、还没有 terminal 时，会开出下一个 `attempt_ordinal` 重新采集。所以用**完全相同**的 principal/context/plan/now 再调一次，就是 SDK 自带的重试语义 —— 不可能产生第二条持久召回请求/结果/决策/terminal。设计文本也是这么写的：S3 slice §5.5「same key+same hash：terminal 后直接返回 exact stored bytes；**unexpired dangling attempt 从零 durable candidate state 重跑**；没有持久化半 candidates」。 |
| **B. 串行化（分析落库 vs 在飞召回）** | F-A + F-B | **否决**。epoch 由 8 个抛点推进（`sqlite_v5.py:1861/2350/2510/2701/3688/6682/7037/8528`：抑制决策、短时程投影重建、短时程世代重建、认知向量世代、短时程清理、procedure 观测、prospective 信号、记忆变更套用）。其中记忆套用发生在 SDK 自己的 `DurableMemoryJobRunner` 事务里，Host 没有闸门；短时程索引 worker 是常驻的，若为前台召回让路会互相饿死；procedure/prospective/suppression 又是前台触发的，闸门根本盖不全。为一个良性竞态引入跨车道全局锁，风险远大于收益。 |
| **C. 在用途围栏处重采** | F-B | **不可行**。召回结果的 `result_id/result_hash` 已随工具回执写进对话、并被 provider 请求指纹覆盖；epoch 单调递增，重试永远不会成功；SDK 也没有"重新围栏一个已存结果"的公开 API，而 Host 明令禁止直读 SDK 私表（`typed_context_use.py` 文件头契约）。 |
| **D. 在 `snapshot_intents` 丢掉过期召回片段** | F-B | **否决**。片段文本仍在消息里，却没有用途授权 —— 正是这道围栏要防的披露完整性问题。 |
| **E. 复用 `MandatoryContextActionRequired` 做同 Run 有界修复** | F-B | **本版不可行**。`simple_harness/execution/context_action.py:13` 把 `MandatoryContextRejectionV1.reason` 硬限定为 `pending_prospective_occurrence`，且 react_loop 只在"无 tool call 的终局路径"接住它（`react_loop.py:657/713`），接不到 `_authorize_context_use`。要走通必须改 Harness SDK。 |

**最终裁定**：
1. F-A 用方案 A（有界重采，默认 2 次重采 / 共 3 次尝试），每次尝试独立审计，用尽后抛稳定 Host 码，
   由 `context_route` 工具边界收敛成**工具级拒绝**，绝不上升为 Run 失败。
2. F-B 在 Host 侧不可修复，只做**可归因收敛**：把裸 SDK 字符串换成稳定 Host 码
   `recall_context_use_authority_stale` + 无载荷日志，并明确记录"更干净的做法在 SDK"。
3. 顺带收益：F-A 的重采让返回给模型的召回结果总是带**最新** epoch，
   把 F-B 的暴露窗口从"整段召回时长 + 交接"压缩到"工具返回 → 用途授权"的几十~几百毫秒。

## 4. 实现

新增 `backend/deskpet/memory/recall_authority.py`：

- `RECALL_AUTHORITY_STALE` / `MAX_RECALL_AUTHORITY_RECOLLECTS = 2`
- `is_recall_authority_stale(error)`：只认 `type(error) is MemoryValidationError and str(error) == "RECALL_AUTHORITY_STALE"`，
  不做模糊匹配，避免把别的校验错误一起重试。
- `execute_typed_recall_recollecting(journal, manager, ...)`：有界重采循环。
  - 每次尝试都走 `MemoryAttemptJournal.execute_typed_recall` → **一次尝试一行审计**。
    重试循环刻意放在 journal 之外：`memory_attempts.py` 文件头写明它"是记录者、永远不是执行/重试权威"。
  - 参数逐次原样透传（尤其 `now`）：换 `now` 会改 `context.expires_at` 与请求摘要，
    被 SDK 判成 `IDEMPOTENCY_CONFLICT`。
  - `asyncio.CancelledError` 立即上抛，取消永远不是重采机会。
  - 预算用尽抛 `RecallAuthorityStale(code="recall_authority_stale")`（`RuntimeError` 子类，
    带 `caller`/`attempts`，`__cause__` 保留原 SDK 异常便于排障）。
- `RecallContextUseAuthorityStale(code="recall_context_use_authority_stale")`：F-B 专用，不重试。

接线：

| 文件 | 改动 |
|---|---|
| `backend/deskpet/memory/human_memory_v7.py:460` | 前台 `typed_recall` 改用 `execute_typed_recall_recollecting(caller="foreground_recall")` |
| `backend/deskpet/memory/semantic_correction.py:174` | 分析候选车道同样改用有界重采（`caller="analysis_candidates"`），它同样与其它落库并发 |
| `backend/deskpet/sdk_adapters/typed_context_use.py:253` | 用途围栏的 `RECALL_AUTHORITY_STALE` → `RecallContextUseAuthorityStale` + 无载荷 `warning`（只记 run_id/turn_id）；其它异常原样上抛 |

**没有改** `context_route.py`（本轮由其它 worktree 的 agent 持有）。
它现有的 `except Exception` 已经把 F-A 用尽后的异常收敛成工具级拒绝
`context_route_adjudication_failed` —— 稳定、fail-closed、不杀 Run，需求已满足。
只是这个码是通用码而非专属码：该分支的注释明确写着
「Exception messages/codes can contain provider or source text」，
所以它**故意**不透传异常自带的 code。若要对模型暴露专属码
`context_route_recall_authority_stale`，需要在那个文件里加一条白名单常量映射
（3 行），属于该文件持有者的设计决定，本轮以 handoff 形式留下（见 §7）。

## 5. 测试

新增 `backend/tests/memory/test_recall_authority_stale.py`（8 例，全绿）。
竞态用**真实安装的 SDK**复现：包住后端真实的 `_collect_typed_recall_candidates`，
在采集刚结束、SDK 还没进写锁复核 epoch 的那一刻，通过 SDK 自己的
`_advance_recall_authority_unlocked`（真实 `recall_authority_events` 行 + head CAS）推进 epoch。

| 用例 | 证明 |
|---|---|
| `test_sdk_fence_is_reachable_and_recognized` | 复现手法确实触发了 SDK 真实围栏（`str(exc) == "RECALL_AUTHORITY_STALE"`） |
| `test_recollection_succeeds_and_keeps_one_durable_recall` | 推进 1 次 → 采集 2 次、召回成功；审计三行 `raised/returned/returned`；**重放同一请求返回同一 `result_id`/`result_hash`/`decision_id`** → 无重复召回副作用 |
| `test_recollection_is_bounded_and_becomes_a_stable_rejection` | 每次都推进 → 恰好 3 次尝试、3 行审计全 `raised`、抛 `RecallAuthorityStale(code=...)` |
| `test_recollection_bound_is_configurable_and_validated` | `max_recollects=0` 只试 1 次；`-1` 直接 `ValueError` |
| `test_cancellation_is_never_recollected` | 取消只调用 1 次，立即上抛 |
| `test_exhausted_stale_is_a_tool_rejection_never_a_run_failure` | 走**真实** `ContextRouteToolService.handle_context_route`：返回 error dict 而非抛异常，`context_route_tool_invocations.verdict='rejected'`，回执里不含 `RECALL_AUTHORITY_STALE` 原文 |
| `test_context_use_fence_gets_a_stable_host_code`（2 参数化） | 用途围栏的 stale → `RecallContextUseAuthorityStale` 且**只调用一次**（不重试）；非 stale 的 `MemoryValidationError` 原样上抛 |

回归：见 §6。

## 6. 回归结果

改动前后各跑一次同一套，逐条比对失败集合（不是只比数字）：

| 套件 | 基线（`git stash` 后） | 本改动后 | 结论 |
|---|---|---|---|
| `tests/memory` + `tests/operation_audit` | 64 failed / 622 passed / 2 errors（609s） | 64 failed / **630** passed / 2 errors（651s） | **失败集合逐条完全一致**（`diff` 空）；passed +8 恰为本次新增用例 |
| `test_typed_context_use_primary` + `test_context_route_tool` + `test_model_recall_selection` + `test_no_recall_gate` + `test_primary_history_outbound` | 12 failed / 52 passed | 12 failed / 52 passed | 失败集合完全一致 |
| 新增 `tests/memory/test_recall_authority_stale.py` | — | 8 passed | — |
| importer / `compileall` | — | 6 个受影响模块导入通过、字节码编译通过 | — |

上表第二行的 12 条已知红里，9 条是 `typed_context_use_primary`（既有红），1 条是
`test_late_history_denial…[sent_unknown]`（既有红），2 条 `test_no_recall_gate` 经核也与本改动无关：
一条是 `record_no_recall` 抛 `MandatoryContextActionRequired`（prospective occurrence 门），
一条是 `IndexError: pop from empty list`（借用 `test_s5a_milestone_route_loop.py` 的假 Provider，
`s5a_milestone_route_loop` 本身在既有红名单里）；两条在基线上同样红。

其余既有红（本轮忽略，与本改动无关）：short-index embedder、
`s5b_acceptance_matrix`/`effect_gate`/`s5a_milestone_route_loop`、`scope_disclosure`、
`typed_context_use_primary`、`/Users/denny` 路径类、timing `CancelledError` flake、
`test_late_history_denial…[sent_unknown]`、
`test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite`。

## 7. SDK 改动会不会更干净？—— 会，而且建议做

F-B 在 Host 侧**无解**。而且对照设计文本，问题的重心其实在 SDK 侧：

**（1）epoch 被超出设计范围地推进 —— 首选修复。**
S3 slice `§5.4` 对 epoch 的定义是
「任何**可能改变资格**的 suppression/revoke、认知 head/conflict/classification 变化、
Short-Horizon source 失效和 policy version 变化，均在同一事务追加 event 并增加 epoch」。
但实现里推进 epoch 的 8 个抛点包含了纯**索引/投影/世代重建**：
`short_horizon_projection_changed`（`sqlite_v5.py:2350`）、
`short_horizon_generation_changed`（`:2510`）、
`cognitive_vector_generation_changed`（`:2701`）。
这些只是把同一批内容重新算了一遍向量/分片，**不改变任何一条记忆的资格**，
却让 epoch 在一次会话里跑到 26（见 §1 证据），把一道本该罕见的语义围栏变成高频误报。
建议把这三类（以及没有实际过期项时的 `short_horizon_cleanup`）从 epoch 推进里摘掉，
或者让它们只推进一个独立的"索引世代"，不参与 `authorize_recall_context_use` 的相等性判断。
这一条改完，本次事故的窗口基本消失，且不动任何契约。

**（2）用途围栏本身有冗余 —— 次选/可叠加。**
`sqlite_v5.py:4505-4513` 先做 epoch/policy 相等性判断并抛错，
紧接着 `:4541` 就会调用 `_validate_recall_context_use_sources_unlocked`
**逐条重新校验每个被绑定来源**在当前状态下的 head/revision/content_hash/有效期/抑制/披露。
只要来源全部仍然成立，这次用途本来就是安全的。
可把 epoch 相等性从"抛错"降级为"先做来源重校验，全部通过则签发收据并带一个
`authority_epoch_advanced` 降级码"，只有来源真的变了才抛 `RECALL_AUTHORITY_STALE`。
SDK 自己的 `backends/history_visibility.py:321` 已经是这个思路
（把 `RECALL_AUTHORITY_STALE` 映射成软结果 `history_source_stale` 而不是抛错）。
注意 S3 §5.4 把"suppression 先 commit → 授权返回 `RECALL_AUTHORITY_STALE` 且零 payload"
写成**固定的并发线性化结果**，也就是说它本来就被设计成一个**可预期的正常返回**，
而不是一个应当击杀 Run 的异常 —— 现状是 Harness SDK 与 Host 都没有为这个可预期结果留出路。

**（3）Harness SDK 兜底 —— 代价最大。**
把用途围栏拒绝纳入有界同 Run 修复：`MandatoryContextRejectionV1.reason`
（`simple_harness/execution/context_action.py:13`）增加 `recall_authority_stale`，
让 `dispatch._authorize_context_use` 的该类失败走 `react_loop._reserve_context_repair`，
由模型重新发起一次 `context_route`。要动 checkpoint/repair 状态机，但能彻底消灭这一类 Run 失败。

推荐顺序：(1) → (2) → (3)。

## 8. 严格自审

逐条对着改动问「会不会出事」：

1. **重试会不会产生第二条持久召回？** 不会。参数逐次原样透传，`plan.idempotency_key`
   与 `request_hash` 都不变，SDK 走「同 request、新 attempt」；用例
   `test_recollection_succeeds_and_keeps_one_durable_recall` 用公开重放身份
   （`result_id`/`result_hash`/`decision_id` 三者一致）证明了这一点，没有去读 SDK 私表。
2. **换 `now` 会怎样？** 会改 `context.expires_at` 与请求摘要 → `IDEMPOTENCY_CONFLICT`。
   所以 `now` 必须复用，代码与文档都写死了这条；`typed_recall` 本来就先算一次 `moment` 再传。
3. **重试会不会撞上 deadline？** `request_row.deadline_at = 原 now + budget.deadline_ms/1000`，
   而每次重试传的仍是原 `now`，恒小于 deadline_at；每次 attempt 自己重新计算
   `deadline_monotonic`（各 1s 物理预算），不会累加成假超时。
4. **取消会不会被吞？** 不会。`asyncio.CancelledError` 单独 `except` 先行上抛，
   与 `memory_attempts.execute_typed_recall` 的既有取消纪律一致；有专门用例。
5. **会不会把别的错误当成 stale 重试？** 不会。`is_recall_authority_stale` 用
   `type(error) is MemoryValidationError and str(error) == "RECALL_AUTHORITY_STALE"`，
   子类与同名字符串的其它异常都不算；参数化用例覆盖了「非 stale 的
   `MemoryValidationError` 原样上抛」。
6. **审计会不会被污染？** 每次尝试各占一行 `memory_call_attempts`
   （同一 `request_ref`、不同 `attempt_ref`），失败的那次 `state='raised'` 并自带
   `memory_call_findings` 行；重试循环刻意放在 journal 之外，没有破坏
   「journal 是记录者、不是重试权威」的既有契约。
7. **日志会不会泄载荷？** 只打 `caller`/`attempt` 与 `run_id`/`turn_id`，
   没有 query、没有召回内容、没有 source ref。
8. **无竞态时行为变没变？** 没变：没有 stale 就是一次调用、一行审计，
   回归结果里失败集合与 passed 计数（除新增用例外）完全一致。
9. **`semantic_correction` 加重试有没有副作用？** 分析车道原来遇到 stale 直接把这次
   analysis attempt 判失败（Memory 侧有界重试→dead_letter）；现在先自己重采 2 次，
   用尽后仍抛（`RecallAuthorityStale` 同样是 `Exception`），失败语义不变、只是更少触发。
10. **`RecallContextUseAuthorityStale` 会不会改变失败面？** 它仍然是 `RuntimeError`，
    在 SDK dispatch 处照旧冒泡→`run.fail`；变化只有异常类型名与消息从裸
    `RECALL_AUTHORITY_STALE` 变成稳定 Host 码，`sdk_run_driver_failed` 因此可归因。
    **这条没有消除 Run 失败**，是本轮明确的已知遗留（§7、§9）。

## 9. 遗留 / handoff

- `context_route.py` 里为 `memory_standalone` 分支加一条白名单常量映射，
  把用尽后的 stale 暴露为专属公开码 `context_route_recall_authority_stale`
  （不透传异常文本）—— 该文件本轮由其它 agent 持有，未改。
- F-B 仍会失败 Run（现在带稳定 Host 码、可归因、可统计），
  彻底修复取决于 §7 的 SDK 改动。
- `backend/deskpet/memory/analysis_executor.py` 一侧未改：记忆套用发生在 SDK job runner 事务内，
  Host 无法在那里加闸（见 §3 方案 B）。
