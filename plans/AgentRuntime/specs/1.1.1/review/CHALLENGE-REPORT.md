# ARP-EXEC-1.1.1：R1–R5四视角作者自审

本轮基于收到的局部评审修订；未调用独立子代理，不能代替未来独立生产代码审阅。不重开Q01–Q20、不引入其他Runtime、不以未来功能未实现否定明确开发任务。

|视角|挑战|处理与可复核依据|
|---|---|---|
|实现者|PURGING异常没有合法持久路径，且普通重建可能复活|同state typed PurgeProgress；SessionDeleteResume明确caller/DTO；tests PurgeReview + FinalCrossChecks|
|合同|扫描中COMPLETE_EMPTY、partial-index COMPLETE_EMPTY都可能过嵌套检查|单一status_for表＋validate_receipt/page递归，R2-A/B负例和正常词法降级|
|合同|Query返回单页count但Manifest引用整个F缺完整身份|ContextRecallResult全候选/页链hash＋manifest v2非nullaggregate引用；ContextSummary独立selected数|
|并发恢复|自动检索重启换embedding身份或混入后来Journal|原prepare slot/C0不可变request/原call key/C1精确snapshot/C4page幂等/C6aggregate；参考composer恢复反例|
|并发恢复|不许词法降级的UNKNOWN可能借总timeout变成可选F跳过|来源安全优先；WAITING_EMBEDDING且禁止降级到期限BLOCKED，参考测试明确拒绝|
|测试|删除receipt已写但PURGED未写，再出现目录异常无法记录|允许PURGING/DELETE_CONFIRMED带typed blocker，PURGED要求无block；原rename/delete receipt不可覆盖|
|测试|SQL缺JSON phase字段会因NULL三值逻辑漏检；DRAINING可跳相位|显式json_type/COALESCE；DRAINING→PURGING仅RENAME_PENDING；窄SQL负例|
|合同|RRF各路截断与全局截断混合，tie key不一致|完整ScoredRow stable_key；shared stable_fused_topk；K2/M1和反向record/chunk测试|
|实现者|内部clock/checkpoint只是名字，失去明确生产归属|严格ContextRecallCheckpoint；capture_context_recall_clock_locked复用原ClockPort/receipt，未声称已有隐藏producer|
|测试|旧fixture的page本身语义非法会使新规则测试误失败|更新合法正常fixture，不放宽新语义；继承生产断言未改|

结果：本轮R1–R5规范选择已同步；109参考测试、32窄例、10+5参考变异的实际结果另见VALIDATION。它不是“数学意义100%零缺陷”；真实integration parent、资源和来源真实性仍按本地盘点与主体后验收取得证据。

参考测试不验证原账本收费、真实授权、实际FileGuard安全删除、真实UI/模型。具体生产缺口已有case:RV_R1/2/3A/3B/3C/4/5；不得用此自审报告将它们标PASS。
