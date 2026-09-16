# SimpleHarness TaskGraph 专项设计与实现约定

**性质：完整目标的专项细化，不替换原总计划、不新增一套编排框架。**

- 日期：2026-09-16（Asia/Singapore）。
- 需求基线：《Agent 编排层完整设计方案》§6/7/8/15/17/20/25，以及 `simpleharness-complete-target-and-gap-plan-2026-09-16.zh-CN.md`（FULL-TARGET-1.0）的 §4、§6–9、§18.5。
- 源码基线：`DennyWanye/simple-harness-sdk@61a85eb7e8c003fa894497090f5de893419ebbd1`；本轮重新读取 main，未发生变化。
- 证据范围：定向源码阅读与一手技术资料；未运行仓库、真实模型、故障注入或 UI。文中的新增类型、接口、表与测试均为待实现设计。
- 本文沿用最新日期版本的 `Task.form=compound/primitive`、`Obligation`、`MethodContract`、`MethodInstance`、`PlanRevision`、`GoalResolution`。不重新创建与 Task 并列的独立 Goal 服务，不改回此前另一份文档的工作包编号。

## 1. 结论与系统边界

**TaskGraph 应实现为持久化、类型化、版本化的 TaskNetwork；用于实际调度的 Task DAG 是它的一个编译投影。**

一份关系数据库保存任务合同、方法实例、采用关系、执行关系和证据引用；一个逻辑 Commit Service 修改正式状态；Planner/Manager/Verifier 只提交提案或审阅。不同图是不同语义的查询，不是多个可以相互覆盖的数据库。

保留现有 BaseAgent、Attempt、AgentTurn、模型/工具账本、预算准入、隔离工作区和验证能力。HTN 负责决定和解释工作结构，TaskGraph 负责表达与约束结构，Scheduler 执行当前已获准的原子工作。Workflow 仍在工具层；用户长期记忆继续排除。

本文补充的具体裁决包括：ORDER 的释放条件、DATA 的绑定时机、复合节点边界编译、同名产物的命名空间、语义 read-set 的集合读取、前提检查阶段、复合 Task 的状态驱动、以及图损坏时拒绝执行。这些不是假装原文已经逐项规定，而是落实完整目标所需的新工程约定。

## 2. 已读取源码：保留什么，修改什么

| 当前代码 | 本轮直接观察 | 新模式的处理 |
|---|---|---|
| `graph/task_graph.py::TaskNode` | 单层 Task 节点主要有 goal、dependencies、criteria、policy、tools、budget、outputs；没有 Method/form/输入端口 | 旧 wire 不变；新模式要求完整 `TaskSemanticBinding` |
| `graph/dependency_checker.py::check_dependencies` | Kahn 排序；拒绝缺失、自依赖、重复和环；相同提案稳定排序 | 保留机械检查；只对正确的执行投影调用，不对所有类型关系混合排序 |
| `graph/changes.py::validate_change` | 8 类操作；只允许 BLOCKED 原位改依赖，执行中需替代；系统 synthesis 依赖不可改 | 保留 legacy；新语义提案经过 Method/输入/当前性检查后转换为受控变更 |
| `commit_service.py::_commit_graph_change` | 一个事务写 Task、预算、变更、事件和回执；已有按 touched/referenced task IDs 的无冲突 rebase | 不重做 OCC；增加事实、输入、方法、验收与集合范围的语义检查 |
| `commit_service.py` 初始/动态建 Task | 初始 parent 为空；替代任务可将旧 Task 放入 parent 字段 | 不能把旧 parent 直接解释为 HTN 细化。替代关系与方法子位置必须分开 |
| `scheduling/allocator.py::frontier` | READY、未暂停、所有 dependency Task 为 COMPLETED | 接收新模式的 `EligiblePrimitiveTask`；READY 本身不是执行许可 |
| `artifacts/versioning.py` | 默认传递所有依赖祖先的已接受文件；依赖链允许后者覆盖前者同路径产物 | 新模式只按明确 DATA/输入 manifest 物化；ORDER 不授予数据和覆盖权限 |
| `planning/manager.py::terminal_task` | synthesis 优先，否则最后一个非冲突拓扑叶子 | 旧逻辑保留；新模式使用根 Obligation/GoalResolution 与明确交付对象 |
| `graph/deduplicator.py` | goal/dependencies/criteria 相同会被判重复；相似只警告，不自动合并 | occurrence、scope、输入、准则、时效和操作意图都要纳入判断 |
| `artifacts/versioning.py::topological` | 遇到未排序节点时把剩余节点按 ordinal 附加到结果 | 新执行路径必须返回 GraphIntegrityError；诊断图可显示损坏，但不可继续交接 |

最后一项是静态可见风险：上游已有正常环检查，不能据此宣称已复现生产环或数据破坏。需要损坏快照/旁路输入的负控验证。当前拓扑实现已经按真实依赖而非单纯 ordinal 排序，不能把此前修复忽略掉。

现有 Mission 有最终评审，并非“最后一个叶子一完成就无条件成功”；这里要改的是隐式的最终目标/产物选择语义，以及不能表达方法替代的限制。

## 3. TaskNetwork 的实体和身份

### 3.1 不合并的身份

| 身份 | 定义 |
|---|---|
| MissionId | 一份有限用户目标、要求、权限与预算 |
| ObligationId | 稳定的履约责任；细化/换名/换方法不能重置它的累计约束 |
| TaskId + contract_revision | 一项工作及其具体合同；form 与 kind 是两个轴 |
| MethodRef | 不可变的方法定义及版本/hash |
| MethodInstanceId | 某次目标、参数、证据和方法的具体绑定 |
| OccurrenceId | 一个方法中的任务出现位置；重复调用同类动作仍有不同身份 |
| PlanRevision | 当前采用的结构、方法选择和绑定的版本，不是每次心跳计数 |
| AttemptId / AgentTurnId | 内容尝试及具体 Agent 输入处理 |
| OperationId | 真实外部操作的业务意图身份；不由新的 Attempt 自动重新生成 |
| Acceptance/GoalResolutionId | 对指定合同、输入、产物和检查的不可变接受回执 |

`MethodInstance.goal_id` 在此解释为目标用途的 TaskRef/ObligationRef，而不是允许再建立一个无约束的 GoalNode 权威。

### 3.2 Task 数据组成

沿总计划 §18.5，将已有 Task 与 `TaskSemanticBindingV1` 在同一 orchestrator 库组合读取。

```text
TaskView = 现有 Task 的工作事实 + 版本化语义绑定

语义绑定至少有：
  task_id, obligation_id, contract_revision, contract_hash
  form: compound | primitive
  goal_signature, typed_parameters, requirement_refs
  input_ports, output_ports, operator_ref（仅primitive）
  semantic_scope, capability_requirements
  precondition_refs, applicability_check_policy
  method/occurrence bindings
  input_binding_revision, dispatch_generation
```

`kind=work/conflict/synthesis/...` 继续表示用途，不拿它表示 compound/primitive。模型只提供局部 key、业务目标与参数；Mission 身份、账户、实际 scope 和 execution generation 由系统绑定。

旧 Task JSON、哈希和请求字节不重写。新 Mission 的每个 Task 必须有对应语义记录，缺失是完整性错误，不能回退成 legacy Worker。

### 3.3 contract、状态、计划是不同版本

- contract_revision：目标、参数、接口、成功条件或语义输入约定改变。
- execution state version：Attempt/结果/等待的状态变化。
- plan_revision：采用的 Method、节点与边等结构改变。
- binding revision / dispatch generation：影响这项工作能否继续提交或交接的局部代次。
- event cursor：客户端观察进度。

无关分支更新 plan_revision，不自动使其他合法输入或冻结请求过期。新增计划需保留未变的局部 binding/generation；不能把所有仍在运行的工作要求成“必须出生在最新图版本”。

## 4. 关系模型：不要给一条 dependencies 边塞五种意思

所有公开图示采用“前置/生产者 → 后继/消费者”。旧代码的 `key -> predecessors[]` 是算法适配表示，必须用明确转换函数，避免箭头方向混淆。

### 4.1 细化/满足：Task → MethodInstance → ChildOccurrence

复合 Task 通过 MethodInstance 细化。一种方法中的所有必要位置是 AND；同一目标的多个可接受方法是 OR。使用独立的 Method 节点或关系表表达超边，不使用一个无语义的 `children[]`。

每个 MethodInstance 记录 required children、composition criteria、相关参数和前提见证。可选子工作必须来自明确政策，Planner 不能把用户必需要求改成 optional。

方法可以递归引用定义；某一次有限实例展开使用不同 occurrence，带燃料/进展检查。递归定义不是同一执行实例的循环等待。

### 4.2 ORDER：只表示释放先后条件

```text
OrderConstraint(before_occurrence, after_occurrence, release_condition)
release_condition:
  accepted（默认）
  settled_terminal（只在明确清理/收敛合同中允许）
```

- `accepted`：指定前置出现位置产生了符合该顺序约定的接受事件。纯顺序见证是发生过的事实；若要求其内容到现在仍然有效，必须另列 DATA/SUPPORT 条件，不能隐含在 ORDER 中。
- `settled_terminal`：等待前置停止并完成必要核对；FAILED/CANCELLED 不再被当作内容成功。
- ORDER 不自动授予读取前置全部文件的权限，也不授予覆盖同名文件的许可。
- 纯 ORDER 不能自动传播内容失效；如它实际依赖内容或当前事实，应另建 DATA/SUPPORT。
- 前置被取消，不会默认释放 accepted 条件；需计划显式改动。

更细的 start-to-start/流式触发不是本次原子完成依赖的缺省含义；未定义的触发类型必须拒绝，不能猜。

### 4.3 DATA：明确的产物端口和版本

```python
# 拟议合同形状；不是可直接导入当前仓库的 API。
@dataclass(frozen=True, slots=True)
class DataRequirement:
    producer_occurrence: OccurrenceId
    output_port: str
    consumer_occurrence: OccurrenceId
    input_port: str
    schema_ref: SchemaRef
    assurance_policy_ref: PolicyRef
    freshness_policy_ref: PolicyRef

@dataclass(frozen=True, slots=True)
class BoundInput:
    requirement_id: DataBindingId
    producer_result_id: ResultId
    acceptance_id: AcceptanceId
    artifact_id: ArtifactId
    content_hash: str
    schema_ref: SchemaRef
    source_revision: str
```

规划时产物可能尚未生成，所以 DataRequirement 是约定；派发前解析为 BoundInput，产生不可变 InputManifest。单值 input port 只能有一个有效 binding；集合端口必须定义类型和顺序，不能“谁最后写谁赢”。

Schema 兼容默认使用完全匹配或注册的显式转换/兼容声明，不声称系统能自动证明任意 JSON Schema 的包含关系。运行时还要验证实际值。

源版本策略分开：
- PINNED：任务明确研究历史版本；出现新版不自动否定旧结论。
- FOLLOW_AUTHORIZED_REVISION：用户要求随当前版本更新，变化触发相关重新验收。
- 撤权、删除与用途限制仍需当前检查，不能以 PINNED 为理由绕过。

可选推测执行必须明确标记 provisional，仅在获准范围内运行；不能对外宣称已经满足必需 DATA，也不能执行依赖未证前提的不可逆动作。

### 4.4 SUPPORT / ASSUMPTION

关联观察、知识、要求、方法适用性及验收；它不等于执行次序。沿已有 source_dependencies 扩展，不再复制另一套事实库。

一个结论可以有多个足够的支持集合；一个集合内部 AND，多个集合之间 OR。失效一项证据后需要重求值，不把全部历史后继无差别取消。禁止无外部锚的循环自证。

### 4.5 监督、资金与替代关系

- supervision：协调责任和 scope；不是自动读取别人 Context 的权限。
- funding：唯一归属的预算授权树；不是多父执行 DAG。
- supersedes：旧工作与后继的历史关系；不是 HTN 父子。

旧 `parent_task_ids` 可能包含替代来源，因此 legacy 解析不能默认将它们全部转换为 refinement。

## 5. 哪张图必须无环？

| 视图 | 约束 |
|---|---|
| 方法定义调用图 | 可以递归；检验有界细化、重复状态与搜索停止 |
| 当前已实例化细化关系 | 不允许同一 occurrence 成为自己的祖先；复用是显式引用，不删 occurrence |
| 当前采用方法的执行完成依赖投影 | 必须 DAG；包含具体 DATA/ORDER 约束和组合边界 |
| 证据支持图 | 按支持集合计算；无锚 SCC 不得靠循环自证晋级 |
| 历史 supersedes / event / 使用关系 | 保留因果与时间，不能混入执行拓扑 |
| 实时资源 wait-for 图 | 另做死锁检测或固定锁顺序；Task DAG 无环不保证资源无死锁 |

不能把上下级、执行、验收回传的所有箭头放进一个 `DiGraph` 后调用 acyclic；“孩子结果支持父”与“父派出孩子”本来就方向相反但不是执行等待环。

TaskGraph 的完整历史会不断增长；调度只看本 Mission/Cycle 当前采用结构。循环业务应实例化新的出现位置/周期，不把执行 DAG 本身画成闭环。

## 6. 复合边界编译：避免“父等子、子又等父”的伪循环

假设逻辑顺序为 `A → C(compound) → D`，C 采用方法 `B1/B2 → Join`。

正确编译：

```text
A.accepted → C.entry
                  ├── B1 →┐
                  └── B2 →┤
                          Join/Review → C.exit → D.entry
```

- C.entry/C.exit 是逻辑 gate，不是收费 Agent、不伪造工具调用。
- entry 条件满足后开放对应子工作。
- exit 在选定方法的必要孩子与组合义务通过、GoalResolution 提交后开放。
- 组合 Review 等孩子，而不是等 C 已完成；否则会循环。
- C 本身不进入普通 Worker 队列。规划 Agent 的方法生成是独立用途的 AgentTurn，由现有派发计量。
- 子任务之间只有明确 partial order 才添加边；拓扑线性列表用于确定性枚举，不把它回写成一条强制串行链。
- 方法中的 DATA 端口同样经入口/出口映射；可提前使用某个子结果必须由父接口明确允许，不用执行器自行推断。

形式导出必须保持所声明的部分序与 occurrence 映射。实际调度可以选择一种合法执行次序；这与声称“任意线性化后与原模型等价”不同。

## 7. primitive 与 compound 的状态驱动

保留原 Task/Attempt 定义；新模式明确选择不同的状态转移规则。

- primitive：由 Attempt 准入、执行、结果和验收驱动，复用既有合法转移。
- compound：由细化准入、方法采用、孩子推进、组合审阅和 GoalResolution 驱动。没有 Worker Attempt 也可以进行规划和接受。

建议沿用粗粒度 TaskStatus 作为展示，但增加类型化 phase：

```text
compound:
  READY / planning_ready
    → ACTIVE / refining 或 waiting_children
    → VERIFYING / composition_review
    → COMPLETED / resolution_committed

缺必需前提：BLOCKED / evidence_or_authority_wait
```

这些是新模式的明确转移函数 `next_compound_task`，不能为了走旧状态机创建一个假的 Worker Attempt；也不能先把复合 Task 写 COMPLETED 再让孩子执行。旧 `next_task` 继续解释 legacy/primitive。

TaskView 必须同时携带 form、phase、historical_status、current_resolution_validity。已有 COMPLETED 不回退；要求或支持失效时更新当前有效性，生成后继工作履行同一 Obligation。

所有新模式的 `list_tasks`、`_unblock`、并发统计、死锁诊断、快照和根评审消费者都要经过 TaskView/执行投影。只改 Scheduler 而忘记根完成检查，会留下复合节点永远阻塞或被误算运行的漏洞。

## 8. Ready 与执行许可：不再只有 all(dep.COMPLETED)

### 8.1 三层集合

1. PlanningFrontier：待细化、待查前提、待选方法/审阅等工作。
2. ExecutionFrontier：当前计划采用、语义依赖满足的 primitive 候选。
3. AdmittedDispatch：在当前事务/实际交接门中确认资源、预算、权限后可以执行。

`READY` 是加速查询的投影提示，不是长寿命 capability。

### 8.2 拟新增纯判断

```text
evaluate_readiness(TaskView, ActivePlan, Resolutions, Facts, InputBindings)
    → NOT_SELECTED
      NEEDS_REFINEMENT
      WAITING_ORDER
      WAITING_DATA
      WAITING_EVIDENCE
      WAITING_APPROVAL
      STALE_BINDING
      READY_CANDIDATE
```

输入缺失、观察服务不可用、外部操作 UNKNOWN 是不同原因，不统一转换为“任务失败”。

### 8.3 真实 dispatch

生成 `EligiblePrimitiveTask` 时绑定 task contract、局部 generation、input manifest、method/requirement/acceptance/fact 版本。随后通过现有 Commit/预算与执行入口，再检查当前采用关系、身份、额度和物理容量并创建 Attempt/intent。

Model/工具 handoff 前仍须检查会变化的权限、前提和外部对象版本。read-set 不能冻结整个现实世界；对可变外部对象优先用 ETag/条件写或明确核对协议。没有必要控制能力时拒绝依赖该保证的动作。

## 9. 前提在什么时候必须成立？

必须给前提一个检查阶段，避免“方法改变了世界，导致自己永远无法通过验收”。

```text
SELECT/START：选用/进入该步骤时成立，例如当时库存充足。
MAINTAIN：执行范围内必须维持，例如一直禁止向外泄露数据。
ACCEPT：接受结果时仍需成立，例如交付版本是当前获准版本。
```

方法先前成功消费一项资源后，START 前提不再为真，不自动使已完成结果失效。MAINTAIN 被破坏则需要中止/核对；ACCEPT 条件失败阻止当前 GoalResolution。每个前提的支持见证与检查点均保存。

四态 TRUE/FALSE/UNKNOWN/CONFLICT 与 CURRENT/STALE/REVOKED 沿总计划定义。未知不能凭没有记录变成假；Planner 写的 expected effects 只在预测模型里，不自动更新现实 Fact。

## 10. 产物组装必须同时修改

这是本轮发现最应优先拆开的代码耦合。

### 10.1 不再默认收集所有祖先文件

新模式不要使用 `collect_upstream_inputs()` 的“全部祖先”语义；新增：

```text
resolve_declared_inputs(task, data_requirements, accepted_results)
    → InputManifest
materialise_input_manifest(manifest, authorized_artifact_store)
```

ORDER-only 前置不进入 manifest。实际消费的内容必须由 DATA、Mission 明确材料或合法检索来源清单记录；动态发现更多材料通过受控读取并写入本次 read-set，而不是无记录地读取全部工作区。

### 10.2 同名文件与竞争候选

`attempt-A/report.md` 与 `attempt-B/report.md` 是两个隔离命名空间，不应只因相对路径相同就禁止独立探索。它们作为不同 Artifact 引用进入综合任务。

两个不同 hash 要映射到同一个最终 `report.md`，必须明确选择/转换/综合，得到新 Artifact 并重新验证。不能用“B 拓扑更靠后”挑一个，也不能为了获得覆盖许可临时加 ORDER 边。

资源身份至少是 `(namespace/workspace/object-id, normalized-path)`，对层级路径、大小写和平台规则做真实适配。访问权限和真实写入锁由工具层落实；图里的声明只是排程依据，不能替代沙箱。

### 10.3 旧逻辑不能突然放松

当前 exact-path 冲突保护对应旧的全祖先合并机制。在 input manifest、独立候选和新合并规则同时接通以前，不能先删除保护导致静默覆盖。两种模式按明确版本分流。

## 11. 动态修改：一次完整的语义 PlanPatch

### 11.1 提案与编译增量分开

Manager 可提出：细化某 Task、选择/退出某 Method、补前提、重新绑定某输入、替代任务、释放某个共享消费关系等。

系统编译出 `PlanDelta`，包含：新/保留/退出的 occurrence、方法采用关系、typed edges、实际输入变化、受影响 Resolution、共享 consumer、资源/预算与在途 generation 处理。

不能让 Agent 直接提交 SQL、随意填正式 ID、自己声明“这次不影响任何人”。也不能用原 8 个低层操作绕开新模式的 HTN 完整性。

### 11.2 完整 read-set

除单对象版本，还应包含所依赖集合的版本：

```text
requirements_revision
method contract/instance revisions
Task contract/input-binding revisions
Fact/Acceptance/GoalResolution validity versions
incoming edges of task X 的集合版本
某共享结果的有效consumer集合版本
某资源上“没有冲突写者”的读取范围版本
授权、部署能力和预算依据
```

“不存在某个对象/边/写者”也是读取事实。只记录已看到的对象，无法发现准备后新插入的冲突对象。

现有 touched-task rebase 保留在 legacy；新模式要在最新图上重验结构、端口、覆盖、资源和全部语义读集。两个 Manager 改不同 Task，也可能合起来形成环或争抢最后一份共享预算。

### 11.3 事务协议

```text
事务外：
  读取固定快照
  LLM/solver提出候选
  编译增量、影响分析、独立审阅
  保存不可变候选内容和审阅依据

短写事务：
  验证提交者当前身份
  查命令身份和payload hash；已完成同命令返回原回执
  校验所依据的活跃计划及语义read-set
  在当前结构上重新检查受影响网络/资源/预算
  创建PlanRevision，更新采用关系
  对影响工作提升局部generation/登记取消或核对
  写完整业务事件、同步投影、预算变更、outbox和CommitReceipt
  原子切换active plan revision

事务后：
  派发/取消/重验；通过既有跨库幂等回执继续
```

旧命令重放不得由于 Mission 后来已完成就重新应用；在当前主体有权读原回执的前提下返回原结果。相同 key 不同 payload 是冲突。

SQLite 继续使用当前受控事务接口，不在里面 `await` 模型或启动 solver。不能嵌套调用各自开启事务的公开 Commit 方法；应复用同一事务内的内部原语。

### 11.4 大型候选与局部复验

在内存快照上做有界、完整的结构验证；对超大候选使用 PREPARED manifest、基础版本/hash和变化集。激活时基础版本/语义输入/当前预算不匹配就回到准备阶段，不能盲切旧候选。

只有一个 active revision。未受影响任务保留同一局部 generation；Scheduler 同时读取该 revision 与绑定版本。不得发布一半新边、一半旧 Task。

### 11.5 旧在途工作

- 未派发旧工作：撤销其旧 generation 的派发资格。
- 已运行旧工作：请求停止或允许完成用于历史/片段，按策略区分；不直接批准新版本。
- 迟到结果：保存候选与真实费用；当前采用检查失败就不解锁新依赖。
- 外部操作结果未知：继续核对原 OperationId，并阻塞与它冲突的后继动作。
- 图回退不等于现实回滚；补偿是另一项被授权工作。

## 12. 共享子任务：复用结果，不抹去责任

共享签名至少包括 goal type、typed parameters、contract/requirements、input versions、scope、assurance、freshness、Operator 与副作用身份。词面或向量相似只产生候选合并建议。

保留每个 Method 的 occurrence。只有明确允许 reuse、且满足条件时，多个 occurrence 才引用同一个有效结果/同一次受控执行。不同用户意图的“发送报告”，即使参数相同也不能被自动合并；同一发送重试也不能因换 Attempt 再发一次。

资金由一个明确账户承担；其他分支只记录消费者或受控转账归因。退出分支只删自身采用关系；仍有消费者、根独立责任或未结算操作时，不取消共享工作、不退回未知费用。

HDDL 验证时需保留出现位置和分解见证。运行时把两次出现绑定到一份已有结果，是扩展语义；必须在导出模型中显式表达可复用结果/无副作用重用方法，不能把少做一个动作的轨迹直接当作原 HTN 计划。

## 13. GoalResolution 与根完成

primitive 的候选被验证并 Commit，产生对其合同/输入/产物的 Acceptance。compound 的全部必要子义务具备有效 Resolution 后，触发组合任务/独立评审。

```text
当前目标满足：
  当前需求允许该方法
  AND 选定方法的必要子义务均满足
  AND 所有端口与组合标准通过
  AND 必需证据与授权仍在允许的有效范围
  AND 独立Review适用于这些确切版本
  → Commit GoalResolution
```

有多个 OR 方法时，一份有效完成见证可满足目标；但必须处理其他仍可能产生真实副作用的尝试。内容目标满足与全部物理/账务收尾是两项事实。可以先报告结果形成，最终 Mission 交付不得掩盖 UNKNOWN 或失控动作。

不要求废弃路线所有 Task 完成，也不只看最后拓扑叶子。保留既有 Mission 最终评审能力，用明确根覆盖/输入集合替代隐式目标选择。

方法 A 的局部失败不等于根 Obligation 无解；搜索预算耗尽、没有找到方法、已证某有限模型不可解必须分开。

## 14. 算法与存储选择

### 14.1 不更换数据库来代替语义设计

默认继续 SQLite + 已有 Store + Artifact CAS。逻辑结构复杂不要求先引入图数据库。若未来性能证据要求其他存储，再实现相同持久端口；算法不能依赖某个 UI 图组件。

沿总计划表设计，具体细化为：

| 逻辑记录 | 用途 / 索引 |
|---|---|
| task_semantics / contract revisions | task+revision唯一，mode/form/scope/operator可查 |
| method_contracts / instances | method id+version不可变；Mission下instance唯一 |
| method_child_occurrences | instance+slot唯一；保留reuse binding |
| plan_revisions / plan_bindings | Mission active revision单一；PREPARED不可调度 |
| order_constraints | mission/revision/before与after双向索引 |
| data_requirements / bound_inputs | consumer port与producer索引，确切输出/acceptance/hash |
| input_manifests | 与Task/Attempt/request绑定，immutable |
| resolution / support / consumers | 有效性反向索引与消费者集合版本 |
| commands / events / outbox | 复用已有幂等回执和事件设施，不再开一套队列权威 |

一张物理 typed_edges 表可以实现多种关系，但内部必须有对应判别类型与严格payload codec，不能重新变成 `kind:str + config:Any`。预算与执行事实仍在原账本，typed_edges 不保存另一份可独立修改的余额。

### 14.2 拓扑与增量索引

采用明确的 nodes + adjacency + indegree，对每次待提交的执行投影做完整校验作为正确性基线。稳定 tie-break 用 immutable key/ordinal，但 key 不代表业务顺序。推荐 heap-based Kahn；总成本约 `O(E + V log V)`，不是当前使用 list.pop/sort/index 的实现具有该界。

环、缺失节点、重复绑定、孤立必需义务、输入端口、根覆盖、资源冲突分别报告。节点增长大时增加受测增量可达性/索引缓存；缓存以plan/version绑定，可重建，不持有权威状态。

Python `graphlib.TopologicalSorter` 适合固定快照的基准/测试：prepare后不能继续add，不能将同一实例作为会持续增长的TaskGraph；它会自动加入未显式定义的前置节点，故必须先做节点完整性检查。get_ready/done也不代表业务验收或持久执行。

### 14.3 损坏处理

结构校验失败返回 GraphIntegrityError 并停止受影响 scope 新派发。诊断器可展示剩余节点和具体环，但不能输出“凑齐的拓扑顺序”用于执行。恢复必须核对事件水位与当前投影hash，不能通过多跑Agent补出丢失历史。

## 15. 代码改造表（在已有结构上实现）

| 位置 | 改动 |
|---|---|
| `contracts/htn.py` [新增，总计划既定] | TaskSemanticBinding、Method/Occurrence、端口、typed关系、read-set |
| `graph/task_graph.py` | legacy解析不改；接入新版TaskView/Projection验证与显式mode |
| `graph/dependency_checker.py` | 对单一执行投影查环；提供完整性错误，扩容时改为迭代稳定算法 |
| `graph/deduplicator.py` | legacy保留；新模式occurrence/签名/reuse见证，不把文字相同当同意图 |
| `graph/projections.py` [新增] | planning、execution、support、UI视图；不是不同写库 |
| `graph/readiness.py` [新增] | pure semantic readiness，区分compound/primitive和各阻塞原因 |
| `planning/htn/compiler.py` [既定新增] | 复合entry/exit、AND–OR、部分序、数据与组合编译 |
| `planning/repair/impact.py` [既定新增] | 语义依赖闭包、支持集重求值、保留/退出集合 |
| `orchestrator/plan_commits.py` [既定新增] | 语义read-set/OCC、激活屏障、共享关系、generation与事件原子提交 |
| `orchestrator/resolution_commits.py` [既定新增] | current GoalResolution接受和根判据 |
| `orchestrator/commit_service.py` | 单一写入入口复用；新mode分派，禁低层旁路，内部事务原语不嵌套 |
| `orchestrator/event_handler.py` | 编排新模型提案/事实/结果/审阅；新模式list/ready/terminal改用投影 |
| `scheduling/allocator.py` | 使用EligiblePrimitiveTask；评分和物理容量继续复用，compound进入planning frontier |
| `artifacts/versioning.py` | 新模式manifest代替all-ancestors；环拒绝；ORDER不再决定覆盖 |
| `artifacts/input_bindings.py` [新增] | 解析DataRequirement，冻结manifest，端口/字节/源与权限核验 |
| `context/context_builder.py` | 获准方法/任务/输入/约束视图；不因顺序边引入无关全部历史 |
| `storage/schema.py` / `storage/htn_store.py` | 读取当前最高migration再追加；typed记录、双向索引、active pointer |
| `observability/business_replay.py` [总计划新增] | 该新语义事件从首次引入就可重放；完整历史旧缺项仍走基线 |
| `api/facade.py` / Host实际文件 | 版本化graph snapshot/cursor/why；只做意图与展示，Host路径需实际核查 |

不能只改graph目录：artifact input、unblock、result acceptance、root judge和cancel/recovery必须一起支持新语义。公共函数中的旧逻辑只在legacy mode使用；新模式缺binding时拒绝，不能偷偷降级。

## 16. 一次实际演示应是什么样子

假设用户要求“对指定语料实现混合检索，能回读原文且权限隔离；实现A不可用时允许B”。

```text
根Task G（compound，Obligation O）
  OR 方法M1：合同C + 实现A + 集成I1
  OR 方法M2：合同C + 实现B + 集成I2
```

1. C 为只读、可复用并被独立接受，两个方法保留各自 occurrence，共用C的Resolution。
2. A 在执行中提交“依赖扩展不可用”的受控工具证据，而不只是文字抱怨。
3. 证据更新M1的适用性。Manager提出替代；系统判定只修M1关联输入/在途工作，C和已经有效B成果保留。
4. M2需要补一个前置D。新D ID晚于I2也无妨，执行顺序根据typed edges，不根据创建顺序。
5. 若I2仍在执行，旧输入不能原位改：生成后继I2'，延续同一Obligation，旧结果记历史，当前generation失效。
6. D/B/C被明确绑定到I2'输入manifest。M1的ORDER祖先文件不自动混入。
7. I2'输出新产物，独立Verifier验证组合和根要求；仅相关有效Resolution支持根完成。
8. 重启后恢复同一计划、输入、dispatch身份；不重做C、不重复预算、不让UI根据图节点变绿推断完成。

这个场景必须在另一组参数与非预写的变更下成立，不能用if/else写死演示。

## 17. 施工与验收：不另改总计划编号

本文落在现有P1（语义合同）、P2（HTN执行）、P3（修复）、P4（搜索与调度），并接通P5/P6/P7/P9的受影响边界。60条总需求保持不变。

施工可按三个完整接线目标推进，不能将后两项标成“可选优化”：

1. **类型化网络到真实执行**：ORDER/DATA/复合节点/manifest/验收同时接通；含两种领域、部分序与复合边界。
2. **动态方法与共享成果修复**：参数变化/新前提/方法替代/read-set/在途generation/重复事件/崩溃一起测试。
3. **完整重建与产品解释**：plan/state/event对齐、局部图查询和why、根Resolution与真实Task/费用一致；原总计划外部评测继续。

测试规范在 `taskgraph-tests.json`。TG编号是专项用例编号，不是新需求，也不替换总计划T001–T090。每项映射现有R编号；尚未执行的runner与evidence保持null。

测试至少包含：固定纯图反例、Hypothesis状态机产生的动作序列、SQLite并发/故障切点、真实Agent端到端、HDDL独立验证。随机测试比较被测增量实现与独立全量重算，不把生产缓存结果直接当oracle。

## 18. 本次研究依据与可声称范围

- 原始设计明确要求动态DAG、共享前置、Proposal/Commit、版本、独立验收和恢复；没有具体定义ORDER释放阶段、端口/manifest、compound状态落库等细节。本文对这些提出显式细化，不冒称原文已经实现。
- 最新完整计划明确了Task.form、MethodInstance、TaskSemanticBinding、PlanRevision、Obligation与GoalResolution，本文件不另建目标架构。
- Python graphlib是固定图拓扑工具，不是持久Agent调度系统。
- PANDA/HDDL为已建模层次规划与分解见证提供依据；形式计划有效不等于任意开放任务在现实中成功。
- SQLite隔离文档支持短事务和单writer下的原子切换，不能推出跨库/外部工具的全局exactly-once。
- Hypothesis状态机测试用于生成动作序列与缩小反例，不保证软件绝无缺陷。

**交付结论：TaskGraph不是“换成一个更大的DAG库”，而是把方法结构、真实输入、当前有效性和执行资格连成一致的持久协议。只有图、输入、调度、验收、恢复一起改变，新HTN语义才真正生效。**

详细固定源码和官方资料链接见 `source-index.json`。本轮没有运行项目测试、付费Provider或修改远端仓库。
