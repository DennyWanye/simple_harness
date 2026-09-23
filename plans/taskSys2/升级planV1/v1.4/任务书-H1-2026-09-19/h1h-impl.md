你是 H1-H 接线实施者。只在 SDK 独立 worktree 中工作，基线为 `main@5ac3f05` 加 HTTP 入口修复 `af56b90`。先读：

- `simpleharness-llm-native-htn-execution-plan-v2.zh-CN.md` §35–47；
- `LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md` §五–八及 2026-09-19 15:20 追加裁定；
- `H1-V2对照源码冲突检查-2026-09-18.zh-CN.md` §1、§5、§6；
- `planning/decision_codec.py`、`planning/decision_admission.py`、`planning/decision_adapter.py`、`storage/planning_decision_store.py`。

允许改动：`src/agent_orchestrator/api/missions.py`、`src/agent_orchestrator/orchestrator/event_handler.py`、`src/agent_orchestrator/orchestrator/hierarchical_dispatch.py`、必要的 H1-H focused tests，以及本任务书列明的 journal。不得改旧编译器语义、旧协议字节、H1-A–G 合同。

实施目标：

1. `planning_protocol_version` 从真实 HTTP request 进入 `MissionSpec`；缺省旧协议字节不变，未知/非字符串拒绝。
2. 旧协议走 `_collect_plan_hierarchical` 原路径，旧 `PlanningRejected` 字段与字节不变，旧任务不出现任何 `PlanningDecision*` 事件。
3. 新协议按 request ordinal 从 0 开始，复用现有“提案不可读→带 repair hint 重问”梯子，格式重试最多 1 次；不得新建第二套 retry loop。
4. 新协议主链为 codec → planning decision store → admission → adapter → 原有 ground/compile/commit；`WAIT`/`NO_CHANGE` 只持久化为 `NO_STATE_CHANGE`，不 compile、不产生 PlanRevision；`NO_CHANGE` 不走 `NoApplicableMethodDeclared`。
5. 新协议每次评价同时发旧 `PlanningRejected` 和 `PlanningDecisionEvaluated`；后者字段固定为 `decision_id, request_id, attempt_ordinal, decision_type, status, rejection_codes, canonical_hash`。旧协议不发新事件。
6. `BIND_EXISTING_GOAL` 与 `REPAIR/PROPOSE_SUCCESSOR` 本阶段只解码，准入回 `DECISION_TYPE_NOT_ENABLED`，不得静默执行或伪造旧提案。

先写行为测试再实现。若无法从现有 request/world/registry 记录无损组装 `AdmissionContext`，停止并在 journal 记录 blocker，不得用默认空值绕过准入。

---

## 本轮主编排补充裁定（2026-09-20，约束性增补，覆盖上文冲突条款）

用户已明确要求继续完成并授权按架构最佳实践自行决策。以下为对第 3、4 条的实施口径补充，不改变本任务书其余条款。

### 补充裁定 A：允许新增 `PlanningDecisionStore` 事务化受约束换绑方法

**新增白名单**（在原有 allowlist 之上扩展，其余限制不变）：

- `src/agent_orchestrator/storage/planning_decision_store.py`
- 必要的 store focused tests（如 `tests/orchestrator/unit/test_planning_decision_store_rebind.py`）

**原因：** `PlanningRequestBinding` 的 `intent_id` 在首次插入时被冻结（§34 请求身份一旦写入即不可变），而本任务书第 3 条要求"格式重试复用现有梯子"。真实链路为：第一次评价因 raw 不可读 → `UNREADABLE`（终态吸收，§36 不允许同一 ordinal 离开该状态，故不能原地改回）→ 带 repair hint 重问 → 第二次评价在**同一 `request_id`** 下产生**新的 `intent_id`**。现有 `insert_planning_request` 只能"内容全等幂等返回"或"不同内容 `StoreConflict`"，没有表达"同一 request 身份、绑定表须记录本次实际 intent"的受约束路径；若不新增，实施者只有两条错误出路：伪造/复用首个 `intent_id`（绑定表记录与真实 intent 不符），或新建第二个 `request_id`（违反同 request 重试，使 ordinal 语义与 §35 脱钩）。两者都是静默篡改或协议破坏，因此按架构最佳实践新增显式受约束方法，把该换绑做成一次性、事务内、可审计的操作。

**所需行为验收（必须全部由 focused tests 钉住，且是行为可区分，不接受源码字符串断言）：**

1. **真实梯子**：不可读 → 带 repair hint 重问 → 第二次评价走的仍是既有格式重试梯子（最多 1 次），不新建第二套 retry loop；`attempt_ordinal` 从 0 起，重试为 1。
2. **`created_at` 差异**：换绑后绑定行保留首次 `created_at`（请求身份建立时刻不变），仅 `intent_id` 及随后续写入变化的列更新；须断言"变更前 `intent_id` ≠ 变更后 `intent_id`"且"`created_at` 不变"，两条都必须可区分。
3. **冲突**：换绑对象必须是**同一 `request_id` 已存在的绑定**；不存在的 request 必须拒绝而非隐式插入。非同一 request、越权/越序换绑（如终态已 `COMMITTED` 后）必须 `StoreConflict`，且拒绝后行保持逐字节原样（校验先于语句执行）。
4. **回放**：同一 `(request_id, attempt_ordinal, raw_output_hash)` 回放返回既有行，不重复 compile、不重复发事件；同一 `(request_id, attempt_ordinal)` 不同 raw 仍是 Store identity conflict。换绑本身必须幂等：用已生效的相同 `intent_id` 再次调用不产生第二次写入、不改变 `created_at`。
5. **事务性**：换绑与同一事务内的决定行写入要么全部落库要么全部回滚；方法不得自行开连接、不得持有独立 connection。

**明确不允许：**

- 改变任何 schema / DDL / 列集合；
- 改变旧 `insert_planning_request` 的幂等语义与 `StoreConflict` 语义；
- 改变旧协议默认值、旧协议字节、H1-A–G 既有合同；
- `event_handler.py` 直接写 SQL（event_handler 只调用 store 方法，SQL 只存在于 store 内）。

### 补充裁定 B：字段映射纪律不变

本轮增补不解除上文的准入纪律：仍不得用默认空值补齐 `AdmissionContext`；某个字段确认无权威来源时单独记 blocker，不得复用旧 `apply_planner_reply` 绕过新协议。

### 状态

本轮仅为计划记录与白名单更新。**不宣称实现完成**——实现由另一 Claude CLI 代理在 SDK 独立 worktree 执行；核验由独立会话执行（见 `h1h-verify.md`）。
