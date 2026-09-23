# SimpleHarness LLM-Native HTN Core 代码级执行计划 V2

**版本：HTN-LLM-NATIVE-2.0**  
**日期：2026-09-18**  
**执行对象：SimpleHarness SDK / Host**  
**当前仓库基线：`DennyWanye/simple-harness-sdk@7f839f0e3d83aa17a0d0e2e54157ca9b1c9465a5`**  
**运行代码基线：`c0e13a4927717abfa5281e78d6fdca1ee32d732c`（0.12.2 candidate sixth cut + release-doc commit）**  
**替代文档：`simpleharness-llm-native-htn-execution-plan.zh-CN.md`**  
**定位：直接交给编码 Agent / 独立核验 Agent 执行；不是愿景文档。**

---

# 0. 本版为什么重写

上一版方向正确，但作为编码规格粒度不足。本版对以下问题作出不可再自行猜测的裁决：

1. `PlanningDecisionEnvelopeV1` 精确线上 JSON Schema；
2. 一条模型回复允许几个决定；
3. `reason_refs / visible_refs` 的完整引用词表；
4. 完整拒绝码；
5. Planner 请求与 `plan_revision` 的绑定方式；
6. `decision_id` 的生成、幂等和重复提交规则；
7. H1 是仅类型实验还是实际线上接线；
8. 开关位置、默认值、迁移和转默认条件；
9. 阶段 × DecisionType 的启用矩阵；
10. 第一版提示词与 package 版本配对；
11. `PlanningFeedbackV1` 如何返回模型及预算字段来源；
12. PlanningDecision 是否持久化、表和事件；
13. H1 所需全部类型；
14. 基线升级到 0.12.2；
15. 与 P2.3s–v 及现有确定性修复机制的关系；
16. 各阶段量化验收门；
17. `contracts/` 改动许可；
18. 版本和发布节奏；
19. 审计包；
20–26. H2–H8 全部补到代码级实施粒度。

**编码 Agent 不得以“实现时自行决定”为由改变本文的 wire format、状态语义或兼容策略。**  
若真实 checkout 与本文基线存在冲突，先写 `BLOCKER-<date>.md`，列出路径、HEAD、冲突字段和建议；不得静默改协议。

---

# 1. 总架构裁决

SimpleHarness 的 HTN 采用 **LLM-native** 模式：

```text
LLM / Planner
负责：
理解目标
比较 Method
未来推演
搜索
诊断
提出 Repair
提出新 Method

        │
        ▼

PlanningDecision Protocol
只允许结构化 Proposal

        │
        ▼

SimpleHarness
负责：
Strict Codec
Reference Scope
Evidence
Authority
Method Applicability
Grounding
Compiler
TaskNetwork
ORDER / DATA
Obligation
Budget
PlanRevision
Commit
Acceptance
GoalResolution
Operation Recovery
```

**不把以下内容建设成 HTN Core 的必需能力：**

- 通用 A*；
- 通用 MCTS；
- 自研 CP-SAT；
- 自研完整 PDDL/HDDL solver；
- 全局 World Model；
- learned value function；
- 在线 RL planner；
- 穷举所有未来状态。

这些能力以后属于：

```text
LLM 内部能力
或
PlanningBackendPort
```

---

# 2. 当前 0.12.2 能力：哪些必须保留

当前代码已经有，**不得在本计划中重写成第二套**：

```text
contracts/htn.py
contracts/evidence_state.py
contracts/obligations.py
contracts/resolution.py
contracts/semantic_base.py

planning/htn/registry.py
planning/htn/applicability.py
planning/htn/grounding.py
planning/htn/refinement.py
planning/htn/compiler.py
planning/htn/validation.py
planning/htn/world.py
planning/htn/planner_package.py
planning/htn/evidence_round.py
planning/htn/observation_pipeline.py
planning/htn/backends/panda.py

graph/task_network.py
graph/projection_validation.py
graph/eligibility.py

orchestrator/plan_commits.py
orchestrator/hierarchical_dispatch.py
orchestrator/resolution_commits.py
orchestrator/leaf_acceptance.py
orchestrator/root_review.py

knowledge/predicates.py
knowledge/validity.py
knowledge/justifications.py

artifacts/input_bindings.py
```

保留语义：

- `TaskForm.COMPOUND / PRIMITIVE`
- `ObligationId`
- `MethodContract`
- `MethodInstanceDraft`
- `OccurrenceId`
- `PlanRevision`
- `DispatchGeneration`
- `ValidityRevision`
- `ORDER / DATA`
- `ReusePolicy`
- `RunningWorkPolicy`
- `TRUE / FALSE / UNKNOWN / CONFLICT`
- recursion fuel
- repeated expansion / no-state-change
- `InputManifest`
- `Acceptance / GoalResolution`
- Proposal → Ground → Compile → Commit
- PANDA/HDDL adapter

---

# 3. 与 0.12.2 P2.3s–v 的关系：不得打架

以下确定性机制继续保留；PlanningDecision **不能替代其安全职责**。

| 当前机制 | V2 决策 | 原因 |
|---|---|---|
| P2.3s 修复前 reconcile 被退役方法下仍运行的兄弟 Attempt | **永久保留为确定性 pre-commit gate** | 模型不能判断真实在途执行是否已停止 |
| P2.3t 准则驱动写型 `tests` 端口 | **永久保留** | 属于 Method/Composition 合同，不是搜索策略 |
| P2.3u 只读叶写守卫 | **永久保留** | 权限/副作用边界 |
| P2.3v 相同验证失败 N=3 早停 | **保留检测与硬上限；H4 后触发 REPAIR 请求** | 模型可决定修法，但不能取消上限 |
| 证据饱和后跳过无价值 Planner 直接合成 | **H1–H3 保留；H4 后仍可作为 deterministic fast-path** | 避免让模型回答已经确定“无可用 Method”的问题 |
| MethodSynthesizer 最多重问一次/总问数上限 | **保留硬上限** | 防无限模型循环 |
| `max_root_review_repairs` | **保留硬上限** | Repair Planner 不能扩大 |
| provider UNKNOWN 有界重交接/停机 | **永久保留** | 现实副作用/费用语义 |
| 只读叶拒绝有界升级 | **保留；H4 后升级点生成 RepairRequest** | 模型决定修法，程序决定什么时候必须升级 |
| 根评审 REJECT 后 retire+refine | **H1 新协议覆盖其 wire 表达；安全检查保持原实现** | 不重写工作收敛/预算/Commit |

**已知遗留：**重复失败早停之后如果所属方法实例仍需退役，H4 的 `REPLACE_METHOD` 编译必须显式覆盖这一情况，并加入端到端回归。

---

# 4. 版本策略与发布节奏

## 4.1 0.12.2

现有稳定基线，只接受必要 bugfix；不回灌新 Protocol。

## 4.2 0.13.0

承载：

```text
H1 PlanningDecision Protocol
H2 PlannerPackage V1
H3 LLM-native Method Selection
H4 RepairDecision
```

H1–H4 均可逐片合并到 `main`，但默认开关关闭。

**不要求每个 H 片单独发布 package。**

候选：

```text
0.13.0-rc1：H1+H2
0.13.0-rc2：H3
0.13.0-rc3：H4 + 真实模型回归
0.13.0：H1–H4 全门禁通过
```

Host 只在对应 RC 的冻结 wheel / SHA 已产生后更新 vendor pin；开发中 commit 不要求 Host 追随。

## 4.3 0.14.0

承载：

```text
H5 DomainPackage
H6 Method Lifecycle
H7 PlanningBackendPort
H8 Cross-domain Acceptance
```

---

# 5. `contracts/` 改动纪律

允许新增：

```text
src/agent_orchestrator/contracts/planning_decisions.py
src/agent_orchestrator/contracts/repair_decisions.py
src/agent_orchestrator/contracts/planning_backends.py
```

**禁止为了本计划修改旧 v1 canonical wire 的字段或默认值。**

尤其：

```text
MethodContract V1
TypedRef
EvidenceRef
Acceptance / GoalResolution
PlanProposal
```

的旧字节、旧 hash 解释保持。

若必须扩展已有对象：

```text
新 side binding
或
新 schema version
```

不得静默改变 v1。

---

# 6. H0：基线冻结

H0 不改代码语义。

必须生成：

```text
plans/llm-native-htn/H0/baseline.md
plans/llm-native-htn/H0/git-status.txt
plans/llm-native-htn/H0/test-results.json
plans/llm-native-htn/H0/prompt-digests.json
plans/llm-native-htn/H0/event-golden-digests.json
```

至少记录：

```bash
git rev-parse HEAD
git status --short
python --version
sqlite3 --version
```

测试：

```bash
pytest -q tests/orchestrator/full_target
pytest -q <旧模式固定回归集合>
ruff check <本计划涉及文件>
```

执行 Agent 提供的当前本机口径：

```text
new mode: 2960 passed / 2 skipped
old mode: 560 passed / 13 skipped / 0 failed
_new_mode sentinel: 19
```

这组数字**必须在 H0 重新实测确认**；若真实 checkout 不一致，以 H0 实测为唯一后续对照，并在 baseline.md 解释差异。

---

# 7. H1 总裁决：是真实线上接线，不是只做类型

H1 完成后：

```text
legacy Mission
→ 继续 <plan_revision_proposal>

显式 planning-decision-v1 Mission
→ 真 Planner dispatch
→ <planning_decision>
→ strict decode
→ admission
→ 适配现有 ground/compile/commit 主链
```

**新协议默认关闭。**

H1 不新增新的规划语义，只把当前已经能做的动作换成统一 wire。

---

# 8. H1 开关

## 8.1 MissionSpec

在：

```text
orchestrator/commit_service.py::MissionSpec
```

增加：

```python
planning_protocol_version: str = "legacy-plan-proposal-v1"
```

常量：

```python
LEGACY_PLANNING_PROTOCOL = "legacy-plan-proposal-v1"
PLANNING_DECISION_V1 = "planning-decision-v1"
```

`MissionSpec.to_json()`：

```python
if self.planning_protocol_version != LEGACY_PLANNING_PROTOCOL:
    data["planning_protocol_version"] = self.planning_protocol_version
```

**因此默认 Mission 的 spec bytes/hash 不变。**

## 8.2 持久化

新增 schema migration 19：

```sql
CREATE TABLE mission_planning_protocols (
    mission_id TEXT PRIMARY KEY NOT NULL,
    protocol_version TEXT NOT NULL,
    package_version INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    created_at REAL NOT NULL
) STRICT;
```

规则：

- legacy Mission 不必写行；读不到行 = legacy；
- 新协议 Mission 在创建 Mission 的同一业务事务写绑定；
- Mission 创建后不可切协议；
- clone/new cycle 必须显式重新选择；
- recovery 只读该持久绑定，不读环境变量猜模式。

**不把此开关放进 `policy_snapshot()`，避免无关 Mission 的策略 digest 漂移。**

---

# 9. H1 Prompt / Package 版本

当前 hierarchical Planner：

```text
package version 3
prompt planner-hierarchical-v5/v6/v7
```

H1 新增：

```text
HIERARCHICAL_PLANNER_PACKAGE_VERSION = 4
prompt = planner-hierarchical-v8
wire = planning-decision-v1
block = <planning_decision>
```

版本配对：

```python
HIERARCHICAL_PLANNER_VERSIONS_BY_PACKAGE[4] = frozenset({
    "planner-hierarchical-v8",
})
```

旧 1–3 映射逐字节不动。

新提示词 digest 加到现有冻结摘要表。

**v8 只能配 package 4。package 4 不允许 pin 回 v7。**

---

# 10. H1 一条回复只能有一个决定

硬规则：

```text
一个 Planner AgentTurn
→ 恰好 0 或 1 个 <planning_decision> block
```

合法：

```text
1 block
```

拒绝：

```text
2+ blocks → MULTIPLE_DECISIONS
0 block → DECISION_BLOCK_MISSING
同时出现旧 <plan_revision_proposal> → MIXED_PROTOCOL_BLOCKS
同时出现 <method_proposal> → MIXED_PROTOCOL_BLOCKS
```

**禁止数组形式的多个顶层决定。**

需要“退旧方法 + 采用新方法”的原子修复时：

```text
一个 REPAIR decision
payload.repair_kind = REPLACE_METHOD
```

不是两个顶层 decision。

---

# 11. H1 DecisionType 最终枚举

```python
class PlanningDecisionType(StrEnum):
    REFINE = "REFINE"
    PROPOSE_METHOD = "PROPOSE_METHOD"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    REPAIR = "REPAIR"
    BIND_EXISTING_GOAL = "BIND_EXISTING_GOAL"
    DECLARE_BLOCKED = "DECLARE_BLOCKED"
    REQUEST_HUMAN = "REQUEST_HUMAN"
    WAIT = "WAIT"
    NO_CHANGE = "NO_CHANGE"
```

**大小写严格。**

`SELECT_METHOD` 删除：选择 Method + 参数就是 `REFINE`。

`RETIRE_METHOD_USE` 删除：属于 `REPAIR`。

`REUSE_RESULT` 改名 `BIND_EXISTING_GOAL`，因为旧 `bind_shared_goal` 同时支持：

```text
REUSE_ACCEPTED
SHARE_ACTIVE
```

不能错误地把 active sharing 叫作 result reuse。

---

# 12. 阶段 × DecisionType 启用矩阵

含义：

- D = 可解码/保存；
- A = 可做 semantic admission；
- X = 可执行映射到正式行为；
- R = 返回 `DECISION_NOT_ENABLED_IN_PHASE`。

| DecisionType | H1 | H2 | H3 | H4 | H5 | H6 | H7/H8 |
|---|---|---|---|---|---|---|---|
| REFINE | D/A/X | X | X | X | X | X | X |
| REPAIR / REPLACE_METHOD | D/A/X | X | X | X | X | X | X |
| REPAIR / PROPOSE_SUCCESSOR | D/A/X | X | X | X | X | X | X |
| BIND_EXISTING_GOAL | D/A/X | X | X | X | X | X | X |
| DECLARE_BLOCKED | D/A/X | X | X | X | X | X | X |
| WAIT | D/A/X | X | X | X | X | X | X |
| NO_CHANGE | D/A/X | X | X | X | X | X | X |
| REQUEST_EVIDENCE | D/R | D/R | D/A/X | X | X | X | X |
| REQUEST_HUMAN | D/R | D/R | D/R | D/A/X | X | X | X |
| PROPOSE_METHOD | D/R | D/R | D/R | D/R | D/R | D/A/X | X |

H1 prompt v8 **只告诉模型 H1 可执行类型**，不会诱导模型输出尚未启用类型。

---

# 13. H1 模型线上 JSON：PlanningDecisionEnvelopeV1

模型只输出 Core Fields。

**模型不输出：**

```text
decision_id
request_id
mission_id
plan_revision
tenant
principal
budget account
authority
approval
dispatch generation
registry status
operation id
acceptance id
timestamp
model/prompt identity
```

线上对象：

```json
{
  "schema_version": 1,
  "decision_type": "REFINE",
  "subject_key": "subject-root",
  "rationale": "选择已注册且当前可适用的方法。",
  "reason_refs": [],
  "assumptions": [],
  "payload": {},
  "uncertainties": [],
  "alternatives": [],
  "replan_triggers": []
}
```

---

# 14. JSON Schema 文件

新增：

```text
src/agent_orchestrator/contracts/schemas/planning-decision-v1.schema.json
```

SDK 运行时**不新增 jsonschema 依赖**；Python strict codec 是运行时权威。JSON Schema 用于：

- 文档；
- fixtures；
- 其他语言 Host；
- 静态 schema conformance 测试。

Draft：

```text
https://json-schema.org/draft/2020-12/schema
```

顶层：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:planning-decision:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "decision_type",
    "subject_key",
    "rationale",
    "reason_refs",
    "assumptions",
    "payload",
    "uncertainties",
    "alternatives",
    "replan_triggers"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "decision_type": {
      "enum": [
        "REFINE",
        "PROPOSE_METHOD",
        "REQUEST_EVIDENCE",
        "REPAIR",
        "BIND_EXISTING_GOAL",
        "DECLARE_BLOCKED",
        "REQUEST_HUMAN",
        "WAIT",
        "NO_CHANGE"
      ]
    },
    "subject_key": {"type":"string","minLength":1,"maxLength":256},
    "rationale": {"type":"string","minLength":1,"maxLength":4000},
    "reason_refs": {"type":"array","maxItems":32,"uniqueItems":true,"items":{"$ref":"#/$defs/planningRef"}},
    "assumptions": {"type":"array","maxItems":16,"items":{"$ref":"#/$defs/assumption"}},
    "payload": {"type":"object"},
    "uncertainties": {"type":"array","maxItems":16,"items":{"$ref":"#/$defs/uncertainty"}},
    "alternatives": {"type":"array","maxItems":8,"items":{"$ref":"#/$defs/alternative"}},
    "replan_triggers": {"type":"array","maxItems":16,"items":{"$ref":"#/$defs/replanTrigger"}}
  }
}
```

Codec additionally applies `decision_type → exact payload schema`。

---

# 15. Canonical bytes / hash

模型原始响应：

```text
raw_output_artifact
raw_output_hash = sha256(raw bytes)
```

严格 parse 后：

```text
canonical_decision_json = canonical_json(decision.to_json())
decision_payload_hash = sha256(canonical_decision_json)
```

**模型不提交 hash。**

Unknown field：拒绝。对象 key 顺序不参与 canonical hash；array order 参与。

---

# 16. 限额

```python
MAX_PD_RATIONALE_CHARS = 4_000
MAX_PD_REASON_REFS = 32
MAX_PD_ASSUMPTIONS = 16
MAX_PD_ALTERNATIVES = 8
MAX_PD_UNCERTAINTIES = 16
MAX_PD_REPLAN_TRIGGERS = 16
MAX_PD_BINDINGS = 64
MAX_PD_WAIT_REFS = 32
MAX_PD_BLOCKERS = 16
MAX_PD_HUMAN_OPTIONS = 12
MAX_PD_ARGUMENTS = 32
```

JSON value nesting沿用现有 `json_value()` 深度上限。

---

# 17. 新 PlanningRefV1：不要修改现有 TypedRefKind

现有 `TypedRefKind` 没有 `fact`、`obligation`、`authority`、`capability`。H1 不修改它。

```python
class PlanningRefKind(StrEnum):
    TASK = "task"
    OBLIGATION = "obligation"
    METHOD = "method"
    REQUIREMENTS = "requirements"
    ARTIFACT = "artifact"
    SOURCE = "source"
    OBSERVATION = "observation"
    REVIEW = "review"
    ACCEPTANCE = "acceptance"
    RESOLUTION = "resolution"
    OPERATION = "operation"
    TOOL_RECEIPT = "tool_receipt"
    KNOWLEDGE = "knowledge"
    AUTHORITY = "authority"
    CAPABILITY = "capability"
```

**没有 `fact`。** 规划意义上的“事实”引用统一为 `kind=observation`。

```python
@dataclass(frozen=True, slots=True)
class PlanningRefV1:
    kind: PlanningRefKind
    id: str
    semantic_revision: int
    content_hash: str
```

全部必填。

---

# 18. visible_refs

PlannerPackage V4 增 `visible_refs`。模型输出中的正式引用必须逐字节匹配：

```text
(kind, id, semantic_revision, content_hash)
```

不允许数据库里存在但本请求未暴露的 ref，也不允许只比 id。

---

# 19. subject_key

PlannerPackage 给：

```json
"planning_subjects": [
  {
    "subject_key": "subject-root",
    "occurrence_id": "...",
    "task_id": "...",
    "obligation_id": "...",
    "contract_revision": 4
  }
]
```

模型只回 `subject_key`。subject_key 只在本 PlannerRequest 内有效。

---

# 20. AssumptionV1

```python
@dataclass(frozen=True, slots=True)
class AssumptionV1:
    key: str
    statement: str
    required_for: tuple[str, ...]
    risk: AssumptionRisk
    suggested_predicate_key: str | None
```

risk：`LOW / MEDIUM / HIGH`。Assumption 永不形成 TRUE。

---

# 21. UncertaintyV1

```python
@dataclass(frozen=True, slots=True)
class PlanningUncertaintyV1:
    statement: str
    severity: str
    affects: tuple[str, ...]
```

只用于 trace/UI/evaluation/review context。

---

# 22. AlternativeSummaryV1

```python
@dataclass(frozen=True, slots=True)
class AlternativeSummaryV1:
    method_ref: PlanningRefV1 | None
    label: str
    disposition: str
    reason: str
```

`alternatives` 不参与安全准入。

---

# 23. ReplanTriggerHintV1

H1 只保存为 hint，不安装 callback：

```python
@dataclass(frozen=True, slots=True)
class ReplanTriggerHintV1:
    description: str
    referenced_predicates: tuple[str, ...]
    suggested_decision: str
```

H4 后可映射到已注册 Condition AST。

---

# 24. H1 payload：REFINE

```json
{
  "method_ref": {
    "kind": "method",
    "id": "code.fix-by-patch",
    "semantic_revision": 2,
    "content_hash": "..."
  },
  "bindings": {"target":"..."}
}
```

准入顺序：visible ref → method identity → subject open → parameter type → applicability → capability/authority → unresolved operation gate → existing grounding/compiler/Commit。

---

# 25. H1 payload：REPAIR / REPLACE_METHOD

```json
{
  "repair_kind": "REPLACE_METHOD",
  "rejected_method_instance": {
    "id": "mi-...",
    "semantic_revision": 1,
    "content_hash": "..."
  },
  "replacement_method_ref": {
    "kind": "method",
    "id": "code.alt-fix",
    "semantic_revision": 1,
    "content_hash": "..."
  },
  "bindings": {}
}
```

映射成现有 `retire_method + refine`，并由系统设置 `REQUEST_STOP_THEN_RECONCILE`。P2.3s 继续处理在跑兄弟工作。

---

# 26. H1 payload：REPAIR / PROPOSE_SUCCESSOR

保留旧 `propose_successor` 能力：

```json
{
  "repair_kind": "PROPOSE_SUCCESSOR",
  "old_task_ref": {...},
  "obligation_ref": {...},
  "goal_type_ref": {"id":"...","version":1,"content_hash":"..."},
  "bindings": {}
}
```

H4 并入完整 Repair AST。

---

# 27. H1 payload：BIND_EXISTING_GOAL

```json
{
  "mode": "REUSE_ACCEPTED",
  "consumer_method_instance_ref": {...},
  "step": "inspect",
  "goal_ref": {...},
  "resolution_ref": {...}
}
```

或 `mode=SHARE_ACTIVE` 且 `resolution_ref=null`。

规则：REUSE_ACCEPTED 要 CURRENT resolution/acceptance；SHARE_ACTIVE 要仍被 demand 的 active goal；写型现实动作不得自动 share。

---

# 28. H1 payload：DECLARE_BLOCKED

```json
{
  "blockers": [{"code":"NO_USABLE_METHOD","detail":"..."}],
  "resumable_if": ["new_method_admitted"]
}
```

只记录 decision 并交现有 synthesis/stall logic；不直接 Mission FAIL。

---

# 29. H1 payload：WAIT

```json
{
  "wait_for": [{"kind":"review","id":"...","semantic_revision":1,"content_hash":"..."}],
  "reason": "等待当前已派发工作返回"
}
```

不改 plan，不创建新等待类型。

---

# 30. H1 payload：NO_CHANGE

```json
{"reason":"当前采用方法仍有效，已有工作正在推进。"}
```

不能包含状态修改。

---

# 31. H1 仅解码类型

`REQUEST_EVIDENCE / REQUEST_HUMAN / PROPOSE_METHOD` 严格 parse + persist，然后 `DECISION_NOT_ENABLED_IN_PHASE`，不 silent fallback。

---

# 32. 系统字段禁止规则

结构层禁止模型写：

```text
mission_id tenant_id principal principal_id scope scope_id manager_epoch
budget_account budget_grant_revision registry_status opened_by authorization_ref
grant_ref provenance authored_by dispatch_generation plan_revision expected_plan_revision
operation_id acceptance_id approval_id decision_id request_id
```

**不扫描 arbitrary domain parameter map 的 key 名。**

---

# 33. 完整拒绝码

```python
class PlanningDecisionRejectionCode(StrEnum):
    DECISION_BLOCK_MISSING = "DECISION_BLOCK_MISSING"
    MULTIPLE_DECISIONS = "MULTIPLE_DECISIONS"
    MIXED_PROTOCOL_BLOCKS = "MIXED_PROTOCOL_BLOCKS"
    MALFORMED_DECISION = "MALFORMED_DECISION"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MODEL_SET_SYSTEM_FIELD = "MODEL_SET_SYSTEM_FIELD"
    DECISION_TYPE_UNKNOWN = "DECISION_TYPE_UNKNOWN"
    DECISION_NOT_ENABLED_IN_PHASE = "DECISION_NOT_ENABLED_IN_PHASE"
    SUBJECT_NOT_IN_REQUEST = "SUBJECT_NOT_IN_REQUEST"
    REF_OUTSIDE_CONTEXT = "REF_OUTSIDE_CONTEXT"
    REQUEST_BINDING_STALE = "REQUEST_BINDING_STALE"
    PACKAGE_HASH_MISMATCH = "PACKAGE_HASH_MISMATCH"
    METHOD_NOT_FOUND = "METHOD_NOT_FOUND"
    METHOD_STALE = "METHOD_STALE"
    METHOD_RETIRED = "METHOD_RETIRED"
    METHOD_REJECTED = "METHOD_REJECTED"
    METHOD_INAPPLICABLE = "METHOD_INAPPLICABLE"
    METHOD_NOT_AUTHORIZED = "METHOD_NOT_AUTHORIZED"
    PARAMETER_INVALID = "PARAMETER_INVALID"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    DATA_UNBOUND = "DATA_UNBOUND"
    STRUCTURE_INVALID = "STRUCTURE_INVALID"
    ORDER_CYCLE = "ORDER_CYCLE"
    REFINEMENT_CYCLE = "REFINEMENT_CYCLE"
    COVERAGE_GAP = "COVERAGE_GAP"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    OBLIGATION_NOT_OPEN = "OBLIGATION_NOT_OPEN"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    OPERATION_UNRESOLVED = "OPERATION_UNRESOLVED"
    RUNNING_WORK_NOT_RECONCILED = "RUNNING_WORK_NOT_RECONCILED"
    REPAIR_NOT_ALLOWED = "REPAIR_NOT_ALLOWED"
    REUSE_NOT_ALLOWED = "REUSE_NOT_ALLOWED"
    PLANNING_BOUND_REACHED = "PLANNING_BOUND_REACHED"
    INTERNAL_CONTRACT_ERROR = "INTERNAL_CONTRACT_ERROR"
```

每个新增码必须有 1 条负向 fixture。

---

# 34. plan_revision 归属与晚到决定

模型不再提交 `expected_plan_revision`。

新增持久 `PlanningRequestBinding`：

```python
@dataclass(frozen=True, slots=True)
class PlanningRequestBinding:
    request_id: str
    mission_id: str
    protocol_version: str
    package_version: int
    package_hash: str
    base_plan_revision: int
    requirements_revision: int
    scope_epoch_digest: str
    subject_bindings_hash: str
    visible_refs_digest: str
    prompt_version: str
    prompt_hash: str
    created_at: float
```

H1：`base_plan_revision != current` 直接 `REQUEST_BINDING_STALE`。H2 后再支持 unrelated-change 精确重评。

---

# 35. decision_id

模型不写。

```python
decision_id = "pd-" + sha256(
    request_id + "\x1f" + str(attempt_ordinal) + "\x1f" + raw_output_hash + "\x1f" + "planning-decision-codec-v1"
).hexdigest()[:24]
```

重复同 request+ordinal+raw：返回同 record，不重复 event/compile/commit。相同 request+ordinal 不同 raw：Store identity conflict。

---

# 36. H1 新存储：migration 19

```sql
CREATE TABLE planning_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    mission_id TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    package_version INTEGER NOT NULL,
    package_hash TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL,
    requirements_revision INTEGER NOT NULL,
    scope_epoch_digest TEXT NOT NULL,
    subject_bindings_hash TEXT NOT NULL,
    visible_refs_digest TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    created_at REAL NOT NULL
) STRICT;

CREATE TABLE planning_decisions (
    decision_id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL,
    attempt_ordinal INTEGER NOT NULL,
    raw_output_hash TEXT NOT NULL,
    raw_artifact_ref TEXT,
    canonical_json TEXT,
    canonical_hash TEXT,
    decision_type TEXT,
    status TEXT NOT NULL,
    rejection_codes_json TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(request_id, attempt_ordinal),
    FOREIGN KEY(request_id) REFERENCES planning_requests(request_id)
) STRICT;
```

status：`UNREADABLE / DECODED / REJECTED / ADMITTED / COMPILED / COMMIT_REJECTED / COMMITTED / NO_STATE_CHANGE`。

---

# 37. H1 新事件

仅新协议 Mission：`PlanningDecisionEvaluated`。legacy Mission 不得多任何 `PlanningDecision*` event。

---

# 38. H1 PlannerPackage V4 最小变化

在当前 package 上只加：

```text
planning_protocol
planning_subjects
visible_refs
previous_feedback
decision_limits
```

原 `plan/method_library/applicability/operators/facts/rejected_refinements` 保留。

---

# 39. PlanningFeedbackV1

```python
@dataclass(frozen=True, slots=True)
class PlanningFeedbackV1:
    previous_decision_id: str
    status: str
    rejection_codes: tuple[PlanningDecisionRejectionCode, ...]
    problems: tuple[PlanningProblemDetailV1, ...]
    changed_refs: tuple[PlanningRefV1, ...]
    budgets: PlanningRetryBudgetView
```

```python
@dataclass(frozen=True, slots=True)
class PlanningRetryBudgetView:
    same_request_format_retries_remaining: int
    planning_rounds_remaining: int
    synthesis_asks_remaining: int
    root_review_repairs_remaining: int
    repeated_failure_before_escalation_remaining: int | None
```

来源分别是：新常量 format retry=1、现有 max_planning_attempts、现有 synthesis durable count、max_root_review_repairs、P2.3v streak。

---

# 40. PlanningProblemDetailV1

```python
@dataclass(frozen=True, slots=True)
class PlanningProblemDetailV1:
    code: PlanningDecisionRejectionCode
    subject_ref: PlanningRefV1 | None
    field_path: str | None
    detail: str
    expected: str | None = None
    observed: str | None = None
```

---

# 41. H1 Prompt v8 必含段落

1. hierarchical Planner，只提出 Decision；
2. 只输出一个 `<planning_decision>`；
3. 类型只能从 package enabled_decision_types；
4. subject_key 只照抄；
5. refs 只从 visible_refs 完整照抄；
6. 禁止系统字段；
7. H1 不能 REQUEST_EVIDENCE，不能证明就 BLOCKED/NO_CHANGE；
8. rejected_refinements 用一个 REPAIR/REPLACE_METHOD；
9. sharing 用 BIND_EXISTING_GOAL；
10. no method 用 DECLARE_BLOCKED，让系统决定 synthesis；
11. 不输出内部 CoT；
12. block 外禁止文字。

pair：`planner-hierarchical-v8 <-> package4 <-> planning-decision-v1`。

---

# 42. H1 Codec

新增 `planning/decision_codec.py`，纯函数，不访问 DB。严格流程：唯一 block → mixed block guard → JSON → unknown keys → schema → payload → refs → canonicalize。

---

# 43. H1 Admission

新增 `planning/decision_admission.py`。顺序：request identity → package/prompt binding → plan revision → subject → visible refs → phase enable → payload checks → method/applicability/evidence/auth/capability → operation gate → typed admitted command。

---

# 44. H1 Adapter 到现有主链

新增 `planning/decision_adapter.py`：

```text
REFINE → existing RefineOperation
REPAIR/REPLACE_METHOD → retire_method + refine + REQUEST_STOP_THEN_RECONCILE
REPAIR/PROPOSE_SUCCESSOR → existing propose_successor
BIND_EXISTING_GOAL → existing bind_shared_goal
DECLARE_BLOCKED → existing synthesis/stall logic
WAIT/NO_CHANGE → durable decision only
```

不直接 SQL。

---

# 45. H1 线上接线

`hierarchical_dispatch.py` 严格双分支：legacy EXACT OLD PATH；new protocol 走 v8/package4 → persist → codec → admission → adapter → 原 ground/compiler/commit。

---

# 46. H1 黄金 fixtures

目录：

```text
tests/orchestrator/full_target/fixtures/planning_decision_v1/
  valid/
  invalid/
```

valid 至少 11 个；invalid 每个 rejection code 至少 1 个，并配 `expected_stage/expected_code`。

---

# 47. H1 验收门

合同、安全、兼容和现网四类必须全部通过。量化：H0 基线 0 新失败；legacy 0 新失败；sentinel 目标 <=22；mutation >=12 全 killed；独立核验不同会话；真实模型至少 REFINE/REPAIR/BLOCKED→synthesis/WAIT 四场景。

---

# 48. H2：PlannerPackage V1

H2 把 H1 的旧 package+附加字段收敛成正式 Views：`GoalView/ObligationView/PlanView/MethodView/FactView/AcceptedResultView/FailureView/CapabilityView/PlanningBudgetView`。

字段：

- GoalView：subject_key, occurrence/task/obligation, signature, statement, params, requirement refs, contract revision, requiredness
- ObligationView：parent, relation, demand, fuel, failures, attempts, budget
- PlanView：plan_revision, adopted methods, open compounds, pending primitives, ORDER/DATA 摘要, rejected refinements
- MethodView：method_ref, goal signature, registry/applicability, precondition summary, caps, schema, steps, rejected reasons
- FactView：observation_ref, proposition_key, polarity, availability, truth, coverage, observer, times
- AcceptedResultView：acceptance_ref, producer occurrence, ports, artifacts, currentness, support revision, permitted uses
- FailureView：source, reason, attempt/review ref, repeat count, last_seen, findings
- CapabilityView：registered/configured/reachable/healthy/authorized/compatible
- PlanningBudgetView：planning/synthesis/root-repair remaining, method candidate/new-step/repair-action limits, token budget

包 JSON <=96KiB；methods<=12/signature, facts<=24, accepted<=24, failures<=16, visible refs<=128。必须输出 `truncated/omitted_counts`，控制必需信息不能截掉。

新增 `PlannerPackageAssemblerV1`，current planner_package collectors 保留。package v5 + planner-hierarchical-v9。

---

# 49. H3：LLM-native Method Selection

新增 policy：`DETERMINISTIC / MODEL_ON_MULTIPLE / ALWAYS_MODEL`；新协议默认 MODEL_ON_MULTIPLE，legacy 不变。

触发：0 applicable→evidence/synthesis；1→deterministic fast path；2+→Planner REFINE。相同 `(plan_revision,evidence_epoch,candidate_set_digest)` 最多 1 selection call。

H3 正式启用 REQUEST_EVIDENCE，最多 8 questions，必须 predicate registered+observer+权限，复用现有 evidence pipeline。

---

# 50. H4：完整 RepairDecision

RepairActionType：`RETRY_SAME_METHOD, REFINE_DEEPER, REQUEST_EVIDENCE, REPLACE_METHOD, REBIND_INPUT, BIND_EXISTING_GOAL, CANCEL_BRANCH, REQUEST_COMPENSATION, DECLARE_RUNTIME_BLOCKED, ESCALATE, PROPOSE_SUCCESSOR`。

五触发源：Worker reject、Verifier/Acceptance reject、Evidence invalidation、Runtime unavailable、Requirements update。统一生成 RepairRequestV1。

程序 Impact Analysis 读取 reverse DATA/support/method membership/demand/acceptance/operation，输出 retained/revalidate/supersede/new_work/unresolved_operations/unknown_coverage。模型 affected_hints 不作为完整范围证明。

H4 正式启用 REQUEST_HUMAN。

---

# 51. H5：DomainPackage

新增 `PlanningDomainPackageV1 / ObserverRegistrationV1 / OperatorRegistrationV1`，复用 ObjectSchema/TaskTypeSpec/Predicate/MethodContract。安装做 hash、duplicate、cross-ref、capability validation。

静态禁止 core 目录出现 `if domain == code/appworld/drone`，新增 `scripts/check_htn_core_domain_branches.py`。

先迁 code/appworld，再增 `drone-sim-v1`，CI 只用模拟器。

---

# 52. H6：Method Lifecycle

完成 `TRIAL_ADMITTED → EVALUATED → ADMITTED`。新增冻结 EvaluationSet 和 MethodEvaluationRecord。默认 policy：trials>=20, heldout>=5, acceptance>=0.85, critical side-effect failure=0, no unresolved operation, median cost<=2x baseline。领域可覆盖；这些阈值不写进 MethodContract。

H6 才正式启用 PROPOSE_METHOD 统一 Decision；旧 `<method_proposal>` 保留兼容。

---

# 53. H7：PlanningBackendPort

定义 PlanningBackend / PlanningProblemSnapshot / PlanningLimits / PlanningBackendResult / CandidatePlanWitness。状态必须区分 SOLVED/UNSOLVABLE_PROVEN/SEARCH_LIMIT_REACHED/TIMEOUT/UNSUPPORTED_FEATURE/BACKEND_UNAVAILABLE/TOOL_ERROR。

现有 PANDA 通过 adapter 接口化；UP-Aries 作为 optional extra。Solver result 仍必须映射回 SH 结构 → validate → ProposedPlanDelta → Commit；SOLVED != Acceptance。

---

# 54. H8：跨领域验收

强制 code/appworld/drone-sim 三域。每域至少 8 normal +4 repair +2 evidence-conflict +2 recovery =16，三域至少48 scenarios，每个3 trials。

四臂：Strong Single Agent、H-native、H-native-no-repair、H-solver；同模型/工具/总预算。

真 Tello 只做额外人工监督单机验收，不是 CI 完成条件。

---

# 55. 每阶段统一质量门

mutation 下限：H1 12/H2 10/H3 8/H4 15/H5 8/H6 8/H7 10/H8 core 5。每阶段 contract/negative/restart/legacy/independent review/audit package。H1–H4 每阶段跑真实模型。

0.13.0 最终要求：冻结代表 14 局不劣于 0.12.2，硬不变量100%，若 completion 下降必须逐局证明为诚实拒绝而非协议缺陷。

---

# 56. Audit Package

每阶段：

```text
plans/llm-native-htn/Hx/
  plan.md
  journal.md
  source-map.json
  test-report.md
  mutation-report.md
  review.md
  review-disposition.md
  real-model/
    run-config.json
    per-case-summary.md
    receipts-manifest.json
```

---

# 57. H1 文件级施工顺序

H1-A Contracts → H1-B Store → H1-C Codec → H1-D Package v4 → H1-E Prompt v8 → H1-F Admission → H1-G Adapter → H1-H Hierarchical Dispatch → H1-I golden/mutation/real-model。

不并行修改热文件。

---

# 58. H1 测试文件建议

```text
test_planning_decision_contract.py
test_planning_decision_json_schema.py
test_planning_decision_codec.py
test_planning_decision_ref_scope.py
test_planning_decision_request_binding.py
test_planning_decision_store.py
test_planning_decision_adapter.py
test_planning_decision_refine_e2e.py
test_planning_decision_repair_e2e.py
test_planning_decision_sharing_e2e.py
test_planning_decision_legacy_bytes.py
test_planning_decision_recovery.py
```

---

# 59. 关键并发/恢复测试

至少：request 后 plan 变化、old decision 晚到、同 raw replay、同 ordinal 不同 raw、decision row 后 kill、admitted 后 kill、compile 后 kill、commit 成功回执丢、repair sibling running、Operation UNKNOWN + 换 Method。不得重复 PlanRevision/Operation，不得重置预算/责任。

---

# 60. H1 Done Definition

```text
[ ] JSON Schema 与 Python codec 黄金集一致
[ ] 一回复一 Decision
[ ] 系统字段不能由模型设置
[ ] visible ref scope 生效
[ ] 模型不再抄 plan revision
[ ] stale request 可检测
[ ] decision id 幂等
[ ] PlanningDecision 持久化/replay
[ ] REFINE 真接 existing compile/commit
[ ] REPAIR 替换真接 P2.3s reconcile
[ ] BIND_EXISTING_GOAL 真接 existing sharing
[ ] WAIT/NO_CHANGE 不生成 PlanRevision
[ ] legacy bytes/event/prompt hash 不变
[ ] mutation >=12 全 killed
[ ] 真实模型专项通过
[ ] 独立核验 = 可合
[ ] audit package 入库
```

---

# 61. 给执行 Agent 的直接指令

```text
先只做 H0/H1。

不得自行改变：
PlanningDecisionType、wire 字段名、一回复一个 Decision、PlanningRefKind、rejection code、H1 enable matrix、prompt/package 绑定、legacy 默认关闭、decision_id 系统生成、request-bound plan revision、migration 19 表职责。

顺序：H0 → contract/schema → golden fixtures → storage → codec → package/prompt → admission → adapter → live dispatch → mutation → real model → independent review。

所有新 Planner 结果最终仍走原 ground/compiler/Commit；Repair 继续受 P2.3s–v 的确定性安全机制约束。

遇源码冲突：停止当前片并写 blocker，不得通过删字段、放宽验证、fallback PASS 解决。
```

---

# 62. 最终设计原则

> **SimpleHarness 不负责替大模型思考得更聪明；它负责让大模型的规划结果以严格、可验证、可恢复、可审计的形式进入真实系统。**

未来模型越强，变的是 PlanningDecision/Method/Repair 的质量；不变的是 Protocol/Evidence/Authority/TaskNetwork/ORDER-DATA/Obligation/Budget/Commit/Acceptance/Recovery。
