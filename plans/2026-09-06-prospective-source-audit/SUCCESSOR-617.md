# Host V2 与 invalidation 终局观察接入

2026-09-06：Dirac已完成本叶源码及所列分批实际结果限定复审ACCEPT；不重跑旧绿，不外推本叶未验的组合或native。

2026-09-06。产品c48e7b88，测试f1c5c5f9；实际运行Host f1c5c5f9，使用各自已安装H076/M617 target，共享通用Python依赖，无SDK源码覆盖。M617 wheel及16变动成员已获独立限定ACCEPT，不在主重复扫描。

公开读取V1、V2与settle三种operation显式区分request/observation域、结果类型与reason。settle required的source_hash和not_required的receipt_hash分别绑定；SDK原host_persistence_unverified保留，Host另存实际接收结果，不转授权或替代SDK持久回执。原V1路径与缺能力不fallback规则保留。

新批8 PASS / 2.91s：4个新控制验证实际V2结果携带V1观察不能提升、真实public settle拒绝的精确请求绑定、SDK取消使用独立settlement_cancelled原因、Host调用前取消不归因SDK finding；另外4个受影响用例适配默认V2 hook、明确缺V2能力proxy、required dependency新返回、并发V2读取后固定首个grant。未重跑原14绿或旧timer/schema集合。

该批不包括not_required真实消费成功、依赖跨库丢响应、typedcursor完整顺序与重建恢复；相关Host产品已接入但仍由单独组合测试验证，不能以这8项宣称完整consumer/scheduler或全Agent审计覆盖。没有Provider/native。

资源PG71205 exit0/remaining=[]/cleanup_error=null，elapsed3.863s，峰值181168KiB，最低磁盘3947MiB。运行后逐已加载SDK模块路径确认来自对应installed target，无源码overlay。原始产物仅本机ignored。

|证据相对路径|SHA256|
|---|---|
|.local-test-evidence/2026-09-06/successor-audit/r1/command.log|3240d3b51e55aff1c3a42f99da386d17aa12080e5f7883266defd98307495f74|
|.local-test-evidence/2026-09-06/successor-audit/r1/resource.json|3fb35ed073e2b3c31bc61aeddeddafc394f14f29dfd027dc2cf90f6982a95b9f|
|.local-test-evidence/2026-09-06/successor-audit/r1/identity.json|6c1eeb521729089f1456ba9be31fc18068a940340057fbd1637a030625023425|
|.local-test-evidence/2026-09-06/successor-audit/run.py|47990f5ba1763170a91c7670baed9ead20fdd0f9e887f0d3a4f192a160583010|
