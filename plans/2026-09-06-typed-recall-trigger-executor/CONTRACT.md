# Prospective trigger executor与projection构造边界

2026-09-06，base dfec8bbb；复用own树feat/typed-recall-trigger-executor。
源码准备未测试，固定审查后仅新test方法+原3格；旧19/Procedure不重跑。

## 原三格与公开执行

原typed-recall-v3:701–723不指定state，而指定typed_trigger_complete/current_signal_complete及expected。
使用真实公开CREATE pending，保留原event release_succeeded及source payload，不改boolean/expected。
- trigger-signal-complete：实际public outbox registration ACK，pending具当前有效registration，真实recall。
- signal-missing：同一合法pending尚未ACK，真实recall no_recall；再消费同ID实际ACK的独立正对照，
  不改原无signal观察。当前signal定义复用M0613对pending的accepted registration规则，不声称外部event发生。
- trigger-missing：公开ProspectiveMemoryPayload(action,None)确切TypeError；没有伪造current signal，
  标CONSTRUCTION_CONFLICT/BLOCKED，不能把DTO拒绝判成原INELIGIBLE PASS。

父oracle读取冻结原三行，独立核original recipe/boolean/expected；有效source/ACK/ref/clock/hash复用已审独立校验，
对照与原回合均核完整public wire、query/subject/run/budget/来源内容/分类/evidence/revision/实际candidate access/重放。
新test三原格应2PASS1BLOCKED；三篡改包括外Run、丢publicoutbox、正控旧revision自洽重hash命中来源理由。

## Projection精确冲突（只读事实，未改原格/阈值）

原`selection-budget/projection:procedure`（fixture2010起）要求payload.applicability={tool:git,version:2}，
payload_hash=756bf2a8b670fb8e52556de57a9ff69394dfaab000898c44ce3eb25afea02ab7。
H073 runtime/memory_protocol.py:2333 ProcedureMemoryPayload.applicability只能tuple[str]；
M0613 sqlite_v5.py:5009公开投影返回该list，现合法实际值是[git@2]，不是冻结object。

原`selection-budget/projection:prospective`（2052起）要求trigger={kind:event,event:release_succeeded}，
payload_hash=f3218e1a14ea1c2f47781b304b445a9e2ff84247781df674661723b603fdfdaa。
H073 memory_protocol.py:2410/2466要求strict ProspectiveEventTrigger，公开wire含trigger_kind/event_authority_ref/
condition/condition_hash；M0613 sqlite_v5.py:5020直接返回typed trigger。和冻结两字段对象及hash不相等。

这不是缺Host scheduler API；仅补signal/registration不能改变公开projection字节。
另原full source还要求secret-evidence/SENSITIVE/cross_scope/extra_typed_field完整来源与去字段证明，
现normal seed仅带allowed字段，不能偷偷宣称该canary/crossscope AC已完成。
保留两格BLOCKED及原hash。最小后继需要明确决定冻结payload与当前public wire的兼容解释/接口变更，
并独立补真实公开分类/来源scope/canary setup；本叶不改SDK wire、阈值或把原source改为结果gold。
若此边界暂未决，独立可执行下一组是原Procedure applicability match/mismatch/absent（已有public snapshot seam），
不需要Host UI/模型；须继续保留原app-v2/app-v3语义标识与actual fingerprint映射，另固定小叶执行。
