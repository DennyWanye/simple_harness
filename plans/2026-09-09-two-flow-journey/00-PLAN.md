# 两轮完整流程（含重启）验收方案（2026-09-09）

> 义务：S6 Task 7「required 真人桌面 E2E」中的**两轮长对话 / 重启旅程**——
> 「至少 3 个语义类+负例，含 cold start、>=20-turn 长上下文、两个 TaskScope、exact resume、
> 两次独立 root run 和正向业务结果」。
> 同时给下列 obligation 提供真桌面证据：
> `HM-TO-A1`（唯一主对话 → 逻辑遗忘 → 普通路径不可见 → 显式审计受控可见并留痕）、
> `HM-TO-A3`（**冷重启 / 换 Agent 恢复** 半边）、`HM-TO-A4`（五路分流、久远任务模糊搜索→候选→exact open、
> 类型化召回、no-recall）、`HM-TO-A5`（Procedure / Prospective 一等能力）、
> `HM-TO-A7`（受控审计面，含新增「本机执行审计」分节）、`HM-TO-A8`（重启不产生第二条可写主对话）。
> 冻结场景：`HM-S2`（久远任务发现与完整恢复）、`HM-S5`（模糊愿望负例）、`HM-S7`（审计与逻辑遗忘）、
> `HM-S10`（五路分流，部分）。
>
> 执行方式：**真实主模型 + 原生 App + System Events UI 驱动，无截图**（同 HM-TO-A6）。
> 驱动脚本：`scripts/native/twoflow_driver.sh`（分两阶段跑）；核对脚本：`scripts/native/twoflow_verify.py`。

---

## 0. 调查结论：重启怎么做、证据落在哪

### 0.1 同 userdata 重启的确切配方（读自 `scripts/native/launch_native_candidate.py`）

启动器把 userdata 和证据目录分开：`--evidence-root` 下每次启动 `mkdtemp(prefix='primary-ui-')`
生成一个新的 run 目录，**而 `--userdata` 显式给定时就复用它**（`launch_native_candidate.py:97-99`）：

```python
run  = Path(tempfile.mkdtemp(prefix='primary-ui-', dir=args.evidence_root)).resolve()
user = args.userdata.resolve(strict=True) if args.userdata else run / 'userdata'
```

因此：

| 阶段 | 命令 | 产物 |
|---|---|---|
| 第一次启动（冷启动，全新数据目录） | `--evidence-root .local-test-evidence/2026-09-09/twoflow-<sha>/`（**不带** `--userdata`） | `<E1>`，DB 在 `<E1>/userdata/data`，日志 `<E1>/native.log` |
| 关闭 | `pkill -f '<bundle 名>.app/Contents/MacOS'`（注意是 **bundle 名**不是 bundle id），再 `lsof -nP -iTCP:18120` 确认端口释放 | — |
| 第二次启动（重启，同 userdata） | 同样的 `--evidence-root`，**加** `--userdata <E1>/userdata` | `<E2>`，DB 仍在 `<E1>/userdata/data`，新日志 `<E2>/native.log` |

必须记住的三条硬约束：

1. `--userdata` 走 `resolve(strict=True)`，**目录必须已存在**，所以只能传第一次跑出来的 `<E1>/userdata`。
2. 带 `--userdata` 时启动器 **不会重写** `llm_runtime.json`（`launch_native_candidate.py:120-121`），
   第一次跑的 provider 配置原样保留——这正是"同一环境重启"该有的语义。
3. 启动器在探测到任何 `/Contents/MacOS/simple-harness` 进程时抛 `native_test_already_running`
   （`:71-75`），所以**必须先杀干净再重启**；端口 18120 也会被残留 `main.py` 占住。

**所有 DB 证据（`state.db` / `sdk-product-state.db` / `human_memory_v7.db` / `operation-audit.db` /
`simple-harness-sdk/execution-v6.sqlite3`）都在 `<E1>/userdata`**，重启不搬家；`<E2>` 只多出
第二段 `native.log` 与 `launch.json`。因此核对脚本以 `<E1>` 为 `--evidence`，用
`--second-log <E2>/native.log --second-launch <E2>/launch.json` 补上重启那一半。

### 0.2 关键证据表（均已在 A6 尝试 5 的真实证据库上确认存在）

| 断言 | 表 / 列 |
|---|---|
| 唯一永久主对话 | `state.db.human_memory_primary_conversations(primary_conversation_id, subject, writable)` —— A6 实测恰 1 行 |
| 每轮前台 Run | `state.db.foreground_run_heads(host_run_id, current_state, sdk_run_id, updated_at)` |
| 五路分流 | `state.db.context_route_decisions(route, origin, task_scope_id, binding_set_revision, …)`；A6 实测 route 取值 `create_new / continue_active / direct_standalone / memory_standalone / resume_existing` |
| 任务发现与 exact open | `state.db.task_scope_search_access_receipts(operation)` —— A6 实测 `search` 7 次 / `open` 23 次 |
| 任务 canonical 状态与收口 | `state.db.task_scope_canonical_revisions(revision, state_json)`（`state_json` 内含 `status` / `goal` / `closure_reason`）、`state.db.task_scope_closure_receipts(outcome, reason_code)` |
| 六阅读视图物化 | `state.db.task_scope_read_view_revisions(view_kind, content)`（README/PLAN/STATUS/DECISIONS/RESUME/EVIDENCE） |
| 四类认知记忆 | `human_memory_v7.db.cognitive_memory_heads(memory_type)` —— 取值 `episode/semantic/procedure/prospective`；A6 实测 `episode 9 / semantic 5` |
| Procedure / Prospective 一等表 | `human_memory_v7.db.procedure_records(name, applicability_json, steps_json, risk_level)`、`prospective_records(action_text, trigger_kind, trigger_json, scheduler_registration_ref, due_at)` |
| Procedure 真实使用 | `state.db.procedure_uses(use_id, task_scope_id, memory_id, target_revision, body_hash)` |
| 类型化召回 | `human_memory_v7.db.typed_recall_requests / typed_recall_results / typed_recall_result_items(result_item_json)` |
| 逻辑遗忘（append-only，**触发器禁止 UPDATE/DELETE**） | `human_memory_v7.db.suppression_directives(event_kind IN ('directive','revoke'), scope_kind, scope_ref, reason_code)` + `suppression_targets(target_kind, target_ref)`；UI 点一次「忘记这条记忆」产生 `scope_kind='MEMORY'`、`scope_ref=<memory_id>`、`reason_code='user_forget'` 的一行（`backend/deskpet/memory/primary_cognitive_controls.py:209-216`） |
| 受控审计面 | `operation-audit.db` 的 `human_audit_grants` / `human_audit_deliveries` / `human_audit_host_streams(section)` / `human_audit_host_deliveries(section)`；section ∈ `runs` / `run_operations` / `memory_calls`（`backend/deskpet/operation_audit/human_access.py:30-31`，`HOST_MAX_READS=32`、TTL 300 s） |
| 原始证据永不物理删除 | `human_memory_v7.db.evidence_envelopes` 行数只增（`HM-TO-R1`） |

### 0.3 必须遵守的产品决定（Host `CLAUDE.md`「用户产品决定（2026-09-07）」第 2 条）

> **遗忘只针对记忆，不针对会话记录**：在 UI 忘记一条认知记忆，只影响该记忆的召回/图谱/工作记忆，
> **不得**把它的来源对话轮从主对话视图或短期历史中隐藏。

所以本旅程的 T19（遗忘后再问）和 T20（原始对话是否还在）是**一对**断言：
前者必须"答不出/明确说不再持有该记忆"，后者必须"原始那句话仍能在主对话里翻到"。
把这两条同时验，才不会把产品决定验反。

---

## 1. 验收目标 → 可观测证据映射

| 目标条款 | 可观测证据 | PASS 判定 |
|---|---|---|
| TF-1 唯一永久主对话跨重启不变 | `human_memory_primary_conversations` 恰 1 行且 `writable=1`；`<E1>/launch.json` 与 `<E2>/launch.json` 的 `userdata` 字段相同 | 单行主对话 + 两次启动共用同一 userdata |
| TF-2 ≥20 turn 长上下文 | `twoflow-progress.jsonl` 记录到 T22；`foreground_run_heads` 终态 Run ≥18（20 个发送轮扣掉可能的合并/失败） | 20 个发送轮全部有终态 Run |
| TF-3 重启确实发生且是冷启动 | progress 有 `phase=flow1`/`flow2` 与一条 `restart` 记录；`<E2>/native.log` 存在且与 `<E1>/native.log` 不同；两份 `launch.json` 的 `pid` 不同 | 两段日志 + 两个 pid + 同 userdata |
| TF-4 两个 TaskScope | `task_scopes` ≥2；`context_route_decisions.route='create_new'` ≥2 | 两个任务各自建档 |
| TF-5 exact resume 不混任务 | `task_scope_search_access_receipts` 同时有 `operation='search'` 与 `'open'`；重启后出现 `route='resume_existing'`；被 resume 的 `task_scope_id` == 任务 A 且 ≠ 任务 B | 先候选后 exact open，且开的是 A |
| TF-6 跨重启类型化召回 | T12 窗口新增 `typed_recall_requests`/`typed_recall_results`；回答含「霜降素材 / 成片」 | 召回链路在重启后仍工作 |
| TF-7 Procedure 一等能力 + 跨重启使用 | `procedure_records` ≥1（T9 建）；`state.db.procedure_uses` 在 T16 窗口 +1，且 `task_scope_id` == 任务 A | 建档在 flow1，使用在 flow2 |
| TF-8 Prospective 明确触发才 pending | T10 后 `prospective_records` +1 且 `trigger_kind` 非空；`cognitive_memory_heads.memory_type='prospective'` ≥1 | 有 1 条带触发的前瞻 |
| TF-9 逻辑遗忘落 append-only suppression | T18 后 `suppression_directives` +1（`event_kind='directive'`、`scope_kind='MEMORY'`、`reason_code='user_forget'`）且 `suppression_targets` +1；两表行数只增 | 遗忘写入且不物理删除 |
| TF-10 遗忘后普通召回不可见 | T19 窗口的 `typed_recall_result_items.result_item_json` 不含被遗忘的 `memory_id`/`scope_ref`；驱动 note 记录模型回答不再给出该目录 | 召回结果零命中被遗忘项 |
| TF-11 原始会话文本仍在（产品决定 #2） | `evidence_envelopes` 行数在 T18 前后不减；T20 的 UI/回答 note 记录「原话仍可在主对话里翻到」 | 证据不减 + 历史仍可见 |
| TF-12 受控审计面（含本机执行审计） | `human_audit_grants` ≥1；`human_audit_deliveries` ≥1；`human_audit_host_streams` 覆盖 `runs`/`run_operations`/`memory_calls` 三个 section；`human_audit_host_deliveries` ≥3 | 三个分节都被真实读过 |
| TF-13 两个任务都正向收口 | `task_scope_closure_receipts` ≥2；两个 scope 的 `task_scope_canonical_revisions.state_json` 里出现完成态 `status`/`closure_reason` | 两任务均收口 |
| TF-14 物理删除禁令 | `evidence_envelopes` / `suppression_directives` / `task_scope_events` 三个计数在 progress 中逐轮单调不减，重启前后也不减 | 全程单调不减 |
| TF-15 snapshot 重放指纹 | 用安装目标 venv 的 `provider_request_from_json` / `provider_request_fingerprint` 对每行 `request_json` 重算，与 `provider_invocations.request_fingerprint` 比对（复用 `a6_verify.build_replayer`） | 重放全等 |

### 负控（必须为「不发生」）

| 编号 | 负控 | 证据 |
|---|---|---|
| NC-T1 | T6 简单改写不建/不切 TaskScope，也不查长期库 | T6 窗口无 `route='create_new'`；active scope 不变；`origin='no_recall'` 或该窗口无 typed recall 请求 |
| NC-T2 | T17 模糊愿望不产生 pending Prospective | T17 窗口 `prospective_records` 增量 = 0，`prospective_scheduler_registrations` 增量 = 0 |
| NC-T3 | 纯 UI 轮（T18/T21）不新增 provider 调用 | 这两轮 `provider_invocations` 增量 = 0 |
| NC-T4 | 全程请求体不含凭据形状 | 全量 `request_json` 对 `a6_verify.CREDENTIAL_PATTERNS` 零命中 |
| NC-T5 | 重启不产生第二条可写主对话 | `human_memory_primary_conversations where writable=1` 恰 1 行（HM-AC-8） |

---

## 2. 对话脚本（22 轮 = 20 个发送轮 + 2 个纯 UI 轮，中间一次重启）

同一永久主对话内完成。任务 A =「霜降素材整理」，任务 B =「霜降字幕校对」。
`T` 列即 `twoflow_driver.sh` 的轮号；驱动分两阶段跑（`flow1` / `flow2`），`--start` 可从任意轮续跑。

### 流程一（重启前，T1–T11）：建任务 → 用工具 → 记决定 → 收口

| T | 类型 | 用户输入（原文） | 预期行为 | 预期证据 | PASS 项 |
|---:|---|---|---|---|---|
| 1 | 发送 | 记住：我整理素材的时候，成品一律放到「霜降素材 / 成片」这个目录。 | 明确记住 → 语义事实（跨重启召回目标） | `cognitive_memory_heads` +1（`memory_type='semantic'`） | TF-6 前置 |
| 2 | 发送 | 新建一个项目任务：霜降素材整理。 | `context_route(create_new)`，任务 A 建档 + managed home 绑定 | `task_scopes` +1；`route='create_new'`；`task_workspace_binding_revisions` +1 | TF-4 |
| 3 | 发送 | 在这个任务的工作目录里建一个 clips.md，写三行素材条目：A-01、A-02、A-03。 | 工具使用 + 正向业务结果 | `execution_effects` 出现写文件；文件真实存在 | S6 Task 7「正向业务结果」 |
| 4 | 发送 | 把 clips.md 读回来，确认三行都在。 | 第二次工具使用（读回核对） | `execution_effects` 出现读文件 | 同上 |
| 5 | 发送 | 记一个决定：素材编号统一用 A-序号 两段式，不再用日期前缀。 | `task_scope_update(decision.record)` | `task_scope_events` +1；DECISIONS 视图含该决定 | TF-5 后置核对 |
| 6 | 发送 | 把上一句话改得更简洁一点。 | **负控**：简单改写不建档、不召回 | 无 `create_new`；active scope 不变；`origin='no_recall'` | NC-T1 |
| 7 | 发送 | 另外新建一个项目任务：霜降字幕校对。 | 第二个 TaskScope（任务 B） | `task_scopes` +1；第二次 `create_new` | TF-4 |
| 8 | 发送 | 在字幕校对这个任务的目录里建一个 subs.md，写一行「待校对」。 | 任务 B 的工具效果绑在 B 的 binding 上 | `context_route_decisions.task_scope_id` == 任务 B，`binding_set_revision` 属 B | TF-4 / HM-TO-R7 旁证 |
| 9 | 发送 | 请记住我以后常用的一个流程，名字叫「霜降清点」：第一步在当前工作目录写 inventory.md，列出目录里的文件；第二步把同样内容再抄一份到 inventory-backup.md。以后我说按霜降清点做，就按这两步。 | Procedure（语义类 2）明确用户程序 | `procedure_records` +1；`memory_type='procedure'` | TF-7 |
| 10 | 发送 | 这次霜降素材整理收尾之后，提醒我写一份成片说明。 | Prospective（语义类 3）：明确行动 + 明确事件触发 → pending | `prospective_records` +1 且 `trigger_kind` 非空 | TF-8 |
| 11 | 发送 | 霜降素材整理这个任务先到这里，标记完成。 | `task_scope_update(task.complete)` → 任务 A 收口 | `task_scope_closure_receipts` +1；canonical `status` 进完成态 | TF-13 |

### ⟨重启⟩ 驱动在此暂停（`@RESTART@`）

```
# 1) 关闭第一个进程
pkill -f '<bundle 名>.app/Contents/MacOS' ; sleep 3 ; lsof -nP -iTCP:18120 -sTCP:LISTEN   # 应无输出
# 2) 同 userdata 重启, 产生 <E2>
python scripts/native/launch_native_candidate.py --launch \
  --source /Users/taiwan/PROJECTS/SimplaHarness/simple_harness \
  --bundle "<…/SimpleHarness Memory Verify <sha>p18120.app>" \
  --installed-target <installed-h0710-m06xx-s0313> \
  --userdata <E1>/userdata \
  --evidence-root .local-test-evidence/2026-09-09/twoflow-<sha>/ --port 18120
# 3) 等到主对话可用（UI 不再显示「等待主对话就绪 / 正在重新读取…」）后回车放行驱动
```

### 流程二（重启后，T12–T22）：搜索恢复 → 跨重启召回 → UI 遗忘 → 审计面核验

| T | 类型 | 用户输入 / UI 动作（原文） | 预期行为 | 预期证据 | PASS 项 |
|---:|---|---|---|---|---|
| 12 | 发送 | 我们接着聊。你还记得我最早说过，成品要放到哪个目录吗？只依据我以前说过的回答。 | 跨重启类型化召回，答「霜降素材 / 成片」 | 本轮新增 `typed_recall_requests`/`typed_recall_results`；主对话 ID 未变 | TF-1 / TF-6 |
| 13 | 发送 | 我之前做过一个跟「霜降」有关的整理任务，帮我先找出来，别急着打开。 | `task_scope_search` → 只给候选，不切 scope、不授权 | `task_scope_search_access_receipts.operation='search'`；active scope 未变 | TF-5 前半 |
| 14 | 发送 | 就精确打开「霜降素材整理」，把它的恢复要点和已经记下的决定说给我听。 | exact open → ResumePackage；读 RESUME + DECISIONS | `operation='open'`；`route='resume_existing'`；开的是任务 A | TF-5 后半 |
| 15 | 发送 | 这个任务当时留下的 clips.md 现在还在吗？读出来核对一下三行素材条目。 | 跨重启的工作区绑定仍有效，文件仍在 | 读文件 effect 成功；内容含 A-01/A-02/A-03 | TF-3 佐证 |
| 16 | 发送 | 在这个任务的目录里，按我以前存的「霜降清点」流程做一遍。 | Procedure 跨重启真实使用 | `state.db.procedure_uses` +1，`task_scope_id` == 任务 A；inventory.md / inventory-backup.md 生成且内容一致 | TF-7 |
| 17 | 发送 | 以后有机会我想学做饭。 | **负控**：模糊愿望只进 Semantic Goal | `prospective_records` 增量 0；无调度注册 | NC-T2 |
| 18 | `@UI@` | 点 `记忆` → `记忆列表` → 找到「成品一律放到『霜降素材 / 成片』」那条记忆，点 `忘记这条记忆`（**一击即生效，无二次确认**）。记录 `before=<条数> after=<条数> forgot=1` | 逻辑遗忘写 append-only suppression | `suppression_directives` +1（`scope_kind='MEMORY'`、`reason_code='user_forget'`）、`suppression_targets` +1 | TF-9 / NC-T3 |
| 19 | 发送 | 再问一次：我说过成品要放到哪个目录？ | 遗忘后普通召回不可见 | 本轮 `typed_recall_result_items` 不含被遗忘 memory_id；模型不再给出该目录 | TF-10 |
| 20 | 发送 | 我最早说那句话的原文，现在还能在这个对话里翻到吗？把那一条原话找出来给我看。 | **产品决定 #2**：遗忘只针对记忆，不隐藏会话记录 | `evidence_envelopes` 未减少；模型能复现原话 | TF-11 |
| 21 | `@UI@` | 点 `操作记录` → `查看我的记忆操作记录（仅元数据）` → `读取记录`（至少一页，按钮随后改名 `下一页`）→ 在 `本机执行审计` 区点 `读取终态 Run 审计` → 对其中一个 Run 点 `查看该 Run 的操作` → 点 `读取记忆调用记录` → 点 `结束本次查看` → 切回 `记忆列表`。记录 `grant=1 pages=<n> sections=runs,run_operations,memory_calls` | 受控审计（含新增「本机执行审计」）真实被调用 | `human_audit_grants`/`human_audit_deliveries`/`human_audit_host_streams`/`human_audit_host_deliveries` 均有行，三个 section 齐 | TF-12 / NC-T3 |
| 22 | 发送 | 霜降字幕校对这个任务也标记完成，然后用一句话总结这两轮我们一共做了什么。 | 任务 B 收口 + 全程总结 | 第二条 `task_scope_closure_receipts`；20 个发送轮全部有终态 Run | TF-13 / TF-2 |

---

## 3. 执行前置与操作要点

1. **预检**（两次启动前各跑一次）：
   `bash scripts/native/preflight_native.sh <env-file> <model> 18120 32000 [<userdata>]` 必须全绿。
2. **第一次启动 → flow1**：
   ```
   bash scripts/native/twoflow_driver.sh <bundle-id> <E1>/userdata <E1> flow1
   ```
3. **重启**：驱动跑完 T11 后打印 `@RESTART@` 提示并暂停；按 §2 的三步操作，完成后回车。
4. **第二次启动 → flow2**：
   ```
   bash scripts/native/twoflow_driver.sh <bundle-id> <E1>/userdata <E1> flow2
   ```
   注意 **evidence-dir 两次都传 `<E1>`**：进度文件 `twoflow-progress.jsonl` 必须是同一份，
   核对脚本靠它做逐轮增量。`<E2>` 只在核对时用 `--second-log/--second-launch` 带进来。
5. **审计面纪律**（T21）：`本机执行审计` 分区在 grant 生效前**不存在于 AX 树**——必须先点
   `查看我的记忆操作记录（仅元数据）` 并等到 `结束本次查看` 出现，才能找到
   `读取终态 Run 审计`。`查看该 Run 的操作` 在该 Run `status != "enumerated"` 时是禁用的。
   grant TTL 300 s、`HOST_MAX_READS=32`，整段取证要在 5 分钟内做完。
6. **忘记按钮是一击生效、且底层行被触发器保护不可删除**——点错就永久生效。T18 前先用
   `记忆列表` 的 `下一页` 翻到正确那条，确认文本再点。
7. **不要**去 `更多 → 记忆管理` 的旧面板（那是 legacy overlay，忘记按钮是 `🗑`，语义不同）。
8. **AX 名称速查**（System Events）：

| 目标 | 角色 | 名称 |
|---|---|---|
| 打开记忆面板 | `AXCheckBox` | `记忆` |
| 四个 tab | `AXCheckBox` | `记忆列表` / `关系图` / `操作记录` / `任务` |
| 记忆列表翻页 | `AXButton` | `下一页` |
| 遗忘 | `AXButton` | `忘记这条记忆`（同名多条时用 `ax_click_nth.sh`） |
| 审计授权 | `AXButton` | `查看我的记忆操作记录（仅元数据）` |
| 审计翻页 | `AXButton` | `读取记录`（读过一页后改名 `下一页`） |
| 本机执行审计 · Run | `AXButton` | `读取终态 Run 审计` |
| 本机执行审计 · Run 内操作 | `AXButton` | `查看该 Run 的操作` |
| 本机执行审计 · 记忆调用 | `AXButton` | `读取记忆调用记录` |
| 结束取证 | `AXButton` | `结束本次查看` |
| 任务搜索框 | `AXTextField` | `搜索任务`（placeholder `输入任务标题、目标或关键词`） |
| 任务精确打开 | `AXButton` | `精确打开` |
| 发送 | `AXButton` | `发送`（运行中变 `■ 停止`） |

---

## 4. 判定规则：PASS / FAIL / BLOCKED / INCONCLUSIVE

**明确的 FAIL 条件**
- 重启后 `human_memory_primary_conversations` 出现第二行，或 `writable=1` 的行不止一条；
- 重启后主对话 ID 变化（同一 userdata 却换了 `primary_conversation_id`）；
- T14 打开的是任务 B（搜错任务），或 RESUME/DECISIONS 里混进了任务 B 的内容；
- T18 遗忘后 `suppression_directives` / `suppression_targets` / `evidence_envelopes` 任一行数**减少**
  （物理删除禁令，`HM-TO-R1`）；
- T19 的召回结果仍命中被遗忘的 `memory_id`；
- T20 显示原始对话轮被隐藏（违反 2026-09-07 产品决定第 2 条）；
- T17 模糊愿望产生了 pending Prospective 或调度注册；
- T18/T21 两个纯 UI 轮新增了 `provider_invocations` 行；
- `request_json` 命中凭据形状；
- 指纹重放与 `request_fingerprint` 不等。

**BLOCKED（缺陷登记，不记 FAIL）**
- 重启后 UI 长期停在「等待主对话就绪 / 正在重新读取…」，发送不再产生 Run
  （既知 `primary_read_policy_unavailable` 家族）→ 整个 flow2 BLOCKED，记缺陷；
- provider 传输超时导致 Run 停摆（既知 F06：`transport_timeout` / `provider_attempt.degraded` /
  `reconcile.unknown_settled`）→ 该轮 BLOCKED，同 userdata 再重启一次从该轮 `--start` 续跑
  （**注意：这会产生第三个 run 目录 `<E3>`，核对时把它当 flow2 的日志之一记入结果文档**）；
- 审计 grant 5 分钟内没点完导致过期 → T21 BLOCKED，可在同一进程内重做一次 T21；
- 模型拒不调用文件工具（T3/T4/T15/T16 无 effect）→ 正向业务结果无从取证，记 BLOCKED（路由/环境）；
- 任一轮超过 8 分钟未见终态：驱动记 `timeout`，该轮相关断言降级。

**INCONCLUSIVE**
- T13 搜索返回 0 候选（`task_scope_search` 索引尚未落库）→ TF-5 INCONCLUSIVE，
  备用杠杆：改问「把最近的任务列出来，然后精确打开『霜降素材整理』」重试一次；
- T16 模型没走 procedure 路径（`procedure_uses` 增量 0）→ TF-7 INCONCLUSIVE，
  备用杠杆：改说「按我存过的名字叫『霜降清点』的那个流程做」重试一次；
- T12 答对了目录但该窗口没有任何 typed recall 记录 → 说明答案来自最近上下文而非召回，
  TF-6 记 INCONCLUSIVE（跨重启召回未被证明）；
- 屏幕锁定 / System Events 枚举不到窗口 → 整轮作废重跑，不判定。

**不计入判定**
- 截图缺失（全部以 DB/日志为证据）。

---

## 5. 执行与证据归档

```
# 核对（--evidence 用第一次启动的 <E1>，它拥有 userdata；
#  必须用 backend/.venv 的 Python 3.12——指纹重放要 import 安装目标的 simple_harness）
backend/.venv/bin/python scripts/native/twoflow_verify.py --evidence <E1> \
  --second-log <E2>/native.log --second-launch <E2>/launch.json \
  --installed-target <installed-…> [--out <E1>/twoflow-verify.json] [--json-only]
# 自检（无需真实证据）
backend/.venv/bin/python scripts/native/twoflow_verify.py --selftest
```

> `twoflow_verify.py` 直接 `import a6_verify`（复用 `RoDb` / `Item` / `Evidence` /
> `build_replayer` / `CREDENTIAL_PATTERNS`），两个脚本必须留在同一目录。
> 用系统 Python 3.9 跑会让 TF-15 因 `StrEnum` 导入失败而 INCONCLUSIVE。
> 自检已跑通：20 项判定全部可执行；在 A6 尝试 5 的真实证据库上冒烟，
> TF-15 指纹重放 118/118 全等、无误判 FAIL。

退出后对 `<E1>/userdata/data/{state.db,sdk-product-state.db,human_memory_v7.db,operation-audit.db}`、
`<E1>/userdata/data/simple-harness-sdk/execution-v6.sqlite3`、`<E1>/native.log`、`<E2>/native.log`、
`<E1>/twoflow-progress.jsonl` 逐个取 SHA-256（核对脚本已自动输出），记入本目录的
`RUN-01-RESULT.md`。原始证据只留在 `.local-test-evidence/2026-09-09/…`，Git 只存结论与 SHA-256。

预期总时长：**flow1 11 轮 ≈ 35–55 分钟；重启 ≈ 5 分钟；flow2 11 轮 ≈ 40–60 分钟**，
合计约 1.5–2 小时（T18/T21 两轮 UI 取证需要人在场）。
