# 事件 AA 决策备忘：用途围栏在一轮之内失效时，有界重采而不是击杀 Run

> 日期：2026-09-09 ｜ 义务：HM-TO-A6 原生真实模型验收（第 10 次整跑，attempt 10 / turn 18）
> 相关 SDK：`simple_harness 0.7.10` / `simple_harness_memory 0.6.37`
> 前置裁决：`DECISION-RECALL-AUTHORITY-STALE.md`（事件 F，commit `8bbb44b4`）

---

## 1. 现象

`.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-j5yjctfj/native.log:1809-1810`

```
00:54:26.309206Z  warning  deskpet.sdk_adapters.typed_context_use
                  recall_context_use_authority_stale
                  run_id=product-sdk-ba0ebb83…400b53 turn_id=86d4501a-baa1-58f6-8116-77585085e8ee
00:54:26.309870Z  error    simple_harness.runtime.kernel
                  event=sdk_run_driver_failed
                  error_type=RecallContextUseAuthorityStale
                  error_message=recall_context_use_authority_stale
```

turn 18 是「读一下这个任务的 README / STATUS 视图」，同一轮里模型跑了 12 次 provider 调用
（`task_scope_search`、`context_route` ×3、`tool_search` ×3、`tool_describe`、`tool_activate`、
`run_shell` ×5、`context_page_in`）。整轮被这一条定性驱动失败击杀。

## 2. 根因：**不是 epoch 前进，也不是来源变了，是被绑定召回的授权租约到期**

逐行复原（时间均为 UTC，来自证据库）：

| 时刻 | 行 | 事实 |
|---|---|---|
| 00:53:03.003 | `human_memory_v7.db` `recall_authority_events` 第 9 行 | `cognitive_memory_changed`，epoch 8→9（T17 的分析批次落库，`cognitive_memory_heads` 新增 `…0821d961fd40d3` r1） |
| 00:53:21.347 | `typed_recall_results` `recall-result:85f74417117792d40c86a3a9a1c2284e` | **本轮绑定的召回**：`authority_epoch=9`、`policy_hash=c27604aa…`、`authority_expires_at=00:54:21.347`（= 采集时刻 **+60s**）、7 个 item 全部 `cognitive_memory` r1 |
| 00:53:21.414 / .424 | `state.db` `context_route_decisions` / `context_route_tool_invocations` | `origin=context_tool`、`route=memory_standalone`、`verdict=accepted`、`typed_carrier` 带 7 个 fragment，全部绑到 `85f7…` |
| 00:53:21.769 → 00:54:16.242 | `recall_context_use_receipts` **11 行** | 同一 `result_id=85f7…`、同一 `turn_id=86d4501a…`、`authority_epoch=9`、每行 `expires_at` 都是 `00:54:21.347` —— 11 次 provider 请求全部被正常授权 |
| **00:54:21.347** | —— | **租约到期** |
| 00:54:26.309 | native.log | 第 12 次 provider 请求组装用途授权 → `RECALL_AUTHORITY_STALE` → `RecallContextUseAuthorityStale` → `run.fail` |
| 00:55:13.330 | `cognitive_memory_revisions` | `…a98b51b66c19ba` r2（epoch 9→10）——**发生在失败之后**，与本次无关 |

抛点：`simple_harness_memory/backends/sqlite_v5.py:4813-4824`
（`authorize_recall_context_use` 的四选一判据）

```python
if (policy_hash != result.policy_hash
        or self._recall_policy_hash != result.policy_hash
        or effective_now >= result.authority_expires_at      # ← 命中的是这一条
        or epoch < result.authority_epoch):
    raise MemoryValidationError("RECALL_AUTHORITY_STALE")
```

逐条排除另外三条：`policy_hash` 全程 `c27604aa…` 恒定；`epoch` 在绑定与围栏两端**都是 9**
（`epoch < result.authority_epoch` 为假，且 0.6.29 起单纯的 epoch 前进也**不再**抛错）；
逐来源重校验（`:4991 _validate_recall_context_use_sources_unlocked`）在那一刻会全部通过——
7 条被绑定记忆在 00:54:26 仍是 r1、`content_hash` 未变、`suppression_directives` 无命中。
成立的只有 `effective_now >= result.authority_expires_at`：
`00:54:26.309 ≥ 00:54:21.347`，**超出租约 4.96 秒**。

Host 抛点：`backend/deskpet/sdk_adapters/typed_context_use.py::authorize_recall_context_use`
← Harness `simple_harness/execution/dispatch.py:442 _authorize_context_use`
← `react_loop.py:521 services.provider.invoke`（无 except）→ `kernel.py` → `RunState.FAILED`。

**租约是 Host 自己定的**：`human_memory_v7.py::typed_recall` 里
`RecallContext.expires_at = moment + 60.0`，SDK `sqlite_v5.py:4104/4302` 原样取作
`result.authority_expires_at`（再与逐 item 的过期时间取 min）。也就是说：
**一个 60 秒的召回上下文期限，被当成了整轮用途授权的租约**；只要一轮工具步骤跑过一分钟，
这一轮就必死，与记忆是否真的变过毫无关系。

## 3. 事件 F 的重采路径为什么没有接住

事件 F 的三条判断在 0.6.28/0.6.29 之后都已过期：

1. **位置错**。F 的 `execute_typed_recall_recollecting` 装在**召回执行时**
   （`human_memory_v7.typed_recall`，即 `context_route` 调用边界），修的是 F-A：采集与写锁复核之间
   的竞态。本次抛点在**组装下一次 provider 请求时**的用途围栏（F-B），F 明确裁定
   「F-B 在 Host 侧无解」（§3 方案 C）。
2. **前提错**。F §3-C 的理由是「epoch 单调递增，重试永远不会成功」。0.6.28 起 epoch 只跟踪
   可能改变资格的事件，0.6.29 起 epoch 前进本身不再抛错（改由逐来源重校验裁定，本库
   `recall_authority_events` 只有 10 行、`recall_context_use_receipts` 有 28 行，正是这两个修复
   在生效）。剩下的两类里，**租约到期是可以靠重采治好的**——重采得到的是一段全新的租约。
3. **判别力不足**。`is_recall_authority_stale` 把 SDK 四类判据压成同一个字符串，Host 分不出
   「租约到期 / epoch 前进」（良性，可重采）与「来源被抑制、被更正、被降级披露」（必须 fail closed）。

## 4. 修法

**唯一能重新绑定的地方是 `snapshot_intents`**，不是围栏本身。理由是硬约束，不是偏好：
`simple_harness/execution/context_use.py::ProviderContextUseGrantV1.__post_init__` 会
`receipt.validate_request(intent.request(attempt))` 逐字段比对
（`decision_id/decision_hash/result_id/result_hash/item_bindings/snapshot_manifest_hash`），
围栏返回的收据**必须**属于 intent 里那一个 result；等请求到了 `_authorize_context_use`，
result 身份已经冻结在 provider attempt 里，任何重试都不可能成功。

于是：

| 文件 | 改动 |
|---|---|
| `sdk_adapters/context_route.py` | `_memory_standalone` 把这次召回的 Host 计划（`query`/`memory_types`/`include_short_horizon`/`turn_ordinal`）传给 `build_carrier` |
| `sdk_adapters/typed_context_use.py::build_carrier` | typed carrier 增加可选 `recollect` 段：上面四项 + 本次结果的 `authority_epoch`/`authority_expires_at`。**`schema_version` 仍是 1**，旧 carrier 逐字不变、只是没有这一段（于是不可重采，退回原行为） |
| `typed_context_use.py::_occurrences` | 行由 3 元组变 4 元组，第 4 位是该 effect 的重采计划（含本次重新解析出的 `AdmittedRecallContext`）。**对模型可见回执的全部既有校验一字未动**，`display["history_binding"]` 仍然对着**原始**绑定比 |
| `typed_context_use.py::snapshot_intents` | 先关掉 Host 只读事务，再走 `_current_occurrences`（重采会真的执行召回并追加 Host 回执，不能压在一个读事务里让写者等锁） |
| `typed_context_use.py::_current_occurrences` / `_rebind` / `_recollect` / `_apply_recollection` | 见下 |
| `typed_context_use.py::consumed_occurrences` | 用**耐久 grant 里的** `view.requests[*].(result_id, result_hash)` 反查 Host 重采回执来复现绑定，绝不在校验路径上再采一次 |
| `sdk_adapters/context_authority.py::ContextRouteLedgerStore` | 新增 `read_context_use_recollections` / `record_context_use_recollection`（幂等、无载荷、并入既有 Harness evidence ingress） |
| `memory/migrations/primary/048_context_use_recollections_v56.sql` + `memory/context_use_recollect_schema.py` + `memory/schema_chain.py` + `main.py` | 新表 `context_use_recollections`（域链 v56，只追加、带恢复围栏与恢复表登记） |
| `memory/recall_authority.py` | `CONTEXT_USE_RECOLLECT_PURPOSE` / `MAX_CONTEXT_USE_RECOLLECTS=16` / `CONTEXT_USE_LEASE_MARGIN_SECONDS=10` / `CONTEXT_USE_RECOLLECTED`，新异常 `RecallContextUseSourceSuperseded` |
| `typed_context_use.py` + `main.py` | 可选 `fault_sink=RunFaultMemo`：这两个稳定码进 `run_terminal` 的 `public_payload.error_code`，不再只落在 native.log |

判定与动作：

1. `_current_occurrences`：`now + 10s >= carrier.recollect.authority_expires_at` 才动。
   留 10 秒是因为 snapshot 之后还有 Memory 用途授权与 Harness `validate_handoff(now)` 两道
   时间检查，「还没过期」不足以拿来组装请求。**没有租约压力就一行不改、一次不采**（用例钉死）。
2. `_rebind`：先看已有回执，有一条自己的租约还够就直接复用（同一轮的多次 provider 请求只采一次）；
   都过期且未超 `MAX_CONTEXT_USE_RECOLLECTS` 才开下一个 `generation`。
3. `_recollect`：用**同一个计划**、同一个 `AdmittedRecallContext`（因而同一 disclosure、同一 evidence ref）
   再调一次 `typed_recall`，但换 `idempotency_purpose=context-route-use-recollect`、
   `idempotency_scope=f"{effect_id}:{generation}"`——这是 SDK 的一条**独立耐久请求**
   （`typed_recall_requests` 两行：`context-route:{run}:{turn}` 与
   `context-route-use-recollect:{run}:{turn}:{effect}:{gen}`），不会污染模型那次召回的重放身份。
4. **安全性判据**：每个被绑定 fragment 必须在重采结果里找到
   `(source_ref, source_revision, public_payload_hash)` 三元组**逐字相等**的 item。
   这三元组就是「模型已经拿到的那串字节仍然是当前值」。全中 → 改绑（只换 authority binding，
   payload / payload_hash / disclosure / evidence_refs / fragment_id 一个字节不动），
   写一行 `reason_code=context_use_recollected` 的不可变回执；
   任何一条缺失或对不上 → **fail closed**，抛 `RecallContextUseSourceSuperseded`
   （稳定码 `recall_context_use_source_superseded`），不写任何回执。
5. **确定性/重放**：重采请求由 SDK 持久化，改绑映射由 Host 回执持久化；
   `consumed_occurrences` 只按耐久 grant 的 result 身份去认回执，认不到就抛
   `typed_use_recollection_receipt_missing`，绝不自己再造一个身份。
   `consumed_occurrences` 返回的 consumed 集合仍按**原始** item id（那是模型看见的东西），
   所以 `primary_dependencies` 的读模型行为不变。

## 5. 有意留下的边界（不假装解决）

- 「来源真的变了」这一支**仍然会失败整轮**。要把它变成模型可动作的重路由，唯一的位置是把那条
  `context_route` 工具回执的正文换成通知——但 `context_route` carrier 按现有设计
  **不可 elide**（`context_authority.py` `_current_tool_page_facts` 文档明写），
  且这条投影/分页链路（`backend/deskpet/execution/current_tool_pages.py`）本轮属于别的 worktree。
  本轮做到的是：**良性一支（租约到期 / epoch 前进而值未变）不再失败**，
  失败一支拿到**独立**的稳定码并进入 `run_terminal` 证据。彻底修复是 §7 的 Harness SDK 改动。
- 重采返回的 item 集合理论上可能因排序/预算抖动而少掉某条被绑定 item，被判成 supersede。
  同 query、同计划、记忆未变时不应发生（用例正向钉住），记为需要观察的锐边。

## 6. 测试

新增 `backend/tests/sdk_adapters/test_typed_context_use_recollect.py`（7 例，全绿）。
用**真实安装的 Memory SDK**、真实 `state.db`（域链 v56）、真实种子记忆、真实 `build_carrier`
产出的真实 fragment，Host 语义时钟显式推进（不碰进程全局时间）。

| 用例 | 证明 |
|---|---|
| `test_bound_lease_is_real_and_the_fence_really_refuses_it` | 事故本身：租约内授权正常出收据；推进 65 秒后，**只有时钟变了**，真实 SDK 围栏抛 `RECALL_AUTHORITY_STALE`，Host 抛 `RecallContextUseAuthorityStale`（= 击杀 Run 的那条） |
| `test_expired_lease_is_recollected_rebound_and_authorized` | 修复：改绑后同一道真实围栏**签出收据**；payload/payload_hash/source_ref/source_revision 逐条不变而 result_id 变；Host 回执一行、`generation=1`、`reason_code=context_use_recollected`；SDK 侧恰好两条 `typed_recall_requests`（原召回 + 重采用途） |
| `test_recollection_is_reused_while_its_own_lease_holds` | 同一轮的下一次 provider 请求复用同一回执，不再采 |
| `test_untouched_binding_is_never_recollected` | 无租约压力时 occurrences 完全相等、零回执（未受影响路径逐字不变） |
| `test_superseded_source_still_fails_closed_with_a_stable_code` | 负例：`suppress` 掉一条被绑定记忆后重采 → `RecallContextUseSourceSuperseded`（`recall_context_use_source_superseded`），**不写回执**，原绑定仍被真实围栏拒绝 |
| `test_recollection_budget_is_bounded` | 预算用尽 → `RecallContextUseAuthorityStale`，回执数不再增长 |
| `test_replay_uses_the_receipt_the_grant_was_issued_against` | 重放确定性：按耐久 grant 的 result 身份复现改绑；grant 指原绑定则复现原绑定；无回执覆盖的身份 → `typed_use_recollection_receipt_missing`；全程不新增回执 |

**main 基线（行为红）**：新模块在 main 上连导入都不成立
（`ImportError: cannot import name 'CONTEXT_USE_RECOLLECT_PURPOSE'`）。
用同一世界在 main 一次性复算（throwaway worktree `aa-main`，脚本已删）：

```
carrier keys: ['admitted', 'fragments', 'schema_version']
bound result: recall-result:cfca3b17…  lease: 60.0 s
inside lease -> receipt recall-result:cfca3b17…
PAST LEASE   -> RecallContextUseAuthorityStale recall_context_use_authority_stale
host re-collect API present: False
```

## 7. 回归（逐条比对失败集合，不是只比数字）

| 套件 | main 基线 | 本改动后 | 结论 |
|---|---|---|---|
| `test_typed_context_use_primary` + `test_context_authority_primitives` + `test_recall_authority_stale` + `test_context_route_tool` | 9 failed / 48 passed | 9 failed / 48 passed | 失败集合逐条相同 |
| `test_startup_epoch_dispatcher` + `test_composed_startup_extensions` + `test_human_memory_program_primary` + `test_s5b_v46_cutover` + `test_effect_closure_migration_v46` + `test_primary_effect_sources_v47` + `test_trusted_disclosure` + `test_s5c_schema_chain` | 11 failed / 46 passed | 11 failed / 46 passed | 同上（v56 注册被 `test_s5c_schema_chain` 的守卫用例接受） |
| 上一行 4 个 + `test_s5c_store` + `test_primary_history_tool_calls` + `test_recovery_fence` | 11 failed / 100 passed | 11 failed / 100 passed | 同上 |
| `test_prospective_notice` + `test_prospective_mandatory_repair` + `test_prospective_ack_routes` | 1 failed / 15 passed | 1 failed / 15 passed（+ 新用例 7） | 同上；这三套是走真实 runtime 的 `memory_standalone` + `consumed_occurrences` 全链路 |
| 新增 `test_typed_context_use_recollect.py` | — | 7 passed | — |

`test_typed_context_use_primary` 的 9 条既有红与本改动无关，根因是该叶子用例还在
`monkeypatch` 老的 `ProductRunContextAuthority(typed_use_authority=…)`，而 fixture
`tests/execution/test_primary_foreground_runtime.py::build` 早已自带 `context_use_memory` /
`typed_use_authority` 参数，两边重复传参 → `TypeError`；改用 fixture 参数后还会露出第二处陈旧
（`ContextRouteToolService` 的 patch 在 `monkeypatch.context()` 退出后失效，工具服务拿不到
typed carrier）。本轮**没有**改它，以保持失败集合与 main 逐条一致；记为 followup。

## 8. 严格自审

1. **重采会不会把旧值当新值交给模型？** 不会。改绑只换 authority binding，
   payload 与 payload_hash 由 `_apply_recollection` 与 `_recollect` 两处各校验一次；
   三元组对不上就 fail closed。模型可见的那条工具回执一个字节没动，
   `_occurrences` 对它的全部既有校验（含 `display["history_binding"]` 对**原始**绑定）原样保留。
2. **会不会多出第二条模型召回？** 不会。重采走**另一个** `idempotency_purpose`，
   模型那次召回的 `request/result/decision/terminal` 与重放身份一字未动；用例钉了两条 key 的字面值。
3. **重放会不会飘？** 不会。校验路径只认耐久 grant 的 result 身份，按 Host 回执复现；
   认不到就抛，不重采、不自造。
4. **写路径会不会在读事务里死等锁？** 不会。`snapshot_intents` 先关只读事务再重采（注释写明理由）。
5. **无竞态时行为变没变？** 没变。`_untouched_binding` 用例断言 occurrences 完全相等且零回执；
   三套 prospective 全链路回归失败集合与 main 逐条一致。
6. **回执会不会泄载荷？** 不会。表里只有 id/hash/epoch/时间/`fragment_id`；
   evidence ingress 的 public payload 只有 effect/generation/reason/两个 result id/epoch/来源条数；
   日志只有 run_id/generation。**注意**：typed carrier 的 `recollect.query` 是模型自己的查询串，
   与 carrier 里早已逐字保存的 `public_payload` 同库同行，未新增披露面。
7. **预算会不会无限？** 不会。`MAX_CONTEXT_USE_RECOLLECTS=16`，用尽抛稳定码，用例钉死。
8. **围栏还在不在？** 在。`authorize_recall_context_use` 的 stale 处理一字未松，
   只是从「常态」退化成「兜底」——组装请求之后、授权之前来源才变的那一格仍然 fail closed。

## 9. SDK 侧 followup

1. **Harness SDK（首选）**：`simple_harness/execution/context_action.py:13` 的
   `MandatoryContextRejectionV1.reason` 目前硬限定 `pending_prospective_occurrence`。
   增加 `recall_context_use_superseded`，并让 `dispatch._authorize_context_use` 的该类失败走
   `react_loop._reserve_context_repair`，模型就能被告知「那条记忆变了，请重新路由」，
   §5 的最后一格 Run 失败随之消失。事件 F §7(3) 已经提过，本轮的证据把它从「更干净」升级为
   「唯一剩下的路」。
2. **Memory SDK**：`authorize_recall_context_use` 的
   `effective_now >= result.authority_expires_at` 与逐来源重校验语义重叠——
   0.6.29 起每一次授权都会把每条被绑定来源按当前状态重校验一遍，租约到期并不能多挡住任何一条
   已经失效的来源，只能挡住「跑了超过一分钟的正常回合」。
   建议把它降级为一个 `authority_lease_expired` 的**降级码**（像
   `authority_epoch_advanced` 那样签发收据 + 记日志），或至少把 60 秒的来源交给调用方按轮次预算配置。
3. Host 侧 `RecallContext.expires_at = now + 60` 这个常量本身也应随 (2) 一起重新定义：
   它现在同时承担「召回上下文期限」与「整轮用途租约」两个语义，而这两者的合理量级差一个数量级。
