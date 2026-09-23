# SimpleHarness TaskGraph 代码级实施计划

**编号：TG-EXEC-2.0｜日期：2026-09-20｜用途：实施 Agent 与独立核验 Agent。**

这份文档是 TaskGraph 专项的执行规格，不是已合入补丁。完整 SQL、查询、八份 JSON Schema、可运行的局部参考规则、迁移前置检查脚本、测试清单均在同目录。末尾附全部新增 DDL 与查询，单独发送本 Markdown 也能阅读完整数据库合同。

## 0. 范围、前提与不得改变的裁定

### 0.1 实施目标

把已有 HTN 编译结果可靠地连接到 **版本化任务网络 → 精确输入 → 原子计划提交 → 有资格的 primitive 派发 → 独立验收 → 局部修复与恢复**。不是再写一个 HTN 求解器、另建 Agent 生命周期、替换数据库或把图交给模型直接编辑。

保留 `Task.form=compound/primitive`、Task/Obligation、MethodInstance、Occurrence、ORDER/DATA、InputManifest、PlanRevision、Acceptance/GoalResolution。Task 是工作合同；Occurrence 是出现位置；不能新增另一个可写 Goal 服务。证据真假及当前有效性仍归 Evidence/Validity；真实操作事实仍归原 actions/execution 账本。

### 0.2 输入材料与证据边界

- [D1] 原 TaskGraph 专项 `simpleharness-taskgraph-implementation-design.zh-CN.md`：这是目标要求，不是当前完成清单。
- [D2] `H1H-ADM-1.0`：本计划直接复用其 authorization producer、Operation identity bridge、分阶段 admission/preview/Commit。
- [D3] LLM-Native HTN V2：模型只提交 PlanningDecision；系统产生身份和检查。
- [S01–S16] 本轮读到的远端参考仍为 `5ac3f05890a4c5160e2f103a01f863753ea5d498`。完整文件链接见 `sources.json`。
- 本地候选 `102ad3df…`、SDK `51dbed2e…`、Host `6c457908…` 及未提交改动没有在本轮被读取。**远端参考不是升级指令，不覆盖本地候选。**

禁止 `reset/clean/cherry-pick/pull/rebase` 来强行对齐；禁止读写用户生产数据库作试验。实现可以在已有候选的受控分支进行；先保存 HEAD、allowlist 文件 hash、diff 清单，不能覆盖 NanoJev 或其他未提交工作。

### 0.3 三项前置完成门

1. H1H-ADM 三条来源链存在可执行实现和真实来源测试，而不只是 type/Protocol。
2. H1-H 独立核验与 H1-I/完整 H1 门禁通过，才能给新 TaskGraph 开生产/实际任务开关。纯函数、DDL 和离线测试可以先开发。
3. 实际路径/SQL键在 `scripts/probe_checkout.py` 输出中已核对。预检只确认环境和接口存在，不能代替功能验收。

**继续有效：**`BIND_EXISTING_GOAL`、`REPAIR/PROPOSE_SUCCESSOR` 在当前 H1 wire 只解码、不执行；NanoJev PR-7 保持 Shadow，`RETRY_OR_ESCALATE` 延后；真实模型仅用当前获准 GPT-5.6 配置。本文不自动开放任何新模型 Decision。

TaskGraph 可以承载已批准 Method 内的共享和替代结构；这不授权通过裸 API 绕过被禁用的顶层 Decision。缺少上游可表达的合法命令时，内核测试可验证能力，生产不伪造一个 Planner 决定。

### 0.4 规范优先级与本次明确补充

本次不修改原 60 项编号或 P1–P9。工作包 TG-A…TG-E 只是施工单元。已有裁定冲突时按：**实际已批准后续裁定 > H1H-ADM > 本文对 TaskGraph 的专项补充 > 旧 TaskGraph 草案**处理，并记录适用条款。

| 争议点 | 本次裁定 |
|---|---|
| 旧 TaskGraph 草案建议自动 rebase；H1 要精确请求版本 | **H1 仍不自动 rebase**。旧 Planner 回复失效后新请求；无关分支已在运行的 Attempt 不因全局图版本变化而作废 |
| 一个 GraphSnapshot 同时表示历史结构与当前状态 | 拆成 immutable `NetworkDocumentV1` 与 `GraphReadContext`；前者按指定 revision 读取，后者带当前执行/证据水位 |
| 接入新图需要重新建全部表 | 复用 `htn_schema.py` 既有表；只加本文九张扩展表（见 §6），不得新建第二套 Task/预算/Operation 状态表 |
| 结构已部分展开，是否允许提交 | 允许尚未细化的 compound 作为 **显式 pending**；所有已物化结构必须完整检查。`NOT_CHECKED` 不是“部分展开”的同义词 |
| CANCELLED 是否足以释放 ORDER/资源 | 不足。`settled_terminal` 要实际工作和相关操作已核对、必要结算有依据 |
| 新方法换了，旧动作是否可以重发 | 不可以。继续使用 H1H-ADM 全 Mission 保守 Operation 闸门；本文不引入“猜它与本分支无关”的豁免 |

## 1. 当前可复用的代码与本次改动方向

| 已读代码 | 已有能力 | 本次工作 |
|---|---|---|
| `graph/task_network.py` [S03] | immutable snapshot、多关系视图、compound entry/exit、采用方法、有限执行投影 | 保留算法；补历史精确读取、共享消费者和完整检查接线 |
| `graph/eligibility.py` [S04] | Planning/Execution Frontier、typed readiness、primitive 门 | 保留；输入全部来自正式 producer，禁止生产默认允许 |
| `planning/htn/grounding.py` [S10] | 方法/slot 稳定身份、参数化、SharingSignature | 复用，禁止重新发明 ID/hash 或文本复用 |
| `planning/htn/validation.py` [S11] | DeltaReport、结构/端口/前提/覆盖检查 | 与 H1 preview 共用；区分 pending expansion 与缺输入未检查 |
| `artifacts/input_bindings.py` [S06] | 端口、schema、witness、精确 manifest | 复用；去掉接线层“解析失败则空 manifest”的回退 |
| `HtnStore` [S02] | plan、members、ORDER/DATA、task_semantics、输入清单等 | 扩展同连接 Store；补每 revision 的精确 pins 和每 Attempt 的输入关联 |
| `plan_commits.py` [S08] | 幂等、版本、scope/read-set、保留工作、预算与采用提交 | 保留正式权威；同事务加完整图记录、pins、需求投影和 followup |
| `hierarchical_dispatch.py` [S07] | 实际编译/执行连接、TaskView、root Review | 只装配；不在该大文件再堆一套图算法 |
| `_read_set.py` [S09] | 统一 semantic read-set，unresolved/stale 分开 | 复用；planning grant/Operation 集合继续走 H1H-ADM 补充 guard |

**静态观察，不是已复现故障：**当前 `_read_network()` 会按 Task 取最新 semantics，并按当前 `method_instances.state` 过滤；这不能直接用于“历史 r1 的结构”。`admissions()` 中还有 `result.manifest is None → InputManifest(...)` 的回退。新版本必须用明确的完整读取/合法空输入证明替代；不能据此声称当前所有正常任务已经失败。[S02,S07]

## 2. 不可破坏的 16 条不变量

I01 单一逻辑 Commit；模型、UI、图算法不直接写业务表。  
I02 每 Mission 仅一个 ACTIVE PlanRevision，历史 revision 内容不可重写。  
I03 每 adopted goal occurrence 最多一个 adopted MethodInstance；候选方法不是必须完成的工作。  
I04 一个方法内所有 required/当前 conditional slot 均需满足；compound 不创建 Worker Attempt。  
I05 ORDER 不传播文件/授权；DATA 只绑定声明端口。  
I06 发布到 Worker 的 InputManifest 全部冻结；恢复不取 latest。  
I07 Task/Method/Operator/Schema/输入身份都必须完整匹配，不凭文本或 ID 前缀授权。  
I08 历史完成不回退；当前 Acceptance 有效性另算。  
I09 新工作表示延续相应 Obligation，不能重置累计费用/失败/燃料。  
I10 共享生产者按实际保留的 consumer/demand 判断，不沿退休父节点盲取消。  
I11 预览无 DB/CAS/网络写入，不先取消旧工作再检查新候选。  
I12 Runtime/Operation 数据未读到不能等同空集合；未知不能转“未执行”。  
I13 同命令同内容回原回执；同命令异内容冲突。  
I14 Commit 重新检查当前权限、相关版本、集合、费用和在途工作。  
I15 未影响的在途 Attempt 可继续；受影响旧 generation 不得新 handoff，晚到结果仍归档计费。  
I16 图重建不调用模型/工具、不重放外部动作、不重建活 lease 或当前授权。

## 3. 类型和所有输入的来源：不再让 Context 调用者猜

### 3.1 类型复用与新增

**复用实际类型：**TaskNetworkSnapshot、OccurrenceSpec、TaskSemanticBindingV1、MethodInstanceDraft、ChildBinding、OrderConstraint、DataRequirement、BoundInput、InputManifest、AcceptedOutputsIndex、SemanticReadSet、GraphStructureBudget、RefinementCompilation、DeltaReport、EligiblePrimitiveTask、PlanPrincipal，以及 H1H-ADM 的 authorization/operation/runtime/preview 类型。

新增到 `graph/execution_contracts.py`，一律 frozen、slots、keyword-only；内部多态用 tagged union，不用 `valid: bool=True`：

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CompleteRead(Generic[T]):
    value: T
    source_id: str
    source_digest: str
    through_seq: int

@dataclass(frozen=True, slots=True, kw_only=True)
class SourceUnavailable:
    source_id: str
    reason_code: str
    observed_refs: tuple[SourceRef, ...]

@dataclass(frozen=True, slots=True, kw_only=True)
class GraphReadToken:
    mission_id: str
    plan_revision: int
    manifest_hash: str
    through_seq: int
    validity_epochs: tuple[tuple[str, int], ...]

@dataclass(frozen=True, slots=True, kw_only=True)
class PlanEffectSet:
    retained: tuple[OccurrenceId, ...]
    revalidate: tuple[OccurrenceId, ...]
    retiring: tuple[OccurrenceId, ...]
    newly_materialized: tuple[OccurrenceId, ...]
    shared_retained: tuple[OccurrenceId, ...]
    coverage: Literal['COMPLETE', 'CONSERVATIVE']
```

`T` 是类型参数，不是 `Any`。`SourceRef` 使用 H1 内部 source receipt 表达 `(channel,identity,revision,digest)`，不可变；不同 purpose 的 witness 不能互换。对已有同名 `SourceUnavailable` 直接导入，不再定义第二份。构造检查 `bool` 不是整数、非空 ID、64位 hash、重复 key、enum、边界。来源成功但集合为空仍返回带 digest 的 `CompleteRead(())`；读失败返回另外分支。

`GraphReadContext` 是按用途区分的 union，而不是每个读接口都强制读取全部来源：
- `StructuralReadContext`：caller、policy、指定 NetworkRecord、原 codec/immutable对象、event水位；用于历史结构/API diff。
- `ExecutionReadContext`：上述结构 + 当前 binding/method登记、Validity、AcceptedOutputs、输入policy、demand、实际work/operations、执行授权、预算和资源。用于当前就绪/派发；不要求仍有效的旧规划grant。
- `PlanMutationReadContext`：ExecutionReadContext + 原PlanningRequest/Decision + 当前PlanningAuthorizationSnapshot + 精确H1 preview输入；用于新计划采用。

三者各自构造函数显式接收所需类型，不用`None/()/True`填不适用字段。`NoPlanMutation` 沿用 H1H-ADM，不构造后两类来处理 WAIT。

### 3.2 producer / source / join / 缺失行为（完整登记见 `implementation/seams.json`）

| 字段 | 生产函数及精确来源 | 关联/完整性要求 | 缺失行为 |
|---|---|---|---|
| caller | 现有 facade 固定 Principal/tenant + `_mission()` | caller 当前可读/可提交此 Mission；不能来自 payload | 拒绝或认证部署错误 |
| request | 原 PlanningDecisionStore 冻结记录 | request_id→decision/AgentTurn；visible_refs 用存储原件 | SOURCE_UNAVAILABLE，不重新拼 request |
| planning authority | `build_planning_authorization()` [D2 §3] | 原 request→grant revision/hash→当前 lineage/policy/TTL | 缺 grant≠producer 错；按补遗分类 |
| 当前结构 | 新 `TaskGraphStore.read_revision()` | ACTIVE revision→records/pins→精确 binding/draft；集合全读 | 完整性错误，不遗漏节点继续跑 |
| Method/Catalog | HtnStore.get_method + 已注册 TaskType/Schema snapshots | id/version/hash；定义冻结、状态当前复查 | 缺类型/服务配置不能假 default |
| Evidence | 原 witness/input_witness/start_witness producers | purpose、consumer、support、scope epoch、有效期均匹配 | 不判 FALSE，进入取证或阻断 |
| Operations | `build_operation_snapshot()` [D2 §4] | frozen Operation→精确 action_key/version/回执，全 Mission | 不猜 join；SOURCE_UNAVAILABLE/DEFERRED |
| 在途 work | `read_running_work()` [D2 §4.7] | 退休 instance→原 occurrence→Task→Attempt/intent/实际回执 | 不能凭 Task 终态或 lease 过期判收敛 |
| accepted outputs | `HierarchicalDispatch.accepted_outputs()` | producer port→result→Acceptance→Artifact hash、当前披露权 | 不能由 Worker 声称的 output 填充 |
| ORDER outcome | `occurrence_outcomes()` + 新 `settlement_facts()` | ACCEPTED 见证，或本次 work/操作/所需结算已终止 | UNKNOWN 不 release |
| Schema compatibility | 现有 SchemaCompatibilityRegistry | 相同身份或真实注册方向规则；转换须有实际新产物 | 不推导任意 Schema 蕴含 |
| resources | Operator 注册能力 + TargetRules/ResourceIdentity | namespace/标准化路径/访问模式/目标版本；Host 实测规则 | 目标规则未知则不能放行写入 |
| 需求 | 新 `read_active_consumers()` + ObligationStore.account | adopted instance/slot 完整集合；独立责任另外读取 | 查不到!=无人需要 |
| Budget | `BudgetLedger.account(task_account(id))` + parents/holds/tails | 原 limits/reserved/settled/unpriced；结构 fuel 独立 | 读失败不视为0消耗或无限 |
| 候选结构 | H1H-ADM `preview_candidate()` | 同一 proposal+source snapshot→同一 compilation/delta | NOT_CHECKED/PARTIAL_CHECK 不作为 PASS |
| 冻结输入 | `resolve_declared_inputs()`→原 InputManifest→新增 per-attempt binding | 先以实际 resolver证明完整，再冻结 input_hash/intent | 不允许 fallback 空 manifest |
| lease/配额 | 现有 `_dispatch_attempt` 的 quota/lease 生产链 | 实際 owner、共享物理池、过期后须核对旧 call | 不另建图级物理槽位池 |
| 最终验收 | 原 leaf_acceptance/root_review/resolution_commits | 指定 ReviewPackage/inputs/criteria/Method/当前证据 | Graph 不自签 PASS |
| UI | 新 `TaskGraphReadApi` | 按读用途取得同一读事务 plan/所需states/event watermark/epochs | 返回诊断，不编造业务 UNKNOWN |

**关键分工：**planning grant 只约束改计划；已采用工作继续执行时检查它自己的当前执行授权、工具限制和输入权限，不能把一份24小时规划 grant 当成所有后续动作的总审批。它过期不追溯抹掉已完成计划回执。

### 3.3 必须显式实现的四个装配函数

在 `orchestrator/taskgraph_sources.py` 新增；仅调用 Store reader 与已批准只读 producer，不调用模型/Observer/executor：

```text
read_structure(mission_id, revision, caller)
  -> CompleteRead[StructuralReadContext] | SourceUnavailable
read_execution_context(mission_id, caller)
  -> CompleteRead[ExecutionReadContext] | SourceUnavailable
read_plan_context(request_id, decision_id, caller)
  -> CompleteRead[PlanMutationReadContext] | SourceUnavailable

build_plan_effects(before: TaskNetworkSnapshot, after: TaskNetworkSnapshot,
                   changed_supports, demands_after, coverage)
  -> PlanEffectSet

build_dispatch_inputs(context: GraphReadContext, occurrence_id)
  -> CompleteInputResolution | PendingInputs | InvalidInputs | SourceUnavailable

settlement_facts(context: GraphReadContext, occurrence_id)
  -> AcceptedOccurrence | SettledTerminalOccurrence | InFlightOccurrence | OutcomeUnknown
```

`read_plan_context` 步骤：开启原 Store 一致读事务→tenant guard→读 ACTIVE 和 pinned结构→原实例/contract状态→要求/方法登记→原证据/支持/epochs→完整接受/工作/操作→原预算/资源描述→event MAX(seq)→生成 source receipts→结束事务。不能把四次不同事务读的结果称为同一快照。纯 preview在此后运行。

外部执行库事实先走现有持久导入；图组件不 `ATTACH execution.db` 伪造跨库一致性。原导入尚不能确认在途工作时保持不确定。

## 4. 模型协议不变；新增的内部协议必须完整

本包 `schemas/` 八份文件都是完整 Draft 2020-12，无悬空 `$ref`。它们是本次新增边界，不取代 SDK 已有复杂合同 codec。

### 4.1 NetworkDocumentV1

字段：`schema_version=1, mission_id, revision, codec_manifest_hash, requirements_ref, root_occurrence_ids, adopted_instance_ids, required_obligation_ids, objects[]`。Schema的完整限制以附件和附录C为准。

每个 object 为 `(kind, identity, sha256, canonical_json)`。kind 仅允许 `occurrence/task_semantics/method_instance/order_constraint/data_requirement/typed_edge/obligation_coverage`。`canonical_json` 保存**原对象的完整 to_json canonical字节字符串**，而非解释后的摘要。读取时先 hash核对，再用该 kind 对应的已注册原 codec解码。Schema检查 string 结构不等于已经验证了内部合同。

| kind | identity 的系统生成规则 | 原 decoder |
|---|---|---|
| occurrence | 原 OccurrenceId | OccurrenceSpec.from_json |
| task_semantics | canonical `[task_id,binding_revision]` | TaskSemanticBindingV1.from_json |
| method_instance | 原 instance_id | MethodInstanceDraft.from_json |
| order_constraint | canonical `[before,after]` | OrderConstraint.from_json |
| data_requirement | 原 requirement_id | DataRequirement.from_json |
| typed_edge | 原合同 identity；若无独立 ID 则原内容 hash | 现有 TypedEdge codec |
| obligation_coverage | 原内容 hash | 现有 ObligationCoverage codec |

不因为表主键约束自动 dedupe 错误输入。重复 identity 先拒绝。顶层集合按 `(kind,identity)` 排序，adopted/roots/required obligation 为显式集合去重校验后排序；**每个 Method 内原 steps 和每个有序端口数组保持原顺序**。`codec_manifest_hash` 对应 source-map 锁定的 codec版本清单，不冒称旧 canonical格式已升级。

历史方法采用情况看 document/pins，而不是今天 `method_instances.state`。历史合同看 binding_revision，而不是 `latest_task_semantics()`。当前状态 overlay 可以展示今天的新状态，但必须标明 `read_token`；不能把它写回历史 document。

### 4.2 PreviewBindingV1

字段精确为 Schema 所列：decision/request/Mission，base_revision，decision/network/candidate/delta/read_set hashes，validator id/version，完整 source_reads，pending_compound_ids，required_convergence_ids。

所有 source_reads 必须 `coverage=COMPLETE`，12个channel逐个出现恰好一次（每个channel摘要覆盖全部相关成员）；保守扩大影响范围是 `PlanEffectSet.coverage=CONSERVATIVE`，不等于读取源不完整也可以通过。preview绑定由系统生成，不允许模型提交这个证明包。

### 4.3 TaskGraphViewV1、FollowupV1、ErrorV1

View 包含读token、typed nodes/edges、两个 frontier、root resolution refs、分页指针/complete。前端不能据节点颜色决定可执行；本版本只支持有界全量快照，`complete=true,next_cursor=null`；不拼接不同 token 的结构与状态。

Followup 仅三类：`REEVALUATE`、`CONVERGE`、`REQUEST_COMPOSITION`，每条绑定原 event、Mission、subject、revision、cause_ref。不是一个能直接发送 tool 参数的通用队列。

Error 明确 `origin=MODEL/SYSTEM/RUNTIME`、stage、code、detail、retry_kind、source_identity。内部错误细节不能泄漏不可见对象。H1反馈继续使用已批准拒绝码，映射沿 H1H-ADM，不重新命名公开协议。

`TaskGraphView.read_token.manifest_hash`是结构document的hash；`snapshot_hash`是完整授权视图（去掉snapshot_hash本字段后）经原canonical序列化的hash。快照水位与validity_epochs包含在其中，不能用结构hash冒充运行状态也被冻结。八份结构样例的hash是测试占位，**仅用于Schema测试，不是SDK可接受的内容证明**。

三个只读附加返回合同已经在Schema锁定：`TaskGraphExplanationV1`绑定具体occurrence/read token和有权展示的source refs；`TaskGraphDiffV1`为kind/identity/before_hash/after_hash变更集；`TaskGraphConvergenceViewV1`返回完整有界jobs/targets和事件水位。diff单项before/after不得同时null；相等hash不列入changes；这些跨字段要求由strict codec验证并加负测。非同Mission源不返回其身份/详细错误。

`certificate_json`应用层是受限联合：普通提交仅允许 `{version:1,kind:"PLAN_ADMISSION",preview:PreviewBindingV1,commit_receipt_ref:SourceRef,admission_check_ref:SourceRef}`；captured baseline仅允许 `{version:1,kind:"CAPTURED_BASELINE",captured_through_seq:int,baseline_command_ref:SourceRef,quiescence_ref:SourceRef,policy_ref:SourceRef,codec_manifest_hash:hash}`。所有字段必填，未知字段拒绝；后者来自真实baseline命令和H1H runtime完整核对，不补造历史APPLIED。SQL只检查JSON格式，内容由该union codec和指向的实际回执核对。

### 4.4 Source 与哈希不能循环定义

先冻结不含 `manifest_hash/event_id/created_at` 的 NetworkDocument bytes→求 hash→写事件（引用此 hash）→写 revision_record（引用 event_id）。parent chain 使用先前 record 的 hash。`sdk_snapshot_hash` 与新增 document hash 分开，**不改旧 snapshot_hash 的算法**。

command intent 使用原 `CommitPlanCommand.intent_hash()`，新内部 guard/preview hash以 side binding关联，不让重启时间戳改变业务 command身份。

## 5. TaskNetwork 的精确语义与算法

### 5.1 Projection

继续用原 `execution_projection()` / Kahn，节点命名复用当前带长度前缀 ID，不新造 `occ + ':exit'` 碰撞形式。

1. 从**固定根**和 adopted MethodInstance 遍历可达 occurrence；不从“所有没有入边的 Task”重新推断用户根目标。
2. compound→entry/exit，primitive→可执行节点。entry/exit不计费、不创建 AgentTurn。
3. entry打开孩子；required/conditional child的接受参与parent退出；optional_authorized不替代缺失必需孩子。
4. ORDER P→Q 形成 P.exit→Q.entry；DATA产生producer→consumer依赖，但不隐式带入其他文件。
5. 所有declared端点必须存在且在合法 adopted网络；遇悬空端点报告，不过滤后继续。
6. 拓扑无环只是结构检查，不代替前提、端口、根覆盖、预算和独立验收。

未展开 compound 允许结构 span 占位，但它只能进入 PlanningFrontier，不能因为 span存在直接打开实际exit。已 adopted 方法零 gating children 默认 COVERAGE_GAP；真正的零工作目标必须走显式 reuse/验证合同，不由空 AND默认完成。

### 5.2 部分展开 != 部分检查

`validate_delta` 对“尚无采用方法、未承诺可以执行的 compound”返回 typed pending list，而不是声称它已可约化或已完成。检查完整性分开：

- `structural_check_complete`：当前 materialized nodes/edges/ports全部已检查；必需为真。
- `decomposition_complete`：是否所有 compound已经细化；允许假，不作为执行批准。
- adopted Method前提、schema、要求覆盖、runtime来源不能缺省；缺输入产生 SOURCE_UNAVAILABLE。

需要调整现有 validator 时只增加明确 `pending_compounds` 报告，保留 H1H-ADM 的 NOT_CHECKED/PARTIAL_CHECK拒绝规则。不能在 mapper中把 PARTIAL_CHECK全忽略。

### 5.3 共享与slot

以原 ChildBinding/SharingSignature为准；slot identity 和共享 producer identity分别保存。`method_child_occurrences.occurrence_id`、`goal_occurrence_id`不能凭字段名互换。Store 新接口直接返回两者和对应原 ChildBinding，由 producer核验。

共享只读结果要求合同、参数、input versions、scope、保障/新鲜度policy、domain语义一致。写型工作/真实副作用默认不可共享；相同文本/文件名/hash本身不够。

活跃共享不能对已经执行的 producer加“过去本应满足”的新前置。若新consumer要求的ORDER/前提尚未成立，拒绝 SHARE_ACTIVE；可等待其完成后按合格Acceptance复用，或使用独立新工作。禁止把某个消费者的入门条件悄悄删掉求并行。

取消某方法只撤销该方法slot需求。`retired_targets = proposed_removed_or_changed - producers_required_by_remaining_demands - independently_required_work`。同时用结果网络再次检查保留的共享producer仍有显式可达/需求归属。不是遍历旧父节点把全部后代取消。

### 5.4 ORDER release

`accepted`：指定合同的合法接受见证；若边另有guard则实时检查guard。  
`settled_terminal`：已进入指定终态且所有相关真实调用/动作已收敛，合同要求的结算已完成。  
UNKNOWN、查询失败、只有 lease过期、只有Task.CANCELLED均不能放行。纯ORDER已发生的先后见证不自动传递内容；需要消费当前内容时另有DATA/witness。

### 5.5 准备输入

复用 `resolve_declared_inputs()`。调用方显式提供：consumer binding、当前该consumer的DataRequirements、AcceptedOutputsIndex、ResolutionPolicy（pins/schema_registry/真实scope_epochs/now/purpose）、TargetRules、该consumer的witness。

- 必需port没有生产者：DATA_UNBOUND；生产者未完成：WAITING_DATA；源读取失败：SOURCE_UNAVAILABLE。
- 首次单一合法producer可以选择后冻结；多个revision没有已授权选择则歧义拒绝，不选最新。
- PINNED不自动升级；FOLLOW_AUTHORIZED_REVISION只允许在新绑定/新Attempt前解析。已冻结请求不跟随变化。
- registry声明需要converter时，实际转换必须形成新产物和验收；不能拿原字节加新schema标签。未部署转换执行路径就明确拒绝。
- SINGLE一份；SET/LIST按已有PortOrdering/显式order列表；MAP按声明key，重复key拒绝。
- `InputManifest()`合法空的条件：源读取完整、所有必需port均无需数据且已满足/无必需port、没有pending requirement。必须由resolver返回明确成功，不允许 `manifest or empty`。

冻结与派发步骤见 §8，hash/创建键沿用原实现，不另写 canonical 函数。

### 5.6 局部影响分析

`build_plan_effects` 输入before/after和有效性结果，程序先计算：增加/移除occurrences、变化的输入绑定、Method采用、共享需求。对实际DATA及support反向边用迭代BFS传播 `NEEDS_RECHECK`；ORDER-only不自动传播字节失效。支持覆盖不完整时保守扩大revalidate，不宣布最小影响。

已知不变只意味着候选保留；实际dispatch/验收仍检查当前Validity。另一组充分支持可维持结论，但不能悄悄重写已签产物的引用。需求变化需要原授权的RequirementsRevision，不让Planner减去难准则。

## 6. 数据模型与所有新增SQL

### 6.1 复用表（不重建）

`missions/tasks/attempts/dispatch_intents/events/results/artifacts/budget_*`；`task_semantics/method_contracts/method_instances/method_child_occurrences/plan_revisions/plan_memberships/order_constraints/data_requirements/bound_inputs/input_manifests/input_manifest_bindings/obligations/requirements_revisions/review_packages/review_records/acceptances/goal_resolutions`；以及H1H-ADM四表。

新增 **九张**表：policy binding、revision record、member pins、method pins、demand refs、attempt inputs、convergence jobs、convergence targets、followups。它们没有第二份费用或Operation状态。

### 6.2 写入者与读取者

| 表 | 唯一写入口 | reader / producer | 源还是投影 |
|---|---|---|---|
| taskgraph_policy_bindings | authenticated `enable_taskgraph_contract()` | TaskGraphStore.policy | 不可变部署/Mission绑定 |
| taskgraph_revision_records | 原 `commit_plan_revision` 同事务扩展；baseline显式命令 | read_revision/replay | 不可变图源记录；旧关系表为同步投影 |
| taskgraph_member_pins | 同一次plan commit/replay reducer | read_revision | 历史精确关联投影 |
| taskgraph_method_pins | 同上 | historical_method_view | 每revision采用关系投影 |
| taskgraph_demand_refs | 同上，只从真实adopted slots导出 | read_active_consumers | 可重建需求索引 |
| taskgraph_attempt_inputs | 原创建Attempt+reservation+intent的同事务 | resume_attempt_inputs | 不可变执行关联 |
| taskgraph_convergence_jobs/targets | `begin_convergence()`、`advance_convergence()` | fence/恢复 | 原取消流程的持久工作协调；不替代执行事实 |
| taskgraph_followups | 同一次来源事件事务 | `pump_taskgraph_followups()` | 投递机制，非Task执行权威 |

### 6.3 TaskGraphStore 完整接口清单

放 `storage/taskgraph_store.py`，组合原 Store连接，不自建connection：

```text
policy(mission_id) -> PolicyBinding | Missing
insert_policy(binding, receipt) -> PolicyBinding                  [需外层事务]
read_revision(mission_id, revision) -> NetworkRecord              [失败显式]
insert_revision_record(record, pins, method_pins, demands)        [需外层事务]
list_member_pins(mission_id, revision) -> tuple[MemberPin,...]
list_method_pins(mission_id, revision) -> tuple[MethodPin,...]
read_active_consumers(mission_id, producer) -> CompleteRead[tuple[DemandRef,...]]
insert_attempt_inputs(binding) -> AttemptInputBinding              [幂等比全内容]
get_attempt_inputs(mission_id, attempt_id) -> AttemptInputBinding
insert_convergence(job, targets) -> ConvergenceJob                 [同决定幂等]
cas_convergence(job_id, expected_version, old_state, new_state)    [rowcount=1]
active_fences(mission_id, task_id) -> tuple[Fence,...]
append_followup(message) -> Followup                              [同key异payload冲突]
claim_followup(owner, now_ms, lease_until_ms) -> Followup | Empty
ack_followup(message_id, owner, row_version, consumer_receipt)
retry_followup(message_id, owner, row_version, failure, now_ms)
```

DDL在附录A和`sql/001_taskgraph_extension.sql`；read/CAS SQL在附录B。没有动态SQL表名来自模型。`insert_*`同key不同hash是冲突，不用 `INSERT OR REPLACE`。

### 6.4 存储与API边界校验

SQL明确PK/FK/unique/check和新表subject triggers。仍需程序核对原JSON里的全部内容hash、same Mission/scope、合法状态转换；不能因为SQLite收下就等于业务通过。

`binding_revision` 在当前实际HtnStore就是contract_revision；本次不改旧字段解释，不假造一个额外storage revision。控制变化仍通过原合法binding更新；historical pins冻结原binding，当前叠加读新binding。心跳不得修改合同/输入。

`taskgraph_attempt_inputs`补的是现有 `input_manifest_bindings` 按 `(mission,task,manifest_hash)` 去重时不能唯一表达多次Attempt的问题。原manifest内容仍只有一份，每次真实Attempt有自己的关联行。

SQL只允许增量迁移：**实际最大迁移号+1**，含H1H-ADM/NanoJev已占号；不覆盖19或旧checksum。执行前`PRAGMA foreign_keys=ON`，对测试库运行foreign_key_check/integrity_check。生产不手动executescript，使用既有migration runner，事务内不得意外commit。

### 6.5 kernel opt-in 与旧任务

新增 `enable_taskgraph_contract(mission_id, command_id, caller)`，仅固定Host/内部API，不是模型工具；必须tenant guard、明确非legacy、planning-decision-v1、H1门禁部署满足、有效planning委派、未终结。政策采用原GraphStructureBudget/原候选policy/原成本限制，不从环境变量临时替换。

新Mission在第一次TaskGraph提交前启用；默认旧路径不写这九表。已有活跃Mission迁入：要求停新派发、原工作/Operation已可靠收敛、有一次一致active snapshot、用户/操作者明确命令，捕获 `CAPTURED_BASELINE`。不能把全库自动升级为新语义。

baseline仅说明“从此刻这个已核对结构开始可重建”；不伪造此前所有revision。旧历史缺pins，历史查询返回 `HISTORICAL_STRUCTURE_UNAVAILABLE`，不能拿今天latest补昨天。

## 7. Preview与原子改图：两阶段来源链完全复用

### 7.1 plan命令入口

H1不改变wire：REFINE、REPAIR/REPLACE_METHOD经原Decoder→PreAdmission→Adapter→`preview_candidate`。新增图功能只消费这条管线和已批准系统命令，不建裸`add_edge`公网API。

内部 `TaskGraphCandidate` = 原CandidatePreview + PlanEffectSet + NetworkDocumentV1草稿 + pins/demands +完整source receipt集合。纯编译能产生草稿，但不得触碰Store、CAS写入或执行服务。

### 7.2 完整提交顺序

外层 `commit_admitted_plan` 仍是H1H-ADM的权威入口。其与原`commit_plan_revision`在**同一个Store写事务/同connection**中执行；内层如果使用savepoint，不得自行commit外层。

```text
当前caller读取权限
→ BEGIN IMMEDIATE（或原Store等价串行写）
→ 按原command_id取原CommitReceipt；同intent返回原结果，不重编、不再派发
→ protocol/enablement + request/prompt/package/subject绑定
→ 当前planning grant lineage/policy/时效
→ H1 coarse plan/requirements/scope gate（不自动rebase）
→ 原SemanticReadSetChecker + 当前capability/输入/证据/预算
→ H1H-ADM完整Operation集合hash、在途work和新convergence fences
→ 同candidate/delta/source hashes；完整结构校验、保留集合、需求/资源检查
→ 原预算/Obligation opening、退休generation处理、Task物化
→ 原plan revision/members/ORDER/DATA/method rows
→ 原PlanCommitReceipt + H1 decision最终关联/APPLIED check（仍在同一外层事务）
→ 新TaskGraphRevisionRecorded事件
→ 本次revision_record/pins/demand index（精确引用前述APPLIED check）+ followup
→ convergence job APPLIED（如有）
→ 原子COMMIT
```

不要先写“VALID”后再补budget，不要用新record绕开原`require_commit_ready/_check_preserves_plan/_check_alternatives_are_method_instances`。发生异常共同rollback；外部调用在事务外。

**关联集合也要复核：**若任一新增action/consumer/边发生，旧成员本身hash可能不变。重新读完整被检查集合/其当前digest，不只比原来那几个ID。

### 7.3 无关改图与在途结果

新PlanningDecision仍绑定老plan时，按H1 stale拒绝/新请求；这不是重新执行所有工作。已有Attempt用自己的contract/input/generation/purpose授权比较，不仅比较全局plan_revision。未被影响的输入/合同保留，结果可按原合同接纳；被替换者费用仍登记但不能批准新图。

### 7.4 已Commit响应丢失

恢复用原 command_id/intent，从原receipt确定执行已经发生。授权撤回后，当前caller如果仍有读取权限可以读历史回执；不能借回执再做新写。不要重新mint command_id或重新求当前snapshot以发另一遍命令。

## 8. 派发、输入冻结、并发与结果接纳

### 8.1 完整派发入口

原事件循环从 `HierarchicalDispatch.admissions()` 得到合法primitive候选；Allocator只排序这些候选。NanoJev Shadow不改变集合或准入判断。

正式创建前：

1. 原配额管理申请可用slot/调度许可（失败不能占满事务等待），只短时持有；不是新TaskGraph配额池。
2. 同Store短事务内重新读取目标的binding、当前demand、ORDER、START/MAINTAIN witness、输入、fence、当前执行权限和预算。
3. 确认primitive且candidate_policy仍允许另一个Attempt；有相同真实工作不再创建。
4. 以原代码路径创建Attempt身份；调用原BudgetLedger.reserve，保留保护尾额度，预留/UNKNOWN按原协议。
5. resolver输出必须 `problems=()`、manifest存在且frozen；写原InputManifest/绑定；冻结实际Provider请求的config/message/input_hash。
6. 原DispatchIntent写入，连同`taskgraph_attempt_inputs`一起提交。来源admission_check必须与所属revision_record指向同一实际APPLIED的plan check。仅显式CAPTURED_BASELINE允许两者均为NULL，由实际baseline授权回执解释，不能给历史补造PASS；**本次执行检查和原plan检查是两种事实**。
7. 事务外由原AgentBridge按creation_key/input_id交接；失败释放物理调度占用按原规则，不能无证释放逻辑费用。

只给本地目录赋值不等于物化完成：CAS逐文件hash核对、只读取manifest列出路径、按TargetRules拒绝跨目录/大小写冲突/符号链接逃逸，继承原工作区安全逻辑。

### 8.2 恢复

用 `get_attempt_inputs(mission_id,attempt_id)` 查冻结manifest和intent；与原intent的creation_key/input_id/input_hash逐项比较。缺行阻断新协议恢复，不重新resolve latest。实际SDK已收到任务但orch未收到回执，沿原稳定ID查询，不新建AgentTurn。

### 8.3 handoff fence

原 Provider/Tool handoff authorizer 与 Action begin_handoff加入调用 `active_fences(mission,task)`，并复查原generation/输入权限。图模块不修改executor中的实际已发call结果。

fence只能阻止未来获准交接，不能撤回已经发上网的请求。已发请求由原reconciliation处理。暂停或waiting时释放可释放的物理槽位，不等于释放预算预留。

### 8.4 结果与验收

接收结果先按原Attempt/turn归档和计费。用`taskgraph_attempt_inputs`固定消费的版本构造ReviewPackage；不要给Reviewer改成当前latest。组合Review输入由**当前 adopted 方法**的必需slot Acceptance构造；独立Verifier给Review，原resolution_commit再核对要求与Validity。

Graph的parent.exit只消费有效GoalResolution。根完成还要原Mission closeout，不能只选最后一个拓扑叶。No-change/WAIT也不能替代这个判断。

## 9. 在跑方法替换：状态机、fence、保留与补偿

### 9.1 状态机

`taskgraph_convergence_jobs`：

```text
合法preview + 真实需要收敛 → FENCED
FENCED → WAITING                 原取消/核对已发起或等待外来owner
FENCED/WAITING → READY          目标work全部可靠收敛
READY → WAITING                 新观察发现仍需核对，保持fence
READY → APPLIED                原PlanCommit同事务成功
FENCED/WAITING/READY → ABANDONED 仅明确放弃且满足安全释放条件
```

READY仍fenced；每次更新按row_version CAS。`ABANDONED`不是“超时算了”：必须证明无在途危险动作，且操作者/系统规则批准恢复旧需求或保持撤回。无法证明时继续WAITING并报告，不自动清fence。

### 9.2 begin_convergence 的具体算法

`begin_convergence(candidate, caller)`在独立受控事务中重验request/grant、candidate hash、当前base、完整operation/runtime来源、结构report；非法preview不创建job、更不取消work。

系统计算targets：退休/改输入且没有保留共享consumer的生产者。把job+targets+事件+CONVERGE followup同事务写入。**此时先用target fence阻止未来dispatch/handoff；不提前修改正式plan、contract revision或输入hash**，避免把原PlanningRequest变成自己制造的stale。

原cancel/reconcile消耗真实回执更新runtime；pump按job对同一决定重读，不调用Planner。原有P2.3s取消流程若同时更新会影响request identity的scope/requirements/plan版本，按真实stale处理，不能回写request。纯Attempt状态变化不换业务命令身份。

### 9.3 最终应用

READY不是永久证书。最终Commit重新检查临时fence、当前真实在途和operations；然后原`_revoke_running_work`仅对真实retiring targets更新generation，保留targets excluded集合。`_retired_children`现有“一退休父的所有孩子”只可用于候选影响发现，**不能直接作为最终取消集合**。

让新的typed impact显式传入原Commit检查；旧模式保留原行为。新模式必须核验 supplied impact与系统重算相等，不能信model列表。共享保留集合在结果网络中必须仍合法，不能靠“跳过generation”留下悬空工作。

### 9.4 已有外部效果

APPLIED结果不得被换方法遗忘；UNKNOWN/IN_FLIGHT不让新路线越过。CONFIRMED_NOT_APPLIED的真实性和重发资格仍由H1H-ADM回执规则确定。TaskGraph没有 `mark_reconciled(True)`。补偿请求走原新受控Operation，本文不自动产生补偿。

## 10. 事件、followup、恢复与重建

### 10.1 事件合同

新kernel额外写 `TaskGraphRevisionRecorded`：schema_version1、mission/revision/parent_hash、manifest_hash、sdk_snapshot_hash、command_id、decision_id、source_kind、effect_set_hash。原`PlanRevisionCommitted`字节不改。`network_json`完整内容在同事务immutable record中，事件引用该源记录；两者都属于该模块备份范围，不能宣称单靠旧Event payload可重建全部。

辅助事件：`TaskGraphContractEnabled`、`TaskGraphConvergenceRequested/Advanced`、`TaskGraphDispatchBound`。它们的producer只能是对应Commit。复用message id/command id去重，不按current timestamp建新业务身份。

### 10.2 followup三种消费者

- REEVALUATE → 原调度事件循环重读当前graph/readiness；只标脏或唤醒，不直接开Worker。
- CONVERGE → `advance_convergence(job_id)`，调用原取消/核对入口；其命令身份由job+原Attempt确定。
- REQUEST_COMPOSITION → 原root/composition review producer；幂等key包含 parent、MethodInstance、requirements、子Acceptance/输入digest；不重复评审同一组合。

Poll/claim/ack SQL见附录B。lease只属于通知消费者，不等于Worker lease。消费者**先有下游持久命令/意图/回执，再ACK**；中间强退重送同key。普通暂时IO失败按既有退避策略，连续5次后BLOCKED，仍在运维查询里可见；不得丢到日志就删除。新策略不得在轮询里重问模型。

事件监听的来源列表：PlanRevisionCommitted、Acceptance/GoalResolution提交、Witness/Evidence失效、Attempt/DispatchIntent终态、Action核对、Budget/approval变化。新kernel在原事件回调同事务追加通知或经持久事件水位消费；游标存在原scheduler_state（键包含kernel/consumer/Mission）。每个处理原事件的事务：原事件水位+派生通知一起Commit，防漏通知。

### 10.3 图重建

`observability/taskgraph_replay.py::rebuild_projection(source_db,target_db,mission_id,through_revision)`：默认只写**新的离线目标库**。不直接DELETE现有用户表。

1. 读policy和首个baseline/seed record及hash。
2. 按revision验证parent hash链、原对象hash/codec、引用完整性；缺任一源记录报coverage缺口。
3. 使用versioned reducer重新生成pins、method adoption投影、需求索引、ORDER/DATA执行投影。
4. 与目标revision document/snapshot hash比对；不能从源库当前active投影补缺失历史。
5. 不重建当前owner lease、未处理通知到可执行状态、planning grant有效性或action结果；恢复运行前重新核对这些事实。
6. 真要恢复生产：已有停机/备份/恢复流程下独立核验输出，再受控切换；本计划不自动替用户交换数据库文件。

`CAPTURED_BASELINE`只覆盖捕获点及之后；文档中必须报告这个覆盖边界。

## 11. Host接口与UI：给明确API，不靠未知本地路径

新增SDK `api/taskgraph.py::TaskGraphReadApi(commit, *, tenant_id, principal)`，复用MissionControlV1._mission等价tenant guard。方法固定：

```text
snapshot(mission_id, *, revision=None) -> TaskGraphViewV1
why_not_ready(mission_id, occurrence_id) -> TaskGraphExplanationV1
diff(mission_id, from_revision, to_revision) -> TaskGraphDiffV1
convergence(mission_id) -> TaskGraphConvergenceViewV1
```

本次唯一模式为 **FULL_BOUNDED_SNAPSHOT**。按照实际绑定的 GraphStructureBudget 全量、有界返回；`next_cursor=null`、`complete=true`。超限返回 BOUND_REACHED，不截断、不拼接不同时间的数据。**不开放 cursor/limit 参数，不承诺尚未实现的分页。** UI 的树和执行图读取同一 token，均无业务写权限。

`snapshot(revision=历史版)`返回`view_mode=HISTORICAL_STRUCTURE`、两个frontier为空、节点phase为`HISTORY_ONLY`、reason=`HISTORICAL_VIEW_NON_EXECUTABLE`，只显示pinned generation，不伪装可执行。当前页为`view_mode=CURRENT`，运行状态与结构共同绑定read token。

`why_not_ready`返回当前reason及合法source refs、readtoken，不调用LLM；`diff`只比两个immutable记录，状态变化另读事件；`convergence`显示job及未决对象，不自行标reconciled。UI无权限修改edges/TaskStatus。

Host实际route/Tauri command路径不可读：allowlist只限本地source-map确认的现有Mission读接口装配与对应store/component；逻辑映射精确为 `taskgraph.snapshot`→上述SDK，命令native/HTTP由现有transport承载，不新建Node网关。缺route不影响纯SDK实现，但Host seam验收保持PENDING，不宣称产品已上线。

## 12. 错误分类与决策优先级

同一检查阶段按typed code排序，反馈保留field/source identity；不按异常文本contains做分类。优先级：身份/协议→request stale→来源不可读→权限→方法/参数/前提→结构/数据/覆盖→runtime需收敛→预算→正式Commit冲突。

| 新图内部状态 | H1/外部可见处理 |
|---|---|
| source缺失/读不完整/codec不支持 | SOURCE_UNAVAILABLE，H1 INTERNAL_CONTRACT_ERROR；不消耗格式重问次数 |
| corrupt pins/悬空端点/图损坏 | GRAPH_INTEGRITY + STRUCTURE_INVALID；停止受影响Mission新派发 |
| ORDER/REFINEMENT循环 | 原 ORDER_CYCLE / REFINEMENT_CYCLE |
| declared data缺失/歧义/schema不兼容 | DATA_UNBOUND；尚未完成producer单独WAITING_DATA |
| 刚失效的输入/witness/授权 | REQUEST_BINDING_STALE 或 VALIDITY_RECHECK_PENDING，按所在阶段 |
| 合法候选但旧work/operation未决 | DEFERRED，原 RUNNING_WORK_NOT_RECONCILED / OPERATION_UNRESOLVED |
| 需要扩图但预算/fuel不足 | 原BUDGET_INSUFFICIENT/PLANNING_BOUND_REACHED |
| 同命令不同payload | COMMAND_PAYLOAD_CONFLICT/StoreConflict，不让模型修JSON掩盖 |
| 历史版本无原始record | HISTORICAL_STRUCTURE_UNAVAILABLE，仅诊断，不用latest补 |

所有enum映射必须全集测试；未知新enum fail-closed。WAIT/NO_CHANGE/DECLARE_BLOCKED仍按补遗不进入此图编译/operation管线，不写TaskGraphRevisionRecorded。

## 13. 逐文件施工与allowlist

所有SDK前缀 `src/agent_orchestrator/`。`NEW` 是本文明确授权新增；实际等价文件存在则在source-map指向唯一实现，禁止复制两套。

| 文件 | 实施内容 | 必须调用的真实来源/消费者 |
|---|---|---|
| orchestrator/taskgraph_policy.py NEW | 认证kernel opt-in与CAPTURED_BASELINE | 原caller/委派/CommitReceipt及现有政策 |
| graph/execution_contracts.py NEW | 完整输入/结果union、source/token/effects类型 | 导入原SDK和H1补遗类型，不复制领域合同 |
| graph/network_codec.py NEW | NetworkDocument编码解码、全hash和重复identity检查 | 原 to_json/from_json；严格codec manifest |
| storage/taskgraph_schema.py NEW | 附录DDL原样注册下一migration | 原Migration runner |
| storage/taskgraph_store.py NEW | §6.3全部读写/查询/约束接口 | 原Store同连接 |
| orchestrator/taskgraph_sources.py NEW | §3完整source生产与snapshot边界 | amendment授权/操作、原HtnStore/Budget/Validity |
| graph/task_network.py | 保留投影内核，pending/共享/完整端点规则 | 当前Method/ChildBinding；不硬编码业务域 |
| graph/projection_validation.py | 完整检查分类、规模、循环反例 | H1 preview同一report mapper |
| planning/htn/validation.py | 显式pending compound≠NOT_CHECKED | 不忽略缺evidence/registry |
| planning/plan_preview.py | 加graph effects/pins/document；仍纯 | 复用H1H-ADM compile pipeline |
| planning/repair/impact.py NEW或扩展 | reverse DATA/support和需求保留 | CompleteRead，不信model affected hints |
| orchestrator/plan_commits.py | 同事务写记录/index/outbox；精确retiring targets | 保留原identity/read-set/budget/CommitReceipt |
| orchestrator/planning_admission_commits.py | 复核全部来源+graph fence/effects | 既有planning authority+operations guard |
| orchestrator/taskgraph_convergence.py NEW | begin/advance/abandon状态机 | 原P2.3s取消/lease/核对，严禁直接close call |
| artifacts/input_bindings.py | 保留resolver，显式完整空/歧义/转换行为 | witness/purpose/schema严格检查 |
| artifacts/versioning.py / workspace.py | 新kernel只按原manifest物化/读取CAS | legacy路径不改 |
| orchestrator/hierarchical_dispatch.py | pinned reader，no-empty-fallback，当前read context | 逻辑移到新小模块；compound phases/实际rootReview复用 |
| orchestrator/event_handler.py | 实际派发binding同事务与followup调用 | 不复制Allocator/Planner/verification逻辑 |
| runtime/agent_worker.py / provider_budget_guard.py / tool_gateway.py | 从原执行authorizer连接临时fence和绑定复核 | 不改execution.db事实接口或新增可绕过入口 |
| orchestrator/action_commits.py | 保留H1 bridge/handoff guard，加入Task fence查询 | 真实操作身份不改 |
| orchestrator/taskgraph_followups.py NEW | §10消费三种通知、receipt后ACK | 原各类实际工作入口 |
| observability/taskgraph_replay.py NEW | 离线重建和覆盖报告 | 不读live projection补数据 |
| api/taskgraph.py NEW | §11固定caller只读快照/why/diff/convergence | UI只能消费正式结果 |
| tests/orchestrator/full_target/taskgraph_exec/ NEW | test cases/source/SQL/e2e/fault/stateful | 见 §15 |

**不在allowlist：**新公开PlanningDecision类型、H1 decode-only enablement、NanoJev、用户长期记忆、模型路由大改、真实无人机、发布vendor pin、全局RBAC、替换Op账本、通用solver。若某现有函数因新接口必须小改，source-map列出具体调用边和对应负测；不能把整仓授权给一次重构。

## 14. 实施顺序：完整切片，不是先写空接口

| 施工单元 | 完整交付 | 结束验收 |
|---|---|---|
| TG-A 精确结构与SQL | policy opt-in、现有表读写、immutable record/pins、历史结构/overlay、真实迁移 | S组；本地升级库与新建库一致，legacy不变 |
| TG-B 纯编译与真实输入 | H1 preview、ORDER/DATA、actual resolver、共享需求、误输入拒绝 | D/S/A组；真实compiler/Store，不mock PASS |
| TG-C 原子提交与派发 | H1 guard→原Commit→原Attempt/manifest/intent、闭环Review/Resolution | A/C/V组；真实调用身份及费用 |
| TG-D 动态收敛与恢复 | 两阶段修复、共享保留、fence、UNKNOWN、followups、进程强退 | R/C组；不重复operation/派发，不掉责任 |
| TG-E Host与整体集成 | 只读API、现有Host接线、图replay、模型smoke、性能上限 | V组+全H1门禁+独立审核 |

TG-A可在H1验收前做离线开发；TG-C/D真实运行要求H1H-ADM生产seam先通过。每片先写能打断错误的测试，再改实现，最后独立核验。后续依赖接口与SQL本次都已规定，不允许实施时扩大wire/放松guard来跳过。

## 15. 验收：42组场景 + 12个定点变异

完整输入/步骤/断言/预计路径在`sdk-test-cases.json`。所有条目标为PENDING，参考单元测试不自动替它们填PASS。分组：S结构8、D数据6、A来源准入6、R修复8、C事务恢复8、V验收Host6。

**真实内核要求：**只stub模型与受控外部测试目标；compiler、Store、H1 producers、Commit、manifest resolver、实际status reducer必须真实。安全层失败测试必须记录“无新增计划/无新DispatchIntent/无外部调用/原预算未错误释放”，不能只断言Exception。

**属性测试：**使用现有锁文件下Hypothesis stateful（未安装时作为开发依赖明确加入并锁定），200 examples×50步骤，生成新增/细化/接受/撤回/取消/换方法/重启。每步对照完整重算；保存seed和最小反例。此项不是模型token测试。

**12个变异必须被对应行为断言杀死：**取消grant复核、操作读失败改空、历史reader取latest、忽略DATA只用ORDER、空manifest回退、去掉source集合digest、按旧父批量取消、generation不检查、以Task.CANCELLED视为settled、NOT_CHECKED改PASS、receipt丢失创建新command、compound派普通Worker。import错误/环境错误不算KILLED。

**真实GPT-5.6候选验证：**复用已批准H1固定模型/预算/题集；新增四条代表场景：有序数据链、分支共享并换方法、前提/输入失效后重新规划、Commit回执丢失恢复。每条3次记全结果，不挑成功局。固定Provider驱动的故障注入先通过，真实模型只验证集成质量，不要求它随机“制造”安全故障。存在用户授权或模型环境缺失则保留REAL_MODEL_PENDING，不能以fake模型宣称全完成。

**规模：**默认复用现有GraphStructureBudget 256 live tasks/2048 projection nodes/8192 edges/depth64等实际绑定值；测等于边界和+1，超限显式拒绝。大图实验可用显式测试政策上调，不改默认或宣称性能提升。记录actual latency/内存与SQL次数；不要写未经基线证明的固定token/s或毫秒SLA。

### 15.1 明确的执行命令

在资料包根目录验证本包：

```bash
python -m unittest discover -s tests -v
python scripts/validate_kit.py
python scripts/probe_checkout.py --sdk /Users/denny/projects/simple-harness-sdk-h1h-impl --out ./checkout-probe.json
```

probe只读已有checkout，不安装依赖、不改worktree、不访问生产数据库。最后一项必须在用户机器执行；本环境没有那个路径。

实现后在SDK候选目录：

```bash
uv run --frozen --group dev pytest -q tests/orchestrator/full_target/taskgraph_exec --junitxml=taskgraph-exec.junit.xml
uv run --frozen --group dev pytest -q tests/orchestrator/full_target/h1h_admission
uv run --frozen --group dev pytest -q tests/orchestrator/full_target
uv run --frozen --group dev mypy
```

这些focused目录为本文/补遗明确要求创建的测试目录；已有等价nodeid登记复用。mutation/旧模式/H1-I用现有任务书的实际runner，将完整命令和配置hash登记，不编造一个不存在的SDK CLI。已有红项与新增红项分开，必需用例不能skip；“只通过focused”不能标全部完成。

## 16. 最终Done与交付给下一Agent的资料

必须同时获得：

- PRODUCER_MAP_COMPLETE：24项来源均有真实路径、join、完整性与复核测试；无默认放行。
- TASKGRAPH_SQL_PASS：新建/升级/错误key/跨Mission/同事务rollback/parent chain/重复Attempt等通过；H1H-ADM组合迁移也通过。
- TASKGRAPH_CORE_PASS：全部42组实际SDK测试、12变异、stateful、legacy回归完成。
- TASKGRAPH_REAL_MODEL：获准GPT-5.6集成结果记录，未完成标PENDING。
- TASKGRAPH_HOST_SEAM：实际Host只读接口/恢复/协议错误显示完成，未完成标PENDING。
- INDEPENDENT_REVIEW：独立核验会话核对真实source、diff、回执与失败处理。

交付目录包含：本文、source-map/dirty文件hash、migration编号与checksum、42组nodeid映射、focused/全回归/变异/故障报告、真实模型manifest、Host接口map、review及disposition。模型日志默认摘要+hash，必要回读原记录，避免不断贴全部轨迹消耗token。

本计划通过仅能证明指定kernel/支持范围的TaskGraph完整接线；不代表60项目标全部完成，不声称普遍任务成功或行业认证。

## 17. 给实施Agent的直接指令

> 先读取TG-EXEC-2.0与H1H-ADM-1.0，在当前真实候选上登记source-map和未提交改动，不切换到远端参考。复用现有HTN/TaskNetwork/Commit/Budget/Operation与H1分阶段preview；新增SQL只承载明确的历史pins、需求投影、执行输入关联和收敛/通知。严格按TG-A→E完成每条来源到消费者的路径。不得用latest补历史、用空manifest表示解析失败、用Task终态代替真实核对、按退休父取消共享生产者、或让Graph/模型自行签Acceptance。H1只解码类型和NanoJev延后继续有效。任何未能取得的现实来源明确SOURCE_UNAVAILABLE，不把它替换成bool/空tuple/模型解释。按42组真实SDK验收和12变异证明行为，不能只交接口/SQL执行成功/参考测试结果。

## 18. 实际验证边界

本轮可以运行的是本包reference算法、JSON Schema、SQL在注明的parent契约fixture上的测试和文档检查；不能读取或执行用户未提交worktree。`VALIDATION.md`只列真实执行结果；实际SDK/Host/模型测试保持PENDING。SQL以本轮已读原表关键列和PK/FK为依据，生产迁移还必须在本地完整schema复制库上执行一次；fixture通过不等同完整SDK迁移通过。

## 附录A：完整新增DDL

> 本DDL是生产增量合同，需要通过原migration runner注册下一未占用版本；**tests/parent_fixture.sql不属于生产迁移**。原表完整DDL继续以实际已安装SDK为准。

```sql
-- TG-EXEC-2.0, additive migration. Never execute on a live database by hand.
-- Register next UNUSED migration after H1H-ADM-1.0. Existing table names/keys are checked in preflight.
-- Requires SQLite STRICT support, foreign_keys=ON. Transaction owned by Store migration runner.
CREATE TABLE taskgraph_policy_bindings (
 mission_id TEXT PRIMARY KEY NOT NULL REFERENCES missions(mission_id),
 kernel_version TEXT NOT NULL CHECK(kernel_version='taskgraph-exec-v2'),
 policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 enabling_command_id TEXT NOT NULL UNIQUE,
 enabling_receipt_hash TEXT NOT NULL CHECK(length(enabling_receipt_hash)=64),
 created_at REAL NOT NULL
) STRICT;

-- Immutable replay source; existing plan_revisions remains the ONLY active pointer.
-- network_json is canonical NetworkDocumentV1. Runtime states/permissions are NOT copied into it.
CREATE TABLE taskgraph_revision_records (
 mission_id TEXT NOT NULL REFERENCES taskgraph_policy_bindings(mission_id),
 revision INTEGER NOT NULL CHECK(revision>=0),
 parent_revision INTEGER,
 parent_manifest_hash TEXT,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('SEED_COMMIT','COMMIT','CAPTURED_BASELINE')),
 requirements_revision INTEGER NOT NULL CHECK(requirements_revision>=0),
 manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),
 sdk_snapshot_hash TEXT NOT NULL CHECK(length(sdk_snapshot_hash)=64),
 network_json TEXT NOT NULL CHECK(json_valid(network_json)),
 certificate_json TEXT NOT NULL CHECK(json_valid(certificate_json)),
 admission_check_id TEXT REFERENCES planning_admission_checks(check_id),
 event_id TEXT NOT NULL REFERENCES events(event_id),
 command_id TEXT NOT NULL,
 created_at REAL NOT NULL,
 PRIMARY KEY(mission_id,revision),
 UNIQUE(mission_id,revision,manifest_hash),
 UNIQUE(mission_id,command_id),
 FOREIGN KEY(mission_id,revision) REFERENCES plan_revisions(mission_id,revision),
 FOREIGN KEY(mission_id,requirements_revision) REFERENCES requirements_revisions(mission_id,revision),
 FOREIGN KEY(mission_id,parent_revision,parent_manifest_hash)
   REFERENCES taskgraph_revision_records(mission_id,revision,manifest_hash),
 CHECK((source_kind='CAPTURED_BASELINE' AND admission_check_id IS NULL) OR
       (source_kind IN ('SEED_COMMIT','COMMIT') AND admission_check_id IS NOT NULL)),
 CHECK((parent_revision IS NULL AND parent_manifest_hash IS NULL AND source_kind IN ('SEED_COMMIT','CAPTURED_BASELINE')) OR
       (parent_revision IS NOT NULL AND parent_manifest_hash IS NOT NULL AND length(parent_manifest_hash)=64 AND revision=parent_revision+1 AND source_kind='COMMIT'))
) STRICT;

CREATE TABLE taskgraph_member_pins (
 mission_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 occurrence_id TEXT NOT NULL,
 task_id TEXT NOT NULL,
 binding_revision INTEGER NOT NULL CHECK(binding_revision>=0),
 binding_hash TEXT NOT NULL CHECK(length(binding_hash)=64),
 PRIMARY KEY(mission_id,revision,occurrence_id),
 FOREIGN KEY(mission_id,revision) REFERENCES taskgraph_revision_records(mission_id,revision),
 FOREIGN KEY(mission_id,revision,occurrence_id) REFERENCES plan_memberships(mission_id,revision,occurrence_id),
 FOREIGN KEY(task_id,binding_revision) REFERENCES task_semantics(task_id,binding_revision)
) STRICT;
CREATE INDEX taskgraph_member_pins_task_idx ON taskgraph_member_pins(mission_id,task_id,revision);

CREATE TABLE taskgraph_method_pins (
 mission_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 instance_id TEXT NOT NULL,
 goal_occurrence_id TEXT NOT NULL,
 adopted INTEGER NOT NULL CHECK(adopted IN (0,1)),
 draft_hash TEXT NOT NULL CHECK(length(draft_hash)=64),
 PRIMARY KEY(mission_id,revision,instance_id),
 FOREIGN KEY(mission_id,revision) REFERENCES taskgraph_revision_records(mission_id,revision),
 FOREIGN KEY(mission_id,instance_id) REFERENCES method_instances(mission_id,instance_id),
 FOREIGN KEY(mission_id,revision,goal_occurrence_id) REFERENCES plan_memberships(mission_id,revision,occurrence_id)
) STRICT;
CREATE UNIQUE INDEX taskgraph_one_adopted_per_goal_idx
 ON taskgraph_method_pins(mission_id,revision,goal_occurrence_id) WHERE adopted=1;

-- Derived demand index, not a second financial ledger. One row per adopted slot.
CREATE TABLE taskgraph_demand_refs (
 mission_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 consumer_instance_id TEXT NOT NULL,
 slot_key TEXT NOT NULL,
 slot_occurrence_id TEXT NOT NULL,
 producer_occurrence_id TEXT NOT NULL,
 obligation_id TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('new_work','reuse_accepted','share_active')),
 requiredness TEXT NOT NULL CHECK(requiredness IN ('required','optional_authorized','conditional')),
 source_slot_hash TEXT NOT NULL CHECK(length(source_slot_hash)=64),
 PRIMARY KEY(mission_id,revision,consumer_instance_id,slot_key),
 FOREIGN KEY(mission_id,revision,consumer_instance_id) REFERENCES taskgraph_method_pins(mission_id,revision,instance_id),
 FOREIGN KEY(consumer_instance_id,slot_key) REFERENCES method_child_occurrences(instance_id,slot_key),
 FOREIGN KEY(mission_id,revision,slot_occurrence_id) REFERENCES plan_memberships(mission_id,revision,occurrence_id),
 FOREIGN KEY(mission_id,revision,producer_occurrence_id) REFERENCES plan_memberships(mission_id,revision,occurrence_id),
 FOREIGN KEY(mission_id,obligation_id) REFERENCES obligations(mission_id,obligation_id)
) STRICT;
CREATE INDEX taskgraph_demand_producer_idx ON taskgraph_demand_refs(mission_id,revision,producer_occurrence_id);

-- Existing input_manifest_bindings may collapse repeated attempts with equal bytes;
-- this is the exact per-attempt association, not another manifest content store.
CREATE TABLE taskgraph_attempt_inputs (
 attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES attempts(attempt_id),
 mission_id TEXT NOT NULL,
 task_id TEXT NOT NULL,
 occurrence_id TEXT NOT NULL,
 source_revision INTEGER NOT NULL,
 binding_revision INTEGER NOT NULL,
 contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64),
 input_binding_revision INTEGER NOT NULL CHECK(input_binding_revision>=0),
 dispatch_generation INTEGER NOT NULL CHECK(dispatch_generation>=0),
 manifest_hash TEXT NOT NULL REFERENCES input_manifests(manifest_hash),
 intent_id TEXT NOT NULL UNIQUE REFERENCES dispatch_intents(intent_id),
 creation_key TEXT NOT NULL UNIQUE,
 input_id TEXT NOT NULL,
 frozen_input_hash TEXT NOT NULL CHECK(length(frozen_input_hash)=64),
 admission_check_id TEXT REFERENCES planning_admission_checks(check_id),
 origin_hash TEXT NOT NULL CHECK(length(origin_hash)=64),
 created_at REAL NOT NULL,
 FOREIGN KEY(mission_id,source_revision,occurrence_id) REFERENCES plan_memberships(mission_id,revision,occurrence_id),
 FOREIGN KEY(task_id,binding_revision) REFERENCES task_semantics(task_id,binding_revision)
) STRICT;
CREATE INDEX taskgraph_attempt_inputs_occ_idx ON taskgraph_attempt_inputs(mission_id,occurrence_id,dispatch_generation);

-- Durable convergence coordination. It never records the external effect as resolved.
CREATE TABLE taskgraph_convergence_jobs (
 job_id TEXT PRIMARY KEY NOT NULL,
 mission_id TEXT NOT NULL REFERENCES taskgraph_policy_bindings(mission_id),
 decision_id TEXT NOT NULL REFERENCES planning_decisions(decision_id),
 request_id TEXT NOT NULL REFERENCES planning_requests(request_id),
 command_id TEXT NOT NULL,
 source_revision INTEGER NOT NULL,
 candidate_hash TEXT NOT NULL CHECK(length(candidate_hash)=64),
 impact_hash TEXT NOT NULL CHECK(length(impact_hash)=64),
 state TEXT NOT NULL CHECK(state IN ('FENCED','WAITING','READY','APPLIED','ABANDONED')),
 row_version INTEGER NOT NULL CHECK(row_version>=1),
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(mission_id,decision_id),
 FOREIGN KEY(mission_id,source_revision) REFERENCES taskgraph_revision_records(mission_id,revision)
) STRICT;
CREATE TABLE taskgraph_convergence_targets (
 job_id TEXT NOT NULL REFERENCES taskgraph_convergence_jobs(job_id),
 mission_id TEXT NOT NULL,
 source_revision INTEGER NOT NULL,
 occurrence_id TEXT NOT NULL,
 task_id TEXT NOT NULL,
 expected_generation INTEGER NOT NULL CHECK(expected_generation>=0),
 target_kind TEXT NOT NULL CHECK(target_kind IN ('RETIRING','INPUT_REPLACED')),
 PRIMARY KEY(job_id,occurrence_id),
 FOREIGN KEY(mission_id,source_revision,occurrence_id) REFERENCES plan_memberships(mission_id,revision,occurrence_id)
) STRICT;
CREATE INDEX taskgraph_fence_lookup_idx ON taskgraph_convergence_targets(mission_id,task_id,job_id);

-- Only graph follow-up notifications. Original dispatch_intents remains the work handoff authority.
CREATE TABLE taskgraph_followups (
 message_id TEXT PRIMARY KEY NOT NULL,
 mission_id TEXT NOT NULL REFERENCES taskgraph_policy_bindings(mission_id),
 source_event_id TEXT NOT NULL REFERENCES events(event_id),
 kind TEXT NOT NULL CHECK(kind IN ('REEVALUATE','CONVERGE','REQUEST_COMPOSITION')),
 subject_key TEXT NOT NULL,
 dedupe_key TEXT NOT NULL UNIQUE,
 payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 delivery_state TEXT NOT NULL CHECK(delivery_state IN ('PENDING','LEASED','ACKED','BLOCKED')),
 lease_owner TEXT,
 lease_until_ms INTEGER,
 attempts INTEGER NOT NULL CHECK(attempts>=0),
 row_version INTEGER NOT NULL CHECK(row_version>=1),
 next_attempt_ms INTEGER NOT NULL CHECK(next_attempt_ms>=0),
 last_error_code TEXT,
 consumer_receipt_json TEXT CHECK(consumer_receipt_json IS NULL OR json_valid(consumer_receipt_json)),
 CHECK((delivery_state='ACKED' AND consumer_receipt_json IS NOT NULL) OR
       (delivery_state<>'ACKED' AND consumer_receipt_json IS NULL)),
 CHECK((delivery_state='LEASED' AND lease_owner IS NOT NULL AND lease_until_ms IS NOT NULL) OR
       (delivery_state<>'LEASED' AND lease_owner IS NULL AND lease_until_ms IS NULL))
) STRICT;
CREATE INDEX taskgraph_followups_poll_idx ON taskgraph_followups(delivery_state,next_attempt_ms,mission_id);

CREATE TRIGGER tg_revision_source_guard BEFORE INSERT ON taskgraph_revision_records BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM events e WHERE e.event_id=NEW.event_id AND e.mission_id=NEW.mission_id
 ) THEN RAISE(ABORT,'TG_REVISION_EVENT_MISMATCH') END;
 SELECT CASE WHEN NEW.source_kind<>'CAPTURED_BASELINE' AND NOT EXISTS (
  SELECT 1 FROM planning_admission_checks c JOIN planning_requests r ON r.request_id=c.request_id
  WHERE c.check_id=NEW.admission_check_id AND c.phase='APPLIED' AND r.mission_id=NEW.mission_id
 ) THEN RAISE(ABORT,'TG_REVISION_ADMISSION_MISMATCH') END;
END;
CREATE TRIGGER tg_pin_subject_guard BEFORE INSERT ON taskgraph_member_pins BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM task_semantics s JOIN plan_memberships m
    ON m.mission_id=NEW.mission_id AND m.revision=NEW.revision AND m.occurrence_id=NEW.occurrence_id
  WHERE s.task_id=NEW.task_id AND s.binding_revision=NEW.binding_revision
    AND s.mission_id=NEW.mission_id AND m.task_id=s.task_id AND m.obligation_id=s.obligation_id
    AND m.form=s.form AND s.content_hash=NEW.binding_hash
 ) THEN RAISE(ABORT,'TG_PIN_IDENTITY_MISMATCH') END;
END;
CREATE TRIGGER tg_method_subject_guard BEFORE INSERT ON taskgraph_method_pins BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM method_instances i WHERE i.mission_id=NEW.mission_id AND i.instance_id=NEW.instance_id
   AND i.goal_occurrence_id=NEW.goal_occurrence_id
 ) THEN RAISE(ABORT,'TG_METHOD_IDENTITY_MISMATCH') END;
END;
CREATE TRIGGER tg_demand_subject_guard BEFORE INSERT ON taskgraph_demand_refs BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM taskgraph_method_pins p JOIN method_child_occurrences c
   ON c.instance_id=p.instance_id AND c.mission_id=p.mission_id
  WHERE p.mission_id=NEW.mission_id AND p.revision=NEW.revision
   AND p.instance_id=NEW.consumer_instance_id AND p.adopted=1 AND c.slot_key=NEW.slot_key
   AND c.occurrence_id=NEW.slot_occurrence_id AND c.goal_occurrence_id=NEW.producer_occurrence_id
   AND c.obligation_id=NEW.obligation_id AND c.reuse_policy=NEW.mode AND c.requiredness=NEW.requiredness
 ) THEN RAISE(ABORT,'TG_DEMAND_IDENTITY_MISMATCH') END;
END;
CREATE TRIGGER tg_attempt_identity_guard BEFORE INSERT ON taskgraph_attempt_inputs BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM attempts a JOIN dispatch_intents d ON d.subject_id=a.attempt_id
  JOIN plan_memberships m ON m.mission_id=a.mission_id AND m.task_id=a.task_id
  JOIN task_semantics s ON s.task_id=a.task_id AND s.binding_revision=NEW.binding_revision
  JOIN taskgraph_revision_records rr ON rr.mission_id=NEW.mission_id AND rr.revision=NEW.source_revision
  LEFT JOIN planning_admission_checks c ON c.check_id=NEW.admission_check_id
  LEFT JOIN planning_requests r ON r.request_id=c.request_id
  JOIN input_manifest_bindings b ON b.mission_id=a.mission_id AND b.task_id=a.task_id
   AND b.manifest_hash=NEW.manifest_hash AND b.input_binding_revision=NEW.input_binding_revision
  WHERE a.attempt_id=NEW.attempt_id AND a.mission_id=NEW.mission_id AND a.task_id=NEW.task_id
   AND d.intent_id=NEW.intent_id AND d.mission_id=NEW.mission_id
   AND d.creation_key=NEW.creation_key AND d.input_id=NEW.input_id AND d.input_hash=NEW.frozen_input_hash
   AND m.revision=NEW.source_revision AND m.occurrence_id=NEW.occurrence_id AND m.form='primitive'
   AND s.mission_id=NEW.mission_id AND s.form='primitive'
   AND s.input_binding_revision=NEW.input_binding_revision AND s.dispatch_generation=NEW.dispatch_generation
   AND ((rr.source_kind='CAPTURED_BASELINE' AND rr.admission_check_id IS NULL AND NEW.admission_check_id IS NULL)
     OR (rr.source_kind<>'CAPTURED_BASELINE' AND rr.admission_check_id=NEW.admission_check_id
         AND r.mission_id=NEW.mission_id AND c.phase='APPLIED'))
 ) THEN RAISE(ABORT,'TG_ATTEMPT_IDENTITY_MISMATCH') END;
END;
CREATE TRIGGER tg_convergence_identity_guard BEFORE INSERT ON taskgraph_convergence_jobs BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM planning_requests r JOIN planning_decisions d ON d.request_id=r.request_id
  WHERE r.request_id=NEW.request_id AND d.decision_id=NEW.decision_id AND r.mission_id=NEW.mission_id
 ) THEN RAISE(ABORT,'TG_CONVERGENCE_IDENTITY_MISMATCH') END;
END;
CREATE TRIGGER tg_target_identity_guard BEFORE INSERT ON taskgraph_convergence_targets BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM taskgraph_convergence_jobs j JOIN plan_memberships m
   ON m.mission_id=j.mission_id AND m.revision=j.source_revision
  WHERE j.job_id=NEW.job_id AND j.mission_id=NEW.mission_id AND j.source_revision=NEW.source_revision
   AND m.occurrence_id=NEW.occurrence_id AND m.task_id=NEW.task_id
 ) THEN RAISE(ABORT,'TG_TARGET_IDENTITY_MISMATCH') END;
END;
CREATE TRIGGER tg_followup_owner_guard BEFORE INSERT ON taskgraph_followups BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM events e WHERE e.event_id=NEW.source_event_id AND e.mission_id=NEW.mission_id
 ) THEN RAISE(ABORT,'TG_FOLLOWUP_OWNER_MISMATCH') END;
END;

CREATE TRIGGER taskgraph_policy_bindings_no_update BEFORE UPDATE ON taskgraph_policy_bindings BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_policy_bindings_no_delete BEFORE DELETE ON taskgraph_policy_bindings BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;

CREATE TRIGGER taskgraph_revision_records_no_update BEFORE UPDATE ON taskgraph_revision_records BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_revision_records_no_delete BEFORE DELETE ON taskgraph_revision_records BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;

CREATE TRIGGER taskgraph_member_pins_no_update BEFORE UPDATE ON taskgraph_member_pins BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_member_pins_no_delete BEFORE DELETE ON taskgraph_member_pins BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;

CREATE TRIGGER taskgraph_method_pins_no_update BEFORE UPDATE ON taskgraph_method_pins BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_method_pins_no_delete BEFORE DELETE ON taskgraph_method_pins BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;

CREATE TRIGGER taskgraph_demand_refs_no_update BEFORE UPDATE ON taskgraph_demand_refs BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_demand_refs_no_delete BEFORE DELETE ON taskgraph_demand_refs BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;

CREATE TRIGGER taskgraph_attempt_inputs_no_update BEFORE UPDATE ON taskgraph_attempt_inputs BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_attempt_inputs_no_delete BEFORE DELETE ON taskgraph_attempt_inputs BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;

CREATE TRIGGER taskgraph_convergence_targets_no_update BEFORE UPDATE ON taskgraph_convergence_targets BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER taskgraph_convergence_targets_no_delete BEFORE DELETE ON taskgraph_convergence_targets BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;

-- These constrain permitted database transitions, but DO NOT replace real convergence/receipt checks.
CREATE TRIGGER tg_convergence_transition BEFORE UPDATE ON taskgraph_convergence_jobs BEGIN
 SELECT CASE WHEN NEW.job_id<>OLD.job_id OR NEW.mission_id<>OLD.mission_id
  OR NEW.decision_id<>OLD.decision_id OR NEW.request_id<>OLD.request_id
  OR NEW.command_id<>OLD.command_id OR NEW.source_revision<>OLD.source_revision
  OR NEW.candidate_hash<>OLD.candidate_hash OR NEW.impact_hash<>OLD.impact_hash
  OR NEW.row_version<>OLD.row_version+1
  THEN RAISE(ABORT,'TG_CONVERGENCE_IDENTITY_OR_VERSION') END;
 SELECT CASE WHEN NOT (
   (OLD.state='FENCED' AND NEW.state IN ('WAITING','READY','ABANDONED')) OR
   (OLD.state='WAITING' AND NEW.state IN ('WAITING','READY','ABANDONED')) OR
   (OLD.state='READY' AND NEW.state IN ('WAITING','APPLIED','ABANDONED'))
 ) THEN RAISE(ABORT,'TG_CONVERGENCE_TRANSITION') END;
END;
CREATE TRIGGER tg_convergence_no_delete BEFORE DELETE ON taskgraph_convergence_jobs BEGIN
 SELECT RAISE(ABORT,'TG_IMMUTABLE'); END;
CREATE TRIGGER tg_followup_transition BEFORE UPDATE ON taskgraph_followups BEGIN
 SELECT CASE WHEN NEW.message_id<>OLD.message_id OR NEW.mission_id<>OLD.mission_id
  OR NEW.source_event_id<>OLD.source_event_id OR NEW.kind<>OLD.kind
  OR NEW.subject_key<>OLD.subject_key OR NEW.dedupe_key<>OLD.dedupe_key
  OR NEW.payload_hash<>OLD.payload_hash OR NEW.payload_json<>OLD.payload_json
  OR NEW.row_version<>OLD.row_version+1
  THEN RAISE(ABORT,'TG_FOLLOWUP_IDENTITY_OR_VERSION') END;
 SELECT CASE WHEN NOT (
   (OLD.delivery_state='PENDING' AND NEW.delivery_state='LEASED') OR
   (OLD.delivery_state='LEASED' AND NEW.delivery_state IN ('PENDING','LEASED','ACKED','BLOCKED')) OR
   (OLD.delivery_state='BLOCKED' AND NEW.delivery_state='PENDING')
 ) THEN RAISE(ABORT,'TG_FOLLOWUP_TRANSITION') END;
END;

```

## 附录B：全部标准读取与CAS查询

以下是具名Store查询模板，不作为一个migration连续执行。所有`:name`都绑定参数，SQL表名/列名只在代码定义，不能来自模型。

```sql
-- Named read/guard/CAS templates. Named parameters are bound values, NEVER interpolated.
-- These statements are not one executable migration. Use by the named Store methods in PLAN.

-- Q01: authoritative active pointer. More than one is prohibited by existing unique index.
SELECT revision,snapshot_hash FROM plan_revisions
 WHERE mission_id=:mission_id AND state='ACTIVE';

-- Q02: historical task contract. Never use latest_task_semantics for a historical revision.
SELECT p.occurrence_id,p.task_id,p.binding_revision,p.binding_hash,s.binding_json
 FROM taskgraph_member_pins p JOIN task_semantics s
 ON s.task_id=p.task_id AND s.binding_revision=p.binding_revision
 WHERE p.mission_id=:mission_id AND p.revision=:revision AND s.mission_id=:mission_id
 ORDER BY p.occurrence_id;

-- Q03: historical method adoption. Current method_instances.state is NOT historical adoption.
SELECT p.instance_id,p.goal_occurrence_id,p.adopted,p.draft_hash,i.draft_json
 FROM taskgraph_method_pins p JOIN method_instances i
 ON i.mission_id=p.mission_id AND i.instance_id=p.instance_id
 WHERE p.mission_id=:mission_id AND p.revision=:revision ORDER BY p.instance_id;

-- Q04: active shared consumers. Complete rows are hashed as a set in the read certificate.
SELECT d.* FROM taskgraph_demand_refs d JOIN plan_revisions r
 ON r.mission_id=d.mission_id AND r.revision=d.revision
 WHERE d.mission_id=:mission_id AND d.producer_occurrence_id=:producer_occurrence_id
 AND r.state='ACTIVE' ORDER BY d.consumer_instance_id,d.slot_key;

-- Q05: immutable Attempt input. No "latest producer" lookup on replay.
SELECT b.*,m.manifest_json FROM taskgraph_attempt_inputs b
 JOIN input_manifests m ON m.manifest_hash=b.manifest_hash
 WHERE b.mission_id=:mission_id AND b.attempt_id=:attempt_id;

-- Q06: authoritative fence. READY still fenced until commit or authorized safe abandonment.
SELECT t.job_id,t.occurrence_id,t.expected_generation,j.state,j.row_version
 FROM taskgraph_convergence_targets t JOIN taskgraph_convergence_jobs j ON j.job_id=t.job_id
 WHERE t.mission_id=:mission_id AND t.task_id=:task_id AND j.state IN ('FENCED','WAITING','READY');

-- Q07: convergence CAS. Caller validates legal transition and factual quiescence first.
UPDATE taskgraph_convergence_jobs SET state=:new_state,row_version=row_version+1,updated_at=:now
 WHERE job_id=:job_id AND mission_id=:mission_id AND state=:old_state AND row_version=:expected_version;
-- Require rowcount==1; 0 means conflict, NOT success.

-- Q08: complete endpoint audit for old tables (SQL FKs alone do not check these endpoints).
SELECT o.before_occurrence,o.after_occurrence FROM order_constraints o
 LEFT JOIN plan_memberships a ON a.mission_id=o.mission_id AND a.revision=o.plan_revision AND a.occurrence_id=o.before_occurrence
 LEFT JOIN plan_memberships b ON b.mission_id=o.mission_id AND b.revision=o.plan_revision AND b.occurrence_id=o.after_occurrence
 WHERE o.mission_id=:mission_id AND o.plan_revision=:revision AND (a.occurrence_id IS NULL OR b.occurrence_id IS NULL);
SELECT d.requirement_id FROM data_requirements d
 LEFT JOIN plan_memberships a ON a.mission_id=d.mission_id AND a.revision=d.plan_revision AND a.occurrence_id=d.producer_occurrence
 LEFT JOIN plan_memberships b ON b.mission_id=d.mission_id AND b.revision=d.plan_revision AND b.occurrence_id=d.consumer_occurrence
 WHERE d.mission_id=:mission_id AND d.plan_revision=:revision AND (a.occurrence_id IS NULL OR b.occurrence_id IS NULL);

-- Q09: notification claim. Select and update in ONE short transaction; no model inside it.
SELECT message_id,row_version FROM taskgraph_followups
 WHERE (delivery_state='PENDING' AND next_attempt_ms<=:now_ms)
 OR (delivery_state='LEASED' AND lease_until_ms<=:now_ms)
 ORDER BY next_attempt_ms,message_id LIMIT 1;
UPDATE taskgraph_followups SET delivery_state='LEASED',lease_owner=:owner,
 lease_until_ms=:lease_until_ms,attempts=attempts+1,row_version=row_version+1
 WHERE message_id=:message_id AND row_version=:expected_version
 AND ((delivery_state='PENDING' AND next_attempt_ms<=:now_ms) OR (delivery_state='LEASED' AND lease_until_ms<=:now_ms));

-- Q10: acknowledge only after existing consumer command/intent has a durable receipt.
UPDATE taskgraph_followups SET delivery_state='ACKED',consumer_receipt_json=:consumer_receipt_json,lease_owner=NULL,lease_until_ms=NULL,row_version=row_version+1
 WHERE message_id=:message_id AND delivery_state='LEASED' AND lease_owner=:owner AND row_version=:expected_version;

-- Q11: read-side watermark. Read in the SAME transaction as task/validity/graph rows.
SELECT COALESCE(MAX(seq),0) AS through_seq FROM events WHERE mission_id=:mission_id;

-- Q12: bounded notification retry. Classify the error before applying; no hidden model retry.
UPDATE taskgraph_followups SET delivery_state=CASE WHEN attempts>=5 THEN 'BLOCKED' ELSE 'PENDING' END,
 lease_owner=NULL,lease_until_ms=NULL,row_version=row_version+1,
 next_attempt_ms=:next_attempt_ms,last_error_code=:error_code
 WHERE message_id=:message_id AND delivery_state='LEASED' AND lease_owner=:owner AND row_version=:expected_version;

-- Q13: explicit operator recovery of a blocked notification, after real source repair.
UPDATE taskgraph_followups SET delivery_state='PENDING',row_version=row_version+1,
 next_attempt_ms=:now_ms,last_error_code=NULL,attempts=0
 WHERE message_id=:message_id AND mission_id=:mission_id AND delivery_state='BLOCKED' AND row_version=:expected_version;

-- Q14: immutable graph record read. Decoder verifies bytes, hash chain and complete pins.
SELECT * FROM taskgraph_revision_records WHERE mission_id=:mission_id AND revision=:revision;

-- Q15: current control binding (only for current runtime checks, never historical reconstruction).
SELECT binding_revision,binding_json,content_hash,input_binding_revision,dispatch_generation
 FROM task_semantics WHERE mission_id=:mission_id AND task_id=:task_id
 ORDER BY binding_revision DESC LIMIT 1;

```

## 附录C：Store与生产者详细算法

# C. Store / producer 逐步实现合同（TG-EXEC-2.0）

本文件是主计划的规范部分。下列步骤必须落到真实SDK Store/Commit，不是允许用测试helper替代。参数只来自固定API调用者、冻结请求、原codec和完整source reader。

## 1. 来源失败分类与具体装配

所有输入分三种：读成功（可以为空）、来源缺失/不完整/解码失败、来源完整但不满足业务要求。第三种返回具体拒绝/等待，不能一律叫系统错误。`CompleteRead`只能由真实reader生成，不提供全True的默认构造器。

### 1.1 已有装配对象的具体使用

- `HierarchicalDispatch.planning` 必须是已安装的 `DeploymentPlanningWorld` 或经验证的同合同实现。声明读取使用其 `catalog/schemas/registry/predicates`；能力读取调用 `capabilities()`，实际由 `world.py::capability_records` 和安装的 `records` 生产。**不新建一个默认 CapabilitySnapshot()。**当前实现对一些可达/兼容轴有缺省值，这只能证明部署声明；真实Tool/Provider执行授权和连通性仍在原handoff门检查。需要额外健康证据的policy没有真实reader时返回unavailable，不把声明升格为健康证明。
- schema兼容读取 `HierarchicalDispatch.resolution_policy.schema_registry`；规则为空可以是“只接受同一schema”的明确policy，不是读异常fallback。
- 目标物化规则来自 `HierarchicalDispatch.target_rules` 与工作区/Operator实际绑定。为None时，非物化的纯图读可以执行；请求落文件的执行必须 `TARGET_RULES_UNAVAILABLE`。不得根据运行Python的OS猜用户指定的远程target规则。
- 证据通过原 `witnesses / start_witness_index / input_witness_index / scope_epochs` 读取。判断授权或current必须用真实consumer/purpose，不复用Planner的PLAN witness作为Worker的START许可。
- 预算读取原 `BudgetLedger.account`（纯读）；`reserve`只在原派发写事务执行，不能出现在snapshot producer。
- `settlement_facts`先读原 `occurrence_outcomes`，再用H1H的running-work/operation证明核对。所有相关Attempt至少处于真实terminal并没有未决provider/tool账目（按cleanup合同），没有 `PENDING/PARTIAL/UNKNOWN` effect，才可产生 `SettledTerminalOccurrence`。

### 1.2 不同用途的输入类型

`StructuralReadContext`字段：caller、policy、NetworkRecord、解码后的TaskNetworkSnapshot、codec_manifest、through_seq。只读取结构历史不需要live planning grant。

`ExecutionReadContext`字段：structure、current_task_bindings、current_method_registrations、witnesses/starts/licences、scope_epochs、accepted_outputs、source_input_policies、target_rules（读取用途可为显式NOT_APPLICABLE联合分支）、active_demands、independent_obligations、running_work、operation_snapshot、execution_policy、budget_snapshots、deployment_capabilities。每个集合都带source receipt和完整性；这些不是随意dict默认值。

`PlanMutationReadContext`字段：execution、PlanningRequestRecord、PlanningDecisionRecord、PlanningAuthorizationSnapshot、AdapterContext、source-candidate限制。其Planning request grant只是改图授权，不能当execute grant。

生成过程：在原Store一次一致读事务读取同库数据，外部execution facts仅通过原可靠导入。结束事务后运行纯compiler。以canonical source digest记录输入；currentness重新检查时重读**相关集合及成员**而不是只查旧成员id。

没有拿到原call结果是 `SourceUnavailable/OutcomeUnknown`，不能把它转换成 `not_running`。

## 2. kernel启用与政策写入

新增 `orchestrator/taskgraph_policy.py::enable_taskgraph_contract(mission_id, command_id, caller)`，由认证Host装配调用，不注册给模型。

1. 调用原facade租户/读取guard；查同command的原commit_receipts。有回执时核对相同意图，再按当前读权返回。
2. 验证当前Mission非legacy、planning-decision-v1、未终结，H1H-ADM依赖就绪并通过实际部署验收，不以环境变量声称通过。
3. 读取实际planning委派、冻结GraphStructureBudget、候选策略、schema/target/deployment政策引用，生成policy JSON。policy内容只来自已安装版本化配置。
4. 已有ACTIVE图时先确认不在派发、真实工作已收敛，执行显式CAPTURED_BASELINE流程；不允许自动在in-flight图上启用。
5. 原Store单写事务复核全部条件，写 `taskgraph_policy_bindings` + 原版本化event + 现有command receipt。`enabling_receipt_hash`为该确定性receipt内容hash，不含动态重放时间。
6. 同Mission已有不同policy不UPDATE旧行。后续政策升级需独立版本裁定；本版本只有一次明确opt-in。

## 3. `read_revision` 与 `insert_revision_record`

### 3.1 编码规则

`NetworkDocumentV1.objects`的kind到原codec映射是主计划§4.1的固定表。对每个原对象：
1. 原 `to_json()` -> 项目既有canonical JSON UTF-8 bytes -> SHA-256。
2. 保存完整canonical JSON字符串和hash，禁止用Python repr/按字段猜摘要。
3. kind+identity重复直接拒绝；顶层集合排序，原对象内部有序数组不重排。
4. roots/required obligations/adopted ids从compiled network显式拷贝并检查引用，不从Task列表推断。
5. `codec_manifest_hash`绑定使用的codec版本及源码hash清单；清单存版本化repo文件，升级不能覆盖旧清单。

### 3.2 `read_revision(mission_id,revision)`

1. 同事务读取原 `plan_revisions` 与Q14；不存在record但有legacy图时返回HISTORICAL_STRUCTURE_UNAVAILABLE，不能补写。
2. 验证NetworkDocument完整Schema、canonical hash、mission/revision与row一致；验证parent chain。
3. 用原codec逐个解码objects；去原不可变表读取时只能用doc中精确revision/hash（不能查today registration状态来重写历史）。
4. Q02、Q03取得pins；比对数量、所有identity、hash以及完整集合digest。`LEFT JOIN`读取并检测缺失，或JOIN后与原pin总数严格相等；不能丢失一行后返回“完整”。
5. pins和objects是一一映射：多余/缺少都错误；ORDER/DATA端点完整检查Q08。
6. 从document显式adopted集合构造TaskNetworkSnapshot；物理`method_instances.state`仅用于当前overlay或新Commit准入，不用于重构历史。
7. 对执行读运行完整projection验证；对历史读返回合法结构及非执行标记。错误返回GraphIntegrityError，不返回排序完成的健康前缀。

### 3.3 写入顺序（必须在外层Commit同一事务）

1. 原Commit先完成受控预算/Task/Method/plan_memberships/ORDER/DATA写入，生成原PlanCommitReceipt。
2. H1H-ADM写**本次实际成功**的APPLIED check；没有提前为未来结果造PASS。
3. 生成NetworkDocument hash及TaskGraphRevisionRecorded事件。
4. 写revision_record（COMMIT/SEED_COMMIT需要APPLIED check；CAPTURED_BASELINE只允许NULL，并由真实baseline授权receipt解释）。
5. 遍历结果网络所有occurrence，写member pin。必须核对原Task binding身份、mission、obligation、form与hash。不是只写新节点。
6. 遍历结果网络所有MethodInstance，写method pin及本revision adopted状态；不是只存当前全局state。
7. 从本revision **adopted** Method 的真实ChildBinding生成demand_refs；child的occurrence/goal_occurrence两个身份严格核对，拒绝无生产者的共享引用。独立Obligation不用伪造一个consumer slot，单独从原账本查。
8. 完整比较所有成员/方法/需求集合，追加followup，收敛job置APPLIED；最外层提交。

任何阶段失败，原预算与Task写入一并rollback。现有函数内部savepoint不能提交外层。

## 4. 派发绑定：一份manifest不等于一次Attempt

`insert_attempt_inputs`只由原实际创建Attempt/DispatchIntent入口调用。字段来源：

| 字段 | 精确生产来源 |
|---|---|
| attempt_id/task_id/mission_id | 原已创建但未提交的Attempt row |
| occurrence_id/source_revision | 当前ACTIVE plan_member，明确参与该次执行 |
| binding_revision/contract_hash | 本次正在核验的TaskSemanticBinding，不从旧preview猜 |
| input_binding_revision/dispatch_generation | 同一binding中的原控制字段 |
| manifest_hash | 原resolver成功生成的完整冻结InputManifest |
| intent_id/creation_key/input_id/frozen_input_hash | 同一次原DispatchIntent内容 |
| admission_check_id | **source_revision对应revision_record**的APPLIED check；CAPTURED_BASELINE明确NULL |
| origin_hash | canonical上述身份、manifest及intent，不含created_at |

首次检查 `taskgraph_attempt_inputs` 已存在：比较上述全语义字段；相同返回原行，不重复reserve；不同StoreConflict。创建两个合法Attempt使用相同manifest时，保留两条attempt关联，而不是覆盖原input_manifest_bindings中的attempt_id。

派发事务仍要执行原execution authorization/预算/资源/源witness/fence检查。原Plan的APPLIED check只是出处，**不能授权当前执行**。

## 5. 收敛状态机与并发

候选合法但旧work未收敛时创建job：`job_id=derive_id('tg-converge', mission_id, decision_id)`，candidate_hash和impact_hash来自纯preview。job/targets/event/followup在同一原Store事务；一decision同candidate返回原job，异candidate冲突。

| 原状态 | 合法新状态 | 必需条件 |
|---|---|---|
| FENCED | WAITING | 已按原取消/核对入口提交稳定命令，仍有在途事实 |
| FENCED | READY | 从真实完整runtime/operation读取证明已收敛 |
| WAITING | WAITING | 事实仍未决；有界更新，不调用Planner重想同决定 |
| WAITING | READY | 所有target核对完成且没有共享误取消 |
| READY | WAITING | 新观察表明仍有在途影响，不能硬Commit |
| READY | APPLIED | 同一候选经当前完整source检查后实际PlanCommit成功；同事务转换 |
| FENCED/WAITING/READY | ABANDONED | 当前获准操作者放弃候选；原工作已收敛或另有显式责任移交及安全恢复授权 |

APPLIED/ABANDONED终态不能回退。READY仍然是fence，不因TTL到期、队列空或lease过期自动解除。

`advance_convergence`在事务外调用已有取消/核对，稳定子命令key由job_id+原Attempt/Operation+action种类决定。接收外部回执后短事务读当前job版本，核对identity再CAS。planner的旧request不因job状态更新或临时fence被自动改写；若真正Plan/要求/权限变化，按H1拒绝旧决定，先安全处理已有job，不能自动换base重编。

## 6. 通知投递流程与SQL

`append_followup`输入完整FollowupV1，系统生成message_id/hash/dedupekey；dedupekey取原event_id+kind+subject_key。原事件相同而payload不同是冲突。只允许三个kind。

`claim_followup`在短事务Q09选取并CAS；租约默认30秒，可由固定部署配置调整。过期仅使**通知**可再次投递，不判Worker已停。

`pump_taskgraph_followups`映射固定handler：REEVALUATE调用原eventloop的目标Mission重新检查入口；CONVERGE调用advance_convergence；REQUEST_COMPOSITION调用现有composition/rootReview producer。handler返回已经持久存在的command/intent/receipt引用；Store按对应读取器验证引用再Q10 ACK，把canonical引用写consumer_receipt_json。

若handler暂时失败，Q12前四次失败分别退避1/2/4/8秒，第5次BLOCKED；不删除消息，不无限调用模型。BLOCKED恢复必须真实source故障修复+明确操作者命令后Q13重置，不自动loop。每一consumer错误有具名code，raw exception/secret不写日志。

“消费者已经创建下游意图，但ACK前崩溃”的恢复：下一次同message重复调用，原稳定下游key返回同意图/receipt，再ACK。不能看异常就mint新的下游command。

## 7. Snapshot / diff / recovery具体结果

历史：`view_mode=HISTORICAL_STRUCTURE`，`planning_frontier=[]`,`execution_frontier=[]`，节点phase=HISTORY_ONLY、readiness=NOT_SELECTED、reason=HISTORICAL_VIEW_NON_EXECUTABLE。这不是声称历史任务当时不可执行，而是此API结果不提供执行许可。

当前：CURRENT，原TaskView/current witness/operation/预算用于readiness，全部数据带同一read_token。读取失败返回TaskGraphError，不把坏数据写成Task UNKNOWN。UI保留最后合法画面并标stale。

`diff`集合键按kind+identity比较完整对象hash，输出added/removed/changed和from/to hashes；不将运行状态变化混入结构diff。不读取最新Task JSON来补历史。

replay只在新的离线目标库：从首个真实seed/captured记录解码所有对象，根据版本化reducer重新建立pins/adoption/demands和既有图投影；若目标需parent rows，使用记录中冻结原对象和显式baseline清单恢复。无法重建外部Task/账本证据时生成纯图验证库，不伪造完整可运行SDK库；正式恢复必须使用项目已有完整备份恢复流程。图replay报告分`GRAPH_PROJECTION_VERIFIED`与`RUNTIME_RESUME_NOT_AUTHORIZED`，不能因图hash相同自动恢复执行。

## 8. 数字、时间、大小与默认值

旧SQLite时间REAL维持原Store秒单位；新增lease/源witness时间名带`_ms`的为UTC毫秒。`int(store.now*1000)`只在明确读clock边界执行，preview用冻结now_ms。ordinal/revision是非负整数，bool不接受；JSON公开整数上限2^53-1，超限明确错误，不截断。

NetworkDocument最大bytes绑定GraphStructureBudget和新增固定 `max_network_document_bytes=16*1024*1024`；preview source_reads固定12类，不可缺类或重复类；公共view最大nodes/edges受Schema和部署预算较小者限制。`CompleteRead`空集合允许，但需完整reader成功并带digest；NOT_APPLICABLE与UNAVAILABLE不能用同一个None表达。

任何新模块只导入本文授权的版本化codec/状态，不通过`getattr(obj,'state',True)`、`.get(...,{})`给关键来源造默认成功。


## 附录D：42组真实SDK验收

# 42组真实SDK验收

全部待执行；reference结果不填写到本表。

| ID | 场景 | 前置/执行 | 必须断言 |
|---|---|---|---|
| S01 | 历史结构准确读取 | 同一Mission r1固定binding1；r2修改binding且退役旧Method；分别读r1和r2；重启后再读 | r1返回原binding/draft/adoption/hash；不得读取latest；不同tenant拒绝 |
| S02 | Method与slot身份 | 注册两个同目标不同版本Method、含重复slot的坏候选；各自ground；坏候选preview | 参数/版本/hash准确；同输入ID稳定；重复slot拒绝，不能静默dedupe |
| S03 | 候选来源完整 | 合法REFINE的真实package及H1 grant；删除一个必需producer；两次preview再尝试Commit | 正常候选hash一致零写；缺来源SOURCE_UNAVAILABLE；不得默认形状通过 |
| S04 | AND-OR采用 | 根goal含两种替代方法，一种含两个required孩子；只采用一种；尝试同时采用两种 | 只采用的方法可运行；两种同goal采用拒绝；未选路线不阻塞合法根目标 |
| S05 | compound入口出口 | compound内两个独立primitive和组合Review；嵌套compound；生成projection、驱动实际调度 | 无父子死等；primitive可并行；compound/gate没有Attempt或模型费用 |
| S06 | 图损坏与环 | 缺端点、自环、ORDER+DATA形成环、refinement自祖先；preview/reader/物化入口分别执行 | 具名拒绝；无部分拓扑执行、不滤坏边；其余Mission可继续 |
| S07 | pending与检查完整 | 尚未展开compound但现存结构合法；另缺EvidenceSnapshot；运行完整validator | 前者显式pending且不可派Worker；后者NOT_CHECKED拒绝；不可共用skip |
| S08 | 边界与确定性 | 等于及超过实际GraphStructureBudget；打乱无关节点顺序；重复投影；测时间/SQL数；运行+1 | 边界成功/超限BOUND_REACHED；无截断；非语义顺序不改变结果 |
| D01 | ORDER与DATA分离 | A有两个输出；B只ORDER依赖A；C的一个端口DATA绑定A指定输出；运行真实resolver/工作区物化 | B不自动收到A文件；C只有声明输出；拒绝未授权路径和替换 |
| D02 | 合法空与未取得 | 一任务无输入要求；另一必需port未绑定；另源读取异常；调用实际resolver、admissions、派发 | 只有第一项得到明确frozen空manifest；其余等待/拒绝，不fallback空 |
| D03 | schema与选择 | 相同schema、显式声明、需要converter、两份同port不同revision；resolve并dispatch | 不推测兼容；converter未运行不得改schema标签；歧义拒绝不选最新 |
| D04 | ORDER真实结算 | ACCEPTED/FAILED/CANCELLED加实际IN_FLIGHT/UNKNOWN/已结算组合；分别检查accepted和settled_terminal边 | 失败不满足accepted；有未决真实调用时CANCELLED也不释放cleanup |
| D05 | 物化与资源身份 | 两个不同namespace同路径；同namespace大小写别名/path逃逸/symlink；实际TargetRules+CAS读取+workspace物化 | 隔离同路径可共存；真实冲突/越界拒绝；不因ORDER授覆盖权 |
| D06 | 每Attempt冻结输入 | 同Task两个合法Attempt使用同manifest；另v2变更；创建真实Attempt/intent/绑定，重启恢复旧Attempt | 两条attempt关联一份内容；恢复各自input_id/hash；不升级到latest |
| A01 | 真实caller/启用 | 两个tenant、legacy/新协议；已认证caller和未授权caller；enable_taskgraph_contract、读写接口 | 仅获准新协议写新表；旧默认无变化；payload伪造principal无效 |
| A02 | 冻结request | H1 package/visible refs/subject/request真实绑定；混入不可见ref、旧request及decode-only决定 | 拒绝原H1语义；不把方法修复跳过request；decode-only无图写入 |
| A03 | grant与Commit竞态 | 合法grant生成preview；Commit前revoke/expire；调用最终原Commit入口；重送已成功旧command | 未提交者拒绝；已提交历史receipt按当前读权可读，无新写 |
| A04 | Validity目的与来源 | PLAN witness用于START、其他consumer、过期epoch、新反证；准备manifest/dispatch/accept | 目的/consumer/support/epoch均检查；新反证使旧缓存重算；历史事实保留 |
| A05 | 能力和权限 | 实际PlanningWorld声明与当前Tool授权不同；缺targetRules；尝试规划和handoff | 声明不是调用权限；未部署或未经授权不能执行；无默认True |
| A06 | 预算守恒 | 父账户剩余不足、UNKNOWN hold、共享任务、换Method/Task；并发reserve与改图 | 不新增资金；尾部保护；UNKNOWN不释放；Obligation消耗与失败不归零 |
| R01 | 共享保留 | 两个adopted消费者依赖同只读producer；其中一个退休；preview effects然后Commit | 保留producer及另一个消费者；不撤其generation/预算；仅退出对应需求 |
| R02 | 局部影响 | A→DATA→B；Z只ORDER关联；输入/支持改变；运行impact和Validity重算 | B进入revalidate；不因ORDER复制失效；不完整覆盖保守处理有标记 |
| R03 | 非法修复不先取消 | 旧Method兄弟Attempt在跑，新候选有cycle；触发REPAIR/REPLACE | 先拒绝形状；原工作和派发权不被preview取消 |
| R04 | 收敛完整闭环 | 合法替换，旧工作在途；fence持久写入；强退后advance，原runtime收敛，最终Commit | 同一decision无需第二LLM；READY仍fenced；Commit代次只增一次 |
| R05 | 最后需求并发 | 最后consumer退休与另consumer新增同时提交；两个连接barrier控制读/写次序 | 至少一个stale/冲突或按当前完整集合重评；不误取消仍需的工作 |
| R06 | 退役操作仍UNKNOWN | 旧方法留下Operation link/action UNKNOWN，另链读取失败；尝试新图和恢复mapper | 仍扫描所有历史关联；读失败!=空；换方法不创建新现实动作 |
| R07 | 晚到结果 | A换输入且generation增加；B不变但global plan变；接收A/B原结果和Verifier回执 | A只归档费用不批准新输入；B原合同仍可合法接受 |
| R08 | 延期/撤权/取消 | convergence WAITING时grant变化或用户取消；重复唤醒/abandon请求 | 不回活旧权限；无证不释放fence/hold；收敛责任可查且有界 |
| C01 | 一写事务与幂等 | 合法H1决定生成candidate，准备原Commit；各写点异常，随后同command重送和异内容重送 | 计划/记录/预算/事件/回执全有或全无；同key复用，异内容冲突 |
| C02 | 并发计划环 | 同base两提案A→B与B→A；独立SQLite连接提交 | H1第二个stale，无自动rebase；新base验证环拒绝；无半图 |
| C03 | 进程强退四点 | 真实测试DB，原Store/Commit/dispatch；before Commit、after Commit before receipt、after reserve before intent、after executor receipt四点os._exit | 未提交rollback；已提交找原receipt/AgentTurn；不重复reserve/operation |
| C04 | 离线图重建 | 两revision真实记录后删除新离线目标投影；rebuild_projection并篡改一个源hash重试 | 相同结构hash；坏源拒绝；不读当前状态猜历史；零Provider/tool |
| C05 | 通知至少一次投递 | source event+followup原子保存，消费者已完成持久意图但没ACK；强退恢复重新投递 | 相同下游command复用一次意图；ACK后无重复工作 |
| C06 | 迁移兼容 | 真实0.12.x及当前H1H-ADM测试库copy，旧迁移checksum；用原runner新建和升级；故意FK/alias错误 | 原历史bytes/checksum不变；新约束有效；错误不部分迁移 |
| C07 | SQL跨Mission负控 | 两Mission实际图/Method/Attempt/manifest/event；尝试交叉插pin、action check、source event、jobtarget | DB触发器/外键或API拒绝；源JSON全hash另测 |
| C08 | Attempt原子绑定 | 实际创建Attempt/reserve/input/intent中途失败；两次相同manifest；真实transaction和重启 | 无孤立可派发intent；每Attempt源关联唯一；缺关联不可用latest修复 |
| V01 | 组合验收不可省 | 两child各自ACCEPTED但接口不兼容，另全兼容版本；真实独立Verifier+root resolution | 前者根拒绝、后者根通过；不是all child statuses shortcut |
| V02 | 不改图决定 | 有UNKNOWN或budget耗尽但合法状态回复；WAIT/NO_CHANGE/DECLARE_BLOCKED逐项执行 | 不调用preview/operations mapper；不写新PlanRevision；WAIT不丢唤醒 |
| V03 | 完整方法修复 | 合法Method A失败、共享C有效、改B并新增D；真实H1 Planner/worker/Verifier/Commit整链 | 保留C；按DATA给D/Join正确输入；旧A回执不能批准B；根closeout正确 |
| V04 | Host一致视图 | 当前/历史revision、未知错误消息、权限切换；TaskGraphReadApi和实际Host组件打开/重连 | 同readtoken；历史frontiers为空；错误保留stale画面；UI不直接改状态 |
| V05 | Shadow隔离与回归 | 同seed/fixed model输出，在NanoJev有无shadow下运行；比较正式plan、input、authorization路径；跑legacygolden | shadow不绕producer或替代HTN；旧Prompt/package/事件/默认行为不变 |
| V06 | 真实模型与规模 | H1后同候选/获准GPT-5.6配置，四代表场景各3trial；冻结manifest执行，记录所有失败/成本；运行bound及+1 | 安全不变量全成立；质量结果原样报告；未跑不写PASS；性能不凭感觉 |


## 附录E：完整新增协议Schema

这些Schema不新增模型可执行Decision，且必须与原Python边界codec对同一正负样本保持一致。源材料中的数据类型已由原codec验证，本Schema中的canonical_json字符串还须经原对象decoder与hash核对。


### followup-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:followup:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "mission_id",
    "source_event_id",
    "kind",
    "subject_key",
    "source_revision",
    "cause_ref"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "mission_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "source_event_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "kind": {
      "enum": [
        "REEVALUATE",
        "CONVERGE",
        "REQUEST_COMPOSITION"
      ]
    },
    "subject_key": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "source_revision": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "cause_ref": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "kind",
        "id",
        "revision",
        "content_hash"
      ],
      "properties": {
        "kind": {
          "type": "string",
          "minLength": 1,
          "maxLength": 512
        },
        "id": {
          "type": "string",
          "minLength": 1,
          "maxLength": 512
        },
        "revision": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "content_hash": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$"
        }
      }
    }
  }
}

```

### network-document-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:network-document:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "mission_id",
    "revision",
    "codec_manifest_hash",
    "requirements_ref",
    "root_occurrence_ids",
    "adopted_instance_ids",
    "required_obligation_ids",
    "objects"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "mission_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "revision": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "codec_manifest_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "requirements_ref": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "kind",
        "id",
        "revision",
        "content_hash"
      ],
      "properties": {
        "kind": {
          "type": "string",
          "minLength": 1,
          "maxLength": 512
        },
        "id": {
          "type": "string",
          "minLength": 1,
          "maxLength": 512
        },
        "revision": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "content_hash": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$"
        }
      }
    },
    "root_occurrence_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 256
    },
    "adopted_instance_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 4096
    },
    "required_obligation_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 4096
    },
    "objects": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "kind",
          "identity",
          "sha256",
          "canonical_json"
        ],
        "properties": {
          "kind": {
            "enum": [
              "occurrence",
              "task_semantics",
              "method_instance",
              "order_constraint",
              "data_requirement",
              "typed_edge",
              "obligation_coverage"
            ]
          },
          "identity": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "sha256": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          },
          "canonical_json": {
            "type": "string",
            "minLength": 2,
            "maxLength": 8388608
          }
        }
      },
      "maxItems": 16384
    }
  }
}

```

### preview-binding-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:preview-binding:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "decision_id",
    "request_id",
    "mission_id",
    "base_revision",
    "decision_hash",
    "network_hash",
    "candidate_hash",
    "delta_hash",
    "read_set_hash",
    "validator_id",
    "validator_version",
    "source_reads",
    "pending_compound_ids",
    "required_convergence_ids"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "decision_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "request_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "mission_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "base_revision": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "decision_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "network_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "candidate_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "delta_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "read_set_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "validator_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "validator_version": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "source_reads": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "channel",
          "identity",
          "digest",
          "coverage"
        ],
        "properties": {
          "channel": {
            "enum": [
              "request",
              "authorization",
              "network",
              "methods",
              "evidence",
              "capabilities",
              "operations",
              "running_work",
              "acceptances",
              "demand",
              "budget",
              "inputs"
            ]
          },
          "identity": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "digest": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          },
          "coverage": {
            "const": "COMPLETE"
          }
        }
      },
      "maxItems": 64,
      "minItems": 12,
      "uniqueItems": true,
      "allOf": [
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "request"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "authorization"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "network"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "methods"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "evidence"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "capabilities"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "operations"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "running_work"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "acceptances"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "demand"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "budget"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "channel"
            ],
            "properties": {
              "channel": {
                "const": "inputs"
              }
            }
          },
          "minContains": 1,
          "maxContains": 1
        }
      ]
    },
    "pending_compound_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 4096
    },
    "required_convergence_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 4096
    }
  }
}

```

### taskgraph-error-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:error:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "origin",
    "stage",
    "code",
    "detail",
    "retry_kind",
    "source_identity"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "origin": {
      "enum": [
        "MODEL",
        "SYSTEM",
        "RUNTIME"
      ]
    },
    "stage": {
      "enum": [
        "SOURCE",
        "PREVIEW",
        "ADMISSION",
        "CONVERGENCE",
        "COMMIT",
        "DISPATCH",
        "REPLAY",
        "READ"
      ]
    },
    "code": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "detail": {
      "type": "string",
      "maxLength": 2000
    },
    "retry_kind": {
      "enum": [
        "NONE",
        "REQUERY",
        "RECONCILE",
        "NEW_PLANNER_REQUEST",
        "OPERATOR_REPAIR"
      ]
    },
    "source_identity": {
      "anyOf": [
        {
          "type": "string",
          "minLength": 1,
          "maxLength": 512
        },
        {
          "type": "null"
        }
      ]
    }
  }
}

```

### taskgraph-view-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:view:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "mission_id",
    "read_token",
    "nodes",
    "edges",
    "planning_frontier",
    "execution_frontier",
    "root_resolution_refs",
    "next_cursor",
    "complete",
    "view_mode"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "mission_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "read_token": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "plan_revision",
        "through_seq",
        "validity_epochs",
        "snapshot_hash",
        "manifest_hash"
      ],
      "properties": {
        "plan_revision": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "through_seq": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "validity_epochs": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "scope_id",
              "epoch"
            ],
            "properties": {
              "scope_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512
              },
              "epoch": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9007199254740991
              }
            }
          },
          "maxItems": 256
        },
        "snapshot_hash": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$"
        },
        "manifest_hash": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$"
        }
      }
    },
    "nodes": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "occurrence_id",
          "task_id",
          "obligation_id",
          "form",
          "contract_revision",
          "dispatch_generation",
          "phase",
          "readiness",
          "reason_codes"
        ],
        "properties": {
          "occurrence_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "task_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "obligation_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "form": {
            "enum": [
              "compound",
              "primitive"
            ]
          },
          "contract_revision": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "dispatch_generation": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "phase": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "readiness": {
            "enum": [
              "NOT_SELECTED",
              "NEEDS_REFINEMENT",
              "WAITING_ORDER",
              "WAITING_DATA",
              "WAITING_EVIDENCE",
              "WAITING_APPROVAL",
              "STALE_BINDING",
              "READY_CANDIDATE",
              "WAITING_OPERATION_UNKNOWN",
              "OBSERVER_UNAVAILABLE",
              "GRAPH_INTEGRITY",
              "VALIDITY_RECHECK_PENDING"
            ]
          },
          "reason_codes": {
            "type": "array",
            "items": {
              "type": "string",
              "minLength": 1,
              "maxLength": 512
            },
            "maxItems": 32
          }
        }
      },
      "maxItems": 4096
    },
    "edges": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "kind",
          "source",
          "target",
          "identity"
        ],
        "properties": {
          "kind": {
            "enum": [
              "refinement",
              "order",
              "data"
            ]
          },
          "source": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "target": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "identity": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          }
        }
      },
      "maxItems": 8192
    },
    "planning_frontier": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 4096
    },
    "execution_frontier": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 4096
    },
    "root_resolution_refs": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "kind",
          "id",
          "revision",
          "content_hash"
        ],
        "properties": {
          "kind": {
            "const": "resolution"
          },
          "id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "revision": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "content_hash": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          }
        }
      },
      "maxItems": 256
    },
    "next_cursor": {
      "type": "null"
    },
    "complete": {
      "const": true
    },
    "view_mode": {
      "enum": [
        "CURRENT",
        "HISTORICAL_STRUCTURE"
      ]
    }
  }
}

```


### taskgraph-convergence-view-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:convergence-view:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "mission_id",
    "through_seq",
    "jobs",
    "complete"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "mission_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "through_seq": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "jobs": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "job_id",
          "decision_id",
          "source_revision",
          "candidate_hash",
          "impact_hash",
          "state",
          "row_version",
          "targets",
          "diagnostic_refs"
        ],
        "properties": {
          "job_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "decision_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "source_revision": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "candidate_hash": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          },
          "impact_hash": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          },
          "state": {
            "enum": [
              "FENCED",
              "WAITING",
              "READY",
              "APPLIED",
              "ABANDONED"
            ]
          },
          "row_version": {
            "type": "integer",
            "minimum": 1
          },
          "targets": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": [
                "occurrence_id",
                "task_id",
                "expected_generation",
                "target_kind"
              ],
              "properties": {
                "occurrence_id": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 512
                },
                "task_id": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 512
                },
                "expected_generation": {
                  "type": "integer",
                  "minimum": 0,
                  "maximum": 9007199254740991
                },
                "target_kind": {
                  "enum": [
                    "RETIRING",
                    "INPUT_REPLACED"
                  ]
                }
              }
            },
            "maxItems": 4096
          },
          "diagnostic_refs": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": [
                "kind",
                "id",
                "revision",
                "content_hash"
              ],
              "properties": {
                "kind": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 512
                },
                "id": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 512
                },
                "revision": {
                  "type": "integer",
                  "minimum": 0,
                  "maximum": 9007199254740991
                },
                "content_hash": {
                  "type": "string",
                  "pattern": "^[a-f0-9]{64}$"
                }
              }
            },
            "maxItems": 64
          }
        }
      },
      "maxItems": 4096
    },
    "complete": {
      "const": true
    }
  }
}

```

### taskgraph-diff-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:diff:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "mission_id",
    "from_revision",
    "to_revision",
    "from_manifest_hash",
    "to_manifest_hash",
    "changes",
    "complete"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "mission_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "from_revision": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "to_revision": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "from_manifest_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "to_manifest_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "changes": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "kind",
          "identity",
          "before_hash",
          "after_hash"
        ],
        "properties": {
          "kind": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "identity": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "before_hash": {
            "anyOf": [
              {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$"
              },
              {
                "type": "null"
              }
            ]
          },
          "after_hash": {
            "anyOf": [
              {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$"
              },
              {
                "type": "null"
              }
            ]
          }
        }
      },
      "maxItems": 16384
    },
    "complete": {
      "const": true
    }
  }
}

```

### taskgraph-explanation-v1.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:simpleharness:taskgraph:explanation:v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "mission_id",
    "occurrence_id",
    "read_token",
    "readiness",
    "reason_codes",
    "source_refs",
    "details"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "mission_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "occurrence_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 512
    },
    "read_token": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "plan_revision",
        "through_seq",
        "validity_epochs",
        "snapshot_hash",
        "manifest_hash"
      ],
      "properties": {
        "plan_revision": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "through_seq": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "validity_epochs": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "scope_id",
              "epoch"
            ],
            "properties": {
              "scope_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512
              },
              "epoch": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9007199254740991
              }
            }
          },
          "maxItems": 256
        },
        "snapshot_hash": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$"
        },
        "manifest_hash": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$"
        }
      }
    },
    "readiness": {
      "enum": [
        "NOT_SELECTED",
        "NEEDS_REFINEMENT",
        "WAITING_ORDER",
        "WAITING_DATA",
        "WAITING_EVIDENCE",
        "WAITING_APPROVAL",
        "STALE_BINDING",
        "READY_CANDIDATE",
        "WAITING_OPERATION_UNKNOWN",
        "OBSERVER_UNAVAILABLE",
        "GRAPH_INTEGRITY",
        "VALIDITY_RECHECK_PENDING"
      ]
    },
    "reason_codes": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 512
      },
      "maxItems": 32
    },
    "source_refs": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "kind",
          "id",
          "revision",
          "content_hash"
        ],
        "properties": {
          "kind": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512
          },
          "revision": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "content_hash": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$"
          }
        }
      },
      "maxItems": 64
    },
    "details": {
      "type": "array",
      "items": {
        "type": "string",
        "maxLength": 2000
      },
      "maxItems": 32
    }
  }
}

```

## 附录F：可复核来源与实施范围

[D1] 原TaskGraph TG-DESIGN-1.0；[D2] H1H-ADM-1.0；[D3] HTN-LLM-NATIVE-2.0。正文沿用其职责和状态，本文明确标出的历史pin、每Attempt关联、收敛与通知存储为新增实施裁定，不宣称原计划已有。

源码[S01–S16]固定于 `5ac3f05890a4c5160e2f103a01f863753ea5d498`；增补[S15] `planning/htn/world.py`、[S16] HierarchicalDispatch 装配属性核对。精确URL、原文hash和被读范围保存在sources.json。未读取用户本地未提交实现。

外部实施依据：SQLite isolation/foreign keys 用于同库事务与约束；Python graphlib 用于固定图交叉验证；Hypothesis stateful 用于序列不变量测试。它们不证明整个SimpleHarness已正确。

- https://www.sqlite.org/isolation.html
- https://www.sqlite.org/foreignkeys.html
- https://docs.python.org/3/library/graphlib.html
- https://hypothesis.readthedocs.io/en/latest/stateful.html

交付所有PASS只限定VALIDATION.md中明确运行的本包检查；真实SDK/Host/模型状态见sdk-test-cases.json，均初始为PENDING。
