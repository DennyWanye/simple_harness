# Prospective source read Host账本

2026-09-06；base c98b6a27，独立feat/prospective-source-observation。

接口：`ProspectiveSourceJournal(path, clock=time.time, fault=None)`，
`await journal.read_prospective_outbox_source(manager, *, principal, outbox_id,
payload_hash, operation="read_prospective_outbox_source")`；
`PublicRegistrationAuthoritySource(..., operation_audit=None)`默认创建同state目录
`operation-audit.db` journal，主可传共享实例。main/composition本叶不改。

直接复用`memory_call_attempts`及`memory_call_findings`，不新增表/迁移/权限。
caller区分v1/v2；request_ref绑定operation+owner+SDK请求哈希，attempt每实际调用唯一。
Run/context/plan/decision列NULL；result_hash只记录SDKsource_hash。
SDK observation按原public DTO重新解析、核canonical observation hash、请求/claimed owner/
source/outcome及异常类别；不存payload、查询原文、原始异常、SQL或原始outbox ID。
哈希沿既有`SHA256(C({domain,payload}))`，C为UTF8、sort_keys、紧凑JSON、
ensure_ascii=False、allow_nan=False。不是NUL域，也不是SDK receipt签名。

M616没有Host attempt context输入。`captured_bound`只表示直接调用捕获且绑定一致；
不声称SDK持久绑定Host attempt/授权/密码学防伪。原observation及
`persistence_status=host_persistence_unverified`逐字段保留，Host settlement另外提交。
拒绝、取消、读失败均保留原异常，记录闭集reason，不把observed source当grant。
原registration replay无需新source读，故不捏造额外SDK调用账目。

写入start后只调用一次Manager；开始写失败仍调用一次，不声称有持久记录；settlement
失败保留pending，绝不为诊断重放业务。安全last_code/log报告storage/read失败，失败日志
不证明完整持久化。强杀、磁盘不可写、未接wrapper的入口仍是coverage缺口。
取消先join自有SQLite线程并关闭连接后传播，读取已取消时尝试保存真实attachment；
取消发生在开始写期间不调用SDK，标cancelled_before_call/not_invoked。busy timeout
200ms仅SQLite锁等待，不声称硬200ms总耗时；无脱管writer。page仅受信进程内owner查询，
不是对外授权接口；返回all_operations_recorded=False、usage/cost=None。

operation白名单预留v2，必须精确调用对应公开方法，M616缺v2明确失败，不fallback。
v2完整source/observation wire由Singer后继契约固定；本片不把预留调度称v2验收通过，
后继沿同账本接DTO验证，不另造schema。

本次oracle：真实public builder+Host evidence admission+prospective CREATE/outbox→
默认Host登记读及实际grant不变；重开保存exact SDK observation/零原文/owner隔离；
payload/owner/type拒绝；公共aiosqlite故障点下实际Manager取消/读失败及复用；
start/settlement写故障不重读；写中重复取消清理/不调用SDK；
真实返回后变异request/owner/source或缺observation不得captured_bound；
sidecar读失败安全码、旧M616无v2不fallback。
测试载体只复用已安装H075target+M616installed及原tiny解释器通用依赖；
不重跑pin/旧075绿色，不新建环境，不发Provider/native。
