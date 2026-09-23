# V1.4 / H1-H：AdmissionContext 三个 Blocker 裁定补遗

**编号：H1H-ADM-1.0 · 日期：2026-09-20**  
**用途：给实施 Agent、独立核验 Agent 的代码级施工规格。不是已合入补丁。**  
**范围：authorization、operation identity/state、candidate preview，以及连接三者的 H1-H 提交链。**

## 0. 先读：本次裁定改变什么、不改变什么

**先把本补遗与 source-map 入库，再继续 H1-H。允许增加下文明确限定的内部合同、producer、持久映射和预览接口；禁止在原任务书下自行填默认值绕过 blocker。**

本补遗修正原 V2 §24、§34、§43–45、§59–60 的接线不足：

1. 规划授权单独建模；**规划委派权 ≠ Method 前提 ≠ 外部动作审批**。
2. frozen OperationEnvelope 与真实动作账本之间建立显式身份桥；**映射表不保存第二份执行状态**。
3. 改为 **Decision Pre-admission → 纯编译预览 → Plan Admission → 事务内复核并 Commit**。不得先造 `PlanShapeView()` 再声称完成检查。
4. `WAIT/NO_CHANGE/DECLARE_BLOCKED` 使用有类型的“不改计划”分支；它们不需要候选形状，不得读不到 operations 就填空集合。
5. H1 只执行 `REFINE`、`REPAIR/REPLACE_METHOD`、三个不改图决定。`BIND_EXISTING_GOAL`、`REPAIR/PROPOSE_SUCCESSOR` 继续只解码、不执行；其他类型沿用已批准 enablement。**统一 wire、Prompt 和现有 canonical bytes 不因本补遗改变。**
6. NanoJev PR-7、`RETRY_OR_ESCALATE`、Shadow、HTN/Jev 专项证据不属于本次解锁条件，不抢占或替换本工作的权威。
7. focused 通过只能解除三个 blocker；独立核验、H1-I 真实模型专项、完整 H1 门禁仍必须执行。只使用当前已批准的 **GPT-5.6 系列**配置，不换 Claude/Grok。

### 0.1 证据边界与基线

| 类别 | 本轮能确认什么 |
|---|---|
| 用户 handoff | Host `6c457908…`、SDK `51dbed2e…`、候选 `102ad3df…`，均有未提交改动；19 passed / 13 failed 是 handoff 的报告，不是本轮复跑 |
| 用户本地裁定 | 09-19 的只解码决定、NanoJev 延后，按用户 handoff 原样保留；本轮没有读到这些本地原文件 |
| 已读原 V2 文件 | §43 列准入项目，§44–45 列 Adapter/compile/Commit 顺序；未给完整 producer、join、preview 合同 [S1] |
| 可访问远端参考 | `5ac3f05890a4c5160e2f103a01f863753ea5d498` 的准入、审批、账本、验证器等源码 [S2–S11] |
| 无法读取 | 所给本地 SDK 两个提交在连接中找不到；Host 指定文件 404；**未提交 diff 无法从远端读取** |

远端参考 **不取代**候选 worktree 基线。严禁为对齐本文执行 `reset/clean/cherry-pick/rebase/pull`。路径中的具体函数若已等价实现，只做精确映射，不复制第二套。

### 0.2 开工前一次性的本地对齐（不能省略，也不是重新设计）

在三个已有 worktree 分别记录 `git rev-parse HEAD`、`git status --short`、`git diff --name-only`；只对 allowlist 源文件保存哈希和审阅用 diff，不复制凭证/数据库/完整模型输出。记录到 `h1h-admission-source-map.json`：

```json
{
  "amendment": "H1H-ADM-1.0",
  "candidate_head": "实际 git rev-parse HEAD",
  "contract_sources": ["原计划实际路径和SHA256", "09-18补遗实际路径和SHA256", "09-19裁定实际路径和SHA256"],
  "symbols": [
    {"logical": "planning_request_reader", "path": "实际文件", "symbol": "实际函数", "sha256": "实际哈希"},
    {"logical": "mission_owner_guard", "path": "实际文件", "symbol": "实际函数", "sha256": "实际哈希"},
    {"logical": "pure_compile_kernel", "path": "实际文件", "symbol": "实际函数", "sha256": "实际哈希"}
  ],
  "decision_enablement": ["REFINE", "REPAIR/REPLACE_METHOD", "WAIT", "NO_CHANGE", "DECLARE_BLOCKED"]
}
```

上面是**必须实际填写的检查产物，不是生产配置**。缺项使该接缝保持 `SOURCE_UNAVAILABLE`；不能由执行 Agent 猜定权限或关联键。若实际 09-19 裁定与用户 handoff 冲突，记录冲突，不自动选宽松版本。

## 1. 对 handoff 五个问题的逐项裁定

| Blocker | 规格依据 → 缺口 | 权威来源 | 允许改动 | focused 核验 | 是否修订 |
|---|---|---|---|---|---|
| authorization | V2 §24/§43 要求 authority；§32 禁模型设权；没有 mission planning grant producer | 固定在 Host/facade 实例的 Principal/tenant、Mission 归属、**本补遗新增的显式规划委派记录**；动作审批继续读现有 approvals | §3 的 producer/授权 API/side binding；不冒充 action approval | A01–A08 | **必须；本补遗即修订** |
| operations | V2 §43/§59 要求 UNKNOWN 闸门；未规定 OperationEnvelope 到 actions 的 identity bridge | `operation_identities/operation_bindings` 的冻结身份 + `actions` 精确 action_key + 原回执/核对记录 + 实际在途 Attempt | §4 的只读 mapper、显式写时链接、原 handoff 增加链接完整性门 | O01–O10 | **必须；不允许文本匹配** |
| plan_shape | V2 §43–45 的原顺序为先 admission 后 compile；原文没有 PlanShapeView | Adapter 生成的 typed proposal，经相同编译内核与 `validate_delta()` 得到的报告 | §5 的 pre-admission/preview/plan-admission；无写预览；不改图算法 | P01–P10 | **必须；不是补个默认 valid 值** |

H1-H 允许实施上述新增项，但授权范围仅如下文 allowlist。不允许顺带做 H2/H4 全部功能、开放新 Decision 或重建全局 RBAC/Operation 引擎。

## 2. 统一管线、类型与失败分类

### 2.1 接线顺序（严格）

```text
收到实际 AgentTurn 结果
  → 原 PlanningDecisionStore 幂等保存/解码
  → 查原 request 绑定、原 visible_refs、subject、Prompt/package、enablement
  → 读取 planning authority（同一一致快照）并检查真实委派
  ├─ WAIT / NO_CHANGE / DECLARE_BLOCKED
  │    → 各自现有非改图语义 → 原子记 NO_STATE_CHANGE + 回执
  └─ REFINE / REPAIR-REPLACE
       → Method/参数/证据/能力/预算预检查
       → 得到 PreAdmittedPlanningDecision（不是最终许可）
       → 只读收集 operations 和实际在途工作
       → Adapter → 同一纯 ground/compile 内核 → validate_delta
       → Plan Admission：结构、完整性、operations、runtime convergence
       ├─ 需收敛：持久 DEFERRED；既有受控收敛；再读快照、重新检查
       └─ 合法：AdmittedPlan（绑定确切 delta 和所有读取证据）
            → 短写事务：查原 CommitReceipt → 复核 → commit_plan_revision
            → 同事务关联 decision/回执/outbox；事务外真正派发
```

预检查 **绝不调用**带取消、写 witness、写事件、预留预算或 Commit 的 `apply_planner_reply()`。不能用“执行后 rollback”模拟无副作用：外部调用和文件改写不受 SQLite rollback 保护。

### 2.2 不再让一个 Context 强制装下所有阶段

在 `planning/decision_admission.py` 内拆内部调用面；不修改模型 wire：

```python
# 新的内部类型；字段的完整原类型由现有 AdmissionContext 复用。
# 不使用 context: Any，不保留宽松 default factory。
DecisionAdmissionContext = 原 AdmissionContext 的请求/身份/引用/Method/预算字段 + PlanningAuthorizationSnapshot
# 不含 operations、plan_shape。

PreAdmissionResult = PreAdmittedPlanningDecision | NoMutationDecision | PlanningFeedbackV1 | SourceUnavailable

PlanAdmissionContext = OperationSnapshot + RuntimeWorkSnapshot + CandidatePreview
PlanAdmissionResult = AdmittedPlan | DeferredDecision | PlanningFeedbackV1 | SourceUnavailable
```

目标函数：

```python
pre_admit_planning_decision(decision, *, context: DecisionAdmissionContext) -> PreAdmissionResult
adapt_for_preview(pre_admitted, *, context: AdapterContext) -> PlanProposal
preview_candidate(proposal, *, inputs: PreviewInputs) -> CandidatePreview | PreviewUnavailable
admit_compiled_plan(pre_admitted, *, context: PlanAdmissionContext) -> PlanAdmissionResult
commit_admitted_plan(admitted: AdmittedPlan, *, principal: PlanPrincipal) -> PlanCommitReceipt
```

`PreAdmittedPlanningDecision` 不得被 `commit_admitted_plan` 接受。Adapter 的字段转换函数可以复用，但不能先创建假的旧 `AdmittedPlanningDecision` 再绕过类型门。

旧 `admit_planning_decision()` 如被 H1-A–G 测试/其他内部调用使用，保留组合 wrapper：**只有调用方已提供完整且绑定正确的 preview，才能调用最终阶段**。不允许 `skip_shape=True`、`operations=None→()` 等捷径。H1-H 生产路径只走新分阶段入口。

### 2.3 三种“不通过”不能混用

| 类型 | 持久结果 | 是否再问模型 |
|---|---|---|
| 模型/提案错误，如参数错、图有环 | 原 `PlanningFeedbackV1`，确定错误码 | 按既有预算允许 |
| 合法提案等待真实在途工作、UNKNOWN 核对 | 新内部 check phase=`DEFERRED`，decision 保持未终态；不伪造新模型结果 | **不因轮询再花 Planner token** |
| producer 缺失、join 不完整、校验未执行、数据库不可读 | check phase=`SOURCE_UNAVAILABLE`，对外原 `INTERNAL_CONTRACT_ERROR` + 结构化内部原因 | **不算模型格式错误，不自动让模型补值** |

授权确实不存在/已拒绝：`AUTHORIZATION_REQUIRED`。请求所绑授权后来变化：`REQUEST_BINDING_STALE`。这些和 producer 本身无法读取要分开。

旧决策拒绝码枚举不扩容；详细原因放 `PlanningProblemDetailV1.detail` 与审计 `origin=SYSTEM|MODEL|RUNTIME`。未识别的源状态/新枚举一律 fail-closed，不用字符串包含 `cycle` 等推测类别。

## 3. Blocker A：规划授权的权威生产链

### 3.1 明确不从哪里取

- `applicability.authorization_gate()` 只证明受检前提的证据足够，不是用户把 Mission 的规划权委派给当前服务 [S4]。
- `required_approvals(level)` 是风险级别对应**份数**；不是 approval id，不是 grant receipt [S3]。
- `SemanticReadSetChecker.authority_state()` 已读代码调用 `Store.get_approval()`；这是动作审批通道，不能往里塞新 planning grant ID [S5]。
- 原 `AuthorizationView(approval_granted=True, required_approvals=())` 默认值不作为生产合法来源 [S2]。

### 3.2 授权对象与规则

**本补遗新增规划专用持久委派，不伪造审批。** H1 的权限只包括当前 Mission 范围内提出/采用 REFINE、已允许的替换修复、报告状态；不包括外部调用批准、扩大工具权限、修改用户要求、知识晋级或直接宣告完成。

规划一个未来需要审批的动作可以合法；具体 action 未有用户批准，仍在现有 ActionExecutor 的 handoff 门停止。不得因为未来 approval 还不存在，让 Planner 无法先生成待审批候选。

`PlanningAuthorizationSnapshot` 必须至少包含：

| 字段 | 来源/含义 |
|---|---|
| `request_id, mission_id, tenant_id` | 原 PlanningRequest + Mission 记录；不是模型 JSON |
| `scope_id, planner_principal_id` | Host/Orchestrator 构造的服务主体与 scope；必须等于冻结 request side binding |
| `grant_id, grant_revision, grant_hash` | 下述持久委派记录，按 grant_id 最大 revision 读取当前版本 |
| `issuer_id, issuer_command_id, issuer_receipt_hash` | 真实签发命令与已认证 Host 主体；不能凭服务名合成 |
| `allowed_decisions` | 签发时 server policy 限定；不能来自 Decision payload |
| `policy_hash` | 规划通道政策版本内容身份；政策改变使旧 request 过期 |
| `not_before_ms, expires_at_ms` | server 计算的 TTL，不由 Planner 指定；时间源使用现有 Store 时钟 |
| `active` | 当前 grant revision 的真实状态；无 default |
| `checked_at_ms` | 本次读取/判断时间，不参与“同一 grant 内容”的身份哈希 |

旧 `AuthorizationView` 字段不再适合表达这些事实。允许在**内部 admission 模块**替换/扩展它并迁移调用者。动作审批仍使用原 `permissions.py` 类型和 approvals 表。

### 3.3 谁签发：明确 API，不把 boolean 移到另一个文件

新增 `api/planning_authorization.py::PlanningAuthorizationApi`：构造参数沿用 facade 可信边界：

```python
PlanningAuthorizationApi(commit, *, tenant_id: str, principal: Principal,
                         deployment: PlanningLanePolicy)
```

`Principal` 与 tenant 来自已认证 Host/facade 实例，不从 HTTP body、LLM 或环境变量读取。现有 `MissionControlV1` 正是构造时固定 caller，并以 `_mission()` 对照 tenant [S6]。

提供三个**系统入口，不注册成模型工具**：

```python
issue(mission_id: str, *, command_id: str) -> PlanningGrantReceipt
renew(grant_id: str, *, expected_revision: int, command_id: str) -> PlanningGrantReceipt
revoke(grant_id: str, *, expected_revision: int, command_id: str, reason: str) -> PlanningGrantReceipt
```

`issue` 的精确流程：

1. 使用同一 Mission tenant guard；本版本不增加新“owner=LLM 输出”规则。
2. 确认 Mission 显式使用 `planning-decision-v1`、未终结；绑定 stable service `PlanPrincipal.principal_id` 和 scope，值从实际 Host 装配读取。
3. 采用服务端固定 `planning-lane-policy-v1`：上述五个 enablement key、现有工具/风险授权的交集；TTL 本补遗设为 **24 小时**。续期是新 revision，旧请求不可继承新许可。TTL 是新规划委派政策，不修改旧 DeploymentPolicy canonical bytes。
4. 写事务中，先查相同 `command_id` 的既有 receipt。同 command 同输入返回原结果，不延长 TTL；不同输入 `StoreConflict`。
5. grant ID 用现有稳定 ID 工具从 `mission_id + service principal + scope + first issuer_command_id` 产生；首次 revision=1。后续撤回/续期追加 revision，不覆写历史。
6. 同事务写 grant、签发事件/回执。`issuer_receipt_hash` 是这次真实写入命令回执的 hash，不是假 approval hash；Receipt ID 与签发内容 hash 无循环依赖：先冻结 command body hash，再生成事件/回执。
7. Host 创建/启用**新协议** Mission 后、首次 Planner request 前显式调用 `issue`。已有候选 Mission 无 grant 时暂停新规划，由当前已认证操作者显式补签；不能迁移时自动签发。
8. 新 Planner request 在派发前，同一事务绑定 grant identity 到 `planning_request_authority_bindings`。

**若 Host 当前没有可以认证/固定 Principal 的入口，SDK producer 可实现并测试，但该部署不能放行。** 需要先把这个新增 API 接到实际已认证入口；本补遗已授权这一条接线，不授权另造认证系统。

`renew/revoke` 与 issue 共用 Mission tenant/固定 caller 检查，在同一写事务读取当前 grant 最大 revision，并做 expected_revision CAS。renew 只对当前 ACTIVE 且 Mission 未终结的 grant，保留原 grantee/scope 和获准动作交集，按当前政策重算时效，追加 revision+1；REVOKED grant 不自动复活。revoke 追加 active=false 的 revision+1，保留原期限和准入范围。两者都记录真实命令回执，重复 command 幂等。没有身份或 CAS 冲突时不写任何新 grant。续期 API 不向 Planner 暴露。

### 3.4 生产者函数与查询

新增 `governance/planning_authorization.py`：

```python
build_planning_authorization(
    request_id: str, *, read: PlanningAuthorityReader,
    caller: PlanPrincipal, policy: PlanningLanePolicy, now_ms: int
) -> PlanningAuthorizationSnapshot | SourceUnavailable
```

`PlanningAuthorityReader` 只读以下三个来源：

```text
PlanningDecisionStore 里的冻结 request
Store.get_mission(request.mission_id)
PlanningAdmissionStore 里的 request-authority binding + 当前 grant revision
```

当前 grant 查询必须同一 grant lineage：

```sql
SELECT * FROM planning_lane_grants
WHERE grant_id = ? ORDER BY revision DESC LIMIT 1;
```

检查 request/mission/tenant/scope/grantee 对齐 → grant revision/hash 等于发包绑定 → 当前 policy hash → active/时效 →决定 key 属于 allowed。producer 只读，不能顺手补签、续期、创建 approval。

**模型引用的 `reason_refs` 不等于安全 read-set。**此 snapshot 不必暴露给模型，系统把它纳入补充 Commit guard；不向旧 `_read_set.authority_state()` 塞假的审批 ID。必要的动作审批仍由现有 action gate 原样核对。

### 3.5 复核与撤权

- 发包时保存 grant revision/hash；收包时读当前 grant；Commit 前再读一次。
- grant 撤回/续期或 policy 变化，使未提交的旧决定 `REQUEST_BINDING_STALE`，不得自动改绑到新 revision。
- 已提交命令重送：先检查 caller 现在有权读取该 Mission，再返回原 CommitReceipt；**不是再次使用旧 grant 产生新变更**。
- WAIT/NO_CHANGE/DECLARE_BLOCKED 仍需合法 request/caller/scope；它们无需外部动作批准、不耗新的执行准入预算。已撤权回复可保存原始审计，但不应用它携带的副作用建议。

## 4. Blocker O：Operation 身份到实际 actions 的无损映射

### 4.1 权威与基数：不是“一 Task 对一 action”

已读 `operation_bindings` 有 `operation_occurrence_id, operation_id, request_hash, principal_id, scope_id, mission_id, obligation_id` [S7]；它没有运行状态。

已读 actions 记录有精确 `action_key`、`action_id`、`version`、`idempotency_key`、参数/产物 hash、task/result/attempt、state/handoffs/receipt [S8]。

因此采用：

```text
一个 frozen OperationOccurrence/OperationId + request_hash
          ↔ 一个被绑定的 legacy action_key（包含它的 version）
          → 多次 handoff / 多条核对观察 / 迟到回执

一个 HTN primitive occurrence → 可以拥有多个 OperationOccurrence
替换 Task/Method → 不生成新的同意图 OperationId
```

不得按 task_id 单独 join，不得按 target/connector 文本相似、最后一条 action、当前活跃 plan 成员或相同 hash 猜“一一对应”。

### 4.2 新表只做 identity bridge

新增 `planning_operation_action_links`，完整 DDL 见附件 `reference/schema.sql`。其核心键：

```text
operation_id                       PK
operation_occurrence_id            UNIQUE
action_key                         UNIQUE，引用 actions 的精确键
(operation_id, request_hash)        引用 operation_identities
mission/principal/scope/obligation
producer_task_id / producer_htn_occurrence_id / 原 contract 与 plan revision
action_id / action_version / params_hash / idempotency_key
envelope_hash / provenance_receipt_id / link_hash
```

**该表没有 `state`、`unknown`、`reconciled`、`approval_granted` 列。**这些值每次从原记录求出，不建第二份可写执行账本。

### 4.3 谁写链接：只有真实 candidate→action 的系统交接点

允许给 `ActionCommitsMixin.propose_action()` 增加 **仅新协议使用**的 keyword-only 参数：

```python
planning_origin: BoundPlanningOperationOrigin | None = None
```

`BoundPlanningOperationOrigin` 来自系统已冻结 OperationEnvelope、原 Task/Occurrence/Obligation、具体 accepted result，不由 candidate JSON 填写。无参数时旧路径返回字节/事件原样。

新协议路径在**同一个 Store 写事务**内：

1. 校验 origin 的 Mission、scope、principal 与 OperationEnvelope 原字节/hash；由确定的 Task→occurrence 绑定取原身份。
2. 沿现有 `check_candidate / bind_artifact_params / propose_action` 生成或找到原 action version。
3. 校验 action 记录的实际 params_hash、artifact_hash、connector、operation、target 与冻结映射的一致性；复用现有 hash/normalize 函数，不能自造另一种 hash 公式。
4. 写 identity bridge；同冻结身份与同 action_key 重复是幂等。冲突 rollback，不能重写 link。
5. actions 记录与 bridge 一起提交；其后才允许 `begin_handoff()`。
6. 新协议的 `begin_handoff` 增加 bridge 必需检查：缺 link 不能发送。`rehandoff=True` 还必须核对 §4.6 的完整未应用证明与所有旧交接均已收敛；仅有 `reconcile=CONFIRMED_NOT_STARTED` 不够。无法取得这种证明则具名拒绝重发。旧协议不触发本补遗的新检查。

**重要限制：**旧 `business_action_id` 会把同 Mission、同目标、同操作合为同一业务动作 [S8]。本补遗不重写它；如果两次合法不同 occurrence 会被它折成同一 action_key，报 `operation_occurrence_alias_conflict` 并阻止交接，不能覆盖 link 或伪称已支持重复业务操作。这种业务扩展另行裁定。

### 4.4 历史/未提交候选数据怎么办

- 已有 link：精确验证后使用。
- 历史绑定未有 link：只有原始受控记录同时明确给出 OperationOccurrence 和 action_key/版本，才能通过审计 backfill 写入；输入必须来自原系统记录，不能仅有操作者手填猜测。
- 无这样的证据：`SourceUnavailable(operation_mapping_incomplete)`；保留 UNKNOWN 与旧记录，禁止改图/自动重发，允许 UI 展示和显式核对。
- 只是提前冻结 envelope、尚未生成 action：H1 mapper 不能凭缺行声称“从未发送”。本补遗的最小默认也是 `operation_mapping_incomplete`。后续如需要正常支持“预绑定未派发”，必须由原 action writer 出具完整、可复核的 NOT_MATERIALIZED 记录；**本次不让模型或 mapper 自填**。
- 唯一可明确排除的旧拒绝记录：`state=REFUSED AND handoffs=0 AND idempotency_key IS NULL`；它表示从未获派发身份的拒绝，仍纳入读取 digest。其他 unlinked records 不自动排除。

### 4.5 只读快照 producer

新增 `runtime/planning_operations.py`：

```python
build_operation_snapshot(
    mission_id: str, *, reader: OperationReader, now_ms: int
) -> OperationSnapshot | SourceUnavailable
```

必须一次一致读取（同一编排库 read transaction）：

1. Mission 的 **全部** operation_bindings（含已经退役方法下的身份）。
2. Mission 的全部 links。
3. `Store.list_actions(mission_id)` **不按 state 截断**，并逐项 `get_action(action_key)` 读取完整原记录；数据量超过配置上限则明确 INCOMPLETE，不返回部分“安全”结果。
4. 通过上述键逐项核对；遗漏 binding、悬空 link、未知 action、版本/hash 不一致、重复 alias → SourceUnavailable。
5. 对 action 用原有 `receipt_mismatch()` 和真实核对记录做身份验证。
6. 生成 `read_digest`：排序后的全部 identities、links、action 状态/历史/回执内容 hash 与已读集合计数；不仅 hash UNKNOWN 子集。
7. `COMPLETE_EMPTY` 只在三个完整读取集合都没有任何可执行操作时成立；空集合也有有效 snapshot digest。

H1 采用**Mission 范围保守 gate**。本补遗不引入跨分支精确影响优化；不能把未确认“无关”的 UNKNOWN 排除掉。读失败与真实 complete-empty 用不同 union 分支。

### 4.6 映射状态表（不得使用 `state != UNKNOWN`）

| 原动作事实 | producer 的实际结论 | 是否阻止新的改图 |
|---|---|---|
| PROPOSED/AWAITING_APPROVAL/APPROVED，handoffs=0 | NOT_HANDED_OFF | 不因 UNKNOWN gate 阻断；被退役范围仍需撤销这些可交接意图 |
| HANDED_OFF | IN_FLIGHT；lease 过期也不等于没执行 | 阻止/等待原核对 |
| UNKNOWN | UNRESOLVED | 阻止 |
| SUCCEEDED + 匹配该版本真实 receipt | APPLIED | 不因 UNKNOWN gate 阻断；换方法不能忽略已发生事实、不能改 OperationId 再做 |
| SUCCEEDED，无/错 receipt | SOURCE_UNAVAILABLE | 阻止；不是把它降成普通 FAILED |
| FAILED/REJECTED/REVOKED/EXPIRED/SUPERSEDED/CANCELLED/REFUSED，handoffs=0 | CONFIRMED_NOT_APPLIED（本地从未交接） | 可处理，但仍需合法新计划/预算 |
| 同上，handoffs>0 | 默认 UNRESOLVED | 不能只用终态名称放行 |
| 有明确未应用证明，覆盖全部 handoff，且排除迟到应用 | CONFIRMED_NOT_APPLIED | 可解除 UNKNOWN 阻塞；**不意味着本补遗批准重试** |
| 只有 `reconcile="CONFIRMED_NOT_STARTED"`、空 lookup、调用已取消或 lease expired | 仍 UNRESOLVED，除非有上一行的充分事实 | 不自动重试 |

`reconciled` 不保留为脱离具体 outcome 的 bool。核对结果必须同时说明是 APPLIED、CONFIRMED_NOT_APPLIED 还是仍不确定。

**当前 runtime `reconcile()` 可以自动再次 handoff [S9]。H1-H producer 严禁调用这个方法。**本次只读已经持久化的回执；核对由原受控执行路径负责。新协议不能因这个 mapper 触发自动重发；handoff gate 若未能验证精确 link/重发资格，继续阻断。旧运行时安全语义不在本补遗中放宽。

**负证明的生产者**只能是实际 connector 的持久核对回执 adapter。它要记录 action_key、准确幂等 key/params hash、覆盖的 handoff 序号集合、目标系统查询水位、权威性依据，以及旧请求为什么不可能再应用。现有回执没有这些信息就返回“证明不足”，不能新增一张只写 `no_late_apply=True` 的表来替代事实。本补遗允许为新协议在 action 的既有 history 里追加此结构，禁止改写旧 history；`record_reconciliation` 只接受受控 executor 提交并核对原 action identity 的回执。

### 4.7 Running work 是另一套真实记录

新增 `read_running_work(mission_id, retiring_instance_ids)`：沿**原计划**的 instance→child occurrence→Task→实际 Attempt/DispatchIntent/lease 查询。`MethodInstanceView.running_work` 由这个完整读结果派生，不能从 Operation 空集合推出 False。

返回：

```text
RuntimeWorkSnapshot
  retiring_instance_ids
  live_attempt_refs（含真实 owner/lease/generation）
  live_dispatch_intent_refs
  handed_off_actions
  snapshot_digest
```

外来活 lease 不抢占；Task 显示 CANCELLED 但真实 call 仍在途，不算已收敛。取消预留与费用继续由已有 P2.3s–v 路径处理。

## 5. Blocker P：候选形状的无副作用 preview

### 5.1 不再制造循环依赖

当前 `PlanShapeView` 注释要求“调用方对候选做结构预检”，但实际 `validate_delta()` 只能拿编译结果检查 [S2,S10]。正式裁定：

- 第一阶段 Pre-admission 不读取 PlanShape。
- Adapter 产生原 typed proposal；pure grounding/compiler 计算候选。
- 第二阶段 Plan Admission 读取真正报告。
- **同一候选编译结果进入 Commit，不重新用一套可能不同的算法生成。**

### 5.2 输入合同（全部显式注入）

新增 `planning/plan_preview.py`：

```text
PreviewInputs
  decision_id / decision_hash / request_id
  source_plan_revision / source_network_hash
  exact PlanProposal
  immutable TaskNetworkSnapshot
  registry snapshot + catalog snapshot + schema registry identity
  EvidenceSnapshot + PredicateRegistry snapshot
  source requirements revision
  scope/manager/budget snapshot refs
  GraphStructureBudget
  system identity seed（由原 command/request 派生）
  now_ms（同一读快照时间）
  RuntimeWorkSnapshot
```

这些字段**不是允许填写 `Mapping[str,Any]` 的清单**：数据类型复用实际 `PlanProposal/TaskNetworkSnapshot/MethodRegistry/Catalog/EvidenceSnapshot/GraphStructureBudget`；支持跨进程存储的部分用既有 `to_json/from_json`；运行时接口不得注入可写 Store。

`PreviewInputs` 的 registry/catalog 必须冻结，不将活对象随编译改变；条件求值不调用 Observer。没有必需输入返回 `PreviewUnavailable`。

### 5.3 实现方法

1. 从现有 `HierarchicalDispatch` 的 parse/assess/ground/compile 路径抽取**纯函数内核**，命名 `compile_candidate_from_snapshot()`；移出的只能是计算，不移动已经发生的 Commit 语义。
2. 保留旧方法外壳按原顺序调用。新 preview 只拿冻结输入，调用同一个 `ground_method / compile_refinement_bundle`。
3. 不调用 `issue_start_witnesses`、`record_method_applicability`、`reconcile_retiring_work`、`_release_attempt`、模型/solver、CAS 写入或任何 emit。
4. 对候选目标网络，调用 `validate_delta` 并显式传入 `registry, catalog, methods, snapshot, predicates, now_ms`；传入编译器产生的**候选 network**，避免缺省 merge 恢复旧 retired membership。
5. 读取 `DeltaReport` **以及** `projection_report/refinement_report`。不能只看 `report.ok`，忽略 NOT_CHECKED/PARTIAL_CHECK。
6. 给冻结产物计算 `delta_hash` 与 `snapshot_hash`。不要把原 proposal hash 偷充 delta hash。
7. 返回 `CandidatePreview`；任何异常不回填空 shape。

```text
CandidatePreview
  decision_hash / source_snapshot_hash / compilation_hash
  compilation: RefinementCompilation（真实结果）
  delta_report: DeltaReport
  plan_shape: CHECKED_VALID | CHECKED_INVALID
  mapped_problems: typed tuple
  required_convergence: RuntimeWorkSnapshot 中需收敛的精确对象
  validator_id/version
```

`PreviewUnavailable` 与 `NoPlanMutation` 是其他 union 分支；二者都不是“valid 的空图”。

### 5.4 在跑修复如何 preview，避免“先破坏旧计划再发现新方案不合法”

允许预览**拟采用的结构**中退旧上新，但不能把真实 runtime 标成终态来骗 compiler。将原 compilation 路径中“在途是否收敛”的检查提取为显式 `convergence requirement`：

- 结构/参数/证据错误：直接拒绝，尚未取消任何工作。
- 结构通过，确实有在途兄弟：Plan Admission 返回 `DEFERRED(RUNNING_WORK_NOT_RECONCILED)`。
- 由既有 Commit/调度停止 gate，执行**先撤销后续派发权→请求停止→核对真实在途**。该过程有原事件与稳定恢复身份，不属于 preview。
- 收敛后用同一个已保存 Decision 重读 current state，重新 preview；不消耗新的模型回复，不改原 raw/request。
- 若原 request 绑定的 plan/requirements/scope 已真正变更，依 H1 原规则 `REQUEST_BINDING_STALE`；不得自行一般化 rebase。仅 Attempt 结束、不改变被绑语义时，可继续原决定。
- 停止/核对有界；无法收敛保留阻塞记录。不能把 `19/13` 变成 PASS 的方式设为 `running_work=False`。

### 5.5 `DeltaProblemKind` 完整映射

| DeltaProblemKind | 原公开拒绝码 |
|---|---|
| NOT_REDUCIBLE | STRUCTURE_INVALID |
| PORT_UNBINDABLE | DATA_UNBOUND |
| PRECONDITION_FALSE | METHOD_INAPPLICABLE |
| PRECONDITION_UNKNOWN | EVIDENCE_REQUIRED |
| PRECONDITION_CONFLICT | EVIDENCE_CONFLICT |
| PRECONDITION_WITNESS_STALE | REQUEST_BINDING_STALE |
| ROOT_COVERAGE_GAP | COVERAGE_GAP |
| SIZE_BOUND | PLANNING_BOUND_REACHED |
| REFINEMENT_CYCLE | REFINEMENT_CYCLE |
| PROJECTION_DEFECT | 按下一张 typed 表，不按 detail 文本猜 |
| NOT_CHECKED | SOURCE_UNAVAILABLE → INTERNAL_CONTRACT_ERROR；不是有效计划 |

`PROJECTION_DEFECT` 读取对应 `ProjectionProblem.kind`：

| ProblemKind | 结果 |
|---|---|
| CYCLE | ORDER_CYCLE |
| UNBOUND_PORT / SINGLE_PORT_OVERBOUND / SET_PORT_UNORDERED | DATA_UNBOUND |
| NO_GATING_CHILDREN / ORPHAN_OBLIGATION / UNREACHED_REQUIRED_OCCURRENCE / ROOT_COVERAGE_GAP | COVERAGE_GAP |
| DANGLING_ENDPOINT / MISSING_EDGE / DUPLICATE_SLOT / RESOURCE_CONFLICT | STRUCTURE_INVALID |
| BOUND_REACHED | PLANNING_BOUND_REACHED |
| PARTIAL_CHECK | SOURCE_UNAVAILABLE，除非**同份报告**逐项证明该项被后继精确检查替代；H1 默认不豁免 |

新出现未映射枚举 → `INTERNAL_CONTRACT_ERROR/unmapped_plan_problem`，静态测试要求映射键集合覆盖当前实际 enum。参考实现已包含上述映射；不要创建空 `PlanShapeView` 丢掉 refinement_cycle 或非环缺陷。

编译前的 `CompilationRefused` 用它自己的 typed reason 做**显式表**；source-map 记录实际所有 reason，映射到相同语义表。没理由码的异常是内部故障，禁止用字符串模糊匹配归到用户错误。

### 5.6 无计划改变类型

- **WAIT**：校验引用来自冻结 visible_refs，并确认目标确为实际在途/可等待的工作；已经完成则返回既有反馈或即时唤醒，不挂死。持久注册/复用原唤醒关系，登记后再查一遍事件水位，防“检查后、订阅前已完成”丢唤醒；不 busy-loop 再问模型。
- **NO_CHANGE**：只确认本轮不改图；不推导 Mission 已完成，不消费新执行预算，不视为任务一定可进展。
- **DECLARE_BLOCKED**：精确这个 wire 名称；不新增 `DECL_BLOCKED` alias。只报告 blocker，由现有有界 synthesis/stall 路径决定下一步，不能立即当业务 FAIL。

这三个分支生成 `NoPlanMutation`，**不调用**operation mapper、compile、validate_delta、TaskNetwork Commit 或 handoff。它们仍执行 request/visible ref/协议/identity 检查；已知 UNKNOWN 不会因“没检查”变成 resolved。诊断不要求未知账本必须先可读，但也不能触发现实动作。

## 6. 一致快照、提交与恢复：三个 producer 怎样连接

### 6.1 快照读取

所有编排库来源在一条只读 transaction 内取得，然后结束 read transaction；把不可变快照交给纯计算。不要分三次独立读取后假定同一时刻。

WAL 的一致读可能是旧快照，不能把它升级写入而假定没有并发变化。最终另开短写事务，重新取当前值比较 [W1]。跨 execution.db 的实时事实只通过已有持久导入/稳定回执反映，本补遗不宣称跨库原子。

### 6.2 Commit 补充守卫（只对新协议）

新增 `orchestrator/planning_admission_commits.py`，由现有 CommitService 组合：

```text
1. 验证 caller 对 Mission 的读取/提交身份。
2. BEGIN IMMEDIATE / 原 Store.transaction 的等价写语义。
3. 查既有 plan_commit_receipt(command_id)。存在且 intent_hash 一致：返回原结果，不重编/不重派。
4. 校验实际 new protocol / request / decision / canonical hash / enablement。
5. 重新读当前 grant lineage，核对发包绑定、期限、policy。
6. 原 H1 的 plan/requirements/scope freshness + 原 SemanticReadSetChecker。
7. 重读完整 operations/bindings/links/actions 集合，比较 producer digest；新增 action 也必须被发现。
8. 重读 retiring work 的 Attempts、intents、lease 与 generation；不得仍能合法产生新副作用。
9. 证据/能力/预算保持原检查；验证 preview 的 decision/snapshot/delta identity。变化即 stale/deferred，不在事务中调模型重规划。
10. 调用现有 commit_plan_revision 的同连接写逻辑；其检查不跳过。
11. 同事务写 admission check 与 decision 的最终状态/receipt 关联。
12. COMMIT；真正派发走原 outbox。
```

旧 `_read_set` 的 authority 通道仍是审批记录；新 grant 和 action 集合通过**同一 Commit 内的补充 guard**检查，不挪到模型 read_set。不要把所有 visible_refs 当成必需安全依赖，也不要因旧 resolver 不认识 operation/capability 而删安全检查。

### 6.3 Adapter 的 read-set 具体怎么构造

新 H1-H 管线**不调用** `AdapterContext.from_admission_context()` 把全部 visible_refs 无差别转换为 ReadItem。改为 `build_adapter_context(pre_admitted, frozen_request, source_snapshot)`；proposal/command ID 继续来自原 decision/request 的系统幂等身份。

| 被检查的事实 | 应放到哪里 | 来源 |
|---|---|---|
| 目标 Task 合同、选中 Method、被使用 observation/Acceptance/Obligation、requirements | 既有 `SemanticReadSet` 的对应合法通道 | 复用 `SemanticReadSetChecker.read_item()` 的真实 revision/hash；不得重复写算法 |
| 退役 MethodInstance/occurrence/输入绑定 | 原编译输出的 read-set 与 pinned plan/网络身份 | 原 `compile_refinement_bundle` 及网络快照 |
| planning grant | 本补遗补充 guard，不伪装成 approval | `planning_request_authority_bindings` + 当前 grant |
| 实际 operations 集合 | 本补遗补充 guard，不丢掉校验 | 完整 OperationSnapshot digest + source rows |
| 当前能力、预算、物理配额 | 保留既有准入/Commit 检查；必要版本保存在内部 admission check | 实际部署能力与原预算账本 |
| 只供模型了解而没有实际被此决定消费的额外材料 | 不自动成为业务输入；引用披露范围仍检查 | 原 visible_refs manifest |

编译器实际读取的新数据必须被其 read-set 捕获。`reason_refs` 不是完整读集证明；旧 resolver 不支持某个必需通道时，不能过滤掉该通道继续提交，必须通过本节明确的补充 guard 或保持 SOURCE_UNAVAILABLE。

### 6.4 暂缓、拒绝和审计的存储

新增 `planning_admission_checks` 保存 append-only 阶段检查；不改 `planning_decisions` 原 enum/rank 语义：

- Preflight/preview 只保存检查产物，不提前写 `COMMITTED`。
- Runtime deferred：decision 留可继续的非终态，不先 REJECTED 后反向改 ADMITTED。
- 真正旧 request stale/提案非法：写相应终态，下一条请求是新的合法身份。
- 成功 plan Commit 与 decision COMMITTED 在同一个业务事务；读取旧 receipt 后补审计也必须幂等。
- NO_STATE_CHANGE 的记录、必要 waiting 意图、事件回执在一个事务写；不可一半订阅一半记完成。
- SourceUnavailable 不占用模型的格式修复次数。工程修复后可对原 raw 继续处理，必须有新的 check identity，不能改原 raw/hash。

`check_id` 用既有 canonical ID 工具计算 `(decision_id, phase, source_snapshot_hash, decision_hash, check_schema)`。所有时间信息仅用于审计，不能因重启换时间而重新生出一项业务命令。

`SOURCE_UNAVAILABLE` 无完整 snapshot 时，仍可保存**读取尝试身份**，但不能冒充快照证明。`snapshot_hash` 在该 phase 取 canonical `{kind:"source-read-attempt-v1",request_id,decision_id,failed_source,observed_refs_digest}` 的摘要；`detail_json.coverage="INCOMPLETE"` 必填。只有成功取得完整数据的 check 才能写 `coverage="COMPLETE"`；最终 guard 拒绝将前者用于授权或提交。读取失败前可证明的 refs 为空时，摘要表示“无已取得引用”，不是“世界为空”。未解码结果继续用原 UNREADABLE 记录，不强造 canonical_decision_hash。

### 6.5 快照/preview 要不要落库

授权 grant 是新增源记录；request side binding 是不可变来源绑定；operation link 是唯一身份桥；preview 是可重算的派生资料，不是第二份 TaskGraph。

需要审计时把序列化快照和编译结果存 CAS，`planning_admission_checks.detail_json` 记录 refs/hash。**写 CAS 由外层 orchestrator 执行，不在 pure preview 内执行。**重启重算可以，但必须读原 request 和当前权威来源，不依靠恢复模型隐藏思维。

## 7. 数据库迁移与可改文件 allowlist

### 7.1 迁移

附带 `reference/schema.sql` 定义 4 张新增表：

```text
planning_lane_grants
planning_request_authority_bindings
planning_operation_action_links
planning_admission_checks
```

由 `storage/admission_seams_schema.py` 提供 DDL，并注册**本地当前最大迁移号+1**；在对齐报告里固定最终号。不能覆盖 migration 19、NanoJev 已占编号、旧 checksum。若已存在完全等价表，记录等价字段/约束并复用，不能并行建第二份。

生产使用 `foreign_keys=ON`，以实际原表 DDL 验证复合外键和 action_key；附件 DDL 的本地测试只用了明确标识的 parent-schema fixture，不表示实际 SDK 迁移已经通过 [W2]。

只允许新协议写新增表/事件；legacy branch 不填表、不多发事件、不改 Prompt/package/default config canonical bytes。新增授权 API 不改变原 `create/decide` 的 wire。

### 7.2 允许的文件与职责

| 文件（SDK 根下） | 授权修改 |
|---|---|
| `src/agent_orchestrator/planning/decision_admission.py` | 分阶段 Context/结果；移除生产默认放行；错误映射入口 |
| `planning/decision_adapter.py` | 共享转换内核，新增 pre-admitted→preview proposal；保持 decode-only 抛拒 |
| `planning/admission_sources.py`（新） | 一致读装配；不可模型调用/写库 |
| `planning/plan_preview.py`（新） | 纯 compile preview 和完整类型报告映射 |
| `governance/planning_authorization.py`（新） | policy、grant 快照 producer 与纯判断 |
| `api/planning_authorization.py`（新） | 复用固定 Principal/tenant 的显式 issue/renew/revoke 入口 |
| `runtime/planning_operations.py`（新） | 精确 link/账本 mapper + running work reader |
| `storage/admission_seams_schema.py`、`storage/planning_admission_store.py`（新） | 唯一新增表读写模块，复用 Store 同连接 |
| `storage/schema.py` | 只注册下一未使用迁移 |
| `storage/planning_decision_store.py` | 只增加 request side binding 同事务接线/审计关联；不得改历史身份 |
| `orchestrator/planning_admission_commits.py`（新） | issuer、request binder、最终 guard 的唯一业务提交入口 |
| `orchestrator/commit_service.py` | 组合新 mixin/API，不改变 legacy 路径 |
| `orchestrator/action_commits.py` | 新协议显式 origin→link 的同事务写入和 handoff 门 |
| `orchestrator/hierarchical_dispatch.py` | 抽纯计算、调用新管线；不在 preview 取消工作 |
| `orchestrator/event_handler.py` | 仅 H1-H 路由和既有收敛事件接线，不塞新算法 |
| `planning/htn/compiler.py` | 只有在提取纯内核所必需时改参数/复用函数；计算语义不改，旧测试必须不变 |
| `tests/orchestrator/full_target/h1h_admission/`（新/等价） | 本补遗 focused/integration/fault/变异测试 |

表中除第一行外 SDK 文件前缀均为 `src/agent_orchestrator/`。公开 `contracts/planning_decisions.py` **不在变更 allowlist**；内部新类型放本模块。若实际已发布依赖旧 `AdmissionContext`，必须保留 wrapper 并做反向类型测试，不能让调用方绕过第二阶段。

**Host allowlist**：V1.4 目录中新建本补遗、source-map、任务/核验书更新、审计报告；运行代码只允许 source-map 已明确的“构造 MissionControlV1/Principal 的现有认证装配函数”新增规划 grant API 调用。没有核实到具体路径前不能把 `backend/**` 全目录授权修改。**不碰**PR-7/NanoJev、candidate artifact、vendor pin、版本发布、其他架构文档。

不必升级 Prompt：模型 wire 未变；Prompt/codec 新字段、Stage 扩容另开裁定。H1-H 完成不自动发布、不自动 cherry-pick。

## 8. focused 与集成验收（共 36 组；具体断言不可省）

所有数据通过真实 Store/Commit 创建；仅替代模型与外部测试目标，不 mock 掉生产 producer、compiler、receipt guard。已有等价用例可映射复用，必须指到实际 test nodeid。

### A：Authorization（8 组）

| ID | 操作 | 必须断言 |
|---|---|---|
| A01 | 已认证 caller issue；request 同事务绑定；REFINE | grant/issuer/request 三条 identity 对齐；一次正式 plan commit |
| A02 | 缺 grant 或 producer 不可读 | 前者 AUTHORIZATION_REQUIRED，后者 SOURCE_UNAVAILABLE；均无 plan/write/handoff |
| A03 | 跨 tenant、跨 Mission、wrong grantee/scope | 拒绝；不泄漏他人 grant 细节 |
| A04 | 发包后 revoke/renew | 旧回复 stale；不自动绑定到新 grant |
| A05 | preview 后、Commit 前 TTL 到期 | 事务内拒绝；无新 revision/outbox |
| A06 | required_approvals=0、Method gate=True 但无 grant | 不能放行；有合法 grant 但外部 action 尚未审批时可以规划、不能发送 |
| A07 | 相同签发 command 重送/改输入 | 同输入回原 receipt 不延长 TTL；不同输入冲突 |
| A08 | 已 Commit 后 grant 撤回，再送同 Decision | 有当前读权限时回原 receipt；不新增计划、不新派发；其他人不可读取 |

### O：Operation（10 组）

| ID | 操作 | 必须断言 |
|---|---|---|
| O01 | 全量读三个集合为空；另测读取报错 | 前者有 COMPLETE_EMPTY digest；后者不能变空 |
| O02 | 相同 target 的两个动作/Task、不同 hash/occurrence | 不猜 join；alias 不能覆盖原 link |
| O03 | 退役方法仍有 UNKNOWN action | 即使不在 active plan，也被 mapper 发现并阻断 |
| O04 | 错 action_key/version/params_hash/tenant，悬空 link | SOURCE_UNAVAILABLE；不查 latest 补齐 |
| O05 | HANDED_OFF，lease 未过/已过 | 都不能因 lease 自动判 NOT_APPLIED |
| O06 | UNKNOWN + 仅 reconcile 字符串/空查询；测试 executor 请求 rehandoff | mapper 仍阻断且零 connector 调用；即使旧 executor 提出重发，新协议 begin_handoff 也拒绝不完整证明 |
| O07 | 匹配成功 receipt / 错成功 receipt；完整负证明 / 无迟到排除 | 分别 APPLIED / SOURCE_UNAVAILABLE / NOT_APPLIED / UNRESOLVED |
| O08 | preview 读完后新增 action 或开始新的 handoff | Commit 前集合复核抓到变化，不只查旧 unresolved 集 |
| O09 | 新模式 propose_action 与 link 写到一半抛异常；同次重送 | 两者原子 rollback；重送恰好一份身份；handoff 不得看见无 link action |
| O10 | Task cancelled 但 call 在途；活的外来 lease | RuntimeWorkSnapshot 仍需收敛；不释放/伪造完成，不抢 lease |

### P：Preview（10 组）

| ID | 操作 | 必须断言 |
|---|---|---|
| P01 | 合法 REFINE 从真实 package 到 preview 到 Commit | delta/compiled/Commit 身份一致；不是预览 A 提交 B |
| P02 | candidate ORDER cycle、refinement cycle | 分别正确 code；既有 plan、账本、文件不变 |
| P03 | DATA 无端口、多绑、覆盖缺失、资源冲突 | typed 映射，不统一归 `cycle` |
| P04 | 缺 registry/evidence/validator、PARTIAL_CHECK | 拒绝不完整检查；不填空 report |
| P05 | 编译预览重复两次相同冻结输入 | 候选 hash 相同；Store.total_changes=0、文件 hash 不变、tool/provider calls=0 |
| P06 | 修复候选结构非法但兄弟在跑 | 不取消任何原工作；真正合法后才允许进入收敛流程 |
| P07 | 合法替换需收敛，暂停/重启后完成 | 原 decision 不变；DEFERRED 可恢复；没有第二次 LLM 调用/重复 plan commit |
| P08 | WAIT/NO_CHANGE/DECLARE_BLOCKED（逐项参数化） | shape/operation producer spy 被调用即失败；无 PlanRevision；合法报告仍能落库 |
| P09 | WAIT 登记前目标已完成、订阅竞争 | 立即可唤醒/反馈，不永远等待，不产生忙循环 |
| P10 | 新增 DeltaProblemKind/未知 ProjectionProblemKind | 显式未映射失败；枚举覆盖断言红，不静默放行 |

### I：总线/恢复/回归（8 组）

| ID | 操作 | 必须断言 |
|---|---|---|
| I01 | 09-19 decode-only 两类输入 | 先保留 raw/严格解码，再 NOT_ENABLED；authorization/preview/Commit 均无副作用 |
| I02 | 同 raw/ordinal 重放与同 ordinal 不同 raw | 原身份幂等/冲突；无重复新事件和计划 |
| I03 | grant/request side binding 事务中真实子进程强退 | 都未写或都已写；恢复不补签新权 |
| I04 | preview 已记录、未 Commit 强退 | 原 raw 保留；重建当前来源再检查；不直接用旧 grant 放行 |
| I05 | Commit 完成回执返回前强退 | 恢复命中原 receipt；新 revision/outbox 数量不变 |
| I06 | authorize 或 action row 在两个并发提交间改变 | 至少一个因真实冲突/stale 拒绝；不得两份互相冲突的计划都放行 |
| I07 | 关闭新协议、旧 Mission 冷恢复 | 原 Prompt/package/event/canonical bytes、旧 golden、预算口径不变 |
| I08 | NanoJev shadow 有结果/无结果各运行同一 H1 fixture | H1 三 producer 必须真实运行；Shadow 不能替代校验或改变 Commit 权限 |

这些是 **36 组 SDK 验收规格，不是本轮通过记录**。另需把当前 `19 passed / 13 failed` 的全部 nodeid 逐项列出为 `原缺失→哪项新生产链→具体断言`，不能只比较总数。

### 8.1 12 项必须杀死的变异

1. 无 grant 默认允许；2. 只比 principal 不比 tenant/scope；3. 不复查 Commit 时 grant；4. 操作读失败改空；5. 按 target 自动 join；6. 只扫活跃方法；7. `CONFIRMED_NOT_STARTED` 字符串即放行；8. 漏 action 集合新增；9. 丢 refinement_cycle；10. NOT_CHECKED 改 PASS；11. preview 用原 `apply_planner_reply` 产生写入；12. 非改图决定偷偷进入 plan Commit。

每项必须以相关行为断言失败杀死，不把 import error、缺环境或 timeout 自称 KILLED。

## 9. 实施与核验顺序

1. **签入本补遗/source-map**，保存工作树现场。记录 current migration/协议 enablement，不触碰并行 PR。
2. **先红测试**：复现上述缺来源、空默认、错 join、预览循环；保留原 13 个失败证据。
3. **A producer**：先 issue/renew/revoke+request binding+current snapshot；真实 source test 通过。不要只写 dataclass。
4. **O mapper**：纯状态映射→真实 Store 完整读→原 propose_action 同事务 link→handoff guard；无明确历史关联的 case 保留 blocked。
5. **P preview**：分 admission 阶段→抽纯内核→typed 完整映射→拒绝无检查→接 deferred 收敛。
6. **最终事务 guard**：同库 re-read，连接原 Compile/Commit/decision receipt。状态/费用以现有账本为准。
7. **独立会话核验**：36 组、12 变异、真实进程故障；审阅不能由实施会话自签。
8. **H1-H 通过后执行 H1-I**：冻结候选、使用既有获准 GPT-5.6 配置与测试集/预算；前四类 REFINE、REPAIR、BLOCKED→synthesis、WAIT 与完整 H1 门禁均保留。旧文件写 Claude/Grok 的历史案例可以作固定数据，不作为本轮模型调用。

### 命令与结果口径

原脚本依赖以现有锁文件为准，不安装一组最新依赖替换环境。在 SDK 候选 worktree 执行：

```bash
# 不执行 reset/clean/cherry-pick，也不改主 worktree。
git rev-parse HEAD
git status --short
uv run --frozen --group dev pytest -q tests/orchestrator/full_target/h1h_admission --junitxml=h1h-admission.junit.xml
uv run --frozen --group dev pytest -q tests/orchestrator/full_target
uv run --frozen --group dev mypy
```

上面的 focused 路径是本补遗新增目录，Agent 必须创建/映射到实际 nodeids 后运行。旧模式回归、mutations、H1-I runner 使用现有 taskbook 的精确命令，写入 source-map 后执行；本轮未取得本地脚本时不编一个假命令。既有 mypy/全仓已知红项独立列出，新改动模块零新增错误；**focused 不得 skip 必需 producer case**。

H1-I 首次通过才可标 `H1_COMPLETE`；只过本补遗可标：

```text
H1H_ADMISSION_SEAMS = PASS
H1_INDEPENDENT_REVIEW = PENDING / PASS
H1_I_REAL_MODEL = PENDING / PASS
H1_FULL_GATE = PENDING / PASS
```

不能用 14 局小样本声称普遍成功率或行业认证；本次目的为指定候选的可靠接线与回归。

## 10. 审计包、完成标准与不做事项

建议 Host V1.4 目录下 `H1-H-admission-amendment/`：本补遗、source-map、schema-change.md、prior-failures.csv、focused/mutation/recovery 报告、review/disposition、H1-I manifest。只存脱敏摘要、hash 与指向真实受控回执的引用，不能复制 secrets。

完成三条主链必须分别证明：

```text
授权：真实 caller → issue receipt → grant → request side binding → 当前读取 → Commit 再核对
操作：原 envelope → 写时精确 action_key link → 原回执/状态 → 全集合读取 → Commit 再核对
形状：原 Decision → typed Proposal → 纯 compile → 实际 validate_delta → 同 delta 提交
```

本次不做：新 Decision、TaskGraph 新算法、全局 RBAC、重定义业务操作幂等、自动回填旧授权、重新发布 SDK、NanoJev 执行接管。local producer 尚未真正接通时维持 fail-closed，不把工作转移给模型猜测。

## 11. 可直接给执行 Agent 的指令

> 先应用 H1H-ADM-1.0 的规格修订，再继续 H1-H。保留现有脏 worktree 与并行 PR。以本地 H1-H 候选及已批准 09-19 裁定为实施基线；远端参考不是升级指令。先读 source-map 所列真实来源，建立 grant 签发/request 绑定、精确 Operation→action_key 映射以及纯编译 preview。不能把 action approval 数量当规划授权，不能将未读到的 operation 当空集合，不能用空 PlanShapeView 过关。非改图决定明确不编译；改图决定最终由原 Commit 在同事务重查授权、完整 action 集合、在途工作和原 read-set。BIND_EXISTING_GOAL/PROPOSE_SUCCESSOR 继续只解码。NanoJev/Shadow 不代替任何 producer。36 组 focused 及独立核验通过后继续 H1-I 和完整 H1 门禁；只用已批准 GPT-5.6 配置。

## 12. 来源与验证边界

本文件中的类型/表/API/阶段拆分是 **本补遗的新裁定**，不是声称原计划已规定。

- [S1] 已上传 `simpleharness-llm-native-htn-execution-plan-v2.zh-CN.md`，§24、§32、§34、§43–45、§59–61。
- [H1] 用户 2026-09-20 handoff 的全部禁止事项与本地状态；未核实的本地文件不引用为已读源码。
- [S2] 固定远端参考 `planning/decision_admission.py`：三个 View、AdmissionContext、stage 顺序。
- [S3] `governance/permissions.py`：Principal、action binding、required_approvals 份数。
- [S4] `planning/htn/applicability.py`：authorization_gate 的条件/证据意义。
- [S5] `orchestrator/_read_set.py`：authority_state 读取 get_approval，统一 semantic read-set。
- [S6] `api/facade.py`：构造时 Principal/tenant、Mission 归属、内部调用边界。
- [S7] `storage/htn_schema.py`：operation identities/bindings 的真实键。
- [S8] `orchestrator/action_commits.py`：propose_action、action_key/version、审批与 receipt_mismatch。
- [S9] `runtime/actions.py`：持久化先于执行；lookup/reconcile 可能自动 rehandoff。
- [S10] `planning/htn/validation.py` 与 `graph/projection_validation.py`：实际枚举、NOT_CHECKED、projection报告。
- [S11] `planning/decision_adapter.py`、`storage/planning_decision_store.py`、`orchestrator/hierarchical_dispatch.py`：值转换、decode-only拒绝、不可逆决策状态、已有真实执行链。

以上 [S2–S11] 均以 `5ac3f05890a4c5160e2f103a01f863753ea5d498` 为参考，完整固定 URL 见 `sources.json`。

- [W1] SQLite Isolation：`https://www.sqlite.org/isolation.html`。
- [W2] SQLite foreign keys：`https://www.sqlite.org/foreignkeys.html`。
- [W3] Python sqlite3：`https://docs.python.org/3.11/library/sqlite3.html`；可用于只读连接与 fault-test instrumentation，但 `mode=ro` 不替代授权判断。

**附带 reference 只实现局部纯规则及 DDL 契约检查，不能替代真实 SDK producer/Commit/并发测试。实际已跑结果单列 `VALIDATION.md`，其余均标待执行。**
