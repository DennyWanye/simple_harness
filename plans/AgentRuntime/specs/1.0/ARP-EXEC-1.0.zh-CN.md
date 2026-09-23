# SimpleHarness Agent Runtime Plane：完整代码级实施计划

**编号：ARP-EXEC-1.0｜2026-09-22｜Agent Runtime＋Context＋Capability＋Skill＋Tool**

交付入口是本文件和同目录完整 ZIP 内容。本文是新增施工规格，不是当前产品实现证明。`inputs/` 是追踪原件，不复制其中的旧缺口、默认配置或已被补遗替代的行为。

## 0. 范围、裁定优先级与施工纪律

### 0.1 必须保留的用户目标

1. LLM 负责理解、推演和选择；Runtime 负责身份、权限、协议、预算、持久执行、验收与恢复。不再自研一个竞争的智能总路由器。
2. Context 最大长度可配置，例如 **512K=524288 tokens**（UI 必须显示确切整数；不是声明当前模型支持512K）。A–E＋实际 session recall＋最近连续对话＋预留。N 是剩余容量的计算结果，不是固定轮数。
3. 每个**逻辑 AgentSession**有独立临时向量数据库；历史先进入已有 Journal，索引是派生数据。正式销毁时删除临时数据库和缓存；进程崩溃、AgentTurn结束、关闭UI不等于销毁。
4. 单主Agent、可创建多个子Agent、独立Verifier；统一BaseAgent执行内核。模型无关 Task→Capability→Provider→Execution→Verification。
5. Workflow 属于工具/能力执行层，不是与Agent平级的业务控制器。Skill是版本化可复用配方，是否可信由准入与当前用途决定，不因存在SKILL.md就等于已验证。
6. 当前不接入用户长期记忆；不重开HTN/TaskGraph/Assurance/OCC/OPS设计。NanoJev/Shadow/PR-7不纳入本交付验收。保留应用当前获准模型配置，实施/核验子代理沿会话GPT-5.6约束，不擅自升级路由。
7. 既有dirty SDK/Host不reset/clean/rebase/pull/cherry-pick覆盖。代码与跨层主体接线先完成；期间只对阻塞做最小静态/定点检查。BODY_WIRED后统一验收，参考包测试不是编码期间反复攒分的步骤。
8. 完整实现、验收通过后，**新创建Agent默认使用本profile**，随同交付默认ON；既有运行Agent保持原绑定，不偷偷切换。TaskGraph等未完成门禁不会因此解除。

### 0.2 本轮依据的三种层级

- **用户约定**：本轮及紧邻的Context讨论；上述预算公式、独立session临时索引、ZIP交付。
- **当前合同依据**：ASSURANCE-EXEC-1.1、Operation Completion、OPS及HTN/TaskGraph原件。Assurance的scope、Exposure、Validity、restore quarantine、主体先行与最终验收继续使用。
- **参考代码证据**：远端`5ac3f058…`中BaseAgent、AgentRuntimePorts、ContextComposer、SessionRetriever、execution/sqlite/base_agent/schema。远端并不包含用户当前dirty候选，**不要求回退、不拿它宣称当前功能缺失**。参考已读：BaseAgent有持久身份/submit/close；Composer有完整协议组与超限拒绝；Retriever有scope和混合检索；Ports有Tokenizer/Embedding/Authorization/ProviderAdmission。优先扩展它们。

本地实施身份仍是报告的`simple-harness-sdk-h1h-impl`、`codex/h1h-impl`、`102ad3d…`＋真实dirty字节。首次集成盘点在`implementation/source-map.json`填actual_path/symbol/file_hash，不能用AST存在当行为PASS。普通命名/迁移号/同义接口映射由实施者直接处理并留证，不再让用户转问。主计划决定语义；同包Schema决定字段；SQL决定存储约束；算法实现与测试冲突必须修正，不能靠优先级静默放宽。

### 0.3 本次明确修正上一轮说明中的歧义

| 原来可能的误读 | 本计划的唯一解释 |
|---|---|
| Agent“消失”就删库 | 逻辑销毁完成才删；close/cancel/crash按§5处理，不丢恢复依据 |
| 一个AgentTurn只有一份Context | **每次Provider请求**一份ContextManifest，工具循环内多个请求分别冻结 |
| 一个Context数字对所有模型通用 | 实际部署能力与用户上限取约束交集；未证明的token上界不用于大窗口放行 |
| Python/浏览器都是read-only | 风险由受控工具实现、sandbox与网络权限确定，不按工具名字判断 |
| 只要加载Skill就能用所列全部工具 | Skill请求权限只收窄，不能授予；每次实际调用仍经过原当前权限门 |
| 加一个SkillRun内核 | SkillUse只是绑定/计量引用，执行和结果仍走原AgentTurn/Tool/Workflow账本 |
| 所有外部写动作必须待Mission完成 | 沿OPS：准备→真实意图→ACTION_PROPOSAL→物化→授权→handoff→效果验收 |
| 每份附录/新模块缺失都需再批准 | 新目标按合同实施；只有真正冲突才请求裁定，不因未写代码或未来测试未跑阻止开工 |

## 1. 完整架构与状态权威

```text
Host authenticated commands / existing Mission dispatch
    → original BaseAgent creation + runtime profile binding
    → one logical session partition
    → original Journal append / protocol-group closure
    → Capability/Tool/Skill exact snapshots + authorised A–E sources
    → ContextComposerV2: budget → retrieval → recent suffix → manifest
    → original provider request freeze / budget reservation / handoff
    → BaseAgent tool loop through original ToolGateway
        read-only or sandbox work → original execution receipt
        external effect → original Operation intent/Review/approval/recovery
    → raw result/import + actual exposure receipt
    → original Assurance scoped acceptance, NOT skill self-report
```

**三种数据库职责固定：**
- `orchestrator.db`：Task/Obligation/Plan/Requirements、Assurance、Operation来源与授权仍由原Commit拥有。
- 原`execution.db`：BaseAgent/turn/journal/call/reservation仍由原UOW拥有；本包`arp_*` side binding放在这个库，由现有runtime服务写，不增加另一个SQLite权威库。
- `runtime_root/sessions/<system-random-session-id>/index.sqlite3`：每session一个派生索引，包含chunk/FTS/vector/索引水位，不保存第二份Agent状态或效果账本。权限来自原execution会话绑定与实时authorizer，不来自DB文件名。

`CatalogStore` 是现有Capability/Tool/Skill registry的持久后端；若当前已有等价持久registry，应迁移到该原writer，**不并行维护第二个active registry**。同一namespace的注册、停用、准入更新同事务提高epoch。所有已冻结使用精确版本；当前撤销仍覆盖未来调用。

## 2. 角色、生命周期与对外入口

### 2.1 身份

`BaseAgentId`稳定；`AgentSessionId`是其当前工作记忆分区；`AgentTurnId`是一条接受输入；`ProviderRequestKey=(turn_id,provider_request_ordinal)`是一轮真实模型请求；`ToolCallId`使用原模型工具调用与原execution身份；`SkillUseId`引用原执行；`OperationId`仅原OPS签发。

默认同一个Agent同一时刻只允许一个处理中的AgentTurn和一个生成中Provider请求；输入可以排队。并行工作使用不同Agent；同一模型回复的多工具调用只在原并发/资源/冲突政策允许时并行，结果按call_id关联，不能按到达次序串错。

### 2.2 唯一production callers

1. Host现有authenticated Agent/会话命令入口或原AgentBridge调用`AgentRuntime.create`的实际位置，新增`RuntimePlaneService.bind_agent_profile_locked`（NEW目标），同原Agent创建UOW事务写profile/session CREATING。原AgentConfig bytes不改；同事务写独立`arp_agent_protocols`创建标记，profile side binding不可缺省成legacy。
2. 原`BaseAgent.submit`/signal input在Journal持久后返回receipt。**不从模型raw生成session/role/authority**。
3. 原BaseAgent execution调用Provider前，进入`prepare_provider_context(turn_id,ordinal)`；所有Planner、Manager、Worker、Verifier均在此入口，不能上层单独`await model()`。
4. Host固定命令：`agent_context_summary/history/settings_update`、`agent_session_destroy`、`agent_capabilities_list`、`agent_skills_list/install/admit/suspend`、`agent_tool_catalogue`。§14定义DTO。

模块目标`simple_harness/agents/runtime_plane.py`只装配/委托原Runtime；不复制Agent主循环。不具有Mission调用上下文的独立聊天Agent使用显式`owner_mode=STANDALONE_CHAT`，B/C/D由该模式给出`NOT_APPLICABLE`证书；Mission Agent缺这些来源必须拒绝，不用聊天模式兜底。

## 3. Context合同：保留用户公式并使其可执行

### 3.1 A–G精确内容和来源

| 区 | 内容 | 权威producer、缺失行为 |
|---|---|---|
| A | 系统行为合同、角色、输出协议 | 原AgentConfig/RoleTemplate精确版本；缺失拒绝 |
| B | 原要求、当前Task/Method/Obligation、完成准则 | 当前批准Requirements＋精确Task contract＋采用Method＋OCC scope；不能从摘要恢复权威 |
| C | 当前局部计划、ORDER/DATA、必需输入manifest/工作区定位 | 原TaskNetwork projection和InputManifest reader；只有DATA传产物，ORDER不传全部祖先文件 |
| D | 当前有效知识、冲突、失败、Verifier反馈 | 原Assurance/Validity＋Review/Failure reader；权限与当前用途检查后选材；旧结论保留历史标签 |
| E | 当前可用capability、已选skill说明、可见tool schemas、限制 | 本计划registry snapshot＋原授权/预算；真实密钥不进Context；工具schema全部计数 |
| F | 本AgentSession历史召回 | §4本session索引→原Journal精确回读；只作为来源标记数据 |
| G | 最近连续session交互 | 原Journal的完整语义协议组；从最新向前取连续后缀 |

A–E并非都硬固定，也不是每块可无限占窗。每块返回`ContextBlock(required,source_refs,view_ref,trust,budget_charge,validity_witness)`。硬要求/权限/当前输入不可丢；旧摘要、背景文档、额外skill说明是optional。完整资料可通过已获准的精确ref回读；但不能把必须解释给模型的根要求只藏成一个不知含义的ID。

### 3.2 唯一预算计算

设：用户配置总窗`C_cfg`；真实部署总窗`C_model_total`（若服务确实分离输入/输出则显式null）；真实输入上限`C_model_input`；本请求输出reserve `O`；token安全边际`S`；预留工作空间`H`。

```text
U = min(C_cfg-O-S-H,
        C_model_input-S-H,
        C_model_total-O-S-H  [仅不为null时])
RecentBudget = U - charge(A+B+C+D+E) - charge(F_actual)
N = RecentBudget内从最新往前能放入的最大连续完整交互组后缀对应的轮数
```

`O`必须在部署max_output内，并与实际Provider参数一致；隐藏推理/工具调用输出按对应Provider的正式限额语义处理，不能把其生成空间漏算。`H`为**有意保留的空白**，不是当前tool schema/body的替代计数，不许重复扣除。默认每次请求都重组Context，因此`H=0`；用户可显式调高以留工作余量。`S`是明确安全边际，不能弥补未知tokenizer或多模态未知成本。

示例policy在`examples/Policy.json`：C=524288，O=8192，S=2048，H=0，recent保护容量65536，recall上限98304。其余是可调的版本化政策，不声称是模型最佳长度。实际模型更小则显示effective budget及clamp原因，不假装真的发了512K。

### 3.3 TokenCounter与最终请求

新增目标`RequestTokenMeter.measure(rendered_request, model_limits) -> TokenReceipt(count,mode,counter_ref,renderer_hash,coverage)`；adapter复用现有TokenizerPort和Provider serialization。覆盖**全部messages、roles、tool declarations、response schema、media、provider framing**。

- mode只能EXACT或部署明确认证的CERTIFIED_UPPER_BOUND；没有可证计量→`TOKEN_COUNT_UNAVAILABLE`。
- 字符数/4不作安全上界。tokenizer/model不同、模板不同、不可计的image/audio拒绝该请求，不给一个任意小数继续。
- 分块`budget_charge`允许安全的可加上界；最终完整payload再计数。上界模式的N是按该上界可保证的后缀，不宣传真实token最优装填。
- 任何overflow先在未冻结的计划里删除optional召回，再删除最旧非必需recent组，再删除optional A–E并重组；不拆组、不删当前输入。每次只减少，最多256次重新计量；仍不收敛→`CONTEXT_ASSEMBLY_LIMIT`。**已冻结请求不可重组**。
- 更换模型或output cap必须新Provider请求身份、重新预算；原UNKNOWN请求仍按原runtime核对，不借此重发。

### 3.4 “轮”和完整协议组

`ContextGroup`从原`protocol_groups`获得：包含一条当前输入及其关联的assistant/tool_calls/tool_results；多工具响应须按每个call_id闭合。当前未结束的工具链是必需tail，它可以跨多次Provider请求。assistant真实签名/opaque reasoning片段如Provider协议需要，使用原ledger原样恢复，不索引隐式思维链。

`N_complete_turns`只计完整输入→已完成答复轮；另记录`recent_group_ids`和当前active group，不能把一次tool result当一轮；活跃长AgentTurn的锚点与已闭合组规则详附录D。配置recent_min是**容量保护**，不是硬凑足字数；组不可拆导致空余是正常。最新mandatory组本身大于floor但能放入总窗，扩大保护；超过整个U，显式`REQUIRED_CONTEXT_TOO_LARGE`。

### 3.5 无循环、可复现的选材算法

1. 捕获一次一致的Journal highwater、来源精确pins、授权/Assurance证书、registry/deployment版本和model limits。并发新输入留下一请求；不能边读边换最新状态。
2. 建A–E；保留hard块，optional按 `(required,section_policy_priority,source_relevance,stable_id)`确定序，软上限只裁optional。
3. 从最新完整组向前，在`max(required_tail_charge,recent_min)`内保护连续后缀。必需tail必须全保留；floor不足不代表可以挤掉当前输入。
4. 检索F时排除保护后缀；候选来自该session全部历史索引，不限最近2000条。一次查询取得有界候选集合与覆盖收据。
5. 按固定rank放F，同时满足recall_max与保护后缀；没相关命中允许真实空集，不填充随机记忆。
6. 用剩余容量依次向前扩G。每考虑一个旧组，**先从F删除与该组重叠的所有chunk/summary，再计算试放大小**。能放则加入；不能放则停止，不越过大组挑更旧的小组。
7. 删除重叠后释放的空间只继续扩G，**本轮不回填F**，避免召回与recent边界来回振荡。候选快照下一请求再更新。
8. 按A–E、历史F数据块、G原顺序render；不把历史USER_INPUT提升成新的用户命令，不把retrieved文本作为新system policy。
9. 最终完整计量＋source/权限/版本复核，生成ContextManifest；§6的原请求事务冻结。

纯token分配oracle在`reference/runtime_rules.py::allocate`，用正的token charge测试连续性、去重和边界；它不替代真实tokenizer，也不证明512K模型质量。

### 3.6 超大tool结果与溢出

ToolGateway先按原账本保存完整结果，`ToolResultPresenter`在**首次构造该工具消息之前**选择INLINE或REFERENCED。REFERENCED至少含原call_id、状态、artifact exact ref、hash、范围读取工具和已知关键结构；原文不从账本丢掉。涉及评审必须看到的字段不得摘要掉，引用不能假装已经曝光全文。

已经冻结发送的旧消息不改写；新请求允许使用有来源的派生历史视图，但必须重新记录该视图的hash/曝光范围。新用户输入超大时不擅自删改用户要求，返回具名overflow及可选的明确资料导入路径；模型内部摘要不是自动安全后备。

## 4. AgentSession临时记忆：原文、检索与索引

### 4.1 存储选择

**默认物理隔离**：每个AgentSession一个SQLite索引DB，含FTS5 word/trigram、chunk metadata与float32向量。保留原EmbeddingPort，索引查询默认CPU分块余弦点积（标准库参考，生产可复用已安装numpy），无需每Agent启动Qdrant/Milvus服务或安装不确定native扩展。

FTS仅是候选检索，不是真相。向量必须来自真实已配置embedding provider；embedding fingerprint包含模型制品/版本、维度、归一化、chunker与tokenizer。不能用随机向量或文本hash假装语义向量。语义模型选择沿Host当前获准embedding配置，缺配置记录ENVIRONMENT_DEPENDENCY；不擅自使用远端付费模型、不修改密钥。

已安装等价向量后端可复用`SessionVectorIndexPort`，但必须保留per-session物理namespace销毁、精确source hash、index lag、过滤/分页和模式声明。参考baseline不限制历史时间范围；数据量达限时加背压或返回PARTIAL，不能默默改成最近窗口搜索。

### 4.2 先Journal，再派生索引

- 原所有输入/assistant/tool/result先经`base_agent_session_journal_v1`及原UOW提交。
- 新schema不复制第二份session权威原文。session划定`agent_id＋journal_seq_from＋本次highwater`。
- 默认ON_CLOSED_GROUP：一个完整组闭合后立即排索引，平滑摊销embedding。最近组可以已建向量，但搜索时排除实际recent；满足“退出窗口后可以召回”，不等于只在退出瞬间才排工作。
- 创建闭合索引工作与原Journal append在同execution事务写`arp_jobs(kind=INDEX,semantic_key=group_id+source_hash+embedding_fp)`。现有runtime tick消费，不新增独立守护Agent。
- embedding在DB事务外，用原execution invocation/reservation计费。返回后核对session_id/generation/source_hash/model_fp，再写局部index DB；central job ACK引用真实embedding receipt。跨两个库没有原子承诺：局部先写后central ACK，重放以稳定chunk/vector键幂等；原Journal可重建缺失索引。

### 4.3 chunk与coverage

按原已获准、可索引的record内容分块：embedding tokenizer每chunk≤1024tokens，overlap128（必须0≤overlap<chunk）；UTF-8偏移须是合法字符边界。每chunk属于单record及完整group，记录speaker/provenance、source_hash、view_hash、起止偏移。工具原始二进制/密钥/未披露敏感字段不向量化；符合权限的文本抽取必须有抽取器版本和source ref。短句1个chunk；空文本闭合组可合法chunk_count=0。

chunk_id=原derive_id(session_id,record_id,source_hash,offsets,chunker_ref)。vectors唯一 `(chunk_id,embedding_fp)`。有限float、正确dim、非零norm；错误维度/NaN拒绝，不能降成近似可用。新embedding版本重新建索引；查询不能混算不同空间。

coverage按**连续闭合group的完整chunk/vector集合**推进，不使用`MAX(done_seq)`掩盖中间空洞。每轮搜索捕获source_highwater和index_snapshot。`COMPLETE`、`PARTIAL`、`LEXICAL_ONLY`、`UNAVAILABLE`明确返回；index落后但词面完整仍只宣称词面覆盖，不能宣称语义完整。

### 4.4 检索唯一算法

query由当前任务目标、最新合法用户输入、当前明确blocker/Verifier反馈生成；不读取模型隐藏推理。query有1024 embedding token上限，按这些字段优先级截取，记录query_hash和truncation，不把截取用于改变控制要求。

候选顺序：精确ID/seq→FTS words/trigram→同embedding_fp全部可用历史vector分页扫描→RRF→原Journal与source权限回读→预算装配。

默认每路top128，RRF常数60，weights exact=1/words=.6/trigram=.5/vector=.7；这些是版本化起点，不是质量保证。vector相似度阈值沿校准policy；未校准不声明“相关”事实。扫描page512；默认2s CPU/工作预算到达→PARTIAL，返回扫描范围和可续cursor，不能COMPLETE。分页offset绑定source/index/embedding/query指纹，源变则cursor失效。

**不再使用`search_window=2000`裁掉候选。**精确seq工具能读取该session的任何保留范围。模型不提交任意agent_id/session_id：工具context固定当前session。合法零命中附完整查询收据；SQL/embedding失败是具名degradation，不能回`[]`当没有历史。

### 4.5 临时记忆不是事实/命令

RAW_DIALOGUE、AGENT_CLAIM、USER_INPUT、TOOL_RESULT、VERIFIER_FEEDBACK保持来源。召回历史用户请求不重新授权当前动作；相似度不产生CURRENT Acceptance。材料读权/Assurance用途先核对；索引中旧敏感文本被撤权后，即便文件尚未GC也不能回给模型。元数据候选过滤与回读后最终披露两道门都要有。

Verifier只检索自己的session。它通过正式ReviewPackage/当前允许运行的只读工具取得Worker成果，不隐式搜索Worker的Journal。跨Agent共享只能用正式Artifact/Acceptance/Scope转交，不共享session DB。

## 5. 生命周期、销毁和重启

### 5.1 创建和恢复

`CREATING→ACTIVE→DRAINING→PURGING→PURGED`是**session资源清理状态**，不是新增Agent/Task状态机。QUARANTINED是恢复隔离/异常目录状态。

创建事务先写session身份和CREATING状态；CREATING本身就是持久待创建记录，原Runtime启动/tick扫描它，不新增不存在的CREATE job kind，随后在批准root下创建随机目录、metadata marker、session SQL，再验证标记并CAS到ACTIVE。中途崩溃启动扫描继续创建；已有不同marker目录拒绝；文件存在不当身份。所有查询`mode=rw`打开已存在DB，不能查询时自动创建丢失库。

进程崩溃恢复同一逻辑session/generation和原Journal；按原lease fencing取得执行权。索引丢失且session未销毁，可由真实create/rebuild命令从Journal重建，标REBUILDING/PARTIAL；不能从查询偷偷创建一个空库并报告COMPLETE。

### 5.2 关闭与删除的精确定义

- `BaseAgent.close()`沿原语义拒绝新输入、等待当前处理，不立即删历史。
- owner完成后调用`destroy_session(command)`；即使自动由owner生命周期触发，也要真实close receipt，不靠Python对象析构/弱引用。
- destroy事务先写DRAINING、generation+1、唯一delete command，并阻断新Provider请求、检索、skill load及index写入。晚到原call/raw/费用继续进入**原执行账本**，不能因临时区关闭丢掉事实。
- 只读reconcile与正式审阅的已冻结材料由原保留根读取，不通过关闭后的session检索API。

### 5.3 允许进入PURGING的唯一证明

`SessionDisposalReader.read_complete`返回同root/session的完整集合：活跃turn/call、UNKNOWN调用、待导入结果/费用、索引writer/read lease、仍指向临时区的正式pin/transfer。完整空集有来源/epoch，不用默认空tuple。

所有冻结请求、Review材料、产物已在原 durable request/CAS/Artifact/Assurance pin中，或同流程完成有回执的copy-and-pin后才删除临时衍生副本。新context本来就冻结在原请求存储，不应反向引用临时DB做唯一恢复来源。

收敛时先fence、取消排队index、停止活跃writer、关闭SQLite连接、checkpoint WAL；同文件系统rename整个受控目录到`trash/<session_id>.<destroy_id>`，再删除db/wal/shm/FTS/vector/queries/summaries/caches，最后CAS PURGED。Windows busy handle→PURGING重试，不填成功；未完成delete不能“重新ACTIVE”。路径必须resolve/nofollow校验marker与root、拒绝junction/symlink逃逸；不用模型/用户输入拼任意删除路径。

未知效果/费用阻止最终析构，UI显示DRAINING原因。自动重试有界：默认60s内指数1/2/4/8s后30s；超过15分钟转人工清理告警但保持PURGING/DRAINING，不抹除责任。它是重试政策，不是TTL自动删除活跃Agent。

### 5.4 用户所说“临时库消失”的范围

正式销毁成功后，该session SQLite索引/向量/全文衍生/私有query cache不再存在，session API返回SESSION_DESTROYED，不能在下一次搜索自动复活。

**原执行请求、费用、Formal Review/Artifact属于正式审计，不因销毁临时检索库删除。**原Journal仍按现有受保护保留/隐私删除政策处理，不能成为可隐式继续召回的“备用记忆”。需要彻底清除个人数据是另一条原retention权限流程；没有把本次“临时DB删除”冒充磁盘安全擦除/备份删除。

整个旧备份还原沿Assurance新root QUARANTINED；旧session不自动恢复读权/执行权。当前认证重新授权仅开放指定历史材料。继续新工作默认创建新Agent/session，显式转交获准资料；同root进程重启才是自动恢复原session。删除墓碑/撤权后的旧备份不能自行证明今天获准。

## 6. 冻结Context、实际曝光与跨库桥

### 6.1 freeze是每个Provider请求，不是submit整轮

新增`prepare_provider_context(turn_id,ordinal)`：先查原ProviderRequestKey，有已冻结记录直接返回原ContextManifest与payload，不重新检索。缺记录才读取来源、配额、能力、skill、工具与Journal，然后运行§3算法。

最终执行UOW事务：重读Agent lifecycle/generation、Journal读取终点、profile、registry epoch与原handoff authority；调用**原模型请求prepare/reserve locked路径**取得原request key；写`arp_context_requests`及必要blob roots与原event，事务commit。外部调用在事务外。需要改原函数时抽取locked内部函数，旧wrapper保持行为，不在wrapper内隐藏commit。

跨orchestrator源的证书不提供跨库ACID。原ProviderAdmissionPort/ToolAuthorization在真正交接时检查当前fence/authority；证书过期或read-set变化在未发送阶段重新准备新请求。不宣称撤权能撤回已经离开发送边界的包；UNKNOWN按原ledger，不换context重发。

### 6.2 Exposure不是“组装过”

只有原Provider invocation实际输入receipt/import能够指向请求hash、message IDs和具体source/view范围时，才形成`RuntimeContextExposed`/原AssuranceEvidenceDisclosed adapter。PREPARED仅表示已冻结，不宣称模型已看到。

历史F的ref标签与Assurance catalogue分工：runtime source-view映射给Assurance原label生成器，不另造不同ev编号。语义摘要只曝光摘要view，不声称全文已曝光；工具调用返回但下一次模型请求未包含该tool result，也不算曝光。保存用于审阅结论的最终input manifest关系。

### 6.3 预算、未知与重入

原Provider/Tool/embedding/evaluation调用都必须有真实reservation与usage归属。Session索引job用当前Agent原账户/维护配额；不是免费模型调用。无额度可继续原Journal写入但index PARTIAL，不能虚签向量。destroy不自动退UNKNOWN预留，费用晚到沿原import。

Pure编译器不调用模型/embedding、不扣预算。Query embedding同policy/model/session/root绑定缓存，大小256个LRU，session销毁清除；内容hash不赋予读取权。embedding请求UNKNOWN不通过新job ID不断重问。

## 7. Capability：最小注册与确定性选择

Capability定义“需要什么输入、输出与语义”；Provider定义实际实现/部署；Skill/Tool是实现种类，不反过来拥有任务责任。

`CapabilityRegistry.register_definition`安装不可变定义；`ProviderRegistry.bind_deployment`以当前受信系统caller绑定已部署adapter制品、namespace和凭据引用（不取模型字段），实际probe返回DeploymentSnapshot。每项分开记录registered/configured/reachable/healthy/compatible/authorized；声明支持不是本次成功。

`CapabilityResolver.resolve_requirement(subject,requirement_key,input_manifest)`：
1. 原Task/Operator明确CapabilityRequirement，读取真实注册版本；
2. 过滤输入输出schema、语义约束、平台、当前授权、预算、effect class和实际健康；
3. 单Provider直接选；多Provider按用户冻结priority→stable ID选，或由原Planner已批准选择ref，不新增层层LLM router；
4. 冻结CapabilityBinding及具体tool/skill/provider/version、原verification policy；
5. handoff复核当前部署/授权；缺能力返回CAPABILITY_UNAVAILABLE，不创建假Provider。

fallback只允许**尚未交接**且同合同/权限不变的新candidate选择；旧调用UNKNOWN不切Provider重发。模型更换重做tokenizer/Context/预算，不能继承旧ContextManifest。非LLM专用模型/控制器不强制生成LLM上下文，但仍保存typed input/真实执行/验收，未接入的硬件能力保持部署不可用。

## 8. Tool：声明、曝光、执行和结果

### 8.1 工具注册与动态加载

ToolDefinition含精确输入/输出schema、provider/implementation hash、effect class、scope、timeout/retry与结果限额。MCP只是已有ToolExecutorAdapter中的运输协议；server声称readOnly/idempotent不作为授权或不重复证明。多服务器同名用安装实例ID＋工具ID生成本地唯一名，不用不唯一server display name。

`tool.discover(query)`只列已获准目录摘要；`tool.expose(tool_ref)`验证真实grant并准备下一Provider请求的exact schema（不执行工具、不发权限）；全部model_name→tool_ref/schema/provider映射存在ToolSnapshot中。调用必须属于**产生该call的原请求快照**，当前撤销仍可拒绝。不按最新registry随意重绑同名工具。

模型native tool call的arguments只当输入，不能赋principal/session/authority；domain参数名可以叫scope，但绝不提升为系统scope。duplicate JSON keys/NaN/未知参数/错版本都走严格codec。

### 8.2 三条执行路径

- READ_ONLY：原ToolGateway→实时授权→原executor→真实receipt。只读可能外传数据，因此network/credential scope仍需要真实许可。
- SANDBOX_WRITE：原受控workspace executor；限制mount/path/网络/进程。测试、Python、Shell、浏览器操作按实际能力分类，不因名为compute就默认安全。
- EXTERNAL_EFFECT：**只提交候选或已认证来源下的原Operation intent**，沿OCC/OPS的T0→Review→T1→授权→handoff→T3；model不能凭Skill manifest/signature签发USER_COMMAND。返回PENDING_OPERATION与精确intent ref，不伪装原效果已经成功。

用户授权范围内L0/L1自动执行政策仍可复用，不新增逐文件人工审批。高风险动作审批规则沿原系统；本计划不统一降低/提高所有风险等级。

### 8.3 真实结果和动态schema

参数/工具版本被冻后，实际回执必须匹配call身份。错误消息不当成功数据；tool执行timeout可能UNKNOWN，不按UNKNOWN重新运行。网络工具不保证exactly-once。

`ToolResultView`返回SUCCEEDED/FAILED/UNKNOWN/PENDING_OPERATION、原receipt、inline/Referenced视图。大结果提前外置完整artifact，输入长度与外存限额分别检查。MCP schema变化导致下次catalogue epoch变动；旧call用旧实现仍受当前撤销门，不能更新到latest。

## 9. Skill：可复用执行配方，不是新Agent或新HTN

### 9.1 定义与封装

原生包：`skill.json`（本计划Skill schema）、`SKILL.md`（说明）、可选scripts/references/assets；兼容Agent Skills常见目录布局。`skill.json`是本平台执行元数据，不宣称外部标准原本包含全部字段。导入仅扫描/规范化/隔离，不执行script、不下载依赖、不自动注册生产工具。

SKILL.md-only输入使用专门导入器：调用项目既有安全YAML解析；没有safe YAML依赖时返回SKILL_IMPORT_FORMAT_UNAVAILABLE并在当前锁环境的依赖变更流程解决，不用eval/不做隐式pip。解析结果转为候选native manifest，缺输入/输出/权限/entrypoint需明确候选填写，不能以文本猜生产许可。执行模式有：
- INSTRUCTIONS：在当前Agent上下文按需加载说明，仍由原Agent loop执行；不创建SkillRun。
- SCRIPT：固定runner ref＋bundle内script path，原sandbox执行，typed JSON输入文件/argv数组，`shell=False`，无字符串拼shell；脚本无依赖安装权。
- WORKFLOW：固定**已注册工作流executor及immutable workflow_ref**，作为工具能力调用，不新建上层TaskGraph或业务Commit。如果部署没有这类executor，能力不可用；不为完成Skill模块重写Workflow引擎。

脚本/工作流内部工具请求仍带同一owner/skilluse/original call chain经过ToolGateway；未知副作用进入OPS。若现有workflow executor不能记录/恢复原子step identity和真实回执，该provider不准声明durable支持；一项workflow超时不能自动重跑其全部步骤。

### 9.2 文件与执行完整性

默认bundle≤64MiB、文件≤1024、单文件≤16MiB（policy可更严），禁止绝对路径/..、反斜线、NUL、Windows设备名、大小写/NFC冲突、symlink/hardlink、ZIP bomb与路径逃逸。规范化以索引manifest记录每相对文件的bytes/hash/role。ZIP整体hash与展开后的bundle hash不同：bundle hash=canonical排序的file records＋manifest执行字段；归档压缩差异不改变配方内容身份。

部署执行前以内容地址不可变目录/唯读mount读取，检查bundle exacthash，不能检查完再从可修改工作区执行。引用文件按需加载；scripts不会因在包内就获得Host权限。SKILL.md里的“忽略规则”是包内容，不改变A区控制指令。

### 9.3 生命周期与审批

QUARANTINED→TRIAL→ADMITTED→SUSPENDED/RETIRED，suspend后重新准入必须新当前权限与有效评估，RETIRED终态不复活。版本body不可改，同ID/version异body拒绝；更新产生新revision。

唯一入口是固定Principal的`SkillRegistryCommands.install/admit/suspend`；模型只有`skill.propose`生成候选artifact。TRIAL绑定测试scope无生产凭据；通过原评估Task＋TASK_CONTENT Review/Acceptance证明候选配方与测试结果（不新增第七种ReviewPurpose），`admit_skill_revision`验证exact bundle、依赖lock、评估集合、无critical失败和用户/政策批准，才更新registry。评估要求由已批准policy固定，不从“成功三次”推导所有用途可靠。

安装的依赖必须全部解析到精确ref，递归依赖检测cycle，最多16层/256节点；同schema版本不代表行为兼容。有效权限是用户/当前Task/role/session/skill及每tool/provider许可的交集，任一来源缺失拒绝。描述中的requested permissions不是grant。

### 9.4 Discovery/load/use

默认只加载有限的name/description catalogue；`skill.load`后按已获准文件返回Instructions和ref，计入E或实际tool result对应组，不能重复两处计入。加载是下一Provider请求的上下文变化，不给当前已冻结request补写字段。

`bind_skill_use`冻结bundle、input manifest、owner、当前dependency locks、tool snapshot、评估与权限引用。INSTRUCTIONS use引用当前AgentTurn，reservation_fact可为null并明确“没有另一个模型调用”；SCRIPT/WORKFLOW在原executor prepare得到真实call/ref/reserve后同execution事务写SkillUse，不能先编造reservation随后补齐。

结果只产生候选/receipt，必须由原Assurance按任务要求接受。Skill在其他任务成功不代表本任务成功。Workflow/trace抽象成Skill仍经过候选→隔离测试→独立审阅→准入，不自动把用户敏感session内容保存成全局Skill。

## 10. 完整源合同与唯一读写入口

`implementation/seams.json`逐项列R01–R32，`implementation/FIELD-CONTRACTS.md`解释全部contract字段的来源/含义，`field-producers.json`指向唯一Schema JSON Pointer，不复制nested_schema。

内部RuntimePin限定kind/id/revision/hash；由adapter根据原对象的不可变body导出，不把可变预算row_version=0包装成“冻结”。跨库call/usage/approval读精确receipt＋原import桥；namespace和root一起验证，不能只按id检索。

`CompleteRead`必须带已读范围、真实highwater/epoch/count/集合摘要及结束标志。查不到配置、SQL报错、索引不完整、权限不明分别返回ERROR/UNAVAILABLE/PARTIAL；只有当前scope无需求才有真实EMPTY/NOT_APPLICABLE。不允许测试固定True/空tuple生产替身。

## 11. SQL、幂等与迁移

完整生产增量DDL在`sql/execution_additive.sql`，会话DDL在`sql/session_partition.sql`，命名查询在`sql/queries.json`。**两个脚本属于不同库**，不能一起对orchestrator.db执行。

13张execution side表：profile、创建协议、session、policy adoption、context request、catalog revision/state/epoch、capability binding、tool exposure、skill use、jobs、blob roots。会话库5张普通表＋2个FTS虚表；没有第二份权威Journal。

每个连接强制 `foreign_keys=ON`、`recursive_triggers=ON`、busy_timeout由部署policy，受控本地磁盘WAL；禁止外部进程绕过原Store直接业务SQL。表trigger仅保证结构/状态，不能替代producer的当前权限、hash、issuer与FK跨库校验。

原Agent binding/turn表名依参考存在。若本地等价存储不同，migration adapter使用实际FK并记录source-map；不能创建参考父表在生产冒充。禁止修改原已发布DDL字节或checksum。执行库migration和编排库migration各自盘点，采用各自下一未占用号，不能沿用H1H/Assurance/TaskGraph的旧数字。

迁移通过真实execution UOW runner：完整空库→升级有真实legacy Agent/turn/journal/call的副本→FK/checksum/重开→失败整体rollback→进程提交前后退出。trigger禁止按`split(';')`拆；若真实runner有此缺陷复用已经批准的statement iterator窄修复。默认写端`INSERT`＋existing-key exactbody compare，永不INSERT OR REPLACE。

已有Agent保持legacy profile，基于可信创建标记分流；不能因新profile binding丢了就legacy回退。现有fts/vectors只供legacy，ARP创建新session后只写新分区，不双写两套查询索引；历史迁移只明确copy已获准range并保留源hash。

## 12. 事务与崩溃矩阵

| 动作 | 写事务/外部工作 | 幂等与恢复 |
|---|---|---|
| 创建Agent＋profile/session | 原execution UOW绑定＋CREATING | 同creation key同body回原Agent；建文件崩溃由creating扫描继续 |
| Journal闭组＋INDEX工作 | 同execution事务 | source group/hash/model固定；不因ACK丢失重付embedding |
| embedding执行 | 原execution调用；事务外计算 | 实际receipt先存；向量库写与job ACK分阶段精确重放 |
| Context freeze | 原request/reserve＋manifest side绑定同execution事务 | 同turn/ordinal唯一，异body冲突；已冻结重试不重检索 |
| effect工具 | 原OPS intent链 | 不由本计划提前签APPLIED/Acceptance |
| catalogue suspend | definition scope/状态＋registry epoch同事务 | 当前handoff复查；冻结旧来源不代表可继续执行 |
| session销毁 | DRAINING fence→收敛证明→PURGING→目录删除→PURGED | 删除前后退出都可恢复；缺db不自动ACTIVE；不删除正式pin |
| restore旧备份 | 原Assurance隔离流程 | 不自动激活旧skills授权、sessions、UNKNOWN调用 |

所有调度等待需不占用被等待资源：Agent等tool/workflow子步骤时释放模型槽，session pending recall不占模型槽；同一provider容量从真实deployment key聚合，不能每Agent各认为自己有全额。预算hold可保留但不能阻塞控制/收据import线程。原取消/费用流保持可推进。

## 13. 文件级实施allowlist和接线顺序

一个integration owner独占共享热文件的最终集成；其余可分工小模块。目录“NEW”表示允许实现目标，不宣称已存在。详表`implementation/integration-map.json`。

| 原路径/新增目标（相对src） | 工作 |
|---|---|
| `simple_harness/agents/base.py / runtime.py / execution.py / ports.py` | 原生命周期、每Provider请求prepare hook、实际request暴露桥；旧API语义保持 |
| `simple_harness/agents/context/{composer,budget,tokenizer,protocol_groups}.py` | 本计划V2预算/保护suffix/最终计数，复用原组解析 |
| `simple_harness/agents/memory/{retrieval,embedding}.py` | session索引adapter、全历史候选、可见degradation、原EmbeddingPort |
| `simple_harness/agents/runtime_plane.py` NEW | profile/session/resource生命周期的薄装配服务 |
| `simple_harness/agents/context/session_composer.py` NEW或合并composer | §3纯规划与ContextManifest |
| `simple_harness/agents/memory/session_index.py` NEW | SQLite index/FTS/vector/源验证/销毁；无模型自主权限 |
| `simple_harness/capabilities`现有registry/builder contracts | 复用类型；增加持久catalog façade，不创建平行“总大脑” |
| `simple_harness/tools`原registry/executor/gateway接缝 | exact tool snapshots、结果视图、OPS路由；实际路径本地确定 |
| `simple_harness/skills/{contracts,registry,installer,loader}.py` NEW或等价原模块 | 生命周期、native manifest、SKILL.md导入、按需加载 |
| `simple_harness/execution/sqlite/runtime_plane_{schema,store}.py` NEW | 同原UOW连接side表/索引生命周期记录，不新开权威DB |
| `agent_orchestrator/runtime/agent_bridge.py`及实际委派构造位置 | 绑定真实owner、Assurance来源、权限和scope；不由模型填写 |
| `agent_orchestrator/context`原collector + Assurance exposure importer | A–E精确视图、validity证书、实际request manifest导入 |
| Host `backend/deskpet/orchestration/{handlers,service,projection}.py`和`backend/deskpet/sdk_adapters/sdk_candidate.py` | 原认证命令、投影、候选SDK导入；参考摘录位置需本地hash确认 |
| Host `tauri-app/src/views/MissionsView.tsx`及实际Agent详情组件 | Context预算/技能/工具/销毁状态可见；不重造业务状态 |

禁止本计划修改：HTN PlanningDecision wire、新Operation身份算法、OCC完成公式、NanoJev、现有模型路由配置、用户长期memory SDK。必要的真实source reader/authorizer接缝按原合同扩展，不能使用AllowAllAuthorization生产兜底。

## 14. Host合同与原生验收

统一`HostRequest/HostResponse`在Schema中。所有mutating verb必须command_id＋expected_revision＋payload_ref；reads要求三项显式null，cursor仅分页类使用。caller/tenant由原handler绑定，不接受请求中的principal。

| verb | payload_ref指向 | 真正owner / 返回 |
|---|---|---|
| agent_context_settings_update | Policy artifact | 原配置审批writer：新请求生效，已冻结不变；profile revision规则见§15 |
| agent_session_destroy | SessionDelete artifact | RuntimePlaneService destroy，返回DRAINING/blocked refs，不谎报立刻物理删除 |
| agent_skill_install | 精确Skill包artifact | registry installer→QUARANTINED |
| agent_skill_admit | 已批准eval与skill精确ref的命令artifact | 原认证权限writer→ADMITTED，验证原Acceptance和bundle |
| agent_skill_suspend | 精确skill revision＋reason命令artifact | epoch变化，后续调用拒绝；不丢旧结果 |
| agent_context_summary/history | 无写payload | 当前/历史ContextManifest计量、每source pin/召回情况；全文单独读权 |
| agent_skills_list/capabilities_list/tool_catalogue | null | 只列当前scope可见；分页游标绑定namespace/epoch/query hash，变动返回CURSOR_STALE |

HostResponse.items对每verb使用`implementation/HOST-DTOS.md`的限定类型，不能任意混合Json。`as_of_context_id`用于历史；current validity另字段标明，不把历史状态改成今天。分页默认50上限100，超限明确错误；UI协议解析失败保留最后有效画面并报不兼容，不把缺字段填0。

context设置：原用户请求更新写新的Policy revision。默认**现有session不换profile**；用户对活动session改context上限时执行显式`context_settings_update`，同事务追加`arp_context_policy_adoptions`、真实settings adoption回执＋原下一Provider请求配置binding（不修改session最初profile）；所有manifest必须记录这份effective policy ref。更新不能扩大工具权限，不能改变已冻结请求；缺真实配置接线为实现任务，不填环境变量。

原生验收在隔离Host源码/venv/userdata/端口运行固定candidate wheel；不得在共享Host dirty树重装。Tauri按项目原启动方式自管backend/vite，不双启动。记录实际import文件hash、wheel hash、OS、配置与截图/日志；源码行/HTTP脚本不替代点击、重连、历史Context和销毁UI。原始证据留ignored；Git只保存摘要/相对索引/hash。

## 15. BODY_WIRED、版本兼容与默认启用

ARP内部schema独立version1，公开旧AgentConfig/TypedRef/ResultEnvelope保持原字节；wire错误不得通过“兼容”猜字段。运行中旧Agent没有ARP绑定只能在**`arp_agent_protocols`中的可信legacy创建标记**下继续，不根据缺行判断legacy。需要给旧Agent启用新模式时，显式新session/Agent或受控adoption；不修改正在执行的原请求。

首次部署真实activation由Host原配置管理入口/可信deployment migration producer签发，写ARP profile＋catalogue scope，环境/模型计量/embedding能力不足则阻断对应Agent创建，UI给具名依赖，不偷偷降级为旧profile。当完整BODY_WIRED及全部规定验收通过，同交付factory默认ARP（不是完成后再永久OFF等待第二次批准）。

BODY_WIRED必须有：创建绑定、Journal/index、真正Provider freeze、所有ToolGateway、所有角色context、Skill三种模式能力声明与可用provider、catalogue撤销、Assurance exposure、OCC effect路由、删除/恢复、Host生产caller、policy更新、GC引用检查；不能按文件数量判通过。可选外部provider未部署不是空stub，可明确不可用；至少原工具执行器与一个真实embedding provider+Skill INSTRUCTIONS/SCRIPT主链须可验。

## 16. 统一验收，不降低旧要求

`implementation/sdk-cases.json`列60组目标场景，分R/C/M/K/T/I，每组给producer→消费者、真实步骤、断言、target nodeid/证据路径；actual nodeid在本地collect后填，不造PASS。参考测试只是本文纯规则/SQLfixture验证，不能代表SDK实现。

**主体期间**只针对明确阻塞做最小编译/SQL/纯函数检查。**主体接线完成后**统一：
1. 新60组，所有必需子断言通过；真实Store/UOW/Journal/Composer/Resolver/Commit不得mock PASS，只能stub provider transport和受控外部测试目标。
2. 独立stateful：固定seed 200×50动作（输入、闭组、更新policy、索引失败、取证、工具加载、撤销、close、崩溃、恢复、删除），对照无cache重算，检查无泄漏/无丢原文/无旧generation写入。
3. 16项SDK变异必须被对应行为断言发现，不把import/语法错误当kill。原H1/TG/Assurance/OCC受影响要求建立复用映射，不用本组替它们填绿。
4. 真实模型只在代码冻结后：四场景×3局，模型与budget取当前批准profile，不替换失败样本；§17给oracle。
5. Windows/Linux/macOS原生功能与封装路径；没有平台实测就标该平台PENDING，不宣称跨平台已证明。至少用户当前macOS Host与实际Linux部署完成本机原生验收。
6. 独立审阅者用真实可调用会话/工具读最终代码，不以本包作者四视角自审替代。

命令（目录为实施后的目标；包reference测试单独运行）：
```bash
SDK=/Users/denny/projects/simple-harness-sdk-h1h-impl
HOST=/Users/denny/projects/simple_harness
PY="$SDK/.venv/bin/python"
test -x "$PY" || exit 2
RUN="$(TZ=Asia/Shanghai date +%Y%m%dT%H%M%S)-$$"
EVID="$HOST/.local-test-evidence/2026-09-22/agent-runtime/$RUN"
git -C "$HOST" check-ignore -q "$EVID" || exit 2
mkdir -p "$EVID"
cd "$SDK"
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q tests/agent_runtime_plane \
  -o cache_dir="$EVID/pytest-cache" --junitxml="$EVID/sdk.junit.xml" \
  > "$EVID/sdk.log" 2>&1
```
锁环境缺依赖走正常依赖锁定，不系统pip安装；禁止collect-only视为通过。每次测试绑定HEAD＋dirty文件hash/锁hash＋profile/provider/renderer/embedding指纹。旧数字不外推当前版本。

## 17. 四类真实模型场景与oracle

- **LM1 长历史/召回**：固定输入流包含超过recent窗口两倍的授权技术讨论、两个很早的精确标记、一个后来撤回的错误结论；实际query语义改写。必须正确找回两个标记来源，明确错误结论不是当前事实。每局8Provider请求/64k实际新生成tokens/15min上限；长输入和embedding费用另按真实receipt记，模型实际不支持512K时跑其支持窗口，不标512K实测。
- **LM2 动态工具/Skill**：隔离代码任务使用一个已准入instruction skill和一个脚本skill；工具只按需曝光，完整结果进入Assurance。必须提交通过独立固定检查的artifact；未曝光/已撤销工具不得实际执行。每局12Provider请求/96k输出/20min。
- **LM3 跨Agent独立性**：两Worker私有历史包含不同唯一标记，Verifier只拿指定结果和正式证据；不能自动看到另一Agent私有记录；最终审阅按冻结资料。每局总18Provider请求/128k输出/20min。泄漏判定主要以实际输入manifest和工具授权，不靠模型“不知道”一句话。
- **LM4 关闭与恢复**：在真实任务一次已准备请求或已完成工具回执后重启同root，恢复原上下文/call，不增副作用；正式destroy后query拒绝、临时库不存在。未决操作模拟器＋真实LLM，不能对真实用户外部系统制造危险故障。总12Provider请求/96k输出/20min。

每场景3局共12；硬不变量12/12必须满足，每场景至少2/3业务oracle通过且总≥10/12；所有失败保留，不无限重抽。达限不归咎模型必错，按MODEL_LIMIT/ENVIRONMENT/SPEC/IMPLEMENTATION/OUTCOME分别记；不能因接口失败删场景。实际512K另跑确定性token计量/用户部署能力验证＋1局实际支持的长上下文场景；不靠10KB模拟token测试宣传模型512K可靠。

## 18. 开发单元：完整设计先定，主体一起接线

- **RP-A**：source-map、全部strict contracts、profile/session与原UOW接口、完整SQL/Host DTO；不造假producer让接口先PASS。
- **RP-B**：A–G装配、双预算计量、实际模型freeze/曝光、全历史索引；与RP-C的能力快照接口同步，不以空工具表占位宣布完成。
- **RP-C**：Capability/Tool原registry后端、Skill安装准入/依赖/加载/执行、原工具与OPS回流。
- **RP-D**：lifecycle销毁、CAS roots、cross-db imports、恢复/撤销、原生Host/settings/默认策略。
- **RP-E**：BODY_WIRED后统一机制/变异/回归/原生/模型/独立代码验收；更新架构与状态。

这些是施工分工，不把完整要求降为MVP。WorkAgent有权实现NEW producer、映射真实接口、采用下一未占用迁移、修复明确合同内的代码；不能因需写模块就把它回传为新的架构blocker。真正需要裁定的是无法同时满足的语义，而非字段还没写。

## 19. Done与禁止虚假关闭

同时具备：本文60组与16变异实际证据、受影响legacy/H1/Assurance/OCC/TG回归、实际embedding/tokenization、context四种异常（oversize/partial/unauthorized/unknown）区分、Skill与tool所有执行路径受guard、会话生命周期/删除证据、原生Host与真实模型结果、最终独立审阅。

更新`ARCHITECTURE/AGENT_ORCHESTRATION.md`或项目对应事实源、`PROJECT_STATUS.md`及索引日期；正文只写真实范围与证据相对索引/hash，不提交raw log/DB/截图/凭据。完成的功能同交付默认ON，新旧profile边界可见。

没有实际候选字节或测试的字段保持PENDING_SOURCE/PENDING_SDK；参考包PASS只标REFERENCE。作者自审不叫独立子代理；没有数学意义100%零缺陷保证，不以此妨碍完成明确实施/验收。

## 20. 外部技术依据（不替代项目合同）

W01 SQLite FTS5（全文/trigram、external-content索引维护）：https://www.sqlite.org/fts5.html
W02 SQLite隔离（同库事务、snapshot边界）：https://www.sqlite.org/isolation.html
W03 SQLite Backup（不能直接拷active WAL库声称完整备份）：https://www.sqlite.org/backup.html
W04 Python sqlite3（complete_statement不是完整validator、事务行为）：https://docs.python.org/3/library/sqlite3.html
W05 Agent Skills包布局与渐进披露：https://agentskills.io/specification
W06 MCP tools（schema、工具元数据为提示，不是权限）：https://modelcontextprotocol.io/specification/2025-11-25/server/tools
W07 Context engineering参考（检索/笔记/压缩的权衡，不改用户的动态N模型）：https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

实际部署版本按本地锁文件，不自动追上述文档最新版本。更多源码依据及证据层级见`implementation/source-map.json`。

## 附录 A：实施前四个精确接缝裁定（与正文共同执行）

### A1. 实际authorizer与永久Grant不是一回事

`RuntimeAuthorizationRecorder.after_real_authorizer`在原AuthorizationPort真实返回后保存不可变receipt，绑定实际request key、caller、owner、purpose、输入hash、scope/policy、TTL和原decision receipt；**不把一个allow bool转换成看似有来历的grant**。本lane生产authorizer必须来自Host已认证principal＋原policy/授权服务；默认AllowAllAuthorization仅测试/演示，不可被profile激活逻辑接受。

`AuthorizationReceipt` Schema是原结果的投影，不是裸批准写API。跨orchestrator的批准通过原import桥携带固定来源；如果当前原Authorizer无法给出可核验policy/来源，实施者需在该原评估执行点加真实recorder，与实际评估事件同事务或可靠导入，不能在ContextBuilder新建签发器。原动作审批与H1 planning grant不互相替代。

profile激活无循环：先通过原部署配置命令批准配置payload并产生receipt，再建立引用这份receipt的profile。receipt可以引用被批准的配置artifact hash，但不反向引用尚未存在的profile hash。活动session采用新context policy保存原`AgentContextPolicyAdopted`事件，ContextManifest必须带`effective_context_policy_ref`；不靠profile初始值或环境变量猜当前设置。

### A2. session文件并发、删除和迟到embedding

所有session DB打开/查询/index最终写入/rename/delete通过同一个`SessionFileGuard`。锁文件放在runtime root的`locks/<session_id>.lock`（不在要删除的目录内），使用项目已有跨进程文件锁；无等价实现时 Unix用`fcntl.flock(LOCK_EX)`、Windows用`msvcrt.locking`固定首字节并使用有界重试；不拿文件存在或lease超时当锁。

在持锁后重新读central session状态/代次和本地marker；index网络/embedding计算**不持锁**，返回只在重新持锁和状态核对后入库。DRAINING写入前也先取得同一锁，因此fence之前已在写的任务先结束，fence之后任何迟到embedding都不得入库。purge持同一锁完成关闭连接/rename/unlink；所有工具读都使用with-finally释放句柄。不能在别的thread拿未获准的SQLite连接。

原`SessionDisposalReader`的“index writers/read leases为空”通过已取得排他的同一FileGuard并且没有当前进程遗留cursor/handle来证明，**不凭默认0**。调用/UNKNOWN/import/blob roots仍从原真实持久来源完整读取。锁外的Embedding调用属于原execution在途，也要核对完成，不能删库后让迟到worker重新mkdir。

### A3. 消息表示与计量的非循环顺序

预算装填用**renderer定义的非负单调charge**（exact分段或已认证上界）而非猜字符；固定framing计入A–E。先做单调suffix装填，再对完整实际wire执行最终计量。两者差异只触发未冻结候选的单向缩减；最终guard不信“已经算过”。没有可加证明的counter可用完整render计数作保守组上界，但必须经模型adapter验证；无法得到任何可靠上界则拒绝，不宣传N为实际token最优。

Current required tail包括本次user/feedback输入和尚未闭合tool协议关系，哪怕此前某个字段已被TaskContext重复表达也不能删除消息骨架。F返回普通数据view，不能拼成可执行的assistant tool_calls。ToolSnapshot全部schema与native response格式计入counter。

### A4. Skill/工具与正式保留根

动态tool model_name固定为`rt_`＋完整tool identity规范hash的前40hex，碰到同名异ref必须拒绝，不覆盖；原名称保留display metadata，不能以名字猜server。依赖Skill的完整bundle/脚本在use时锁定immutable CAS/mount；skill后续升级只影响新use。

原request和review材料存原受保护CAS/ledger。`arp_blob_roots`是GC引用根而非artifact权威。创建context/skilluse之前，真实写入pin或原request原生引用；只有原owner完成/正式转移receipt才release。所有原GC delete前通过原root集合查询包含ARP根；`TRANSFERRED`要求目标正式pin存在、同对象hash且当前reader证明，其后不能因ARP销毁而GC正式材料。旧备份恢复仍沿Assurance quarantine。


## 附录 B：独立创建协议与Context policy的真实持久来源

`arp_agent_protocols`是不可变创建/迁移标记，不是第二份Agent生命周期。ARP新建由原Agent factory在同UOW中写ARP_V1与原创建回执；显式legacy新建写LEGACY_EXPLICIT。首次安装迁移在排他升级事务中仅将**升级前真实已有**BaseAgent登记为LEGACY_AT_MIGRATION，来源为真实已应用迁移描述符/事件回执＋该行不可变配置hash，不依靠日期猜。无可信来源不自动登记。此后缺marker＝RUNTIME_CREATION_MARKER_MISSING，ARP marker存在而session/profile缺失＝RUNTIME_BINDING_CORRUPT；两者不能当legacy。

Context初始policy来自profile.context_policy，在原创建事务以adoption_revision=1保存`arp_context_policy_adoptions`；它引用原policy artifact/批准配置事实和真实创建回执。新settings命令必须带expected_effective_policy_ref，读取当前最大adoption_revision作CAS，检查当前caller/session/合法policy，再append revision+1、原event及command receipt。Policy body沿原CAS/注册Policy源保存，adoption只保存精确ref，不复制新的预算权威。命令重送回原receipt，同命令异body冲突；并发旧expected只一个成功。读取当前policy用`read_effective_context_policy(session_id)`，旧manifest恢复直接读其记录的exact policy ref，绝不查当前MAX替换。

供迁移使用的descriptor receipt来自**实际runner成功登记的迁移记录/不可变描述符**；该引用只说明旧Agent的profile兼容分类，不授予新的读写权限。内部ref kind `policy/receipt`必须由FIELD-CONTRACTS限定resolver读取，不将任意字符串包装成签发来源。


## 附录 C：最小registry epoch与元数据来源补充

本版registry使用**每个execution root一个聚合epoch**，namespace_id由真实root绑定固定为`runtime-catalogue-v1:<root_id>`。所有Capability/Provider/Tool/Skill定义、activation、部署与所依赖schema变更都在原registry writer事务中bump一次；不从entry_id前缀猜所属namespace，不把不同namespace的epoch相加当版本。当前访问scope仍来自activation.scope_ref和原authorizer，聚合epoch只是保守失效信号。profile引用真实registry定义/namespace身份；跨root恢复走隔离，不从备份继承当前准入。

内置Tool/Capability/Provider注册也经真实deployment命令与QUARANTINED→TRIAL→ADMITTED状态；非Skill的TRIAL表示装配待验证，不要求执行有副作用的试动作。Skill的TRIAL才必须绑定隔离评估scope并验证既有批准evaluation policy。所有真正外部Effect不得为了“probe健康”而自动执行。

正式durable request/CAS的独立审计pin不阻止**临时索引**销毁；DisposalReader只将仍指向本session临时文件的活跃root计入live_temp_root_refs。已有正式pin经精确存储位置/同object hash证明不依赖临时区时，可以形成transfer receipt并释放临时根；不是删除正式pin。不能因一份长期保留Review还存在，就让无依赖的临时向量DB永久不能删除。


## 附录 D：长AgentTurn、召回范围与结构化空状态

1. 一个尚未结束的AgentTurn可能包含很多**已经闭合**的工具协议组；不能把整个AgentTurn所有历史调用都标为mandatory，否则长任务永远不能出窗。原当前输入是独立必需锚点，当前未闭合工具协议组是必需tail，其余闭合组可以退出窗口并索引。完整后缀规则施加于排除独立必需锚点后的可选闭合组序列；原消息锚点按Journal原顺序render，不拆call/result组。`N_complete_turns`仅统计某个历史输入turn的全部需要保留协议组都在manifest中的完整轮，当前活跃turn不算N；另列实际group count。若普通聊天每turn一组，即退化成用户公式的最近N轮。实现不得把只剩部分的历史turn计为一个完整turn。
2. 对同一record的重叠chunk，F构造先合并其合法UTF-8范围并生成一个精确source view，或确定性保留较高rank范围并删除重复覆盖；默认**合并相接/重叠span**，合并后重新计量，超policy视图大小则选择rank高的原span，不猜新摘要。不同provenance/权限/epoch不得合并。该步骤在§3.5候选封装之前；G扩展删除整个重叠group的F条目，保证无重复。
3. 未有请求的contextsummary返回一份ContextSummaryView：context_id/manifest_ref=null、charges/counts=0、retrieval_status=NOT_REQUESTED、degradations含NO_REQUEST_YET。**0来自真实“尚无ContextRequest”的完整读取**，不是解析失败兜底。Host DTO表使用同一语义，错误读取仍Error，不保留第二套空值兜底。
4. 空body、输出为UNKNOWN、ToolResultView中的nullable字段都由分支严格校验：REFERENCED必须result_artifact_ref；PENDING_OPERATION必须operation_intent_ref；SUCCEEDED不得同时error_code非空；UNKNOWN不携带被当成成功判定的result。receipt可以记录失败原文，但它不是完成证据。


## 附录 E：Skill包hash、脚本argv及query参数的最终规则

`Skill.files`只列SKILL.md和scripts/references/assets，**不列skill.json自身**，避免manifest自含hash。规范清单必须覆盖所有获准payload文件，归档多出未声明可执行文件直接拒绝，不忽略。`manifest_hash=sha256(原canonical(Skill))`；`bundle_digest=sha256(canonical({format:"arp-skill-bundle-v1",manifest_hash,files:按relative_path排序的SkillFile完整数组}))`。ZIP原始字节另存archive_hash，不替代bundle。安装前实际文件hash与声明一致，规范化后不悄悄改一个已批准revision；SKILL.md-only导入产生新的candidate native manifest。

SCRIPT调用：`argv=[approved_runner_executable, *runner_fixed_args, immutable_script_path, *rendered_argv_template]`。模板仅允许**整token**`{input_json}`与`{output_json}`，分别替成Sandbox创建的绝对输入/输出文件路径；含其它占位符/内插字符串拒绝。其它literal token来自已准入manifest原字节，不把模型arguments拼进shell。输入写typed JSON文件、flush后交接；输出文件按outputschema读取，限字节、不跟随symlink出sandbox。环境变量白名单从真实executor policy生成，不把Host env/secrets原样继承。stdout/stderr有限保存原receipt，超限引用artifact，不用截断PASS。脚本完成≠Task接受。

`queries.json`中的`:recent_start`是**可选闭组保护后缀**的起点，不是所有mandatory锚点seq的最小值；当前user input锚点可以很早，但不能因此把随后已退出窗口的历史工具组排除出召回。查询另按完整`excluded_group_ids`做过滤并保存收据。`:page_size/:limit`取本policy上限；`:escaped_match`必须走原FTS literal terms tokenizer/escape，不直接把模型文本当FTS表达式。所有这些查询只对已验证marker的当前session DB执行。
