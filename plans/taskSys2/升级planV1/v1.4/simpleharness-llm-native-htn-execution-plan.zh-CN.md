# SimpleHarness LLM-Native HTN Core 执行计划

**版本：HTN-LLM-NATIVE-1.0**  
**日期：2026-09-18**  
**适用基线：`DennyWanye/simple-harness-sdk@f2dfa6426ff55f944887fa938dea6fa8d0bfab82`（0.12.2 candidate）**  
**目标读者：负责继续实现 SimpleHarness HTN 的编码 Agent / 审阅 Agent**

---

# 0. 目标与总原则

SimpleHarness 的 HTN 不继续朝“自研一个越来越大的传统自动规划求解器”发展。

最终方向固定为：

> **LLM 负责复杂理解、创造、方法比较、未来状态推演和搜索；SimpleHarness 负责协议、结构、状态、证据、约束、Commit、执行、验收与恢复。**

换句话说：

```text
LLM
= 智能规划器 / 方法生成器 / 搜索器 / Repair 决策者

SimpleHarness HTN Core
= 规划协议
+ Method 标准
+ Planning World 标准
+ TaskNetwork 编译
+ 确定性准入
+ 版本与 Commit
+ 动态修复协议
+ Acceptance / GoalResolution
+ 可插拔 Solver Backend
```

长期目标：

> 随着大模型从 GPT-5.x → GPT-7 → GPT-10 变得更强，SimpleHarness 不需要不断重写核心 Planner，只需要模型输出越来越好的标准 PlanningDecision。

因此，本计划的核心不是增加更多搜索算法，而是把 **“模型想完以后必须交什么”**、**“系统怎样确定性地检查和执行”** 固定下来。

---

# 1. 不可改变的设计原则

## 1.1 LLM 负责不确定性，程序负责确定性

交给 LLM：

- 理解用户目标；
- 比较多个 Method；
- 进行未来状态推演；
- 选择路线；
- 提出新的 Method；
- 判断何时需要补证据；
- 判断失败原因；
- 提出 Repair；
- 提炼长期可复用 Method；
- 在复杂开放任务中进行隐式搜索、Tree-of-Thought、内部 rollout 等。

留给程序：

- ID / Version / Scope；
- 参数类型检查；
- Method 是否存在；
- Predicate 是否注册；
- 前提是否有足够证据；
- 权限和授权；
- ORDER / DATA 合法性；
- TaskNetwork 是否存在结构冲突；
- Budget / Obligation 守恒；
- Operation 是否重复；
- PlanRevision 原子提交；
- Acceptance / GoalResolution；
- 崩溃恢复和外部副作用核对。

## 1.2 HTN Core 不关心模型内部怎么思考

SimpleHarness 不要求模型解释：

```text
我是用 DFS
还是 MCTS
还是 latent reasoning
还是内部模拟了 40 个未来
```

SimpleHarness 只要求：

```text
你最终选择了什么？
依据哪些事实？
有哪些假设？
需要什么证据？
你建议系统修改哪一部分正式计划？
```

## 1.3 不自研通用求解器作为默认路线

HTN Core **不默认实现**：

- 通用 A*；
- 通用 MCTS；
- CP-SAT；
- 通用 temporal planner；
- 通用 PDDL/HDDL planner；
- 全世界 World Model；
- 通用 cost-to-go 学习器；
- 在线强化学习 Planner。

这些能力以后通过：

```text
LLM-native Planner
或
PlanningBackendPort
```

获得。

## 1.4 Solver 是 specialized backend，不是系统大脑

默认：

```text
backend = llm-native
```

可选：

```text
panda
up-aries
future-solver
```

Solver 返回的只是 **Candidate Plan / Witness**。

它不能：

- 修改 TaskNetwork；
- 写 Budget；
- 创建 Operation；
- 跳过 Evidence；
- 跳过 Acceptance；
- 直接完成 Mission。

---

# 2. 当前代码必须保留的基础

以下能力已经存在，不重新实现：

```text
contracts/htn.py
planning/htn/registry.py
planning/htn/applicability.py
planning/htn/grounding.py
planning/htn/refinement.py
planning/htn/compiler.py
planning/htn/validation.py
planning/htn/world.py
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

当前已有的重要语义继续保留：

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
- 四态证据 `TRUE / FALSE / UNKNOWN / CONFLICT`
- recursion fuel
- repeated expansion / no-state-change
- `InputManifest`
- `Acceptance / GoalResolution`
- PANDA/HDDL adapter
- Proposal → Compile → Commit

---

# 3. 最终 HTN Core：10 个稳定模块

SimpleHarness HTN 最终只把以下 10 个概念视为“核心”：

```text
1. Goal / Task / Obligation
2. MethodContract
3. MethodRegistry
4. PlanningWorld / Predicate / Evidence
5. MethodApplicability
6. PlanningDecision Protocol
7. TaskNetwork Compiler
8. Execution Projection（ORDER / DATA）
9. RepairDecision Protocol
10. Acceptance / GoalResolution
```

它们外面的高级搜索能力全部可替换。

---

# 4. 总体运行闭环

```text
User / Main Agent
        │
        ▼
RequirementsRevision
        │
        ▼
Planner Agent
读取：
- 当前 Goal / Obligation
- 当前 TaskNetwork
- Method Library
- Facts / Evidence
- Accepted Results
- Failures
- Budget / Capabilities
        │
        │ 模型内部自行：
        │ - 分析
        │ - 推演
        │ - 比较
        │ - 搜索
        ▼
PlanningDecisionEnvelopeV1
        │
        ▼
Deterministic Planning Admission
- schema
- ids
- method existence
- parameter types
- evidence
- authorization
- structure
- budget
        │
        ▼
Compiler
        │
        ▼
ProposedPlanDelta
        │
        ▼
CommitService
        │
        ▼
TaskNetwork / PlanRevision
        │
        ▼
Scheduler / BaseAgent
        │
        ▼
Candidate Result / Observation / Failure
        │
        ├──────────────► Verifier / Acceptance
        │
        └──────────────► Planner Repair
                              │
                              ▼
                    PlanningDecisionEnvelopeV1
```

---

# 5. 第一核心协议：PlanningDecisionEnvelopeV1

这是本计划最重要的新合同。

## 5.1 为什么需要统一 Envelope

当前已有：

```text
PlanProposal
MethodProposal
repair hints
root-review repair
Manager 建议
MethodSynthesizer 输出
```

这些输出在语义上都是：

> “模型建议系统下一步怎样改变规划状态。”

长期目标应该统一成：

```text
PlanningDecisionEnvelopeV1
```

但 **不立即删除旧 PlanProposal / MethodProposal**。

第一阶段采用：

```text
PlanningDecisionEnvelope
    └── payload
         ├── legacy PlanProposal adapter
         └── legacy MethodProposal adapter
```

等新协议稳定后再逐步退役旧 wire format。

---

# 6. DecisionType

建议新增：

```python
class PlanningDecisionType(StrEnum):
    REFINE = "REFINE"
    SELECT_METHOD = "SELECT_METHOD"
    PROPOSE_METHOD = "PROPOSE_METHOD"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    REPAIR = "REPAIR"
    RETIRE_METHOD_USE = "RETIRE_METHOD_USE"
    REUSE_ACCEPTED_RESULT = "REUSE_ACCEPTED_RESULT"
    DECLARE_BLOCKED = "DECLARE_BLOCKED"
    REQUEST_HUMAN = "REQUEST_HUMAN"
    NO_CHANGE = "NO_CHANGE"
```

语义：

### REFINE

选择一个已经注册的方法展开某个 compound occurrence。

### SELECT_METHOD

当系统显式要求“从多个候选 Method 中选一个”时使用。可以和 REFINE 共用内部执行，但 wire 层保留独立语义。

### PROPOSE_METHOD

没有可用 Method，模型提出新的 `MethodProposal`。模型只能生成 DRAFT。

### REQUEST_EVIDENCE

模型判断继续规划前需要知道某项事实。

### REPAIR

运行中失败 / 证据改变 / root review reject 后提出结构化修复。

### RETIRE_METHOD_USE

明确不再使用某个 **MethodInstance**，不是删除 MethodDefinition。

### REUSE_ACCEPTED_RESULT

建议某 occurrence 复用已有 Acceptance，系统仍需检查 reuse policy。

### DECLARE_BLOCKED

Planner 判断当前没有合法推进方式。这是报告，不是 Mission 自动 FAIL。

### REQUEST_HUMAN

需要用户/人工输入、授权或裁决。

### NO_CHANGE

Planner 判断不应修改 Plan。

---

# 7. PlanningDecisionEnvelopeV1 Wire Contract

建议新增：

```text
src/agent_orchestrator/contracts/planning_decisions.py
```

```python
@dataclass(frozen=True, slots=True)
class PlanningDecisionEnvelopeV1:
    schema_version: int
    decision_id: str

    subject: PlanningSubject
    decision_type: PlanningDecisionType

    rationale: str
    reason_refs: tuple[TypedRef, ...]
    assumptions: tuple[AssumptionHint, ...]

    payload: PlanningDecisionPayload

    model_declared_uncertainties: tuple[str, ...] = ()
    alternatives_considered: tuple[AlternativeSummary, ...] = ()
    stop_or_replan_conditions: tuple[ReplanConditionHint, ...] = ()
```

模型 **不能写**：

```text
mission_id authority
principal
scope authority
manager_epoch
budget_account
budget_grant
registry_status
dispatch_generation authoritative value
plan_revision authoritative value
approval id
operation id
acceptance id
```

这些由 request context 和系统绑定。

---

# 8. PlanningSubject

```python
@dataclass(frozen=True, slots=True)
class PlanningSubject:
    occurrence_id: OccurrenceId
    task_ref: TaskRef
    obligation_id: ObligationId
```

Mission 信息来自当前 dispatch intent。

模型不能把 Proposal 改投别的 Mission。

---

# 9. ReasonRefs：Planner 的理由必须可追踪

不要要求模型完整输出 chain-of-thought。

只要求它告诉系统：

```text
“这个决定主要依据哪些正式对象？”
```

例如：

```json
{
  "reason_refs": [
    {"kind": "fact", "id": "fact-test-failing"},
    {"kind": "acceptance", "id": "acc-patch-12"},
    {"kind": "method", "id": "code.fix-by-patch@2"}
  ]
}
```

目的：

- 不是审计模型隐式思维；
- 是形成可复核的输入依据；
- 给 Repair / Replay / UI 提供结构化解释；
- 检测 Planner 使用了请求中不存在的假事实。

系统必须验证：

```text
reason_ref ∈ PlannerPackage.visible_refs
```

如果模型引用一个根本没给过它的 Observation：

```text
DECISION_REF_OUTSIDE_CONTEXT
```

不能猜。

---

# 10. Assumption 协议

Planner 可以提出假设，但假设不能冒充 Fact。

```python
@dataclass(frozen=True, slots=True)
class AssumptionHint:
    key: str
    statement: str
    required_for: tuple[str, ...]
    risk: Literal["low", "medium", "high"]
    suggested_observation: str | None
```

规则：

```text
Assumption
≠ Evidence
≠ TRUE
```

如果一个安全/执行前提只能靠 assumption：

```text
不得派发。
```

Planner 可以说：

> “如果 X 成立，我建议 Method A。”

系统则转成 `REQUEST_EVIDENCE(X)` 或保持 method inapplicable。

---

# 11. REFINE Payload

```python
@dataclass(frozen=True, slots=True)
class RefineDecision:
    method_ref: MethodRef
    parameter_bindings: Mapping[str, JsonValue]

    reuse_hints: tuple[ReuseHint, ...] = ()
    preferred_order_hints: tuple[OrderHint, ...] = ()
```

模型只能选择 Method 和参数。

模型 **不能直接输出**：

```text
新 Task ID
新 Occurrence ID
Budget Account ID
Acceptance ID（除非引用 visible_refs 中已有对象）
Operation ID
DispatchGeneration
```

这些由 compiler 生成或解析现有引用。

---

# 12. PROPOSE_METHOD Payload

继续复用当前 `MethodProposal`。

新 Envelope：

```text
PlanningDecisionEnvelopeV1
    decision_type = PROPOSE_METHOD
    payload.method_proposal = MethodProposal
```

Method Proposal 仍走：

```text
DRAFT
→ structural validation
→ admission
→ TRIAL_ADMITTED
→ current Mission
```

模型不能直接声明 `ADMITTED`。

---

# 13. REQUEST_EVIDENCE Payload

```python
@dataclass(frozen=True, slots=True)
class EvidenceRequestDecision:
    propositions: tuple[EvidenceQuestion, ...]
```

```python
@dataclass(frozen=True, slots=True)
class EvidenceQuestion:
    predicate_ref: VersionedRef
    arguments: Mapping[str, JsonValue]
    purpose: str
    blocking: bool
```

系统处理：

```text
验证 predicate 是否注册
        ↓
查 observer
        ↓
生成 EvidenceOccurrence
        ↓
只读执行
        ↓
Fact / UNKNOWN / UNAVAILABLE
```

Planner 不能指定：“把结果记成 TRUE”。

---

# 14. RepairDecision Protocol

建议 `REPAIR` 使用明确的 Repair AST。

```python
class RepairActionType(StrEnum):
    RETRY_SAME_METHOD = "RETRY_SAME_METHOD"
    REFINE_DEEPER = "REFINE_DEEPER"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    REPLACE_METHOD = "REPLACE_METHOD"
    REBIND_INPUT = "REBIND_INPUT"
    REUSE_ACCEPTED_RESULT = "REUSE_ACCEPTED_RESULT"
    CANCEL_BRANCH = "CANCEL_BRANCH"
    REQUEST_COMPENSATION = "REQUEST_COMPENSATION"
    DECLARE_RUNTIME_BLOCKED = "DECLARE_RUNTIME_BLOCKED"
    ESCALATE = "ESCALATE"
```

```python
@dataclass(frozen=True, slots=True)
class RepairDecision:
    trigger_refs: tuple[TypedRef, ...]
    diagnosis: RepairDiagnosis
    actions: tuple[RepairAction, ...]
```

---

# 15. RepairDiagnosis

```python
class RepairCause(StrEnum):
    IMPLEMENTATION_DEFECT = "IMPLEMENTATION_DEFECT"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    METHOD_INAPPLICABLE = "METHOD_INAPPLICABLE"
    INPUT_STALE = "INPUT_STALE"
    REQUIREMENTS_CHANGED = "REQUIREMENTS_CHANGED"
    PERMISSION_MISSING = "PERMISSION_MISSING"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    VERIFICATION_REJECTED = "VERIFICATION_REJECTED"
    COMPOSITION_FAILED = "COMPOSITION_FAILED"
    EXTERNAL_OUTCOME_UNKNOWN = "EXTERNAL_OUTCOME_UNKNOWN"
    BUDGET_LIMIT = "BUDGET_LIMIT"
    PLANNING_BOUND_REACHED = "PLANNING_BOUND_REACHED"
    RUNTIME_FAILURE = "RUNTIME_FAILURE"
    UNKNOWN = "UNKNOWN"
```

模型的 diagnosis 是建议。

系统根据真实事件、Evidence、Operation 状态验证。

---

# 16. Repair 的硬规则

## 16.1 UNKNOWN Operation 不允许通过 Repair 绕过

如果：

```text
Operation O = UNKNOWN
```

模型提出 `REPLACE_METHOD`，系统仍必须先 reconcile O。

不能因为 Method 换了就重新执行同一现实动作。

## 16.2 换 Method 不重置 Obligation

```text
Method A failed
→ Method B
```

同一稳定责任：

```text
ObligationId 不变
累计预算不归零
失败计数不归零
```

## 16.3 Repair 不能直接确定 impact scope

模型可以输出 `affected_hint`，但系统自己计算：

```text
DATA reverse edges
support reverse edges
MethodInstance membership
shared DemandRefs
Operation state
```

模型不能说“只影响 B”，系统就只改 B。

---

# 17. MethodContract 最终稳定核心

当前 MethodContract 已经很接近最终形态。

本计划要求 **停止继续无限扩张 MethodContract**。

MethodContract 只保存：

```text
identity
achieves
parameters
applicable_when
steps
ordering
data flow
composition
declared effects / risk
```

不保存：

```text
动态 priority
当前评分
LLM confidence
临时搜索分数
某一模型的思考过程
当前预算余额
当前 Agent 数量
```

这些属于运行时。

---

# 18. MethodContract 推荐最终结构

概念结构：

```python
@dataclass(frozen=True, slots=True)
class MethodContract:
    method_id: str
    version: int

    achieves: GoalSignature
    parameter_schema_ref: VersionedRef

    applicable_when: Condition
    steps: tuple[MethodStep, ...]
    ordering: tuple[MethodOrderConstraint, ...]

    composition: CompositionSpec
    declared_effects: tuple[EffectDeclaration, ...]

    provenance: Provenance
```

实际实现以当前已有字段优先复用。

**不要为了和本文字段名字完全一致而重写已有 canonical bytes。**

---

# 19. MethodStep 必须回答的 7 件事

每个 Step 必须能回答：

```text
1. 要做什么 TaskType？
2. 参数从哪来？
3. 它 refine parent obligation 还是独立 responsibility？
4. 必需还是可选？
5. 是否可复用已有 Acceptance？
6. 输入从哪个 step output 来？
7. 它的 side-effect 风险是什么？
```

不能只有：

```text
"step": "analyze"
```

---

# 20. Composition 是 Method 的必需语义

一个 Method 不能只写：

```text
A
B
C
```

还要说明：

> A/B/C 的结果为什么合起来满足父目标？

至少包含：

```python
@dataclass(frozen=True, slots=True)
class CompositionSpec:
    required_slots: tuple[str, ...]
    criterion_links: Mapping[str, tuple[str, ...]]
    review_policy_ref: VersionedRef
```

GoalResolution 必须检查 Composition。

---

# 21. PlanningWorld：只表达“当前规划需要的最小世界”

不建立全世界状态。

```python
@dataclass(frozen=True, slots=True)
class PlanningWorldSnapshot:
    snapshot_id: str
    evidence_revision: int
    capability_revision: int
    predicate_values: Mapping[GroundPredicateKey, PredicateObservation]
```

PlanningWorld 是只读快照。

Planner / Compiler 不直接修改 World。

---

# 22. Predicate 标准

建议统一：

```python
@dataclass(frozen=True, slots=True)
class PredicateSpec:
    predicate_ref: VersionedRef
    arguments_schema_ref: VersionedRef

    observation_policy: ObservationPolicy
    freshness_policy_ref: VersionedRef

    allowed_uses: frozenset[PredicateUse]
```

四态：

```text
TRUE
FALSE
UNKNOWN
CONFLICT
```

不能把 `UNAVAILABLE` 硬映射成 FALSE。

---

# 23. Observer 标准

```python
class PredicateObserver(Protocol):
    @property
    def predicate_ref(self) -> VersionedRef: ...

    async def observe(
        self,
        request: ObservationRequest,
        context: ObservationContext,
    ) -> ObservationResult: ...
```

```python
@dataclass(frozen=True, slots=True)
class ObservationResult:
    availability: Availability
    truth: TruthValue | None
    evidence_refs: tuple[EvidenceRef, ...]
    observed_at_ms: int
    valid_until_ms: int | None
    coverage: ObservationCoverage
```

Observer 不直接写 Knowledge。

Commit/Evidence path 负责正式记录。

---

# 24. Operator 标准

TaskType 不能只是一个 label。

Primitive Task 必须对应注册 Operator。

```python
@dataclass(frozen=True, slots=True)
class OperatorSpec:
    operator_ref: VersionedRef
    task_type_ref: VersionedRef

    input_schema_ref: VersionedRef
    output_schema_ref: VersionedRef

    preconditions: tuple[Condition, ...]

    side_effect_kind: SideEffectKind
    required_capabilities: tuple[str, ...]

    verification_policy_ref: VersionedRef
```

`expected_effects` 只能作为规划预测。

真实执行结果仍然需要：

```text
Observation
CheckReceipt
Acceptance
```

---

# 25. DomainPackage 标准

目标：新增一个陌生领域时，不改 HTN Core。

```python
@dataclass(frozen=True, slots=True)
class PlanningDomainPackage:
    domain_id: str
    version: int

    schemas: tuple[ObjectSchema, ...]
    predicates: tuple[PredicateSpec, ...]
    observers: tuple[ObserverRegistration, ...]
    task_types: tuple[TaskTypeSpec, ...]
    operators: tuple[OperatorSpec, ...]
    methods: tuple[MethodContract, ...]
```

安装：

```python
planning_world.install(domain_package)
```

必须通过：

```text
schema validation
duplicate identity
content hash
predicate/operator compatibility
method structural validation
capability availability
```

Core 禁止出现：

```python
if domain == "code":
if domain == "drone":
if domain == "appworld":
```

---

# 26. MethodApplicability 的最终职责

MethodApplicability 只回答：

```text
这个 Method 在当前 PlanningWorld 中：
- 可用？
- 被反驳？
- 需要证据？
- 存在冲突？
- 当前没有执行能力？
```

不回答：

```text
它是不是“最聪明”的方法？
```

建议语义：

```python
class ApplicabilityStatus(StrEnum):
    APPLICABLE = "APPLICABLE"
    INAPPLICABLE = "INAPPLICABLE"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    CONFLICTED = "CONFLICTED"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
```

选择哪个 Method 是 Planner 的工作。

---

# 27. 当前 deterministic `chosen = selectable[0]` 的定位

现有 deterministic selector 不删除。

但其定位改为：

```text
Fallback / deterministic baseline
```

默认生产模式：

```text
Planner Agent
→ explicit MethodSelection
```

只有以下情况可以 fallback：

```text
1 个唯一 applicable method
或者
policy 明确允许 deterministic choice
```

多个高价值候选时：

```text
不要 silent first().
```

应进入 Planner decision。

---

# 28. PlannerPackage 标准输入

建议统一 Planner 看见的结构：

```python
@dataclass(frozen=True, slots=True)
class PlannerPackageV1:
    package_id: str

    goal: GoalView
    obligation: ObligationView

    current_plan: PlanView
    method_library: tuple[MethodView, ...]

    facts: tuple[FactView, ...]
    accepted_results: tuple[AcceptedResultView, ...]
    failures: tuple[FailureView, ...]

    capabilities: CapabilityView
    budget: PlanningBudgetView

    visible_refs: tuple[TypedRef, ...]

    request_kind: PlanningRequestKind
```

模型只能正式引用 `visible_refs` 中存在的 ref。

自然语言可以讨论其他可能性，但系统不能把未给出的 ID 当正式事实。

---

# 29. PlannerPackage 必须给模型什么，不应该给什么

必须：

```text
根用户目标摘要
当前 Goal / Task
当前 MethodInstance
可用 Method
明确 Fact / UNKNOWN / CONFLICT
已通过 Acceptance
失败原因
Verifier feedback
预算边界
允许能力
```

不应该：

```text
整个数据库
所有 Agent 历史
所有内部权限对象
密钥
未授权敏感信息
系统真实 authority token
```

---

# 30. Planner 输出不要求 CoT

禁止把“完整思维链”作为协议依赖。

只要求：

```text
decision
rationale（简洁）
reason_refs
assumptions
alternatives_considered（摘要）
stop/replan conditions
```

这样可以：

- 模型可替换；
- 不依赖特定 CoT 格式；
- 不把安全边界建立在“模型有没有解释好”上。

---

# 31. Stop / Replan Condition Hint

Planner 可以告诉系统：

> “什么情况下这个选择应该重新考虑？”

```python
@dataclass(frozen=True, slots=True)
class ReplanConditionHint:
    condition: Condition
    suggested_action: str
```

系统验证 Condition AST。

模型不能注册任意 Python callback。

---

# 32. Method Lifecycle

当前已有：

```text
DRAFT
STRUCTURALLY_VALID
TRIAL_ADMITTED
EVALUATED
ADMITTED
SUSPENDED
REJECTED
RETIRED
```

需要完成：

```text
TRIAL_ADMITTED
        ↓
trace collection
        ↓
offline Method Evaluation
        ↓
EVALUATED
        ↓
promotion policy
        ↓
ADMITTED
```

---

# 33. MethodEvaluationRecord

新增：

```python
@dataclass(frozen=True, slots=True)
class MethodEvaluationRecord:
    method_ref: MethodRef
    evaluation_set_id: str

    trials: int
    successes: int
    verifier_accepts: int
    composition_accepts: int

    failures_by_kind: Mapping[str, int]

    median_cost: float | None
    median_runtime_ms: int | None

    evaluated_domains: tuple[str, ...]
    counterexample_refs: tuple[TypedRef, ...]

    result: MethodEvaluationVerdict
```

不要给 Method 一个“万能 0.87 分”。

不同任务域指标不同。

---

# 34. PromotionPolicy

```python
class MethodPromotionPolicy(Protocol):
    def evaluate(
        self,
        method: MethodContract,
        record: MethodEvaluationRecord,
    ) -> MethodPromotionDecision:
        ...
```

模型可以生成候选评估摘要。

最终晋级由系统/离线评测政策决定。

模型不能自我晋级。

---

# 35. Method 经验保存什么

保存：

```text
适用范围
成功样本
失败样本
反例
Verifier findings
平均成本
出现的证据类型
模型版本
环境
```

不保存为核心 Method 字段：

```text
“某个模型觉得这个很好”
```

---

# 36. PlanningBackendPort

Solver 统一通过：

```text
src/agent_orchestrator/planning/backends/base.py
```

建议：

```python
class PlanningBackend(Protocol):
    @property
    def backend_id(self) -> str: ...

    def capabilities(self) -> PlanningBackendCapabilities: ...

    async def solve(
        self,
        problem: PlanningProblemSnapshot,
        limits: PlanningLimits,
    ) -> PlanningBackendResult: ...

    async def validate(
        self,
        problem: PlanningProblemSnapshot,
        candidate: CandidatePlanWitness,
        limits: PlanningLimits,
    ) -> PlanningValidationResult: ...
```

---

# 37. Backend 状态必须细分

```python
class PlanningBackendStatus(StrEnum):
    SOLVED = "SOLVED"
    VALIDATED = "VALIDATED"
    INVALID = "INVALID"

    UNSOLVABLE_PROVEN = "UNSOLVABLE_PROVEN"

    SEARCH_LIMIT_REACHED = "SEARCH_LIMIT_REACHED"
    TIMEOUT = "TIMEOUT"

    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    TOOL_ERROR = "TOOL_ERROR"
```

绝对不能：

```text
TIMEOUT → UNSOLVABLE
```

---

# 38. BackendCapabilities

```python
@dataclass(frozen=True, slots=True)
class PlanningBackendCapabilities:
    hierarchical: bool
    partial_order: bool

    temporal: bool
    numeric_integer: bool

    optimization_metrics: tuple[str, ...]
    supported_condition_ops: tuple[str, ...]

    plan_validation: bool
    repair_support: bool
```

调用前先 capability check。

不支持时明确：

```text
UNSUPPORTED_FEATURE
```

不能静默删掉语义。

---

# 39. 默认 Backend 策略

```text
llm-native
```

未来：

```text
BackendRouter
├── llm-native
├── panda
└── up-aries
```

路由示例：

```text
开放研究 / 写代码 / 未知多
→ llm-native

明确有限 HTN + HDDL
→ panda

时间 / 资源 / makespan / action-cost
→ up-aries
```

这个路由可先从规则开始。

---

# 40. Solver 输出不能直接成为 PlanRevision

必须：

```text
BackendResult
        ↓
CandidatePlanWitness
        ↓
Result Mapper
        ↓
SimpleHarness Method/Occurrence/ORDER/DATA
        ↓
validate
        ↓
ProposedPlanDelta
        ↓
Commit
```

---

# 41. CandidatePlanWitness

统一：

```python
@dataclass(frozen=True, slots=True)
class CandidatePlanWitness:
    backend_id: str
    backend_version: str

    source_problem_hash: str

    method_instances: tuple[BackendMethodInstance, ...]
    primitive_occurrences: tuple[BackendPrimitiveOccurrence, ...]
    ordering: tuple[BackendOrderConstraint, ...]

    objective_values: Mapping[str, float]

    raw_artifact_ref: ArtifactRef | None
```

所有 backend 必须能映射到稳定 occurrence。

不能映射：

```text
不能自动采用。
```

---

# 42. LLM-Native Backend

不要真的写一个复杂 solver。

`llm-native` 的含义：

```text
Planner Agent 就是 planning engine
```

通过：

```text
PlannerPackageV1
→ PlanningDecisionEnvelopeV1
```

工作。

例如：

```text
unsolvable_proven = 永远 false
```

除非有独立形式证明。

---

# 43. TaskNetwork Compiler 的职责保持不变

Compiler 不是 Planner。

Compiler 负责：

```text
MethodInstanceDraft
        ↓
child occurrences
ORDER
DATA
shared result mapping
Obligation openings
coverage
budget requirement
semantic read-set
```

Compiler 不应该：

```text
调用模型
自己选择路线
自己做高阶未来搜索
```

---

# 44. Compile 前必须重新检查的内容

```text
Method version
Task contract
parameter schema
precondition evidence
capability
reuse Acceptance currentness
required slots
DATA ports
ORDER edges
structural budget
```

---

# 45. Plan Commit 不信 Planner

即使未来模型非常强，也必须：

```text
PlanningDecision
        ↓
parse
        ↓
validate
        ↓
ground
        ↓
compile
        ↓
current-state revalidation
        ↓
commit
```

Planner 的“我已经确认”没有系统权限。

---

# 46. 错误分类标准化

建议统一：

```python
class PlanningRejectionCode(StrEnum):
    MALFORMED_DECISION = "MALFORMED_DECISION"
    DECISION_REF_OUTSIDE_CONTEXT = "DECISION_REF_OUTSIDE_CONTEXT"

    METHOD_NOT_FOUND = "METHOD_NOT_FOUND"
    METHOD_VERSION_STALE = "METHOD_VERSION_STALE"
    METHOD_NOT_APPLICABLE = "METHOD_NOT_APPLICABLE"
    METHOD_NOT_AUTHORIZED = "METHOD_NOT_AUTHORIZED"

    PARAMETER_TYPE_ERROR = "PARAMETER_TYPE_ERROR"
    UNKNOWN_PREDICATE = "UNKNOWN_PREDICATE"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"

    DATA_UNBOUND = "DATA_UNBOUND"
    ORDER_CYCLE = "ORDER_CYCLE"
    REFINEMENT_CYCLE = "REFINEMENT_CYCLE"
    COVERAGE_GAP = "COVERAGE_GAP"

    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    OBLIGATION_NOT_OPEN = "OBLIGATION_NOT_OPEN"
    OPERATION_UNRESOLVED = "OPERATION_UNRESOLVED"

    PLAN_READ_SET_STALE = "PLAN_READ_SET_STALE"
```

Planner 下一轮收到结构化 reason code。

不要只收到：

```text
"plan failed"
```

---

# 47. Feedback 给 Planner 的协议

```python
@dataclass(frozen=True, slots=True)
class PlanningFeedbackV1:
    previous_decision_id: str
    accepted: bool

    rejection_codes: tuple[PlanningRejectionCode, ...]
    problems: tuple[PlanningProblemDetail, ...]

    changed_refs: tuple[TypedRef, ...]

    repairable: bool
    retry_budget_remaining: int
```

这个 Feedback 是模型下次 Repair 的主要依据。

---

# 48. Dynamic Repair 完整闭环

```text
Worker / Verifier / Evidence / Runtime
        ↓
ExecutionFeedback
        ↓
系统分类 + formal facts
        ↓
PlannerPackage(request_kind=REPAIR)
        ↓
PlanningDecisionEnvelope(REPAIR)
        ↓
Impact Analysis
        ↓
Compile Repair Delta
        ↓
Commit
        ↓
旧工作收敛 + 新计划
```

---

# 49. Impact Analysis 必须是程序做

Repair 模型可给：

```text
affected_hints
```

系统自己计算：

```text
reverse DATA
support edges
MethodInstance membership
Acceptance dependency
shared Demand
Operation state
```

输出：

```python
@dataclass(frozen=True, slots=True)
class RepairImpact:
    retained: tuple[TypedRef, ...]
    revalidate: tuple[TypedRef, ...]
    supersede: tuple[TypedRef, ...]
    new_work: tuple[TypedRef, ...]
    unresolved_operations: tuple[TypedRef, ...]
```

---

# 50. 不要把所有失败都理解成“重新分解”

```text
实现缺陷
→ RETRY_SAME_METHOD

缺证据
→ REQUEST_EVIDENCE

任务太大
→ REFINE_DEEPER

Method 前提失效
→ REPLACE_METHOD

输入旧了
→ REBIND_INPUT

权限不足
→ REQUEST_HUMAN / wait

外部 UNKNOWN
→ reconciliation

Verifier reject
→ REPAIR / replace / retry

Backpressure
→ Scheduler，不改内容计划
```

---

# 51. Context 与 HTN 的边界

HTN 决策不能依赖“模型恰好召回到了某件事”。

必需控制信息：

```text
Goal
Obligation
Method
current plan
required evidence
accepted inputs
failure feedback
```

必须结构化进入 PlannerPackage。

可选背景知识：

```text
历史讨论
相似任务
旧失败
文档
```

可以 retrieval。

---

# 52. 大模型未来越来越强时，哪些部分会自然增强

不改 Core：

```text
Method 选择质量
未来状态推演
Repair 原因判断
Method 生成
多方案比较
未知风险发现
```

只需升级：

```text
model
prompt
PlannerPackage policy
evaluation
```

---

# 53. 哪些部分不会因为模型变聪明而消失

永远保留：

```text
Typed IDs
Version
Scope
Evidence
Authority
Budget
TaskNetwork
ORDER / DATA
Commit
Operation identity
Acceptance
Recovery
```

因为这些是系统一致性问题，不是智力问题。

---

# 54. 建议代码目录

```text
src/agent_orchestrator/

contracts/
  planning_decisions.py      NEW
  repair_decisions.py        NEW
  planning_backends.py       NEW

planning/
  decision_codec.py          NEW
  decision_admission.py      NEW
  planner_package.py         EXTEND

  htn/
    registry.py              KEEP/EXTEND
    applicability.py         KEEP
    grounding.py             KEEP
    refinement.py            REDUCE TO CORE/FALLBACK
    compiler.py              KEEP
    validation.py            KEEP
    world.py                 EXTEND

  repair/
    classification.py        NEW
    impact.py                NEW
    compiler.py              NEW

  backends/
    base.py                  NEW
    llm_native.py            NEW
    panda.py                 ADAPT EXISTING
    up_aries.py              FUTURE OPTIONAL

knowledge/
  predicates.py              EXTEND
  validity.py                KEEP

orchestrator/
  hierarchical_dispatch.py   REWIRE
  plan_commits.py            KEEP
```

实际 checkout 若已经有等价位置：

```text
扩展原文件
不要平行复制。
```

---

# 55. 兼容策略

必须兼容：

```text
legacy Mission
full-target hierarchical Mission
已有 PlanProposal
已有 MethodProposal
已有 PANDA adapter
已有 persisted MethodContract
```

新增：

```text
planning_protocol_version = "planning-decision-v1"
```

默认新 hierarchical Mission 可逐步启用。

旧历史不重新解析成新 Decision。

---

# 56. Adapter：旧 PlanProposal → PlanningDecision

第一阶段：

```python
def legacy_plan_proposal_to_decision(
    proposal: PlanProposal,
    context: PlannerPackageV1,
) -> PlanningDecisionEnvelopeV1:
    ...
```

只映射能证明的语义。

不要补假的：

```text
alternatives_considered
assumptions
reason_refs
```

缺少就为空。

---

# 57. Adapter：旧 MethodProposal

```python
def legacy_method_proposal_to_decision(
    proposal: MethodProposal,
    subject: PlanningSubject,
) -> PlanningDecisionEnvelopeV1:
    ...
```

decision_type：

```text
PROPOSE_METHOD
```

---

# 58. Prompt 输出格式

建议统一：

```xml
<planning_decision>
{
  "schema_version": 1,
  "decision_id": "...",
  "decision_type": "REFINE",
  ...
}
</planning_decision>
```

Planner / Repair Planner 都使用同一个 block。

MethodSynthesizer 可暂时保留：

```xml
<method_proposal>
```

迁移完成后可统一进 `PROPOSE_METHOD`。

---

# 59. Prompt 最重要的规则

Prompt 不要求模型实现内部算法。

只说：

```text
你可以自行进行任意必要的规划、比较和未来推演。

最后必须返回一个 PlanningDecision。

不要伪造系统 ID。
不要把假设当事实。
不知道的前提使用 REQUEST_EVIDENCE。
已有 Method 不够时 PROPOSE_METHOD。
当前路线不再适用时 REPAIR。
```

---

# 60. Planner 请求类型

```python
class PlanningRequestKind(StrEnum):
    INITIAL = "INITIAL"
    REFINE = "REFINE"
    SELECT = "SELECT"
    REPAIR = "REPAIR"
    RECOVER = "RECOVER"
    REVIEW_REPAIR = "REVIEW_REPAIR"
```

请求类型改变上下文，但不改变 Decision wire contract。

---

# 61. Deterministic fallback

必须保留一个没有 LLM 也可运行的最小 fallback：

```text
唯一 applicable method
→ 选择

明确 UNKNOWN
→ REQUEST_EVIDENCE

0 applicable methods
→ DECLARE_BLOCKED / request synthesizer
```

用途：

- 单元测试；
- offline；
- 低成本；
- 模型不可用；
- 基线评测。

它不是未来的主智能来源。

---

# 62. Evaluation：比较“框架价值”，不是算法炫技

评测至少四臂：

```text
S: strong single-agent
H-native: SimpleHarness LLM-native HTN
H-native-no-repair
H-solver: SimpleHarness + optional Solver
```

看：

```text
Mission 完成率
错误完成率
模型调用数
重复工作量
被保留的合法成果
Repair 成功率
Verifier reject 后恢复率
预算
时间
```

---

# 63. 不允许用以下指标证明 HTN 更好

```text
更多 Agent
更多 Task
更深 DAG
更多 Method
更多 Token
更多 planning rounds
```

这些都不是成功。

---

# 64. 跨领域验收

必须至少 3 个完全不同领域：

```text
A. code
B. appworld / enterprise workflow
C. 新领域
```

推荐 C：

```text
Tello TT drone domain
```

原因：

- 有真实能力；
- 有安全限制；
- 有观察；
- 有动作；
- 有失败；
- 有副作用；
- 与代码任务差异很大。

---

# 65. Drone Domain 验收目标

不修改 HTN Core，只增加：

```text
schemas
predicates
observers
task types
operators
methods
```

例如：

```text
battery_above(drone, threshold)
connected(drone)
airborne(drone)
position_known(drone)
```

Operator：

```text
connect
takeoff
move
land
```

Method：

```text
inspect_area
return_home
```

如果为了无人机增加：

```python
if domain == "drone":
```

到 HTN core：

```text
验收失败。
```

---

# 66. 故障验收

必须覆盖：

```text
Planner 输出 malformed
引用不存在 Fact
引用未给 Planner 的 Fact
选已 RETIRED Method
Method 前提变 UNKNOWN
Method 前提变 CONFLICT
Method version 更新
旧 Decision 晚到
PlanRevision 并发变化
Repair 同时发生
共享 Acceptance 被撤回
Operation UNKNOWN
Provider timeout
Planner unavailable
```

---

# 67. 性质测试

推荐 Hypothesis stateful：

操作序列：

```text
add method
suspend method
observe fact
retract fact
select method
refine
accept result
reject result
retire method use
repair
restart
```

不变量：

```text
无权限扩大
无预算凭空产生
无旧 generation 派发
无 stale Acceptance 新用
无 UNKNOWN operation 重复执行
无 dangling occurrence
无 TaskNetwork execution cycle
```

---

# 68. 迁移实施阶段

## Phase H0：冻结当前基线

记录：

```text
HEAD
schema
full_target test count
legacy regression
real-model scenarios
```

不改功能。

## Phase H1：PlanningDecision Protocol

实现：

```text
contracts/planning_decisions.py
decision_codec.py
decision_admission.py
legacy adapters
prompt v1
```

验收：

```text
旧 PlanProposal 行为不变
新 Envelope round-trip
系统字段无法由模型设置
unknown refs reject
```

## Phase H2：PlannerPackage V1

统一：

```text
goal
methods
facts
accepted
failures
capabilities
budget
visible_refs
```

删除 Planner 各处分散拼包逻辑。

注意：旧 prompt pin 保留。

## Phase H3：Method Selection 改为 LLM-native

改变：

```text
多个 applicable method
```

默认：

```text
ask Planner
```

而不是 `selectable[0]`。

唯一 Method 可以 deterministic fast path。

## Phase H4：RepairDecision

实现：

```text
contracts/repair_decisions.py
repair/classification.py
repair/impact.py
repair/compiler.py
```

接：

```text
Worker fail
Verifier reject
Evidence invalidate
root review reject
runtime unavailable
```

## Phase H5：DomainPackage 标准化

实现：

```text
PlanningDomainPackage
PredicateObserver registry
Operator registry
domain install validation
```

迁移：

```text
code
appworld
```

禁止 domain branch in core。

## Phase H6：Method Lifecycle

补：

```text
MethodEvaluationRecord
PromotionPolicy
EVALUATED
ADMITTED
SUSPENDED
```

默认：

```text
offline promotion
```

## Phase H7：PlanningBackendPort

把现有 PANDA 接成：

```text
PlanningBackend
```

新增：

```text
llm-native backend descriptor
```

UP-Aries：

```text
先作为 optional adapter
```

## Phase H8：跨领域验收

运行：

```text
code
appworld
drone
```

核心源码 domain branch count：

```text
0
```

---

# 69. 每个 Phase 必须交付的东西

每一阶段：

```text
1. code
2. contract tests
3. negative tests
4. state/recovery tests
5. backward compatibility
6. design journal
7. independent review
8. exact acceptance report
```

不能：

```text
“类型定义好了”
→ 宣布完成
```

---

# 70. 代码审查必查项

Reviewer 必须检查：

```text
是否增加了新的 authority？
是否让 LLM 决定系统字段？
是否把 UNKNOWN 当 FALSE？
是否引入 domain hardcode？
是否把 Solver result 当 Acceptance？
是否出现 second TaskNetwork truth？
是否重置 Obligation？
是否绕过 Commit？
是否在 write transaction 调模型？
是否用文本相似度决定 Operation identity？
```

---

# 71. 性能原则

不要提前优化 Planner 内部算法。

先优化：

```text
PlannerPackage 大小
Method library 检索
Evidence query
TaskNetwork incremental read
Prompt cache
Model routing
```

大模型推理成本很可能高于纯 Python Method 查找。

---

# 72. Method Library 检索

Method 很多以后：

```text
GoalSignature exact match
→ scope / domain
→ applicability cheap check
→ top candidate subset
→ Planner
```

不要：

```text
把全库 10 万 Method 全塞给模型。
```

---

# 73. Method 检索不是 Method 选择

Retrieval：

```text
哪些 Method 值得给模型看？
```

Selection：

```text
模型决定采用哪个。
```

两者分开。

---

# 74. PlanningBudgetView

不要把固定候选数写进核心协议。

```python
@dataclass(frozen=True, slots=True)
class PlanningBudgetView:
    max_method_candidates: int
    max_new_method_steps: int
    max_repair_actions: int
    max_planning_turns: int
```

---

# 75. 长上下文也不能取消结构化引用

即使模型能读 1M / 10M context：

```text
Fact ID
Acceptance ID
Method Ref
Operation Ref
```

仍然必须存在。

因为它们用于：

```text
Commit
Replay
Impact
Validity
Audit
```

不是为了省 token。

---

# 76. 不把 model confidence 变成权限

Planner：

```json
{"confidence": 0.99}
```

不能：

```text
→ 自动执行生产动作
```

如保留 confidence，只作为 telemetry。

---

# 77. Explainability 最小标准

每个 PlanningDecision 可解释为：

```text
做了什么
针对哪个责任
采用哪个 Method
依赖哪些 formal refs
有哪些明确假设
什么变化应触发 replan
```

不保存模型完整 CoT。

---

# 78. Restart / Replay

恢复时读取：

```text
PlanRevision
TaskNetwork
Method instances
Evidence
Acceptance
Operation facts
```

不要求恢复：

```text
模型当时未输出的内部搜索树。
```

这是 LLM-native 架构的重要优点。

---

# 79. 成功标准

这项 HTN 演进完成时，应满足：

```text
[ ] Planner 可以换模型而不改 HTN Core
[ ] Planner 可以内部使用任何搜索思想
[ ] 所有正式输出都走 PlanningDecision
[ ] MethodContract 已稳定
[ ] 新 Domain 通过注册接入
[ ] Planner 不可伪造 authority
[ ] UNKNOWN 前提不会被执行
[ ] Repair 不重置 Obligation
[ ] Operation UNKNOWN 不因换 Method 被重复
[ ] Method 可 trial / evaluate / promote
[ ] PANDA / Aries 可作为 backend
[ ] Solver 不成为业务状态权威
[ ] Root completion 仍由 Acceptance / GoalResolution
[ ] 三个不同 Domain 不修改 HTN Core
```

---

# 80. 明确不作为本计划完成条件的内容

```text
实现通用 A*
实现通用 MCTS
复制 Aries
复制 PANDA
完整 PDDL 支持
完整 temporal planning
训练 learned planner
在线 RL
全部世界状态形式化
```

以后有真实需求再做 Backend。

---

# 81. 实施 Agent 的直接工作指令

```text
你正在实现 SimpleHarness 的 LLM-Native HTN Core。

目标不是开发一个新的传统 HTN solver，而是：

1. 固定模型与系统之间的 PlanningDecision 协议；
2. 收口 MethodContract；
3. 标准化 PlanningWorld / Predicate / Observer / Operator；
4. 让 LLM 负责 Method 选择和高阶规划；
5. 让程序确定性验证所有正式状态修改；
6. 建立统一 RepairDecision；
7. 完成 Method lifecycle；
8. 把 PANDA / Aries 等 solver 降为可插拔 backend；
9. 证明新增 Domain 不需要修改 HTN Core。

不得：
- 直接让模型写 PlanRevision；
- 直接让模型生成系统 Authority；
- 把 UNKNOWN 当 FALSE；
- 把 Planner 的 confidence 当权限；
- 新建第二个 TaskNetwork 状态权威；
- 新建第二个 Budget / Operation ledger；
- 在数据库事务中调用 LLM；
- 为了新 Domain 在 HTN core 添加 if domain 分支；
- 把 Solver SOLVED 当成 Task Acceptance；
- 把 Repair 当成“整图重建并丢掉历史”。

每个改动必须：
Proposal → strict codec → deterministic admission → compiler → Commit。
```

---

# 82. 最终心智模型

```text
              ┌─────────────────────────────┐
              │            LLM              │
              │ 理解 / 推演 / 比较 / 搜索   │
              └──────────────┬──────────────┘
                             │
                    PlanningDecision
                             │
                             ▼
              ┌─────────────────────────────┐
              │       SimpleHarness         │
              │                             │
              │ Contract / Evidence         │
              │ Method / TaskNetwork        │
              │ ORDER / DATA                │
              │ Budget / Authority          │
              │ Commit / Acceptance         │
              │ Recovery                    │
              └──────────────┬──────────────┘
                             │
                             ▼
                      真实 Agent / Tool
                             │
                             ▼
                    新事实 / 新结果 / 失败
                             │
                             └──────► LLM
```

一句话：

> **模型越来越聪明，SimpleHarness 不跟模型竞争“思考能力”；SimpleHarness 负责让模型的思考能够被安全、稳定、标准化地转化成长期可执行的系统行为。**

---

# 83. 建议立即开始的第一项

第一项只做：

```text
PlanningDecision Protocol V1
```

不要同时重写 Method、Repair、Backend。

交付范围：

```text
PlanningDecisionEnvelopeV1
PlanningDecisionType
PlanningSubject
ReasonRefs
AssumptionHint
RefineDecision
EvidenceRequestDecision
Decision codec
Decision admission
legacy PlanProposal adapter
legacy MethodProposal adapter
Planner prompt v1
negative tests
```

完成并通过后，再进入：

```text
MethodContract finalization
```

这样可以保持小步、可测、可回滚。
