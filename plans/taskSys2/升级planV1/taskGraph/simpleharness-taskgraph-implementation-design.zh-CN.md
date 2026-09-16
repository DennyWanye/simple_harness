# SimpleHarness TaskGraph：完整语义、动态修改与执行连接设计

版本：TG-DESIGN-1.0 · 2026-09-16 · Asia/Singapore  
固定源码：`DennyWanye/simple-harness-sdk@61a85eb7e8c003fa894497090f5de893419ebbd1`  
定位：对最新《完整目标架构与差距闭合计划》的 TaskGraph 专项细化，不替换其 60 项要求或 P1–P9 工作包。

**证据边界：**本报告阅读了原始方案、最新完整计划及固定提交中的关键代码；未修改仓库、未运行 SimpleHarness／真实模型／Host UI 测试。本文新类型、表、函数、操作词表和验收场景均为拟实施规格。资料包自检只检查文档和映射完整性，不证明生产行为。

## 0. 决策摘要

TaskGraph 不应继续被理解为“Task 字典 + dependency_ids + 拓扑排序”。它应当是 **Mission 范围内、版本化、类型化的任务网络 TaskNetwork**，保存工作责任、复合/原子任务、方法采用关系、数据绑定、执行约束与结果满足关系；Task Graph Manager 通过现有 CommitService 管理其正式修订。

**HTN 负责提出如何细化，TaskGraph 负责表达、验证、提交和解释当前采用的计划，Scheduler 只派发当前可执行的原子工作。**独立 Verifier 判断开放式成果是否满足要求；Commit 再检查该判断在当前版本与权限下能否应用。

保留 Python、BaseAgent、Attempt、执行账本、CAS 产物和现有预算/取消机制。不引入第二个可写任务权威，不为图遍历增加微服务，不因图结构复杂就先换图数据库，不接入当前排除的用户长期记忆。

本轮最关键的四项工程变化：

1. `ORDER` 和 `DATA` 分开；先后依赖不再自动复制全部祖先文件或授权覆盖文件。
2. Method/AND–OR 与执行 DAG 分开表达；复合任务不能因 READY 直接派给 Worker。
3. 不可变计划修订 + 精确语义读集 + 在途执行代次；不是全图版本一变就重做所有任务。
4. 历史完成与当前结果有效性分开；根目标由显式 Resolution 和组合审阅接受，不靠最后一个叶子推断。

## 1. 依据及术语冻结

### 1.1 原文已经要求什么

原始《Agent 编排层完整设计方案》§6 要求动态 DAG、共享前置与 Task Contract；§15 要求 Proposal/Commit；§17 要求版本、幂等和 Single Writer；§20 要求隔离产物及组合后再次验收；§25 将 Task 的 COMPLETED/FAILED/CANCELLED 作为终态。以上都保留。[D1]

最新完整计划进一步规定 `Task.form=compound|primitive`、稳定 Obligation、MethodInstance、ORDER/DATA、条件理由、GoalResolution、计划读集与旧执行 generation。这些是本专项的主术语，不重新采用早一版中另一套独立 GoalNode 生命周期。[D2 §§6–9,18.5]

### 1.2 消除前后两版术语歧义

- **Task**：正式任务出现位置（task occurrence）。有自己的合同、用途和 form。`kind` 仍表示 work/conflict/synthesis 等用途。
- **Obligation**：需要履行的稳定责任，可由后继 Task 或其他方法继续完成。负责累计限制，不等于一笔新的资金。
- **Goal**：目标含义/GoalSignature，通常由 compound Task 承载；不新增一套能独立改写 Task 的 Goal 服务。
- **MethodInstance.goal_id**：在新协议中明确定义为所细化 Task 的有类型引用；与 `obligation_id` 分开，不让字符串可随意互换。兼容旧草案字段时由 codec 明确映射。
- **Acceptance**：针对一次产物/检查/子成果的不可变接受回执。
- **GoalResolution**：对一个目标及其 Obligation 的整体满足决定，绑定方法、子 Acceptance、原要求、根审阅和有效性依据。
- **TaskNetwork**：上述对象和多种关系的业务聚合；“多个图”是投影视图，不是相互竞争的数据库。
- **ExecutionDAG**：选定计划中可执行步骤和必要门的投影；不等于候选方法库，也不等于 Agent 通信树。

此澄清是对最新方案的实施细化，不扩大或缩小其目标。

## 2. 当前代码：可以复用什么，缺口具体在哪里

| 当前位置 | 已核实行为 | 对新语义的影响 |
|---|---|---|
| `graph/task_graph.py` | 检查合同、依赖、重复、预算、工具、领域、深度与独立输出冲突；初始 TaskNode 主要保存文本目标及依赖 | 是执行图准入基础，未表达方法/端口/组合目标 |
| `graph/dependency_checker.py` | Kahn 排序；拒绝缺失、自环、重复和环；相同提案顺序产生确定顺序 | 保留校验，不把这个排序器当成持久调度器 |
| `graph/changes.py` | 八类正式图操作；BLOCKED 可改依赖，正在执行的 work 走替代；系统任务受保护 | 保留 legacy；新模式必须先通过方法与责任语义检查 |
| `CommitService._commit_task_graph` | 事务内生成 Task、账户、事件和回执；初始 `parent_task_ids=()`；可追加固定 synthesis | 不能把依赖根节点误作 HTN 目标根；新模式显式绑定根义务 |
| `CommitService._commit_graph_change` | 已有基于 affected-task overlap 的安全 rebase，再验证合并图 | 不是“完全没有并发控制”；需要升级到需求、方法、证据、绑定和集合谓词读集 |
| `artifacts/versioning.py` | 已按实际依赖重算拓扑顺序，ordinal 只做 tie-break | **不再把创建顺序错误列为当前缺口** |
| 同文件 `collect_upstream_inputs/merge_accepted` | 汇入所有祖先接受产物；依赖链允许后产物覆盖前产物；无依赖的异内容同路径报冲突 | ORDER 一旦存在便隐含数据传播/覆盖，是必须解耦的地方 |
| 同文件 `topological` | 检出排序不完整时把剩余任务追加后返回，注释认为来自损坏库 | 静态发现的容错风险：新语义须抛完整性错误，不能“补排序”继续物化 |
| `graph/deduplicator.py` | 同规范化目标、依赖和成功条件被判重复而拒绝；近似重复仅警告 | 它没有自动合并；但此签名不足以判断 scope、参数或真实动作相同 |
| `scheduling/allocator.py` | Frontier 依赖 READY、未暂停、依赖 Task.COMPLETED | 新模式必须检查语义资格及当前 Acceptance，不能只看状态字符串 |
| `planning/manager.py`/初始 graph receipt | 用 synthesis 或某个末端 Task 组织收尾，且有现有最终评审 | 不应说“没有最终验证”；缺少显式 AND–OR 目标满足/组合 Resolution |
| `artifacts/workspace.py` | CAS、原始字节核验、隔离副本、保护资料和真实验证视图 | 继续使用；新增的是精确输入清单与组合策略，不重做安全文件读写 |

上述为源码定向检查，不是故障复现报告。[S01–S12]

### 2.1 不把所有历史执行都当成当前计划

当前部分算法用“所有非 CANCELLED Task”表示 live graph。在完整规划模式中，候选、已放弃方法、历史替代和当前采用方案需要分别表示。不能删除历史以简化 active graph，也不能让历史失败的备用路线一直阻止当前根目标结束。

## 3. 一份任务网络，按语义形成不同视图

```text
Mission / RequirementsRevision
            │
            ▼
TaskNetwork (唯一 Commit 权威)
   ├─ Task + TaskSemanticBinding + Obligation
   ├─ MethodInstance + MethodChildBinding
   ├─ ORDER constraints + DATA bindings
   ├─ Observation/Justification/Acceptance refs
   ├─ PlanRevision + adopted membership
   └─ GoalResolution + dispatch generation
            │
      ┌─────┼───────────┐
      ▼     ▼           ▼
  HTN视图 执行DAG视图  证据/解释视图
      │     │           │
  Planner  Scheduler   Verifier/Context/UI
```

### 3.1 关系的精确定义

| 关系 | 方向与含义 | 进入执行环检查？ | 是否自动带入正文？ |
|---|---|---|---|
| refinement | compound Task → MethodInstance → child occurrence | 不直接混入；由编译器生成门/顺序 | 否，只提供任务背景 |
| ORDER | 前步骤的指定事件 → 后步骤准入 | 是，按事件/阶段展开 | 否 |
| DATA | producer output port → consumer input port | 是，生产先于消费 | 只带入该绑定声明的产物 |
| ASSUMPTION/JUSTIFICATION | 方法/结果 → 被引用观察和证据 | 另外检查证明循环和前提可得性 | 按权限及 Context 预算 |
| supervision | 管理 scope → 执行者 | 不进入 Task DAG | 不继承全部历史或权限 |
| resource conflict | 两个工作对真实对象存在互斥关系 | 对称冲突不是两条有向依赖；先选择合法顺序/锁方案 | 否 |

**不能把所有边取并集，再调用一次 DAG 检查。**一个父目标等待孩子结果、孩子引用父要求，作为带不同含义的关系是合理的；混成一套先后边就会出现伪循环。

执行边在公开表示中统一为 `producer/predecessor → consumer/successor`。兼容旧 `key→dependencies` 字典时显式翻转，避免两个模块相反解释。

### 3.2 AND–OR 需要方法节点，不用普通子节点列表冒充

```text
T-root / O-root
   ├─ M-A  [AND: C, A, Join-A]
   └─ M-B  [AND: C, B, Join-B]
```

根目标采用哪种方法由 PlanRevision 记录。每个方法的必要 child occurrence 都需满足；方法之间为获准替代关系。替代方法不自动获得执行资格；搜索可保留多个候选，只为当前批准的探索/执行范围创建真实 demand。

AND 表示共同必要，不表示全序。OR 表示替代满足，不表示任选一个子任务成功就行，也不表示不需再进行根目标独立审阅。

### 3.3 方法递归与执行循环要区分

MethodDefinition 可以递归；每次实例化要有不同 occurrence、grounded parameters 和展开进展/燃料。有限已展开实例的执行投影必须无环。不得让 Task A 的执行依赖直接指回自身，模拟递归。

环检测只证明无环；方法可约化、端口可绑定、前提可满足及根要求覆盖另行验证。Formal 模式保存 HDDL、后端身份和分解见证；开放式模式标记证据驱动，不冒称形式完备。[W02,W05]

## 4. 数据模型和版本：先固定这些合同

### 4.1 Task 的三类变化不能共用一个 version

| 标识 | 变化原因 | 不应该因什么变化 |
|---|---|---|
| `contract_revision/hash` | 目标、成功条件、输入合同、效果/能力要求变化 | 心跳、开始运行、UI位置 |
| `record_version` | 原子状态更新/CAS | 不自动使业务成果内容失效 |
| `dispatch_generation` | 执行权或输入被替代、取消、scope撤销 | 纯排序显示、无关分支进展 |
| `PlanRevision` | 采用方法、成员、ORDER/DATA 或绑定发生正式变化 | 每个 token、每次 heartbeat |
| `validity_revision` | 观察、支持和验收当前可用性变化 | 无关计划分支变化 |

新模式的不可变合同与语义绑定通过 `task_semantics` 等表关联已有 Task，不重写旧 Task JSON。完整更换合同或 primitive→compound 采用后继 occurrence，旧 Attempt 保留，Obligation 与真实 Operation 身份按批准规则延续。[D2 §18.5]

`goal_id`、task_id、obligation_id、method_instance_id 必须用不同的名义类型；边界解析决定它引用的对象类型，不能凭字符串前缀取得授权。

### 4.1a compound状态与一致读取

primitive继续现有Attempt驱动状态机。新模式的compound由类型化计划reducer推进：BLOCKED表示前提/细化未就绪，READY表示存在可采用的合法方法，ACTIVE表示已采用方法并有子义务在推进，VERIFYING表示组合评审进行中，COMPLETED仅在有效GoalResolution提交时产生。它不会创建一个假的Worker Attempt来驱动这些状态。只有新模式明确采用这一form相关解释；旧Task状态语义不变。

历史COMPLETED不因来源撤回变为ACTIVE，当前有效性另行投影。primitive需要细化时采用后继occurrence而非原地把正在运行的记录改为compound。

一个公开快照应在同一读事务中取得 `(plan_revision, state_event_watermark, validity_version)` 和对应对象。PlanRevision固定结构，不承诺其中Task执行状态永不变化。前端不能将新图边与旧节点状态无标识拼成“当前快照”。

### 4.2 MethodInstance / ChildBinding

MethodInstance 至少保存：定义 ID/version/hash、父 Task 合同引用、参数值、前提见证、假设、assurance 范围、本次采用状态、所属 Mission、规划依据。

`ChildBinding` 至少保存：`instance_id, slot_key, occurrence_id, obligation_id, requiredness, reuse_policy, accepted_result_ref`。

唯一键 `(instance_id,slot_key)` 保证重放不会多创建孩子。不能把自然语言数组索引当作唯一长期身份。模型提供局部 key；正式 ID、scope、budget owner、generation 由系统核验或绑定。

### 4.3 DATA 端口，不再“祖先所有文件”

一条未执行的数据绑定描述 producer task ref/output port → consumer task ref/input port，带输入 schema、可接受的产品版本/验收策略、敏感性和目标路径。

进入 Attempt 前生成不可变 `ResolvedInputBinding`：

```text
binding_id
producer_task_ref + output_port
producer_result_id
acceptance_id + support_revision
artifact_id + content_hash
consumer_task_ref + input_port
materialization_target
read_policy / freshness / disclosure_scope
```

未来生产者尚未完成时可以保留符号绑定；**冻结 dispatch intent 时必须解析为精确身份**，不能把 `latest` 留给 Worker 每次调用时自行解释。

只有产物 schema、来源权限、输入版本与验收满足合同，才允许绑定。一般 JSON Schema 蕴含关系并非简单判断；初始执行检查使用注册兼容规则/相同schema身份并对实际值再次验证。无法证明兼容时产生显式转换 Task 或拒绝，不用 `Any` 放过。

一个 input 需要多份文件，使用显式 list/map port；多个候选 producer 需要冻结选择，或创建确定性的 merge/synthesis Task。不可把两个来源按拓扑序覆盖成一个路径后当作组合完成。

如果 B 必须修改 A 的代码，记录 transform 输入（A的精确hash）和输出，以及审核/合并政策。**“B依赖A”不再本身授予覆盖A全部文件的权利。**

### 4.4 ORDER 的触发条件

ORDER 必须绑定事件含义，例如“前项合法接受后”或“前项已确定结束并允许进入清理”。默认成功路径使用明确的接受条件；清理/补偿使用单独获准的结算条件，不把失败当成功。

真实外部结果 UNKNOWN 不满足“已确定结算”。一个调用退出、本地协程取消或 Lease 过期，不足以满足它。

纯顺序已发生的历史事实通常不会因内容新版本失效；但其 guard 若引用某项验收/权限，guard 仍必须重新求值。不能笼统宣布所有 ORDER 永远不受变化影响。

## 5. HTN 编译为执行 DAG：关键算法

### 5.1 compound 不能与孩子互相等待

不要创建：`parent完成→child开始` 同时 `child完成→parent完成`。

编译器为 compound Task 建立派生 `entry/exit` 门：entry 只表示选定方法及输入条件获准；exit 只在其 GoalResolution 有效后打开。孩子满足后触发实际的 composition/review 工作，再打开 parent.exit。

```text
parent.entry
    ├─ child1.entry → ... → child1.exit
    └─ child2.entry → ... → child2.exit
                   \      /
                 composition / independent review
                              ↓
                         parent.exit
```

这些门不是伪造已运行的 Worker，不创建假的模型费用或通过记录。必要的组合/审阅本身是实际工作，必须通过既有预算和 Agent/Verifier路径。

外部 ORDER `P before Q` 默认编译为 `P.exit → Q.entry`，实现 HTN 任务块的先后语义。能否放宽到部分输出流式消费需要独立声明的更强合同；本版不默认允许部分工具响应作为完成输入。

### 5.2 编译步骤

1. 冻结 requirements、选择、方法定义与观察版本。
2. 检查变量作用域、参数类型、目标签名、真实 Operator 和方法准入。
3. 生成 method/slot 对应的稳定 occurrence；只展开所需 frontier，保留未展开 compound。
4. 保留每个出现位置；对允许 reuse 的槽绑定精确 Acceptance，不删除 HTN occurrence。
5. 展开 ORDER、生成 DATA 依赖和 compound entry/exit 门。
6. 检查读写威胁与资源冲突。明确资源要求不应依赖 LLM 随意声明，需取注册 Operator上界＋实际工具目标。
7. 生成或验证组合义务和根 Resolution 所需输入；未覆盖要求拒绝或交独立规划审阅。
8. 对执行投影做循环、缺边、重复slot、端口、输入可得性和规模检查。
9. 生成完整 ProposedPlanDelta、预算需求、protected tail 与语义 read-set。
10. 交唯一 CommitService；不直接创建 Agent或访问生产工具。

### 5.3 算法选择

当前规模采用迭代式 Kahn＋邻接表；稳定排序可用 heap，复杂度约为 `O((V+E) log V)`；不用每次排序时线性 `keys.index`，不用全图递归访问造成深栈。算法是局部实现选择，不代表实测已有性能收益。

网络较大时采用反向索引和局部 reachability：插入 u→v 前检查 v 是否可达 u。完整/合并后的执行图仍做必要全局校验，尤其多个提案并发合并时。只有 profiling 证明图检查是瓶颈，才引入动态拓扑顺序维护。

Python `graphlib.TopologicalSorter` 可用于冻结快照的检查和交叉测试；其 prepare 后不能再添加节点，因此不能作为长期可变任务状态权威。[W01]

## 6. Frontier：不再只检查 dependency.COMPLETED

规划和执行有两种不同 frontier：

```text
PlanningFrontier：待细化compound、待取证前提、待选择方法、待修复义务。
ExecutionFrontier：当前采用计划中符合准入的primitive任务/合法后续候选。
```

统一的 `evaluate_readiness()` 返回结构化理由，不发模型请求、不改账本：

```text
等待细化 / 等输入 / 等顺序事件 / 等前提取证 / 等审批
/ 等资源 / 外部结果未知 / 观察不可用 / 已过期
/ 当前允许派发候选
```

`EligiblePrimitiveTask` 只能由受控验证函数构造，但不是安全令牌；真正 reserve+dispatch 和后续工具/Provider handoff 还要复查实时资格。

允许派发的必要条件：当前Mission允许继续；语义绑定完整；任务为primitive；当前有获准demand；方法/要求/Observation在相关范围有效；ORDER满足；DATA已解析到可披露的有效结果；没有冲突的未决Operation；当前Attempt/候选策略允许；权限、预算与实际资源满足。

READY可以作为可重建索引/显示状态，不能绕过上述门。Compound 即使旧状态字段写了READY，也不得进入普通 Worker 路径。[D2 §18.5]

如果计算成本较高，缓存 readiness 并附 read-set；正式准入事务验证这些版本，变化后重算。不能信任几分钟前列表页缓存的可执行状态。

## 7. PlanRevision 与原子改图

### 7.1 图不是每次覆盖一个大 JSON

每次正式 PlanRevision 保存不可变 delta、版本化事件、采用方法、成员/边变更、语义 read-set、相关检查回执和 hash。Current State 由同一版本 reducer在同一事务更新，建立查询索引；定期保存有hash的快照。不是每次心跳复制全任务史。

原始合同、事件及旧Revision保留；索引可重建。新模式以事件＋受控基线定义重建来源，不保留另一份可独立编辑的 graph JSON 与边表。现有Task/费用/执行账本职责不变，新graph事件只拥有声明覆盖的规划状态。

### 7.2 两级 Proposal

上层语义操作建议采用：

```text
RefineTask
AdoptMethod / RetireMethodUse
BindAcceptedOutput
ReplaceWorkRepresentation
ReviseRequirementBinding
SubmitGoalReview
```

这只是图领域命令词表，不是一套新的通信平台。Compiler将其转成类型化delta；可以复用现有八个图操作的安全实现，但新模式不开放绕过方法/Obligation的裸 `add_task` 旁路。分析、取证和系统组合Task也必须有合法义务和用途绑定。

### 7.3 Read-set 必须包含集合谓词

当前已有 affected-task overlap rebase，不应重做成只有严格全局版本相等。扩展检查到：

```text
requirements_revision
Task contract hashes / plan membership
MethodInstance semantic revisions
相关Observation与支持集版本
DATA binding/Resolution版本
manager scope epoch
预算账户余额/hold（事务内查）
依赖集合/活跃消费者集合的version或digest
“尚不存在某个绑定/动作”的唯一约束或谓词版本
```

“我没读到那个Task”不是保证。两个提案分别新增 A→B、B→A，即使各自只改一个节点，在合并后的图里仍可能形成环；应在当前事务状态上完整再验证。

### 7.4 提交伪代码

下面只表达调用职责，不是可直接替换当前仓库的实现。

```python
def commit_plan_revision(command, principal):
    authorize_command_identity(principal, command)
    with store.transaction():
        old = receipt_for(command.command_id)
        if old is not None:
            require_same_payload_hash(old, command)
            return redact_for_current_reader(old, principal)

        current = read_current_plan_and_related_state(command.mission_id)
        require_mission_writable(current)
        require_scope_epoch(command, current)
        check_semantic_read_set(command.read_set, current)

        delta = apply_proposal_to_current_snapshot(command, current)
        validate_method_network(delta)
        validate_execution_projection(delta)
        validate_data_bindings_and_demand(delta, current)
        validate_resource_and_operation_conflicts(delta, current)
        validate_budget_conservation(delta, current)

        revoke_affected_dispatch_generations(delta)
        events = build_complete_plan_events(current, delta)
        append_events_and_apply_versioned_reducer(events)
        write_dispatch_cancel_outbox(delta)
        return insert_commit_receipt(command, delta, events)
```

身份认证优先；同命令回执优先于“Mission已终态”的拒绝判断，保证已成功命令在任务结束后重试仍能查询原结果。返回也遵守当前读取权限；幂等不授予被撤销权限。

重型求解、embedding、LLM审阅在事务外；进入事务后验证其输入/输出身份与read-set。若全图验证超出短事务预算，可在事务外生成检查证书，再以完整结构版本CAS确认；变了就重新检查，不能跳过。

SQLite单库写入串行化与BEGIN IMMEDIATE可以承载该边界；WAL支持并发读，但并不自动验证图业务条件。[W03] 与execution.db/真实外部环境仍依赖持久Outbox/Intent和回执，不宣称跨库exactly-once。

## 8. 根据反馈局部修复

### 8.1 反馈分类，不把所有问题当成再拆一次

| 反馈 | 动作 |
|---|---|
| 实现缺陷 | 同合同的新Attempt，沿用Obligation累计限制 |
| 缺资料 | 补合法输入/取证工作 |
| 范围太大 | 受控细化成后继结构 |
| 方法前提否定 | 方法实例失效，选择替代 |
| 权限不足 | 等审批/缩小获准范围，不靠拆分绕过 |
| 工具结果UNKNOWN | 核对原Operation，不新开同一实际动作 |
| 用户改要求 | 新RequirementsRevision＋影响分析 |
| 积压/观察失败 | 调度或基础设施处理，不误写内容失败 |

### 8.2 影响分析的集合

从变化的产物端口、要求、Observation或支持理由出发，使用反向DATA和支持索引找到候选受影响集合。先标记NEEDS_RECHECK，重新计算有效理由；不要一发现一条引用过期就销毁所有下游事实。

输出至少包括：

```text
retained：仍然成立，可继续使用
revalidate：可能成立，需要核对或补Review
retired_memberships：当前计划不再采用
superseded_work：需阻止后续执行的旧工作
new_occurrences：本次新工作
retained_shared_results：被其他需求仍然消费
unresolved_operations：必须先核对的真实动作
unknown_dependency_coverage：读依赖未覆盖，需扩大保守范围
```

替代祖先方法只是修复候选；共享结果可能有多个消费者，必须覆盖全部相关消费者。调用LLM得到“只影响B”不构成影响范围证明。

### 8.3 借鉴增量构建，但不把Agent当纯函数

Bazel Skyframe记录明确输入依赖，沿反向闭包定位受影响节点，并在结果值未变时剪掉不必要重建。[W04] 本方案采用这两个思路：精确输入和有依据的局部重算。

但Agent会读取时间、外部服务和不确定材料。相同产物hash并不自动说明语义/权限/时效一致，也不保证再次LLM调用得到相同结果。所有实际工具读取应记录产物/Observation身份与scope；依赖追踪不完整时标记覆盖缺口并保守重新审阅，不宣称“最小影响”已被证明。

### 8.4 旧结果与执行代次

任务被替代时，先使相关dispatch_generation不再有未来handoff权限。已发出的请求不能撤回，旧Worker/Verifier的结果和实际费用照常记录，但只可应用于其原合同和绑定。

未受影响分支B可以继续；不能仅因Mission全局graph_version变了就拒绝B的合法审阅。相反A的输入变了，即使节点ID没变，也不能复用旧Review。

新旧工作可能写同一资源时，要有真实资源fence/版本检查；旧进程的SQLite租约失效不等于外部工具已经停止。不支持可靠核对/条件更新的高风险目标暂停或人工处理。

## 9. 共享子任务与副作用

### 9.1 四种去重必须分开

1. 同命令/同事件重送：幂等回执，不重复应用。
2. 同一个方法slot重复声明：结构错误。
3. 相似目标：仅推荐复用候选。
4. 共用执行或成果：完整合同、参数、scope、输入、时效、验证和effect occurrence一致才准入。

旧 `find_duplicates()` 的目标文本签名只适用于既有提案检查。新模式不能仅因两项文字相同就合并或拒绝；比如两个不同收件人、两个不同月度周期是合法不同工作。[S06]

### 9.2 优先成果复用，活跃共享需Demand

同一不可变Acceptance可满足多个获准slot，各slot身份仍然保留。真正共用仍在执行的Task时增加 `DemandRef(consumer_instance, slot, obligation, producer, state)`。

只有所有活跃需求都退出、没有独立责任、没有未核对Operation、也没有必须保留的提交/验证过程，才允许收敛和清理。未来候选方法仅是prospective demand，不自动花预算；实际探索也要正式准入。

新增消费者和最后消费者撤销必须在相同事务规则下检查集合版本，不能用失效的内存refcount。费用由唯一资金归属承担，归因可多方展示但不能重复消费/退款。

### 9.3 原始Task状态保持历史事实

从计划移除某个尚未执行BLOCKED任务，可以先退役membership并阻止派发，不为适应新图而偷偷增加legacy不存在的状态转移。ACTIVE等按已有取消协议收敛。终态Task不重新打开；新执行表示建立后继任务，历史完成/失败继续可读。

### 9.4 HTN见证保持task occurrence

标准HTN中的重复动作出现位置不是可随便删除的缓存。Formal导出需要显式表达“已满足/可复用产物”的模型，保留每个出现位置到分解见证的映射；Evidence-guided模式也要保存复用授权和消费者。[D2 §8.3; W02]

## 10. 如何判断任务和Mission完成

Execution Task.COMPLETED是一次既有执行事实；GoalResolution描述现在能否凭该产物满足某个目标。

primitive的Resolution需绑定独立Review、实际测试证据（若合同要求）、输入/输出hash和要求。compound的Resolution还需：当前采用的方法仍可用、所需子义务的有效支持齐全、数据接口和组合标准通过、无未决强制风险、根目标要求未被削弱。

```text
satisfies(compound)
 = authorized method exists
   AND all required child obligations are currently satisfied
   AND composition has valid independent review
   AND original requirements and constraints are covered
```

这不是用布尔公式代替内容审阅；布尔部分是检查合法Review及其绑定是否齐全。模型不能修改success_criteria降低标准，空children也不能跳过根要求。

B分支失败不代表A分支已满足的目标失败；可选路线仍在探索不一定阻止用户交付，但它的费用、取消、UNKNOWN和执行清理不能被静默删除。区分“结果已交付”和“所有相关执行已安全收敛”的投影。

来源撤回后，历史完成保持；当前Resolution进入NEEDS_REVIEW/STALE。若还有另一组完整合法支持且没有未解决反证，则可以维持或经重新绑定保持当前有效，记录新决定。

## 11. 持久化和恢复

### 11.1 同一orchestrator.db内的逻辑存储

优先复用现有Store；按语义新增或扩展：

```text
task_semantics / immutable task contracts
obligations / obligation relations
method_instances / child bindings
plan_revisions / plan_memberships
order_constraints / data_bindings
resolved_input_manifests
goal_resolutions / support sets / reverse indexes
demand_refs / per-object generation
existing events / commit_receipts / dispatch_intents
```

所有表都带Mission/tenant归属及正确外键/唯一约束，跨Mission只接受授权的不可变产物引用。不把可变任务网络放进向量库。

新模式图事实由版本化事件/reducer及immutable合同组成，关系表为同事务更新的写侧投影/索引。不要让client、Planner和另一个graph service单独更新边。也不要让execution.db复制预算或方法决策作为另一个权威。

### 11.2 Graph scope的可重建范围

新增事件至少包含：plan delta的全部类型化内容、父revision/hash、slot身份、合同引用、成员采用、DATA/ORDER变更、Resolver决定、retained/superseded集合、dispatch generations、关联预算/动作引用及原始命令身份。

快照包含高水位、reducer/schema版本和hash。恢复从受信任基线重放到该水位并重新计算执行投影、Reverse Index和Frontier。图replay不调用模型、不重试外部动作、不借当前表补缺失历史。

旧记录缺少证据时采用标注覆盖范围的legacy基线；不能伪造完整过去。此包只闭合TaskGraph重建，不声称完整费用/全业务Replay已替代总计划P7。

### 11.3 必须有的崩溃切点

- 计划验证后、Commit前：没有正式图改变。
- 事件/投影/预算写入中途：共同回滚。
- Commit后、派发前：恢复原Outbox。
- SDK已收工作、编排未收回执：相同creation_key/input_id找回原AgentTurn。
- 产物已提交、Review前：只继续Review。
- Review后、Resolution前：核对原read-set再提交，不默认重评。
- 方法被替代但外部旧动作UNKNOWN：保留原action身份与预留，禁止替代动作重复执行。

## 12. 规模和运维语义（不是发布流水线）

当前32任务/深度6是旧policy边界，不能作为“通用HTN”的定义。新Mission绑定版本化上限：live tasks、历史revision、展开节点、AST节点、候选宽度、递归燃料、搜索时间和费用；超限返回明确原因，不无声截断。

建立producer→consumers、observation→support→resolutions、method→active child、resource→holders索引。通过具体变更更新相关readiness，不每个token重扫全Mission。先用完整校验作参照测试，再优化；优化前后结果应等价。

Task DAG无环也不能防所有死锁：资源等待、父Agent占住全部槽位等待子Agent、审批循环均需wait-for分析。使用统一资源获取次序、等待时释放可释放物理槽位、为验证/收尾留容量；逻辑预算reservation不随等候随便释放。

不允许优先级分数绕过安全资格。公平和aging属于Scheduler政策；与图正确性分开测试。

## 13. 代码级变更落点

| 文件 | 实施动作 |
|---|---|
| `contracts/htn.py`、`contracts/obligations.py` [新] | 沿完整计划定义名义ID、TaskSemanticBinding、Occurrence、Method/Resolution合同和codec |
| `graph/task_graph.py` [现有] | 保留legacy；增加v2读取/门，不复用老父/依赖推测方法结构 |
| `graph/task_network.py` [新] | immutable snapshot和类型化关系视图，无DB/模型副作用 |
| `graph/validation.py` [新] | 方法引用、端口、门、active执行DAG和预算要求的纯验证 |
| `graph/dependency_checker.py` [现有] | 抽出可复用迭代拓扑/环证据；全调用链不容忍cycle后补排 |
| `graph/eligibility.py` [新] | 构造Readiness/EligiblePrimitiveTask，解析DATA与当前支持 |
| `graph/demand.py` [新] | 共享消费者、独立责任、取消与资金归属规则 |
| `graph/deduplicator.py` [现有] | legacy保持；新增区分message重复、slot重复、reuse候选与Operation身份 |
| `graph/changes.py` [现有] | legacy八操作保留；新模式需typed delta及规划绑定，阻断裸改图旁路 |
| `planning/htn/compiler.py` [按总计划新建] | Method到网络的唯一编译器，不在graph内再造一个 |
| `planning/repair/impact.py` [按总计划新建] | typed reverse dependency影响分析、未知依赖覆盖、多重支持 |
| `orchestrator/plan_commits.py` [新] | 作为现有CommitService的组合/mixin，事务应用delta/事件/reducer/outbox，不独立写库 |
| `orchestrator/commit_service.py` [现有] | 按冻结planning_mode分派，旧回执/序列化/基线不改 |
| `orchestrator/event_handler.py` [现有] | 连接反馈、HTN、图提交、结果和Resolution；保持短协调代码 |
| `scheduling/allocator.py` [现有] | 旧Mission可保留旧frontier；新模式只接受通过语义门的候选，不能静默fallback |
| `runtime/agent_worker.py` [现有] | 冻结规划/合同/输入/generation身份进入dispatch；不要直接重建旧请求 |
| `runtime/provider_budget_guard.py`/`tool_gateway.py` [现有] | 每次适用handoff复查generation、计划/资源授权；旧结果依旧计费归档 |
| `artifacts/versioning.py` [现有] | v2显式resolve_input_manifest；不按ORDER导入祖先；拓扑失败明确报错 |
| `artifacts/workspace.py` [现有] | 继续CAS校验与保护；仅物化受控InputManifest；输出映射/合并显式 |
| `planning/manager.py` [现有] | legacy terminal/synthesis保留；HTN根采用Resolution而非末端启发式 |
| `storage/schema.py`/`store.py` [现有] | 下一未占用迁移，原checksum不改；唯一约束、索引和事件/reducer接口 |
| `observability/replay.py` [现有] | 新graph coverage，明确与legacy及完整P7范围的区别 |
| Host projection/Store/UI [待定位] | 后端输出树/执行图/差异/证据和readiness解释；不在前端再判预算和目标满足 |

文件名为拟议实现落点；实施时按现有等价模块复用。SDK原有修复继续保留，本轮不要求全仓类型迁移或TS重写。

## 14. 完整交付场景与施工组织

不新增另一套Phase编号；本专项直接映射总计划的P1、P2、P3及其相关集成。

### 场景A：同一引擎生成、执行并验收一个层次计划

注册两个不同任务域的方法，不改主循环；使用compound/primitive、AND/OR、ORDER/DATA、前提观察、实际BaseAgent、独立Verifier和根Resolution。验证不同method中的同名文件不会偷偷混合。该场景同时包含最小持久Commit和重启，不把恢复全部留到最后。

### 场景B：共享C，两种方法，一种执行中被反证

C的只读合同已验证；A前提被否定；切B并保留C；若B还缺D则追加D；旧A费用保留，旧Review不批准B；组合产物不兼容被拒绝；最后B合格并完成根目标。

### 场景C：并发改图、证据失效、真实中断与重建

两个Manager在同一base提出变更，分别验证无冲突合并和合并后循环拒绝；创建消费者与取消竞争；提交前后SIGKILL；重建新图投影不调用Provider；下游仅因真实DATA/支持变化重判，不因无关图版本变化全重做。

所有场景的结构、失败/取消、预算/权限和历史兼容在实现时一起设计，不能只实现成功流程后称完整版。总计划其它要求仍逐项跟踪，本专项通过不等于P1–P9全部完成。

## 15. 验收方法

资料包提供32条细化场景TG01–TG32，均为**待实现、待执行**。不声称已经运行这些测试。

- 纯模型/codec/图性质：对照简单参考解释器；排列无关节点顺序、改变ID不应改变满足和冲突判断。
- 状态化属性测试：Hypothesis生成新增、细化、绑定、完成、撤回、取消、重启等序列，与参考模型逐步比较；每步检查无环、无越权、无重复执行及预算守恒。[W06]
- 真实事务：两个独立SQLite连接、可控制barrier、进程级中断，验证不是只mock一个SUCCESS。
- 形式规划：IPC HTN全序/部分序基准，固定支持片段和独立计划/分解验证；自己的动态扰动测试单独报告。[W02,W05]
- 真实Agent：至少一个受控新领域任务和一个运行中换方法场景；独立验收器不能读取外部benchmark隐藏答案。
- UI：当前scope快照、事件游标、方法状态和why_not_ready；未知消息显示协议错误，不改业务UNKNOWN。

不以图画得像样、类名齐全、测试数量或某次LLM自报成功作为完成标准。

## 16. 与60项总要求的关系

直接覆盖或提供必需接口：R03–R17中的相关项、R25、R28–R30、R36–R41、R44、R48、R50、R57–R59。完整精确映射见`requirement-map.json`。它们保留原ID、标题、主工作包及要求，不重编号。

主要直接语义：R04/R05/R06/R08/R10/R11/R12/R13/R14/R15/R16/R17/R25/R36/R57。R37等只提供本模块证据，不声称替代完整业务Replay；R28等与独立Verifier集成，不声称仅凭图校验完成内容验证。

## 17. 结论

现在应该将TaskGraph作为HTN连接的正式核心，而不是图形展示辅助。

**一个高质量TaskGraph必须同时知道：当前采用什么方法、哪些工作真的需要做、输入来自哪个有效结果、哪些条件仍成立、谁有权改变计划、失败后保留什么，以及什么证据足以完成父目标。**

保留已有动态DAG和安全执行底座，把数据、版本、方法与满足语义补齐；不以更换图算法库代替这些工作。

## 18. 可复核来源

### 文档

- D1：用户原始《Agent 编排层完整设计方案》，重点§6、§7、§8、§15、§17、§20、§25、§26、§27、§31。
- D2：`simpleharness-complete-target-and-gap-plan-2026-09-16.zh-CN.md`，重点§6–9、§18.3–18.6及60项要求。源文件保持不变。

### 固定源码（均为当前读到的同一commit）

统一前缀：`https://github.com/DennyWanye/simple-harness-sdk/blob/61a85eb7e8c003fa894497090f5de893419ebbd1/`

| ID | 文件/范围 |
|---|---|
| S01 | `src/agent_orchestrator/graph/task_graph.py` TaskNode/ValidatedGraph/validate_graph |
| S02 | `src/agent_orchestrator/graph/dependency_checker.py` 全文件 |
| S03 | `src/agent_orchestrator/graph/changes.py` 合同及validate_change |
| S04 | `src/agent_orchestrator/orchestrator/commit_service.py` 800–1475，含两个graph提交入口 |
| S05 | `src/agent_orchestrator/artifacts/versioning.py` 全文件 |
| S06 | `src/agent_orchestrator/graph/deduplicator.py` 全文件 |
| S07 | `src/agent_orchestrator/artifacts/workspace.py` 1–490 |
| S08 | `src/agent_orchestrator/scheduling/allocator.py` frontier与allocate |
| S09 | `src/agent_orchestrator/planning/manager.py` synthesis/terminal约定 |
| S10 | `src/agent_orchestrator/orchestrator/event_handler.py` 当前协调链和artifact导入 |
| S11 | `src/agent_orchestrator/runtime/agent_worker.py` AgentBridge |
| S12 | `src/agent_orchestrator/storage/schema.py` 当前权威存储边界 |

### 外部一手资料（实现依据，不是整个系统的正确性证明）

- W01：Python `graphlib`：`https://docs.python.org/3/library/graphlib.html`。
- W02：PANDA Planning Framework：`https://panda-planner-dev.github.io/`。
- W03：SQLite Isolation：`https://www.sqlite.org/isolation.html`。
- W04：Bazel Skyframe：`https://bazel.build/reference/skyframe`。
- W05：IPC 2023 HTN Tracks：`https://ipc2023-htn.github.io/`。
- W06：Hypothesis Stateful Testing：`https://hypothesis.readthedocs.io/en/latest/stateful.html`。
