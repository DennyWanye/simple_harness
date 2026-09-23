# 字段与引用：唯一编码/来源合同

## 1. 唯一数据源

`contracts/runtime-plane.schema.json` 是结构唯一源；`field-producers.json` 是递归JSON Pointer+hash+type-producer引用，不保存第二份nested_schema。`type-producers.json`指定每种对象真正caller/owner/store，`ref-resolution-map.json`限定44种内部Pin的exact reader。公共TypedRef不改。`reference/schema_codec.py`只实现本包使用的Schema子集；不是一般JSON Schema或生产权限验证器。源码实现必须沿项目strict codec逐字段解析并调用相同语义规则。

生产DTO名/schema_version由冻结ARP_V1_1 profile选择；旧ARP_V1已保存对象仍用旧decoder，不能把1.1新增必填字段强加给旧bytes。数值bool≠int；非有限数、重复JSON键、未知字段拒绝。所有hash以原canonical_json/明确raw字节域为准，不自行统一成一个hash。内部Pin四元组=kind/id/revision/content_hash；revision0仅限原immutable writer确实定义0且精确匹配，不能作为缺字段默认。

## 2. Pin不是授权token

每个Pin字段嵌入自己的kind子集；例如ContextManifest.profile_ref必须profile，input_manifest必须input_manifest，工具Schema必须schema，依赖lock必须dependency_lock，不能统一写agent。artifact/receipt下还需注册明确sub-provider，不按字符串前缀/反射getattr猜来源。

resolver先查实际root/tenant/owner/所属Task或Agent，再核id+revision+hash+用途/currentness。Task/Requirements/Plan/Agent control/policy adoption/Index generation分别是不同版本。输入过期可以保存历史，但不能新使用。持久费用不因读取授权撤回而被抹除。

模型不创建authority/reservation/receipt/context/source等Pin；只能引用该请求实际披露的精确目录。完整catalogue、计划来源、Session回读均要原scope/Assurance读门。没有原call输入收据不能创建RuntimeContextExposed。

## 3. 整体与slice的hash域

Journal record hash=原完整记录；Group source hash=ordered原record refs+closing receipt身份；derived view hash=脱敏/裁剪后的精确UTF8片段；chunk key=Session/index generation/record/source/view/chunker/byte span；vector key再绑定embedding fingerprint；manifest hash=全部冻结字段的canonical bytes（不包含自身hash）；planned_request_hash=实际最终Provider wire字节/原序列化对象约定。search source_snapshot_hash与query_hash、index_snapshot_id不同；分页slice_hash只表示所传片段。

Command receipt由原writer产生，ID可以预分配，body中不循环包含自身content_hash。profile/bootstrap can reference真实先行批准receipt，不让approval反过来要求尚未创建的Agent。读snapshot没有版本时不编revision0。

## 4. 空与失败的完整词典

- 无模型请求：ContextSummary.context_id/manifest/input计量=null，而有效policy/adoption仍存在。
- 本次未检索：ContextManifest.retrieval_receipt_ref=null；summary可显示NOT_REQUESTED，**不产生RetrievalReceipt**。
- 完整查询零命中：SearchPage RESULTS/ranking_final=true、COMPLETE_EMPTY、items=[]、has_more=false，coverage必须完整。
- 扫描中：PARTIAL、SCANNING/ranking_final=false、items=[]、next_cursor非null；不是空命中。
- 索引尚未跟上：扫描结束可RESULTS＋coverage.PARTIAL；next_cursor=null表示当前snapshot结束，不表示全Journal完整。
- embedding运行故障且获准降级：LEXICAL_ONLY，coverage.mode=LEXICAL_ONLY；不是旧协议fallback。
- 根本无来源/无读取权/DB错误：typed Error，不填false/[]后继续。
- 三种job没有工作：完整成功读到空集合才可EMPTY；缺任务库不等于完成。
- disposal七集合全部complete且确为零，才all_safe；部分读/超限均不可purge。

`RetrievalReceipt.returned_count`是本page的数量，1.1不再复制整份returned_items；实际正文只在SearchPage.items。上层对象、SQL字段和工具生成必须采用这一种编码。

## 5. 语义组合与约束执行位置

| 对象 | context-free constructor必须验证 | 真实producer/reader必须验证 |
|---|---|---|
| RuntimeProfile | required/degrade合法；并发单Agent=1；protocol匹配 | actual deployment/bootstrap grants/resources |
| ModelLimits/TokenReceipt | W/P/O独立、支持mode、finite/count; P>0有basis | 当前原counter/renderer/serializer/resource映射 |
| ProtocolGroupSnapshot | mandatory完整、call/result配对、Turn集合计N | 真实Journal关闭/原执行回执、hidden seq覆盖 |
| ContextManifest | request hash/tokenreceipt一致、input≤budget、N与selectedturns | 最终wire精确字节、readset/版本、原reserve |
| SearchPage | has_more↔cursor、SCANNING不出排名、COMPLETE不能partial | snapshot完整性、真实ACL/缓存scope、全过程预算 |
| HistorySlice | UTF8边界、slice bytes/hash、record_complete | 原文hash、range是否可读、没有隐藏源权限扩大 |
| Capability/Skill/Tool |精确引用kind、依赖无环、无路径碰撞|原catalogue唯一owner、当前许可交集和原executor |
| Deployment |真实状态字段不缺、版本独立| health probe真实receipt，不能model填写healthy |
| Host DTO |每verb payload/response、同adoption/ref、分页一致|固定caller，subjectAgent与Session关系，command幂等 |
| Jobs |kind标签对应payload、状态/lease/期限组合|原call receipt和complete集合，deadline不归零 |
| SessionDisposal |七集合齐全、count/hash一致|当前owner/current root/gen和实际read handles/UNKNOWN |

没有context-free语义约束的纯请求对象仅结构检查，不凭“validator存在”声称生产安全。`check_plan.py`同时测试明确的反例；生产侧仍须登记exact nodeid/断言。

## 6. 参数与字节限制

`Policy`是批准的软/硬预算，不保证数组最大值都能同时装进document上限。实际最先到达的token/body/page/row/时间限制产生具名结果，禁止截断后写COMPLETE。Schema JSON depth默认24是包含结构包装的容器深度；脚本参数可更紧。policy max_group_count最多8192与GroupSnapshot一致。ContextManifest上限8MiB；普通管理请求256KiB；MODEL search/read page≤64KiB；CatalogueSummary≤4KiB；Skill完整definition≤256KiB，文件内容走独立64MiB包与分段ref。包允许1024文件，过长metadata在import报DOCUMENT_TOO_LARGE，不截字段；800普通路径通过summary/details方案。

## 7. Deployment定义与健康快照不得共用hash

`Pin(kind=deployment)`唯一解析为DeploymentDefinition：deployment_id/version/provider/implementation/endpoint namespace/credential ref name/支持语义/平台/scope/approval的静态canonical正文。`Deployment`仅是管理联合View，不允许作为Pin正文。`DeploymentHealthSnapshot`来自原真实probe receipt，用health_revision独立版本，投影存`arp_deployment_health`。同一稳定定义的healthy probe更新可产生新health receipt，但不改变定义Pin或semantic epoch。

原managed catalogue owner在事务外probe，原调用回执先落地，同owner短事务导入immutable snapshot。healthy→healthy不提高admission semantic epoch；READY→UNAVAILABLE、权限/定义变化等调用资格改变才提高相关epoch。最终handoff读取当前合格且未过期的health snapshot；若仅后继健康刷新，复核通过而不是把所有原请求判stale。部署定义升级另开version；旧profile、旧请求不得偷偷切向新实现。

自动重试与显式新命令不同：error-catalogue.new_identity_allowed=false表示错误本身不授权换身份，不禁止用户按新输入发新command。CANCEL_UNSENT必须拿到原no-send/settlement证据后按原状态迁移授权新ordinal；不能仅看Error.retry值猜。
