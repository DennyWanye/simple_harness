# 主体接线检查单

主代理是唯一开发者；子代理仅在实际可调用且模型受限时审阅，不能写开发补丁。一个集成人处理Runtime/assembly/UOW/wire/schema/Host hot files与Assurance后继合并；不得覆盖dirty内容。

| ID | 已接生产调用链而不是文件存在 | 对应验收 |
|---|---|---|
| BW01 | authenticated profile/bootstrap→所有root/child/batch/delegate创建意图→defer kernel→同UOW绑定→ACTIVE | case:R01/R02/N07/N08 |
| BW02 | 原wire恢复tool/opaque→计量wire+prior→原reserve/request+ContextManifest→handoff当前检查 | case:C01–C10/N05/N09 |
| BW03 | Journal闭组→INDEX→原embedding call/receipt→分区gen→ACK；全历史search/read分页 | case:M01–M10/N02/N06/N10–N13/RV_R2/RV_R3A/RV_R3B/RV_R3C/RV_R4/RV_R5 |
| BW04 | 同namespace原catalogowner→runtime_catalog；无平行可写registry/旧UI分叉 | case:N15/N17 |
| BW05 | CapabilityRequirement→ProviderDefinition/Deployment硬门→精确priority→原调用 | case:K01/K02/N15（不是T01–T04） |
| BW06 | 真实Skill import/lock/trial/eval/admit/load/execute；原沙箱/OPS权限交集 | case:K03–K10/N16；MU10检查这里 |
| BW07 | close/drain/destroy/七集合/rename/purge/rebuild；UNKNOWN费用保留 | case:R03–R10/N14/N19/RV_R1 |
| BW08 | Settings完整GET/UPDATE/replay、history和catalog分页、Host新旧视图、原event消费 | case:I01–I10/N03/N18/N20 |
| BW09 | 原GC/Assurance pins/exposure/OCC/TaskGraph输入真实adapter；保留根transfer可恢复 | case:N04/N19 |
| BW10 | 两DB各runner/checksum/FK/PRAGMA、legacy marker、默认ON工厂、无源码旁路 | case:N19/N20 |

每项填实际source ref/qualified symbol/hash/owner/尚缺边。BODY_WIRED表示主体均已实现和连通，可进入统一验收；不是提前填SDK PASS。当前NEW代码尚未完成不应阻止开始编码。

施工顺序：一次有界来源/资源盘点→主体合同与原事务helper→Context/索引/工作/资源全链→catalog/Skill/Tool全链→Host/所有角色→统一验收。期间只针对明确阻塞做最小技术反例，不反复跑批量测试攒数字。非依赖模块继续，缺真实资源时对应activation拒绝不填默认。

同交付更新 Host `ARCHITECTURE/AGENT_ORCHESTRATION.md`（或现有对应模块事实源）、`ARCHITECTURE/index.md`、`PROJECT_STATUS.md`、证据相对索引；当前源码/安装制品/原生UI证据分开。新功能完整验收后默认ON，既有active Agent profile不变。原始日志/JUnit/SQLite/截图/cursor/模型input留ignored，Git仅结论/命令/nodeid/hash。

R3新增主体调用边：原prepare slot→固定source/group快照→内部recall start/resume→原query调用→全页聚合→纯composer→真实wire冻结＋aggregate ref。R1普通rebuild与destroy-resume的路径必须分开。功能代码未落地的本地proof保持PENDING；不能用包内参考composer替生产BODY_WIRED。
