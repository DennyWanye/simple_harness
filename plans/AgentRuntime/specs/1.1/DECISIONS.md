# Q01–Q20 一次性裁定处置

本表的RESOLVED指规格选择和资产已同步，不是SDK已实现；MAPPED为手头handoff已报告来源，实际后继合并字节仍需本地核对；ENVIRONMENT_DEPENDENCY已有唯一可实施协议，缺少实际模型资源/模板证明，不能假装运行通过。所有一般实现继续，不把未来验收当开工许可。

| ID | 状态 | 唯一决定 | 修改资产 | 关闭反例 |
|---|---|---|---|---|
|Q01|RESOLVED|44个内部Pin种类，逐字段子集与白名单resolver，不改公共TypedRef|contracts/runtime-plane.schema.json;implementation/ref-resolution-map.json|正确profile通过；把agent替代profile拒绝|
|Q02|RESOLVED|Search扫描进度与固定排名分页、UTF8原文范围页，管理/模型purpose隔离|implementation/HOST-DTOS.md;implementation/CONTEXT-SEARCH.md|早期命中在后续扫描页仍被找到；cursor陈旧拒绝|
|Q03|RESOLVED|policy批准与adoption分开，首次GET完整，更新CAS与命令回执读口|implementation/HOST-DTOS.md;examples/host-settings-flow.json|adoption3竞争4拒绝；断线同命令读原回执|
|Q04|MAPPED|TaskGraph23与Assurance后继为合并链；已报告真实位置入source-map，未见后继字节不伪hash|source-map.json;implementation/role-coverage.json|bridge实际为agent_worker；全role最终经过真实wire guard|
|Q05|ENVIRONMENT_DEPENDENCY|复用DeepSeek/HF计量，公开wire/P/O/计费分开，先恢复tools后冻结|resources/DEPENDENCIES.md;implementation/INTERFACES.md|P单扣；512K配置不冒充真实端点支持|
|Q06|ENVIRONMENT_DEPENDENCY|获准本地BGE-M3 dense或明确兼容已批准资源，原call/usage桥；activation真值表|resources/DEPENDENCIES.md;implementation/JOBS-LIFECYCLE.md|HashEmbedder不可生产；timeout不重复付费；lexical状态明确|
|Q07|RESOLVED|原权限评价点记录真实receipt；bootstrap先批准安装payload再profile，不靠AllowAll|implementation/INTERFACES.md;ARP-EXEC-1.1.zh-CN.md|standalone显式mode；无真实评价来源拒绝激活|
|Q08|RESOLVED|持久creation intent+kernel defer-drive+同UOW最终binding；禁止嵌套facade假原子|implementation/INTERFACES.md;sql/execution_additive.sql|kernel登记后崩溃同IDs恢复且不提前调用模型|
|Q09|RESOLVED|旧请求取消未发送+原hold结算回执后新ordinal；已handoff/UNKNOWN不替代|ARP-EXEC-1.1.zh-CN.md;reference/runtime_rules.py|与handoff竞争失败必须核对原请求|
|Q10|RESOLVED|原协议组锚点/闭组，N按完整历史Turn集合，不逐组相加|implementation/CONTEXT-SEARCH.md;reference/runtime_rules.py|旧Turn中间组被移除不计1；早anchor仍保留|
|Q11|RESOLVED|Journal源不可变，view/chunker/model新index generation；固定upper_commit快照|sql/session_partition.sql;implementation/JOBS-LIFECYCLE.md|同span跨gen可共存；同closedgroup不同sourcehash拒绝|
|Q12|RESOLVED|全snapshot分批扫描、固定词面+RRF、短中文fallback、稳健f32归一化|implementation/CONTEXT-SEARCH.md;reference/runtime_rules.py|1e308非零finite；PARTIAL无全局排名；追加不混快照|
|Q13|RESOLVED|INDEX/PURGE/BIND_IMPORT三种严格job；SUMMARY不启用；原调用身份恢复|implementation/JOBS-LIFECYCLE.md;contracts/runtime-plane.schema.json|lease过期只重领协调；index成功ACK前退出可幂等继续|
|Q14|RESOLVED|close drain与destroy不同；七集合disposal；固定无环锁序|implementation/JOBS-LIFECYCLE.md|drain允许已有Turn结束；destroy不需新LLM结束；busy保留|
|Q15|RESOLVED|原Host目录唯一realm owner，多pool挂载；ProviderDefinition与priority明确|ARP-EXEC-1.1.zh-CN.md;implementation/integration-map.json|health正常刷新不全局失效；撤权阻断相应新调用|
|Q16|RESOLVED|safe import/quarantine/unresolved lock/trial/eval/admit完整，原runner执行|ARP-EXEC-1.1.zh-CN.md;implementation/INTERFACES.md|旧页面已安装仍可管理但未获准不可调用；依赖diamond冲突|
|Q17|RESOLVED|管理/模型summary+受权details分页，完整包不塞进单项64KiB|implementation/HOST-DTOS.md;contracts/runtime-plane.schema.json|800文件可分页，模型不披露credentialref/endpoint|
|Q18|RESOLVED|统一ErrorCode目录+严格事件body+原eventseq来源，不再任意字符串码|contracts/error-catalogue.json;contracts/event-catalogue.json|未请求/空/部分/不可用组合可表达；未知错误不透传|
|Q19|RESOLVED|exec和partition独立迁移；所有连接FK/recursive triggers；精确retention/原roottransfer|sql/MIGRATION-CONTRACT.md;sql/execution_additive.sql|不占用TG25作为exec号；OR REPLACE被拒；正式根不依赖temp不阻删|
|Q20|RESOLVED|保留60+新增20；MU10/BW05纠正；主体优先、平台/实际资源依赖显式|implementation/TEST-PLAN.md;implementation/BODY-WIRED.md|未来测试未运行不阻纯编码；没有原生/模型证据不填完成|

## 不再转问用户的一般事项
普通目录/函数命名、当前迁移空号、等价封装、已有工厂落点由主集成人记录。不得通过改需求、关守卫、填空/True、删原验收解决。只有实际后继接口与本文两项语义无法同时满足时，一次提交Q编号、实际source/hash、冲突两项和最小推荐方案；其他工作继续。

## 当前结论
规格已选择明确原生实现；有界本地盘点和主体开发可以推进。实际集成父源、首模型计量资源、embedding/平台制品为本地依赖，不预填通过。ARP不是新Runtime，不需要重开HTN/TaskGraph/Assurance。