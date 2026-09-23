# 字段、精确来源和读写合同

本文件＋唯一 JSON Schema 是编码依据；`field-producers.json`给每个字段 JSON Pointer 和子树摘要，不再复制第二份嵌套Schema。所有样例只是结构正例，不是合法生产签发证据。下面的业务校验同样是 MUST，不能因为JSON Schema过了就略过。

## 1. 通用编码

- `contracts/runtime-plane.schema.json#/$defs/<Name>`是内部 DTO 的结构事实源，未知字段拒绝，所有字段必填，nullable显式null。实现可用现有frozen dataclass或TypedDict＋严格constructor，但边界只解析一次，内部不能`.get(...,True)`。
- 所有 `Pin={kind,id,revision,content_hash}`从原对象正确kind的精确不可变body取得；不修改公共TypedRef，不把id视作访问权限。`revision=0`只有明确不可变receipt/call fact语义允许。预算余额/健康状态等可变row不能假装revision0。
- 原SDK `canonical_json`继续用于持久身份；参考`canonical()`仅用于纯规则。schema_version表示本内部文档版本，和业务对象revision不同。
- 默认文档≤256KiB UTF-8、JSON最大深度24（根深度0）；ContextManifest独立上限=min(policy.max_manifest_bytes,8MiB)。数组上限独立，最先触达的byte/depth/items限制报错，不能截断后当完整。原Provider实际请求body另外受policy.max_request_bytes，不能用manifest8MiB保证wire也能发。
- 重复JSON键、NaN/Infinity/浮点溢出、整数位置bool、同逻辑ID重复、未知enum拒绝。集合先拒重复再稳定排序；conversation、tool calls、rank与workflow步骤保序。
- 引用payload读取先同tenant/root/owner scope检查，再精确对象kind/revision/hash和实际原body，最后查当前用途。缺行=SOURCE_UNAVAILABLE，不读latest/猜prefix；schema为null仅表示该明确分支无需该来源，不表示读取失败。

## 2. Pin白名单 resolver

| kind / 允许字段 | 唯一源/producer | 精确读取与当前性 |
|---|---|---|
| agent、session | 原BaseAgent binding；本session binding | 精确AgentId/root/session/generation，状态由原读者；不以Agent生命周期字段hash声称历史不变 |
| task、requirements、method、occurrence、completion_scope | 原HTN/OCC合同/Plan绑定 | 原合同revision/hash；原Task心跳row_version不可替代；occurrence以明确采用版本定位 |
| artifact、input_manifest | 原metadata/CAS，或原输入manifest writer | 原完整hash、源owner、访问/retention/pin；URI不是权限 |
| journal_record | 原Journal record_id+agent/seq+原content_hash | 限当前session授权区间和原view可见性；跨agent拒绝 |
| observation、acceptance、review、resolution | 原Assurance/OCC writer | 原对象body与UseCertificate；当前仍允许指定DISCLOSE/EXECUTE用途 |
| profile、policy | arp_profiles；原批准policy artifact/真实adoption来源 | profile initialrevision不变；effectivepolicy由adoption读者选定，恢复用manifest exactpin |
| capability、provider、tool、skill、schema、workflow、catalogue | 原registry装配的arp_catalog_revisions或已审等价writer | exactkind/id/revision/hash；catalogue同时查看activation/epoch；模型声明无准入权 |
| deployment | 实际部署loader/probe形成的不可变snapshot artifact/receipt | 包含真实adapter制品hash、namespace、健康观察时刻和期限；当前authorizer独立查 |
| authority | 原AuthorizationPort/Host权限writer＋实际recorder | 对本request/purpose/subject/input产生的原决定回执；scope/期限/撤销/root隔离复核 |
| receipt、execution、agent_turn、tool_receipt、reservation_fact | 原runtime/execution/authorizer receipt；跨库用真实import桥 | 不从可变turn或balance重造回执；同原call/request/inputhash、usage来源、issuer，历史导入不要求旧reserve仍ACTIVE |
| context、retrieval、tool_snapshot、skill_use | 本计划真实composer/retriever/catalog exposure/use writer | exactbody和original request/use key；检索收据不自动证明已向模型披露 |
| check_policy | 原Assurance批准检查政策 | 对exact Skill candidate或Task scope生效，不能从SKILL.md自报测试结果 |

实现只支持白名单，`unknown kind`报REF_KIND_UNSUPPORTED；不使用反射getattr。以Schema中的允许kind全集为上界，上表没有许可用途的字段仍拒绝。实际原表/API名在32项seams的一次本地mapping填写；reader或NEW producer尚未写是实施任务，不能用placeholder业务值关门。

## 3. 每份 DTO 的字段生产与语义约束

| Schema | 字段组 → 唯一 producer / 存储 / reader |
|---|---|
| Policy | policy_id/全部token/大小/时间/候选上限→已认证settings或deployment配置命令；原CAS/policy源；只有正上限、chunk overlap<chunk、reserve合法、section仅A–E。softcap可超但只能保required；错误配置拒绝不是静默调小输入要求 |
| RuntimeProfile | identity/owner_mode/context_policy/retention/capability/skill policy/activation refs→原factory配置writer；arp_profiles；profile不可变。parallel_model_per_agent固定1；allow_lexical_degradation不允许把真实向量缺失宣称完整 |
| ModelLimits | provider/model/input/combined/output/counter/renderer/capability_receipt→实际provider adapter。combined=null必须有真实分离限制声明，不是“未知”；EXACT/UPPER_BOUND必须有计量实现来源。支持工具不等于工具获准 |
| Section | A–E/block_id/required/trust/source_refs/view_ref/charge/witness→对应R06–R10源adapter。required不是模型决定，view_ref是实际被render的精确字节，optional根据政策删而不是改原文 |
| JournalGroup | session/group/seq/record IDs/hash/closed/view/charge/turn count/turn_id→原protocol_groups＋Journal。record序列实际连续合法；闭组由完整原协议证明；complete_input_turns非负；未闭合当前组count不能伪造1 |
| RecallItem | chunks/groups/recordpins/view/charge/rank/provenance/epoch→同session真实retriever。candidate出现即需权限；view只证明取回范围，rank不证明truth；不存在source group拒绝 |
| RetrievalReceipt | query/hash/highwater/index/model/coverage/status/exclusions/time/policy→retriever真实查询。COMPLETE需本次所需通道和完整范围完成；LEXICAL_ONLY不等语义完整；UNAVAILABLE与空结果分开。query原文不放长期普通日志 |
| ContextManifest | 系统身份＋effectivepolicy＋modelsources＋A–E＋recent/F＋tools/skills＋计量/读集/root→原request prepare hook；arp_context_requests与原frozenrequest同事务。所有hash先依赖源/实际request，不反向让request body包含manifest自身hash。original request key引用已有真实身份 |
| Capability | id/version/input/output/required_semantics/verification/provider refs→受信catalog注册；定义变化新revision。schema compatibility无可证明adapter时只允许exact schema，不根据描述当等价 |
| Deployment | 实际制品/namespace/credential ref name/平台/health/capability/effect/receipt时效→原部署加载器。credential_ref_name是引用名非凭据。unknown health/config不可作healthy；回读源过期不可自动延长 |
| CapabilityBinding | 系统binding_id/session/owner/requirement/input/selected provider/deploy/auth/verification/epoch→resolver＋原execution writer。先过滤再稳定排序；本次selection不授予新权限；新attempt在handoff前可重新选，但原UNKNOWN不可替换 |
| Tool | identity/version/schema/implementation/effect/permissions/limits→可信安装adapter，remote提示只保存untrusted metadata不用于本字段签发。inline_result_tokens不超过当前真实空间，完整结果limits不由截断解除 |
| ToolSnapshot | identity/session/generation/epoch/tools mapping/hash→tool exposure builder，写arp_tool_exposures。tools是实际model_name→exact Tool Pin/schema/adapter，拒重复name；snapshot PREPARED不等实际曝光 |
| SkillFile | safe relative_path、size_bytes、sha256、role→安装器读取实际解包文件，不接zip条目声明hash。路径规则跨平台执行；总量使用实际解压字节，不能信压缩header |
| Skill | identity/version/manifestfiles/instructions/input/output/capability/tool+skill dependencies/implementation/permissions/verification/origin→候选严格解析＋安装器规范化。instructions_path必须files里INSTRUCTIONS；script入口必须SCRIPT；WORKFLOW ref必须真实已注册；参数不含shell指令。origin不制造评估通过 |
| SkillUse | use/session/turn/input/owner/bundle/locked dependencies/tool snapshot/evaluation/auth/mode/reserve→实际use producer。INSTRUCTIONS无单独call，reservation=null且原模型调用照收费；SCRIPT/WORKFLOW必须原prepare产出的真实reserve，不允许null。body与原call/turn同身份 |
| SessionDelete | session/expectedgeneration/command/reason/retention→认证owner命令或带真实close receipt的原owner自动cleanup。系统回执生成destroy身份；模型不能直接销毁别人session |
| SessionView | IDs/root/generation/state/seq/highwater/indexlag/deletable/blockers→SessionDisposalReader与原session状态。deletable必须完整来源计算；SOURCE_UNAVAILABLE不包装false＋空blockers，应明确错误或unavailable view |
| ToolResultView | call/tool/status/artifact/intent/text/representation/hash/error→原真实toolcollector＋first-message presenter。PENDING_OPERATION必须真实intent；UNKNOWN禁止success inline；INLINE与REFERENCED结构条件由codec额外检查 |
| AuthorizationReceipt | authorization/request/session/agent/caller/owner/purpose/subject/input/decision/scope/policy/originalreceipt/time→真实authorizer执行后recorder。它只是实际来源的受信桥，不能从modelbool/测试allowall签发；拒绝结果也可审计但不能使用作grant |
| SkillAdmitCommand | skill/evalacceptance/evalpolicy/scope→固定Principal管理入口；admit验证原Assurance证据，只有用户/已批准政策可批准 |
| SkillSuspendCommand | exact skill/reason→固定Principal，要求本realm写权，更新activation＋epoch同事务；不抹旧use |
| ContextSettingsCommand | session/candidatepolicy/expectedeffectivepolicy→固定Principal；candidate来自已上传的实际Policy JSON，不能从请求接caller。settings CAS后下一请求采用 |
| ContextSummaryView | 当前或历史context计量/F/N/degradations＋当前访问状态→Host projection；historical记录不以最新policy重新计算，不显示未授权正文 |
| HostRequest/Response/Error | 见HOST-DTOS；body不是authority，所有适用分支严格二次DTO验证；未找到对象对无权限caller不泄漏“其实存在” |

## 4. 重要关联键与提交顺序

- request key: 原 `(turn_id,provider_request_ordinal)`；context_id由原derive_id(request key)产生。重送查oldcontext，不用当前newpolicy重装；同键异planned_request_hash拒绝。
- session create: 原Agent creation_key＋profilehash；AgentId由原factory给出，session_id用系统random ID并与同txn创建回执绑定，不从用户名/path推断。跨tenant重复key不合并；当前DDL的creation_key存原scope限定派生值而不是裸用户key。
- indexed_group: `(group_id,source_hash)`；chunk `(session,record,hash,offsets,chunker)`；vector `(chunk,embeddingfp)`。source hash变化不覆盖oldentry，标旧不可用并创建新generation/entry。
- catalogue: `(entry_kind,entry_id,revision,content_hash)`，ID namespace包含受信安装realm；同tuple不同body冲突。enable/suspend改变activation和epoch，definitions不变。
- SkillUse: `(session,use_key)`，key来自原ToolCall/显式load request，不来自Skill名字；同key新bundle拒绝。INSTRUCTIONS：先从原AgentTurn取得身份再入use；SCRIPT/WORKFLOW：先在原exec.prepare locked分配call/reserve，再同txn写use。
- disclosure: 只能原Provider input receipt→exactcontext→view范围→原Assurance曝光。持久历史审阅pin由原pinwriter产生，临时index只被引用为可删除cache，从不成为唯一正式证据。

## 5. 生产失败码（内部，沿现有ErrorEnvelope适配，不扩PlanningDecision enum）

`RUNTIME_CREATION_MARKER_MISSING`, `RUNTIME_BINDING_CORRUPT`, `PROFILE_UNAVAILABLE`, `TOKEN_COUNT_UNAVAILABLE`, `COUNTER_VERSION_MISMATCH`, `INVALID_OUTPUT_RESERVE`, `REQUIRED_CONTEXT_TOO_LARGE`, `FINAL_CONTEXT_OVERFLOW`, `CONTEXT_ASSEMBLY_LIMIT`, `CONTEXT_SOURCE_INCOMPLETE`, `REQUEST_BYTES_LIMIT`, `CONTEXT_NOT_EXPOSED`, `SESSION_NOT_ACTIVE`, `SESSION_IDENTITY_MISMATCH`, `SESSION_DESTROYED`, `SESSION_INDEX_PARTIAL`, `SESSION_INDEX_UNAVAILABLE`, `SESSION_DISPOSAL_BLOCKED`, `SESSION_PATH_UNSAFE`, `SESSION_FILE_BUSY`, `EMBEDDING_UNAVAILABLE`, `EMBEDDING_DIM_MISMATCH`, `INVALID_EMBEDDING`, `REF_KIND_UNSUPPORTED`, `REF_BODY_MISMATCH`, `CURSOR_STALE`, `REGISTRY_SOURCE_INCOMPLETE`, `CAPABILITY_UNAVAILABLE`, `TOOL_NOT_EXPOSED`, `TOOL_REVISION_STALE`, `TOOL_EFFECT_CLASS_UNKNOWN`, `SKILL_PATH_INVALID`, `SKILL_DEPENDENCY_CYCLE`, `SKILL_IMPORT_FORMAT_UNAVAILABLE`, `SKILL_NOT_ADMITTED`, `SKILL_BUNDLE_CHANGED`, `AUTHORIZATION_REQUIRED`, `REQUIREMENTS_CHANGED`, `OPERATION_UNRESOLVED`, `SOURCE_UNAVAILABLE`, `MODEL_CAPABILITY_UNSUPPORTED`。

缺权限、identity/hash冲突、scope越界不自动重试。index lag/服务暂不可用只重试原获准只读/embedding身份，明确次数/预算；context overflow只在未冻结候选单调减optional。原已执行未知调用不新造ID。公开errors隐藏未经授权对象详情，内部审计保留具名原因。

## 6. policy、完备性与用户设想的精确边界

`max_group_count`是单次装配的算力/消息上限，不是只保留最后若干历史。达到此上限而更旧组仍可能放下时返回CONTEXT_ASSEMBLY_LIMIT，不能仍称全token最大N；实际部署可调高。有界计算保证和全历史可检索保证是两回事。

默认实际检索可PARTIAL，但原必需输入不靠召回。如果某次任务明确要求完整历史审计，PARTIAL必须继续分页/补索引直到complete或明确blocked，不许写“没找到所以不存在”。对照基准用全量授权Journal读取，不能以向量topK作为负事实证明。

近期组摘要、工具首次外置都按source view计量/曝光；原输入的授权不是一条历史文本本身。正式sessiondestroy不执行用户长期记忆删除；本计划没有全局记忆库、没有跨session自动私有历史共享。
