# 原 Procedure applicability 三格公开 executor

2026-09-06，base65f42522，own feat/typed-recall-applicability-executor。
旧runner untracked applicability WIP不读入、不覆盖。源码待独审，尚未测试。

原typed-recall-v3 eligibility_cases:678–700完整行保留：bound app-v2；current app-v2/app-v3/null；
expected ELIGIBLE/INELIGIBLE/INELIGIBLE及APPLICABILITY_STALE/APPLICABILITY_REQUIRED不改。
原格未指定state，使用明确explicit_user active正控，原projection输入git/version2沿用，非observed晋升。

公开CREATE→ConversationEvidenceRegistration→ProcedureObservationAuthority APPLICABILITY_SNAPSHOT→
record_procedure_observation实际提交与重放→typed recall及exact重放。
app-v2映射公开ProcedureApplicabilityContext(git,public-procedure-fixture,2,真实schemahash)的fingerprint；
app-v3只改当前工具版本3，使用公开DTO计算；null对应当前fingerprint空tuple。
独立oracle按公开hash domain/原输入计算期望，核exact原row、实际source/receipt/revision/authority/evidence及当前context。
snapshot不声明成功terminal、不凭空增加independent_successes。原reason是类别，actual decision仍为公开
recall_no_eligible_memory；实际读取候选、完整wire/来源/hash/权限/clock与精确零读取重放才PASS。
前置拒绝保持BLOCKED；不通过改seed epistemic等方式修32非法组合。

新增必要一个方法原3正/负格+三错current context与一错authority revision反例；fixed source独审后145默认锁2GiB/180s执行。
旧已绿Procedure4/Prospective19/trigger3不重跑。Projection两格的原wire/hash/canary/scope冲突仍BLOCKED；
本片synthetic环境公开SDK合同，不是Host环境探测/真实工具执行或提醒。
