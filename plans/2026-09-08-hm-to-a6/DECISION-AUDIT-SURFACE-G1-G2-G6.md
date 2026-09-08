# DECISION：受控审计读取面与两条对账回执（HM-AC-7 G1 / G2 / G5 / G6）

- 上游核对：`plans/2026-09-08-hm-to-a6/AUDIT-COVERAGE-2026-09-08.md`（G1、G2、G5、G6）
- 分支：`worktree-audit-surface`（基线 `fd89f5ca`）；装机 `simple-harness-memory-sdk 0.6.31`，**不改 SDK、不改 pin**
- 涉及文件：
  - Host 受控面：`backend/deskpet/operation_audit/human_access.py`、`backend/deskpet/memory/human_memory_api.py`、`backend/deskpet/memory/human_memory_service.py`
  - G1 回执：`backend/deskpet/operation_audit/page_in_receipts.py`（新）、`backend/deskpet/tools/context_page_in_tools.py`、`backend/main.py`
  - G2 回执：`backend/deskpet/task_scope/search.py`、`backend/deskpet/sdk_adapters/context_route.py`
  - 核对器：`backend/deskpet/quality/audit_coverage.py`
  - 前端：`tauri-app/src/primary/auditRequests.ts`、`tauri-app/src/components/PrimaryAuditPanel.tsx`、`tauri-app/src/primary/UI-CONTRACT.md`
- 本次**不动**：SDK（G3/G4/G7 仍是 SDK followup）、`foreground_runtime._record_audit`（G8）、`MemoryAttemptJournal.page()` 的 coverage 常量（G9）

---

## 0. 一句话

Host 侧终态 Run 审计页与记忆调用日志，第一次有了**和记忆记录同一张授权**下的只读分页面
（`primary.audit.host.page`）；page-in 与 TaskScope search/open 这两处"只有 effect 头、无法逐条对账"的
操作，各自补上一条**不含正文**的持久回执，核对器因此可以按 `effect_id` 一一对账。

---

## 1. G6：`primary.audit.host.page`（新受控读取操作）

### 判断

缺口是"发射有、读取面没有"：`audit_pages` / `memory_call_attempts` 只有进程内 `AuditStore.inspect`、
`journal.page()`。可选的三条路：

| 方案 | 取舍 | 结论 |
|---|---|---|
| 把 Host 行塞进 `primary.audit.page` 的 OA1 分页 | 要么伪造 family，要么改 SDK 的 item 契约；Host 行没有 OA1 的 `family/occurred_at` 形状 | 否 |
| 新开一个顶层操作 `primary.audit.runs`（独立授权） | 用户要点两次授权才能看完一次"为什么这么做"；两套过期/预算难对齐 | 否 |
| **同一 grant 下加一个操作，按 section 分流** | 授权语义不变（一次显式授权 = 一次取证会话），读取是本机只读、不花 SDK 预算 | **采用** |

### 契约

请求字段固定为 `{primary_ref, audit_ref, page_action_id, section, cursor_ref, target_ref}`，
`section ∈ {runs, run_operations, memory_calls}`，`target_ref` **仅** `run_operations` 必填、其余必须为空
（不满足即 `primary_audit_request_invalid`）。

- **一次授权、两族读取**：仍走 `HUMAN_AUDIT_OPERATIONS` 与写者栅栏，`_human_audit_runtime()` 的
  lease/`local_owner_auth` 校验不变；最终发送前由 `check_primary_audit_response` 在活的 lease 下再查一次 grant。
- **不花 SDK 预算**：Host 读取不经 `manager()`、不产生 SDK charge，因此不动 `human_audit_grants.reads`；
  每个 section+target 是**独立的流**，各自 32 页（`HOST_MAX_READS`）。总量上限 = 用户显式点击次数 × 页大小，
  且被 grant 的 300s TTL 兜住——这是本次接受的唯一一处"预算不是全局单调"的取舍：
  Host 读是本机 sqlite 只读，没有对外成本，按流计费比按会话计费更能让用户读完一个 Run。
- **一次动作一份交付**：`human_audit_host_deliveries(audit_ref, action_id)` 唯一；同 key 同输入重放返回同一份
  已保存响应（并校验 `response_hash`），同 key 换输入是 `primary_audit_action_conflict`。
  与 SDK 页不同的是，Host 读取"请求→读→存"在**同一个事务**里完成，因为它不可能部分收费：
  中途失败（`before_host_page` 故障点）不留 delivery、不留 stream、不计读数（有测试）。
- **快照冻结**：流的第一页固定 `snapshot_at`，之后所有页 `created_at<=snapshot_at`；
  授权之后新入库的 Run 不会挤进本次枚举（有测试）。`cursor_ref` 与流状态必须严格配对，流在一张授权内**不能重开**。
- **只投影、不外泄**：`run_operations` 只取审计页里 `record_type ∈ {head, boundary}` 的记录，
  且只投影 `_OPERATION_FIELDS` 白名单 + `usage` 的数值字段；`arguments/content` 一类键在白名单外，
  测试直接断言序列化结果里不含 `SECRET` / `arguments`。`memory_calls` 按 `owner_ref(principal)` 过滤，
  并要求 `principal.actor_id == grant.subject`。
- **关闭即不可读**：close 之后同一 `page_action_id` 的重放返回 `primary_audit_closed`。

### 前端（「操作记录」tab）

沿用 `UI-CONTRACT.md` 的既有约束：无自动授权、无自动翻页、无轮询、无跨授权缓存。
新增「本机执行审计」小节只在**已有 grant** 时出现，两个按钮（终态 Run 审计 / 记忆调用记录）与每行的
「查看该 Run 的操作」都是显式点击；close / rebind / 过期 / 隐藏 tab 会连同 Host 页一起清空。
响应逐字段核对（section/target 回声、`snapshot_hash` 不变、`reads_used` 必须 +1、条数不超过该 section 的页上限、
逐行 item 形状），任何一项不过就整页不确认、保留同一 `page_action_id` 可重试。

---

## 2. G1：page-in 引用的持久回执

`ContextPageInStore` 是进程内 TTL 存储，引用的发放/消费此前只有 effect 头。
新增 `context_page_in_receipts`（落在 `operation-audit.db` 旁路，不进业务库）：

- 三种 phase：`issued`（`put` 同步写）、`consumed` / `denied`（工具处理器异步写，带 SDK `run_id` / `effect_id`）；
- **无正文**：引用 id、source、session/request/scope 全部只存 `digest(...)` 域名化哈希，
  只有 `source_hash`（本就是内容哈希）、封闭的 `outcome` 码与时间戳按原样存；
- **不改变工具结果**：写回执失败只记 `page_in_receipt_unavailable` 日志，异步路径还套了 0.5s 超时；
  为此把处理器里几处早返回改写成 `outcome` 汇聚后统一返回，错误码集合与原来逐字相同。
- 取舍：`issued` 走同步写（`put` 是同步 API），最坏情况在 sqlite 竞争下阻塞 0.5s。
  选同步而不是"发完再补"，是因为消费回执必须晚于发放回执才能对账；
  写入很小、`operation-audit.db` 由 `AuditStore` 建库时已是 WAL，且建表只在首次写时执行一次。

## 3. G2：search/open 访问回执带 effect/run 引用

`task_scope_search_access_receipts.receipt_json` 升到 `schema_version 2`，增加 `effect_id` / `sdk_run_id`
（HUMAN 通道读取时两者为 `null`，且必须**同时**给或同时不给，否则
`human_memory_search_access_ref_invalid`，拒绝时不写回执）。
`receipt_hash` 覆盖新字段，所以同一 query 在不同 effect 下是不同回执——这正是逐条对账所需；
已检查无任何调用方按 v1 形状重算该 hash。
`context_route.handle_task_scope_search` 把自身 effect/run 带进 search 与随后的每次 open；
`_identity()` 提前到 `try` 内，缺 envelope 时在写任何回执之前就失败关闭。

## 4. G5：无 UI 的端到端演练

`backend/tests/operation_audit/test_audit_surface_exercise.py`：
grant → SDK OA1 页 → Host runs / run_operations / memory_calls 三页 → close，
每一步都经过生产 `/ws/control` 的同一条路径（dispatch + 最终发送前 `check_primary_audit_response`），
断言六次投递全部 ok、响应里不含种子证据文本，最后**用核对器读同一份证据布局**，
要求 `primary.audit.page(OA1).exercised`、`host.audit_pages.exercised`、
`host.memory_call_attempts.exercised` 均为 true。

## 5. 核对器改动

- `context_page_in`：有 `context_page_in_receipts` 表时，按 `effect_id` 要求每个 effect 有 consumed/denied 回执，
  并校验 `outcome=="ok"` 与 effect state 一致；无该表（旧证据）时退回 effect 级审计并标注"早于 G1"。
- `task_scope_search_open`：见到 `schema_version 2` 回执就逐条按 `effect_id` 对账，
  全是 v1 时仍按 operation 计数并保持 ◐。
- 受控面事实：`host.audit_pages` / `host.memory_call_attempts` 的 `ui_operation` 不再是 `None`，
  并给出 `host_deliveries` / `exercised`；新增 `host.context_page_in_receipts`。
- `SURFACE_PAGES` / `SURFACE_JOURNAL` 两个常量同步改写（原文写着"无 `primary.audit.*` 入口"）。

**旧证据上的效果**：`.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1` 仍是
observed 741 / audited 741 / missing 0、无标识泄漏；`context_page_in` 与 `task_scope_search_open` 依旧是 ◐，
`exercised` 全为 false——因为 G1/G2 回执与 Host 交付表只可能出现在**新的运行**里。
G5/G6 要在真实运行里判定为"已实测"，依赖 00-PLAN 追加的 T16/T24 操作记录取证。

## 6. 未处理（仍是 followup）

G3（`no_mutation` 无结构化决策记录）、G4（Host raise 时两侧只能按 run ref 关联）、
G7（procedure / prospective / current-input 无 OA1 family）都在 SDK 侧；
G8/G9 维持 `AUDIT-COVERAGE-2026-09-08.md` 的"记录不修"结论。
另：`preparation_audit_sources` 仍无读取面，本次未纳入 section（真实运行里该表为 0 行，先不做）。
