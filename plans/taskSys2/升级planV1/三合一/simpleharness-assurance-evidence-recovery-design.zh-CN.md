# SimpleHarness：目标验收、证据有效性与执行恢复联合设计

**版本：AER-DESIGN-1.0 · 2026-09-16 · Asia/Singapore**  
**固定 SDK：`873fd4a2c3bf7c0a2f8927e2fcdcbccf7e45a11d`**  
**定位：**对 FULL-TARGET-1.0（2026-09-16）和 TG-DESIGN-1.0 的三个专项作详细设计。保留原 60 项要求、P1—P9、Task/Obligation/MethodInstance/GoalResolution 术语；不建立另一套 Goal 服务，不重写 BaseAgent，不引入用户长期 Memory，不另做发布流水线。

> 本文件是完整接口、算法、事务、兼容和验收设计，不是已经合入 SimpleHarness 的补丁。新增类、表、函数和状态视图均为拟实施规格。`reference/` 只演示局部纯规则，不是生产实现。原仓库、真实模型、外部服务与 Host UI 本轮未运行。资料包的校验与参考测试不等于生产验收通过。

## 阅读路线

- §0—3：依据、现有代码、共同语义和身份。
- §4—7：目标要求、独立审阅、Acceptance/GoalResolution、正式提交。
- §8—11：条件证据、支持求值、失效传播以及 TaskGraph/Context 接线。
- §12—17：操作身份、执行状态、授权交接、跨库可靠性、取消补偿和恢复。
- §18—22：模块/存储落点、实施闭环、测试、来源和约束。

---

## 0. 依据及明确裁决

### 0.1 哪些是原要求，哪些是本专项新增细化

**[沿用原要求 D1]** Mission 有成功条件；Agent 仅提交候选；独立验证；Claim 与 Knowledge 分开；正式状态由 Commit Service 管理；有预算、审批、幂等、恢复和审计。依据原文 §5、§11、§13—18、§20—23。

**[沿用完整目标 D2]** 用户原始要求不由 Planner 私自放宽；Task.form=compound/primitive；稳定 Obligation；历史完成不可改、当前适用性可变；条件 Justification；Operation 跨 Attempt 稳定；未知结果先核对；完整业务重建。

**[沿用 TaskGraph D3]** ORDER/DATA 分离；精确 InputManifest；Method occurrence 不因复用删除；版本分层；语义 read-set 包含集合读取；只有有效 primitive 进入调度；compound 通过组合审阅获得 Resolution。

**[本专项设计决定]** 明确 Review 的阶段、成功表达式、证据四态及按用途有效性、失效屏障、OperationOccurrence 与交接代次、查询否定的证明范围、取消和补偿的恢复责任。这些是下文提出的规范，不能说原文已经逐字段规定。

**[有意细化原文过简规则]** D1 的“租约过期→新建 Attempt”只适用于没有无法确定后果、且重派合法的情形。新语义先核对冻结调用和外部后果，不能仅凭失去心跳产生一次新副作用。D1 的“Verifier PASS→完成”在本专项中解释为“相应范围全部必需准则满足、证据适用且 Commit 成功”，不是任意局部 PASS。

### 0.2 不改原总表，不偷换验收范围

本包的 AER-* 是专项测试编号，不是新增总需求。`requirements-map.json` 保留总需求的原 ID、标题和 P 包归属。映射表示本专项提供设计/测试支持，不表示已经关闭整项要求；R55（用户长期 Memory 不接入）继续排除。

### 0.3 核查范围

最新 main 相对旧 `61a85eb` 仅增加一个文档指针；compare 显示 1 个提交、1 个文档文件，不包含运行代码修改。[S0]

本轮重新读取了 `verifier_router.py`、`mission_coverage.py`、`source_dependencies.py`、`commit_service.py`、`action_commits.py`、`connectors.py`、`accounting_recovery.py` 的明确范围。[S1—S7] 不是整个执行链的审计，也不依据关键词搜索无结果判断某能力不存在。

Host 当前连接仍返回 404。H0 需要在实际环境登记通信、投影、Store、审批、产物和原生测试的真实路径；本文不编造 Host 文件。

## 1. 当前已经有的能力，以及要补的边界

| 已读路径 | 当前事实 | 本专项如何处理 |
|---|---|---|
| `verification/verifier_router.py` | 按 Task verification_policy 执行检查；必需层未运行为 ERROR，记录 NOT_REQUIRED/SKIPPED；Critic 由编排回调执行 | 复用执行入口；增加有完整版本/范围绑定的 ReviewPackage、逐准则评估和组合接受 |
| `verification/mission_coverage.py` | 文档域有原准则目录、已接受评估、来源当前性与 INCONCLUSIVE 汇总 | 不说“无根验收”；新模式泛化为成功表达式与范围化覆盖，不改旧 profile |
| `memory/source_dependencies.py` | 保存历史多来源版本，单独检查当前性；memo/active 区分菱形共享与环 | 复用来源适配；增加多组理由、谓词/方法/输入/验收/缓存的统一有效性 |
| `orchestrator/commit_service.py` | 单一逻辑写者，事务中处理提案、事件和回执 | 新命令仍加入该服务，不新建独立可写 Review 或 TMS 服务 |
| `orchestrator/action_commits.py` | actions/approvals 已有账本和状态，模型不得设置批准及绑定 Artifact 身份；当前 action ID 基于 Mission+connector+operation+target | 保留 legacy；新操作用明确 occurrence 区分同对象的两次合法行为，跨重试稳定 |
| `runtime/connectors.py` | 已区分 authoritative 与 best_effort lookup，L2/L3 的准入有强限制 | 保留强限制；补 retention、迟到请求、查询覆盖范围、条件写、栅栏和结果阶段合同 |
| `orchestrator/accounting_recovery.py` | 根据原冻结 Agent/Turn/调用及价格核对晚到费用；业务终态不是导入禁令 | 保留；新身份通过绑定接入，不把失效结果的真实成本抹掉 |

当前主链已有许多正确设施。要补的是这些设施之间更精确的共同合同，不是重复开发一套“更高级名称”的设施。

## 2. 联合架构：三个逻辑模块，共用一个正式写入入口

```text
用户要求 → RequirementsRevision → TaskNetwork / HTN 方法
                                      │
                           精确输入 + 合法执行范围
                                      ↓
                              现有 BaseAgent
                                      ↓
                       CandidateResult / Artifact
                                      ↓
          ┌────────────── ReviewCoordinator ──────────────┐
          │ 冻结审阅包；独立 Verifier；真实检查；范围覆盖  │
          └───────────────────┬──────────────────────────┘
                              ↓
                  CommitService.accept/reject
                  ↙                         ↘
       Acceptance/GoalResolution        操作候选与审批
                  ↑                         ↓
          ValidityService             Action Executor
      证据、条件、支持及当前用途        原执行账本/连接器
                  ↑                         ↓
                  └────── 真实观察和回执 ─────┘

发生崩溃：Event/Current State/Outbox + execution 事实 + CAS
           → 先核对 → 收敛未决后果 → 恢复合法执行
```

`ReviewCoordinator`、`ValidityService`、操作协调器是模块，不是三个新常驻服务。它们提供纯查询/决策和受控命令；正式修改最终都由现有 Commit Service 管理。

### 2.1 权威表

| 对象 | 权威 | 禁止的替代 |
|---|---|---|
| 用户要求及修改 | 认证用户命令或明确授权的修改政策 | Planner 自动降低标准 |
| 当前 TaskNetwork、预算、正式接受 | CommitService/编排库 | UI、模型、向量检索直接写状态 |
| Provider/Effect 是否交接及已知结果 | 既有执行账本和可核对连接器回执 | Worker 的“我做过了” |
| 证据正文 | CAS/来源存储及可见性合同 | 向量、摘要当唯一原文 |
| 开放内容判断 | 独立 Verifier 的具体 Review | 同一 Worker 自批、多个模型投票充当证据 |
| 当前可用性 | 按用途、版本、时间和权限求值的 ValidityWitness | 一个永久 VERIFIED 布尔值 |
| 用户看见什么 | 后端正式投影 + 真实交付状态 | 前端从日志文本推导成功 |

## 3. 必须统一的身份、版本与不变量

### 3.1 身份

Mission/Task/Attempt/BaseAgent/AgentTurn 保留。新增或复用计划中的 Obligation、MethodInstance、RequirementsRevision、ReviewPackageId、ReviewRecordId、AcceptanceId、GoalResolutionId、OperationId。

- `ObligationId`：稳定责任与累计尝试/成本；不自动等于一次副作用。
- `OperationOccurrenceId`：一次获准现实意图的位置，系统基于用户命令/周期/方法 effect slot 分配；同一责任中可能有多个合法 occurrence。
- `OperationId`：一次冻结操作；重新派 Worker 或切换方法不自动换此身份。
- `HandoffId/epoch`：同一操作的一次物理交接；用来追踪而不是取得一份新业务授权。
- `ReviewPackageId`：具体审阅目标；追加取证使用不可变 EvidenceAppend 和证据集合 revision，不改已冻结模型请求。

Python 用 NewType/判别联合区分 ID；边界 codec 校验结构；归属、权限和当前性仍在事务中检查。不能凭前缀、cast 或哈希相等授予权限。

### 3.2 版本

`contract_revision`、`record_version`、`dispatch_generation`、`plan_revision`、`validity_revision` 保持 TaskGraph 的不同含义。新加 `policy_ref`、`evidence_set_revision` 和 `authorization_epoch`，用于绑定适用规则与并发判断。

全局计划版本可作为诊断，但不得使无关分支结果全部失效。要求版本改变时，旧审阅保留历史；确有等价子准则需要复用，产生受测的 CarryForwardReceipt，绑定新要求，不修改旧 Review。

### 3.3 共同不变量

I01 候选不是接受；I02 审阅过程结束不是审阅通过；I03 接受报告不是批准外部动作；I04 批准操作不是操作已发生；I05 技术调用成功不是用户目标满足。

I06 原始要求不能暗中放宽；I07 未执行/错误的必需检查不等于 PASS；I08 历史回执不被当前性变化重写；I09 当前可用不是永久正确或公开可披露。

I10 超时/租约失效不是 NOT_APPLIED；I11 重试不改变操作身份与参数；I12 补偿是新操作；I13 已发生的费用和后果即使结果过期也必须记录。

I14 事件、写侧投影、命令回执和 Outbox 同库事务提交；I15 跨库/外部世界不假定 ACID；I16 Replay 不重发操作、不恢复旧权限；I17 跨 scope 读取必须先授权。

I18 UNKNOWN/CONFLICT 不用于安全前提放行；I19 stale 异步传播期间，相关执行门不能使用未经复核的缓存；I20 同一事实在不同时间/用途的可用性可能不同。

---

## 4. 专项 A：用户要求与成功表达式

### 4.1 RequirementsRevision

每版要求不可变，保存原文/原文引用、认证主体、允许解释范围、准则目录、成功表达式、交付合同和授权修改凭据。

一个 Criterion 至少包含：

| 字段 | 作用 |
|---|---|
| `criterion_id`、`revision` | 稳定的可追踪准则 |
| `origin`、`source_ref` | USER_EXPLICIT、POLICY_REQUIRED、DERIVED；关联原文，不冒充用户话 |
| `statement`、`scope` | 要判断什么、针对哪些输入/环境 |
| `requirement_class` | HARD_CONSTRAINT、REQUIRED_OUTCOME、PREFERENCE |
| `evaluation_kind` | SEMANTIC、EXECUTION_RECEIPT、DETERMINISTIC、FORMAL |
| `required_evidence_policy` | 必需检查及其版本/覆盖/独立性，不是模糊分数 |
| `phase` | START、MAINTAIN、ACCEPT；定义在哪个阶段检查 |
| `temporal_use` | HISTORICAL_AS_OF、CURRENT_AT_USE、CONTINUOUS |
| `amendment_policy` | 谁能修改，能否显式豁免；安全底线不可由模型豁免 |

成功表达式采用受限数据 AST：`criterion(id)`、`all(children)`、`any(children)`；children 不得为空。显式否定需另有对应可核查准则，例如“无越权访问”，不以“不知道发生了”当作不存在。AST 与文字的语义一致性由独立审阅/用户确认负责；程序只保证其结构和组合正确。

硬约束全部独立 AND；结果部分允许原文批准的 OR。例如“提供可检查证明，或可复现反例”是合法 OR；不能由模型把“Windows 和 Linux”改成任一平台即可。

### 4.2 覆盖与细化

Planner 提交 `CriterionCoverageProposal`：父准则→子义务/输入/组合检查。结构检查要求所有必需准则都有覆盖，语义审阅检查其覆盖是否合理。一个准则可跨多个孩子；一个孩子也可贡献多个准则，但必须记录范围。

新增取证/修复工作可以不新增用户要求，只新增为了履行原要求的派生义务。派生准则默认不能取消父标准，也不能扩大权限/费用上限。歧义无法在获准假设下处理时创建澄清责任；用户答复产生显式修订。

### 4.3 结果、未知与部分交付

逐准则 verdict 使用 PASS/FAIL/UNKNOWN；还保存独立 execution_status：NOT_RUN/RUNNING/SUCCEEDED/ERROR/CANCELLED。执行 ERROR 投影为未取得足够判断，不把它写成内容被反证。

表达式求值：all 中任一 FAIL 则 FAIL、全部 PASS 则 PASS、否则 UNKNOWN；any 中任一 PASS 则 PASS、全部 FAIL 则 FAIL、否则 UNKNOWN。未采用替代方法可以未评估；但硬约束失败不能被结果 OR 抵消。

未经用户/已冻结政策许可，不把缺失必需证据写成“带限制成功”。报告可交付作为 PARTIAL 产物，必需责任仍未满足。若原始目标就是“调查并明确未验证项”，未验证项的准确披露可以满足该目标，但不能因此把对应外部主张标为已证实。旧文档域 inconclusive-share 规则保持旧模式；新模式必须在要求合同中明确允许的有限性。

## 5. 独立 Review 的完整执行协议

### 5.1 审阅用途分开，不复制执行内核

`ReviewPurpose`：TASK_CONTENT、METHOD_PLAN、COMPOSITION、ACTION_PROPOSAL、OPERATION_OUTCOME、MISSION_FINAL。

Task Critic 和 Mission Judge 使用明确 typed context：前者绑定真实 Task/Attempt 与 Task 账户，后者绑定 Mission 和 verification view/根准则与 Mission 账户。复用既有 BaseAgent/费用准入；不让 Mission Judge 假装有 Worker Attempt，不给每个新用途复制一套 Runtime。

### 5.2 ReviewPackage：不可变审阅锚

包含：purpose、subject typed ref、要求及合同 revision、MethodInstance、InputManifest hash、精确候选结果和产物 hash、直接子 Acceptance/Resolution、准则全集及表达式、审阅政策、缺陷历史、现有可信执行证据、反对证据索引、允许取证的工具与数据范围、审阅预算/停止条件、independence policy、语义 read-set。

系统填写身份/权限/账户/hash；Worker 只能提交自己的候选局部引用。manifest 哈希绑定语义字段，传输 header、数据库自增值、心跳不参与错误的整体内容身份。

每次模型请求按当前有效政策和固定目标构建 Context，但一旦请求冻结，恢复复用原字节。Verifier 可以继续取证；每条新证据形成独立 ledger/CAS 引用，结束时 `ReviewRecord` 冻结使用的 evidence manifest。不能覆盖原 ReviewPackage 或伪造过去已看到新证据。

### 5.3 Verifier 如何实际工作

1. 系统先校验候选结构、scope、完整文件和不可变 hash；确定无效的输入直接拒绝，不浪费 LLM。
2. 确定必需层及评审对象，不允许 Reviewer 删除必需测试/形式检查。
3. 在隔离副本准备获准工具，读取原要求、产物、真实检查和反对证据。Worker 的解释是待核实陈述，不是事实。
4. 独立 BaseAgent 提出审查计划并使用受控工具。结果可重复的检查从已绑定回执复用；过期/输入变化/版本不符时重跑。
5. 对每个准则提交 verdict、证据列表、范围、局限、未覆盖部分与简短理由。不得输出隐藏思维链作为必要审计数据。
6. 系统验证证据身份、执行完备性、实际目标和输出格式，并求值成功表达式。
7. 保存 ReviewRecord。open-ended 的接受必须有独立语义评审；确定性检查可先行拒绝。Review 的 ACCEPT 仍是候选，正式接受由 Commit 检查当前状态。

独立性最低要求：Reviewer Agent 身份不同于生产该候选的 Agent；无修改候选工作区权限；必要的测试在新隔离视图执行；不能审自己在 review 中改出来的版本。可选择不同模型，但不同模型不是“天然独立证据”；多人相同来源不自动变成独立支持。

### 5.4 ReviewRecord 与最终结论

逐准则结果必须与 Package 的准则目录一一匹配（可记录明确不采用的 OR 分支），未知 ID、重复 ID、遗漏必需 ID 拒绝。Receipt 来自真实 dispatcher/工具而非模型生成的字符串。报告范围不得大于证据覆盖范围。

全局语义结论沿用 ACCEPT/REWORK/INCONCLUSIVE/REJECTED：

- ACCEPT：对指定目标的规定表达式与硬门成立，且语义 Review 符合政策。
- REWORK：具体缺陷可以继续修改；附缺陷与应复查范围。
- INCONCLUSIVE：当前证据不足或冲突无法解决；不是 PASS，也不是不可解。
- REJECTED：候选不适用、违反禁止条件或按政策不再接受该候选；不自动关闭整个 Mission。

审阅基础设施错误单独记录为执行故障，不能为了满足枚举伪装成模型判断。限额重试、升级独立 Reviewer/人工及停止均消耗同一义务预算；禁止重复抽样直到有一个 PASS。冲突 Review 保留并触发独立仲裁，不使用简单多数票。

## 6. Acceptance、GoalResolution 与完成语义

### 6.1 四个事实不能互相替代

`ReviewRecord`：谁对哪份候选作了什么判断。

`Acceptance`：Commit 在指定要求、产物、输入、证据和政策下接受了一份贡献。

`GoalResolution`：对指定 Task/Obligation 的整体满足，绑定采用方法、必需孩子、组合 Review 和全部根准则；不是新 Goal 服务。

`DeliveryReceipt`：输出已持久可取、通知已入队/已发送/用户已确认的哪一阶段。只有目标要求的交付阶段才能成为完成条件。

已有拟议 goal-resolution-v1 Schema 保持不变。其 `validity` 解释为序列化时的读侧投影或创建时快照；历史接受凭据不随它重写。当前有效性通过独立版本化 witness 输出。需要 wire 增加 revision 时使用新的消息封装/显式版本，不能修改旧 Schema 使历史字节含义漂移。

### 6.2 正式接受公式

```text
Acceptable(subject, now, purpose) =
    输入/产物/要求/Review 身份匹配
  ∧ 全部硬约束成立
  ∧ 规定成功表达式为 PASS
  ∧ 所有必需实际检查已完成并通过
  ∧ 独立语义Review满足政策（开放任务）
  ∧ 当前用途所需支持有效、可见、可用
  ∧ 在途执行/取消/方法采用关系允许接纳
```

compound 还要求选中合法方法、全部必需 occurrence 有有效贡献、DATA 精确绑定、组合义务通过。替代路线的失败不阻塞已满足的 OR；剩余路线必须安全取消/收敛或显式移交，不能留下无人负责的副作用。

Mission 完成要求根 Resolution 满足当前需求、交付合同达标、关键未决动作已核对或经明确政策移交。可以在任务正常结束后导入迟到费用，不能以终态拒绝真实性记账；已知未决实际动作不可被当成已完成。

### 6.3 避免“先完成 Mission 才能执行最后操作”的闭环死锁

报告验收 `Acceptance(report)` → 对冻结操作候选进行 `ACTION_PROPOSAL` Review → 用户/政策授权 → 实际执行 → `OPERATION_OUTCOME` Review/检查 → 根 `MISSION_FINAL`。

发送需要报告已接受，而不需要整个 Mission 已完成。根 Mission 如果要求发送，必须等对应结果回执，不能只看报告 PASS。任务内部准备与外部后果分阶段，禁止彼此循环等待。

### 6.4 版本变化和复用

新要求影响哪些准则由覆盖关系/独立修订审阅确定。旧 Review 永不改；不相关子结果可通过显式 CarryForwardReceipt 复用，记录等价准则、输入、当前支持和新 scope。泛化语义等价不能只比文字 hash，需要独立检查。根任务仍按新要求再评审。

Mission 已处终态后发生新要求，建立后继 Mission 或既有 Commitment 新周期，关联原有效成果；不将旧终态改回 ACTIVE。

## 7. accept 命令的原子边界

拟新增 `accept_review` / `commit_goal_resolution`，内部复用 CommitService：

```python
# 职责伪代码，不是可直接替换当前仓库的补丁。
def accept_review(command, principal):
    authenticate_and_authorize_command(principal, command)
    with store.transaction():
        prior = read_command_receipt(command.command_id)
        if prior is not None:
            require_same_command_hash(prior, command)
            return disclose_receipt_under_current_policy(prior, principal)
        subject = load_bound_subject(command)
        check_contract_input_artifact_and_review_bindings(subject, command)
        check_scope_epoch_and_related_read_set(command, subject)
        witness = evaluate_validity_for_acceptance(subject, command)
        require_current_witness(witness)
        require_success_formula_and_required_checks(subject, command)
        require_independent_semantic_review_when_required(subject, command)
        require_no_unowned_critical_operations(subject)
        events = build_acceptance_events(subject, command, witness)
        append_and_project(events)
        insert_outbox_for_affected_consumers(events)
        return insert_command_receipt(command, events)
```

CAS 中检查支持集合版本而不仅是逐条证据版本，避免另一个事务刚加入反证却未被旧 Review 看见。Reviewer 的读取权限和 Commit 的写权限分别检查。重型内容审查不在事务中；验证过的 immutable artifacts 必须通过 pin/reachability 合同保护，避免事务前读取后被 GC。

---
## 8. 专项 B：世界状态与条件证据模型

### 8.1 对象与用途

保留现有 Claim/Knowledge；增加统一的 Fact/Justification/Validity 支持，不把所有日志自动提升为事实。

| 对象 | 不可省略的数据 | 语义 |
|---|---|---|
| PropositionKey | 谓词版本、类型化参数、对象版本、Mission/scope、时间范围 | 判断的对象；不靠自然语言相似度认定相同命题 |
| ObservationRecord | 观察值/极性、来源/工具回执、observed_at、recorded_at、有效区间、环境/方法版本 | 某时、某范围的真实观察；非绝对真理 |
| EvidenceRecord | 类型、准确目标/hash、执行与来源回执、来源群组、披露范围、可读取性 | 证据在哪里、怎样产生；hash 证明字节身份而非内容真实 |
| JustificationSet | conclusion、polarity、全部必须 premise 引用、条件、推理/Review receipt、规则版本 | 一组共同充分支持；多组之间可择一 |
| Claim/Knowledge | 现有身份/状态 + scope/assurance/justification refs | 当前能在哪些用途使用；不给所有候选伪造 VERIFIED |
| ValidityWitness | consumer、purpose、as_of、support_selection、read-set、scope epochs、not_after、阻断理由 | 本次是否可以使用，不是永久授权令牌 |

“来源确实写了 P”与“P 在现实中为真”必须是不同谓词。论文/网页/其他 Agent 的陈述先作为来源观察或候选；通过真实实验或语义审阅，只能得到相应范围的支持。

可参考 W3C PROV 的实体、活动、责任、派生关系表达可追溯性，但 PROV 不替代语义蕴含/真实性判定。[W1] 本系统可导出 PROV 映射，运行时不必改用 RDF 或图数据库。

### 8.2 四个维度，禁止挤成一个 VERIFIED 字段

1. **证据结论**：TRUE/FALSE/UNKNOWN/CONFLICT，描述当前合法证据支持。
2. **有效性**：CURRENT/STALE/REVOKED；是否满足当前时间与策略。
3. **保障范围 assurance**：来源核实、执行观察、测试覆盖、独立语义审阅、形式证明等能力标签与范围；不是单一可相加的分数。
4. **使用权与可用性**：当前主体能否读取/披露，原材料是否仍可用。权限不可用不等于命题为假。

UI 可显示受控的综合状态，但内部保持四个维度。低权限用户不得通过“有反证”提示推断隐藏资料；返回 POLICY_UNAVAILABLE 或获准的脱敏理由。

### 8.3 四态的确定性表示

对同一命题与 scope/time/use，分别维护正支持 t 与负支持 f：

| t | f | 结果 |
|---:|---:|---|
| 0 | 0 | UNKNOWN |
| 1 | 0 | TRUE |
| 0 | 1 | FALSE |
| 1 | 1 | CONFLICT |

这是本专项采用的证据合并表示，不是概率。反证存在不被正支持数量冲淡。未找到记录不产生 f=1；来源不可读也不能生成反证。

证据条件 AST 的 `not` 交换正负支持；`and` 的 t=所有子式 t、f=任一子式 f；`or` 的 t=任一子式 t、f=所有子式 f。空 and/or 在合同入口拒绝。安全/执行前提只在结果 TRUE 且用途有效时放行；CONFLICT/UNKNOWN 先处理证据。

条件式不能提供任意 eval、SQL、网络调用或 Python callable。只用注册的纯谓词解释器/类型化常量/有界量词；不支持特性明确拒绝。审阅阶段的“未执行”也不应被 not 转成通过。

## 9. 理由维护算法：多组支持、冲突与循环

### 9.1 受限但完整的支持表示

每个结论可有若干充分支持集合；集合内所有 premise 必须满足。这是“AND 组之间 OR”，不代表需要把任意公式指数级展开成 DNF。复杂公式保留 DAG AST，记录具体被采用的见证路径。

示例：`K ← (E1 AND A1) OR E2`。E1 被撤回时重新检查 E2；E2 是独立充分证据则 K 可以继续得到支持。E1、E2 若只是同一原始来源的两次转述，来源群组保持相同；不能据此满足“两个独立来源”的政策。

开放语义支持由独立 Review 提出范围化 entailment，系统验证身份和适用性；不得从 arbitrary Claim 文本自动生成一条不可反驳的事实规则。方法的 expected_effects 只存在预测状态，不能作为观察 anchor。

### 9.2 防循环自证，不只“发现 SCC 就认为错误”

经典 TMS 记录判断理由并在假设变化时修订信念；这里借鉴理由维护，不声称 TMS 能发现所有现实真假。[W2]

本实现使用**带极性的有限正规则 + 最小不动点**：

1. 根据当前 scope/purpose/as_of 选出合法、可回溯的外部观察/检查 anchor。
2. 所有派生支持先视为未证明，不沿用上次 TRUE 当新 anchor。
3. 按规则遍历，只有全部 premise 已有合格支持且规则/假设有效，才产生对应结论极性的支持。
4. 反向索引或 worklist 推进到不再有新支持。每个签名最多加入一次；超出部署计算限制返回 EVALUATION_INCOMPLETE 并阻止有关放行，不把它解释成 UNKNOWN 世界事实。
5. 正负支持分别得到闭包，组合成四态，并保存实际见证。

A←B、B←A 没有 anchor 时不获得支持。即使存在外部 E，A←(A AND E) 也不能自启动。A←E、B←A、A←B 则可以由 E 合法导出；E 删除后，重新从无 anchor 的 SCC 求值，不能循环保活。

规则不做基于“当前未推导出”而成立的默认否定（negation-as-failure）。需要声明的缺席必须由完整覆盖的权威查询产生独立负观察，或一个显式可撤销假设；高风险门不采信未验证假设。

### 9.3 历史血缘不等于当前可替换的支持

`was_used` 说明产物当时真的读了某份材料；`supports_for_use` 说明当前使用的理由。两者不能互相改写。

一份报告明确引用已撤回来源 E1，即使独立 E2 支持同一结论，也不能静默把原报告的引用换成 E2 后继续发送。应检查当前准则：若要求引用正确，则修订报告、产生新 hash、重新 Review/授权。仅对与 E1 无字节/语义绑定、且原准则允许的通用结论，可以通过新的有效性回执使用 E2；原接受记录仍引用当时依据。

新的充分支持也不能自动消除一份仍有效的反证。需要新的观察/审阅明确反证不适用或被撤回，四态才离开 CONFLICT。

## 10. 失效传播：不能出现“后台尚未更新所以继续用旧知识”的窗口

### 10.1 反向支持索引

扩展 `source_dependencies.py` 的来源适配，建立 typed reverse-use：

`Evidence/Observation → Justification → Claim/Fact → MethodApplicability → InputBinding → Acceptance/GoalResolution → Summary/ContextSelection`。

传播时使用 DATA/SUPPORT 和带 guard 的 ORDER 关系。纯 ORDER 已发生的先后事实不必因内容过期撤销；它引用的权限/验收 guard 仍需复查。不能只沿 Task dependency 递归重做全部后代。

### 10.2 同步安全屏障 + 可异步内容重算

来源/权限/支持变化的同一事务：写变更事实、增加相关 scope 的 `validity_epoch`、登记 durable dirty work、写 Outbox。异步任务随后重算受影响闭包。

任何 Scheduler/Reviewer/Context/ActionGateway 要使用缓存时，必须验证 witness.epoch 与当前相关 scope epoch，并检查 not_after。过旧时：

- 在界限内精确重算自身依赖并记录新 witness；或
- 返回 VALIDITY_RECHECK_PENDING，等待重算。

不能因后台索引尚未更新就继续使用旧 TRUE。这是安全屏障，不是全局作废：无关消费者重新核对未变化的局部依赖后可继续，不重做其工作。

read-set 同时记录相关节点 revision、支持集合 revision/成员 digest 和scope epoch，避免并发新增反证没有改动原正证据而绕过检查。时间跨过 not_after 时即使定时事件未及时跑，真实 handoff 仍检查当前时间；Replay 用事件携带的 as_of，不读取今天时钟重写过去。

### 10.3 不过度重跑

消费者重新求值结果分为：UNCHANGED、REBOUND_SUPPORT、NEEDS_REVIEW、INVALID、UNAVAILABLE。

UNCHANGED：现有材料仍符合，不创建新 Attempt。REBOUND_SUPPORT：允许新用途改选支持时保存新 receipt；有实际输入/引用变更则不能走此捷径。NEEDS_REVIEW：事实未必错，但没有当前足够依据。INVALID：关键前提被反证/要求不符。UNAVAILABLE：权限/材料不可读，不断言内容错误。

### 10.4 前提的时间语义

START：在交接边界有见证即可，例如执行前库存充足。正常执行消耗库存不倒推该操作从未合法。

MAINTAIN：执行中需持续保持；无法监控/隔离保证的长时操作，不得声称获得连续保证。合同必须给出监控间隔、失效响应和实际保证范围。

ACCEPT：验收/实际使用时必须重新确认，例如当前配置匹配或当前来源合法。

HISTORICAL_AS_OF 的证据可在历史查询中继续描述当时状态；CURRENT_AT_USE 使用必须检查新鲜度。授权与披露始终按当前政策，历史分析不豁免权限。

## 11. 与 TaskGraph、HTN、Context 的握手

- HTN applicability 调用 `evaluate_validity(purpose=PLAN/START,...)`，取得条件值、证据和 read-set；不自己维护另一份世界状态。
- compiler 把必需的支持/输入绑定纳入 PlanDelta 和执行需求。
- `evaluate_readiness` 读取当前 purpose 的 witness；只有有效 primitive 获得候选资格，交接时仍复核。
- ReviewPackage 引用 exact inputs/accepted children，不能根据“最新结果”漂移。
- Task.COMPLETE 只表示历史状态；DATA 放行还检查相应 Acceptance/GoalResolution 的当前用途有效性。
- 失效服务只产生状态事实和修复触发，不自行改写 TaskNetwork；由 Manager/HTN 提 Proposal，唯一 Commit 正式切换。
- Context 编译保留要求、当前方法、关键假设、操作未知项和必要读集；语义检索仅补材料。已冻结请求不重组；如果披露权限已撤销，禁止继续发送该旧请求而不是给同一身份偷换 prompt。
- 取证用的观察文本和工具返回始终作为数据；有效性更高不自动提高指令优先级或工具权限。

---

## 12. 专项 C：操作身份与参数不变式

### 12.1 区分业务意图、命令、交接和效果

```text
用户/受批准目标 occurrence
       ↓
OperationId + immutable semantic envelope
       ↓
Action/DispatchIntent（既有编排授权与意图）
       ↓
SDK Effect / Connector HandoffId（真实交接）
       ↓
Service receipt / lookup evidence（实际后果）
```

命令重投 ID 只去重该条命令，不代替 OperationId。一个 OperationId 可对应多次被批准的相同幂等重交接，所有物理尝试和费用仍可追溯。

现有 `business_action_id(mission, connector, operation, target)` 适合早期一次状态修改合同，但会把同 Mission 中同对象的多个合法 occurrence 合到一起。新模式为每个明确现实意图分配 `operation_occurrence_id`，例如“给同一联系人发送初稿”和“获批后发送修订稿”是两个意图；不要仅用目标文字、参数 hash 或新 Attempt 判断。

### 12.2 OperationEnvelope

不可变语义字段：operation_id、occurrence_id、mission/obligation/scope、connector/adapter version、operation kind、规范化目标身份、参数/引用 hash、目标期望版本/前置条件、效果合同、请求 hash、来源/Acceptance 与 Review 绑定、要求版本。

current 控制字段另表：授权状态、授权 epoch、调度 generation、当前 outcome、在途 handoff、budget refs、下一次核对时间。控制重试不能修改不可变语义 envelope。

- 同 OperationId + 同 envelope：重试/查询原身份。
- 同 OperationId + 不同 envelope：OPERATION_PAYLOAD_CONFLICT，不执行。
- 发送前确实更改内容：明确 supersede 原操作，确认无潜在交接，再为新冻结内容建立新 Operation；重新审阅/授权。
- 已 HANDED_OFF/UNKNOWN：不得用 supersede 绕过核对。新意图需要等待/协调未决影响或明确风险决定，不能并发重做假装替代。
- 相同参数、不同用户明确意图/周期 occurrence：不同 OperationId，仍分别授权。

旧 action ID 不重算。新 sidecar 只绑定现有 action/effect 记录；action ledger 是命令/批准权威，execution/connector ledger 是物理事实权威，不创建第二份独立可写事实账本。

### 12.3 幂等键寿命

连接器声明 key namespace、账户/环境范围、保留时长、参数冲突行为、并发去重语义、是否能冻结旧请求。系统的 OperationId 不过期，不代表服务端 dedupe 记录永不过期。超过可证期限后禁止自动重交接；先核对或人工。

服务端幂等不由 HTTP method 名、参数相等或 API 返回成功推断。AWS Builders' Library 强調显式客户端意图 ID、同 ID 不同参数冲突和迟到请求问题，适用于本合同的设计依据。[W3]

## 13. 操作状态：控制、后果、记账分开

新模式的操作视图分三条轴，不强改旧 actions.state：

**control**：PROPOSED / AWAITING_AUTHORIZATION / READY / DISPATCHING / QUIESCING / CLOSED。

**effect_outcome**：NOT_HANDED_OFF / PENDING / APPLIED / NOT_APPLIED / PARTIAL / UNKNOWN。

**accounting**：UNRESERVED / RESERVED / PARTIALLY_SETTLED / SETTLED / USAGE_UNKNOWN。

| 事件 | 控制结果 | 后果含义 |
|---|---|---|
| 候选批准 | READY | 还未证明外部执行 |
| durable handoff 已记，准备发网络 | DISPATCHING | PENDING；崩溃后可能 UNKNOWN |
| 可信回执确认提交目标效果 | 可进入 CLOSED | APPLIED；还需按用户准则验收 |
| 权威证据证明未开始/无效果且不会迟到执行 | READY 或 CLOSED | NOT_APPLIED |
| 网络错误、超时、本地进程丢失 | QUIESCING | UNKNOWN，而非 NOT_APPLIED |
| 有可验证部分效果 | QUIESCING | PARTIAL，生成剩余/补偿责任 |
| 取消且从未交接 | CLOSED | NOT_HANDED_OFF |
| 取消但已交接 | QUIESCING | 保留 PENDING/UNKNOWN/APPLIED 等真实后果 |

APPLIED 之后可能外部状态再被别人改变；这是新观察，不把原“曾执行”改成 NOT_APPLIED。补偿成功也不删除原 APPLIED。原操作与补偿分别记录，另有 compensated_by/剩余影响视图。

“service 接受了邮件请求”“provider 已发送”“收件方已送达”是不同完成里程碑，效果合同必须规定要求哪个；不能把 HTTP 200 自动映射到用户要求的最终效果。

## 14. 交接与核对协议

### 14.1 真实 handoff 的三段流程

**A. 编排授权事务**：检查 current plan/demand、要求与输入、Review/Acceptance、操作合同、审批、authority epoch、预算和资源冲突；保存 action/outbox/稳定 ExecutionBinding。

**B. 执行准入**：执行库原子去重绑定，冻结请求，取得有效 execution fence，验证来源引用。再次检查短期授权/相关有效性/目标条件，记录 HANDED_OFF 后才允许网络或 OS 操作。两库不是一个事务，使用握手和稳定回执；重试仍是同一 binding。

**C. 结果记录**：先由真实执行权威保存 outcome/receipt/费用，再由编排 inbox 导入并更新业务投影。已过期 Worker 的结果可以进入历史证据/记账入口，但不能获得新的业务修改权。

取消/撤权与 handoff 必须在受控网关有可排序边界。新 epoch 可阻止未被网关准入的旧请求；已经获准发到外部系统的请求不能靠数据库 epoch 召回。强撤销保证需要服务端栅栏/取消确认；不支持则只承诺“阻止后续新交接并核对已有请求”。

### 14.2 外部 TOCTOU

本地事务检查“库存/目标版本正确”后，外部仍可能变化。对需要前置条件的修改，使用远端条件写/事务/受保护的单写者机制，例如 ETag/If-Match 的语义。[W4] 不支持原子条件检查又存在其他写者的高风险修改应拒绝自动执行或人工处理，不用普通先查后写冒充一致性。

### 14.3 Lookup 结果的严格合同

`ReconciliationResult`：APPLIED、NOT_APPLIED_FINAL、PENDING、PARTIAL、UNKNOWN，附真实 service namespace/目标/request hash、查询水位或一致性说明、观察时刻和证据回执。

**空结果不必然是 NOT_APPLIED_FINAL。**请求可能还在网络中、服务队列中、异步处理，或记录超过保留期。只有下列情况之一才允许重交接：

1. 原请求已确认无法再应用（服务端取消/截止/栅栏，或可靠的执行停止及请求无在途证据），且权威服务证明未应用；重新检查当前授权后仍用原操作身份；或
2. 原请求是否到达仍未知，但服务端对同一 key 的原子去重、不可变参数与有效保留期可证明成立，且当前意图仍然获准；允许受政策约束的同 key 重交接，不称为“已证明未开始”。

保留当前 L2/L3 对 authoritative connector 的严格门；上述能力不足时进入人工/核对，不降低旧安全底线。策略默认限制物理重试次数与退避，不能无限重发。

### 14.4 连接器能力矩阵

| 能力组合 | 默认行为 |
|---|---|
| 原子去重 + 权威结果 + 明确保留窗口 + 可校验请求身份 | 相同意图、当前授权内允许有限同 key 重交接 |
| 唯一业务键/条件创建 + 权威查询，能证明等价去重 | 注册专门适配并测试后采用相同保证 |
| 只有状态式接口，重复写相同值貌似幂等 | 不据此自动重试：可能覆盖别人后来修改的值/重复通知/审计 |
| best-effort 查询、最终一致查询或无 key | UNKNOWN 保持；不用于高风险自动重放 |
| 只读查询 | 可有界重新观察；每次观察有独立时间/结果，不能伪装为过去回执 |

SDK/HTTP 客户端隐藏自动重试也需检查。带副作用的请求未满足同 key 保证时禁用自动重试；所有物理调用消耗要可追溯。

## 15. 跨库、Outbox 与事件重建

### 15.1 双写问题的具体处理

`orchestrator.db` 中：业务事件、当前投影、outbox、command receipt 同事务。

`execution.db` 中：已有 invocation/effect、执行检查点、真实回执按当前内核事务管理。

Outbox 可重复交付；接收方 inbox 使用稳定消息 ID 和 payload hash 原子去重。ack 丢失导致重送，不产生新 Operation。这里借鉴 Transactional Outbox 模式；它解决数据库更新与消息发布的一致关系，不免除消费者去重。[W5]

不要同步写两库后说“都 commit 了所以原子”。每条跨库消息必须有：logical id、producer authority、immutable payload hash、对应 Operation/Intent/Turn、producer commit watermarks、receipt与消费回执。不能把普通日志转换为可信执行事实。

### 15.2 每种崩溃切点

| 切点 | 正确恢复 |
|---|---|
| 编排 Commit 前 | 没有正式工作；相同命令可再提交 |
| Commit 后、dispatch 前 | 重送原 Outbox，同身份 |
| execution 已接收、编排未记 ack | 查询或重复发送同意图，返回原 receipt |
| HANDED_OFF 已记、网络调用前 | 保守 UNKNOWN；经正式查询/安全去重恢复，不假定未发送 |
| 外部已应用、本地没收到 | 查询原 key/业务身份，不能创建新操作 |
| execution 保存结果、编排未消费 | 导入原 receipt，不重新运行 Worker |
| Review 已保存、accept 未提交 | 当前性检查后接纳原 Review 或标过期，不重造模型请求 |
| accept 提交、UI没收到 | 相同命令查原回执/事件游标，不重复接受/发通知 |
| 费用晚到、Mission 已终态 | 原账户导入事实；不复活权限、不重跑内容 |

### 15.3 事件与投影边界

新模式纯 reducer 重建本专项覆盖的要求、Review、Acceptance、Validity、操作关联、控制状态、等待与通知义务。物理事实引用 execution/connector 的权威记录；不可变引用必须被保留策略保护。

**关键未知事件必须停止该流推进**，不能忽略后继续声称重建完整。Schema/codec/reducer 版本固定，旧事件不变；缺失历史通过注明范围的 GenesisSnapshot 迁移，不伪造当时发生的事件。大型正文存 CAS，可删除敏感内容并留下 tombstone，但诚实说明不能逐字复验的范围。

SQLite 支持同库事务与隔离，写者仍由应用的短事务和逻辑校验组织；不要在持有写锁期间执行模型/求解器/远端查询。[W6]

## 16. 取消、补偿、关闭与长期移交

### 16.1 取消不是结果

用户取消产生 CancelRequested，停止新业务派发和未获准新动作；继续接收已发生结果、费用及获准只读取证。待处理 UNKNOWN 不能被 CANCELLED 覆盖。

能够立即关闭：从未交接，或已确定无未决后果。已经存在 UNKNOWN/PARTIAL：保持取消中的义务，或事务化移交 `RecoveryObligation`，指定有效 owner、下一次核对/报警、资源来源、事件和用户通知。仅写一个 orphan 标志不算移交。

正式 Mission 终态可以在符合政策且恢复责任已成功接管后产生，但不能宣称相关外部目标成功；长期主 Agent/核对队列仍能发现责任。禁止依赖模型记住“下次再查”。

### 16.2 补偿

补偿是一个新的 OperationId，绑定原操作和补偿目标、权限、当前目标版本及预算，经过所需 Review/审批。补偿可能无法还原原世界、可能不是反向顺序、也可能失败，应有自己的恢复与幂等。[W7]

例如把配置从 A 改成 B 后，别人又改成 C，补偿不能无条件写回 A。需要匹配当前合法版本/其他主体影响，无法安全补偿则报告人工处理。发送敏感邮件通常没有可靠逆操作；后续说明/撤销尝试不能声称删除已经发生的披露。

### 16.3 费用

复用 `accounting_recovery.py` 的原调用/原价格身份核对。消费来源为实际 invocation/effect/connector receipt；ResultEnvelope 自报只是线索，不能决定账本。

同一逻辑 invocation 的物理重交接若产生多次费用，按现有权威事实表达全部实际消耗，不能用一次业务幂等隐去费用。UNKNOWN 费用继续持有相应 reservation；已知超出预留必须真实记账、报警并阻止不合法新调用，不截断到预算制造“没有超支”。

Task/Obligation 上限是责任额度，单请求 reserve 是运行准入，验证保护尾额是尚未转出的份额；不得跨层重复统计。修复、换角色、换方法不清零累计责任。低预算时明确拒绝或请求合法调整，不默默取消必需评审。

## 17. 完整恢复协议

1. **RECOVERY_LOCKED**：阻止新业务副作用；认证并取得新的 recovery scope，不复活旧 token/lease。
2. 核对 DB schema、checkpoint、事件水位、CAS/source/connector 账本 manifest；应用当前删除清单与访问策略。
3. 对可重建投影执行版本化 reducer；缺件/哈希错/未知必需事件隔离该流，不能靠重跑补丢失历史。
4. 恢复 durable inbox/outbox/review queue，收取已持久的真实结果与费用。
5. 对 HANDED_OFF/UNKNOWN/PARTIAL 先执行获准的核对；新复核请求有自身审计身份，不产生原业务新副作用。
6. 核对/收敛原执行进程与栅栏，重新计算当前 validity/readiness，原冻结请求保持原字节。
7. 回收孤儿需要 execution identity/boot identity/受保护标签，不能凭旧 PID 杀进程。不能确认则保持有关资源禁用。
8. 回报明确的 READY 或 DEGRADED_RECOVERY 状态；只为已经核对可继续的 scope 开放执行。

备份必须覆盖 orchestrator/execution/CAS/source/connector ledger/政策与恢复密钥材料，并通过阻止新 handoff、记录屏障和未决列表形成一致 manifest。不要求停止外部世界；在途请求仍需恢复后核对。影子重放不调用真实工具，不把数据库回到旧时间当成现实也回到旧时间。

## 18. 代码实施落点与存储约束

### 18.1 模块对应，不把全部逻辑加到 event_handler

以下路径均相对于 SDK `src/agent_orchestrator/`。新路径是建议；若当前已有同义类型/表须复用，不并存两份权威。

| 路径 | 现状/动作 | 具体实现 |
|---|---|---|
| `contracts/resolution.py`、`contracts/evidence_state.py` | 总计划拟新增/扩展 | Requirements、Review、Acceptance、Truth/Validity、OperationEnvelope；严格 codec、名义 ID |
| `verification/verifier_router.py` | 已读，复用 | 继续实际检查；明确 ReviewPurpose 与 package binding；系统错误不伪装语义否定 |
| `verification/assessments.py` | 已由主入口导入；实施需定位完整方法 | 复用原 CriterionAssessmentV1；新模式记录 schema/purpose/结果版本与检查覆盖，不改旧序列化 |
| `verification/mission_coverage.py` | 已读，扩展 | 新语义按显式成功表达式/硬约束/组合 Coverage 求值；保留旧文档域规则 |
| `verification/review_coordinator.py` | 拟新增 | 构建审阅包、受控取证、冻结证据集合、回收结果、升级/停止 |
| `verification/acceptance_rules.py` | 拟新增 | 纯 success_expr、审阅完整性、必需证据、独立性和受支持范围检查 |
| `memory/source_dependencies.py` | 已读，扩展 | 作为来源血缘 provider，连接统一证据反向索引；历史源版本不覆盖 |
| `memory/justifications.py` | 拟新增 | 有类型支持集、极性、规则和最小不动点，循环与资源限制 |
| `memory/validity.py` | 拟新增 | purpose/time/scope 求值、ValidityWitness、缓存上界 |
| `memory/validity_projection.py` | 拟新增 | epoch 屏障、dirty queue、局部重算与显式错误 |
| `orchestrator/resolution_commits.py` | 总计划拟新增 | review 接纳、根 Resolution、carry-forward、版本/读集与 Outbox 原子提交 |
| `orchestrator/evidence_commits.py` | 拟新增 | 观察/理由的准入、撤回、更正、epoch/dirty/事件同事务 |
| `orchestrator/action_commits.py` | 已读，复用并扩展 | 现有审批动作账本；新 mode 绑定 occurrence/envelope/支持见证；不再仅按同目标自动合并多次操作 |
| `runtime/connectors.py` | 已读，分离公共合同与平台实现 | 声明具体幂等/查询/retention/条件写能力；类型化 reconciliation；不削弱现有 L2/L3 authoritative 门 |
| `runtime/operation_protocol.py` | 拟新增 | 执行 binding、retry 决策、outcome 映射；调用既有 action executor，不另发真实操作 |
| `orchestrator/accounting_recovery.py` | 已读，保留 | 晚到费用按原身份/价格导入；新绑定在此显式兼容，不能因为终态/失效跳过 |
| `orchestrator/recovery_coordinator.py` | 拟新增薄协调 | 启动屏障、导入、核对、重新授权、恢复责任移交；复用现有 runtime recovery |
| `orchestrator/event_handler.py`、`commit_service.py` | 已有主链；前者本轮未全读 | 新语义路由到上述服务，不绕开旧预算/派发/结果机制；所有消费者须做新旧模式检查 |
| `graph/readiness.py`/`graph/eligibility.py` | TaskGraph 草案有两种拟议命名 | 实施只保留一份 authoritative eligibility 模块；接入 validity、Operation 冲突和 accepted inputs，不复制规则 |
| `context/*`、`artifacts/*` | 依现有总计划接口 | 记录 exact references/manifest，过滤不可用知识，保持冻结请求，GC pin/copy/不可变读 |
| `storage/schema.py`、`store.py`、`observability/replay.py` | 扩展现有体系 | 下一未占用 migration、完整事件、纯投影、inbox/outbox、恢复索引 |
| Host API/projection/TS store/UI | H0 待实际源码定位 | 呈现接受范围、证据来源/有效性、授权、真实执行结果、UNKNOWN 和后续责任；不推断正式状态 |

不要求换 TypeScript。公开通信 Schema 由现有 Host 契约事实源生成 Python/TS；本包 internal schema 是新内部对象的草案，不能自动暴露给 UI。密钥/fence token 不传模型和前端。

### 18.2 数据表的最小职责

复用已有 requirements/task/method、actions/approvals、dispatch_intents、events、budget 等表；缺少等价语义时新增：

| 逻辑记录 | 必须的键/约束 |
|---|---|
| review_packages/review_records | package/result immutable hash；同 review command 只一份正式接纳；subject 属于对应 Mission |
| criterion_evaluations | `(review_id,criterion_id)` 唯一；check receipt 被目标/输入/policy hash 绑定 |
| acceptances/goal_resolutions | 不可变接受内容；结果重复提交返回原 receipt；当前采用关系单独记录 |
| validity_witnesses | `(consumer_ref,purpose,scope)`、支持 revision/epoch、as_of/not_after |
| observations/justification_sets/support_members | 外部证据/规则/参数/极性与真实引用；倒排索引和成员集 revision |
| validity_dirty/epochs | 变化与 epoch/dirty 同事务，读侧落后 fail closed |
| operation_bindings | `(principal,scope,operation_occurrence_id)` 唯一绑定冻结 envelope；OperationId 不允许不同 request hash |
| execution_bindings | operation→existing action/effect→handoff/adapter epoch 稳定链；禁止多个互相竞争执行权威 |
| inbox/consumer_receipts/outbox | producer/message id+payload hash；同 ID 不同 payload 冲突，ack 可重放 |
| recovery_obligations | owner/next_due/状态/资源来源/移交 ack；不因为 Mission 终态失去发现入口 |

SQLite 外键在每个连接启用；所有判定涉及余额/成员/当前状态在同一写事务/证书 CAS 检查。字段 hash 采用已冻结 canonical codec；不要改成新的序列化规则重算旧身份。

### 18.3 关键性能与锁边界

关联查询/支持图纯算法读取冻结快照，必要时事务外预计算；短事务验证读集。大支持图设置有界求值成本，超出返回待复核，不声称假/无解。epoch 可先使用 Mission scope（安全但可能重查多），后来按 scope 分区也须保持同一合同，不要求上微服务/图数据库。

预算和 execution 锁次序沿用已有协议；新核对不要在持有 orchestrator 写锁时阻塞远端服务。观察、计算、提交三个阶段分离，每次跨阶段以稳定身份/读集衔接。

## 19. 实施顺序：四个完整闭环，不改 P1—P9

### AER-A：指定成果的真实独立验收

对应 P1/P2/P6：建立 Requirements/Criterion、ReviewPackage/Record、Acceptance、根成功表达式及 Commit；复用代码/文档真实检查，接通一次简单 Mission 与一个 compound 组合场景。必须演示无关测试、漏要求、伪 PASS、旧输入、Reviewer 修改产物被拒绝；不仅创建 DTO。

### AER-B：证据变化后的安全继续

对应 P1/P3/P5：来源适配接入 Justification/Validity，四态/最小不动点/epoch barrier；一项证据撤回后正确保留独立支持，相关报告需要修订，无关分支继续。Scheduler、Context、Review、ActionGateway 同时消费 witness；不能先只更新 UI。

### AER-C：获准动作与崩溃后的真实结果

对应 P1/P6/P7：新 occurrence/envelope 接入既有 action/effect，连接器能力声明、同 key 有界重试、条件写、核对、取消/补偿和晚到费用。用受控服务真实注入“已应用但丢回执”“旧请求延迟到达”“dedupe 过期”，证明行为，不开放未验证生产连接器。

### AER-D：贯穿 HTN/TaskGraph 的完整交付与恢复

对应 P2/P3/P6/P7/P9：动态换方法保留已验收成果、来源换版、批准内容变化、真实动作 UNKNOWN、根 Resolution、通知和进程重启全链。Host H0 后接真实 UI；不以 fixture 或 CLI 成功替代原生接线验证。

四个闭环属于同一设计，每一个从合同、状态、执行到验收都完整。严谨地分步施工，不表示省去另一半失败/恢复语义。

## 20. 贯穿场景：方案报告、修订、批准与交付

用户要求比较两份资料，在指定环境核查关键主张；报告列出无法验证项；获批后发送给指定测试收件对象。

1. 保存 Requirements r1：隐私硬约束、输入范围、证据要求和发送完成标准（例如“服务确认提交到 outbox”，不擅自说用户已阅读）。
2. HTN 选方法；TaskGraph DATA 把两份资料版本、实验输入绑定给 Worker。
3. Worker 产出报告 H1，独立 Verifier 逐准则检查。来源只声称 P 时报告必须写“来源声称”，不能写“已实测”。
4. Commit Acceptance(H1)，不完成要求发送的根 Mission。
5. 用户更新一份来源；证据 epoch 提升，原 Acceptance 当前用途需复核。无关实验结果保留，受影响结论/报告产生 H2 和新 Review。
6. 审阅并批准对 H2 的操作 O1；原 H1 的审批不能使用。发送网关复核当前合同/有效性/审批/request hash。
7. 服务应用 O1，主机丢回执退出。重启回到核对状态，查原 key。确认应用后记录原效果；不生成 O2。
8. 核对后的 outcome 与发送完成标准匹配，生成/接纳根 Review 与 GoalResolution。UI 显示证据、局限、已确认的具体交付阶段。
9. 若无法核对，保持 UNKNOWN 或事务化交给恢复责任；可以报告“报告已验收，发送状态待确认”，不能显示整体成功。
10. 同一用户随后明确要求再发修订版，建立新的 operation occurrence；不能被旧的 Mission+target 签名吞掉，也不能改 O1 的原 envelope。

可先用自有受控投递测试服务复现；这证明本协议在该适配下成立，不宣称普通邮箱、任意 SaaS 均具有同样保证。

## 21. 验收、参考代码与证据边界

### 21.1 四组工程验收

本包 `acceptance-scenarios.json` 含 48 项**待实施**场景：目标验收 12、证据有效性 12、执行恢复 12、跨模块 12。每项有 setup/trigger/oracle/evidence 和原 R 编号。

必须分别验证：纯语义规则、真实 SQLite 事务/竞争、真实进程杀停与重启、受控连接器故障、真实模型与 Host。假响应可测协议，不能证明真实模型会正确审阅；进程内异常也不能替代进程级故障切点。

跨动作属性测试应使用 stateful/property-based 测试，生成交错序列并与全量参考求值对照；Hypothesis 支持生成整个动作序列与缩小失败反例。[W8] 暂不指定生产库版本，实施固定项目依赖后执行，不安装未知 latest 作为正式证据。

### 21.2 参考程序的精确范围

`reference/protocol_rules.py` 演示：成功表达式三值求值、显式硬门、带极性理由的最小不动点、上下文已证明条件的简单使用门、重交接决策。它只接受已完成身份/权限/来源核对的抽象输入，**不实现**实际授权、数据库、TMS 增量维护、完整参数 AST、连接器、预算或 HTN。测试通过不构成三大专项的完成声明。

`schemas/` 是本专项新内部消息的结构草案；它不能验证引用存在、真实角色独立、语义蕴含、远端幂等或执行效果。旧 goal-resolution schema 不修改。生成类型后仍需 codec 和事务检查。

### 21.3 效果指标与零容忍的区别

机械安全门测试中的越权、重复不允许副作用、错误复活旧执行、吞掉真实费用、静默漏要求必须阻断发布该能力。模型内容质量则报告误报完成率、证据范围错误、INCONCLUSIVE 校准、局限覆盖、每成功任务成本和延迟；不承诺 100% 开放语义正确。

保留现有强单 Agent/静态/动态基线，不向内部 Verifier 暴露官方评测答案。新增协议通过不等于多 Agent 已证明优于基线。SDK 新文档指针提到 A96 成绩，但原始收据在 Host 未取得，本包不将其作为独立实验结论。[S0]

## 22. 参考依据与能力边界

### 22.1 内部依据

D1《Agent 编排层完整设计方案》：Mission/Task/Claim、独立验证、Proposal/Commit、状态与事件、幂等、预算和审批。  
D2《SimpleHarness 完整目标架构与差距闭合计划》（2026-09-16，FULL-TARGET-1.0）：60 项要求与 P1—P9。  
D3《SimpleHarness TaskGraph：完整语义、动态修改与执行连接设计》（TG-DESIGN-1.0）：Task.form、ORDER/DATA、InputManifest、版本/读集、GoalResolution。

本包新增操作/失效/审阅细则明确作为实施设计，不把草案当原代码。多份早期文件的 GoalNode/GoalTaskBinding 不再引入独立生命周期，统一使用最新 Task/Obligation 语义。

### 22.2 一手技术依据

- W1 W3C PROV-DM（2013 Recommendation）：实体、活动、派生与责任的血缘模型；不提供真实性裁判。https://www.w3.org/TR/prov-dm/
- W2 Jon Doyle, A Truth Maintenance System（MIT, 1979）：记录理由与修订信念的思想；本包的安全四态/单调规则/用途过滤是项目实现决定。https://dspace.mit.edu/entities/publication/5377b306-4ecc-4687-b1f5-78cbb4a0543a
- W3 AWS Builders' Library, Making retries safe with idempotent APIs：显式意图身份、参数冲突、迟到请求。https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/
- W4 RFC 9110 HTTP Semantics，条件请求及 If-Match：只在实际连接器支持并遵守时使用；不为任意 API 自动提供原子检查。https://www.rfc-editor.org/rfc/rfc9110.html
- W5 AWS Transactional Outbox Pattern：同库更新/消息的一致性以及消费者去重。https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html
- W6 SQLite Isolation：写入、快照和 BEGIN IMMEDIATE 的边界。https://www.sqlite.org/isolation.html
- W7 Microsoft Compensating Transaction：补偿与数据库回滚不同，可以失败且需要业务定义。https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction
- W8 Hypothesis Stateful Tests：生成和缩小动作序列。https://hypothesis.readthedocs.io/en/latest/stateful.html
- W9 Lean Validating Proofs：目标语义、内核、公理依赖与隔离的区别；固定实际工具链并审核完整公理集，不能只过滤一个名字。https://lean-lang.org/doc/reference/latest/ValidatingProofs/

外部资料提供机制依据，不使本项目自动拥有形式化完备性或全局 exactly-once。上述命令/协议需要工程实现、威胁模型与环境验收才成立。

**最终闭环：要求规定完成标准；独立审阅为指定成果提供判断；有效性服务决定它当前还能怎样用；执行账本说明现实发生了什么；Commit 只在这些事实相容时改变正式状态。**
