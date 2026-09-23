# ARP-EXEC-1.1 四视角对抗自审

日期2026-09-23。身份：本计划作者自审；没有调用独立子代理，不替代未来独立生产代码评审。输入为Q01–Q20 handoff、原1.0实际包、随附reported源码索引。本轮未取得真实集成父候选或当前Assurance dirty源码。

## 1. 实施者视角：能否沿真实入口构造输入？

|挑战|修订|反例/后续证据|
|---|---|---|
|Q01 Pin词表不一致，样例用agent代替一切|44内部kind＋每字段子集＋exact resolver|错profile kind必须拒；Schema和字段表同步|
|Q02/Q03 页面和设置无法往返|Search/Read/Settings/Policy/CommandReceipt完整DTO、22verb表|含next_cursor可编码；expected_ref/revision冲突拒绝|
|Q04 从旧HEAD臆测父候选|TaskGraph23＋Assurance后继分别登记，32真实reported接缝|本地字段hash未知留null，不以seed当已审|
|Q05/Q06 重写已存在计量器却漏真实embedding|DeepSeek/HF原计量、P单独；获准BGE-M3dense资源＋原调用账本|计量与本地资源真实验证后置；HashEmbedder不作生产|
|Q07/Q08 有类型无生产授权/事务|真实原policy评价点recorder、持久creation intent和defer_drive|kernel已注册未binding不得模型调用；不是外套facade事务|

## 2. 合同与架构：是否有双权威或语义漂移？

|挑战|修订|反例/后续证据|
|---|---|---|
|Q10 按group次数把部分历史Turn计成完整N|TurnCoverage全组集合判断；当前Turn不计|中间组被挤出不计1；老anchor＋最新open tail保留|
|Q11 同span版本唯一键矛盾|Journal源不可变；view/chunker/embedding新generation|同span跨gen可共存，同closed group异source拒绝|
|Q15 部署定义hash被每次health probe改变|DeploymentDefinition静态；HealthSnapshot独立版本|稳定健康刷新不迫使所有请求失效|
|Q16 Skill变第二个runtime|原Host安装与冻结resolver、依赖锁与原runner|权限交集、未装依赖QUARANTINED、WORKFLOW无引擎则不可用|
|Q17 800文件合法但目录一项超限|4KiB summary＋详情分页，不披露deployment字段|800文件定义可导入而summary有界|
|Q18 Error/Event/快照各说各话|统一字典、7事件body、原eventseq与状态组合oracle|任意新码/错status组合拒绝，曝光不等于prepare|

## 3. 并发与恢复：有没有不可安全继续的窗口？

|挑战|修订|反例/后续证据|
|---|---|---|
|Q09 冻结ordinal内换payload|原no-send final＋hold结算后新ordinal|与handoff竞争失败只核对原请求，UNKNOWN不替代|
|Q12 局部扫描topK冒充全局|SCANNING只返回进度，扫描完固定RRF页|第二扫描页早期命中不丢；追加不混snapshot|
|Q13 lease过期重新付费embed|原invocation恢复、partition批次receipt后ACK|已完成index重送不新embed；Late结果先记费用|
|Q14 DRAINING又要求模型结束导致死等|close drain保留原active；destroy原cancel+收敛|无新LLM才能取消；7集合完整disposal|
|Q14/Q19 FileGuard反锁、GC误删|Catalogue→Orch→FileGuard→Exec→Partition；正式roottransfer|长扫描逐页释放；正式pin已独立不阻临时index销毁|
|Q19 依赖PRAGMA未配置|真实每connection设置并回读FK/recursive；关键重复insert guard|OR REPLACE即使recursive OFF也不能换关键身份|

## 4. 测试视角：删检查后哪条测试会红？

- 保留原60 SDK场景原Given/When/Then；增加20个Q级场景，全部PENDING_SDK_EXECUTION。
- 原16 SDK mutation保留；修正MU10到真实权限交集而不是仅Skill晋级。BW05改为K01/K02，不误指工具T组。
- 参考层运行真实定点替换：prior预算、N完整性、UNKNOWN、权限交集、priority、effect route、cursor组合、wire hash、TTL、activation，均由行为断言杀死；导入/语法错误不计成功。
- 新增窄反例包含SQL命名query在指定fixture上EXPLAIN、全snapshot分批scan、不合法embedding/degrade配置、overlap和依赖节点上限。
- 检查器对Schema pointer/hash、ref kinds、事件body、verb DTO、继承断言逐项核对，不用数量相等代替语义。

## 自审中额外发现并修正

1. 旧映射仍引用不存在的`arp_context_manifests`：统一到实际DDL的`arp_context_requests`；catalogue表名同样对齐。
2. profile使用嵌套context_policy的activation开关：参考oracle按真正字段路径读取，不杜撰Profile类型。
3. dependency参考节点限额与正文128不同：统一128并加边界反例。
4. Deployment实时health与定义Pin原混合：拆为静态Definition和HealthSnapshot，管理Deployment只作联合视图。
5. 工厂幂等body不能包含每次变化的授权receipt/健康时间：保留稳定caller/config/profile语义，授权快照另行复核。
6. `vector_ready_count`初次为0不能永久导致PARTIAL：冻结snapshot从upper_commit内真实向量完整集合计算。
7. 完整default policy与可配置示例值区分：新增未批准的default-policy.candidate，避免用fixture覆盖正式默认。

## 结论与未闭合边界

Q01/Q02/Q11/Q17四项已证实合同冲突在本包新Schema/SQL/oracle中具备可执行反例。Q01–Q20都有确定方案；Q04真实后继映射、Q05计量资源、Q06embedding安装及平台制品仍需本地盘点/验证。本包没有把这些填成PASS。

没有本轮已知必须由用户再选择的互斥业务方案。可以按已确定合同进行有界本地映射和主体开发；真实集成的guard/authority/资源不能填默认值。当前不得将此自审解释为SDK_VERIFIED、INDEPENDENT_REVIEW_PASS或PRODUCTION_COMPLETE。
