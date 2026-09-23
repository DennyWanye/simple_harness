# SimpleHarness 原生 Agent Runtime Plane：代码级执行计划 1.1

**编号 ARP-EXEC-1.1｜2026-09-23｜取代 1.0 的当前施工规格。**

本交付修订 Q01–Q20。仅增强既有 BaseAgent / AgentRuntime / ReAct，不引入 Pi、RuntimeBackend、跨 Runtime checkpoint 或引擎热迁移。Provider 是模型、embedding 或工具实现的适配，不是替换 Runtime。

## 0. 权威、基线与执行纪律

1. 当前用户要求、Q01–Q20 handoff、本主规格与配套严格 Schema/SQL/接口共同组成合同。inputs 仅供追踪，不与新正文竞争。没有批准降低 H1–H8、TaskGraph、Assurance、OCC/OPS 的语义。
2. 本轮读到的是 handoff 和其 SOURCE-INDEX 的**路径/符号/文件 hash 报告**，不是本机源码。TaskGraph 集成父候选是报告的 `0.13.0.dev20260922+taskgraph.23`，wheel hash `e1d34a46bcabb43d82be39bf8bb39755838d29993e81f1e1b95b92507e004c77`；后续 Assurance 的实际字节须由主集成人从当前工作交接盘点。不能回到旧 HTN HEAD 假装审阅最新。
3. 共享 Host 的 `htn.1`、HTN dirty 候选 `102ad3d…`、taskgraph.23、Assurance 后继分别登记；本包不 reset/clean/pull/rebase/cherry-pick，不合并、安装或推送代码。新 source-map 使用 REPORTED_SOURCE 和 UNVERIFIED_IN_CURRENT_PARENT，不伪造 actual hash。
4. 主体代码由**主代理**完成。独立子代理仅评测/挑战，不写开发补丁；可调用时只用用户允许的 gpt-5.6-luna/terra/sol。本包挑战是作者自审，不冒充子代理。
5. 先完成主体和跨层接线。之前仅做具名 blocker 的静态/窄反例；之后统一60原场景、20新增场景、原16变异、增量变异、stateful、回归、原生和模型验收。不把未来 NEW 模块尚未实现当开工阻碍。
6. 完整交付并通过规定验收后，新 Agent 默认 ON，不另设永久灰度批准；旧活动 Agent 的创建协议/profile/请求字节不变。新 profile 用 `ARP_V1_1` 独立不可变 marker；原 `ARP_V1` 如已产生数据，保留其冻结 reader/codec，不将新字段强解析进旧行。
7. 用户长期记忆、NanoJev/Shadow/PR-7 不纳入。应用模型从当前已批准 profile 读取；不改路由、不要求提供秘密。

## 1. 最终架构与唯一责任

```text
已认证 Host / Mission 原调度
  -> 原 AgentRuntime 创建、submit、同 Agent 串行 Turn
  -> 原 Journal / Group closure
  -> 原 A–E source / Capability / Tool / Skill 精确快照
  -> 本会话索引检索 F + 最近连续闭组 G + 必需锚点
  -> 原 AgentProviderWire 恢复 tool_calls / renderer / serializer
  -> TokenReceipt + ContextManifest + 原 request/reservation 冻结
  -> 原 ProviderInvocationCoordinator / ToolGateway
  -> 原 raw/call/usage 账本 -> exposure import / Assurance
```

- 编排库仍管 Task/Plan/授权/OCC/Assurance。ARP 不新增编排库业务表；只修改原消费者的适配/来源接口。
- 每个原 execution DB 仍管 Run/Agent/Turn/Journal/call/reserve。本包执行 side-table 存协议、冻结输入和协调绑定，不重复保存 call 的执行状态。
- 每个逻辑 Session 一个 index.sqlite3（包含隔代索引和游标缓存）；所有正文权威仍是原 Journal/CAS。
- 一个部署 realm 只有一个 catalogue owner。复用 Host managed install/resolver 和 SDK runtime_catalog；多 execution pool 是该 owner 的读客户端，不各自准入一套同名目录。§8 规定物理归属。
- 原 AgentBridge 位置是 `agent_orchestrator/runtime/agent_worker.py`，不是 agent_bridge.py。

## 2. 接线与来源（Q04/Q07/Q08）

`implementation/integration-map.json` 覆盖原32接缝，`ROLE-COVERAGE.md` 覆盖所有角色；具体原始方法体未交付的只标未核实，新的 producer 明确目标签名，不冒充原方法已存在。

| 接缝 | 保留的真实入口（handoff 来源） | 本次必须实施 |
|---|---|---|
| Runtime 装配 | `agent_orchestrator/runtime/assembly.py::assemble_orchestrator_runtime` | 为每pool注入同realm CatalogReader、真实authorization adapter、meter adapter、共享embedding资源；ARP拒绝AllowAll |
| Agent创建 | `simple_harness/agents/runtime.py::AgentRuntime.create` | §3 持久creation intent＋延期激活；批量/delegate同入口 |
| binding | `execution/sqlite/uow.py::create_agent_binding` | 提取 connection-level `_create_agent_binding_locked`，原facade保留外层transaction |
| wire | `agents/wire.py::AgentProviderWire.prepare_request` | 恢复tools/opaque片段后才最终计量和冻结；冻结后serializer不得变 |
| Provider | `execution/dispatch.py::ProviderInvocationCoordinator` | 复用request_preparer/admission/physical invocation；不直接HTTP调用 |
| 计量 | `deepseek_tokens.DeepSeekV41TokenEstimator` / `hf_chat_tokens.HFChatTokenEstimator` | §4 的计量适配，不重写tokenizer |
| 预算 | `provider_budget_guard.ProviderBudgetGuard` | 保留 Orch→SDK 顺序、真实reserve/UNKNOWN/prior_output |
| 工具/Skill | `tools/runtime_catalog.py`、Host `capability_catalog.py/skill_resolver.py/skill_install.py` | 原产品入口接唯一目录/准入，禁止新老目录分叉 |
| 数据库 | `execution/sqlite/database.py::Database.open` | 每个真实writer/recovery connection设置并读回FK及recursive_triggers |

用途授权均由原 Host 固定 Principal/tenant、服务委派、TaskScope/工作区/OPS/Assurance 当前来源评估；AgentId 是被授权对象，不是用户本人。具体表见 INTERFACES §2。Recorder 只能在真实评价点记录 policy+输入+来源回执，不包装 AllowAll 的 True。

首次安装先用认证部署命令冻结 policy/schema/内置工具定义；真实安装 receipt 可引用 payload hash，不反向引用尚未产生的 profile。QUARANTINED→TRIAL 做静态/schema/装配检查，→ADMITTED 由原 deployment policy批准内置安全定义；不为探测执行外部effect。独立聊天的 B/C/D NOT_APPLICABLE 由创建协议 STANDALONE_CHAT 及原 owner config证明；Mission 模式缺来源直接拒绝。

## 3. 创建、提交与请求事务（Q08/Q09）

### 3.1 原创建顺序的明确改造

当前 kernel.start 后 facade 才建binding，不能假定一个新 with transaction 会包住所有动作。本次选择**持久创建意图＋禁止提前drive＋最终binding原子化**：

```text
锁外：核验caller/profile/model/embedding/capabilities，计算完整creation body hash
T-create-0 execution UOW：原batch/creation_key去重；预分配系统Agent/Run IDs；
  写 arp_creation_intents(PREPARED, profile hash, IDs, original receipt)
锁外：原kernel注册同Run，defer_drive=True；不得Provider/Tool/embedding
T-create-1 execution UOW：同connection调用原binding_locked、ARP marker、session CREATING、
  adoption=1、输入队列初始字段、原batch/result receipt；creation intent BOUND
事务后：Session文件初始化→激活来源复核→ACTIVE；原kernel允许drive
```

`start_base_agent_run` 的新内部 `defer_drive` 默认不改变legacy。没有完整BOUND＋ACTIVE的新Run不得被普通startup scan拾取执行。崩溃恢复读creation intent原IDs完成注册/绑定，不重新生成；失败仍保留明确ABORTED/原Run关闭的记录，不宣称整个跨调用是单事务。

同creation_key的完整body包含config/profile/owner/mode/embedding及稳定caller/scope身份；当前权限评价receipt/时间/健康采样不重新混入命令hash，它们作为独立准入来源绑定；同key异profile冲突。create_many先整批验证、预分配、写原batch；任一kernel注册失败可以留下有来源的未激活Run，但对外没有部分成功；最终所有binding同事务发布。delegate经过原委派ticket，同样调用这条工厂，不另造子Agent创建路径。

### 3.2 原请求的准备/冻结/使用

1. 从原request key查询冻结结果；存在即校验来源后恢复，不重组。
2. 新ordinal：捕获current sources/profile/adoption/Journal highwater/catalogue witnesses；纯composer生成候选。
3. `AgentProviderWire` 恢复真实tool_calls、必要opaque provider片段，执行最终renderer/serializer；在该最终对象上计量。
4. 原Orch预算事务先预留真实资金并产生durable intent；原SDK UOW写原Provider request准备、ARP ContextManifest、原reserve关联、blob root及事件。跨两库沿原预算导入协议，不声称一笔ACID；SDK失败后由原holder核对/释放尚未交接的hold。
5. handoff前重新核对当前真实read-set/权限/registry/Session gate与**同一最终wire hash**，实际Provider调用仍由原coordinator执行。Exposure在原input receipt确认实际使用后导入；prepare不等于看过。
6. 如果TokenMeter在restore_tool_calls之前运行，只能作为装填估算；不得据此前hash冻结最终request。

### 3.3 不修改旧请求来应用新设置

逻辑状态是原call ledger的投影，不新增第二份状态：

| 原事实阶段 | 设置普通更新 | 来源/工具/权限撤销、output实际需求改变 |
|---|---|---|
| 尚未prepare | 下一次使用新policy | 重新取得合法输入 |
| PREPARED/RESERVED/WAITING_FOR_SLOT 且无handoff | 保留原policy；UI显示下一请求生效 | 原coordinator永久取消未发送项；原 no-send final receipt＋reserve结算后才分配新ordinal |
| HANDED_OFF/UNKNOWN | 原请求继续核对 | 不改payload/ID，不用新ordinal自动替代；停止后续动作，保留原结果/费用 |
| SUCCEEDED | 按原input保存/采用检查 | 结果可因当前性不可用，但费用/原文不抹除 |
| FAILED | 原失败按政策处理 | 仅明确可重试来源开新调用；来源变化不把UNKNOWN变FAILED |

cancel-unsent的线性化点在原call事务中，与handoff抢同一个row version/fence。输给handoff则必须走UNKNOWN/原核对；不能凭“没看见网络响应”释放预算。允许替代记录 `supersedes_request_ref`，新ordinal只在原allocator分配；不重用 `(turn_id,ordinal)`。不改变public PlanningDecision。

## 4. Context 与真实计量（Q05/Q10）

A系统/角色；B目标/Task/Method；C局部图和InputManifest；D知识/冲突/反馈；E能力/Skill/工具定义；F本Session召回；G最近对话。当前输入锚点、未闭合工具链和必要opaque片段独立必需。

设 `I=最终公开wire输入`、`P=服务端仍会计入上下文的prior reasoning/output保留`、`O=本次实际输出上限`、`S=安全边际`、`H=用户选择的额外空白`。

```text
U_wire = min(C_config - P - O - S - H,
             C_model_combined - P - O - S - H [若适用],
             C_model_input - S - H - (P if input_limit_scope=WIRE_PLUS_PRIOR else 0))
G_capacity = U_wire - A_E_actual - F_actual
```

P来自**现有**DeepSeek计量/原request上下文与ProviderBudgetGuard的prior_output记录。计量adapter明确报告旧estimate是否已含P，归一化一次；不得先从旧estimate带P再重复扣P。计费 allowance 与Context容量分别记录；成本预留不等于输入tokens。

第一条支持路径是当前批准的DeepSeek部署＋`DeepSeekV41TokenEstimator`，不通过model字符串猜服务协议。ResourceBinding固定endpoint protocol、tokenizer文件manifest、renderer、serializer、counter代码hash及原profile。HF adapter只在实际服务模板等价性有证据时启用；UpperBoundTokenizer bytes/2不得自行签CERTIFIED。公开wire可EXACT；P可以是原协议保守预留，不能由此声称隐藏内容exact。

仅本期可计量的TEXT＋工具Schema＋支持的JSON response format；image/audio或未支持tool schema模式具名拒绝。S/H不能补偿未知renderer。O越部署上限拒绝；需要更大O在新请求使用，冻结后自动增大禁止。streaming不改变输入编码；partial stream仍由原coordinator管理。

512K=524288是用户上限，不是已部署能力。缺资源可继续编写adapter但不能启用该部署；资源盘点字段及验证入口在resources/DEPENDENCIES.md，不要求下载新聊天模型或提供凭据。

### 4.1 长Turn与N的唯一算法

`ProtocolGroupSnapshot`明确USER_ANCHOR/CLOSED_TOOL/OPEN_TAIL/TERMINAL_ANSWER/OPAQUE_REQUIRED/HISTORY_MESSAGE。原Journal的append ID、tool call ID、真实tool result/end receipt生产闭合状态；不是模型填写closed。闭组中每个call恰好匹配一份有效result，真实多tool顺序由call_id恢复；隐藏Journal记录需留范围/完整性证明，不用seq连续推断无缺项。

1. 固定highwater；索引与装填使用这一版本。mandatory组是当前原始输入、open tail、Provider要求的opaque片段，**不是整个活跃Turn**。
2. A–E hard全部保留；optional按policy section priority、正式关联距离/来源优先级、stable ID排序；没有未定义的LLM relevance分数。各section软上限/fixed_soft_max只约束optional，不截断hard。
3. 对排除mandatory后的可选闭组取最近连续后缀，保护recent_min容量。遇一个放不下的组停止，不跳过去。
4. 一次F查询，过滤mandatory/受保护组、scope/currentness、重叠span；按排名装入recall ceiling。
5. 向前扩G；试放旧组前删除与其重叠的F，不回填F；放不下便停止。mandatory锚点可早于后缀，原chronological次序render。历史召回是data，不伪装新用户/System命令。
6. `N_complete_turns = count(t != current_turn AND t.completed AND all(t.required_group_ids in selected_groups))`。一个历史Turn只留部分组不计数；当前Turn不计数；不累加group.complete_turns。原文与A–E重复但实际重复发送的tokens仍照计，不虚扣。
7. bounded group enumeration先保证mandatory完整，再反向最多8192个optional组；未读更旧组标`N_count_complete=false/omitted_optional_groups`，不影响其在全历史检索中的资格。
8. 最终完整计量后最多256次单向缩减：optional F→最旧optional G→optional A–E；无进展或达限返回CONTEXT_ASSEMBLY_LIMIT，不能返回超限请求。group catalogue不完整不能宣传N是全历史最大值。

参考`reference.runtime_rules.allocate`与相关反例验证长Turn/完整N；真实token计量不由参考整数charge代替。

## 5. 临时索引、检索、分页（Q02/Q11/Q12）

### 5.1 原文不可改；派生版本可共存

原Journal同record ID同hash不可改。组闭合的source hash是其原始record refs有序摘要；同closed group ID不同source hash是GROUP_HASH_CONFLICT，不视为新事实。

**view policy、chunker、embedding变化通过新的index_generation隔离。**Session control generation用于执行/销毁fence，不等同index_generation。分区表PK/FK均显式包含index_generation；不同generation的相同span可共存；同generation同source/span/codec异内容冲突。

新generation在分区构建、READY后，在FileGuard保护的原execution UOW发布active generation引用。分区没有第二个可写active pointer。中央发布前崩溃只留未采用READY数据；重送验证同publish receipt后采用。旧版本直到原cursor/reader销毁或TTL结束才清理。安全撤销使旧读取立即失效；普通chunker更新在新代发布前继续读旧代。

`IndexSnapshot`由query开始时实际读取central generation与partition upper_commit生成，存入分区query_snapshots；包含Journal highwater、完整预期组集合hash、已索引集合hash、index上界、view/embedding/chunker和授权readset。追加索引不改变旧snapshot；generation切换/撤权/close-destroy使cursor失效。不能用随机UUID而不保存对应快照。

### 5.2 唯一分页语义

- SearchRequest/SearchPage，HistoryReadRequest/HistoryReadPage是正式DTO；不能再返回裸RecallItem数组。
- Search先分批扫描冻结索引，每页阶段SCANNING返回**空items＋进度＋next_cursor**；这不是零命中。扫描完才固定各路topK与最终RRF结果，RESULTS分页只append固定排名。不会把局部topK冒充全局，也不会要求客户端猜哪些旧结果要撤销。
- Search的COMPLETE仅指已声明算法完成并且索引覆盖预期闭组、所需向量；缺索引时可RESULTS但index_coverage=PARTIAL。无next_cursor不代表全Journal完备，可能只完成已冻结的部分索引；下一新query须等待catch-up。
- `has_more == (next_cursor != null)`；cursor绑定实际owner/session/root/control generation/index generation/query hash/exclusions/limits/purpose/authority readset/expiry。服务端随机256bit token，数据库存token hash与不可变page/next定位；不是裸base64 offset。重复token回同页内容，仍检查当前访问权。
- Token只用于分页，不授予权限。模型search、模型read、管理read、manifest详情、catalogue分别有purpose，不能互用。
- History按原Journal seq升序，单个超大record按合法UTF-8字节边界分页；每片携带完整record ref、slice hash、byte offsets/total。零长度正文合法，hidden range明确记录；不把被授权隐藏的seq当丢记录。seq_to>highwater拒绝而非悄截。
- 过期CURSOR_EXPIRED；撤权CURSOR_SCOPE_MISMATCH；重建发布CURSOR_STALE；destroy SESSION_PURGED。纯Turn cancel停止该Turn的模型工具cursor，管理读不因此抹去历史。

### 5.3 可复现排名和全历史资源边界

详细算法见 CONTEXT-SEARCH.md。固定query：UTF-8≤4096字符，精确`seq 12/#12/record_id`先查；literal words经NFKC casefold分词；trigram长度≥3才使用；短中文用有界instr/逐页substring scan。所有MATCH由字面量escape，不执行模型FTS表达式。

为避免FTS全库统计因追加变化影响分页，**本版本不用可变全库BM25**：各冻结chunk按精确命中、唯一word命中数、trigram子串命中数、float32向量cosine评分；FTS只作候选加速，不决定可变排名。每路保留top max_candidates（默认128，上限512），全合并→固定权重RRF→stable pin/span tie-break→同源同provenance/权限范围合并→重新计量。完整性是此已声明channel-topK算法，不声称枚举所有相关项。

每page最多256向量行/≤8MiB内存，query总deadline默认30s（上限120s），单page max_retrieval_ms默认500ms从资源等待/embedding/SQL/读源/封装全部计时；超限保留进度不永久锁库。cursor最多16、TTL15min；超总deadline返回INDEX_SCAN_LIMIT不说无历史。语义query向量只算一次并有真实调用receipt。

chunk默认512 embedding tokens、64 overlap、UTF-8≤16KiB，以先达限者切分；不跨不可信来源边界。向量先max-abs缩放再L2，编码little-endian f32后重新检查finite/nonzero；1e308不会归一成全零。runtime限额覆盖DB/WAL/SHM、query pages、old/new generations和staging/trash。ENOSPC回滚index、保留Journal、背压；不能删唯一原文。

## 6. Embedding与持久工作（Q06/Q13）

生产默认适配路径为**获准本地BGE-M3 dense，SentenceTransformer，1024维，CPU作为可用基线**；已批准且满足同合同的本地/服务adapter可显式绑定，不自动替换用户资源。无模型文件不自动下载。模型卡只提供资源候选信息，不证明本机已安装。[W4]

实例owner是原Runtime装配的共享`EmbeddingResourcePool`，key=实际资源fingerprint；多个Agent共享模型对象但不共享私有索引。`local_files_only=True/trust_remote_code=False`，从实际模型manifest记录制品/库锁/tokenizer。原同步EmbeddingPort包装在有界worker执行器，网络/推理不持DB或FileGuard。

所有INDEX/QUERY调用走原execution invocation/usage合同的**embedding purpose adapter**，不伪造chat response、不新建收费账本。缓存结果引用原receipt。NO_PROVIDER_CHARGE明确收费0并记录实际耗时/输入数量；tokens无法测则null而非0。METERED按实际服务usage，不猜价格。

| activation required | lexical allowed | 首次缺真实embedding | 激活后资源故障 |
|---|---|---|---|
| true | 任意 | 拒绝ARP创建，环境依赖 | false→语义查询UNAVAILABLE；true→显式LEXICAL_ONLY |
| false | true | ARP创建可带LEXICAL_ONLY标记 | 保留索引，显式降级 |
| false | false | 配置非法 | 不适用 |

默认required=true。资源fingerprint变化必须建新index generation，不混向量。query服务故障即使已有vectors也不能用假query vector。同步thread取消/timeout不能证明计算停止；原call保持UNKNOWN/实际running，迟到结果先入原费用与结果账本，再按generation判断是否可入索引。

保留job种类只有INDEX/PURGE/BIND_IMPORT；SUMMARY本版不创建、不启用，结构性指针不是摘要模型调用。每种完整payload在Schema，幂等key/事务/lease/重试见 JOBS-LIFECYCLE.md。原runtime startup/tick唯一驱动，单tick领取≤8/每session≤2，轮转；不增加守护Agent。

## 7. 生命周期、锁、删除（Q14/Q19）

- Turn完成回IDLE；UI关闭/进程shutdown不关闭逻辑Agent。
- `close(drain)`：原Agent closing、拒新submit；Session仍ACTIVE供已接受Turn在原预算内正常结束；到原deadline仍未完成走原cancel/reconcile。所有Turn收敛后启动destroy。
- `cancel_turn`：只取消该Turn后续交接，结果/usage/reconcile仍接收，不销毁Session。
- `destroy`：原授权命令先关闭/取消原Agent执行、Session DRAINING并提升control generation；停止新Provider/tool/index写；已发请求只收集/核对，不能再要求一轮模型才能结束cancel。管理诊断与精确账本核对允许，普通历史/模型cursor拒绝。
- Disposal必须7类完整集合：Turns、Calls、UNKNOWN、Imports、Jobs、TempRoots、Handles，每类来源/水位/hash/reader receipt在CompleteSessionDisposal。持FileGuard证明本partition无活句柄，不用lease expired默认计0；其余真实账本完整读。空集合必须有complete证明。
- File操作以系统session/root/marker定位，不接受任意删除路径。PURGING先rename到带原session+generation+destroy receipt的trash，再unlinkDB/WAL/SHM；重启从中央记录和marker定位，目标不存在仅在有已rename/delete receipt时可幂等完成。Windows busy保留PURGING，退避不强删，15min报警后提供同destroy命令retry；不换generation掩盖。

**全局锁序：catalogue owner guard（需时）→ orchestrator UOW（需时）→ SessionFileGuard（需时）→ execution UOW → partition SQLite。**任何代码不得在持execution事务后等待FileGuard，不得在持partition后读编排。原Orch→SDK预算锁序保持。纯检索先锁外取得编排证书，再FileGuard→短execution复核→partition页扫描，释放后原handoff再次查权限；外部变化以本机权威已导入事实为边界，不宣称阻止已发送请求。

embedding/模型/脚本不持上述任何锁。vector每页释放partition和guard，cursor存快照而非活SQLite游标。destroy与index最终写采用同一guard；锁外迟到embedding只结算不复活分区。

同root索引损坏：QUARANTINED→认证rebuild→新gen→原root gate许可后ACTIVE；备份还原新root先Assurance隔离/当前重新授权，不能以“index rebuild”恢复旧执行权。

## 8. Capability/Provider/Tool统一目录（Q15）

`ProviderDefinition`固定kind、input/output schemas、实现artifact、capabilities、整数selection_priority和批准priority policy。Deployment是实际namespace/实现、可用语义、平台、health receipt、TTL、semantic epoch；不是模型猜的描述。

namespace=`realm_id/owner_scope/project_id-or-user`，由原认证Host配置签发。每realm原managed catalogue writer为唯一owner，其durable后端使用指定owner executionDB的arp_catalog_*；已有同语义原Store可用精确adapter取代这些表，不双写独立active状态。各execution pool只写`arp_catalogue_mounts`来源绑定，读owner，不复制批准状态。owner不可达不能用旧缓存执行；旧Context仍可诊断显示。

新install/register/trial/admit/suspend/resume/retire/deployment-change由该原Host writer执行，事务同写definition/activation/semantic_epoch/原receipt。health probe锁外，只在健康类别改变、权限/配置/能力变动时提升semantic_epoch；相同HEALTHY的新采样仅推进health_revision/expiry，准备请求可以用当前fresh健康结果继续，不因每次probe永久失效。任何已撤销binding仍拒绝。

选择：hard filter(输入schema/所需能力/平台/权限/健康/精确依赖)→按批准priority降序→provider exact id/revision/hash稳定序。不把模型confidence当授权；LLM建议只能在合格集合选。部署profile实际source不同则不冒充等价。

ToolExposure复用原runtime_catalog；动态model name为完整身份hash稳定名，禁止同名异ref覆盖。模型必须实际看到该request的snapshot schema才能执行，工具发现返回不等于曝光。ToolGateway再次核验当前授权、role、Skill要求与TaskScope交集；无凭据进模型。READ、SANDBOX、外部Effect取真实实现与policy，不按Python/browser名字分类。外部Effect原OPS链，Skill/script/workflow不例外。

DeploymentDefinition的Pin仅覆盖静态部署合同；健康和时效在独立DeploymentHealthSnapshot中，见FIELD-CONTRACTS §7。管理Deployment是联合视图，不能被当成一个每次probe都变的定义Pin。

## 9. Skill完整链（Q16/Q17）

1. Host原项目/全局skill_install负责接收、核hash、安全解包；SDK冻结skill_resolver与ARP读同一registry。旧已安装资源要由真实现有准入/来源适配；无证明的旧资源标QUARANTINED，UI显示原因，不另藏到平行目录。
2. 原生skill.json严格Schema；SKILL.md-only按Agent Skills frontmatter解析成**新的INSTRUCTIONS候选**，不自动推断可执行script。标准的布局/元数据不是本平台执行授权。[W3]
3. 安全YAML使用锁环境中PyYAML SafeLoader的拒重复key subclass；禁止tags/anchors/aliases/merge keys，不解析任意对象。允许name/description/license/compatibility/metadata/allowed-tools精确类型；unknown执行字段拒绝。name用平台可移植ASCII子集（明确是ARP限制，不冒称标准限制）；导入raw文件不改写。
4. 包≤1024文件、单文件≤8MiB、解压总≤64MiB、SKILL.md≤128KiB。拒路径逃逸、symlink、hardlink、casefold碰撞、Windows保留名/尾点空格、未声明payload；全部hash核对。skill.json不列入自包含files hash；三hash域沿原bundle规则。
5. 安装时解析依赖锁。未安装依赖保留具名unresolved在QUARANTINED，禁止TRIAL/ADMITTED。全部依赖精确ref；深度≤16、节点≤128，diamond同logical id不同版本冲突，不自动选latest。升级形成新lock版本；撤销阻断新load/call不改变旧事实。
6. `begin_skill_trial`由原认证管理入口签隔离eval scope，只允许policy白名单tools、独立workspace、无生产凭据；评估通过原AgentBridge/真实reserve/receipt。INSTRUCTIONS、SCRIPT、WORKFLOW各自验输入输出与边界，不用文件存在当通过。
7. ADMIT检查original evaluation Acceptance+lock+scope+policy+当前调用者；SUSPEND即时拒新调用；RESUME需同版本仍有效eval/依赖，否则回TRIAL；RETIRED不复活。命令重送按原receipt，不另签两份准入。
8. load/execute分别有`SkillUse(mode)`和原工具call绑定。load重复同file/range只在E保留一份；正文进入真实后续request才记exposure。execute的INSTRUCTIONS返回内容，不假装脚本运行；SCRIPT/WORKFLOW调用原executor，不建SkillRun。
9. SCRIPT使用approved runner、固定argv token，仅`{input_json}/{output_json}`整token替换、shell=False、环境白名单、输出schema和字节限额。exit0无output/无效JSON→失败；超时停止原process tree并核对真实状态；stdout/stderr上限原receipt/artifact，不能截断后报成功。WORKFLOW没部署则WORKFLOW_UNAVAILABLE，不逼实施者重写引擎。
10. 目录用**有界summary＋受权details/files分页**；800文件不会塞进64KiB单项。模型summary不含credential_ref_name/endpoint_namespace/绝对路径；管理页也要当前权限，非秘密不等于公开。完整bundle通过原获准artifact下载，不经目录JSON返回。

## 10. Host往返、事件、错误（Q01/Q03/Q18）

详细DTO、示例与逐verb映射在HOST-DTOS.md；Schema是唯一字段源。内部Pin允许类型按字段收窄，11种旧缺失kind有真实resolver合同。revision=0只允许原不可变无version对象，且hash覆盖精确原body；mutable policy/activation不能用0。

ContextSettingsView首次无request也返回当前effective policy/ref、adoption=1、generation和真实activation receipt。settings_update同时核subject AgentId→Session归属、expected_revision==payload.expected_adoption_revision与expected policy pin。policy body由认证policy_submit先CAS冻结并批准；UPDATE返回完整CommandReceiptView，可用agent_command_receipt_get重放查询。新设置下一未冻结request生效，不篡改旧manifest。

Host `view_revision`：settings=adoption revision；session=session row_version；catalogue=semantic epoch；context历史=冻结RuntimeContextPrepared eventseq上界；单manifest=其原RuntimeContextPrepared eventseq（身份仍context pin）；command receipt=原receipt版本。并发控制不能把这些互换。

事件字典指定7类事件、writer/UOW/dedupe/eventseq与body Schema。原runtime event append是唯一源，ARP只存同事务的body binding，不发第二路逻辑事件。跨编排Exposure经BIND_IMPORT；原call先存、导入失败可恢复。host从原eventseq重连，scope不可见只诊断metadata。未接回放handler时不得宣称全Replay。

Error.code由error-catalogue.json枚举：stage/retry/是否允许新ID/实际费用/HTTP/WS映射一致。UNKNOWN不是FAILED；NOT_REQUESTED只用于本次明确未发起检索（无模型请求则summary为null）；COMPLETE_EMPTY只由完整读取推导；SCANNING空页不称无历史。

## 11. 迁移、保留与GC（Q19）

本包execution_additive.sql属于**execution runner**，session_partition.sql属于每个新派生分区；不向orchestrator注册第二份ARP业务表。编排库TG25不能推出execution下一号26。AS/ARP集成人分别盘点两个数据库的runner/descriptor/checksum，在当前实际下一未占用号登记，保留旧bytes。

每个原execution writer/recovery连接与partition连接都在事务前设置并读回`foreign_keys=ON/recursive_triggers=ON`，失败SQL_PRAGMA_UNSUPPORTED。同时关键identity增加BEFORE INSERT重复键拒绝，不能靠REPLACE改写不可变记录；业务代码仍禁止OR REPLACE。备份使用SQLite backup而非复制活WAL；恢复保持Assurance新root隔离。

完整真实parent schema上测试新建/升级/全部triggers/FK/rollback/checksum/旧行不变/reopen。本包parent fixture只测SQL，不宣称产品migration已过。原分区若是ARP1.0，视为可重建派生数据：保留旧central/Journal，生成新partition v2及新generation，不就地改旧checksum。

Session临时DB删除不等于central审计无限保留：retention分开管审计最小metadata（原批准政策期限）、CAS正文、Journal、profile及root引用。正式Review仍依赖正文时先真实copy/pin/hash/import receipt转移；仅正式CAS审计pin已独立不阻止删索引。隐私擦除走原authenticated retention服务，按精确immutable对象body hash签`arp_retention_permits`；DELETE guard只在终态、无未决引用并有同连接验证permit时允许删内容binding。禁止反射式table删除、删原效果/费用凭证或用PURGED推断可删所有源。低层hash/minimal tombstone按原policy留存，未给retention资源的记录不自动放行。

## 12. 实施、验收与实际边界（Q20）

主代理单一integration owner负责 runtime/assembly/wire/UOW/Host现有热文件；未读后继Assurance全部字节时先冻结接口交接，不能覆盖它。普通命名/已有等价adapter/迁移编号自行处理，不把NEW尚未写成新blocker。

施工单元：RP-A合同/源映射/原UOW；RP-B Context/index/meter；RP-C registry/Skill/Tool；RP-D lifecycle/Host/GC；RP-E BODY_WIRED之后统一验收。BODY_WIRED是所有真实caller贯通，不能用空producer凑字段。只有真正不可同时满足的语义才集中返回裁定，其他编码继续。

保留原60场景与16mutation（精确原文在inputs及当前映射），新增Q01–Q20每项至少一个决定性集成反例，不为固定60删要求。BW05映射case:K01/K02；MU10映射真实permissions交集测试。所有R编号显式区分seam:Rxx与case:Rxx。

真实模型沿LM1长历史召回/LM2技能工具/LM3跨Agent独立/LM4关闭恢复四类×3；输入、oracle、故障点与预算预登记。硬不变量12/12，每类至少2/3业务oracle、总≥10/12；失败全部保留不重抽。512K能力与计量/质量单独报告。原应用profile不改，协议故障不能删样本。

macOS在隔离Host/venv/userdata/端口上验原生链，Tauri自管backend/vite；候选wheel和实际import hash需一致。Linux只在用户已有获准运行地点执行，未登记host与制品为ENVIRONMENT_DEPENDENCY，不擅自创建远端/容器；Windows未测PENDING。平台pending不伪称通过；Mac功能完成可交付默认ON，不据此宣称全平台完成。

## 13. 交付事实与开工判定

本包是规格与参考附件，不是生产补丁。当前源码映射来自handoff的静态报告；真实父候选、后继Assurance字节、tokenizer/embedding安装、库版本、Host原生结果保留待核验。spec中的明确NEW接口可开始编写，无须先跑完未来SDK测试。

轻量检查：
```bash
PY="$SDK/.venv/bin/python"
"$PY" -B "$KIT/tools/verify_delivery.py" --root "$KIT"
"$PY" -B "$KIT/tools/check_plan.py" --root "$KIT"
```
两条不安装依赖、不打开产品库、不运行模型。reference tests只说明合同oracle，实施前不批量跑生产测试。实际本轮参考结果见VALIDATION.md；不继承旧116 PASS。

### 外部依据

[W1] SQLite FTS5/trigram/索引维护：https://www.sqlite.org/fts5.html
[W2] SQLite PRAGMA 与 Python事务：https://www.sqlite.org/pragma.html#pragma_recursive_triggers ，https://docs.python.org/3/library/sqlite3.html
[W3] Agent Skills规范：https://agentskills.io/specification （本平台执行/权限字段是扩展，不声称标准保证）
[W4] BGE-M3官方模型卡与SentenceTransformer：https://huggingface.co/BAAI/bge-m3 ，https://www.sbert.net/docs/package_reference/sentence_transformer/model.html
这些资料仅支持第三方行为，不替代本机资源证明，不覆盖用户已批准profile。
