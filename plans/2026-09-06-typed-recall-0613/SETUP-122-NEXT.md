# 原401 setup122：公开构造性调查

2026-09-06，只读代码/既有证据；未运行测试、SDK、模型或native，未占测试槽。
保留626/fbeb public历史与659/bcc source10边界，不更新任何格的结果。
runner现场HEAD5819b15e（主已FF后继续前进）；Memory固定f2a6a706；Harness固定build源0282fa98。
所读Harness四协议文件与0282fa98比较无差异。没有修改SDK、原fixture、阈值或旧applicability WIP。

## 结论与原义务

优先补 **Procedure公开applicability setup + 条件型eligibility oracle**，不需要新SDK端口。
verified两组不能一概归为“缺trusted receipt”：当前32个早期拒绝均涉及SDK明确禁止的epistemic组合。
本次没有证明SDK产品API缺陷；合法链尚需后续installed验证，不能预报PASS。

冻结来源 `testcase/human-memory-program/fixtures/typed-recall-v3.json:946` 要求4×5×5全部组合；
未列出组合INELIGIBLE，具名行还包括 `ELIGIBLE_WITH_APPLICABILITY` / `ELIGIBLE_WITH_TRIGGER_SIGNAL`（1097起）。
`runners/TYPED-RECALL-PUBLIC-LOOP-BATCH.md:8` 明确禁止改epistemic来使setup通过；
explicit_user verified保留原USER并另加独立TOOL验证证据，不可升级原quote。
当前 `typed_recall_normal_inputs.py:25` 给epistemic统一默认active（prospective pending），
这些默认状态是runner选择；不能把它造成的拒绝直接归为SDK回归。

## 122原始互斥计数（非新的执行结果）

| 原原因 | 格数 | 当前判定 |
|---|---:|---|
| verified缺typed evidence | 16 | llm_inference/unknown各4类型×2verified值；即使补typed仍被M禁止，非普通适配遗漏 |
| verified_external非source_verified | 16 | 4类型×其余4值；与公开DTO/写入契约冲突 |
| inference/unknown不能authoritative | 12+12 | 默认active/pending错误；非unverified还违反verification限制，见下文 |
| observed Procedure不能直接active | 6 | create路径不合法；其中原repeated_observation正例可用真实观察晋升路径 |
| applicability或signal未建 | 30 | Procedure11、Prospective19；不能全算Procedure，更不能全算action grant缺失 |
| 同上且projection证明缺失 | 2 | 各一Procedure/Prospective，补authority不自动解决projection |
| Procedure eligible枚举无效 | 1 | 旧语义名需显式映射，不可删格 |
| AUDIT上下文非法 | 24 | 独立disclosure构造性问题，本叶不假称已补 |
| 128byte page / projection其余 | 1+2 | 本次范围外，原门保留 |

计数来自原ignored `typed-recall-0613-formal/cell-classification.json`，合计122；不是新401结果。

## verified / verified_external

公开链已存在：Harness `runtime/evidence_protocol.py:1605,1940` 的
`TypedObservationAuthorityReceipt`、`EvidenceAuthorityVerifierPort.resolve_admitted_evidence/resolve_typed_observation`
与 `verify_evidence_span`；proposal只是ref，真正receipt由可信fixture authority返回，验证exact
envelope/admission/item/pointer/value hash。Memory `MemoryManager.build_human_memory_v7(evidence_authority=...)`
→ `ingest_committed_evidence` → `apply_memory_mutation_plan` 即可走真实公开写入。
receipt不是SDK自动替外部事实验真；fixture issuer必须先持有独立输入/schema及admission，不能从recall输出反造。

runner `adapters/typed_recall_fixture_authorities.py:71` 已实现合法合成TOOL/EXTERNAL发行；
`typed_recall_case_manager.py:100,168` 已为explicit_user verified保留双span，verified_external/source_verified走EXTERNAL。
无需再复制SDK testing helper，也无需SQL。保持package-root DTO + runner-owned issuer，读取实际public结果判断。

禁止组合的第二道约束：M `core/mutations.py:440–555`：
verified_external只许source_verified；inference/unknown只许candidate/draft且unverified。
H `runtime/memory_protocol.py:2631–2647` 更早拒绝部分组合。因此16+16不能补receipt后称通过。
12+12中unverified的4+4可研究合法candidate/draft输入路径（episode/semantic/prospective candidate、Procedure draft），
但会引入lifecycle共同拒绝因子；必须保留原epistemic并报告实际拒绝原因，不能声称独立验证原epistemic gate。
其余verification禁止组合不存在保持同值的公开持久记录构造路径。

最小处理：保留原格/预期，另记录 `construction_conflict`、requested输入、实际拒绝层/原因；
原recall eligibility仍未验证。若未来决定以ingress拒绝覆盖原负向目的，必须显式修订验收解释；本调查不代作该决定。

## Procedure最小合法实现路径

1. 新runner-owned公开适配leaf，给CaseManager注入 `procedure_observation_authority`，实现
   `resolve_procedure_observation_authority`；不要复制原未跟踪applicability模块。
2. 用原payload公开CREATE explicit_user active；以实际mutation返回target/revision为准。
   构造 `ProcedureApplicabilityContext(tool_id,environment,tool_version,input_schema_hash)`，保留原tool/version，
   environment/schema取明确独立fixture输入，不能猜fingerprint或仅传payload的tool@version。
3. 先通过公开conversation registration登记真实合成fixture的同subject/task_scope/run证据，再发行
   `ProcedureObservationIntent(kind=APPLICABILITY_SNAPSHOT, transition_from=active, transition_to=active, ...)`，
   `issue_procedure_observation_authority` → ref → `MemoryManager.record_procedure_observation`。
   H `runtime/procedure_observation_protocol.py:96,149,495`；M `core/manager.py:1280`。
   M `backends/sqlite_v5.py:9694` 明确要求已登记的同task/run来源，只有内存里的admitted span不够。
   snapshot无terminal outcome，不能自造terminal receipt来补齐。
4. 将该实际applicability fingerprint送RecallContext；M `sqlite_v5.py:4890–4928` 只接受已持久绑定且当前context匹配的值。
   用public observation result/receipt、实际调用链与typed recall证明，不能读私表作public oracle。
5. 原observed_behavior/repeated_observation正例不能直接CREATE active。用同epistemic/verification的draft，
   经真实成功观察序列晋升：低风险、无hazard、可归因、90天窗口，2次eligible_for_activation、3次active。
   每次使用实际当前revision、新的scope/run证据与登记的exact terminal causal link；M `sqlite_v5.py:5590–5670,9694`。
   若未产生真实terminal/登记链则该正例仍未执行，不得临时拼receipt。

MemoryActionAuthority只授权REVISE/SUPERSEDE/SUPPRESS（H `runtime/memory_action_protocol.py:36`）；
不能拿通用grant冒充Procedure观察或activate。runner现 `case_manager.py:190` 已给这些mutation接grant，
所以30格统称“缺action authority”并不准确。

## 必须同步修的runner判定

- `runners/typed_recall_a2_oracle.py:379` 的 `normal_expected` 仅接受字符串ELIGIBLE，
  将冻结的ELIGIBLE_WITH_APPLICABILITY/ELIGIBLE_WITH_TRIGGER_SIGNAL误判false。
  需按实际完成的前置证明解释条件，未完成保持BLOCKED，完成后要求选中原对象；不能把期望改为INELIGIBLE。
- 同文件423行无条件为所有Procedure/Prospective加blocker，应改为验证实际public authority证据，缺哪项报哪项。
  负例同样先建合法适用性，以免无fingerprint先挡住而掩盖原lifecycle/epistemic检验。
- `fixtures/...:854` 的eligible需明确映射到H `ProcedureLifecycleState.ELIGIBLE_FOR_ACTIVATION`（1979附近），
  保留原输入字符串及INELIGIBLE/LIFECYCLE_FORBIDDEN预期；记录observed真实枚举，不宣称SDK返回eligible。
- Prospective19+projection1需独立signal公开setup，不搭便车计入Procedure完成。

下一有界验证建议（尚未执行）：explicit_user active匹配/错fingerprint；真实draft→三次success→active；
wrong revision/foreign run/伪terminal拒绝；reopen与同authority重放；条件型oracle正反。
通过后才逐原格定向复验，保留旧122和source10结果，不跑全矩阵、不降低阈值。
