# 后台analysis物理出站guard

2026-09-06，base9f745c7e，feat/analysis-physical-request-guard；保留232审计分支。
主resolver单独e3b6a360已继承为951b1a62，不编辑main工厂接线。

接线：`AnalysisPhysicalRequestGuard(state_db_path, binding=SdkRunBindingV1,
runtime_getter=lambda: runtime)`是async callable；仅受信Host analysis工厂传给
`resolver.build_authority(binding, request_guard=guard).provider`。
默认Ellipsis仍原primaryguard；显式None/非callable拒绝，原provider incarnation/
config/model params/price构建不变。不是按post-turn前缀绕过权限。

源SDKRun是已完成foreground；post_turn attempt是Host执行事实，绝不伪SDK Run/
handoff/use receipt。guard实际查purpose=analysis、handed_off、无settled/unknown，
Host/SDKRun绑定、真实subject和终态generation、members、source outbox provider
binding一致。实际物理requestID定位必须唯一，body按H075公开provider_request_json
完整canonical hash比较，包括temperature/metadata/message name/call_id/tools。

新analysis-attempt-input在reserve_analysis_attempt的原BEGIN IMMEDIATE内，通过
input_observer_tx追加既有Host S1+Receipt：保存完整MemoryAnalysisRequest、公开
ProviderRequest wire/hash、原candidate snapshot identity/hash、source_policy。
没有新DDL/权限ledger；任何中断两者同时rollback或一起保留，旧receipt不重写。
旧无physical_guard_version载体不能新physicalsend；已succeeded响应仍按原durable
恢复不调用Provider，不要求为旧成功补新grant。

source_policy逐ordered S1验证真实Host pair+outbox+turn+原disclosure token/current
head，保存原analysis disclosure context。各source context必须与原请求精确一致；
current SELF语义必须兼容原请求，token更换即拒。原无token来源只允许既有legacy
Host-default SELF规则，不能用后来authenticated-control覆盖。禁止取最新turn代替
全batch来源、禁止用后台requestid查不存在foregroundgrant、禁止重建更宽context。

物理guard重新读取该持久carrier和candidate原S1，不接受调用方两个自洽hash作权限；
保留SemanticCorrection prepare/check、explicit correction授权和原外层guard。
所有source及actual typed candidate四元组通过公开Memory history visibility当前核验，
候选为空不跳source。慢读取之后fresh Host连接复核原policy/attempt，再作最终公开
Memory复查及Host复核；这证明最终检查时点，**不声称两个DB构成原子撤权fence**。
取消传播、拒绝只发生实际delegate前，安全error_code保留私有cause不打印/落新原文日志。

新14控：生产resolver及HTTP MockTransport真实send→Memory materialize→durable
恢复零重发；text/temperature/metadata/tool/requestID/foreignRun/snapshot混配拒绝；
慢Memory后Host披露换代/source forget/candidate forget拒绝；reservation提交前后
故障无半carrier；默认foregroundguard及None/noncallable拒绝保留。
前置foregroundterminal沿既有确定性Host fixture，不冒称该source是新native执行。
只测试本变化，无模型/native、新env、旧075全套。closure/compaction同类接线另列未完成。
