# Agent Runtime Plane 1.0 → PlanAgent：一次性合同补齐交接

日期：2026-09-23（Asia/Shanghai）

**交接结论：CHANGES_REQUIRED。请优化原生 Runtime 增强计划，不实施产品代码；收敛本文件 Q01–Q20 后交回一份自洽、可直接接线的新完整包。**

## 0. 用户最新裁定与交付范围

用户本轮原话：

> 好的，我们暂时不考虑适配其他的runtime（像是Pi），给我一个完整的handoff，我给你PlanAgent继续优化。把你需要的问题一次性提完，你可以进行调研。

因此：

1. 本轮只优化原有 BaseAgent / AgentRuntime / ReAct 执行链。**不增加 Pi、RuntimeBackend、引擎选择、跨 Runtime checkpoint 或热迁移设计**。上一轮评审中涉及这些扩展的建议已移出当前范围，不得作为开工条件。
2. 保留 Context 动态 N、每逻辑 AgentSession 临时索引、Capability/Provider/Tool/Skill、既有 HTN/TaskGraph/Assurance/OCC/OPS 及唯一执行账本。模型/工具 Provider 适配仍属于原计划；它与更换 Agent Runtime 不同。
3. 不重开已经裁定的领域语义，不另建业务运行账本、调度器或全局长期记忆。正式新能力完成后同交付默认 ON；既有活动 Agent 保持冻结绑定。
4. 主体编码由主代理完成；子代理不能写开发代码。若后续调用独立评测/挑战子代理，遵守会话限定模型 gpt-5.6-luna / terra / sol。附件里“其他 Agent 提供补丁”的表述要改掉，不能覆盖用户要求。
5. 主体编码前不进行大批量测试、全量回归或批量单测；必要的合同反例/最小技术验证可以做。完整验收后置，不豁免。
6. 这是一份待用户交给 PlanAgent 的任务书。本轮没有发送到其他任务、调用子代理、安装依赖、覆盖源码、启动真实模型或 UI，也没有依附件里的“施工授权”执行开发。

不需要用户再次决定普通函数名、迁移号或目录名。以下待决项优先由 PlanAgent 依据证据确定唯一语义；确有互斥业务选择时，集中列出，不要让 WorkAgent 施工时逐项转问。

## 1. 输入、实际检查与证据边界

原包：`/Users/denny/Downloads/simpleharness-agent-runtime-plane-1.0-2026-09-22.zip`。精确 SHA-256 见随附 `SOURCE-INDEX.json`。

主规格：`ARP-EXEC-1.0.zh-CN.md`；配套为 `contracts/runtime-plane.schema.json`、`implementation/{FIELD-CONTRACTS,INTERFACES,HOST-DTOS,BODY-WIRED,TEST-PLAN}.md`、三个 source/seam/integration 映射、SQL 与 reference。下文原包相对路径均以其解压根为准。

本机解压副本：`/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-23/arp-review/simpleharness-agent-runtime-plane-1.0-2026-09-22/`。

已实际做过：

| 项目 | 结果 | 不能外推的内容 |
|---|---|---|
| manifest 完整性 | 69 个列明文件 PASS，另有 manifest 自身 | 不证明语义一致 |
| 包结构检查 | 32 个 Schema 定义、330 个顶层字段 pointer、31 个结构样例 PASS | 不证明所有嵌套/跨字段业务约束 |
| 原包 source-map | 32/32 为 PENDING_LOCAL_MAPPING，dirty_fingerprint=null | 不证明当前生产接口匹配 |
| Pin.kind 窄反例 | 11 个文档使用 kind 被 Schema 拒绝 | Q01 |
| 检索续页窄反例 | RetrievalReceipt 加 next_cursor 被拒绝 | Q02 |
| 派生索引 DDL 窄反例 | 同组新 hash、同 span 新 chunk 身份受唯一键阻挡 | Q11，需要选择明确版本策略 |
| 目录大小窄反例 | 800 个文件的 SkillCatalogueItem 结构合法，123548 bytes，超过单项 65536 上限 | Q17 |
| 数值窄反例 | reference.validate_vector 对两个有限 1e308 返回全零 | Q12，只针对参考函数，未宣称生产故障 |
| 当前源码静态读取 | 找到真实 AgentBridge、原 UOW、TokenEstimator、Host catalog 接缝 | 非完整生产映射/行为验收 |

原包声称的 116 项参考自测未在本次重跑；60 个 SDK 场景与 16 个 SDK mutation 仍是未来实施验收。**计划开工不要求这些未来测试先通过；本次阻碍是合同冲突、关键协议空缺与实际接缝未确定。**

原始证据只保存在 ignored 目录：`review-checks.json`、`extended-checks.json`、`extended_contract_probes.py`、外部资料抓取。Git 仅保留本手写结论与来源/hash 索引，不能加入原始日志、数据库、截图或网页抓取。

## 2. 当前集成基线：必须更新，不能沿旧 HEAD 猜

### 2.1 三种身份分别使用

- **共享 HTN/Host**：本次读取的 Host vendor 配置为 `0.13.0.dev20260922+htn.1`；HTN 工作树 `/Users/denny/projects/simple-harness-sdk-h1h-impl`，HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加实际 dirty 字节。HEAD 不能代替 dirty fingerprint。
- **TaskGraph 集成父候选**：`taskgraph.23`，独立于共享 Host。SDK 来源为 `/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-23/sdk/`。后续 Assurance/ARP 的合并基线要参考这一链，不能只在旧 HTN 副本上画接口。
- **Assurance 当前后继**：本轮读到 TaskGraph 已交接第三部分继续开发，未审计该任务的最新 dirty 实现。不得假定它未实现，也不得把计划包或 TaskGraph 历史证据当作最新 Assurance 实现已通过。由集成人在 AS/ARP 接线盘点时冻结实际后继源码与接口；独立工作不能覆盖它。

TaskGraph 交接记录中的版本与 hash：`0.13.0.dev20260922+taskgraph.23`，wheel `e1d34a46bcabb43d82be39bf8bb39755838d29993e81f1e1b95b92507e004c77`；此为交接文档来源值，本次未重新验证安装的 598 个包文件。

最新入口：

- Host `ARCHITECTURE/index.md`、`ARCHITECTURE/TASKGRAPH.md`。
- `plans/TaskGraph/V1.2.1/TASKGRAPH-MAIN-HANDOFF-2026-09-23.md`。
- `plans/TaskGraph/V1.2.1/MAIN-RESULTS-2026-09-23.md`。
- `plans/Assurance/2026-09-22-plan-review/REVIEW-ASSURANCE-EXEC-1.1.md`（Assurance 计划评审，非其最新实施状态）。

当前 TaskGraph **主体阶段 PASS、可接第三部分**；完整重型 acceptance 后置仍 NOT_RUN。旧记忆中“H1-H producer 缺失、只能离线准备”的状态不能直接复制为今天的事实。

### 2.2 已找到的真实接缝，供映射起步

以下是本轮直接读过/定位的当前 HTN 工作树入口，不声称就是最新 Assurance 合并后的最终签名：

| 范围 | 真实文件/符号 | 本轮发现 |
|---|---|---|
| 编排 Agent bridge | `src/agent_orchestrator/runtime/agent_worker.py::AgentBridge` | 原包写的 `runtime/agent_bridge.py` 不存在 |
| Runtime 装配 | `runtime/assembly.py::assemble_orchestrator_runtime` | 建多个 execution pool；AgentRuntimePorts 目前传 AllowAllAuthorization；该处没有注入 embedding |
| Agent 创建 | `src/simple_harness/agents/runtime.py::AgentRuntime.create` | 先 await kernel.start_base_agent_run，再调用 uow.create_agent_binding |
| binding 写入 | `execution/sqlite/uow.py::create_agent_binding` | facade 自己开 transaction，需给同 UOW 接线明确入口 |
| wire 准备 | `agents/wire.py::AgentProviderWire.prepare_request` | 恢复真实 tool-call 消息后再 guard；不能在这之前冻结一个不同 wire hash |
| 原 Provider 协调 | `execution/dispatch.py::ProviderInvocationCoordinator` | 已有 request_preparer/admission/physical invocation；优先扩展 |
| 工具组 | `agents/context/protocol_groups.py::build_units` | instructions/user_input/tool-group 是不同 unit；需适配附录 D 的长 turn 算法 |
| DeepSeek 计量 | `agent_orchestrator/runtime/deepseek_tokens.py::DeepSeekV41TokenEstimator` | 有固定 tokenizer/renderer 指纹，明确 requires_prior_output_reserve=True |
| HF 计量 | `agent_orchestrator/runtime/hf_chat_tokens.py::HFChatTokenEstimator` | 固定本地文件/template；服务端模板一致性需另外验证 |
| 预算交接 | `runtime/provider_budget_guard.py::ProviderBudgetGuard` | 已处理真实 reservation/UNKNOWN/prior_output，声明 Orch→SDK 锁顺序 |
| Host model profile | `backend/deskpet/orchestration/runtime_profile.py::source_runtime_options` | 已绑定部分 tokenizer/profile；不能笼统说整个项目只有估算计量 |
| Embedding port | `agents/memory/embedding.py::EmbeddingPort` | 同步 embed(texts)→vectors；HashEmbedder 仅逻辑测试，不能作为语义能力 |
| 原目录 | `simple_harness/tools/runtime_catalog.py` | 已有可见性/Run 工具曝光，与 ARP 目录需统一 |
| Host Skill/目录 | `backend/deskpet/sdk_adapters/{capability_catalog,skill_resolver}.py`、`backend/deskpet/capabilities/skill_install.py` | 已有项目/全局资源与冻结 resolver，不应新造第二套产品入口 |
| SQLite 连接 | `execution/sqlite/database.py::Database.open` | 固定并核对 foreign_keys；本次读到的初始化未显式设 recursive_triggers |

## 3. 一次性问题总表

分类：**C**=已经证明的合同冲突；**D**=需要补齐的协议定义；**M**=需落实的现有源码映射。不是所有项都意味着架构重做。

| ID | 分类 | 必须交回的答案 |
|---|---|---|
| Q01 | C | 统一 Pin.kind 与逐字段引用规则 |
| Q02 | C/D | history search/read 的完整分页 DTO |
| Q03 | D | settings/summary/命令回执完整往返协议 |
| Q04 | M | 当前父候选、32 个接缝、所有角色/创建入口映射 |
| Q05 | M/D | 首模型计量协议与现有计量器复用 |
| Q06 | M/D | 真实 embedding 装配、计费、故障语义 |
| Q07 | M/D | 真实授权 producer 与 profile/catalog bootstrap |
| Q08 | M/D | 创建/提交/prepare 的原事务接入方式 |
| Q09 | D | 冻结未发送请求失效后的终止/替代协议 |
| Q10 | D | 长 turn 锚点、闭组、完整轮 N 的精确算法 |
| Q11 | C/D | 索引身份、版本切换、唯一键与 snapshot |
| Q12 | D | 全历史检索、分页排名、覆盖与资源边界 |
| Q13 | D | INDEX/PURGE/BIND_IMPORT 等 durable job 合同 |
| Q14 | D | close/cancel/destroy/rebuild 与锁顺序 |
| Q15 | M/D | 唯一 registry、Provider 定义、准入/部署 epoch |
| Q16 | D | Skill 导入、TRIAL、评估、依赖及执行闭环 |
| Q17 | C/D | 管理/模型 DTO 的大小与披露约束 |
| Q18 | D | 错误、事件、内部快照的完整类型与来源 |
| Q19 | M/D | 两库迁移、PRAGMA、GC/retention 与旧数据兼容 |
| Q20 | D | 验收映射、平台/资源依赖、可执行准备门 |

## 4. 逐项问题、推荐收敛方式与关闭证据

### Q01 — Pin 类型与逐字段约束

**证据**：FIELD-CONTRACTS:19–28、INTERFACES:15–21 使用的 kind，与 Schema:15–46 不一致。窄反例中 `profile/journal_record/input_manifest/agent_turn/receipt/context/retrieval/tool_snapshot/skill_use/occurrence/completion_scope` 全部 ENUM 拒绝；ContextManifest 样例则把多种引用统一写成 `kind=agent`，只保证结构过关。

**必须决定**：扩充内部 Pin enum，还是把某些对象正式映射为现有 artifact/source/receipt 类型；两种都可，但每个字段只能有一个明确合同。不要修改公共 TypedRef 来掩盖内部设计缺口。

**交回**：逐字段 allowed kinds、producer、exact resolver、revision=0 许可、canonical hash 输入、root/tenant/owner 校验、当前性与保留规则。泛型 Pin 的全 enum 不能替代字段限制。至少一个语义合法正例、跨 kind 负例；所有 doc/schema/SQL JSON/reference/example 同步。

### Q02 — history search/read 输入输出闭合

**证据**：主计划 §4.4 要求 PARTIAL+可续 cursor；HOST-DTOS:60 与 INTERFACES:44 只有 RetrievalReceipt+RecallItem，Schema 不含 next_cursor，加入即 UNKNOWN_FIELD。read 的范围回执与 has_more 只以文字描述。

**交回**：SearchRequest/SearchPage、HistoryReadRequest/HistoryReadPage 的严格 DTO（或指向精确现有 DTO），明确 next_cursor、has_more、source/index snapshot、covered/scanned range、返回项目上限、UTF-8 bytes 与 seq 的截断规则。明确无命中/扫描不全/不可用和无下一页的区别，以及 cursor 过期/撤权/模型索引版本变化的错误。Host 管理分页与模型工具分页不得隐式混用。

**反例**：早期命中位于第二页；中途索引增加或撤权；单条 Journal 超 max_bytes；取消后 continuation 拒绝。正文承诺与 DTO 均能表达这些结果。

### Q03 — 设置、summary、回执

**证据**：HOST-DTOS:13 要求 adoption revision；ContextSettingsCommand 要 expected_effective_policy_ref。SessionView/ContextSummaryView 没有明确返回这两个字段；HostResponse.view_revision 含义未逐 verb 确定。UI 要求的 configured cap/count mode/新 policy 也不完整在 summary 中。

**交回**：明确 ContextSettingsView 或扩充 summary，首次没有请求时也能读取当前 policy/adoption；定义 view_revision、generation、policy revision、adoption revision 的区别。写命令返回 receipt/ref、重放查询方式、expected_revision 与 payload expected_ref 的一致性；不可保留“使用原 receipt 字段或另行查询”这种未绑定路径。

另外冻结 policy artifact 的上传/读取入口、manifest 详情读口与读权、DTO 字段；裸 payload_ref 不是完整前端流程。给出一次真实格式的 GET→UPDATE→冲突→重读→重试→断线重放样例。subject AgentId 与 payload SessionId 必须检查归属。

### Q04 — 当前基线与 32 接缝，覆盖所有入口

**证据**：32/32 actual_path/symbol/hash 为空；真实 bridge 位于 agent_worker.py。共享 HTN、TaskGraph23、后继 Assurance 不是一个字节版本。

**交回**：source-map 指明审计基线、局部/全源码 fingerprint 范围、EXISTING/MODIFY/NEW、实际 caller→producer→transaction→consumer。现有项必须真实路径符号；NEW 项无需先存在，但要有最终签名、caller 和 store ownership。

角色覆盖不仅写“所有 Agent”：列 root/child、create_many/delegate、Planner/HTN planning backend、Manager、Worker、Critic/Verifier、Mission judge、standalone chat、恢复入口的真实路径。某个角色不走 BaseAgent 时，要选正式 adapter 或明确不在 ARP 范围，不能让它静默绕过冻结 Context。

涉及 Assurance 正在修改的 hot files、TaskGraph read-set、Exposure、GC 时列 owner 与合并边界；先冻结当前接口交接，不要求另一任务的未来整门先 PASS。

### Q05 — 计量协议：修正上一轮“未落实”的精度

**新发现**：当前已有 DeepSeekV41TokenEstimator、HFChatTokenEstimator 与 ProviderBudgetGuard；不能要求重写一套。通用 UpperBoundTokenizer 仍是 bytes/2+固定开销，不自动等于经过认证的模型上界。

**交回**：首个受支持模型/端点协议、counter/renderer/serializer 指纹、对应库与 tokenizer 资源、EXACT 或 CERTIFIED_UPPER_BOUND 证明边界、当前配置如何选入。不得只凭 model name 猜实际协议。

必须区分：公开 wire 输入、服务端续接的 prior reasoning/output reserve、本次输出 O、S/H、预算计费 allowance 与实际 Context 容量约束。说明现有 `requires_prior_output_reserve` 如何进入新 ModelLimits/TokenReceipt/manifest，避免漏计或双扣。output cap 自动增大、streaming、tool schema mode、response format 与多模态不支持分支都要保持原 request 身份规则。

**关闭证据**：一个针对现有计量器的最小适配验证或精确可复用证据，不能以随机安全系数签 CERTIFIED；没有所需资源时标环境依赖，给出名称/制品/hash/注入位置，不要求泄露路径内容或密钥。512K 配置可用性与真实模型质量是两项证据。

### Q06 — 真实 embedding 及 activation 策略

**证据**：原 EmbeddingPort 是同步 embed(texts)→vectors；当前读到的 assembly 并未给 AgentRuntimePorts 注入 embedding。它没有直接返回 durable invocation/usage receipt 的接口。HashEmbedder 明确只能逻辑测试。

**交回**：实际本地/获准服务 provider、模型制品/维度/归一化/tokenizer fingerprint、同一资源加载与关闭 owner、async offload、取消/timeout/late result、真实 call/usage receipt 映射。索引调用和查询向量调用都需明确定价模式/预算来源，本地无收费也应如实记录，不能造用量。

明确 `embedding_required_for_activation` 与 `allow_lexical_degradation` 组合真值表：首次缺失、启动后故障、已有向量仅 query 服务失败、恢复时资源变化分别是拒绝创建/PARTIAL/LEXICAL_ONLY/UNAVAILABLE。降级不自动切 legacy；复用同一个 embedding 模型不等于接入用户长期记忆库。

### Q07 — 原授权与 bootstrap

**证据**：主计划附录 A1 不允许生产采用 AllowAllAuthorization，但当前 assembly 仍传这个对象；原工具/预算 fence 有其他实际授权来源，所以也不能反过来声称当前产品“完全无授权”。ARP 需要准确连接。

**交回**：每个 purpose 的可信 caller、policy evaluator、原决定/审批回执、scope、有效期、撤权源与 writer；认证用户、服务 owner、Agent identity 的区别。将真实 recorder 接到实际判断点，不能把 allow bool 包装成授权来源。

profile/catalog 第一次批准、内置 bootstrap 工具 QUARANTINED→TRIAL→ADMITTED、新安装与旧数据迁移如何无循环启动；哪些资料先存在、哪笔事务写入、崩溃重送用什么 key。standalone 的 B/C/D NOT_APPLICABLE 也需要系统模式与构造规则，不能靠缺行推断。

### Q08 — 原创建与 prepare 事务接口

**证据**：当前 AgentRuntime.create 先 kernel.start，再调用自行开事务的 create_agent_binding；原计划要求 profile/session/protocol/adoption 同原创建 UOW。

**交回**：明确扩大哪一个原事务、提取哪一个 connection-level writer；kernel Run 已创建、binding 尚未创建时的既有恢复规则如何保留。ARP 创建幂等 body 是否包含 profile hash；同 creation_key 不同 profile 怎么拒绝；子 Agent/batch 入口一起处理。

对 Journal 闭组+INDEX job、request prepare/reserve+ContextManifest+blob root、SkillUse+原 call/reserve 都给出伪代码事务时序，标注锁内/锁外及失败回滚。纯 ContextComposer 不写 DB，正式持久化由原 owner 完成。不要把现有 facade 嵌进新 transaction 后假定它没有 commit。

### Q09 — 冻结未发送请求的替代身份

**证据**：§6.1 先按 `(turn_id,ordinal)` 返回冻结内容；§6.1 又允许 source/authority 变化时在未发送阶段“准备新请求”。同 ordinal 唯一约束与原 reserve 处理需要明确衔接。

**交回**：至少逐态说明 PREPARED/RESERVED/WAITING_FOR_SLOT/HANDED_OFF/UNKNOWN/SUCCEEDED/FAILED 在 settings 改变、source-readset 失效、registry 撤权、output cap 改变时如何处理。状态使用真实原枚举映射，不强加第二套状态机。

重点：已冻结未 handoff 的请求是继续原 payload、拒绝当前 handoff、终止后分配新 ordinal，还是其他既定原协议；谁结算/释放 reservation，如何证明未发送，何时允许生成替代身份。UNKNOWN 永不换 payload/ID 重发。计量后的 restore_tool_calls/serializer 必须不再悄改 hash。

### Q10 — 长 turn / N / 必需锚点

**证据**：附录 D 已要求“一个活跃 turn 的闭合工具组可出窗，当前输入锚点保留”；参考 allocate 只有 required_tail 与逐组 complete_turns 求和，不能直接证明此规则。

**交回**：ProtocolGroupAdapter 的完整输出，区分 user anchor、closed tool groups、open tail、terminal answer、需要保留的 opaque provider 片段；定义 group_id/closed producer、seq gaps/隐藏 Journal 记录处理及正确 call/result 顺序。

冻结 `N_complete_turns` 的集合判定：一个历史 turn 只剩部分组不能计 1；当前 turn 不计 1。明确 source_relevance/section priority 的实际 producer 与稳定 tie-break、fixed_soft_max/section caps 的作用、max_group_count 达限和256次收缩退出。current anchor 与 A–E 重复信息的预算计数不能重复省略。

**反例**：一轮包含多组，其中中间组被移出 Context；很早 current input 与最新 open tool tail 同时保留；旧 turn 部分保留；大组阻断后缀；相同来源相同 policy 重组确定性。补充对应的参考表示和窄 oracle，不能拿旧 sum 逻辑证明新语义。

### Q11 — 派生索引版本与 DDL

**证据**：FIELD-CONTRACTS §4 定义 indexed_group `(group_id,source_hash)`、chunk identity 包含 chunker；但 session_partition.sql 的 group PK 只有 group_id 且 UNIQUE(seq_from,seq_to)，chunk UNIQUE(record_id,offsets,source_hash) 不含 chunker。实测同组新 hash 或不同 chunk_id 同 span 被拒绝。

**必须决定**：同一逻辑组的 source/view/chunker 改变能否共存。如果 Journal 原文根本不允许变，应将这种输入定为错误，并改掉“新 hash 新 entry”表述；如果是授权 view/chunker 版本变化，需要明确 version/generation namespace 或原子重建规则。

**交回**：修正 SQL 唯一键/FK/索引、old-entry 不可用标记、generation 切换事务、空 chunk 组、零向量/部分 embedding coverage、重建中读哪个快照。RetrievalReceipt.index_snapshot_id 的真实 producer、持久内容和读取时点必须给出，当前表内无显式该身份来源。若采用整分区重建，说明旧 cursor 和 in-flight jobs 如何失效。

### Q12 — 检索排名、覆盖与资源

**交回**：精确ID/seq识别，words/trigram/vector 各路 query 生成、词法 escape、每路 topK 到全局 max_candidates 的合并顺序、RRF tie-break、同 record 合并 span 后 charge 重算。说明分页返回的是最终 topK 还是已扫范围的临时排名，跨页如何更新/去重，不能把仅部分扫描的 global topK 称 COMPLETE。

量化 max_retrieval_ms 的计时范围、vector 内存页大小、chunk 大小与 JSON bytes 双限制、max_session_disk_bytes 包含 DB/WAL/临时副本哪些项目、ENOSPC 背压。source journal highwater/index coverage/授权 readset 各自何时冻结；索引追加是否让所有 cursor 失效，需要明确稳定快照或进度协议。

短中文查询要有具体 fallback：FTS5 trigram 全文查询不能匹配少于3个 Unicode 字符的子串，不能用零命中冒充没有历史。官方依据：[SQLite FTS5](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)。

参考 `validate_vector([1e308,1e308],2)` 会因 norm 溢出返回零向量；生产与 oracle 都应采用稳定归一化或拒绝，并验证最终 float32 编码后有限且非零。此项是参考层具体问题，不是已发现生产输入泄漏。

### Q13 — durable job payload 与 lease 恢复

**证据**：arp_jobs.kind 有 INDEX/PURGE/BIND_IMPORT/SUMMARY；INTERFACES.resume_pending 仅列前三种，SUMMARY 的 producer/用途未确定；payload_json 只有通用 JSON 约束。

**交回**：每个保留 kind 的严格 payload/result DTO、语义 key、generation、来源 ref、原 invocation/receipt key、预算归属、attempt/deadline/backoff 与错误分类。SUMMARY 若不在本期使用就明确不创建/不启用，不暗增一次模型调用。

冻结 job claim→外部调用→本地 index commit→central ACK 的逐崩溃窗口：lease 超时不能证明 embedding 未发生；重领必须查原调用身份，避免重复付费。BLOCKED 重试与新 model/chunker 版本如何生成新工作；两个 worker 同时处理、取消与晚到结果如何回收。启动/tick 的真实 caller、批次上限、公平性和退出 drain 策略要具名。

### Q14 — lifecycle、dispose proof 与锁顺序

**交回**：Agent lifecycle 与 Session resource state 的联合转移表；close/turn cancel/Agent stop/owner完成/destroy 各自是否停止调度、阻断 Provider/tool、保留哪些读路。DRAINING 后已有活跃 turn 如何结束，不能因禁止后续 Provider 而永远留下 active turn；已有 UNKNOWN 必须继续原 reconcile。

CompleteSessionDisposal 当前仅列部分未类型化字段，正文却要求 active turn/call、UNKNOWN、pending imports、index/read handle、temporary roots 全量证明。补齐强类型完整集合、每个 producer、complete 条件与 root/generation/highwater/receipt 绑定。

冻结 FileGuard / orchestrator UOW / execution UOW / partition SQLite 的统一锁顺序。现有 ProviderBudgetGuard 声明 Orch→SDK，新增 FileGuard 不能形成反向等待。证明锁外 embedding、锁内 write、drain、purge、GC、restore 没有环；长 vector scan 持锁超时怎么退出。

QUARANTINED→ACTIVE 对同 root 索引修复与旧 backup 新 root 恢复必须区分；定义真实 rebuild 命令、marker schema/hash、rename 到 trash 后重启定位、windows busy、15分钟告警后的人工重试入口。销毁对象要同时校验 Agent/Session/generation，不接受任意路径。

### Q15 — 唯一 registry、Provider 定义与 scope

**证据**：有 Capability/Deployment Schema，却没有独立 ProviderDefinition；选择 priority 在 reference 的 ad-hoc candidate dict 中，权威字段未明确。原 SDK runtime_catalog 与 Host managed Skill/catalog 已存在；编排有多个 execution pool，附录 C 又定义每 execution root 一个 epoch。

**交回**：ProviderDefinition/priority/deployment/ref body 的正式来源或精确现有类型；CatalogStore 是现有哪一个 writer、ARP side表的具体职责、scope 是 project/user/root/realm 哪一级，多 execution pool 如何共享或隔离而不多写。不能靠同名“registry”假设等价。

定义 register/install/trial/admit/suspend/retire/health-refresh/deployment-change 的受信 command 与 epoch 策略；健康 probe 在事务外，如何原子落 immutable snapshot+epoch，不让频繁 probe 导致所有准备请求持续失效。区别旧版本仍可读取、当前可调用和单个 binding 的撤销。

### Q16 — Skill 全链与现有资源兼容

**交回**：QUARANTINED→TRIAL 的真实入口、隔离 eval scope issuer、允许工具/凭据、评估 dispatch/receipt、admit 重放与 suspend/resume/retire。仅有 install/admit/suspend 不能把中间状态留给实现者猜。

依赖在安装时必须精确解析，但依赖可未安装：选择拒绝候选还是保存具名 unresolved 并禁止准入。明确 dependency lock 的持久类型/hash、diamond 图冲突、版本升级/撤销、最大层数/节点计数。SkillUse 的 INSTRUCTIONS load 与 execute 何时各写 receipt，防止重复加载/双计 E。

SCRIPT 的 approved runner、固定参数、环境白名单、输入/输出 schema、超时/子进程结束、退出码0但输出无效、缺 output file、stdout artifact 上限与原 executor 对齐；WORKFLOW 未部署明确 UNAVAILABLE，不以此逼重写引擎。沿已有 workspace/Operation 政策，不为计划另建一套新的通用沙箱边界。

native skill.json 与 SKILL.md-only 的精确 import 规则：frontmatter 重复键、类型/YAML 安全解析、name/description/兼容字段、路径规范与平台可接受范围、未知执行元数据。导入元数据不能产生权限。Agent Skills 格式主要定义 SKILL.md/frontmatter/目录布局，ARP 执行/准入字段属于本平台补充：[Agent Skills specification](https://agentskills.io/specification)。

必须映射原 Host 项目/全局 Skill 安装与冻结 resolver，明确新老目录/UI/默认 factory 关系，不能形成“旧页面已安装、新 ARP 永远不可见”的分叉。

### Q17 — DTO 体积与披露范围

**证据**：HOST-DTOS 要 catalogue definition 完整、单项≤64KiB、不能截字段；Skill 允许1024文件。结构合法800文件项实测123548 bytes，超过单项限制，分页不能拆开一个 item。

**必须决定**：使用有界 summary＋受权 details/ref 分页，或在 admit 前用实际编码字节限制可安装定义；优先前者，避免工具/UI限制反过来任意拒绝正常包。保持 metadata 与真实 source refs，模型 discover 不必返回完整管理定义。

统一每个 verb/模型工具的 item/body/request/manifest 限额、超大单 record/file/definition 错误，不允许截断后假称完整。独立定义包含 Deployment credential_ref_name、endpoint_namespace、路径的管理视图与模型视图，不能靠“无密钥”推导所有配置 metadata 都可公开。

### Q18 — 错误、事件和内部类型词典

**证据**：Error.code 是任意字符串；seams 的 PROFILE_SOURCE_MISSING/JOURNAL_SOURCE_UNAVAILABLE 等与 FIELD-CONTRACTS §5 码表并非同一集合；NOT_REQUESTED/COMPLETE_EMPTY/ERROR 等有时作状态，有时作说明，必须映射清楚。INTERFACES 的 CompleteSessionDisposal、原 TokenReceipt、range receipt 等不是完整可导入类型。

**交回**：Error code→stage→retry→是否消费预算→是否允许新ID→公开脱敏→HTTP/WS映射；内部完整 DTO 或精确复用类型路径。各状态“空”“未请求”“读失败”“部分”必须对应可表达、无歧义的字段组合。

对新 AgentContextPolicyAdopted/RuntimeContextExposed/session/job/catalog 事件，定义 payload、原 writer、事务归属、dedupe key、eventseq、版本兼容、Host projector；如果复用原事件则列字段映射。已有语义回放存在历史 PARTIAL，不要继续增加未登记事件再宣布 replay 完整。

### Q19 — migration、PRAGMA、保留根

**交回**：execution DB 与 orchestrator DB 分别的 migration runner/descriptor/当前号；TaskGraph 交接写 HTN23/24、TG25，这是编排库来源事实，不能机械拿 execution 下一号也设26。保留旧 checksum，实际盘点选择编号。

本包依赖 recursive_triggers=ON 防 REPLACE 删除绕过，当前原 Database.open 未显式设置：选择修改并断言所有真实 writer/recovery 连接，或增加不依赖该 pragma 的完整冲突防线；禁止业务 OR REPLACE 仍保留。以真实 runner 验证 FK父键、trigger解析、WAL、事务回滚，不拿最小父键fixture代替。

明确旧 Agent 的合法 legacy marker 来源、缺 marker 拒绝、旧 activity 不重绑；首次 bootstrap 失败如何回滚/重试。ARP tables 不可删与原隐私/retention 删除流程如何协调：审计最小元数据、CAS正文、Journal、profile/roots 的持有与释放分别明确，不能只因临时 DB 可删就忽略中央库长期增长。

GC root 必须列所有实际删除入口、跨 execution/artifact/orchestrator 的 transfer protocol、copy/pin/hash核验和崩溃恢复。无临时依赖的正式 review pin 不阻止索引删除，仍有依赖时不能假 transfer。

### Q20 — 验收与实施准备门

**交回**：Q01–Q19 到 source/seam/field/SQL/API/case/mutation 的映射，明确哪个构造器校验跨字段业务规则，不能只检查 nonempty producer 名称和 schema subtree hash。

修正现有映射错位：BODY-WIRED BW05 把 capability 指向 T01–T04，实际 provider-selection 在 K01/K02；MU10 权限并集只绑定 K07 promotion，需指向确实检查权限交集的行为断言。Rxx 同时用于 seam 与 testcase，请使用 `seam:R01` / `case:R01` 免误读。新增反例按语义覆盖，不为固定“60”删减用例。

主体完成后保留原声明的60场景/16mutation/stateful/相关旧回归/真实模型/原生范围；如重新组织执行矩阵，必须说明等价覆盖与所有后置项，不能把“待执行”改成“免测”。模型12局须预登记输入/独立oracle/故障注入点/预算/失败保留策略，区分机制硬不变量和概率业务成功；不在本次计划修订阶段先跑大批量模型。

平台要求按部署事实落实：当前 macOS 必须有原生 UI 链；原包要求的 Linux 部署需明确运行地点/制品/必测功能；Windows 没测维持 PENDING，不能继承 Linux 路径测试。只有 UI/平台确有环境依赖才列具体依赖，不擅自增加容器/远端部署。

给出首次 ARP 启用后的完整用户操作路径：创建→输入→Context/历史→改设置→发现/加载/执行Skill→撤销→close/destroy→重连。验收时核实际导入 wheel/hash、独立userdata/端口；Tauri自管backend/vite，避免双启动。

## 5. PlanAgent 输出要求

请一次性交回完整修订包（建议 ARP-EXEC-1.1，版本名可自行统一），包含：

1. **DECISIONS.md**：Q01–Q20 逐项 `RESOLVED / MAPPED / ENVIRONMENT_DEPENDENCY / OPEN_CONFLICT`，唯一决定、被改文件/章节、关闭依据。不得用“实现时自行处理”关闭协议空缺，也不要求 NEW 模块已经开发。
2. 一份当前主计划；旧 inputs 明确仅追踪材料，不能覆盖新裁定。把附录最终规则合入对应正文，避免读者漏掉后置修正。
3. 同步 strict Schema、字段 producer/resolver、typed internal interfaces、Host/model tool DTO、错误事件词典、原事务时序、DDL/查询、迁移与 lifecycle 表。
4. 更新 source-map/integration-map/seams。能读当前源码则真实定位；不能读则保留 UNVERIFIED 并给精确本地核验清单，不发明 actual hashes。这里已有静态定位可作为起点，但不能冒充最新 Assurance 字节。
5. 语义有效的样例与窄反例、source→contract→case→mutation 追踪表、BODY_WIRED 定义、统一最终验收计划。参考测试和生产验收严格分开。
6. 每种真实资源的依赖表：模型计量器、embedding、runner、FTS/锁/平台、安装制品。只记录引用与 hash，不包含凭据。缺项明确失败码与受影响功能，不能默认空/true。
7. 完整交付 manifest/hash；给出精确可执行的轻量包检查命令。报告实际运行了什么，不继承旧116 PASS到新包。

建议实施前关闭条件：已证实合同冲突 Q01/Q02/Q11/Q17 全部消除；关键内部/Host/API/事务协议没有互斥定义；当前集成父源码与 owner 已确定；首模型计量和 embedding 路径可落实；所有剩余 NEW 项是明确开发任务而非设计占位。**这叫“实施准备充分”，不保证数学意义的100%零缺陷。**

不应要求：先跑全 SDK 回归、先写完全部 NEW producer、先证明所有平台/所有模型、重做 HTN/TaskGraph/Assurance、恢复已被用户后置的大规模评测，或重新讨论 Pi。

## 6. 无本地源码访问时的处理方式

这份 handoff 本身包含已发现问题的完整触发条件与关键源码事实；配套 ZIP 含原始计划包。PlanAgent 即使没有本机路径访问，也可以修订语义与 DTO。

涉及实际后继 Assurance、32项最终路径/hash、真实资源安装的部分，生成一份可交 WorkAgent 执行的有界盘点表，写清读哪个文件/符号、确认哪个不变量、输出哪些非敏感指纹。它不能因为没有本地访问就填伪哈希，也不应将本 handoff 的疑问再次原样转给用户。

如果具体接缝与推荐方案冲突，按 `Q编号 / 精确来源 / 不可同时满足的两项语义 / 推荐选项 / 影响范围` 一次性报告。其余普通命名与适配决策直接完成。

## 7. 本次研究与校准说明

SQLite FTS5、PRAGMA、Agent Skills 官方页面已只读获取，抓取 hash 存于原始证据目录；官方资料只解释第三方行为，不替代本项目授权与持久执行合同。[SQLite PRAGMA](https://www.sqlite.org/pragma.html#pragma_recursive_triggers)。

上一轮评审中“当前默认 tokenizer 为估算”的事实只适用于通用 fallback；本轮扩大源码盘点后发现专用 DeepSeek/HF 计量器，已在 Q05 修正。上一轮关于可替换 Runtime 的全部建议依用户新裁定移出范围。上述更正应直接写进新主计划，不保留成互相矛盾的附注。
