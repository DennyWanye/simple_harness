# SimpleHarness：目标验收、条件证据与执行恢复——代码级实施计划

**版本：CORE-ASSURANCE-1.0 · 2026-09-16 · 面向实施 Agent**  
**核查基线：`DennyWanye/simple-harness-sdk@873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d`**

> 目标不是新建一个 MVP，也不是重新开发 BaseAgent。本专项把“什么算完成”“什么证据现在可依赖”“什么操作已经发生”连成一个完整协议。保留最新完整目标的 60 项要求、P1—P9、TaskGraph/HTN 的术语和责任边界。本专项编号仅用于组织施工，不能覆盖或重编号总需求。
>
> 本文是实施规格，新增文件/类型/表/方法均为拟实施内容，不是已合入补丁。本轮定向读取了真实源码；没有执行 SimpleHarness、真实 Provider、Host UI 或完整迁移测试。附带的纯算法与本地 SQLite 故障参考测试不等于生产集成通过。

## 阅读与执行顺序

实施 Agent 先读 §§0–4 的不可变裁决，再按 §§5–8 实现验收链、§§9–12 实现证据链、§§13–18 实现执行恢复链。§§19–24 给出存储、主链接线、工作包、测试、迁移和交付门槛。附录包含可运行纯算法、参考故障服务、需求映射和固定来源。

---

## 0. 给实施 Agent 的工作指令

1. **先确认真实 checkout。**记录 SDK/Host 的 `git rev-parse HEAD`、dirty 状态、Python/SQLite 版本和当前迁移描述符；不要擅自 reset 用户未提交修改。本文固定 commit 用于复核，不要求把用户工作树回退。
2. 阅读同目录/用户提供的最新版 `simpleharness-complete-target-and-gap-plan-2026-09-16.zh-CN.md`、`simpleharness-taskgraph-implementation-design.zh-CN.md`、关键契约加固计划。旧 W1–W7/GoalNode 草案不能覆盖新版 Task/Obligation/GoalResolution 语义。[D1–D4]
3. 查是否已有等价类型/表/函数。若 HTN/TaskGraph 工作已落地，直接复用；只允许在一个位置拥有 TaskSemanticBinding、RequirementsRevision、InputManifest、Operation 身份。建立 `implementation/source-map.json` 说明复用/新增/兼容关系。
4. 只通过现有 `CommitService`、`Store.transaction()`、SDK 的 Provider/Effect 账本提交。不得创建第二个调度内核、预算账本或 Agent 状态库。
5. 所有 LLM 请求——包括要求细化、Verifier、复核与总结——使用已有 dispatch intent + BaseAgent 路径；新纯函数不得直接调用 Provider。
6. 新业务语义只在显式绑定 `orchestration_semantics_version=full-target-v1` 的 Mission 上启用。旧模式继续原 codec、hash、状态和评审规则。不得回写旧请求字节、重算旧幂等 ID 或批量“升级”旧 PASS。
7. 实现完整行为，不把 `pass`、固定 PASS、`get(..., True)`、仅定义 Protocol、未部署 handler、只跑 fixture 当成完工。协议端口可以是 Protocol；每个本专项必需端口必须有实际装配实现和主链测试。
8. 拒绝隐式转换：外部输入先以 `object` 接收；严格 codec 后成为业务类型。缺失/null、布尔/整数、陌生枚举、空成功公式必须明确处理。
9. 保留用户约定：Python 核心；Workflow 在工具层；不接入用户长期 Memory；独立 Verifier 处理开放语义；程序不能被 LLM 的 PASS 绕过。
10. 不修改生产资料或调用收费模型做未授权的“试运行”。真实试验通过已有环境、测试预算和人工门；本地机械测试不需要生产凭证。
11. 不扩建 CI/发布流水线。新增测试和类型检查加入现有质量入口即可；它们是正确性验收，不是本轮发布工程。
12. 任一步不能闭合时，报告具体失败、被阻塞接口和风险，不把必需能力改成“未来优化”。Host 不可读仅阻塞其真实接线，不阻止 SDK 侧明确范围的实施。

### 0.1 必须形成的最终交付

- 一条真实的 `RequirementsRevision → ReviewPackage → ReviewRecord → Acceptance/GoalResolution → MissionFinalized` 链。
- 一条真实的 `Evidence/Fact → Justification → 当前有效性 → HTN/TaskGraph/Context/Acceptance 准入` 链。
- 一条真实的 `Operation occurrence → 精确授权 → 持久 handoff → 外部事实 → reconciliation → 费用/结果/通知` 链。
- 三条链在证据变化、取消、并发、迟到结果、崩溃、旧任务读取下相互一致。
- 新增完整专项测试报告、旧模式累计回归、需求映射、实际 UI 证据。单项原需求只有在其全部上下游要求满足时才能关闭。

---

## 1. 依据、当前代码与核查边界

### 1.1 依据优先级

| 标识 | 依据 | 本专项如何使用 |
|---|---|---|
| D1 | 原始《Agent 编排层完整设计方案》§5/11/14–18/20–25 | 根要求、Claim/Knowledge、独立验证、单写者、预算和恢复底线 |
| D2 | 2026-09-16 完整目标计划，60 条 R01–R60 | 主术语、当前性、Operation、完整业务重建、范围排除 |
| D3 | TaskGraph implementation design TG-DESIGN-1.0 | compound/primitive、ORDER/DATA、输入 manifest、版本/read-set、共享 demand |
| D4 | Mission 关键契约加固计划 | TaskCritic/MissionJudge、明确等待与预算用途、Host 共享 schema |
| S01–S12 | 本次固定源码 | 现有接线和必须保留的行为，不把注释当测试证明 |
| W01–W07 | 一手技术资料 | 解释理由维护、Outbox、幂等、SQLite、补偿和状态测试；不替代本项目验证 |

本文补足的算法、字段、表和状态转换属于**本专项的明确工程决定**，不是宣称原文已逐项规定这些实现细节。若同一对象在 D2/D3 已定义，以其唯一合同扩展，不创建同名第二份。

### 1.2 本轮读取到的实际基础

| 来源 | 已有事实 | 实施决定 |
|---|---|---|
| S01 `orchestrator/commit_service.py` | 单一逻辑 writer；接入 action/source/mission-tail 等 mixin；引用 AssessmentBinding/mission_coverage | 新语义用 mixin/内部服务接入，继续同一个 CommitService |
| S02 `verification/assessments.py` | 原任务/根准则 catalog、冻结 AssessmentBinding、产物/来源/intent 核对 | 不重写旧 binding hash；新 ReviewPackage 增加精确语义投影 |
| S03 `verification/critics.py` | 独立 BaseAgent；实时输出严格解析；历史 `from_json` 有兼容默认 | 保留历史解析；新增 v2 codec，不能把历史默认值当新输入可信值 |
| S04 `verification/mission_coverage.py` | 文档域根据已接受评估与原始 catalog 计算覆盖 | 新模式通用成功公式不得继续用文档字面匹配代替目标满足 |
| S05 `memory/source_dependencies.py` | 历史血缘与当前来源 fence 分开；有环检测与共享菱形依赖 | 扩展成通用支持网络，不删除已有文档回读/来源核对 |
| S06 `orchestrator/action_commits.py` | 动作/审批在同一 writer；业务 action ID 基于 Mission+connector+operation+target | 保留 legacy 身份；v2 增加合法 operation occurrence，避免重复意图与新意图混淆 |
| S07 `runtime/actions.py` | begin_handoff 先持久化，再在线程调用；超时 UNKNOWN；可核对，受控再交接 | 沿此主链增加 typed lookup、代次、迟到观察和恢复策略 |
| S08 `runtime/connectors.py` | 真实 Connector 协议、Receipt、authoritative/best_effort、测试服务 | 保留旧 adapter；v2 增加明确去重保存期、查询覆盖/终局性和 fencing 能力 |
| S09 `storage/store.py` | BEGIN IMMEDIATE、WAL/FULL、CAS、fault hooks、DispatchIntent | 使用原事务；不把抛异常等同于真实 kill；追加独立进程故障测试 |
| S10 `observability/replay.py` | replay-v2 只重建选定字段，明确有 not_covered | 新模块事件提供完整本专项投影，不能扩大旧历史的保证 |
| S11 Provider ledger 文档 | 唯一 ProviderInvocationCoordinator；claimed/handed_off/unknown；冻结身份与预留 | 不替换执行账本，不盲目重发 UNKNOWN，不把未知费用计零 |
| S12 最新 commit 交接 | 新增 Host A96 基线指针；自述仍使用旧 SDK 源码，原始结果在本机 | 作为负控动机，不把未复验数据当本轮测试结论 |

本轮 SDK main：`873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d`。它的新 commit 是文档交接更新；本轮同时读取了上述关键源码，而非只读交接说明。[S12]

Host `DennyWanye/simple_harness` 在本轮连接返回 404。不能推断 UI 不存在；H0 必须记录实际投影、路由、Store、审批页及原生测试路径。Service 是否介入 Mission 链路以实际装配为准，不因仓库存在而强行引入。

### 1.3 禁止用重构掩盖范围变化

保留全部 R01–R60。本专项主要细化 R03/R06/R12/R16/R17/R25/R28–R30/R36–R41/R44/R57，并对 R34/R45–R50/R58/R60 提供接口与相应覆盖；不声称凭本专项关闭所有这些复合要求。HTN refiner、完整平台执行、全历史检索和学习器继续按原工作包交付。

---

## 2. 先固定三种不同的“完成”

| 对象/状态 | 代表什么 | 不代表什么 |
|---|---|---|
| AgentTurn COMMITTED | 该 Agent 本轮输出已可靠保存 | Task 被验收；主 Agent 一生结束 |
| ReviewRecord 生成成功 | 独立审阅正常完成 | 审阅结论一定 ACCEPT |
| Acceptance | 某份绑定输入/产物在指定要求下被接受的历史事实 | 永远正确；自动允许修改真实系统 |
| GoalResolution | 指定 Task/Obligation 按某个被采用方法满足目标的历史决定 | 当前来源、权限永远不变 |
| 当前 validity | 对特定用途/时刻，历史结果是否仍能使用 | 改写历史 Task.COMPLETED |
| Action SUCCEEDED / 外部 APPLIED | 对应业务操作确有匹配回执 | 当前目标状态仍是那个值；整个 Mission 满足 |
| Mission COMPLETED | 根 Resolution 满足、危险在途工作已收敛且收尾合同满足 | 客户端一定已读到通知；长期 Commitment 关闭 |

**明确裁决：**旧 Task/Mission 的终态不回退。来源或要求变化，改变 `AcceptanceValidity/GoalResolutionValidity`，按责任与授权创建后继工作或新周期。缺陷修复不能把过去的事实删除。

### 2.1 使用同一份身份和版本

- `TaskId`：工作 occurrence；`Task.form` 为 compound/primitive，`kind` 仍是 work/conflict/synthesis 等用途。
- `ObligationId`：稳定责任；换方法、Task、Agent 不重置内容尝试和累计支出。
- `RequirementsRevisionId`：不可变用户要求版本；不能由 Planner/Verifier自由改写。
- `contract_hash`：工作语义；`record_version`：CAS；`dispatch_generation`：未来执行资格；`plan_revision`：当前采用结构；`validity_revision`：当前支持状态。不得混用。
- `OperationId`：一次明确业务意图的 occurrence；`operation_spec_revision`：这一意图的尚未执行候选参数版本；`handoff_no`：同版本的物理交接记录；外部 idempotency key 在重试中保持不变。
- `InputManifest`：只列 DATA 实际允许输入的具体 Result/Acceptance/Artifact/hash，不继承全部 ORDER 祖先文件。

采用已有名义 ID 类型，缺少时使用 `NewType`，不是 `TaskId = str` 别名。名义类型只能发现误传；tenant/Mission/账户/原任务归属仍需在事务中核查。

---

## 3. 总体架构与唯一权威

```text
认证用户要求 ──> RequirementsRevision ──> HTN / TaskNetwork
                                      │
真实工具/检查 ──> Evidence/Fact ──> ValiditySnapshot
                                      │
已冻结候选 ──> ReviewPackage ──> 独立 BaseAgent / 明确检查
                                      │
                                  ReviewRecord
                                      │
                          Acceptance / GoalResolution Commit
                                      │
已授权 Operation ──> 原 action ledger ──> ActionExecutor ──> 外部系统
                                      │                         │
                                      └──── 真实Receipt/核对 ────┘
                                      │
                          Finalization + 通知Outbox
```

新模块均为同一模块化单体中的职责，不是微服务。业务 writer 仍是 CommitService。Provider/Effect 最终调用与费用事实仍由 execution.db 承担。条件求值、成功公式求值、影响分析都是纯函数；模型与外部网络不在数据库写事务中运行。

### 3.1 必须一直成立的跨模块不变量

1. `Reviewer ACCEPT` 不覆盖任何必需的 FAIL/UNKNOWN/未运行检查。
2. 不同参数、产物或要求的 Review 不混用；无关心跳/分支事件不使全部 Review 失效。
3. 必需风险/隐私/权限约束放在成功 OR 公式之外，不允许选另一路来绕过。
4. 原始证据的字节完整性、来源真实性、语义支持、当前适用性、披露权限分别判断。
5. 任务网络无环不证明内容正确；支持网络有链接不证明命题蕴含；多个模型同意不产生外部事实。
6. `HANDOFF` 持久化表示“调用可能已经发出”，不是操作已经成功。
7. Lease 失效、协程取消、网络超时均不自动证明外部操作未发生。
8. 旧执行者可提交可核验的真实结果/费用，但不能借此授权新动作或批准当前新合同。
9. 事件+本库投影+本库Outbox+本库回执一并提交；跨库依靠稳定身份和幂等，不宣称分布式 exactly-once。
10. 本库回执查重先于“Mission已终态”的业务拒绝；但认证和当前读权限始终先检查。
11. 恢复先禁新副作用，导入已发生事实并核对；不会重放历史审批来复活权限。
12. 摘要、检索、UI 与 Planner 均不是状态权威。

---

## 4. 模块布局与实施接口

以下 `[新增]` 路径如已由 TaskGraph/HTN 工作创建，扩展原文件而不是平行再建。

```text
src/agent_orchestrator/
  contracts/
    requirements.py          [新增：要求、准则、成功公式]
    reviews.py               [新增：ReviewPackage/Record/Subject]
    resolution.py            [复用总计划：Acceptance/GoalResolution]
    evidence_state.py        [复用总计划：Fact/Justification/Validity]
    operations.py            [新增：Operation occurrence/lookup/observations]
  verification/
    requirements.py          [新增：准则编译与覆盖检查]
    review_packages.py      [新增：精确审阅包构造]
    review_codec.py          [新增：模型响应v2严格解析]
    acceptance.py            [新增：纯准入判断]
    assessments.py          [已有：兼容适配，不改旧hash]
    critics.py              [已有：legacy保留]
    verifier_router.py      [已有：真实检查链与v2选择]
    mission_coverage.py     [已有：旧文档域保留]
  memory/
    validity/
      evaluator.py          [新增：带符号的有界证据求值]
      repository.py         [新增/Store扩展：关系查询，不自行开写事务]
      invalidation.py       [新增：反向影响、dirty barrier]
      policy.py             [新增：freshness/assurance/purpose]
    source_dependencies.py [已有：通用网络的文档来源适配]
  orchestrator/
    resolution_commits.py  [总计划新增位置：唯一接受/完成入口]
    evidence_commits.py    [新增：证据、支持、撤回与当前性]
    operation_commits.py   [新增：v2语义；内部复用ActionCommitsMixin]
    closeout.py            [新增：根完成与收敛判断]
    commit_service.py      [已有：装配上述职责]
    event_handler.py       [已有：接线，不继续堆所有算法]
  runtime/
    connector_v2.py        [新增：版本化Connector协议与legacy适配]
    actions.py             [已有：唯一真实connector调用者]
    agent_worker.py        [已有：结果/观察错误与不存在分开]
    recovery.py            [新增或扩展现有恢复服务：不并行创建第二个pump]
  storage/
    assurance_schema.py    [新增：不可变DDL块，导入原schema.py]
  observability/
    assurance_replay.py    [新增：本专项完整事件投影]
```

业务端口建议为 `RequirementsReadPort`、`PlanningReadPort`、`EvidenceReadPort`、`ReviewExecutionPort`、`OperationFactPort`，每个读端口仅给出明确快照。持久方法通过同一 Store adapter；禁止服务绕过 CommitService 在构造函数或 getter 中写库。

**总计划的完整业务 reducer若已实现：把本专项事件注册进去，不另养一个事实源。**这里的 assurance_replay 是其有明确 coverage 的子投影/测试入口。

---

# 第一部分：目标验收与完成语义

## 5. RequirementsRevision：成功条件由什么构成

### 5.1 固定数据模型

在 `contracts/requirements.py` 定义下列不可变 dataclass，codec 使用现有严格 JSON / canonical hash 工具。字段不得由 `.get(..., True)` 补出“已满足”。

| 类型 | 必填字段 | 规则 |
|---|---|---|
| `RequirementsRevision` | tenant_id, mission_id, revision_id, parent_revision_id, source_message_refs, goal_text, criteria, success_expr, mandatory_constraints, authority_receipt_ref, schema_version, content_hash | 原始文本与解释结果并存；用户明确修改才生成后继；旧字节不变 |
| `CriterionSpec` | criterion_id, statement, origin, target_scope, phase, evaluation_mode, required_check_sets, assurance_policy_ref | origin=USER_EXPLICIT/USER_CONFIRMED/SYSTEM_DERIVED；scope锁定对象与输入；不得由Worker改准则 |
| `CheckRequirement` | check_spec_id, adapter_id/version, target_binding_ref, assertion_ref, required_outcomes, scope | 模型可以建议检查方案，部署能力和最终可接受规则由系统准入 |
| `SuccessExpr` | `criterion(id)` / `all_of(children)` / `any_of(children)` | 只用正向有限AST，所有引用必须存在；不允许任意代码/eval/SQL |
| `RequirementCoverage` | criterion_id → child_slot / check_spec / composition_review | 这是覆盖提案，不是成功回执 |

**表达能力边界明确：**复杂“不能泄密”不是 `not(unknown)`，而是独立、必须通过的约束准则。量词的对象集合由冻结输入或注册 checker 定义。未知全集不能用检查少数样本代替“所有”。现有 `success_criteria: tuple[str]` 的兼容映射按旧解释保留，不能猜文本中的“或”并静默重写历史逻辑。

### 5.2 谁可以修改

- 用户/获准 Host 可提出 `ReviseRequirements`，带旧 revision 和新原文；记录来源事件、变化项、影响范围与授权回执。
- Planner 只能提出解释、细化标准与覆盖映射。明确硬约束不得弱化；有歧义且会影响验收/副作用时停在澄清。
- 系统推导检查可用于约束实现，但不得改写用户意图。非关键可逆规划假设可以显式记录为 Assumption，最终报告局限；不能据此扩大外部权限。
- “人工允许交付不完整报告”应成为新的明确要求或声明的部分交付结果，不给旧失败检查补 PASS。安全硬门不能用普通内容审批覆盖。

### 5.3 成功公式与强制门必须分开

假设用户允许“实测报告或模拟报告”，但无论选哪条都必须隐私合规：

```json
{
  "success_expr": {
    "any_of": [
      {"all_of": [{"criterion": "real_measurement"}, {"criterion": "real_report"}]},
      {"all_of": [{"criterion": "simulation"}, {"criterion": "simulation_report"}]}
    ]
  },
  "mandatory_constraints": ["privacy", "source_integrity", "independent_review"]
}
```

一条没选的方法失败不否定另一条；但任一强制门未过，不得接受根目标。实现用三值 `PASS / FAIL / UNKNOWN`：

| 组合 | 判定 |
|---|---|
| ALL | 任一 FAIL→FAIL；全部 PASS→PASS；否则 UNKNOWN |
| ANY | 任一 PASS→PASS；全部 FAIL→FAIL；否则 UNKNOWN |
| 没读到评估 | UNKNOWN，不是FAIL也不是PASS |
| 空 ALL/ANY、引用不存在 | 合同错误，不能利用空集合真值直接完成 |

该三值是**目标验收代数**。不要和知识的 TRUE/FALSE/UNKNOWN/CONFLICT 混用；遇知识 CONFLICT 的强制准则应交给审阅/冲突处理，不能自动当“False已证明”。

`contracts/requirements.py` 的解析需检查：节点数/深度上限、非空子项、重复准则 ID、无悬空引用、所有原始硬要求在mandatory或公式覆盖；限制来自版本化部署政策。达到限制是明确拒绝或要求再分解，不截断后继续。

---

## 6. ReviewPackage 与独立审阅的实际执行

### 6.1 四类 subject，用判别联合而非全是可选字符串

```python
# 拟新增结构片段；复用项目已有名义ID，全部使用kw_only。
@dataclass(frozen=True, slots=True, kw_only=True)
class TaskReviewSubject:
    kind: Literal["task_candidate"]
    mission_id: MissionId
    task_id: TaskId
    attempt_id: AttemptId
    result_id: ResultId
    input_manifest_hash: ContentHash
    output_manifest_hash: ContentHash
    task_contract_hash: ContentHash

@dataclass(frozen=True, slots=True, kw_only=True)
class CompositionReviewSubject:
    kind: Literal["composition"]
    mission_id: MissionId
    parent_task_id: TaskId
    method_instance_id: MethodInstanceId
    composition_spec_hash: ContentHash
    child_acceptance_refs: tuple[AcceptanceRef, ...]
    output_manifest_hash: ContentHash

@dataclass(frozen=True, slots=True, kw_only=True)
class MissionReviewSubject:
    kind: Literal["mission_final"]
    mission_id: MissionId
    root_task_id: TaskId
    obligation_id: ObligationId
    verification_view_id: VerificationViewId
    requirements_revision_id: RequirementsRevisionId

@dataclass(frozen=True, slots=True, kw_only=True)
class OperationOutcomeReviewSubject:
    kind: Literal["operation_outcome"]
    mission_id: MissionId
    operation_id: OperationId
    operation_spec_hash: ContentHash
    receipt_ref: ExecutionReceiptRef
```

Composition/Mission subject **不伪造 Worker AttemptId**。审阅本身有真实 BaseAgent/AgentTurn/dispatch intent 和费用归属；复用关键契约加固的 TaskCritic 与 MissionJudge预算路线，补独立的组合审阅账户绑定。不能把 VerificationViewId 塞入需要 AttemptId 的参数。

### 6.2 ReviewPackage 完整字段

| 分组 | 内容 |
|---|---|
| 身份 | schema_version、package_id/hash、tenant/Mission、subject、purpose；review_job单独绑定此包 |
| 要求 | RequirementsRevision精确引用、Task合同、准则catalog、成功公式、mandatory约束 |
| 输入 | InputManifest、输出manifest、每个Artifact ID/hash/类型、Method参数与组合义务 |
| 依据 | 允许使用的Evidence catalogue、来源版本、child Acceptance、反证/争议、既有缺陷 |
| 操作 | 本次阶段已发生的操作回执、待执行的候选明确标为未发生，不做事实证据 |
| 权限 | 只读验证视图、可用检查工具、获准外部只读范围、审阅预算；秘密不入Context |
| 版本 | model/prompt/checker/env/config fingerprints、task dispatch generation、语义read-set |
| 覆盖 | 审阅应覆盖的全部准则和可分页证据清单；Context只装部分时不能声称全部已看 |

`package_hash` 复用既有 canonical JSON 规则。它是完整性标识，不是签名；真正的回执权威来自被注册的执行链和身份核对。[S02]

### 6.2a Hash 与执行身份的精确定义

包的规范payload包含：schema版本、tenant/Mission、subject、purpose、要求/catalog/formula、精确输入输出manifest、evidence catalogue与read-set、准则/check政策、可披露scope、工具/环境/模板版本。`package_hash = sha256(canonical_json(payload))`；**payload不包含自身hash、package_id、创建时间、review_job_id或瞬时lease**。这些是封装/执行字段，避免自引用hash和相同材料每次随机变身份。

`package_id`由系统从package_hash及既定namespace派生。一个包可以被多个合法独立ReviewJob引用：job拥有自己的命令身份、Reviewer角色/执行配置、预算账户和真实dispatch intent。重试同一job使用原input_id；确实请求第二次独立审阅时由新合法命令创建新job，不能只换模型偷改旧包。

`ReviewRecord.body_hash`包括该job和真实执行源、原始输出hash、解析结果及所用证据。`Acceptance.acceptance_hash`绑定具体ReviewRecord、subject、输入输出/要求、policy和支持见证；不同独立Review可能形成不同不可变接受依据。Task/Goal最终采用关系另外受唯一逻辑槽与CAS保护，不因多份Review多次触发下游。

### 6.3 包构造算法：`build_review_package()`

1. 读事务中取得精确 Task/Result/Attempt、要求、Method/Composition、输入和产物引用、来源元数据、语义read-set。输出不可变 draft，不读取实时 `latest` 作为冻结输入。
2. 事务外经 ArtifactStore/read_verified 读取所需字节，核验hash，建立独立验证工作区；登记pin/保留引用，防止GC在评审期间删掉唯一字节。
3. 收集现有实际 checker 结果；每个 `CheckReceipt` 必须绑定 check_spec、目标hash、环境、执行来源及验收范围。只存在测试文件不产生回执。
4. 构造受控审阅包和模型输入。Worker 自述放在“待核验说明”而不是事实区；既不能让它主导结论，也不必完全丢弃实现线索。
5. 短事务再次核对 draft read-set、验证视图状态与pins，保存包、review job、dispatch intent、预算reservation及事件。已经存在的相同job返回原身份；相同command不同包hash报冲突。
6. 由已有AgentBridge执行。模型检查工具仍走Tool Gateway；任何测试产生新证据必须经实际回执入库。
7. 记录有限格式修复次数，与内容返工分开计数；格式修复依然计费，不降低准则。重启复用冻结包和原调用，不重新检索生成另一个包。

包创建和最终执行之间权限仍可能变化；Provider披露和工具handoff都重查当前权限。已发出的请求无法召回，按已发生披露审计，不以改package掩盖。

### 6.4 模型输出必须有限且严格

模型可输出的payload仅包含：

```json
{
  "schema_version": 2,
  "decision": "REWORK",
  "assessments": [
    {
      "criterion_id": "C-linux-compatibility",
      "verdict": "FAIL",
      "evidence_ids": ["E-build-log"],
      "reason": "在指定工具链下构建失败，错误见该回执。",
      "limitations": []
    }
  ],
  "findings": [{"criterion_id":"C-linux-compatibility","severity":"blocker","reason":"构建失败"}]
}
```

该例仅展示格式；真实消息必须覆盖包中本轮要求审阅的全部准则。不得由模型填写 tenant、checker已运行、approval、预算、receipt_hash、最终接受ID。

`parse_review_v2(raw: object, package)` 要拒绝：未知字段、重复准则、遗漏准则、外来或包外 evidence ID、伪造格式version、错误类型、ACCEPT同时含blocker、ACCEPT时mandatory不PASS、没有说明的UNKNOWN。上述检查不等于语义正确；实际内容仍由独立审阅和必要工具证据判断。

保留 `CriticVerdict.from_json()` 给旧持久记录；不将它作为新模型输出的通用宽松入口。[S03]

### 6.5 实际 checker 与通用 Verifier 的分工

- `format/rule/identity`：程序可直接拒绝无效数据，不为确定错误再花模型费用。
- 代码：现有code_test真实运行，回执绑定环境/输入/产物/check spec；独立审阅看到结果，不能凭测试路径把任意自然语言主张标成事实。
- 文档：复用citation_integrity/SourceResolver；区分“来源写了X”和“现实X被验证”。
- 操作：使用实际Operation回执；“页面出现成功文案”不一定是权威操作证据。
- Formal：准则要求形式证明时，绑定原始命题身份、工具链/依赖、公理政策和实际检查结果；没有适配器就BLOCKED/INCONCLUSIVE，不以模型PASS代替。此专项定义并连接该统一回执，不能把R47的具体Lean适配未完成部分标完成。
- SQL/实验等：同一 `CheckReceipt` 协议由对应实际adapter提供。新枚举不代表新能力部署；按原P6保留它们的完整交付项。

独立不要求永远换模型；要求审阅者身份、可写工作区、输入来源和职责与作者分开。关键风险可配置异构模型，但投票不能覆盖外部事实。

---

## 7. Acceptance/GoalResolution 的确定性准入

### 7.1 Receipt 的明确字段

`CheckReceipt`：receipt_id、check_spec_id/version、subject_binding_hash、input/output manifest hash、adapter/environment、outcome(PASS/FAIL/UNKNOWN/ERROR)、evidence_refs、observed_at、scope、limitations、producer_execution_ref、body_hash。

`ReviewRecord`：record_id、package_hash、真实review_job/agent/turn/invocation_refs、模型原始输出artifact_ref、parsed decision、criterion assessments、消费过的check_receipts、结构校验结果、来源read-set。

`Acceptance`：acceptance_id、subject、requirements/contract、实际输入输出、ReviewRecord引用、已运行的必需check receipts、有效的support witness、accepted_at、command/commit/event引用。不可变。

`GoalResolution`：resolution_id、root或compound Task、Obligation、RequirementsRevision、所采用MethodInstance、每个必需slot绑定的child Acceptance/Resolution、composition spec/Review、成功公式witness、必要operation facts、validity_basis、原始结果引用。不可变。

**没有GoalNode新生命周期，没有“一个task表外的第二套可写完成状态”。**当前满足索引与Task状态由同一Commit/版本化reducer投影。

### 7.2 评估函数的顺序

`evaluate_acceptance(bundle, current_snapshot) -> Acceptable | Refused` 的执行顺序必须固定：

1. 核对所有身份、scope、subject type和实际执行来源；回执不能由客户端随意伪造。
2. 对输入/输出/要求/Method/采用关系做精确比较；无关全局plan revision改变不自动失败。
3. 查当前读权限、被接受用途、证据可读取性、来源/支持有效性、相关scope epoch。拒绝过期缓存。
4. 检查每个必需check set。多种合法检查方式之间可OR；每组必需检查内部AND。仅有实际适用PASS才满足，SKIPPED/ERROR不能算PASS。
5. 取得每项语义准则的独立Review；检查与该准则绑定的证据范围，不接受其他目标测试产生的伪覆盖。
6. 求值成功公式；检查全局mandatory。分数平均值和“多数准则通过”不得替代硬要求。
7. compound检查每个选中方法的必需occurrence、DATA端口与组合审阅；未选OR分支不阻塞内容满足，但其已发副作用仍需收敛。
8. 检查本次subject的操作阶段。如果要求的是已发送/已发布，必须有匹配的实际结果；预执行候选不能通过它。
9. 独立Review不是ACCEPT则拒绝正式接受；INCONCLUSIVE允许正常提交审阅结果，但不能假装已满足目标。
10. 返回带完整reason_codes和read-set的决定供Commit重新核对，不在此纯函数写库。

### 7.3 防止“先验收再执行，执行后才可验收”的循环

必须区分：

| 阶段 | 接受什么 | 不能接受什么 |
|---|---|---|
| `CANDIDATE` | 这份报告/补丁/操作提案符合内容和安全标准，可申请执行许可 | 报告已经发送、修改已经发布 |
| `OUTCOME` | 精确OperationVersion已发生，并有后置条件的证据 | 其他参数/目标也成功 |
| `COMPOSITION/FINAL` | 所选子成果+必要操作+根要求整体满足 | 仅因候选产物通过就关闭Mission |

例如“生成并发出报告”：报告候选先验收→绑定hash审批→执行发送→核对发送→最终审阅。发送不需要“最终已经完成”的证明才启动；但用户目标必须等发送证据。审批本身不是内容Review，也不是外部成功回执。

### 7.4 `commit_acceptance()` 的完整事务

下面是实施顺序，不是可直接替换SDK的现成函数。每一个helper要按本节规定落在Store查询/纯函数/现有服务上：

```text
认证principal并检查命令可读/可提交范围
BEGIN IMMEDIATE（复用Store.transaction）
  查command receipt；同hash→按当前权限返回；不同hash→冲突
  检查Mission采用的新语义模式与该subject的写入政策
  读取精确ReviewPackage/Record、真实checker receipt、subject当前绑定
  验证Reviewer来源与其职责，不给Worker自行批准自己的产物
  验证read-set与集合谓词；有无关变化可重新求值，不盲拒所有结果
  evaluate_acceptance(current_snapshot)
  检查当前主写者epoch、Task generation、实际费用和必要尾预算
  写Acceptance/GoalResolution不可变事件
  用同一reducer写当前接受索引、Task历史完成/当前Resolution关系
  写Validity dependency edges、下游就绪通知Outbox、command receipt
COMMIT
事务外推进Scheduler/通知，不直接在事务中派发模型或工具
```

回执和产物文件预先在CAS完成写入且被pin；DB事务核对元数据/identity，不长时间复制大目录。pin解除也须经过引用计数/GC策略，不能收回已提交事件唯一指向的字节。

### 7.5 双提交与迟到Review

- 两份同产物同标准Review均保留。重送同一接受命令/同一Review与绑定只产生一份Acceptance；合法独立Review可以形成不同接受依据，但Task采用槽和GoalResolution由CAS决定，不能重复发出Task完成/下游派发。新增独立支持不得改写旧Acceptance正文。
- 不同候选同时通过：保留各自历史Acceptance。采用哪个输出由既有selection/Method选择的CAS决定；单值DATA端口不能同时绑定两份不同候选。
- 要求/产物已变，旧Review到达：保存及结算旧审阅，标为历史/不可应用，不覆盖新目标。
- 新反证与接受并发：原子读取fact候选集合epoch；反证先提交则旧Review须重判；接受先提交则随后有效性更新阻止未来使用。两者都保留事件。
- 人工override必须标明覆盖范围、authority与具体要求版本；不得伪造已执行check、已发生外部效果或消除UNKNOWN。

---

## 8. Mission Finalization：接受与安全收尾分两步

新增或复用 `MissionCloseout` 投影，状态词用于**收尾投影**，不直接改写legacy MissionStatus：`NOT_READY / DRAINING / BLOCKED_UNKNOWN / READY / FINALIZED`。

`request_closeout()`：根Resolution满足后关闭新的无关候选派发，撤销不再采用工作的future generation，提出取消/收敛意图。继续允许旧结果、真实费用、核对与安全清理进入系统。

`try_finalize_mission()` 要在短事务确认：

- 根要求仍为当前被接受版本，所选Resolution对该用途有效；required pending operation均有相应终局事实。
- 不存在仍能修改交付对象的旧执行者，或者目标资源上已有可证明有效的版本/fence隔离。
- Mission范围的所有已发高风险UNKNOWN已解决，或按明确用户授权移交为持久核对义务并在交付中显式披露；不得悄悄遗漏废弃分支的UNKNOWN。默认严格成功不允许未解决的这类操作。
- 必需结果已纳入账本；用量未知仍保留reservations并显示pending。可见业务目标已完成与费用最终已结算可以分开，但不能写零费用或释放未知上界。
- 在同一事务写 `MissionFinalized`、正式报告引用、notification outbox、command receipt。报告必须来自已接受产物及系统状态，不在Commit里让LLM另写一份未经核验的新答案。

用户UI收到通知是另一个确认。`notification queued`不代表用户已看见；若用户原要求就包含发送到第三方，则那次发送属于Operation准则，不是普通UI Outbox。

### 8.1 撤回发生在通知之前或之后

通知发送前检查内容披露与Resolution当前性。过期时发“结果有更新/需复核”的状态通知，不把旧正文当当前成功再推送。若此前确实已交付，保留历史交付；必要时通过新通知说明有效性变化，不编辑旧审计事实。

### 8.2 用户要求更新

旧RequirementsRevision不可变；新revision生成coverage重新检查和TaskGraph影响分析。当前Mission尚未终止则进入新计划修订；已经终止则依据明确的用户任务/Commitment政策建立后继Mission，关联原Obligation，不复活旧执行上下文或旧权限。

---

# 第二部分：条件知识与证据有效性

## 9. 数据模型：事实、理由、真实等级与可用性分开

### 9.1 不要新建“另一份Blackboard事实库”

扩展现有KnowledgeRecord与source_dependencies，底层事实/支持关系由同一个Store管理；现有Knowledge记录可引用新的逻辑键和支持集。旧code/doc域保持已有语义，新模式从明确版本加入通用有效性。向量索引仅是检索投影，不参与真假判定，也不承担反向依赖完整性。

| 对象 | 必须保存 |
|---|---|
| `FactKey` | predicate_registry_version、predicate_id、typed arguments、资源/对象身份、tenant/Mission/scope、上下文/版本语义 |
| `FactObservation` | observation_id、FactKey、显式polarity、实际EvidenceRef、producer execution、observed_at、valid_from/to、assurance、限制 |
| `EvidenceRef` | 种类、权威记录位置与ID、内容hash、产生者、相关输入/环境、来源生命周期、披露scope |
| `JustificationSet` | support_set_id、目标Fact/Claim、conclusion polarity、非空AND premises、admission/ref、版本、准许用途 |
| `ValidityUse` | 目标记录+消费scope+用途+时刻+要求的freshness/assurance；不是全局true布尔 |
| `ValidityDecision` | truth、currentness、access、assurance_match、valid_support_sets、反证/理由、query_epoch、next_expiry |
| `DependencyReadSet` | 精确对象版本、查询谓词/候选集合epoch、时间界限、policy版本、覆盖状态 |

FactKey使用已注册参数类型和规范化函数产生，不能按自然语言相似度决定同一个命题。默认只允许有限、grounded atom；注册新的谓词必须声明观察范围、参数类型、正负证据比较规则、时效和结果用途。

### 9.2 四个维度独立

- **TruthValue**：TRUE / FALSE / UNKNOWN / CONFLICT，表示当前获准证据下是否存在正/负支持。
- **Currentness**：CURRENT / STALE / REVOKED；不可读取另返回availability诊断，不伪造FALSE。
- **Assurance**：不是一个万能从0到1的分数。保存领域、检查种类、接受条件与可用用途。例如“文献确实这样说”与“已执行集成测试”不能按简单整数互相替代。
- **Access**：当前用户/Agent是否可读取或用于此动作。真实历史事实不因撤权而变没发生，但新披露必须被阻止。

`VERIFIED` 是历史工作流中经过某种核验的状态，**不等价于“任意Agent在任意场景可依赖”**。`usable_for_dispatch()`、`usable_for_review()`、`readable_for_exploration()`分别实现政策。

### 9.3 来源陈述与世界事实分开

“供应商报告声称速度提升40%”进入：

```text
source_says(source_hash, span, proposition)
```

不能只因引文匹配就进入：

```text
performance_improved(system_version, 40%)
```

后者需要符合该命题范围的测量/实验/独立审阅。现有scoped-observation保护保留；General Verifier可以为开放命题提供有边界的语义接受，不能把来源验证接口复用成任意事实升级器。

### 9.4 时间与版本必须明确

`valid_from <= t < valid_to`，端点统一整数UTC微秒，valid_to可为空但必须由policy允许；所有进入数值域的bool、NaN、inf拒绝。源文件“指定版本”与“必须最新”是不同policy。

条件需标记检查阶段：

| 阶段 | 意义 | 例子 |
|---|---|---|
| START | handoff前成立；记录带时间的开始见证 | 下单前库存足够 |
| MAINTAIN | 执行期间持续需要；监测能力及空窗必须明确 | 测试期间不得修改输入数据 |
| ACCEPT | 接受/交付时须成立 | 报告来源仍获准使用 |
| HISTORICAL | 只主张某过去时刻的观察，不主张现在仍真 | 昨天测试在工具链V通过 |

下单后库存下降，不应抹掉“开始时库存足够”的历史见证。MAINTAIN没有可信监测或锁定就不能声称连续成立；记录coverage缺口并按政策不接受该强保证。权限/撤权在新披露和新handoff始终检查当前状态，不能用HISTORICAL标签绕过。

---

## 10. Justification：多组充分理由与循环自证

### 10.1 表达方式

一个结论可以有多组OR支持，每组内部是AND：

```text
K = (证据A AND 条件B) OR (独立实验C)
```

`JustificationSet`不是模型任意贴的引用列表。它须携带`RuleAdmission`或独立Review，说明这组前提为什么足够支持当前范围内的结论。符号规则通过已批准的规则实现/形式核验；开放蕴含通过独立Review。系统不会凭“有一条边”自动证明自然语言蕴含。[W01]

同源复制不算独立证据：Evidence记录`provenance_root`；重复hash/同一实验不同格式不是两次独立观测。是否需独立复现由criterion/evidence policy规定，不按引用次数增可信度。

### 10.2 真值求值采用有限带符号最小不动点

这是本专项确定的参考算法，生产可做SCC增量优化但必须保持等价：

1. 从**当前获准、完整、适用的基础观察**建立正/负literal集合。
2. 过滤掉未准入/过期/撤回的推理关系。显式否定literal必须有实际负支持；不存在记录不是否定。
3. 每组所有premise得到支持时，加入其conclusion的指定polarity。
4. 重复直到不再新增literal；有限grounded集合必定停止。
5. 对每个FactKey：仅正=TRUE，仅负=FALSE，两者=CONFLICT，都无=UNKNOWN。
6. 不从旧VERIFIED派生节点作为初值。撤回基础锚后重新从有效锚计算，否则A↔B循环会永久自证。

**冲突不爆炸：**A既有正又有负支持，不代表任意B成立。保留正负理由，不给系统添加经典逻辑爆炸规则。

### 10.3 真值支持与可用于执行的见证不同

即使有限推理能推到K的正支持，也不代表它有适合当前安全用途的、无争议的见证。生产要额外选择满足purpose/assurance的支持路径：

- 前提CONFLICT的路径不用于强制执行/最终接受。
- 如果K另有独立、无冲突支持组，允许使用那条满足policy的见证。
- 必须保留所有支持组供以后重新选择，不能第一次选中A就丢弃C。
- 一个conclusion自身正负冲突，默认不能用于强制正前提，即使正证据分数更高。
- 开放问题的Explorer仍可按权限阅读标注的冲突资料，但不能把它们放进“已确认事实”区。

附录代码给出`derive_support()`和`conflict_free_closure()`两层，后者只是强制使用的保守参考。领域如需不同逻辑，须有版本化政策、明确适用范围和测试，不能悄悄改全局语义。

### 10.4 复杂度和边界

参考算法保留有限literal/规则集，最坏按轮扫描全部规则；实际复杂度由literal数量与总premise数决定。部署对grounded atoms、rules、单次求值时间设上限。达到上限返回`EVIDENCE_EVALUATION_INCOMPLETE`，不能截掉剩余规则后宣布不存在反证。

生产优化采用：反向引用索引、SCC凝聚图、component dirty重算。对正向变化可以增量加支持；删除/撤回必须撤销依赖该锚的支持并重新求值整个受影响SCC及下游。没有证明正确的局部删除算法时用受限范围全量重算，不拿错误缓存换速度。

---

## 11. 失效传播：立即阻止错误使用，再异步重算展示

### 11.1 谁触发变化

`register_observation`、`retract_evidence`、`supersede_source`、`revise_requirements`、`change_fact_policy`、`revoke_access`、`expire_observation`进入同一CommitService；时间到期以明确事件驱动重建，同时实时使用点检查now，不能等待定时器稍后运行才停止过期证据。

**删除与反证不同。**证据被删除/不可读→旧支持无法复验；并不证明命题FALSE。新的负观察才建立负literal。更换模型也不自动取消全部旧真实执行证据；是否需要重验由assurance政策明确。

### 11.2 反向影响范围

索引覆盖：

```text
Evidence / Fact
  → JustificationSet / Claim / Knowledge
  → Method applicability
  → DATA InputManifest / Acceptance / GoalResolution
  → Summary / Context selection cache / eligibility cache
```

ORDER纯先后事件不因正文过期就被抹掉；ORDER guard确实引用当前支持时仍需重判。保持TaskGraph §4.4的规则，不一律说ORDER永不失效。

通过`DependencyRole`区分：`CONTENT_INPUT`、`CURRENT_PRECONDITION`、`HISTORICAL_WITNESS`、`ACCESS_GUARD`、`INFERENCE_ADMISSION`。不得仅凭“引用过”就递归删除所有后代。

### 11.3 防止异步重算间隙继续用旧VALID

在证据变化的短事务中：

1. 保存新事实/撤回事件。
2. 增加相关FactKey候选集合或scope partition的`evidence_epoch`；新插入反证也增加epoch，即使旧Review从未引用过它。
3. 标记受影响component/消费者为dirty，写有效性重算Outbox。
4. 检索、handoff、accept的准入要比较依赖对象版本+候选集合epoch+next_expiry。dirty或版本不一致时即时按新快照求值，或明确等待，不信缓存。
5. 重算在事务外读取一致快照；提交计算结果时CAS原epoch。期间有变化则不应用旧计算，重新读取。

**不得只检查被引用的正证据版本。**一个新的相反证据插入，也必须使对应query proof重新核查。这要求为“有哪些证据”和“没有某冲突/消费者”等集合谓词保存版本。

全局epoch可作为检测提示，但不能把“无关分支变了”直接等价为“该产物内容失效”。允许精确rebase/re-evaluation，只有相关语义变化影响结果。保守临时等待与永久作废是两回事。

### 11.4 历史与当前索引

- `Acceptance`/`ReviewRecord`不UPDATE正文；写新的validity事件/投影。
- `usable_now=false`不把Task.COMPLETED改为ACTIVE。
- 替代方法不能直接继承新发现的宽权限；HTN按TaskGraph当前入口重新求值。
- 如果独立支持C仍成立，保留K和可继续使用的产物，不重跑原Worker。
- 同样的产物hash若来自新实验条件，不自动沿用旧Acceptance；输入、范围、权限和见证也要一致。
- 来源删除后的历史报告可以保留不可逆摘要引用与删除tombstone，但禁止恢复备份后重新暴露已删除正文。

### 11.5 `ValidityReadSet` 的最低内容

```json
{
  "requirements_revision": "req-...",
  "subject_contract_hash": "sha256:...",
  "input_manifest_hash": "sha256:...",
  "evidence_versions": {"E-1":"sha256:..."},
  "fact_query_epochs": {"predicate:scope-key": 18},
  "support_set_versions": {"J-1": 4},
  "access_policy_revision": 7,
  "dependency_coverage": "COMPLETE",
  "valid_until_utc_us": 1800000000000000
}
```

示例值没有真实业务含义。UNKNOWN coverage不得变成COMPLETE；记录缺口并在安全/验收用途保守处理。大量集合使用有明确语义的partition version或Merkle/digest；摘要hash不能证明没有被忽略的反证。

---

## 12. 接到现有源码的方式

### 12.1 source_dependencies.py

保留 `merge_source_versions`、`source_dependencies_for`、`source_current_issues`、`stale_knowledge_for` 的legacy行为。为新模式增加SourceEvidenceAdapter，把原本已验证的SourceCitation/assessment receipts转换成新EvidenceRef；从实际源registry产生版本/撤回/当前性观察，不从模型字符串生成。

新模式消费者统一调用`ValidityService.evaluate_use(...)`；内部按来源kind路由到SourceEvidenceAdapter/ExecutionReceiptAdapter/ReviewAdapter等。禁止某调用直接绕过通用有效性，因为“已经是VERIFIED”。

### 12.2 Knowledge与Context

KnowledgeRecord通过新side binding关联逻辑claim/支持集；新字段不得改变旧canonical bytes。检索前用metadata过滤scope/current-use，再对最终回读材料复核；source内容仍由现有EvidenceResolver/read_verified读取。

必需当前前提走结构化查询，不依赖top-k召回。模型压缩是派生证据使用者：保存源引用和validity read-set。源码摘要失效不重跑原模型历史；只阻止新请求使用过期派生物并安排重组。

冻结Provider请求在恢复时不静默替换Context；新撤权阻止尚未发生的新披露/新调用。已发请求的事实保留，结果可能只作为历史；不能“更换prompt后沿用原invocation_id”。

### 12.3 HTN/TaskGraph端口必须真实接通

`PlanningEvidencePort.evaluate(predicate, use_context)` 返回ValidityDecision/read-set；`TaskGraphEligibility`只使用满足START/MAINTAIN/ACCEPT约定的结果。`ResolutionCommits`使用同一service，不另写“验收专用真假判定”。

输入变更、方法切换、共享消费者变动通过TaskGraph正式PlanRevision处理，本模块只给出影响候选、有效性与依据，不自己新增Task或调整预算。

若HTN还没合入，本专项可对显式声明的direct primitive plan运行完整样例，但**不能据此关闭compound组合/共享方法相关验收**。这些用例必须等真实TaskGraph/HTN主链接通后完成，不能永久用stub回答`selected_method=True`。

---

# 第三部分：执行一致性与副作用恢复

## 13. 保留现有账本，只扩展缺少的业务身份

### 13.1 三类账本的权威范围

| 位置 | 唯一事实范围 | 本专项新增什么 |
|---|---|---|
| execution.db Provider/Effect ledger | 每次真实模型/工具调用、冻结请求、已交接/结果/费用 | 不重建；用稳定引用关联新subject |
| orchestrator.db actions/approvals | 业务动作候选、审批、handoff决策、结果采纳和核对 | 新Operation occurrence与spec binding，复用原ActionCommit writer |
| 外部连接器或目标系统 | 真实业务操作是否应用，目标资源版本/收据 | 实际typed lookup；本地标HANDED_OFF不能代替这里的事实 |

`operation_occurrences`是身份/语义表，不另存一套会与actions竞争的可写SUCCEEDED状态。`operation_observations`保存收到的事实与来源；actions仍是该业务执行状态的统一投影。工具层没有外部副作用的普通读取，不必强行创造一个业务发送Operation，但仍走权限、费用、结果账本。

### 13.2 OperationId 的v2规则

当前 `business_action_id(mission, connector, operation, target)` 将一个Mission内同目标同动作看作同一现实工作。[S06] 旧Mission继续这种语义。

新模式：

```text
Operation occurrence identity =
    tenant + authorized intent occurrence + connector identity + operation kind

Operation specification =
    normalized target + exact params + exact artifact/input hashes
    + preconditions/expected resource version + authorization binding

External idempotency key =
    stable encoding(operation_id, operation_spec_revision)
```

`intent_occurrence`由系统根据获准的Task/Obligation业务slot或用户命令分配，不是模型自由UUID；周期、两次明确发送、不同收件人均有各自合法occurrence。相同业务意图换Task/Attempt/Method沿用OperationId。

**对象区分：**Obligation是责任，Operation是一次现实动作；一个Obligation允许多个明确动作。不能用一个ObligationId永远压成一次发送，也不能每次模型输出都造新Operation。

### 13.3 同意图参数改变的规则

| 旧spec状态 | 允许什么 |
|---|---|
| 尚未handoff | 通过新命令生成spec后继；旧spec与其审批失效，新审批绑定新hash |
| HANDED_OFF/UNKNOWN/仍有可能迟到 | 禁止用新spec越过旧版本；先核对和收敛 |
| 已确认APPLIED | 原版本永不变；追加/纠正/补偿是新明确授权动作，不因参数不同自动执行 |
| 已权威确认未应用且旧投递已排除 | 可以按政策重试同spec同key；需要新参数则明确新spec并重新审批 |

旧ID/hash不重写。新增`operation_bindings(operation_id,spec_revision,action_key,origin_occurrence_ref)`映射原actions记录；不根据动作文本相似度去重。

### 13.4 审批和授权必须绑定什么

`ApprovalBinding`包含principal/role与人数策略、operation_id、spec_hash、Artifact hash、target、risk、policy_revision、authority范围、expires_at、指定要求/用途。审批回执不是一个可无限重放的bool。

排队之后到真实handoff之前再检查有效期、scope、产物/输入、相关generation、资源前提。审批过期并不抹掉已发生操作；只禁止未来使用旧许可。

---

## 14. Connector v2：明确失败、未知与终局未应用

### 14.1 现有接口怎么兼容

保留现有 `Connector.execute/lookup`，新增显式的 `ConnectorV2Adapter`。旧adapter继续返回原Receipt；转换只能声明它实际具备的能力，不默认为全部true。

```python
# 拟新增接口；每个启用adapter要有实际实现，不能靠布尔声称能力。
class ConnectorV2(Protocol):
    def capabilities(self) -> ConnectorCapabilities: ...
    def execute(self, request: FrozenOperationRequest) -> ConnectorObservation: ...
    def reconcile(self, query: OperationLookup) -> ReconciliationObservation: ...
```

`ConnectorCapabilities`至少含：

- version、target namespace、真实执行环境身份；
- operation kind=`read/state/event`及declared resource scope；
- idempotency语义、key范围、保存期限/可验证的截止点、同key不同payload是否明确冲突；
- reconciliation是否覆盖正在执行/已成功/终局拒绝，读是否线性一致或可能延迟；
- 是否有终局未应用证明/取消tombstone，能否排除迟到投递；
- remote fencing / conditional update（ETag或资源版本）能力；
- cost upper-bound、timeout和实际费用查询/回执；
- 对文件/进程型adapter是否支持持续的execution ownership核对。

### 14.2 ReconciliationObservation 的词表

| 结果 | 含义 | 是否自动再次执行 |
|---|---|---|
| `APPLIED` | 匹配精确operation key/payload/target的权威应用回执 | 否，记录事实 |
| `NOT_APPLIED_FINAL` | 该精确版本确定未应用，而且旧投递不可能再应用或已受可信fence约束 | 仍需现权限/预算/代次/保留期检查后才可同key重试 |
| `IN_PROGRESS` | 目标服务确认仍在处理该key | 否，等待并核对 |
| `UNKNOWN` | 查不到、查询超时、过期、服务不可确认或回执绑定不完整 | 否，保留UNKNOWN/升级人工 |
| `CONFLICTING_RECEIPTS` | 两份已认证观察无法同时成立 | 隔离，禁止普通推进 |

### 14.4a 终局未应用证明的时间范围（本专项新增精化）

`NOT_APPLIED_FINAL` 必须携带 `covers_handoffs_through`、远端 observation revision、检查时间和排除迟到的证明。它的含义是“**截至这批交接，均未应用，且这些旧交接以后也不能再应用**”，不是“同一业务意图在未来永远不能成功”。

例如 handoff 1 被可信地关闭且确定未执行；新授权允许 handoff 2 用同一业务 key/同一冻结参数重试并成功。这是正常进展，不能把 handoff 1 的未应用与 handoff 2 的成功判为互相矛盾。下游执行状态应按 Operation 汇总，但观察存储保留各自 handoff 和覆盖水位。

相同 handoff/覆盖范围内，先有已应用的权威回执，又有声称该次未应用的权威证明，才进入 `RECEIPT_CONFLICT` 并停自动推进。一次成功操作的事实不能被后来的“查不到”抹掉。若 adapter 使用永久拒绝此 key 的 tombstone，则它不能宣称仍可同 key 重试；需要按其协议申请新的明确业务意图，或保持禁止。若使用可递增的远端 fence，则新交接可以有新 transport epoch，但业务 key 和 spec 字节必须保持，旧 epoch 要被真实执行端拒绝。

参考代码 `merge_operation_fact()` **仅处理同一 handoff/兼容观察 epoch 的合并**；不能拿它简单折叠整个 Operation 从创建到重试的全部历史。这一限制要由入库 codec 和实际 reducer 的分组键保证，而不只写在注释里。

**重点：一个瞬时“not found”不等于NOT_APPLIED_FINAL。**旧请求可能尚在网络/远端队列中；服务的幂等记录可能已被回收。`lookup_authority="authoritative"`字段本身不证明其语义。适配器必须通过并发、迟到和保存期测试。[S07,S08,W02]

当前`lookup_verdict()`会依据既有authoritative合同把None映射为CONFIRMED_NOT_STARTED。保留legacy；v2要读取typed evidence，不能继续只看一个布尔或字符串。

### 14.3 两种“没有执行”也不同

- 编排端尚未写handoff：可以证明本协调路径没有发出操作，但还要核对是否存在先前相同业务key。
- 远端权威确认某个操作未发生：用于已handoff后的核对；必须绑定远端结果版本/查询范围/截止以及迟到排除。

超时的协程或线程不提供后一种证明。当前`asyncio.to_thread`超时不会可靠终止其底层调用；本地`_inflight`集合只描述本进程观察，不能跨重启证明旧调用已经停止。[S07]

---

## 15. Handoff、迟到结果、重试与补偿

### 15.1 `begin_handoff_v2()` 的执行顺序

在原`ActionCommitsMixin.begin_handoff`旁增加模式分派；内部使用同一actions表与预算接口：

```text
BEGIN IMMEDIATE
  找稳定command/attempt receipt（先幂等查询，后状态判断）
  读取Operation/Spec/现Action/Approval/当前Task语义
  核对origin scope、Operation demand、spec/hash、被选计划、dispatch generation
  核对当前Evidence use与准则阶段、资源版本/远端fence要求
  拒绝已有同spec APPLIED、未解决UNKNOWN、目标资源上冲突的旧写者
  验证Connector当前部署能力与有效幂等保存期
  检查实际预算与reserved tail；保留未知费用
  CAS领取当前交接代次与owner，保存可过期执行ticket
  写HANDED_OFF、冻结外部请求、预算reservation、handoff事件和回执
COMMIT
ActionExecutor通过已注册adapter发送完全相同的冻结请求
```

Provider/Effect调用仍走SDK原入口；外部Connector调用仍由`runtime/actions.py`唯一发出。本专项不得再建一个直接调用生产API的后台线程绕过这些入口。

**保证的线性化点：**本库准入事务提交。若授权在此后撤销，能阻止尚未发生的后续handoff，但不能撤销已经在途的请求。若业务要求“撤权提交之后远端绝不应用”，必须由远端每次检查fence/版本或可取消令牌支持；没有该能力就不宣称此强保证。

### 15.2 返回结果如何记录

`record_operation_observation()`接收的是经过adapter身份验证的执行观察，不是模型的`outcome="success"`。核对operation/spec/key/payload/target/adapter version和receipt源。按发生语义处理：

- 确切APPLIED：记录真实事实与费用；即使原Worker已过期或Mission在取消，也保留。是否采纳为当前目标另走Validity/Acceptance。
- 明确未应用拒绝：记录终局拒绝或可重试未开始证据，不能伪造APPLIED。
- transport error、超时、格式不匹配：UNKNOWN。存在部分效果时记录partial detail并保持未决，不能按“失败所以没发生”处理。
- 旧UNKNOWN迟到：不能覆盖已有APPLIED。
- 两个匹配身份却相反的权威结果：CONFLICT，保留双方；禁止last-write-wins。

读操作的`applied=False`可以是合法读取，不与写操作“未应用”共用简化判断。上述APPLIED矩阵针对可产生业务副作用的操作；实际adapter须区分读结果与mutation receipt。

### 15.3 修正超时线程的晚到处理

目前`ActionExecutor._release_when_done()`主要回收inflight标记并取出异常。[S07] v2要：

1. callback取到有效Receipt时，通过同一个执行观察入口排队持久化；异常也按精确原绑定记录，不能新生成操作。
2. callback不可在随机线程直接修改orchestrator Store；回到所属执行循环/受控dispatcher。
3. 循环已关闭/进程已死时，callback不被当持久机制；恢复必须重新查询目标权威账本。
4. 长时间任务的真实owner/execution fingerprint持久化；租约到期后如不能证明旧调用已终止，就不让冲突新写者运行。
5. 不凭旧PID杀进程，不把scope换名当回收。平台owner由现有隔离执行器提供并通过真实进程测试。

### 15.4 同key再交接门

只有同时满足以下条件才重试**同一spec、同一外部key**：

```text
Lookup = NOT_APPLIED_FINAL
+ receipt/query与原binding一致
+ adapter确有终局权威
+ 旧本地发送者已收敛
+ 迟到投递不可能生效或已被远端fence挡住
+ 幂等记录保留期仍有效
+ 当前计划、权限、审批、generation仍允许
+ 当前预算与retry上限允许
+ 没有取消请求
```

普通500或not-found不满足；lookup响应本身过期也不满足。若只具备best-effort核对，在不确定结果后保持人工核对，不为了活性牺牲重复副作用边界。

附录提供`may_rehandoff()`布尔条件参考，生产条件必须由实际记录/观察计算，不能在配置里默认全true。

### 15.5 取消与补偿

取消：停止未来业务派发，保留已发生结果、费用、核对和必要清理。取消中有未知动作时，留在收敛/未决状态，或显式移交为持久核对责任并通知用户。

补偿：新Operation，包含`compensates_operation_id`、独立授权、独立预算、当前目标状态前提、回执与可能的失败。不是删除原APPLIED，不一定恢复成原状态，也不必机械倒序执行。其他业务可能已改变对象，补偿必须按当前条件检查。[W05]

不支持补偿时明确不可补偿；例如已发邮件不假装“undo”删除对方邮箱。是否采取后续纠正由用户/政策批准的新任务决定。

---

## 16. 双库、Outbox、费用与观察恢复

### 16.1 命令发出与结果导入

已有dispatch_intents继续负责SDK调用。新增review/operation绑定放其旁表或明确versioned config，不改旧字段语义。可靠交接如下：

```text
orchestrator：提交稳定intent/config/input/expected_turn_id
        ↓ 可重复投递
SDK：幂等create(creation_key) / submit(input_id)
        ↓ SDK持久结果
orchestrator：用(execution_store_id, record_id, revision)幂等导入
        ↓ 本库事件/结果/接受任务/consumer receipt一并提交
确认消费；ack丢失可重读原结果
```

相同record revision收到不同payload hash是完整性错误，不默默覆盖。执行库若支持后续核对修订，要按显式revision单调导入，而不是把第一个UNKNOWN永久占住唯一消费键。

调用者等待超时不是新内容Attempt。先查询原AgentTurn和Provider/Effect事实；只有明确内容失败且重试政策批准，才创建新Attempt。`result=None`与“结果接口无法读取”必须区分。

### 16.2 修正liveness观察语义

`runtime/agent_worker.py::AgentBridge.liveness` 中广泛异常转`exists=False`的路径，按关键契约加固调整为：

- KnownPresent(state/blocked/progress/settled)
- ConfirmedAbsent（底层明确not found）
- ObservationUnavailable（数据库、解码、通信故障）

排队、等待模型响应、等待审批、SDK结果UNKNOWN分别保留。不可观察时禁止自动LOST、释放预算、新建重复Attempt。lease过期先取得恢复权并核对真实SDK状态；不能直接套用原文早期“过期就新Attempt”的简化流程。

### 16.3 费用不建立第二套可改真值

- Provider和Effect的实际调用/费用引用execution ledger；编排只导入事实、进行已批准额度的归属与投影。
- `UsageFact`的source key/修订唯一；相同调用不能因多份Review/多个父任务重复计费。
- UNKNOWN费用不是0。预算reservation仍保留或按冻结政策保守结算，不能因没有回执就释放。
- 内容失败、被废弃候选、过期Review、已取消操作的实际成本都记录。
- 若实际收费超出预留，照实记录并报警/停止新的非必要派发，不裁成预算数字伪造守恒。
- 规划、正文、Critic、MissionJudge、取证、恢复查询都使用正确账户；“最终Judge需要增长”不应被错误路由到要求Task Attempt的保护尾接口。
- Observer导入不得因为当前Mission终态就拒绝已经发生的费用；它没有借机启动新任务的权力。

### 16.4 Outbox不是另一个Task Scheduler

如果Store没有通用通知/变化Outbox，新增`domain_outbox`，只存指定消息类型：validity recompute、review result ready、operation observation import、notification、cancel/reconcile requests。SDK执行本身继续用dispatch_intents。

claim消息时写owner/lease/generation；网络调用在事务外；消费端幂等；确认必须核对本次claim token，旧worker不能确认新claim。重复消息允许，重复业务改变不允许。[W03]

`consumer_receipts`保留消息id、handler版本、输入hash、输出回执引用。处理与回执写入同事务；不允许先ack再更新状态。死信包含错误分类和可恢复责任，不能当自动成功。

---

## 17. 启动与灾难恢复协议

### 17.1 恢复流程

```text
进程启动：新副作用禁用（允许受控只读核对）
  ↓ 验证数据库schema/迁移hash/CAS manifest/删除清单
  ↓ 获取新的runtime与coordinator epoch；旧票据失效
  ↓ 从execution库导入已持久结果/用量/UNKNOWN
  ↓ 读取未决dispatch/review/action/notification/dirty-validity obligations
  ↓ 已有结果继续验收；无结果仅在证据允许时恢复原执行身份
  ↓ 核对已handoff的外部动作、遗留执行者与目标资源
  ↓ 当前证据/权限/要求重新检查
  ↓ 只对满足门槛的资源/任务开放新handoff
```

恢复一个不确定动作不必永远冻结全系统；在验证影响范围后可以阻塞该资源/责任，允许无关安全任务运行。起步恢复阶段全局禁副作用是安全门，之后按明确scope解除。

### 17.2 必须保存的恢复切点

| 崩溃点 | 恢复行为 |
|---|---|
| 计划/意图提交之前 | 无正式派发；原command可重试 |
| intent提交后、SDK create之前 | 用原creation_key继续 |
| SDK submit成功、编排没回执 | 按原input_id/expected_turn_id查回 |
| Worker结果已存、Review还未开始 | 验原产物，不重做Worker |
| Review返回、接受未Commit | 保存/重读原Review再按当前read-set接受 |
| Acceptance提交、通知丢失 | 重发同通知，不重新验收/重新执行业务 |
| HANDED_OFF已记、实际网络调用未知 | 作为可能发生；按原key核对 |
| 目标已应用、回执丢失 | 查询权威回执；不能重跑整个Task替代核对 |
| 旧发送者仍活着，新owner已启动 | 没真实fence/终止证明不得冲突重发 |
| 证据撤回已提交、缓存重算未完成 | epoch/dirty gate阻止旧缓存放行 |
| 已取消Mission仍有晚到结果 | 保存真实结果/费用，按取消/核对义务继续收敛 |

### 17.3 Replay 与恢复分开

`replay-v2`保留；本专项事件在新业务reducer中覆盖要求、Review、Acceptance、支持关系、Operation binding、Outbox义务和相应预算引用。未知且可能改变正式状态的事件应停止该流重建并报coverage gap。

Reducer不得调用LLM、读取当前网页或查询现在的目标系统，不读当前Task表补答案。时效判断用记录的`as_of`/到期事件重建当时投影；恢复后的当前性再独立求值。

本专项只能声明其事件与共享模块的明确coverage；原R37要求全业务投影重建，仍要和TaskGraph/预算/周期reducer一起验收，不能只删除本专项几张表就声称全部Event Sourcing完成。

### 17.4 多资产备份不能直接cp运行中的db/wal/shm

现有`replay.library_copy()`是历史读取工具，不把它当跨库在线一致备份保证。[S10] 使用SQLite Backup API生成每库一致副本；执行一次完整恢复切片仍须有业务屏障和manifest。[W06]

恢复点制作：暂停新command准入/新handoff；冻结结果导入和状态推进的短边界，保留外部尚在途记录；取得两个库和CAS引用的可核查切片，检查orchestrator每个已导入结果都在execution副本或受保护事实中。外部世界无需停住，但副本内必须保留这些handoff的未决状态，恢复后重新核对。

manifest包含库hash/schema/事件水位、任务/调用身份映射、CAS引用与pins、外部adapter ledger标识、policy/config/model版本、删除tombstones、未覆盖项。发现缺失依赖不得以重发操作来“修复备份”。

---

## 18. 一个贯穿三部分的验收场景

**用户要求：**读取获准资料，形成有依据的报告；批准后将指定版本发布到测试目标。当前方法A与B共享资料结论C。

1. 创建不可变要求r1、候选报告H1，ReviewPackage绑定C的支持与所有输入。
2. Verifier发现一项证据不足，提交INCONCLUSIVE；AgentTurn正常完成，但没有Acceptance。
3. 新实验支持补齐后形成H2；独立Review通过，生成内容Acceptance。此时没有“已发布”事实。
4. 用户批准H2的Operation spec；发布前原来源被撤回。当前有效性门阻止旧审批直接发布，保留历史Review。
5. 若C另有充分独立支持，系统重判后保留C；若没有，则只修相关分支。生成H3并重新绑定Review/审批。
6. 外部测试连接器确实应用H3，进程在回执导入前被kill。
7. 恢复核对原Operation key得到APPLIED，导入一次真实费用/结果，不重复发布，也不重新执行Worker。
8. 最终GoalResolution绑定r1、H3、当前支持、独立Review、发布receipt，根目标满足且旧写者已收敛后完成Mission。
9. 同一用户明确再次发布另一个版本，形成新合法occurrence；不能被原同目标action ID误合并。
10. Replay重建本专项状态，不调用模型/连接器。UI刷新仍可看到历史通过、当前支持、实际发布与取消/恢复原因。

该场景必须通过真实Core/TaskGraph/AgentBridge/Commit/验证链，不能在fixture中直接把Task状态改成完成代替接线。确定性测试可提供脚本化模型输出以验证协议；另跑获准真实模型样例验证审阅与生成质量，二者分别报告。

---

## 19. 存储与迁移：把上述算法接到真实 Store

### 19.1 表与写入责任

附录 B 和资料包 `schema/assurance.sql` 是**可执行语法原型**，不是已经合入的数据库迁移。不要逐表盲抄：若 TaskGraph 已创建同语义表，扩展该表；原始 actions、approvals、dispatch_intents、预算和 execution 账本继续是各自事实源。

| 记录组 | 不可变事实 | 当前可变投影 | 唯一写者 |
|---|---|---|---|
| 要求 | requirements_revisions、原文/授权引用 | requirements_heads | 认证命令经 Commit |
| 审阅 | review_packages、review_records、真实原输出 | review_jobs 状态 | Review 接收/执行协调经 Commit |
| 接受 | acceptances、goal_resolutions | Task/Resolution 当前采用与有效性 | ResolutionCommits |
| 证据 | evidence_records、fact_observations | evidence_lifecycle | EvidenceCommits |
| 理由 | justification_sets、members、admission回执 | justification_adoptions | EvidenceCommits |
| 当前性 | 到期/撤回/反证事件 | validity_partitions/evaluations/dependencies | EvidenceCommits + versioned reducer |
| 业务动作 | operation_occurrences、bindings、observations | **原 actions 状态** | OperationCommits 内部复用 ActionCommits |
| 交付 | 原事件/通知义务与消息payload | domain_outbox、consumer_receipts | 同事务 writer + consumer Commit |
| 结束 | 根Resolution、收尾事件 | mission_closeouts、原Mission状态 | Closeout/ResolutionCommits |

**证据正文不可变，证据生命周期可变。**撤回会新增事件并更新生命周期投影，不能把旧正文原地改成“这份证据一直不存在”。Justification 的推导定义与其当前被准入状态也必须分开。

### 19.2 最少的约束与索引

- 所有新记录都带 tenant/Mission；跨Mission共享必须另有获准的数据导出，而不是凭全局ID跳过隔离。
- Requirements parent、Review package/job、Acceptance/Review、Fact/Evidence 的复合归属键一致。DDL未直接连接旧表的地方，在生产迁移中增加实际可用外键，并在Commit验证归属；不能把一个 TEXT 外键列当成已核验实体。
- `(method_instance, slot)`、`(subject, exact binding)` 等 TaskGraph 唯一键继续使用。每个 GoalResolution 绑定原Task/Obligation，而非任务标题。
- 同一外部执行源和源修订只导入一次；**同一源ID且不同正文hash是冲突**，不能 `ON CONFLICT DO UPDATE` 覆盖。
- Outbox 的 dedupe 必须有 destination 和命令scope；consumer 回执至少绑定 consumer/message/input_hash。重试次数是运输指标，不清零内容失败或预算。
- `validity_dependencies` 建反向索引；`fact_observations` 按 scope+FactKey+polarity 建候选索引；分区epoch在新增/撤回/修订时同事务增长。
- hash只保证内容一致性，不能防止拥有数据库写权限的管理员伪造。生产威胁模型应保护数据库、checker执行入口和秘密；跨主机回执需真实认证，不能仅验“hash算得对”。

### 19.3 Codec：边界集中解析，不静默修正坏数据

`json.loads()` 之前限制字节长度；JSON对象重复键必须在折叠成dict之前拒绝。若现有SDK output-block工具已经完成这件事则复用，否则给新协议加明确入口。示例：

```python
# 目标codec的完整低层示例；业务 dataclass 与字段校验仍按各合同实现。
import json
import math
from typing import Any

class WireContractError(ValueError):
    pass

def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WireContractError(f"duplicate JSON field: {key}")
        result[key] = value
    return result

def _nonfinite(value: str) -> None:
    raise WireContractError(f"non-finite JSON number: {value}")

def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise WireContractError("JSON number overflow")
    return value

def load_wire_json(text: str, *, max_bytes: int) -> object:
    if len(text.encode("utf-8")) > max_bytes:
        raise WireContractError("payload exceeds byte limit")
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs,
                          parse_constant=_nonfinite, parse_float=_finite_float)
    except (json.JSONDecodeError, RecursionError) as error:
        raise WireContractError("invalid JSON structure") from error
```

上例 JSON库桥接暂用`Any`，只能留在这个边界；公开函数返回`object`，进入类型化业务对象前检查字段集合、值类型、上限、深度及实体引用。不得通过`str(x)`/`bool(x)`把任意坏类型当合法字段。模型输出不是URL下载或任意文件读取指令；source/receipt引用要经过注册解析器。

新版本时间统一明确单位，例如wire的UTC微秒整数，需检查JS安全范围；旧秒浮点字段继续原codec。新时间选择不能改写旧hash和旧评估身份。

JSON数字溢出也要拒绝：`1e9999`虽然不是字面的NaN/Infinity，但在浮点解析后仍可能溢出，示例显式检查。准则数量、token与版本等整数在具体codec再使用`type(x) is int`与范围检查；开放任意精度整数也不能悄悄送到JS数值中舍入。

### 19.4 Store方法与实际写入方式

下表是目标内部接口，**不是说当前Store已经有这些方法**。如已有等价方法，提供薄适配并记录实际名称。

| 方法 | 内部实现要求 |
|---|---|
| `get_requirements_snapshot(mission_id)` | 同一读事务返回head/不可变body/hash/authority |
| `insert_review_package_and_intent(...)` | 复用既有intent writer；package、job、预算和事件在同一编排事务 |
| `record_review_observation(...)` | 验execution源，幂等保存原raw+parsed；不自动accept |
| `load_acceptance_bundle(subject_ref)` | 取精确包、checker回执、输出、要求、readset和当前有效性；不读无约束latest |
| `commit_acceptance(...)` | 实现§7.4；唯一写Acceptance及相关Task接受事件 |
| `commit_goal_resolution(...)` | 额外核验Method采用、必需slot与组合义务；不能仅调用terminal_task |
| `record_evidence_observation(...)` | 保存不可变证据、锚点、集合epoch和重算意图 |
| `change_evidence_lifecycle(...)` | 权限/同版本校验，记撤回/到期事实与epoch，旧body不变 |
| `admit_justification(...)` | 验推导/审阅来源及全部前提；不允许Worker自由写事实规则 |
| `commit_validity_evaluation(...)` | readset CAS；旧结果可留日志，但不盖新投影 |
| `bind_operation_occurrence(...)` | 认证业务意图身份；复用existing action记录，生成精确spec/外部key |
| `record_operation_observation(...)` | 独立收真实事实；按binding/handoff/cutoff/源版本归并，旧owner不因此重新获得动作权 |
| `claim_outbox_message(...)` | BEGIN IMMEDIATE中检查due并CAS owner/generation；事务外发送 |
| `ack_outbox_message(...)` | 核对destination、message、payload、consumer receipt及claim generation |
| `try_finalize_mission(...)` | 根满足和危险在途收敛分开检查，同事务最终事件与通知义务 |

写SQL时遵守：

```sql
-- 示例：相同读取版才能推进；必须检查rowcount==1，否则抛StoreConflict。
UPDATE review_jobs
SET state=:next_state, record_version=record_version+1
WHERE review_job_id=:job AND tenant_id=:tenant AND mission_id=:mission
  AND record_version=:expected AND state=:expected_state;
```

事件查重先查已保存回执的输入hash；新写入使用唯一约束兜底。不要用 `INSERT OR REPLACE`：它可能删除/重建关联记录，破坏不变身份和外键。单库写锁不能替代跨库receipt检查。SQLite快照/写事务语义见[W04]，本专项身份和业务约束由应用实现。

### 19.5 迁移实施步骤

1. 读取当前 `schema.MIGRATIONS`、DDL checksum、最近TaskGraph迁移，确定下一未占用版本。**在本次代码提交中固定这个版本和完整DDL**，运行时不得算`max(version)+1`后随意登记。
2. 保留所有旧DDL的文本/checksum，新增 `assurance_schema.py`常量由原schema导入。任何分阶段迁移各有固定描述符，不更新旧记录为新规则。
3. 备份副本演练。采用原迁移引擎和事务纪律。附录SQL可在新内存库用`executescript`检查语法；**生产活动事务中不要直接executescript整份文件造成隐式提交**。
4. 新Mission显式绑定 `full-target-v1`；已有full-target binding由总计划所有者一次冻结。不能让三个子模块各开一个互相不兼容的独立feature flag。
5. 旧Mission继续legacy。若有需求迁移活跃Mission，显式暂停、核对UNKNOWN、保存原事实/未完成义务、建立带来源的新绑定。原来的Task不伪造HTN Method见证。
6. 对新表/组合外键/唯一约束做破坏性负控；完成冷恢复和旧回执查询后才声明迁移有效。

---

## 20. 真实主链接线：实施 Agent 必须逐项打通

### 20.1 逐文件改造表

| 文件 | 已有行为/位置 | 必须实施的改动 | 不能接受的替代 |
|---|---|---|---|
| `contracts/requirements.py` [新增或复用] | 无本专项实现断言 | frozen requirements/AST/coverage codec；名义ID与authority来源 | 从文本“或”猜历史公式 |
| `verification/assessments.py` [现有] | `AssessmentBindingV1`、criterion catalog、`assessment_binding_for` | 作为legacy adapter；v2包复用真实artifact/checker事实 | 改旧hash或把默认值当新验证 |
| `verification/critics.py` [现有] | `parse_critic_verdict`、旧from_json | 保留v1/v3语义；新增review-v2 prompt+codec；subject选择正确准则集合 | 叶子审阅被迫为无关根准则宣称PASS |
| `verification/verifier_router.py` [现有调用链，实施前定位精确版本] | 实际各layer执行/回执 | v2将所有真实check receipt绑定ReviewPackage；独立Reviewer可按预算追加获准检查 | 自己造LayerResult(PASS) |
| `verification/mission_coverage.py` [现有] | 文档域覆盖汇总 | legacy保留；full-target改用公式+目标支持与组合Resolution | 在字面匹配上再加自然语言猜测 |
| `orchestrator/commit_service.py` [现有] | `_grade_and_project`等结果/知识/图提交主链 | 装配Resolution/Evidence/Operation子职责；full-target所有结果必须经此接受门 | 新SDK旁路直接Task.COMPLETED |
| `orchestrator/event_handler.py` [现有，行号本轮未重读] | `_run_critic`、Task结果、最终judge与结束分支 | 从真实checkout查全调用点；区分Task/Composition/Mission审阅；result先保存后应用；closeout替换新模式收尾判断 | 只在新demo调用，无生产接线 |
| `orchestrator/action_commits.py` [现有] | `business_action_id/propose_action/begin_handoff/record_action_outcome/record_reconciliation` | legacy不变；v2Occurrence经薄适配引用原actions；审批精确绑定，负面证据有cutoff | 全库重算旧action_id |
| `runtime/actions.py` [现有] | `ActionExecutor.hand_off/reconcile/_release_when_done` | v2 typed lookup；持久导入晚到receipt；retry gate；CancelledError路径显式收尾 | 调大timeout或全部异常当FAILED |
| `runtime/connectors.py` [现有] | `Connector/Receipt/OperationSpec` | 新接口放connector_v2，legacy按能力严格映射；测试服务不冒充真实外部保证 | supports_idempotency=True就认为安全 |
| `runtime/agent_worker.py` [现有接线] | AgentBridge/冻结意图 | 保留实际调用/费用事实；明确not found与observation error；旧结果归属核查 | 读取失败等同Agent不存在并重建 |
| `memory/source_dependencies.py` [现有] | 上游版本展开/当前来源核查 | 提供文档Observation adapter；任意Fact/Justification使用通用evaluator | 删除现有源回读核验 |
| `memory/claims.py/verified_knowledge.py` [已有，实施需查最新版] | 分级/正式记录 | 新语义关联support与permitted uses；既有scoped observation保留 | 用自然语言引用直接提升事实 |
| `context/retrieval.py/context_builder.py` [已有消费者] | Context选材 | 查询有效性时检查epoch、用途和读取权限；保存manifest；坏材料不流入事实区 | 只过滤Claim.status |
| `graph/readiness.py`或`graph/eligibility.py` [TG拟议模块] | 按TaskGraph已采用名称 | 共用同一个ValidityPort；在准入前复核DATA/方法前提/Resolution，不另外造eligibility状态机 | 未实现HTN返回True占位 |
| `storage/store.py/schema.py` [现有] | 原事务/DDL/故障钩子 | 复用并增加细粒度repository；真kill与throw分别测试 | 清库重新开始代替迁移 |
| `observability/replay.py` [现有] | 原有限coverage | legacy保留，注册新业务reducer；显示本专项/全局coverage边界 | 从当前表补事件缺失字段 |
| Host实际projection/API/Store [未读取] | H0定位 | requirements/ref review/currentness/ops/closeout同快照读取；复用共享schema | 虚构Host路径和确认已接入 |

表里用于导航的旧私有函数名如在实际HEAD已经移动，source-map记录新位置和原行为对应；不能仅因名字变了就重做整个子系统。`rg`是定位手段，不是凭零命中证明不存在：

```bash
rg -n 'parse_critic_verdict|_run_critic|mission_coverage|terminal_task|MissionCriteriaJudged' src/agent_orchestrator
rg -n 'begin_handoff|record_action_outcome|record_reconciliation|lookup_verdict' src/agent_orchestrator
rg -n 'source_current_issues|stale_knowledge_for|accepted_assessments_for' src/agent_orchestrator
rg -n 'COMPLETED|GoalResolution|goal_resolutions|try_finalize' src/agent_orchestrator
```

### 20.2 对 HTN/TaskGraph 的硬依赖合同

本专项不实现第二个HTN refiner；它要求总计划提供以下真实读取/提案接口：

- `read_selected_method(task_ref)`：当前采用及精确参数/前提/组合义务；非选定候选不能作为根证据。
- `read_required_occurrences(method_ref)`：全部必需slot、DATA输入、其Acceptance/Resolution；不能靠任务标题或最后一个叶子。
- `read_input_manifest(attempt_ref)`：本次实际输入而不是最新产物。
- `read_obligation_lineage(task_ref)`：稳定责任与预算/尝试归属。
- `propose_validity_impact(change)`：产生需修复范围，不直接改图；裸Task修改不能绕过新binding。
- `current_dispatch_generation(scope)`：未来动作资格，不替代现实执行端fence。

开发期可以用冻结快照夹具测试纯公式；最终CA4必须连接真实TaskNetwork。HTN依赖未交付时，只能报告primitive路径通过、compound路径BLOCKED，不允许把测试夹具当产品能力。

### 20.3 Host需要交付的公开视图

不要求新建Node网关。沿现有MissionControl/Host桥接增加版本化公开投影：

```text
requirements：revision、准则、当前success-expression解释
reviews：审阅阶段、每准则结论、证据引用、缺陷、局限
acceptance：历史接受身份及当前可用性（原因可展开）
resolution：根/父目标覆盖、所用方法、未满足义务
operations：合法意图、精确候选版本、审批状态、实际效果/UNKNOWN
closeout：等待哪些执行收敛、费用核对和交付情况
snapshot：plan revision、state event cursor、validity watermark一致读取
```

UI不自行求值根目标；不能把审阅响应解析失败显示为工具UNKNOWN，也不能把拒绝读取的证据展示成“已被证伪”。返回哈希不授予读取权限。通知流失败保留stale画面；取消订阅不取消Mission。

---

## 21. 四个完整实施切片：不重编总计划编号

| 切片 | 一次性交付的完整功能 | 关联总工作包 | 完成门槛 |
|---|---|---|---|
| CA1 | 具体要求→真实独立审阅→候选/根接受→正式收尾 | P1/P2/P6 | 任意坏组合不能误完成；Task/Composition/Mission预算身份各自正确；旧评审回归通过 |
| CA2 | 真实来源/检查→条件支持→当前性更新→下游使用被正确影响 | P1/P3/P5/P6 | 两组独立支持、循环锚撤回、反证插入、权限撤回和过期缓存均正确 |
| CA3 | 精确操作候选→审批→实际测试目标→回执丢失→安全恢复 | P1/P6/P7 | operation/intent/跨库去重、迟到发送、保存期、取消、补偿和费用闭环 |
| CA4 | TaskGraph/HTN共享分支+证据变化+实际操作+重启+Host完整交付 | P2/P3/P6/P7/P9 | 三链实际贯通；全部专项场景和累计回归有证据；未完成外部依赖明确列出 |

CA1不是把其他两链设成永远True。它使用当前可用来源/执行记录的严格adapter；CA2扩展通用支持，CA3补完整v2操作身份和恢复。相同接口的语义从一开始固定，后续不能把已知未实现约束默认放行。开发顺序可在安全边界内并行；四个切片均为最终必需交付。

### CA1 实施顺序

1. 定位全部完成/审阅入口；冻结旧criterion/input/hash夹具。新Requirements/Review subject/codec与Store迁移一起加入。
2. 将纯公式和准入算法接入真实审阅结果。追加单准则反例、ANY未选路线、强制安全门、漏准则/伪回执、输入变化测试。
3. 在event_handler实际创建ReviewPackage和review job；通过BaseAgent完成独立审阅。保留模型格式修复与内容返工的不同计数。
4. 由ResolutionCommits产生Acceptance和GoalResolution；阶段区分CANDIDATE/OUTCOME/FINAL。接入Closeout，未决危险操作不能完成根。
5. 对当前代码和文档各跑一个完整场景；compound完成需真实TG组合输入；验所有角色预算账户。并行Work/Test不得修改原准则。

### CA2 实施顺序

1. 把source_dependencies接成一个观察适配器，保留其历史归因；加入FactKey、显式正反、Justification admission和生命周期。
2. 实现附录的grounded最小不动点基线；实际source/权限/时效/checker registry产生anchors/rules，不接收模型伪造的布尔gate。
3. 实现dirty epoch、负面读取/插入反证的集合版本、反向支持重算。先保证完整性，再优化局部索引；算法优化须与全量重算等价。
4. 接入HTN applicability、DATA readiness、Acceptance、Context四个消费者；source/tombstone事件走同一失效链。
5. 运行共享菱形、循环、自证、独立替代证据、撤权、过期、时序和跨scope测试。错误观察不等于FALSE。

### CA3 实施顺序

1. 定义Operation occurrence/spec/handoff/cutoff语义；增加只读v1兼容映射。原action ID/key从不重算。
2. 给真实测试connector实现v2能力与typed lookup；在测试端用真实事务保存效果和key，支持可控延迟、丢回执、源版本冲突和key保留期。
3. 扩展ActionExecutor/ActionCommits：handoff前持久化；超时UNKNOWN；延迟receipt落库；仅在可证明安全的条件下同key再交接。
4. 实现结果导入、consumer receipt和晚到事实接收；全部调用收费与UNKNOWN预留按源revision处理，不借新Task重复算费用。
5. 实现取消/补偿/资源fence/恢复gate；单库和跨库真实kill、两个owner竞争、远端旧请求晚到全部有确定结果。

### CA4 实施顺序

1. H0填入真实Host文件/SDK加载信息；检查current TaskGraph/HTN已有接口，缺失列为阻塞。
2. 跑§18全场景，记录所有要求/Method/Input/Review/Evidence/Operation/Receipt版本。
3. 运行全部机械和状态化测试；真Agent/原生UI只使用获准隔离环境和测试预算。
4. 冷启动旧库、新库；新模式恢复先只读核对。复跑已冻结开发样本作为配对后半，benchmark隐藏答案不能交给内部Verifier。
5. 输出实现覆盖、系统正确性、模型效果三张独立表。某项无效或无收益如实列出，不通过换口径关闭需求。

---

## 22. 验收执行方法与明确的判定标准

### 22.1 三种测试证据不能混用

| 证据 | 本次资料包状态 | 能证明什么 |
|---|---|---|
| reference语义/本地测试服务 | 已运行，见日志 | 参考公式、支持闭包、重试条件、局部SQLite协议符合所列断言 |
| 实际SDK集成/迁移/故障矩阵 | **本轮未执行** | 真正代码入口、账本、版本和跨库协议是否正确 |
| 真实模型/真实Host/外部任务 | **本轮未执行** | Agent能否产生有效证据和Review，用户是否得到正确产品结果 |

资料包的SQLite服务是**测试目标系统**，不声称任意邮件/支付/文件系统都有同样的原子效果+幂等记录。抛异常用于快速失败；进程kill用于验证没有finally也能恢复；设备断电/fsync持久性要在目标平台额外测试。

### 22.2 实施后应能执行的测试命令

以下路径是本专项拟新增测试目录；先实现测试，不能对不存在目录运行后改成“跳过通过”。使用实际仓库锁定依赖和现有质量命令，不擅自升级工具链。

```bash
# 资料包参考实现：立即可运行，纯标准库，不需要模型和SDK。
python -m unittest discover -s tests -v

# 下列在SimpleHarness仓库内、实现对应测试后运行。
uv run --frozen --group dev pytest tests/orchestrator/core_assurance/test_acceptance.py
uv run --frozen --group dev pytest tests/orchestrator/core_assurance/test_validity.py
uv run --frozen --group dev pytest tests/orchestrator/core_assurance/test_operations.py
uv run --frozen --group dev pytest tests/orchestrator/core_assurance/test_integration.py
uv run --frozen --group dev pytest tests/orchestrator/core_assurance/test_stateful.py
uv run --frozen --group dev mypy
# 继续实际仓库现有的lint和累计回归，不缩小原mypy/测试范围。
```

采用Hypothesis规则状态机生成：增加支持、撤回、反证、接受、换输入、取消、重交接和重启的组合；每次对比一个简单的全量重算参考。固定失败seed只为复现，不以换seed规避失败。[W07]

对有限成功公式/支持图可穷举小域；对操作状态模型穷举合法和非法转换。统计成功率不是这些机械不变量的豁免：违反一次跨scope读取、未知动作重发、越权或双扣费就要报告失败。

### 22.3 检查位置比PASS数量重要

每个专项案例至少保留：case_id、真实测试函数、SDK commit、源文件位置、输入/产物hash、数据库事件水位、操作次数、费用记录、预期/实际状态。声明“被拒绝”要指出哪层拒绝：codec、预算、支持求值、Commit或外部fence；不能只看到一个异常就算正确。

正向路径同样重要：安全门不能让合法任务全部无法推进。所有拒绝型测试都配合法相近输入；INCONCLUSIVE不等于成功，但也不等于系统错误。

### 22.4 全部66项实际SDK验收规范

下表为**待实施测试**，全部状态`NOT_RUN_SDK`。资料包另外的46项reference测试不用于填这些生产用例的PASS。机器可读版本保留given/when/then、测试目标及原需求ID。

#### A：目标验收与完成

| ID | 给定条件与动作 | 必须断言 | 原需求 |
|---|---|---|---|
| CA-A01 空成功公式 | 新要求含空ALL或ANY；提交要求 | codec拒绝，不生成已满足目标 | R12,R57 |
| CA-A02 悬空或重复准则 | 公式引用未知ID或catalog重复；解析/提交 | 稳定字段错误；不静默忽略 | R12,R57 |
| CA-A03 替代路线不误阻塞 | 合法A或B；A失败B完整通过；最终验收 | 有效B可满足公式；仍核对A的未决动作 | R10,R12,R40 |
| CA-A04 强制约束不被OR绕过 | 一条路线通过，隐私检查FAIL；Reviewer给ACCEPT | Commit拒绝，保留失败和审阅记录 | R12,R28,R50 |
| CA-A05 未知不当成功 | 一个必需检查缺失/UNKNOWN；完成AgentTurn | Review可正常保存，但Task不被接受 | R28,R57 |
| CA-A06 真实检查失败优先 | 代码测试实际失败；Reviewer声称PASS | 不接受；原测试日志/执行身份保留 | R28,R51 |
| CA-A07 审阅与作者隔离 | Worker拥有可写目录，Review绑定独立视图；Reviewer请求写被审文件 | 被拒，不能改完批准自己的版本 | R28,R50 |
| CA-A08 旧产物Review | H1通过，H2被选中；H1 Review迟到 | 计费/保存H1历史但不能批准H2 | R16,R17 |
| CA-A09 要求修订 | r1审阅途中用户合法提交r2；r1 Review返回 | 不解释为r2已满足，未影响成果按显式映射重判 | R44,R17 |
| CA-A10 无关分支变更 | B无输入/准则变化；A提交新计划；接受B Review | 按相关readset允许，不要求全图所有version相同 | R16,R14 |
| CA-A11 准则遗漏与伪回执 | 模型漏准则或引用包外回执；解析review-v2 | 明确报错，有限格式修复计费不补PASS | R28,R57 |
| CA-A12 局部通过组合错误 | 所有孩子有Acceptance但接口不一致；父组合审阅 | 拒绝父Resolution，子历史不删除 | R12,R21 |
| CA-A13 预执行候选 | 报告审阅通过，发送尚未执行；候选和根分别请求接受 | 内容候选可接受；已发送准则仍未满足 | R12,R40 |
| CA-A14 同候选重复接受 | 同包和相同命令被重送；两个提交者并发accept | 单个正式接受/下游消息，回执相同 | R36,R39 |
| CA-A15 不同候选并发通过 | 两个候选独立被接受；争用一个DATA端口 | 按选择CAS只绑定一个；不覆盖另一个历史 | R16,R12 |
| CA-A16 评审预算身份 | Task Critic和Mission Judge各有真实配置；准入/增长预算 | 各走正确账户；Judge不要求伪Worker Attempt | R03,R26,R57 |
| CA-A17 Mission收敛门 | 根内容通过，弃用分支仍有未知写操作；请求完成Mission | 显示收尾/核对，不能悄悄完成 | R40,R59 |
| CA-A18 终态后查询回执 | 相同成功命令在Mission终态后重送；重试 | 当前读取授权允许时返回旧回执，不创建新任务 | R36,R39 |
| CA-A19 已删除敏感依据 | 旧Acceptance存在但原证据依法定/用户政策删除；重新读/复验 | 保留非敏感事实和限制，不补造原文 | R17,R60 |
| CA-A20 非法类型组合 | 把MissionJudge对象传入TaskCritic函数；静态检查+边界解析 | 预期类型错误/归属拒绝，非缺依赖假失败 | R03,R57 |

#### K：条件知识与有效性

| ID | 给定条件与动作 | 必须断言 | 原需求 |
|---|---|---|---|
| CA-K01 缺观察不是否定 | Fact无证据；查询 | UNKNOWN，不是FALSE | R06 |
| CA-K02 显式反证 | 有可信负literal无正literal；求值 | FALSE并给出负证据，不借未命中生成 | R06 |
| CA-K03 冲突不爆炸 | 同命题正负皆有；求值无关命题 | 当前命题CONFLICT，无关项仍UNKNOWN | R06,R29 |
| CA-K04 AND支持不全 | K需要A和B，仅A成立；读取K | 不成立/UNKNOWN；不可用 | R29 |
| CA-K05 OR独立支持 | K由A+B或C支持，A撤回C仍有效；失效重算 | K可用C见证；不重做无关Worker | R29,R30 |
| CA-K06 重复来源不算独立 | 同实验报告两个格式；需要两份独立证据 | 不满足独立性要求 | R28,R29 |
| CA-K07 无锚循环 | A引用B，B引用A，无外部锚；重算 | 两者不自证TRUE | R29 |
| CA-K08 撤回循环锚 | 有锚时A/B成立，后来唯一锚撤回；重算 | 从当前锚重算，不能从旧TRUE续算 | R30,R29 |
| CA-K09 冲突路径另有替代 | A冲突且A可推K，另有独立C推K；用途求值 | 按无冲突见证C使用，A不用于强制门 | R29,R30 |
| CA-K10 规则不是引用列表 | Worker任意声称A推出B；写Justification | 无合法RuleAdmission/独立Review不得启用 | R28,R29 |
| CA-K11 新增反证的幻读 | Review读到无负证据，随后插入反证；接受旧readset | 集合epoch冲突并重判，不能只查旧正证据版本 | R16,R30 |
| CA-K12 撤回与异步缓存 | 源撤回已Commit，旧缓存还显示CURRENT；handoff/Context/accept | dirty gate立即阻断或最新同步重算 | R30,R34 |
| CA-K13 过期但定时器迟到 | 证据valid_to已过，到期事件尚未调度；实际使用 | 按now拒绝当前用途；后续事件便于重放 | R06,R30 |
| CA-K14 START与ACCEPT不同 | 开始前提库存足够，操作后库存减少；验收历史开始见证 | 不抹掉START；若用户要求当前值另行取证 | R06,R17 |
| CA-K15 权限与真值分开 | 事实仍有证据但Agent读权限撤回；检索/验证 | 禁止披露；不把事实写FALSE | R34,R50 |
| CA-K16 来源声称与现实 | 来源写提升40%，引文核验通过；Claim请求现实VERIFIED | 只确认source_says；实测主张另需证据 | R28,R29 |
| CA-K17 范围/版本不同 | 同名命题来自不同对象/语料版本；查找/共享 | 不合并不同FactKey，不复用不匹配Acceptance | R06,R11 |
| CA-K18 不完整依赖覆盖 | 一次工具读取未记录可追溯来源；影响分析 | 标记coverage不足并保守复审，不称精确最小影响 | R30,R51 |

#### X：操作一致性与恢复

| ID | 给定条件与动作 | 必须断言 | 原需求 |
|---|---|---|---|
| CA-X01 同操作跨Attempt | 同授权occurrence换方法/Agent；再次提出相同操作 | 同Operation与外部key，避免重复应用 | R03,R40 |
| CA-X02 合法第二次同目标 | 同Mission用户明确新意图再次发送；创建新occurrence | 不同Operation；不被旧target key合并 | R03,R40 |
| CA-X03 同key不同参数 | H1已封存handoff；用同key发H2 | 拒绝；不能覆盖原回执 | R40,R57 |
| CA-X04 效果发生丢回执 | 目标已事务提交，进程退出；恢复核对 | 读取原回执，一次应用且正确结算 | R38,R40 |
| CA-X05 handoff后延迟到达 | 旧请求排队中，lookup暂时无结果；新owner恢复 | 保持UNKNOWN；不以瞬时not found重发 | R40 |
| CA-X06 幂等保存期耗尽 | key记录可能被清理；lookup空 | 不自动再发；不能证明未发生 | R40 |
| CA-X07 旧writer仍活 | lease过期但旧线程/进程未停；替代执行开始 | 无真实fence不能冲突新handoff | R16,R40,R49 |
| CA-X08 终局未应用后重试 | 证明覆盖handoff1且它无法再执行；当前授权允许handoff2 | 同key/spec安全再发；新成功不与旧未执行矛盾 | R40 |
| CA-X09 查询权威不足 | best_effort查不到；reconcile | UNKNOWN而不是NOT_APPLIED_FINAL | R40,R48 |
| CA-X10 取消后迟到APPLIED | 用户取消后原操作实际成功回执晚到；导入事实 | 保存真实后果/费用，不授权新动作 | R38,R40 |
| CA-X11 矛盾权威收据 | 相同交接范围收到APPLIED和最终未应用；合并 | quarantine/conflict，无last-write-wins | R40,R57 |
| CA-X12 补偿失败 | 原操作成功，独立获批补偿；补偿被拒/超时 | 原成功不删；补偿独立失败/UNKNOWN记录 | R41 |
| CA-X13 审批绑定过期 | 批准H1，参数或需求变H2；handoff | 原批准不能用于H2，重新审批 | R40,R44,R50 |
| CA-X14 SDK提交编排丢回执 | SDK已创建Turn且保存输入；编排重启 | 原creation_key/input_id查回，不创建新工作 | R39 |
| CA-X15 结果导入重复 | 执行源同revision事实重送；重复消费 | 业务/费用导入一次；不同hash冲突 | R39,R51 |
| CA-X16 未知费用后确认 | 调用UNKNOWN，无可信用量，之后结算；更新源revision | 保留未知预留到可信结算，不提前记0占死消费键 | R25,R26,R39 |
| CA-X17 观察失败不是不存在 | AgentBridge读取临时数据库失败；恢复调度 | ObservationUnavailable，不能生成新Attempt | R38,R39,R57 |
| CA-X18 过期通知worker确认 | 消息租约换代，旧worker返回ack；确认 | 不能确认新claim；相同消费者业务事实仍去重 | R16,R45 |

#### I：三链集成与兼容

| ID | 给定条件与动作 | 必须断言 | 原需求 |
|---|---|---|---|
| CA-I01 全部三链集成 | 真实TG共享分支+多支持证据+受控发布；执行§18流程含kill | 版本/接受/真实效果/费用/通知一致 | R12,R30,R39,R40 |
| CA-I02 compound不伪执行 | 实际HTN Method含两个AND孩子；启动完整Mission | 不创建假父Worker；实际组合审阅和GoalResolution | R04,R12 |
| CA-I03 相同scope多Manager | 两Manager同时修改支持和Task绑定；并发Commit | 相关冲突被发现，不相关安全rebase成立 | R16,R36 |
| CA-I04 安全注入跨共享 | 不可信文档含改权限指令，经摘要/Knowledge传递；下游工具调用 | 引用始终是数据，权限和操作key不可模型控制 | R34,R50 |
| CA-I05 真实UI与坏消息 | 后端仍评审时前端收到坏消息；刷新/重连 | 保留stale画面，不能显示完成或业务UNKNOWN | R57,R58 |
| CA-I06 旧库冷恢复 | 旧Mission/intent/hash/receipt固定夹具；升级后只读/恢复 | 字节与身份不变，legacy规则不被猜成v2 | R17,R38,R57 |
| CA-I07 新投影重建 | 保留事件/CAS/受控基线，清除本专项派生表；Replay | 重建相同事实，无模型/工具调用；coverage边界明确 | R37,R38 |
| CA-I08 跨库备份缺资产 | 备份中遗漏执行回执或CAS；恢复 | 禁新副作用并报告缺口，不重发弥补 | R38,R39,R60 |
| CA-I09 删除后恢复备份 | 旧快照含已删除证据；应用tombstone恢复 | 不复活正文/索引/缓存，历史限制可见 | R60 |
| CA-I10 外部评分器隔离 | 开发集四臂/真实外部任务；系统内部评审 | 看不到隐藏答案；错误完成由外部评分独立判定 | R28,R52 |


---

## 23. 兼容、停止条件与禁止捷径

### 23.1 模式选择与旧行为保护

- 保留`CriticVerdict`、AssessmentBindingV1、旧source_current_issues、旧action ID/key、旧replay-v2覆盖定义。新包具有明确schema和语义版本，不能缺字段就默认“新最严格模式”。
- 新操作绑定到旧actions执行投影，不另起全局operation状态writer。既有已HANDOFF动作只能由其冻结协议核对；不自动换ConnectorV2去重key重新发。
- 修改接口时共享Schema只暴露公开字段。敏感receipt、执行token、费用内核数据不因出现在新对象中就全部送UI。
- 不改变旧真实记录证明范围：旧测试通过仅有旧检查证据；迁移时没有的新criterion/支持集/责任字段，注明legacy或做新的验收，不能追造。
- 新规范若与已合入TaskGraph冲突，先写有具体字段/不变量的ADR并修改唯一合同。不能在两个模块里各自“兼容”不同解释。

### 23.2 允许停止但不允许冒充完工

允许明确返回：`REVIEW_INPUT_INVALID`、`EVIDENCE_UNAVAILABLE`、`INCONCLUSIVE`、`SOURCE_REVOKED`、`OBSERVATION_UNAVAILABLE`、`OPERATION_STILL_UNKNOWN`、`RECEIPT_CONFLICT`、`CONNECTOR_CAPABILITY_UNSUPPORTED`、`RESTORE_INCOMPLETE`。每项带scope和可恢复建议，不混作同一个“FAILED重试”。

预算不足时不启动必然没有钱收尾的分支；查不到事实不等于假；测试超时不等于实现错误；取消不等于未发生；有输出不等于已接受。所有这些区分都要进入代码类型、状态投影和测试，而不仅在Prompt里提醒。

禁止：所有等待一律UNKNOWN；所有异常一律LOST；把旧结果丢弃就不算费用；把反证删除换成功；把compensation当撤销历史；自定义外部评分器以吻合当前结果；mock掉HTN/账本后说全链通过。

---

## 24. 给实施 Agent 的交付清单与开工指令

### 24.1 开工时先提交的两份记录

`implementation/source-map.json`：当前SDK/Host HEAD，dirty文件摘要；每个本文拟新增模块的实际复用/新增/移动位置；当前Schema与新迁移号；所有完成/知识使用/handoff入口覆盖。

`implementation/decision-log.md`：本文已决定的语义、与实际代码差异、所需明确兼容策略。必须列出NOT_APPLIED_FINAL覆盖水位、Requirements修订权限、Justification准入、当前性dirty门、Root收尾和旧模式不变规则。不能让这些变成模型临时自由发挥。

### 24.2 每个切片的完成记录

包括：修改代码/测试函数、输入/输出合同、迁移前后证据、行为和负控结果、恢复切点结果、未完成跨包依赖、风险。不得用“类型通过”替代真实数据库行为；不得用“reference通过”替代SDK；不得用“能启动UI”替代真实用户路径。

### 24.3 可直接交给实施 Agent 的任务文本

```text
按CORE-ASSURANCE-1.0实施，不修改原60项需求与P1–P9范围。
先读取最新完整计划、TaskGraph专项和当前checkout；保护用户未提交修改。
完成source-map与兼容决定，然后按CA1→CA2→CA3→CA4实施；允许不破坏接口的并行。
所有新语义只能经现有Commit、BaseAgent、Provider/Effect ledger和ActionExecutor执行。
参考代码仅用于语义对照；集成时把身份/权限/证据/预算的bool前提替换成真实查询和检查。
不要创建第二套Goal服务、预算账本、Agent内核或绕过Gateway的连接器调用。
不要默认启用高风险真实环境；用获准测试目标跑fault matrix。
每次切片提交实际测试与累计回归，不能改弱失败断言、删必需测试、使用skip作为PASS。
缺Host/HTN/真实adapter时报告明确被阻塞验收，保留其必需状态；不得称MVP已替代完整目标。
最终输出：实现覆盖、系统正确性、真实模型效果三个独立报告，以及复现命令与来源证据。
```

### 24.4 本资料包自身验证范围

本轮已实际运行 **46项参考测试**：包括纯语义、两个SQLite服务场景、DDL/复合外键和交接观察epoch检查；全部通过。另完成资料映射、Python语法、JSON边界样例和DDL语法自检。完整日志在资料包。**66项真实SDK验收均未执行。**

reference的公式、有限证据闭包、重试门、同epoch收据合并使用标准库；附加SQLite测试服务在真实子进程中模拟“外部事务COMMIT后、回执返回前退出”。这说明参考行为符合指定断言，不说明任意外部服务有同等保证。

`schema/assurance.sql`仅验证在本地SQLite的可解析性和测试外键；生产Store已有表、迁移checksum、并发写者和平台锁仍须真正集成后验证。备份恢复测试不能只拿此空库代替用户数据库。

全量范围映射见下表与`implementation/requirements-map.json`。本专项贡献到某R项不代表该项全部完成；尤其R37完整业务重建、R47形式化执行器、R49所有平台和R52外部效果都保留原全量验收。

| 原需求 | 原名称 | 本专项用例/范围 |
|---|---|---|
| R03 | Task、Attempt、AgentTurn、外部操作身份分离 | CA-A16, CA-A20, CA-X01, CA-X02 |
| R04 | 复合/原子Task语义，不把抽象目标当Worker工作直接派发 | CA-I02 |
| R06 | 世界状态区分已知真/假/未知/有争议 | CA-K01, CA-K02, CA-K03, CA-K13, CA-K14, CA-K17 |
| R10 | AND子成果与OR方法路线分离 | CA-A03 |
| R11 | 共享子目标及适用性证明 | CA-K17 |
| R12 | 父目标组合判据与显式GoalResolution | CA-A01, CA-A02, CA-A03, CA-A04, CA-A12, CA-A13, CA-A15, CA-I01, CA-I02 |
| R14 | 局部计划修复，尽量保留有效执行与产物 | CA-A10 |
| R16 | 计划读集、原子激活、在途工作generation | CA-A08, CA-A10, CA-A15, CA-K11, CA-X07, CA-X18, CA-I03 |
| R17 | 完成历史不改写，当前验收有效性可失效 | CA-A08, CA-A09, CA-A19, CA-K14, CA-I06 |
| R21 | 失败片段复用与跨方法综合再验证 | CA-A12 |
| R25 | 稳定Obligation阻止拆分/改名/换角色刷重试与预算 | CA-X16 |
| R26 | 现实Provider容量、上下文上界与预算尾部一致 | CA-A16, CA-X16 |
| R28 | 独立语义Verifier与结构/权限检查分工 | CA-A04, CA-A05, CA-A06, CA-A07, CA-A11, CA-K06, CA-K10, CA-K16, CA-I10 |
| R29 | 条件化Knowledge及多重Justification | CA-K03, CA-K04, CA-K05, CA-K06, CA-K07, CA-K08, CA-K09, CA-K10, CA-K16 |
| R30 | 依赖失效贯通知识、摘要、方法和验收 | CA-K05, CA-K08, CA-K09, CA-K11, CA-K12, CA-K13, CA-K18, CA-I01 |
| R34 | Context按scope/用途隔离，已冻结请求不可重组 | CA-K12, CA-K15, CA-I04 |
| R36 | Proposal/Commit唯一逻辑写入 | CA-A14, CA-A18, CA-I03 |
| R37 | 所有关键业务投影可由事件与受控基线重建 | CA-I07 |
| R38 | 重放不执行工具，恢复不复活租约或已批准旧权限 | CA-X04, CA-X10, CA-X17, CA-I06, CA-I07, CA-I08 |
| R39 | execution/orchestrator双库有可靠收据与去重 | CA-A14, CA-A18, CA-X14, CA-X15, CA-X16, CA-X17, CA-I01, CA-I08 |
| R40 | 外部Operation跨Attempt稳定、UNKNOWN先核对 | CA-A03, CA-A13, CA-A17, CA-X01, CA-X02, CA-X03, CA-X04, CA-X05, CA-X06, CA-X07, CA-X08, CA-X09, CA-X10, CA-X11, CA-X13, CA-I01 |
| R41 | 补偿是独立授权任务，不是回滚模型搜索树 | CA-X12 |
| R44 | 要求版本变更与独立重新验收 | CA-A09, CA-X13 |
| R45 | 单主助手跨Mission通知Outbox与收执 | CA-X18 |
| R48 | 能力注册/可达/健康/授权彼此分开 | CA-X09 |
| R49 | Windows/Linux/macOS执行语义隔离 | CA-X07 |
| R50 | 最小权限、证据来源与提示注入隔离 | CA-A04, CA-A07, CA-K15, CA-X13, CA-I04 |
| R51 | 完整Trace/贡献链/版本/费用归因 | CA-A06, CA-K18, CA-X15 |
| R52 | 独立外部评测而非内部Verifier自报 | CA-I10 |
| R55 | 用户长期记忆保持当前排除范围 | 范围约束：保留，不引入额外实现 |
| R56 | Workflow仍是工具层可调用能力 | 范围约束：保留，不引入额外实现 |
| R57 | 关键契约类型/边界校验/事务归属检查 | CA-A01, CA-A02, CA-A05, CA-A11, CA-A16, CA-A20, CA-X03, CA-X11, CA-X17, CA-I05, CA-I06 |
| R58 | Host与UI共享协议/后端唯一状态投影 | CA-I05 |
| R59 | 停止、反复重规划、死锁和饥饿治理 | CA-A17 |
| R60 | 隐私、冷热归档、恢复manifest与删除不复活 | CA-A19, CA-I08, CA-I09 |

---

## 附录 A：可运行纯语义参考代码

此代码的输入是**已经过真实身份、权限、来源与版本检查的快照**。集成时必须实现前置查询；不能由模型/前端填写`authorized=True`或`binding_current=True`。参考不替代事务、运行时Schema、外部服务契约或完整HTN。

```python
"""Executable semantic reference, NOT a production SDK patch.

Inputs are already schema-validated, authorized snapshots. The real application
must validate identity, scope, receipt provenance and OCC in its CommitService.
No model calls, database writes or side effects occur in this module.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


class ContractError(ValueError):
    pass


class Verdict(StrEnum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True, slots=True)
class CriterionRef:
    criterion_id: str


@dataclass(frozen=True, slots=True)
class AllOf:
    children: tuple[Expr, ...]

    def __post_init__(self) -> None:
        if not self.children:
            raise ContractError('empty all_of is not a completion contract')


@dataclass(frozen=True, slots=True)
class AnyOf:
    children: tuple[Expr, ...]

    def __post_init__(self) -> None:
        if not self.children:
            raise ContractError('empty any_of is not a completion contract')


Expr: TypeAlias = CriterionRef | AllOf | AnyOf


def referenced_criteria(expr: Expr) -> frozenset[str]:
    if isinstance(expr, CriterionRef):
        if not expr.criterion_id:
            raise ContractError('empty criterion identity')
        return frozenset((expr.criterion_id,))
    return frozenset().union(*(referenced_criteria(c) for c in expr.children))


def evaluate_formula(expr: Expr, values: Mapping[str, Verdict]) -> Verdict:
    """Three-valued *completion* formula. Not the four-valued knowledge algebra.

    Missing observations are UNKNOWN; unknown criterion definitions are rejected
    earlier by the contract codec. Limits on AST depth/size belong to that codec.
    """
    if isinstance(expr, CriterionRef):
        return values.get(expr.criterion_id, Verdict.UNKNOWN)
    results = tuple(evaluate_formula(c, values) for c in expr.children)
    if isinstance(expr, AllOf):
        if Verdict.FAIL in results:
            return Verdict.FAIL
        return Verdict.PASS if all(v is Verdict.PASS for v in results) else Verdict.UNKNOWN
    if Verdict.PASS in results:
        return Verdict.PASS
    return Verdict.FAIL if all(v is Verdict.FAIL for v in results) else Verdict.UNKNOWN


class ReviewDecision(StrEnum):
    ACCEPT = 'ACCEPT'
    REWORK = 'REWORK'
    INCONCLUSIVE = 'INCONCLUSIVE'
    REJECTED = 'REJECTED'


@dataclass(frozen=True, slots=True)
class AcceptanceInput:
    formula: Expr
    mandatory_checks: frozenset[str]
    verdicts: Mapping[str, Verdict]
    reviewer: ReviewDecision
    binding_current: bool
    evidence_current: bool
    operationally_settled: bool


@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    allowed: bool
    reason_codes: tuple[str, ...]


def decide_acceptance(value: AcceptanceInput) -> AcceptanceDecision:
    """A reviewer ACCEPT is necessary but cannot override explicit failed gates."""
    reasons: list[str] = []
    if not value.binding_current:
        reasons.append('BINDING_CHANGED')
    if not value.evidence_current:
        reasons.append('EVIDENCE_NOT_CURRENT')
    if not value.operationally_settled:
        reasons.append('OPERATIONS_UNSETTLED')
    missing = sorted(k for k in value.mandatory_checks if value.verdicts.get(k) is not Verdict.PASS)
    reasons.extend(f'MANDATORY_CHECK_NOT_PASS:{k}' for k in missing)
    if evaluate_formula(value.formula, value.verdicts) is not Verdict.PASS:
        reasons.append('SUCCESS_FORMULA_NOT_PASS')
    if value.reviewer is not ReviewDecision.ACCEPT:
        reasons.append(f'REVIEW_{value.reviewer.value}')
    return AcceptanceDecision(not reasons, tuple(reasons))


class TruthValue(StrEnum):
    TRUE = 'TRUE'
    FALSE = 'FALSE'
    UNKNOWN = 'UNKNOWN'
    CONFLICT = 'CONFLICT'


@dataclass(frozen=True, slots=True, order=True)
class Literal:
    atom: str
    positive: bool


@dataclass(frozen=True, slots=True)
class SupportRule:
    """A separately admitted inference/Review, not just an arbitrary citation list.

    Premises are AND; different rules with the same conclusion are OR. Negative
    premises mean explicit negative evidence, NEVER 'not found'.
    """
    rule_id: str
    conclusion: Literal
    premises: frozenset[Literal]
    admission_current: bool = True

    def __post_init__(self) -> None:
        if not self.rule_id or not self.premises:
            raise ContractError('rules require identity and nonempty premises; use anchors for facts')


@dataclass(frozen=True, slots=True)
class SupportClosure:
    supported: frozenset[Literal]
    productive_rules: frozenset[str]
    witness_rank: Mapping[Literal, int]


def derive_support(anchors: frozenset[Literal], rules: tuple[SupportRule, ...]) -> SupportClosure:
    """Least fixed point over finite *grounded* signed atoms.

    Recompute from CURRENT anchors, never seed from previously VERIFIED derived
    nodes: this eliminates unanchored cycles after an anchor is revoked.
    Authorization, source lifetimes and inference admission were filtered before
    this function. It does not certify natural-language entailment.
    """
    ids = [r.rule_id for r in rules]
    if len(ids) != len(set(ids)):
        raise ContractError('duplicate support rule identity')
    supported = set(anchors)
    rank: dict[Literal, int] = {lit: 0 for lit in anchors}
    ordered = sorted(rules, key=lambda r: r.rule_id)
    while True:
        prior = frozenset(supported)
        additions: dict[Literal, int] = {}
        for rule in ordered:
            if not rule.admission_current or not rule.premises.issubset(prior):
                continue
            if rule.conclusion not in prior:
                depth = 1 + max(rank[p] for p in rule.premises)
                additions[rule.conclusion] = min(depth, additions.get(rule.conclusion, depth))
        if not additions:
            break
        supported.update(additions)
        rank.update(additions)
    productive = frozenset(r.rule_id for r in rules if r.admission_current and r.premises.issubset(supported))
    return SupportClosure(frozenset(supported), productive, rank)


def truth_of(atom: str, closure: SupportClosure) -> TruthValue:
    pos = Literal(atom, True) in closure.supported
    neg = Literal(atom, False) in closure.supported
    if pos and neg:
        return TruthValue.CONFLICT
    if pos:
        return TruthValue.TRUE
    if neg:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN


def currently_usable(
    truth: TruthValue, *, fresh: bool, authorized: bool,
    assurance_satisfied: bool, projection_current: bool,
) -> bool:
    """Sensitive execution preconditions: exploratory reading has a different policy."""
    return (truth is TruthValue.TRUE and fresh and authorized
            and assurance_satisfied and projection_current)


class LookupKind(StrEnum):
    APPLIED = 'APPLIED'
    NOT_APPLIED_FINAL = 'NOT_APPLIED_FINAL'
    IN_PROGRESS = 'IN_PROGRESS'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True, slots=True)
class RetryInput:
    lookup: LookupKind
    binding_matches: bool
    authoritative: bool
    delayed_delivery_excluded: bool
    key_retention_valid: bool
    previous_sender_quiescent: bool
    current_approval: bool
    current_generation: bool
    budget_available: bool
    retry_limit_available: bool
    cancellation_requested: bool = False


def may_rehandoff(value: RetryInput) -> bool:
    """Conservative re-handoff: same frozen payload and SAME external key only.

    A momentary 'not found', lease expiry or expired idempotency history cannot
    establish NOT_APPLIED_FINAL. Enforcing adapter semantics is mandatory.
    """
    return (
        value.lookup is LookupKind.NOT_APPLIED_FINAL
        and value.binding_matches and value.authoritative
        and value.delayed_delivery_excluded and value.key_retention_valid
        and value.previous_sender_quiescent and value.current_approval
        and value.current_generation and value.budget_available
        and value.retry_limit_available and not value.cancellation_requested
    )


class OperationFact(StrEnum):
    UNKNOWN = 'UNKNOWN'
    APPLIED = 'APPLIED'
    NOT_APPLIED_FINAL = 'NOT_APPLIED_FINAL'
    CONFLICT = 'CONFLICT'


def merge_operation_fact(old: OperationFact, new: OperationFact) -> OperationFact:
    """Only for compatible observations of ONE handoff/observation epoch.

    A final-not-applied proof covers attempts through a recorded cutoff. A later
    authorized handoff may apply successfully; its observation belongs to another
    epoch and MUST NOT be merged here as a contradiction of the earlier proof.

    Receipt authenticity, adapter epoch and binding checks occur before this pure
    function. Conflicting authoritative receipts are quarantined, not last-write-wins.
    """
    if old is OperationFact.CONFLICT or new is OperationFact.CONFLICT:
        return OperationFact.CONFLICT
    if new is OperationFact.UNKNOWN:
        return old
    if old is OperationFact.UNKNOWN or old is new:
        return new
    return OperationFact.CONFLICT


def conflict_free_closure(anchors: frozenset[Literal], rules: tuple[SupportRule, ...]) -> SupportClosure:
    """A stricter witness projection for acceptance/action use.

    Full paraconsistent truth is retained elsewhere. Conflicted premises do not
    authorize actions, while an independent conflict-free support path can survive.
    This policy does not upgrade the assurance of any inference.
    """
    full = derive_support(anchors, rules)
    conflicted = frozenset(lit.atom for lit in full.supported
                          if Literal(lit.atom, not lit.positive) in full.supported)
    safe_anchors = frozenset(lit for lit in anchors if lit.atom not in conflicted)
    safe_rules = tuple(r for r in rules if r.conclusion.atom not in conflicted
                       and all(p.atom not in conflicted for p in r.premises))
    return derive_support(safe_anchors, safe_rules)

```

## 附录 B：完整SQL语法原型

拟议DDL，合入前按§19与实际TaskGraph表合并。不可把原型表存在视为生产完整性已证明。

```sql
-- Integration DDL proposal. NOT an existing production migration.
-- Merge with equivalent TaskGraph tables; append ONE fixed migration in schema.py.
-- This file never changes existing actions, dispatch_intents or execution tables.
PRAGMA foreign_keys=ON;
CREATE TABLE requirements_revisions (
 revision_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 parent_revision_id TEXT, content_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 authority_receipt_ref TEXT NOT NULL, created_at_us INTEGER NOT NULL,
 UNIQUE(tenant_id, mission_id, revision_id),
 FOREIGN KEY(tenant_id,mission_id,parent_revision_id)
 REFERENCES requirements_revisions(tenant_id,mission_id,revision_id)
) STRICT;
CREATE TABLE requirements_heads (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 record_version INTEGER NOT NULL CHECK(record_version>0),
 PRIMARY KEY(tenant_id,mission_id),
 FOREIGN KEY(tenant_id,mission_id,revision_id)
 REFERENCES requirements_revisions(tenant_id,mission_id,revision_id)
) STRICT;
CREATE TABLE review_packages (
 package_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 package_hash TEXT NOT NULL, requirements_revision_id TEXT NOT NULL,
 subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
 subject_binding_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at_us INTEGER NOT NULL, UNIQUE(tenant_id,mission_id,package_id),
 UNIQUE(tenant_id,mission_id,package_hash),
 FOREIGN KEY(tenant_id,mission_id,requirements_revision_id)
 REFERENCES requirements_revisions(tenant_id,mission_id,revision_id)
) STRICT;
CREATE TABLE review_jobs (
 review_job_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 package_id TEXT NOT NULL, dispatch_intent_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN('PREPARED','DISPATCHED','RECORDED','BLOCKED','CANCELLED')),
 record_version INTEGER NOT NULL CHECK(record_version>0),
 UNIQUE(tenant_id,mission_id,review_job_id),
 FOREIGN KEY(tenant_id,mission_id,package_id)
 REFERENCES review_packages(tenant_id,mission_id,package_id)
) STRICT;
CREATE TABLE review_records (
 review_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 package_id TEXT NOT NULL, review_job_id TEXT NOT NULL,
 source_execution_key TEXT NOT NULL, body_hash TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN('ACCEPT','REWORK','INCONCLUSIVE','REJECTED')),
 payload_json TEXT NOT NULL, created_at_us INTEGER NOT NULL,
 UNIQUE(tenant_id,mission_id,review_id), UNIQUE(source_execution_key),
 FOREIGN KEY(tenant_id,mission_id,review_job_id)
 REFERENCES review_jobs(tenant_id,mission_id,review_job_id),
 FOREIGN KEY(tenant_id,mission_id,package_id)
 REFERENCES review_packages(tenant_id,mission_id,package_id)
) STRICT;
CREATE TABLE acceptances (
 acceptance_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 review_id TEXT NOT NULL, subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
 subject_binding_hash TEXT NOT NULL, acceptance_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL, commit_receipt_ref TEXT NOT NULL, accepted_at_us INTEGER NOT NULL,
 UNIQUE(tenant_id,mission_id,acceptance_id), UNIQUE(tenant_id,mission_id,acceptance_hash),
 FOREIGN KEY(tenant_id,mission_id,review_id)
 REFERENCES review_records(tenant_id,mission_id,review_id)
) STRICT;
CREATE TABLE goal_resolutions (
 resolution_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 task_id TEXT NOT NULL, obligation_id TEXT NOT NULL, requirements_revision_id TEXT NOT NULL,
 acceptance_id TEXT NOT NULL, binding_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at_us INTEGER NOT NULL, UNIQUE(tenant_id,mission_id,resolution_id),
 UNIQUE(tenant_id,mission_id,task_id,requirements_revision_id,binding_hash),
 FOREIGN KEY(tenant_id,mission_id,acceptance_id)
 REFERENCES acceptances(tenant_id,mission_id,acceptance_id),
 FOREIGN KEY(tenant_id,mission_id,requirements_revision_id)
 REFERENCES requirements_revisions(tenant_id,mission_id,revision_id)
) STRICT;
CREATE TABLE evidence_records (
 evidence_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 kind TEXT NOT NULL, source_identity TEXT NOT NULL, content_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL, observed_at_us INTEGER NOT NULL,
 UNIQUE(tenant_id,mission_id,evidence_id),
 UNIQUE(tenant_id,mission_id,kind,source_identity,content_hash)
) STRICT;
CREATE TABLE fact_observations (
 observation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 fact_key TEXT NOT NULL, polarity INTEGER NOT NULL CHECK(polarity IN(0,1)),
 evidence_id TEXT NOT NULL, valid_from_us INTEGER NOT NULL, valid_to_us INTEGER,
 payload_json TEXT NOT NULL,
 CHECK(valid_to_us IS NULL OR valid_to_us>valid_from_us),
 FOREIGN KEY(tenant_id,mission_id,evidence_id)
 REFERENCES evidence_records(tenant_id,mission_id,evidence_id)
) STRICT;
CREATE INDEX fact_candidates ON fact_observations(tenant_id,mission_id,fact_key,polarity);
CREATE TABLE justification_sets (
 set_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 conclusion_key TEXT NOT NULL, conclusion_polarity INTEGER NOT NULL CHECK(conclusion_polarity IN(0,1)),
 admission_ref TEXT NOT NULL, body_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(tenant_id,mission_id,set_id)
) STRICT;
CREATE TABLE justification_members (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, set_id TEXT NOT NULL,
 premise_key TEXT NOT NULL, premise_polarity INTEGER NOT NULL CHECK(premise_polarity IN(0,1)),
 purpose TEXT NOT NULL, PRIMARY KEY(tenant_id,mission_id,set_id,premise_key,premise_polarity,purpose),
 FOREIGN KEY(tenant_id,mission_id,set_id)
 REFERENCES justification_sets(tenant_id,mission_id,set_id)
) STRICT;
CREATE INDEX support_reverse ON justification_members(tenant_id,mission_id,premise_key);
CREATE TABLE validity_partitions (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, partition_key TEXT NOT NULL,
 evidence_epoch INTEGER NOT NULL CHECK(evidence_epoch>=0),
 PRIMARY KEY(tenant_id,mission_id,partition_key)
) STRICT;
CREATE TABLE validity_evaluations (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, subject_key TEXT NOT NULL, use_key TEXT NOT NULL,
 truth TEXT NOT NULL CHECK(truth IN('TRUE','FALSE','UNKNOWN','CONFLICT')),
 currentness TEXT NOT NULL CHECK(currentness IN('CURRENT','STALE','REVOKED')),
 read_set_json TEXT NOT NULL, payload_json TEXT NOT NULL, evaluated_at_us INTEGER NOT NULL,
 next_expiry_us INTEGER, record_version INTEGER NOT NULL CHECK(record_version>0),
 PRIMARY KEY(tenant_id,mission_id,subject_key,use_key)
) STRICT;
CREATE TABLE validity_dependencies (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, consumer_key TEXT NOT NULL,
 dependency_key TEXT NOT NULL, role TEXT NOT NULL,
 PRIMARY KEY(tenant_id,mission_id,consumer_key,dependency_key,role)
) STRICT;
CREATE INDEX validity_reverse ON validity_dependencies(tenant_id,mission_id,dependency_key);
CREATE TABLE operation_occurrences (
 operation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 authorized_intent_occurrence TEXT NOT NULL, connector_identity TEXT NOT NULL,
 operation_kind TEXT NOT NULL, obligation_id TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(tenant_id,mission_id,operation_id),
 UNIQUE(tenant_id,authorized_intent_occurrence,connector_identity,operation_kind)
) STRICT;
CREATE TABLE operation_bindings (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, operation_id TEXT NOT NULL,
 spec_revision INTEGER NOT NULL CHECK(spec_revision>0), spec_hash TEXT NOT NULL,
 action_key TEXT NOT NULL UNIQUE, external_idempotency_key TEXT NOT NULL UNIQUE,
 payload_json TEXT NOT NULL,
 PRIMARY KEY(tenant_id,mission_id,operation_id,spec_revision),
 FOREIGN KEY(tenant_id,mission_id,operation_id)
 REFERENCES operation_occurrences(tenant_id,mission_id,operation_id)
) STRICT;
CREATE TABLE operation_observations (
 observation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 operation_id TEXT NOT NULL, spec_revision INTEGER NOT NULL,
 source_execution_key TEXT NOT NULL, source_revision INTEGER NOT NULL,
 body_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(source_execution_key,source_revision),
 FOREIGN KEY(tenant_id,mission_id,operation_id,spec_revision)
 REFERENCES operation_bindings(tenant_id,mission_id,operation_id,spec_revision)
) STRICT;
CREATE TABLE domain_outbox (
 message_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 destination TEXT NOT NULL, dedupe_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN('READY','CLAIMED','ACKED','BLOCKED')),
 lease_owner TEXT, lease_until_us INTEGER, generation INTEGER NOT NULL CHECK(generation>=0),
 attempts INTEGER NOT NULL CHECK(attempts>=0), next_attempt_us INTEGER NOT NULL,
 UNIQUE(destination,dedupe_key)
) STRICT;
CREATE INDEX outbox_due ON domain_outbox(state,next_attempt_us);
CREATE TABLE consumer_receipts (
 consumer TEXT NOT NULL, message_id TEXT NOT NULL, input_hash TEXT NOT NULL,
 output_receipt_ref TEXT NOT NULL, applied_at_us INTEGER NOT NULL,
 PRIMARY KEY(consumer,message_id)
) STRICT;
CREATE TABLE mission_closeouts (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, resolution_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('NOT_READY','DRAINING','BLOCKED_UNKNOWN','READY','FINALIZED')),
 read_set_json TEXT NOT NULL, payload_json TEXT NOT NULL,
 record_version INTEGER NOT NULL CHECK(record_version>0),
 PRIMARY KEY(tenant_id,mission_id),
 FOREIGN KEY(tenant_id,mission_id,resolution_id)
 REFERENCES goal_resolutions(tenant_id,mission_id,resolution_id)
) STRICT;

-- Current lifecycle is distinct from immutable evidence/Justification bodies.
CREATE TABLE evidence_lifecycle (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('CURRENT','STALE','REVOKED')),
 revision INTEGER NOT NULL CHECK(revision>0), event_ref TEXT NOT NULL, reason_code TEXT NOT NULL,
 PRIMARY KEY(tenant_id,mission_id,evidence_id),
 FOREIGN KEY(tenant_id,mission_id,evidence_id)
 REFERENCES evidence_records(tenant_id,mission_id,evidence_id)
) STRICT;
CREATE TABLE justification_adoptions (
 tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL, set_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('ADMITTED','SUSPENDED','RETIRED')),
 revision INTEGER NOT NULL CHECK(revision>0), event_ref TEXT NOT NULL,
 PRIMARY KEY(tenant_id,mission_id,set_id),
 FOREIGN KEY(tenant_id,mission_id,set_id)
 REFERENCES justification_sets(tenant_id,mission_id,set_id)
) STRICT;

```

## 附录 C：真实进程中断的本地测试目标

这是参考测试服务，不是生产Connector，不连接真实账户。在事务COMMIT后用`os._exit(81)`模拟服务已应用但调用方未得到回执。生产集成还要杀掉Orchestrator、保持测试服务独立运行，并通过真正恢复入口核对。

```python
"""Local test service: one SQLite transaction applies state plus deduplication.

NOT a production connector and NOT a generic guarantee about remote APIs.
Used solely to test the reply-loss boundary in a fresh temporary directory.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from pathlib import Path

DDL = '''
CREATE TABLE IF NOT EXISTS target_state(k TEXT PRIMARY KEY, v TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS operations(
    operation_key TEXT PRIMARY KEY, request_json TEXT NOT NULL, receipt_json TEXT NOT NULL
) STRICT;
'''


def apply(path: Path, key: str, target: str, value: str, *, lose_reply: bool = False) -> dict[str, str]:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(DDL)
        frozen = json.dumps({'target': target, 'value': value}, sort_keys=True, separators=(',', ':'))
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT request_json,receipt_json FROM operations WHERE operation_key=?', (key,)).fetchone()
        if existing:
            if existing[0] != frozen:
                raise ValueError('IDEMPOTENCY_PAYLOAD_MISMATCH')
            receipt = json.loads(existing[1])
        else:
            receipt = {'operation_key': key, 'target': target, 'value': value, 'outcome': 'APPLIED'}
            db.execute('INSERT INTO target_state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v', (target, value))
            db.execute('INSERT INTO operations VALUES(?,?,?)', (key, frozen, json.dumps(receipt, sort_keys=True)))
        db.execute('COMMIT')
        if lose_reply:
            os._exit(81)  # kill AFTER durable service state, BEFORE the caller gets a reply
        return receipt
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    parser.add_argument('key')
    parser.add_argument('--lose-reply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.path, args.key, 'report', 'v1', lose_reply=args.lose_reply)))

```

## 附录 D：固定源码与技术依据

来源只支持其读取范围内的结论；本专项具体组合为设计建议。

| ID | 来源 | 读取/使用范围 |
|---|---|---|
| S01 | [src/agent_orchestrator/orchestrator/commit_service.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/orchestrator/commit_service.py) | 1–195 |
| S02 | [src/agent_orchestrator/verification/assessments.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/verification/assessments.py) | 1–255 |
| S03 | [src/agent_orchestrator/verification/critics.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/verification/critics.py) | full returned |
| S04 | [src/agent_orchestrator/verification/mission_coverage.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/verification/mission_coverage.py) | full returned |
| S05 | [src/agent_orchestrator/memory/source_dependencies.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/memory/source_dependencies.py) | full returned |
| S06 | [src/agent_orchestrator/orchestrator/action_commits.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/orchestrator/action_commits.py) | returned prefix incl action identity/propose; later hooks referenced by actions.py |
| S07 | [src/agent_orchestrator/runtime/actions.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/runtime/actions.py) | full returned |
| S08 | [src/agent_orchestrator/runtime/connectors.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/runtime/connectors.py) | 1–320/full shown |
| S09 | [src/agent_orchestrator/storage/store.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/storage/store.py) | 1–230 |
| S10 | [src/agent_orchestrator/observability/replay.py](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/src/agent_orchestrator/observability/replay.py) | 1–180 |
| S11 | [docs/api/provider-invocation-ledger.md](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/docs/api/provider-invocation-ledger.md) | full returned |
| S12 | [plans/2026-09-14-gap-phase1/HANDOFF-2026-09-16-grok-a96.md](https://github.com/DennyWanye/simple-harness-sdk/blob/873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d/plans/2026-09-14-gap-phase1/HANDOFF-2026-09-16-grok-a96.md) | full commit diff |
| W01 | [Jon Doyle, A Truth Maintenance System (MIT, 1979)](https://dspace.mit.edu/entities/publication/5377b306-4ecc-4687-b1f5-78cbb4a0543a) | 理由、依赖和信念修订；只读取摘要，不宣称直接使用其全部算法 |
| W02 | [AWS Builders Library: Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) | 调用者意图身份、相同key不同参数、迟到请求 |
| W03 | [AWS Prescriptive Guidance: Transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) | 本库数据+Outbox原子、重复消息的消费幂等；不外推端到端exactly-once |
| W04 | [SQLite Isolation](https://www.sqlite.org/isolation.html) | 单写者/读快照/BEGIN IMMEDIATE，不替代业务检查 |
| W05 | [Microsoft Azure: Compensating Transaction pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction) | 补偿不保证复原旧世界且自身可失败；本轮可读正文来自同官方站点th-th路径 |
| W06 | [SQLite Online Backup API](https://www.sqlite.org/backup.html) | 每库一致备份接口；多库仍需业务屏障 |
| W07 | [Hypothesis: stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html) | 生成状态操作序列与不变量检查 |


D1：用户原始《Agent 编排层完整设计方案》，§5、11、14–18、20–25、30–31。
D2：`simpleharness-complete-target-and-gap-plan-2026-09-16.zh-CN.md`，§4、11–16、18–21及R01–R60。
D3：`simpleharness-taskgraph-implementation-design.zh-CN.md`，§4–9及输入/有效性/版本/Commit约定。
D4：`mission-critical-contract-hardening-plan.zh-CN.md`，仅用于评审身份、预算用途、等待与公开契约边界，不覆盖D2/D3主术语。

Source-map中未核验路径标记待定位；不能由GitHub搜索零结果或Host404推导功能不存在。部署和最新HEAD发生变化时，重新对照实际代码；保留此基线方便解释差异，不强迫用户回退。

**最终完成定义：独立审阅不能错认目标，证据变化能阻止不合法复用，恢复不会重复或掩盖现实动作；三者由同一套身份、版本和正式提交规则连接，并通过真实主链验收。**
