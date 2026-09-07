# 审查：提交 9ff9cfc3「记忆面板 TaskScope 只读审查『任务』标签页」（S6 Task 2 前端）

审查日期：2026-09-07。独立只读审查，未改任何源码。对照：`plans/2026-09-05-s6-primary-preparation/CONTRACTS.md` §4、`ACCEPTANCE.md` TC-PS-05、Memory SDK 仓 `slices/S6-ui-and-program-verification.md` Task 2、后端 `backend/deskpet/memory/human_memory_api.py` / `human_memory_service.py` / `backend/deskpet/task_scope/search.py` / `projections.py`、09-07 产品决定（auto 零提示、只读）。

复跑结果：`npx vitest run src/components/PrimaryTaskPanel.test.tsx src/primary/taskScopeRequests.test.ts src/components/PrimaryMemoryPanel.test.tsx` → 3 文件 11 用例全绿；`npm run -s typecheck` exit 0；`npx eslint` 新增 5 文件 exit 0。与 ARCHITECTURE/PROJECT_STATUS 所记「vitest/typecheck/eslint 通过，原生真实点击未验」一致。

## 结论：ACCEPT-WITH-FIXES

本提交可作为 S6 Task 2 的前端增量合入；边界（候选零授权、exact open 前无视图、零写操作、无 Manual/Auto 开关、失效即清空）核对属实。必须改的前端点只有 1 处（stale 语义误导为「请重试」）；Task 2 整体尚未完成，缺口在后端（`task_scope.list`、绑定/模式来源、Host 侧 drift 探测），列在 §5，由主代理在语料批次结束后补。

## 1. 请求/响应形状逐字段核对

| 操作 | 前端请求（`taskScopeRequests.ts`） | 后端接受（`human_memory_api.py` L284-331） | 后端响应（`human_memory_service.py` L1043-1263） | 前端 parse | 判定 |
|---|---|---|---|---|---|
| `task_scope.search` | `{query, max_candidates:8, cursor?}` | `query` 必填，`max_candidates` 默认 8，`cursor` 可空 | `candidates[{scope_ref,source_ref,source_hash,title,goal,project,status,snippet,rank}], next_cursor, receipt_hash` | 9 字段全查；title≤2048/goal≤4096/project≤2048/status≤256/snippet≤1024 与 `search.py` L366-370 `_bounded` 同值（后端按字节截，前端按码点校，码点≤字节，不会误拒）；候选≤100 与 `MAX_CANDIDATES` 一致；scope_ref 去重 | 一致 |
| `task_scope.open_exact` | `{scope_ref, expected_source_hash}`，**不发 `live_probe`** | `scope_ref` 必填，`expected_source_hash` 可空，`live_probe` 可空 | `scope_ref, receipt_ref("sha256:…"), source_ref, source_hash, resume_package{schema_version:1, task_scope_id, source_id, source_hash, canonical_revision, event_watermark, binding_set_revision, binding_receipt_hash|null, checkpoint_sequence, checkpoint_set_root, read_views{README,PLAN,STATUS,RESUME,EVIDENCE:{content,content_sha256,root_block_id,block_count,receipt_hash}}}, resume_sha256, receipt_hash, drift_report|null{drifted,changed_fields,checkpoint_ref,checkpoint_hash,report_hash}` | 顶层 8 字段、包 11 字段、5 个视图各 5 字段全查；`task_scope_id===scope`、`source_id===source_ref`、包 `source_hash===顶层 source_hash` 三重钉死；`drift_report` 缺键即拒（`else if (!("drift_report" in v))`），非 null 时 5 字段全查；包内 content ≤16384 码点，后端 `_bounded(…,4096)` 字节，不会误拒 | 一致 |
| `task_scope.view` | `{scope_ref, kind}` | 同 | `scope_ref, source_ref, source_hash, kind, content, content_sha256, root_block_id, block_count, receipt_hash` | 9 字段全查，且 scope/source_ref/source_hash/kind 必须等于已打开快照与请求；content ≤65536 码点 ≥ `VIEW_LIMITS` 最大 32KiB | 一致 |
| `task_scope.evidence_groups` | `{scope_ref, source_ref, source_hash, limit:8, cursor?}` | 同（`limit` 默认 8） | `scope_ref, source_ref, source_hash, groups[{group_ref,group_hash,logical_group,first_event_sequence,last_event_sequence,event_count}], next_cursor, receipt_hash` | 全查；`logical_group≥1`、`first≥1`、`last≥first`、`event_count===last-first+1`、`≤500` 与后端 `_group_descriptor` L1372-1387 判定条件逐条相同 | 一致 |
| `task_scope.evidence_page` | `{scope_ref, source_ref, source_hash, group_ref, group_hash, cursor?}` | 同 | `scope_ref, source_ref, source_hash, group_ref, group_hash, prior_page_hash|null, page{page_id,content,content_sha256}, next_cursor` | 全查；`page_id === "sha256:"+content_sha256` 与 `_event_page` L1275 一致；group_ref/group_hash 必须等于用户点击的分组 | 一致 |

- parse 全部为「缺字段即拒绝」：所有校验走 `typeof`/正则/`Number.isSafeInteger`，没有 `?? 默认值` 兜底；`record()` 对非对象返回 `{}` 后各字段随即因缺失被拒；未通过时整页丢弃，不显示部分数据（`PrimaryTaskPanel.test.tsx` 第 3 用例、`taskScopeRequests.test.ts` 第 1-3 用例覆盖）。
- 严格度略高于后端的两处（方向安全，不需改）：前端 `id()` 要求 `trim()===v`，后端 `identifier()` 仅要求非空白；前端 `hash()` 要求小写 64 hex，后端 `hexdigest()` 恒小写。
- `DECISIONS` 不在 resume_package.read_views 内但可 `task_scope.view` 页入：`projections.py` L27 `VIEW_KINDS` 含 DECISIONS，`materialize` L641 写入，前端 `pageInKinds` 含 DECISIONS —— 正确。
- 错误码映射：WS 适配层把任意异常投影为 `payload.error.code`（`human_memory_api.py` L129-133），`PrimaryRequests` 把 code 作为 `PrimaryRequestError.message`（`requests.ts` L40），前端据此分支 —— 链路正确。

## 2. 权限边界

| 检查项 | 结果 | 依据 |
|---|---|---|
| 候选是否泄露 archive | 否 | 候选仅渲染 title/status/project/goal/snippet + 折叠的 scope_ref/短 source_hash（`PrimaryTaskPanel.tsx` L37-42）。snippet 由后端 `search.py` `_snippet` 从 document_text（title/goal/project/status/operations value）生成、≤1024 字节，且 `_owned_scope_ids` 只含当前 subject 自己的 scope（`human_memory_service.py` L1810）。前端不存在任何用候选字段去拼 PLAN/RESUME 的路径。测试用 PLAN=「归档秘密计划」、RESUME=「下一步：导入 3 月」的 fixture 断言搜索后不可见。 |
| exact open 前绝不显示视图 | 是 | `<article aria-label="任务详情">` 仅在 `open && pkg` 时渲染（L46）；视图区仅在 `state.views[kind]` 存在时渲染，而 `loadView` 在 `!open` 时直接返回（`taskScopeRequests.ts` L177）。测试断言 open 前 `queryByRole("article")` 为 null。 |
| 写操作 / 模型可操控开关 | 无 | 客户端只定义 5 个 `task_scope.*` 读操作，无 mutate/create/binding/enqueue/queue.control；面板无 `role=switch`、无「继续/绑定/修改/Auto/Manual」按钮（测试断言）。文案明示「不提供继续执行、修改任务或绑定目录的操作」。 |
| UI 不自报 live_probe | 是 | `open()` 请求体固定 `{scope_ref, expected_source_hash}`（测试 `toEqual` 精确断言）；drift 为 null 时文案「不能作为新鲜度证明」，不伪造新鲜度。 |
| 身份变更 / `human_memory_changed` 后旧数据立即退出 | 是 | `connect()` L124-137：`companion_control_rechallenge`/`companion_profile_unbound`/`companion_identity_unready`/`companion_identity_status(ready=false)`/端口非 connected → `revoke()`（清空 + ready=false）；`companion_profile_bound`/`companion_identity_status` 携带不同 `profile_id:profile_generation` → `revoke()`；`human_memory_changed`/`human_memory_privacy_changed`/`human_memory_invalidated`/`companion_projection_retracted` → `clear()`。`clear()` 递增 epoch 并 `reader.invalidate()`，`run()` 对旧 epoch 的成功/失败回复一律丢弃（L147-159）。与 `graphRequests.ts` L80-91 同一套事件集合，行为对齐。测试覆盖 `human_memory_changed` 中途到达、迟到 PLAN 回复被丢、owner 改绑。 |
| 前端缓存 | 无 | 无 localStorage/sessionStorage，无轮询；标签页切走即卸载组件，客户端实例随之丢弃。 |

## 3. 与 09-07 产品决定一致性

- 纯读：不触发任何 effect 类操作，自然满足「auto 零提示」（没有任何需要用户确认的动作被引入）。
- 忘记仅作用于记忆：面板不涉及 forget/suppression；`human_memory_changed` 只用于清空展示，不反向触发任何写。
- 无冲突。

## 4. 三处偏离的裁定

| 偏离 | 裁定 | 理由 |
|---|---|---|
| (a) 无 `task_scope.list`，只有搜索驱动 | **本提交可接受；Task 2 完成前必须补后端 + 前端** | CONTRACTS §4 第一行明确列为「新 task_scope.list」并给出字段与分页规则；SDK Task 2 要求「展示 active/recent TaskScope、status、bound roots/revision、next step/blocker」。后端确无该操作（`_dispatch` 无分支），前端不可能凭空实现；ARCHITECTURE/UI.md 已如实记录「未在本轮」。不能用「列全库再前端过滤」替代（契约禁止）。 |
| (b) Manual/Auto 来源只显示 binding revision/receipt hash | **本提交可接受；Task 2 完成前必须补后端** | 契约「Auto provenance：只读 mode/来源/receipt/binding revision」四项里，resume_package 只带 `binding_set_revision`/`binding_receipt_hash`，没有 mode、没有 root 标识。前端已把能拿到的全部只读展示且不给开关，文案「由 Host 按可信 Run 模式记录，界面不提供切换」正确。缺口是后端没有暴露 mode/root。 |
| (c) 不发 `live_probe` 故 drift 常为 null | **前端行为正确且必须保持；后端必须补** | 契约要求「Host 真实 filesystem probe 产生 drift；UI 不得自报 live_probe」。当前后端 `open_exact` 只在**调用方传入** `live_probe` 时才算 drift（`search.py` L433-438），Host 自己不探测；且公共 `human_memory_request` 通道原样透传客户端 `live_probe`（`human_memory_api.py` L293-301，`_AUTHORITY_FIELDS` 未含该键）。这意味着：UI 遵约则 drift 永远 null；任何持有 WS 的模型/代理若自报 `live_probe:{}` 就能拿到 `drifted:false` 的报告，UI 会如实显示「检查点未漂移」——这是后端契约缺口，不是前端问题。前端当前「未附带实时漂移探测…不能作为新鲜度证明」文案是诚实的。 |

## 5. 必须改的具体点

### 5.1 前端（本提交合入前或紧随其后，小改）

**F-1（必须）`tauri-app/src/primary/taskScopeRequests.ts` L156-158**：错误码分支只识别 `task_scope_source_stale` 与 `human_memory_permission_denied`；后端在 `evidence_groups`/`evidence_page` 上用的是 `human_memory_evidence_source_stale` / `human_memory_evidence_source_unavailable`（`human_memory_service.py` L1339-1342），前端把它们落到「任务数据暂时无法读取，请重试」——重试不可能成功，且契约要求 stale「明确失败或显式标记只读 stale」。需要：
- 增加分支：`human_memory_evidence_source_stale` → 「已打开的任务来源已被更新，当前视图已过期；请重新搜索并精确打开」，并同时把 `open/views/evidenceGroups/evidencePage` 置空（旧快照不得继续显示为可信）。`human_memory_evidence_source_unavailable` → 同样文案的「来源不可用」变体。
- 同理，`task_scope.view` 后端读的是 head 源（`read_view` L1108 以 `task_scope_id` 取最新源），源变更后前端 `parseTaskScopeView` 因 `source_hash` 不等而抛「任务响应未通过核对」——语义上这是 stale 而非畸形响应。建议在 `parseTaskScopeView`/`parseTaskScopeEvidenceGroups`/`parseTaskScopeEvidencePage`（L86/L92/L104）把「scope/source 不匹配」与「字段缺失」分成两个 Error 消息，前者按 stale 处理（清空已打开快照）。
- 补一条 vitest：`evidence_groups` 返回 `{ok:false, error:{code:"human_memory_evidence_source_stale"}}` 后 `open` 为 null、错误文案含「过期」。

**F-2（建议，非必须）`tauri-app/src/components/PrimaryTaskPanel.tsx` L11-14、L47**：README/STATUS 概览来自 resume_package，后端按 4096 字节截断并加「…」（`search.py` L432、L455-457 超 24KiB 时再截到 1024）。STATUS JSON 一旦截断，`statusSummary` 返回 null，状态/目标/水位/未闭合提示整段静默消失。建议：解析失败时显示「STATUS 概览已截断」并允许 README/STATUS 也可点击页入（后端 `task_scope.view` 已支持这两种 kind）。契约只要求「README/STATUS 首次概括」，故不阻塞。

### 5.2 后端（主代理在语料批次结束后做；Task 2 关闭前必须）

**B-1 新增 `task_scope.list`**（CONTRACTS §4 第 1 行）：
- 权限：只返回 `subject == auth.subject` 的 scope（复用 `_owned_scope_ids`），禁止全库列再过滤。
- 请求：`{cursor?, limit?}`，默认 20、上限 50，keyset 游标按「最近事件/修订时间 desc, task_scope_id asc」稳定排序，游标用现有 `_encode_cursor` 风格（自带 sha256 绑定）。
- 每项：`scope_ref, source_ref, source_hash`（供前端直接 `open_exact` 时带 `expected_source_hash`）、`title, status, canonical_revision, event_watermark, binding_set_revision`、`roots_summary`（见 B-2）、`next_step`（RESUME/STATUS 派生的一句摘要，长度上限）、`blocker`（`semantic_closure_pending`/missing root 等）、`continuation_available: bool`；顶层 `next_cursor, receipt_hash`。总响应走 `_assert_public_bound`。
- 前端随后在身份就绪后自动调用一次显示 active/recent 列表；列表项与搜索候选同样「不授予权限」，点击仍走 `open_exact`。

**B-2 暴露绑定来源摘要（mode/root/receipt）**：在 `task_scope.open_exact` 响应顶层（不进 `resume_package`，避免改变 `resume_sha256` 与 `disclosure.py` 的结构字段比较）增加 `binding_summary: [{root_ref(展示用摘要或 digest，不是可执行路径授权), mode: "manual"|"auto", revision, receipt_hash, state: "active"|"revoked"|"missing"}]`；`task_scope.list` 的 `roots_summary` 用同一结构。前端只读渲染，仍不提供切换。

**B-3 Host 侧 drift 探测 + 公共通道拒绝 `live_probe`**：
- `human_memory_api.py` L293：`task_scope.open_exact` 在 `human_memory_request` 通道上若请求含 `live_probe` 直接返回 `human_memory_public_authority_field_rejected`（或把 `live_probe` 加入 `_AUTHORITY_FIELDS`）；可信 Run 路径（`s4_value_adapter.py` L1007）不受影响。
- `open_task_scope` 内由 Host 依据当前绑定（B-2 的 root 记录）自行构造 probe 并调用 `verify_checkpoint`，使 `drift_report` 在有绑定时非 null；无绑定时保持 null（前端现有文案已覆盖）。

**B-4（可选）`task_scope.view` 接受 `source_ref/source_hash`**：与 `evidence_groups` 一致地把页入绑定到已打开的快照源而不是 head；否则任何一次任务变更后，已打开快照的 PLAN/DECISIONS/RESUME 永远无法页入（只能重搜重开）。现状不违约（契约「不同 revision 不可拼成同一快照」已由前端拒绝保证），故为可选。

## 6. 测试与文档一致性

- `PrimaryTaskPanel.test.tsx`（4 用例）：候选不含 archive 内容、open 前无 article、open 请求体精确、无写操作/无开关、按需页入顺序与请求体、`human_memory_changed` 清空且迟到回复被丢、owner 改绑退出、props 失效退出、缺字段/缺 drift/跨源视图拒绝且不显示部分数据、面板标签接入且切到标签不自动发请求。
- `taskScopeRequests.test.ts`（5 用例）：5 个 parse 的正/负例、client 只发 3 类读操作、`task_scope_source_stale` 映射、无效包不留部分数据。
- 未覆盖（随 F-1 补）：`human_memory_evidence_source_stale` 分支；端口 `disconnected` 触发 revoke；`companion_identity_status` unready。
- ARCHITECTURE/UI.md 顶段、PROJECT_STATUS.md、index.md 的描述与实现逐条相符（含「`task_scope.list` 未在本轮、后端未改、原生真实点击未验」的坦白）。无夸大。

## 7. 备注

- 后端 `human_memory_changed` 只由认知 forget 与 ingestion outbox 触发（`primary_cognitive_controls.py` L206、`memory_ingestion_outbox.py` L471），任务修订/绑定变更不广播；已打开快照在此期间保持显示，靠后续 open/view/evidence 的 stale 拒绝兜底——F-1 修好后该兜底才对用户可见。
- 原生真实点击（Task 2 验证：相似 A/B、cold restart resume、drift、Manual/Auto、wrong candidate no authority）仍待 B-1～B-3 落地后一并做。
