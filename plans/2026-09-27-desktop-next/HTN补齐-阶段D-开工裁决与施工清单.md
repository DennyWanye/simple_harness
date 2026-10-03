# HTN 补齐 · 阶段 D（输入来源与桌面观察）：开工裁决与施工清单

- 裁决人：独立裁决子代理（只读代码与文档，除本文件外没有改任何文件、没有跑测试）。日期 2026-10-03，代码以主分支 `8c3a157a`（SDK opt.144）为准。
- 依据：`HTN补齐计划-2026-10-02.md`（第 3.10 版）阶段 D 全节、表二 1a/2/3/10/19、第五节③⑦、第六节；`TaskGraph-补全-方案.md` 2.1～2.4；`HTN补齐-开工前裁决-2026-10-02.md`（下称"前次裁决"，作用域纪元口径）；`HTN补齐-阶段C-偏差裁决-知识支持集合.md`；`HTN补齐-实施记录.md` C-1、C-2；v1.4 原计划 ADR-13（`complete-plan.zh-CN.md:198-215`）与 §9.4。
- 口径：判断交给 LLM，Harness 只管约束与秩序；同一件事只留一条路径、只记一处；旧路径直接删、不做兼容；做好的功能默认开启。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`。

---

## 〇、一句话结论

阶段 D 能做，**共 9 步（D0～D8）；新增 1 个迁移（39）；编码清单重写 1 次（只因 `contracts/htn.py`）；不改任何工具说明（执行池身份不变）；规划器提示词升一次版（v19→v20），执行者、审阅员不动。** 动保证通道一处：删掉从没接上的"存储规则 + 部署观察"验收支持路径，证书完整读集少两张恒空的表、收尾复查不再比查询集——不删的话，D 一写桌面观察，任务里每张验收证书都会"依据已变"。偏差单 7 张，其中 3 条建议报用户知悉（第五节）；**没有需要用户本人决定的事**。

---

## 一、开工前裁决

### 1.1 待定③ 整数图版本闸门：删，由计划修订号取代

**结论：删整数闸门与 `base_graph_version`；计划修订号（提交第 6 道 `PLAN_REVISION_STALE`）是唯一的结构并发闸门，语义读集是第二层。ADR-13 第 1、4 条按此改写（偏差单 1），结论报用户。**

- 事实：`graph_version` 全库没有写方。提交第 4 道读 `mission.final_report["graph_version"] or 1`（`SDK/orchestrator/plan_commits.py:652`），命令也按同一读法填（`SDK/orchestrator/hierarchical_dispatch.py:3386-3391`，注释自认"提交不推进 graph_version"），恒为 1==1；对外快照给 0（`SDK/api/facade.py:723`）。ADR-13 第 1 条的前提"旧模式与新模式共用、`allow_rebase` 不改"已不存在：平面模式已删，`allow_rebase` 只剩一句注释（`plan_commits.py:647`）。
- 第 6 道要求 `base_plan_revision == 当前`（`plan_commits.py:687-708`），任何两份并发提案必有一份被拒——这正是整数闸门想做的粗粒度并发控制。"写上"就要给它找写方、定义何时 +1，定义出来就是计划修订号。
- 改动面：`plan_commits.py:9-18`（模块文档）、`:189` 字段、`:229` 意图哈希、`:642-659` 第 4 道、`:1288`/`:1341` 事件与回执；`hierarchical_dispatch.py:3386-3391`；`facade.py:723`；`Host/diagnostics.py:421`；`SDK/orchestrator/attempt_execution.py:27`（死的记账键）；测试 `T/full_target/test_plan_commits.py:331,340,545,614-621`、`test_hierarchical_event_flow.py:231`。不碰保证通道，`plan_commits.py` 不在编码清单里。**`backend/deskpet/workflows/` 里的 `graph_version` 是工作流引擎的另一件事，不动。**

### 1.2 计划提交核对哪些读集（表二 10）

**结论：读集只记"计划修订号管不到、在提案与提交之间会变"的东西。保留：要求修订、目标契约、做法、前提见证、沿用的验收（`SDK/planning/htn/compiler.py:1323-1408` 已有）。新增两样：①被细化目标及共享目标的义务（OBLIGATION 通道：生命周期、结论引用、形状变更数）；②作用域纪元（`scope_epochs`，取这次规划请求绑定时的那一份，去掉 `assurance:mission`）。删三样：`manager_epoch`、`budget_grant_revision`、`support_sets`。"不存在"不加。**

- 核对器已能核义务与纪元（`SDK/orchestrator/_read_set.py:408-427,463-476`），缺的是产出方：编译器只给 5 类，`SDK/planning/htn/graph_repair.py:111,264` 两处更少。
- 不加"不存在"：三个谓词（`_read_set.py:79-82`：采用了做法实例、开了义务、加了先后边）只有计划提交能改变，第 6 道已把计划提交完全串行化；全库也没有一处产出它们。
- 预算与授权不进读集：预算在提交事务里现读（第 9 道，`plan_commits.py:907`），规划授权由 H1-H 准入在提交事务里重读并比身份（`SDK/orchestrator/planning_admission_commits.py:150-190`）；`budget_grant_revision` 恒为 0、核对器没有权威来源（`_read_set.py:281-298`）。
- **必须与纪元写方同批前做**：编译出的读集从不填 `manager_epoch`（恒 0），第 3 道却拿它比当前纪元（`plan_commits.py:620-640`）——纪元一到 1，每份提案都会 `MANAGER_EPOCH_STALE`。所以 D1（删 `manager_epoch`、补 `scope_epochs`）必须在 D7（接写方）之前。

### 1.3 删 `manager_epoch`；问用户的题目不再绑纪元

**结论：按前次裁决 1.3 删 `manager_epoch`（只留 `scope_epochs`），连带删 `PlanPrincipal`/`ResolutionPrincipal` 的同名字段与两处 `MANAGER_EPOCH_STALE` 闸门。问用户题目的绑定只留计划修订号与要求修订号。**

- 涉及：`SDK/contracts/htn.py:2358,2371,2397,2424,2453`；`SDK/graph/eligibility.py:281,301,1193,1297,1461`；`plan_commits.py:167,620-640`；`SDK/orchestrator/resolution_commits.py:1801-1812`；`composition_review.py:320,413`；`leaf_acceptance.py:516,718`；`root_review.py:927`；`operation_outcomes.py:656`；`event_handler.py:4251,6805,6992,8888,9019,9438`；`hierarchical_dispatch.py:832,2345`；`SDK/storage/htn_store.py:1813-1819`；`SDK/storage/planning_human_store.py:61`。接受一侧的读集本来就带 `scope_epochs`（`leaf_acceptance.py:714-720`、`composition_review.py:409-415`），删后屏障不变。
- 问人题目：绑定把纪元算进去（`planning_human_store.py:54-62`、`event_handler.py:8887,9017-9019`），`retire_stale` 会把待答题目（含阶段 C 的裁决卡）标"过期"。纪元管的是依据凭证（见证）还能不能用，不是这个问题该不该问；观察一变就收回用户面前的卡片，等于把系统内部事实变成对用户的打扰。新事实规划器下一轮会看到，题目还需不需要由它判断。偏差单 6，报用户知悉。

### 1.4 补全方案 2.1～2.4：现在到了哪一步、还差什么

**结论：四条都还没开始。2.2 在桌面里不会触发（只有一种格式），是补洞；2.1、2.3、2.4 是真缺口。**

| 条 | 现在 | 还差 |
|---|---|---|
| 2.1 跟随授权版本 | 编译器写死"钉住"（`compiler.py:717`）；`AcceptedOutputsIndex.authorized_revisions`（`SDK/artifacts/input_bindings.py:240`）与 `ResolutionPolicy.pinned_revisions`（`:314`）都没有生产方，`hierarchical_dispatch.py:1602-1605` 构造不填；`_select_by_revision`（`input_bindings.py:590-628`）因此两种策略都落到"候选任选、有歧义报告"；共享校验写死"钉住"（`SDK/graph/taskgraph_sharing.py:117-121`）；冻结输入复核对非钉住边比授权版本（`SDK/artifacts/taskgraph_inputs.py:53-56`），与 §5.5"已冻结的不跟随"相反；规划器无法声明钉住（`OutputValue` 只有 step/port，`htn.py:345-354`） | 填授权版本；编译器默认跟随；`OutputValue` 加可选 `pin`；钉住值取该消费者第一次冻结的输入记录，没有就取当时的授权版本；授权读不到报"来源不可用"不取最新；共享校验对照编译器实际策略；冻结复核删跟随比对（D0 先确认） |
| 2.2 子目标端口格式 | 编译期已严格比格式（`compiler.py:687-696`）；子目标端口按同名对到收尾步骤（`hierarchical_dispatch.py:1608-1660` `goal_port_outputs`），不比 `schema_ref`；桌面只有 `desktop.workspace-outputs` 一种（`Host/hierarchical.py:25-27`） | 别名处比格式，不一致不出别名，消费者报"数据未绑定" |
| 2.3 同名写入 | `resource_writes` 只来自类型声明（`SDK/planning/htn/grounding.py:812`），桌面类型为空，冲突检查（`SDK/graph/projection_validation.py:805-880`，已跳过有先后的两步）没有输入；同名产出在 `overlay_attempt_inputs`（`hierarchical_dispatch.py:3426-3490`）按"先到先占"静默取舍 | 编译时把 `file:X` 要求链接到的叶子记成写入目标；运行期两步之间没有先后、已验收产出撞同一路径 → 不取舍，记"写入冲突"修复请求 |
| 2.4 共享 | 桌面内容步骤 `LOCAL_WRITE` 且无 `effect_identity`（`Host/hierarchical.py:86-93`）→ 注册检查（`SDK/planning/htn/registry.py:360,1586-1592`）与共享检查（`grounding.py:301`）一律拒绝；产品同形世界是同一声明的副本（`SDK/testing/product_world.py:34-` `user_goal_world`）。规划器的表达入口已有：`BIND_EXISTING_GOAL` + `sharing_candidates`（`SDK/runtime/role_templates.py:231-233`、`event_handler.py:4419-4422`） | 两处类型加固定效果身份并进类型体哈希；提示词讲清内容步骤可共用 |

### 1.5 两张支持集合表与 `SupportSetRead`：删；验收评估器去掉部署观察与存储规则输入

**结论：删 `justification_sets`、`support_members` 两表与读集里的 `SupportSetRead`/`support_sets` 通道；同时删验收评估器的"存储规则 + 部署观察"输入，收尾复查不再比查询集。**

- 事实一：两表生产无写方（阶段 C 偏差裁决）。存储规则也从没接上：`AssuranceDeploymentPorts.resolve_signature`/`admitted_rules` 默认 `None`（`SDK/orchestrator/assurance_assembly.py:321-322`），唯一构造处不传（`SDK/deployment/assembly.py:90-96`）。评估器里，没有签名解析器时存储观察一律被选择器拒为未注册谓词，没有准入规则时存储规则一律不生效（`SDK/knowledge/assurance_sources.py:476-545`）——**验收结论只由审阅与检查两类锚点决定，观察和两表对结论没有任何影响。**
- 事实二：可证书完整读这三张表（`SDK/storage/assurance_reads.py:279-291`），收尾复查只比这三类查询集（`SDK/orchestrator/assurance_recheck.py:25,45-76`），有效性观察也走同一函数（`assurance_consumers.py:216-227`）；比出"变了"就记修复请求，只有改计划或重新验收才能消掉（`planning_repair_requests.py:249-…,560-588`）。
- 为什么 D 非动不可：`observations` 是通道源表。D 要在任务进行中写桌面观察（取证、每轮重读，见 1.7、1.8）；任何一条新观察都会让此前每张验收证书在收尾"依据已变"（与阶段 C 写支持集合同一机理），任务被逼重做已通过的步骤或失败。D0 先用一条用例跑出来。
- 删的范围：迁移 39 先删两表触发器再 `DROP`；源表清单 `assurance_source_inventory.py:13-14` 及其列表、主键表删两项；`evaluate_acceptance_support` 删 `observations`/`justification_sets`/`resolve_signature`/`admitted_rules` 与 `_admit_stored_rules`；`assurance_validity.py:501-533` 不再解两表与观察，`:830-860` 的 `_snapshot_sources` 删；两个端口删；`EVIDENCE_QUERY_KINDS` 删，`changed_items` 只比钉住对象；`htn.py:2250-2286`、`_read_set.py:438-461`、`eligibility.py:337,1195,1311-1320`；`HtnStore` 的 `insert/get/list_justification_set`、`support_dependency_edges`（`htn_store.py:936,1428,1512,1520,2371`）；`SDK/orchestrator/repair_impact.py:53`；`htn_schema.py:466-480`；重放清单两条。
- **对证书完整读集的影响：碰。** 完整读集少两张（本来恒空的）表，所有证书读集指纹随之变化，开发库旧证书不再有效（开发期不兼容，新建编排数据目录，同阶段 C 删金额）。证书签发时机、"准备→提交"短屏障、保证通道纪元、钉住对象的核对都不动；`observations` 仍是通道源表，写入照旧给通道纪元 +1（只记说明，不判过期）。
- 偏离 AER（存储规则与部署观察参与验收支持、I02 反证场景）：产品只做桌面、用户已定不做跨领域，桌面没有准入规则。偏差单 3，报用户知悉。

### 1.6 按"要完成的那件事"汇总失败与花费（表二 19）：不另记，读时推出

**结论：不往义务行写数。新文件 `SDK/orchestrator/obligation_accounts.py` 一个函数 `obligation_accounts(store, mission_id)`，按义务汇总"它和挂在它下面的所有义务"的尝试数、失败尝试数、已结算 token、用量未知的尝试数；读方是规划包的义务视图、诊断导出、对外快照。删义务表三列与两个从没被调用的写函数。不改任何上限。**

- 事实：`ObligationStore.record_failure`/`record_spend`（`SDK/storage/obligation_store.py:175-221`）生产零调用，义务表 `failure_count`/`spent_tokens`/`spent_attempts` 恒为 0；但规划包义务视图正读着它们（`SDK/orchestrator/planner_views.py:121-123`）——**现在给规划器的是假零**。
- 归集：尝试 → 任务 → `task_semantics.obligation_id` → 沿 `parent_obligation_id` 向上逐级加总。换做法后的新子义务仍挂在同一目标义务下，所以"拆分、换做法、换执行者都不重置"自然成立。失败口径用任务尝试计次的现行判定（D0 找到它），基础设施失败照 09-28 决定不计；花费取预留与结算记录。
- 不进任何绑定：`SDK/orchestrator/taskgraph_plan_sources.py:205-210` 的预算通道与需求通道都排除这四个数（需求通道现在带 `failure_count`，接上后一次结算就会让在途规划过期）。规划包在请求时冻结（`event_handler.py:4202`），不受结算影响。
- 理由：花费与尝试本来逐条记在预留与尝试上，再写一份到义务行是同一事实两处记，且每次结算都会写一张通道源表（义务表有屏障触发器）。删列照迁移 38 的做法（先删 `assurance_source_obligations_update` 触发器、删列、照原文去掉这三列子句重建，`SDK/storage/schema.py:716-750`），并进迁移 39。

### 1.7 桌面谓词声明与三样只读观察：实现在 SDK 一份，Host 注册

**结论：新文件 `SDK/planning/htn/observers/workspace.py`（与 `code.py`、`appworld.py` 同层），提供 `workspace_predicates()` 与 `workspace_observers(store, mission_id)`；`Host/hierarchical.py:21` 与 `product_world.py:43` 都调用它注册。`build_planning_world`（`SDK/planning/htn/world.py:404-493`）加参数 `predicates: Sequence[PredicateSignature] = ()`，在建观察器索引（`:488`）之前注册。"传空的地方"是两处 `observers=()`；`domains=()` 保持（桌面不装种子领域）。**

- 三个谓词，都是 CLOSED、各一个观察器、`authority_scope=None`：
  - `desktop.file-present@1(path)`：任务文件视图里有没有这个路径；
  - `desktop.file-sha256@1(path, sha256)`：该路径现行内容的哈希是否为给定值；
  - `desktop.source-current@1(path, version_hash)`：资料表里该路径当前有效版本是否为给定版本（`SDK/storage/schema.py:466-480`，`superseded_by IS NULL AND revoked=0`）。
- "任务文件视图" = 工作区种子（`mission.final_report["workspace_seed"]`）∪ 已验收产物在各路径上的现行版本，"现行"与阶段 C `knowledge_standing` 判产物是否现行用同一个函数。同一路径同时有两份现行产出（并发写，见 2.3）→ 答"观察器不可用"并写明原因、不写记录。
- 观察记录 `coverage=AUTHORITATIVE_WITH_SCOPE`（这样 FALSE 才可采纳，`observers/__init__.py:18-25`），`valid_until_ms=None`，新鲜度由纪元管（1.8）。只读库，不碰磁盘、不调模型。
- 取证只有一条路：规划器 `REQUEST_EVIDENCE`（`event_handler.py:6636-6645`，`SDK/orchestrator/planning_evidence.py`），装上观察器后 `evidence_predicates` 自动非空（`planner_views.py:217-219`）。不给桌面注册"观察类步骤类型"；自动取证轮的"未知前提"那半（`hierarchical_dispatch.py:3050-3141`）在桌面里因目录没有观察类型而不发问，保持现状（第六节）。
- 前提：规划器在 `applicable_when` 写 `{"op":"predicate","predicate_ref":…,"arguments":…}`（`htn.py:515-520` 已支持）；预览时未知 → `EVIDENCE_REQUIRED`（`SDK/planning/plan_preview.py:171`）；做法每个子步骤开工前按 SELECT 再核（`eligibility.py:846-857`，开工见证每轮重发 `hierarchical_dispatch.py:1303-`）。

### 1.8 作用域纪元的写方：观察唯一写入口的同一事务里写；外加"每轮重读"

**结论：写方放在 `HtnStore.insert_observation`（`htn_store.py:1368-1403`）同一事务：插入前后各按 `EvidenceEntry.from_observations`（与 `world.snapshot()` 同一算法）算一次该命题真值；插入前是 TRUE/FALSE、插入后不同（含变冲突、变未知）→ `bump_epoch(mission, scope_id, bumped_by="observation:<编号>")`；插入前是未知的首次观察不加。E 的要求修订是第二个写方（E 做）。另在 `run_evidence_round` 加"重读"：对本任务已记录过、观察器能读的命题逐个重读，真值与当前快照不同才写新观察（随之加纪元），没变不写。**

- `bump_epoch` 自带事务，嵌套安全（`SDK/storage/store.py:409-428`），同事务已记 `validity_epoch` 来源变更（`htn_store.py:1575-1579`），通知已认这一类，不另接。
- 为什么要"重读"：桌面的"世界"是种子、已验收产物和资料表，步骤一验收、资料一换版，命题就可能变；开工门只读已记录的观察，不重读，前提见证会一直按旧的 TRUE 放行。`run_evidence_round` 每轮在 `before_planning` 里被调（`event_handler.py:3578`），按变化写，不会空转（`_gather_evidence` 只在真写了观察时报进展，`:3661-3690`）。
- 与前次裁决的关系：前次裁决 1.2 第 3 条是"真值和上一次快照不同时加纪元"。"未知→已知"不加是细化，不是推翻：未知时没有发出过可用见证（开工见证每轮按支持修订号重发、取最新，`hierarchical_dispatch.py:1487-1514`），不需要纪元收回；加了只会让在途规划答复与接受命令白白过期。偏差单 5。

### 1.9 纪元引起的过期拒绝不算规划器答错

**结论：错误码表加一列"是否计入答错"，"请求过期"类不计；`_planning_attempts` 读这一列。上限由任务总额度兜（已有）。**

- 事实：`event_handler.py:5989-6013` 只豁免"没回复的回合"，`REQUEST_BINDING_STALE` 等过期拒绝照样计入 3 次上限；接上写方后，一次文件变化就可能扣规划器一次。前次裁决 1.3 已定"原地重发、不扣规划次数"，这里落实。提交阶段的 `READ_SET_STALE`、`PLAN_REVISION_STALE` 若以拒绝事件记账，同样不计（D0 确认它们的记账路径）。
- 错误码表文首已预留"按类型码决定秩序就在这里加一列并让生产代码读它"（`SDK/contracts/error_table.py:3-6`），只此一处读。

---

## 二、现状核对表

| 计划说的 | 代码现在（文件:行） | 结论 |
|---|---|---|
| 整数闸门 | 第 4 道恒等，无写方（`plan_commits.py:642-659`，`hierarchical_dispatch.py:3391`） | 删（1.1） |
| 读集"不存在" | 核对器有三谓词、无产出方（`_read_set.py:79-82,478-527`） | 不加（1.2） |
| 读集"集合/支持修订号" | `SupportSetRead`（`htn.py:2250`）无产出方；两表无写方 | 删（1.5） |
| 读集"有效性纪元" | 计划提交读集不带；`manager_epoch` 恒 0（`compiler.py:1402-1408`） | 补 `scope_epochs`，删 `manager_epoch`（1.2、1.3） |
| 读集"预算与授权修订号" | `budget_grant_revision` 恒 0、核对器无来源（`_read_set.py:281-298`） | 删；提交事务里现读（1.2） |
| 2.1～2.4 | 见 1.4 表 | 全部要做 |
| 义务失败与花费 | 写函数零调用、三列恒 0、规划包读假零（`obligation_store.py:175-221`，`planner_views.py:121-123`） | 读时推出、删三列（1.6） |
| 桌面谓词与观察器 | 世界建时 `observers=()`、谓词表空（`Host/hierarchical.py:21`，`product_world.py:43`） | SDK 一份实现、两处注册（1.7） |
| 作用域纪元写方 | 只有 `bump_epoch`、只有测试调用（`htn_store.py:1552`） | 观察写入同事务（1.8） |
| 规划器提示词 | `planner-hierarchical-v19`（`role_templates.py:113`）；条件数组没讲写法（`:266`）；禁止字段含两个将删字段（`:139-141`） | 升 v20（D8） |

---

## 三、施工清单

**结论：9 步。最重的是 D1（读集与闸门，约 20 个文件）、D3（保证通道与迁移）、D7（观察器与纪元写方）。**

| 步 | 内容 | 依赖 | 迁移 | 编码清单 | 提示词 |
|---|---|---|---|---|---|
| D0 | 金丝雀核对 | — | 无 | 不动 | 不动 |
| D1 | 读集与闸门（1.1～1.3、1.9）+ `htn.py` 全部改动 | D0 | 无 | **重写一次** | 不动 |
| D2 | 义务汇总账代码侧（1.6） | D0 | 无 | 不动 | 不动 |
| D3 | 删存储规则支持路径 + 迁移 39（1.5，含义务表三列） | D1、D2 | **39** | 不动 | 不动 |
| D4 | 2.1 跟随授权版本与声明钉住、2.2 | D1（`pin`） | 无 | 不动 | 不动 |
| D5 | 2.3 写入目标与写入冲突 | D0 | 无 | 不动 | 不动 |
| D6 | 2.4 共享 | D4（共享校验） | 无 | 不动 | 不动 |
| D7 | 桌面谓词、观察器、纪元写方、每轮重读（1.7、1.8） | D1、D3 | 无 | 不动 | 不动 |
| D8 | 规划器提示词 v20、清单、文档、发版 | 全部 | 无 | 不动 | v20 |

### D0　金丝雀核对（半天）
1. 产品同形世界两步任务：第 1 步验收后直接 `HtnStore.insert_observation` 一条，跑完——确认收尾报 `EVIDENCE_STALE`（1.5 的前提）。这条留作 D3 的回归用例。
2. `PlanningRejected` 里过期类拒绝（`REQUEST_BINDING_STALE`、提交阶段 `READ_SET_STALE`/`PLAN_REVISION_STALE`）的记账路径与 `_planning_attempts` 的计数，确认 1.9 的改点。
3. 接受一侧因纪元过期被拒（`resolution_commits.py:1820-1827`）后，下一轮是用同一正式记录重建命令提交，还是重新审阅；后者写进实施记录（纪元只在观察真值变化时动，频率低，不另改）。
4. `taskgraph_inputs.py:53-56` 在同一尝试恢复时是否会因"跟随"而拒；会则 D4 删这条比对。
5. 桌面任务的规划授权是否放行 `REQUEST_EVIDENCE`（`planning_evidence.py:14-26` 的 `allowed_decisions`）。
6. 找到任务尝试计次的现行判定函数（基础设施失败不计那一份），D2 复用。
7. 核一次：内容步骤的写只落在本次尝试的隔离工作区、对外发布由系统步骤负责（2.4 第 1 条）。

### D1　读集与闸门
1. `htn.py`：`SemanticReadSet` 删 `manager_epoch`、`budget_grant_revision`、`support_sets`（字段、校验、`to_json`、`from_json` 可选键）；删 `SupportSetRead`；`OutputValue` 加 `pin: bool = False`，`to_json` 只在为真时写 `"pin": true`，值解析接受可选 `pin`。**编码清单 `SDK/graph/network_codec_manifest_v5.json` 在本步重写一次**，实施记录写明旧图历史读不出。
2. `compiler.py:1323-1408` `build_read_set` 加参数 `scope_epochs: Mapping[str,int]` 与 `obligation_reads`：写被细化目标与各共享目标的 OBLIGATION 项（用 `SemanticReadSetChecker.read_item`，提案与核对同一公式）与 `ScopeEpochRead`（去掉 `assurance:mission`）；`graph_repair.py:111,264` 同样补。纪元取值：规划请求绑定时的那份（`event_handler.py:4410` `epochs`，同 `planning_scope_digest` 的来源），经预览输入传进编译器。
3. `plan_commits.py`：删第 3、4 道与 `base_graph_version`（1.1 列的各处）；`PlanPrincipal.manager_epoch` 删；模块文档改写为"计划修订号是结构闸门，读集逐项核"。
4. `_read_set.py`：删 `_check_budget_grant`、`_check_support_sets`、`budget_grant_resolver`、`channel_names` 里对应两项；模块文档 11 通道表改写。
5. `manager_epoch` 其余各处（1.3 列表）删；`ResolutionPrincipal.manager_epoch` 与 `resolution_commits.py:1801-1812` 删；`htn_store.py:1806-1826` 的 `record_read_set` 索引行：删两行，加每个 `scope_epoch` 一行与义务行。
6. 问人题目绑定：`planning_human_store.py:54-62`、`event_handler.py:8887,9017-9019` 去掉纪元。
7. `facade.py:723`、`Host/diagnostics.py:421`、`attempt_execution.py:27` 删 `graph_version`。
8. 1.9：`error_table.py` 的 `ErrorEntry` 加 `charges_planner: bool`（`REQUEST_STALE` 类为假），`_planning_attempts` 读它。
9. 决定解码器与做法提案的禁止字段表（`SDK/planning/decision_codec.py:80-82`、`SDK/planning/htn/method_proposals.py:78-80`）随 D8 一起改（模型写了就按"多余字段忽略"，用户 09-26 决定）。

### D2　义务汇总账（代码侧）
1. 新文件 `obligation_accounts.py`：`obligation_accounts(store, mission_id) -> dict[义务编号, {attempts, failed_attempts, settled_tokens, unknown_usage_attempts}]`，按 1.6 归集（含下级合计）。
2. `ObligationAccountView` 的 `consumed_attempts`/`consumed_tokens`/`failure_count` 改由它填；`planner_views.py:121-123` 形状不变、值变真（规划包版本不升）；`taskgraph_plan_sources.py:205-210` 两个通道都排除这四个数。
3. 删 `record_failure`、`record_spend`、账本里的同名算术与 `load_ledger`/`persist` 对三列的读写（`obligation_store.py:175-221,499-570`、`SDK/contracts/obligations.py` 同名部分）。
4. 对外快照（`facade.py` `snapshot`）加 `obligation_accounts`；Host 诊断导出加一节读它（E 的"预算去向"读同一处）。

### D3　删存储规则支持路径 + 迁移 39
1. 迁移 39（`SDK/storage/schema.py`）：删两表上的屏障触发器、`DROP TABLE justification_sets, support_members`；照迁移 38 删 `obligations` 三列并重建更新触发器。`htn_schema.py:466-480` 同步。
2. `assurance_source_inventory.py`：`MISSION_TABLES`、`SOURCE_COLUMNS`、主键表删两表；`obligations` 列表删三列。钉住清单的测试按当前清单重生成。
3. 评估器、证书签发、收尾复查、读集、`HtnStore`、`repair_impact.py:53`：按 1.5 删。
4. 重放清单 `SDK/observability/business_replay_inventory.json`：两表条目删；`obligations` 条目删三列与 `record_failure`/`record_spend` 写方；守护测试跟着过。
5. 本步之后 D0 第 1 条用例转绿。

### D4　2.1 跟随授权版本与声明钉住；2.2 子目标端口格式
1. `accepted_outputs`（`hierarchical_dispatch.py:1553-1606`）填 `authorized_revisions`：每个生产者端口取当前计数的那次验收（`preparation_acceptance_ids`）对应产出的版本；一个端口出现两个计数中的验收 → 不填并记原因。子目标端口（`goal_port_outputs`）按收尾步骤的同一取法。
2. `compiler.py:717`：默认 `FOLLOW_AUTHORIZED_REVISION`；`OutputValue.pin` 为真的边写 `PINNED`。
3. `ResolutionPolicy.pinned_revisions` 的生产方：从该消费者最早一次冻结的输入记录读；没有冻结记录时钉住目标取当时的授权版本。
4. `_select_by_revision`（`input_bindings.py:590-628`）：跟随边授权读不到而候选非空 → `REVISION_NOT_AVAILABLE`（明细"授权版本读不到"），不再落到"候选任选"。
5. `taskgraph_sharing.py:117-121` 改为与编译器对这条声明会产出的策略比；`taskgraph_inputs.py:53-56` 按 D0 第 4 条处理。
6. 2.2：`goal_port_outputs` 出别名前比收尾步骤端口与子目标端口的 `schema_ref`，不一致不出别名，在该子目标的输入结果里记一条"数据未绑定"原因。
7. 规划器 HDDL 导出把"跟随"列为不支持特性（`SDK/planning/htn/validation.py:834`），只影响求解器通道，产品不装（`SDK/runtime/assembly.py:99`），不动，实施记录写明。

### D5　2.3 写入目标与写入冲突
1. 编译器建子步骤绑定时，对做法 `criterion_links` 里链接到该叶子的每条 `file:X` 要求，加 `ResourceRef(kind="workspace_file", id=X)` 到 `resource_writes`；冲突检查已有、已跳过有先后的两步，冲突在编译期作为结构问题退回规划器（`STRUCTURE_INVALID`，不新增码）。
2. 运行期：`planning_repair_requests.collect_triggers` 加一项扫描——两步之间没有先后（顺序边或数据边的传递闭包），各自现行验收产出撞同一路径 → 一条 `WriteConflict` 修复请求（明细：路径、两步、两份产出编号），同一冲突只记一次；`overlay_attempt_inputs` 对这种撞路径不再"先到先占"，消费者输入结果报"写入冲突、等规划器"。接力型步骤交付上游同名文件的新版本（有先后）不受影响。
3. 新的修复触发类型登记进 v3 覆盖清单。

### D6　2.4 共享
1. `Host/hierarchical.py:86-93` 与 `product_world.py` 的 `user_goal_world`：内容步骤类型 `effect_identity="desktop.attempt-workspace"`，效果类型保持 `LOCAL_WRITE`，并把它写进类型体（`body`）参与类型引用哈希。**禁止改成"无副作用"或"外部只读"**（补全方案 2.4 第 2 条）。
2. 确认 `sharing_candidates` 在桌面任务里出现可共用的生产者；共享校验的版本策略按 D4 第 5 条。

### D7　桌面谓词、观察器、纪元写方、每轮重读
1. 新文件 `observers/workspace.py`（1.7）；`world.py` `build_planning_world` 加 `predicates` 参数；`Host/hierarchical.py:21`、`product_world.py:43` 两处注册。
2. `htn_store.py` `insert_observation`：1.8 的同事务加纪元。
3. `hierarchical_dispatch.py` `run_evidence_round`：加"重读已记录命题"一半（1.8），结果并入同一个返回值。
4. 实施记录写明：纪元从此在产品里会动；问人题目不受影响（D1 第 6 条）；`observations` 写入不再让证书过期（D3）。

### D8　规划器提示词 v20、清单、文档、发版
1. `role_templates.py:113` 升 `planner-hierarchical-v20`，一次写全：①禁止字段表删 `manager_epoch`、`budget_grant_revision`（`:139-141`，连同 D1 第 9 条两张表）；②前提怎么写（条件原子格式与一例；三个桌面谓词各是什么；前提在做法每一步开工前都要成立，不要写会被本做法自己的步骤改掉的前提；未知时预览回 `EVIDENCE_REQUIRED`，用 `REQUEST_EVIDENCE` 取证；文件或资料变了系统会重读，依据随之过期）；③`"pin": true` 的含义（这个输入固定用第一次拿到的那一版；不写则每次新尝试跟随上游当前通过验收的那一版）；④内容步骤可以用 `BIND_EXISTING_GOAL` 共用，一个分支换做法不影响共用的生产者；⑤两个可以同时做的步骤不要负责同一个 `file:` 要求，运行中撞了路径会收到写入冲突修复请求；⑥`obligations` 里的数字是这件事（含下级、含换过的做法）的真实累计。
2. 钉哈希的模板测试按当前版本重生成；`TOOL_SCHEMAS` 不动、执行池身份基线不变；执行者、审阅员模板不动。
3. 实施记录、`ARCHITECTURE/`、需求与场景状态（计划第六节）同次更新；本阶段末发版一次、Host 钉版；**旧编排库因迁移 39 与提示词升版不可用，真机前新建编排数据目录**；真机按第 3.5 版流程放到全部代码写完后。

---

## 四、功能性用例清单（12 条）

**结论：12 条，除 2、7 两条是函数级外都跑在 `SDK/testing/product_world.py` 的世界上，用 `SDK/testing/scripted_replies.py` 的分层脚本化通道。**

| # | 用例名 | 文件（`T/product_world/` 下） | 脚本要点 | 断言 |
|---|---|---|---|---|
| 1 | `test_stale_reply_after_epoch_moved_is_not_charged` | `test_epoch_and_read_set.py` | 规划器第一次被问时（脚本回调里）调 `bump_epoch`，第二次正常回复 | 一条 `REQUEST_BINDING_STALE` 拒绝；`_planning_attempts` 为 0；提交的读集含 `scope_epochs=[mission:1]` 与被细化目标的义务项，没有 `manager_epoch`/`budget_grant_revision`/`support_sets` 键；任务完成 |
| 2 | `test_commit_refused_when_epoch_or_duty_moves_after_preview` | 同上（函数级） | 产品世界里拿到预览编译结果；子情形①预览后 `bump_epoch`；②预览后把被细化目标的义务改为已满足 | 提交 `READ_SET_STALE`，明细分别是 `validity_epoch`、`obligation`；库里没有新计划版本 |
| 3 | `test_pending_question_survives_epoch_change` | 同上 | 规划器 `REQUEST_HUMAN`；`bump_epoch` 后跑一轮 | 题目仍是 `PENDING`；回答后下一轮规划照常 |
| 4 | `test_obligation_account_counts_failures_and_spend` | `test_obligation_accounts.py` | 单步任务，审阅员第一次 `REWORK`、第二次通过 | 下一次规划包里该叶子义务 `failures=[{count:1}]`、`attempts=2`、`spent_tokens` 等于两次结算之和；根义务合计不小于它；义务表没有三列；结算没有让在途规划过期 |
| 5 | `test_observation_after_acceptance_does_not_stale_closeout` | `test_observation_closeout.py` | D0 第 1 条：两步任务，第 1 步验收后写一条观察 | 任务完成；收尾评估不含 `EVIDENCE_STALE`；库里没有两张支持集合表 |
| 6 | `test_retry_follows_authorized_upstream_revision` | `test_input_revisions.py` | 两步接力；照 `test_repair_material.py` 的修复脚本让上游重做出第二个验收；下游新尝试；变体：下游的 `output` 写 `"pin": true` | 下游新尝试冻结的输入是上游第二个验收的版本；钉住变体仍是第一个版本；授权读不到的子情形报 `REVISION_NOT_AVAILABLE` |
| 7 | `test_goal_port_schema_mismatch_is_unbound` | 同上（函数级） | 造两种格式：子目标端口与收尾步骤端口格式不同 | 不出别名，消费者输入结果为"数据未绑定" |
| 8 | `test_parallel_steps_declaring_same_file_refused` | `test_write_targets.py` | 规划器第一份做法让两个无先后的步骤都链接 `file:report.md`；第二份加先后 | 第一份被退回且明细点名两步；第二份提交、任务完成 |
| 9 | `test_runtime_same_path_becomes_write_conflict` | 同上 | 两个无先后的步骤（不链接 `file:`）都写 `notes.md` 并通过；第三步接两者 | 第三步不开工；一条 `WriteConflict` 修复请求点名路径与两步；规划器被问到 |
| 10 | `test_shared_producer_survives_branch_method_change` | `test_sharing.py` | 两个分支经 `BIND_EXISTING_GOAL`（SHARE_ACTIVE）共用一个会写文件的生产者；其中一个分支换做法 | 共用生产者不被取消、不被判"只读步骤还在写"、验收通过；另一分支拿到它的产出并完成 |
| 11 | `test_precondition_needs_evidence_then_commits` | `test_desktop_preconditions.py` | 做法 `applicable_when` 写 `desktop.file-present(path=种子里的 input.csv)`；第一次预览 → 规划器 `REQUEST_EVIDENCE` → 再提交；另参数化函数级检查三个谓词的 TRUE/FALSE/不可用（含同一路径两份现行产出） | 第一次 `EVIDENCE_REQUIRED`；取证后有一条 TRUE 观察、纪元仍为 0（首次观察不加）；第二次提交读集带该前提见证；步骤开工并完成 |
| 12 | `test_file_change_flips_observation_and_moves_epoch` | 同上 | 规划器先取证得到 `file-present(out.md)=FALSE`；第 1 步写出 `out.md` 并通过 | 下一轮重读写出 TRUE 观察；纪元 0→1、`bumped_by` 为 `observation:<编号>`；开工见证按新纪元重发；任务完成、收尾无 `EVIDENCE_STALE` |

**改坏检验（每个功能一条；改前 `cp` 备份，改后清 `__pycache__`，不用 git 恢复）：**

| 功能 | 改坏哪里 | 应失败的用例 |
|---|---|---|
| 读集补纪元与义务 | `build_read_set` 不写 `scope_epochs` | 2① |
| 过期不算答错 | 去掉 `_planning_attempts` 对新列的读取 | 1 |
| 问人题目不绑纪元 | 绑定里加回纪元 | 3 |
| 义务汇总账 | `ObligationAccountView` 改回读零 | 4 |
| 收尾不比查询集 | `changed_items` 加回比观察查询集 | 5 |
| 跟随授权版本 | `accepted_outputs` 不填 `authorized_revisions` | 6 |
| 子目标端口格式 | 去掉别名前的格式比对 | 7 |
| 声明写入冲突 | 不从 `file:` 链接生成 `resource_writes` | 8 |
| 运行期写入冲突 | 恢复"先到先占" | 9 |
| 共享 | 去掉桌面类型的 `effect_identity` | 10 |
| 观察器与前提 | 两处注册传回 `observers=()` | 11 |
| 纪元写方 | `insert_observation` 不加纪元 | 12 |

只跑这 12 条与被改文件直接对应的旧用例（如 `T/full_target/test_plan_commits.py`、`test_htn_store.py`、`assurance_exec/test_v_assurance.py`、`T/product_world/test_knowledge_confirm.py`、`test_operation.py`、`test_sub_goal.py`）；被删机制的旧用例随之删，不跑整目录、不跑全量。无关用例红了记下另行处理。

---

## 五、偏差单（7 条）

**结论：7 处与计划原文或前次裁决的字面不同，都已给出改法；第 1 条按计划要求报用户，第 3、6 条改变用户或审计看得到的东西，建议报用户知悉。**

| # | 原文 | 改成 | 为什么 |
|---|---|---|---|
| 1 | 待定③"整数闸门真正写上，还是由计划修订号取代"；ADR-13 第 1、4 条 | 删整数闸门与 `base_graph_version`；计划修订号是唯一结构闸门，读集是第二层 | 无写方、恒等；平面模式已删；计划修订号已串行化全部计划提交（1.1） |
| 2 | 表二 10"读集补'不存在''集合''支持修订号''有效性纪元''预算与授权修订号'"；ADR-13 第 2 条与 C29 | 补"作用域纪元"与"义务"；"不存在"不加；"集合/支持修订号"连同两表删；"预算与授权"不进读集，删 `budget_grant_revision` | 不存在类只有计划提交能改、已被计划修订号覆盖；支持集合无写方；预算授权在提交事务里现读（1.2） |
| 3 | 表二 8 / 阶段 C 留给 D"支持集合表接上或删" | 删两表，并删验收评估器的存储规则与部署观察输入、收尾复查不再比查询集（偏离 AER 存储规则与 I02 场景） | 从没接上、对验收结论无影响；不删则 D 写观察会让全部验收证书"依据已变"（1.5） |
| 4 | 表二 19"只加汇总账目" | 不另存，读时由一个函数推出；删义务表三列与两个零调用写函数 | 同一事实只记一处；不每次结算写通道源表；规划包现在读的是假零（1.6） |
| 5 | 前次裁决 1.2 第 3 条"真值和上一次快照不同时加纪元" | 细化：已知真值变了才加，未知→已知不加；另加"每轮重读已记录命题、变了才写" | 未知时无可用见证；不重读则前提见证永远按旧 TRUE 放行（1.8） |
| 6 | （计划未写）问人题目的绑定含管理纪元 | 只绑计划修订号与要求修订号 | 纪元管依据凭证，不管该不该问人；否则观察一变就收回用户的卡片（1.3） |
| 7 | 计划未写"过期拒绝不扣规划次数"的落点 | 错误码表加"计入答错"列，请求过期类不计 | 落实前次裁决 1.3；只一处读（1.9） |

**主计划下一版（第 3.11 版）要改的文字（可直接粘贴）：**
- 标题改"第 3.11 版"。
- 表二第 8 条做法要点末尾"（第 3.9 版：本阶段不接，归 D）"改为"（第 3.11 版：D 删两表，连同验收评估器的存储规则与部署观察输入）"。
- 表二第 10 条做法要点改为："读集补'作用域纪元'与被细化目标、共享目标的'义务'；'不存在'由计划修订号闸门覆盖，不加；'集合/支持修订号'随支持集合表删；预算与授权在提交事务里现读，不进读集；整数闸门删，由计划修订号取代（待定③已定，改 ADR-13 第 1、4 条）"。
- 表二第 19 条做法要点改为："只记账、不改上限：不另存，读时按义务（含下级）由尝试与结算推出；读方为规划包义务视图、诊断导出、对外快照；删义务表三个恒零列"。
- 表二第 1a 条做法要点改为："SDK 一份工作区观察器（文件在不在、文件内容哈希、资料是否当前版本，三谓词均 CLOSED），Host 与产品同形世界注册；纪元写方在观察写入同事务（已知真值变了才加）；每轮重读已记录命题；取证只走规划器 REQUEST_EVIDENCE；规划器提示词说明可写前提、可取证"。
- 阶段 D 第 2 条改为："计划提交核对范围（表二 10，见上）；同批删 `manager_epoch`、`budget_grant_revision`、`base_graph_version`；问人题目不绑纪元；纪元引起的过期拒绝不算规划器答错（错误码表加一列）"。
- 阶段 D 第 3 条改为："按'要完成的那件事'汇总失败与花费（表二 19，读时推出，不改上限）"。
- 阶段 D 加第 4′ 条："删 `justification_sets`/`support_members` 与验收评估器的存储规则、部署观察输入，收尾复查只比钉住对象（迁移 39，与义务表删列同一迁移；证书完整读集少两张表，开发库旧证书失效）"。
- 第五节③"谁定 / 什么时候"栏改为："**已定**（阶段 D 开工裁决 2026-10-03）：删整数闸门，由计划修订号取代；结论报用户"；⑦栏补"阶段 D：支持集合表删"。
- 修订记录加："**第 3.11 版（2026-10-03）**：阶段 D 开工裁决（`HTN补齐-阶段D-开工裁决与施工清单.md`）7 张偏差单改入：整数闸门删；读集补纪元与义务、删三项；支持集合两表与验收评估器存储规则路径删；义务账读时推出；纪元写方细化与每轮重读；问人题目不绑纪元；过期拒绝不算答错。"

---

## 六、不在本阶段做的

| 事项 | 去处 |
|---|---|
| 作用域纪元第二个写方（要求修订提交）；"要求修订让旧验收失效 → 知识过时"的测试；界面"预算去向"（读 D2 的同一函数）与"还没细化的目标" | E |
| 桌面类型与任务无关的前置改造（做法跨任务复用要用到 D6 改过的类型身份） | C3+摘要 |
| 12 个改坏检验绑定用例、24 行来源表里 D 碰到的来源行（P 系列的授权版本、观察、写入目标）、崩溃切点加"观察写入 + 加纪元同事务""写入冲突扫描"、D 的真机一局（规划器写了前提时被求值；真机只断言"提供了、用了就正确"） | F（真机按第 3.5 版流程放到全部代码写完后） |
| 证书完整读快照在收尾复查不再比查询集之后是否还需要整表读；`_read_set.py:478-527` 三个无人产出的"不存在"谓词与就绪判断里的 `conflicting_unsettled_operation` 的去留；自动取证轮"未知前提"那半在只剩桌面后的去留 | G（与屏障、重放覆盖一起清点） |
| 求解器通道（HDDL 导出）对"跟随"的支持 | 不做（产品不装求解器） |
| 多模型、两人审批、NanoJev、42 组×3 局、金额、跨领域、组级管理员、学习 | 用户已定暂不做 |
