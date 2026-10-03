# HTN 补齐 · 阶段 C（知识、黑板、有效性）：开工裁决与施工清单

- 裁决人：独立裁决子代理（只读核代码，除本文件外没有改任何文件、没有跑测试）。日期 2026-10-03，代码以主分支 `21eced64` 为准。
- 依据：`HTN补齐计划-2026-10-02.md`（第 3.5 版）阶段 C 全节、第五节①②⑦、第三节表二第 4、7、8、9 条、第六节流程；`审阅通过结论入知识库-方案.md`（下称"知识进库方案"）；`TaskGraph-补全-方案.md` 第 6.5 节；`审阅员信息缺口-检查记录.md`；`审阅升级-方案.md`；`HTN补齐-开工前裁决-2026-10-02.md`（纪元口径，下称"前次裁决"）。
- 口径：判断交给 LLM，Harness 只管约束与秩序；同一件事只留一条路径；开发期旧路径直接删、不做兼容；做好的功能默认开启。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`。

---

## 〇、一句话结论

阶段 C 能按计划做，**共 9 步、不需要新迁移、不动执行图编码清单、不改任何工具说明（执行池身份不变）**；提示词升一次版（执行者三份模板、审阅员一份、上下文组装一份）。有 6 处计划文字要先改（见第五节），其中 2 处建议报用户知悉。

---

## 一、开工前裁决（待定⑦的阶段 C 部分）

### 1.1 `memory/blackboard.py`：删

**结论：删掉这个类，黑板的三层由知识读工具那一个模块直接提供。**

- 事实：`SDK/memory/blackboard.py:22-58` 是一个只读外壳，生产代码零调用；唯一用到它的是旧测试 `T/step04/test_claims_knowledge_main_loop.py:43,185,348`。它只按状态过滤，**不知道"已过时"、不知道"谁在读"（执行者还是审阅员）、还把规则截断的摘要层开着**（第 56-58 行），三样都与本阶段要求相反。
- 现有的读入口是 `SDK/context/knowledge_tools.py:13-75`（分页、哈希续读、已取代过滤都在，挂在工具网关 `SDK/runtime/tool_gateway.py:823-828` 上）。补成三层要改的就是它；再在下面垫一个 `Blackboard` 数据层，等于"工具 → 外壳 → 存储"两层做同一件过滤，是第二条路。
- 做法：删 `memory/blackboard.py`，改 `memory/__init__.py` 文档字符串；`test_claims_knowledge_main_loop.py` 里用到它的两处随之删（第 348 行那条测试的保护由第四节用例 6 接替）。三层的取数函数写在 `context/knowledge_tools.py` 里，数据直接读 `Store`。

### 1.2 知识读工具怎么接到分层执行者：工具说明一字不改，只把两个工具加进执行者模板

**结论：不新增工具、不加参数；三层体现在返回结果里，由执行者提示词讲清各层能不能当事实。这样执行池身份不变，旧数据目录也起得来。**

- 事实一：执行者拿不到知识工具，原因只是角色模板没列。最终可用工具 = 任务 ∩ 步骤 ∩ 角色 ∩ 部署（`SDK/governance/policies.py:205-225`，调用处 `SDK/orchestrator/event_handler.py:9684-9690`）；Host 部署和任务都已带 `knowledge_list`/`knowledge_read`（`Host/service.py:54,282`），但 `WORKER_HIERARCHICAL.tool_names`（`SDK/runtime/role_templates.py:431,482`）没有，交集里被丢掉。
- 事实二："执行池身份"里的 `tool_schema_hash` 是**全部** `TOOL_SCHEMAS` 的哈希（`SDK/runtime/model_router.py:107-117` → `tool_gateway.py:201-210`），启动时比对不上，整个编排服务起不来（`SDK/runtime/assembly.py:381-393`）。所以：改工具说明或加参数 = 所有已有数据目录都要换新执行池；而只在角色模板里多列两个已存在的工具，不进这个哈希。
- 事实三：C3+摘要还要再开摘要层。如果这次改工具说明，C3 很可能还要再改一次。把"层"放在结果里（每条带 `layer` 与 `basis`），C3 加摘要层同样不用动说明——计划要求的"只改一次"变成"零次"。
- 做法：`WORKER_HIERARCHICAL.tool_names` 加 `knowledge_list`、`knowledge_read`，升 `worker-hierarchical-v6`；无人机模拟执行者共用正文，同升 `v2`；AppWorld 执行者模板已有这两个工具，只因 `used_knowledge` 写法变了随升 `v2`（见 1.6、F）。开发期旧任务钉旧版本起不来，按规则不兼容。
- **开工第 0 步要先核对**：原生执行池（产品路径）是否把这两个工具路由到编排网关。`SDK/deployment/native_pools.py:67-86` 的授权只放行部署工具和审阅证据工具，产品同形世界 `SDK/testing/product_world.py` 默认工具里也没有知识工具，全库没有一条测试走过"原生池执行者调知识工具"。用一条金丝雀用例确认，不通先修路由再往下。

### 1.3 审阅员的读工具：本期不给黑板工具；相关知识与争议写进审查包

**结论：保证通道审阅员现在已经有两件只读证据工具，本期不再给它知识读工具；争议和本步用过的知识按"编号 + 版本 + 内容哈希 + 原文"写进审查包。审查包有哈希、钉在正式审阅记录里，"读过哪些条目"天然记进了审阅记录。**

- 事实：审阅员有工具能力——`assurance_find_evidence` / `assurance_read_evidence`（`SDK/orchestrator/assurance_review_runtime.py:231-245` 的 `tool_names=ASSURANCE_EVIDENCE_TOOLS`；绑定在 `event_handler.py:5077-5087`），每次整段读取都写披露回执、记进正式记录（`assurance_review_collect.py:138-145`、`SDK/assurance/reviews.py:51-109` 的 `exposed_evidence_refs`/`disclosure_refs`）。
- 不给知识工具的理由：
  1. **独立性**：知识库里的"已验证"本身就来自同一模型的审阅确认。让审阅员拿知识去确认新结论，就是"知识证明知识"的循环；确认只应以本步审查包里的证据（产物、检查回执）为依据（知识进库方案第 3 条、补全方案 6.5 第 4 条）。知识进审查包但**不进证据目录**，审阅员没法把它写进 `evidence_ids`，循环从格式上就断了。
  2. **记录只留一条路**：审阅员的读取记录现在只有一条路（披露回执）。另给一个知识工具，就要为它再造一套"读过什么"的记录。
  3. 不动工具说明，执行池身份不变（同 1.2）。
- 可见范围：审查包新增两节（见施工 C3）——"本步待确认结论"（本步被审结果里的每条结论：编号、原文、它引的证据路径）；"相关条目"（与本步结论同主题键、立场相反的他步结论或知识，即现有矛盾规则 `SDK/verification/conflicts.py:43-59` 认定的那些；以及本步结果 `used_knowledge` 里声明用过的知识，带版本与哈希）。**一律排除被审步骤（含它所有尝试）自己的其他结论**。选哪些条目只按"同一主题键""声明用过"这两条结构事实，不做字面相关度挑选。
- 已知的另一处可见：审阅员现在通过审查对象（结果信封，`SDK/storage/assurance_reads.py:199-200`）能看到本步的总结和结论原文。见偏差单 5。

### 1.4 "已过时"由谁在什么事务里写：不写库，读取时由同一个判定函数得出

**（2026-10-03 偏差裁决：下文"支持集合"均指知识记录自带的 `support` 字段。）结论：不重建"写已过时"的生产方。知识是否当前，在每次被读（读工具、上下文推送、`used_knowledge` 核对、审查包取条目）时由一个函数 `knowledge_standing()` 当场判定；判定依据只读这条知识的支持集合（见 1.5）。**

- 事实：opt.134 删掉了 `KnowledgeIndex.stale` 与资料依赖；现在知识状态只有 `VERIFIED`/`SUPERSEDED`（`SDK/memory/verified_knowledge.py:28`），读工具只过滤已取代（`SDK/context/knowledge_tools.py:19-23`），`used_knowledge` 核对也只认已取代（同文件 `verified_knowledge.py:131-158`）。`rank_knowledge` 和 `build_summaries` 还留着没人传的 `stale` 参数（`SDK/context/retrieval.py:164,179-182`、`SDK/memory/summaries.py:24`）。
- 关键事实：**验收本身的"是否当前"在库里也不写**——验收行插入后不再改（`SDK/storage/htn_store.py:1129-1169` 只有插入），"当前"一直是读时推出的：义务没被取消或取代、所在步骤的完成范围仍然完整（`SDK/orchestrator/completion_support.py:28-90`）。修复、换做法、要求修订让旧验收"失效"，走的是好几条不同的写路径（计划提交改义务状态、取消任务、新结果被接受……）。如果知识要"写已过时"，就得在每一条这样的路径上都挂一个写方，漏一条就是静默错误。
- 判定规则（Harness 秩序，不含语义）：知识是当前的，当且仅当①状态 `VERIFIED` 且没被取代；②支持集合里的验收仍当前（复用完成范围那一套推法，抽一个 `acceptance_is_current()`，`completion_support.py` 现有两处也改调它，只留一份）；③支持集合里每个证据产物仍是该路径的现行版本；④支持集合里引用的上游知识仍当前（有向、只回溯到更早的知识，不会成环）。
- 前次裁决已定：知识过时不加作用域纪元（`HTN补齐-开工前裁决-2026-10-02.md` 1.2 第 3 条）。本裁决与之一致。
- 与计划原文的差别记偏差单 3。

### 1.5 知识的依据记在知识记录里；没人用的支持集合表本阶段不接，去留归 D

**结论：`KnowledgeRecord` 加 `support` 字段（来源验收编号；证据产物的编号、版本、内容哈希；用过的上游知识的编号、版本、内容哈希），与知识行同一次写入，建成后不改；`knowledge_standing()` 只读它。`KnowledgeRecord.dependencies` 删掉（与 `support.knowledge` 是同一件事，只留一处）。`justification_sets` / `support_members` 不写。**（2026-10-03 偏差裁决改，见 `HTN补齐-阶段C-偏差裁决-知识支持集合.md`）

- 事实：这两张表是保证通道的源表（`SDK/storage/assurance_source_inventory.py:13-14`、`assurance_barrier_v26.sql`），每张使用证书都完整读它们、不分主体类型（`SDK/storage/assurance_reads.py:279-291`），并作为验收支持图的输入（`SDK/orchestrator/assurance_validity.py:501-533,830-860`），收尾复查就比这三类查询集（`SDK/orchestrator/assurance_recheck.py:25`）。往里写知识支持集合会让本任务全部现行验收证书和根结论证书"依据已变"，收尾报 `EVIDENCE_STALE`（已用产品同形用例跑出）。`knowledge` 表不在通道清单里，写知识行不碰证书。
- 代价：修复影响分析（`SDK/orchestrator/repair_impact.py:53`）的影响清单里不再列知识编号；没有代码按它做事，知识过时本来就读时判定。重放清单里这两张表"生产无写方"的缺口保持原样。
- 两张表与计划读集的 `SupportSetRead`（`SDK/contracts/htn.py:2250`）一起，接上或删归 D。

### 1.6 `used_knowledge` 记编号加版本：字符串写成 `编号@版本`，不改合同

**结论：`ResultEnvelope.used_knowledge` 仍是字符串数组（`SDK/contracts/models.py:768`，在编码清单里，不动），约定写成 `<知识编号>@<版本>`；核对、计数、审查包都按这个解析。**

- 版本取 `KnowledgeRecord.version`。知识内容建成后不改，所以审查包里同时写内容哈希，审阅员看到的就是执行者引用的那一份。
- 解析处：`KnowledgeIndex.check`（`verified_knowledge.py:131-158`）、`_grade_and_project` 的证据解析与使用计数（`SDK/orchestrator/commit_service.py:1014-1023,1144-1161`）、验收前核对（`commit_service.py:2610-2621`）。只写编号不写版本按"格式不对"拒收，**不做兼容回落**。

### 1.7 D 项：三种审查"没有结论"时现在走到哪、要改哪

**结论：把"审阅员第二次回复仍无法采用"由导入器记成一份正式的"判不下来"记录（如实标明"没有可采用的回复"），三种审查之后就全部落到已有的"判不下来 → 问人"出口；操作结果审查补上它缺的"判不下来 → 问人"。裁决题过期按"没有结论"交规划器。**

| 审查 | 现在"没有结论"走到哪 | 现在"判不下来"走到哪 | 要改的 |
|---|---|---|---|
| 最终审查 | 第 2 次回复仍解不出 → 写"格式用完"回执（`SDK/orchestrator/assurance_review_consumer.py:486-557`）或"导入被拒"回执（同文件 :310-345）→ 没有正式记录 → 根审查状态停在原地（`SDK/orchestrator/root_review.py:766-768`）→ 卡死确认 → 问规划器一次（`event_handler.py:2672-2683`，明细来自 `:8964-9006`）→ 仍不动则"没有可派发的工作"停 | 复审一次后仍判不下来 → `AWAITING_PERSON`（`root_review.py:775-790`）→ 问人（`event_handler.py:8789-8862`） | 导入器改动后自动落到右栏；停止明细改写（C2） |
| 组合审查 | 同上，`resolve_ready` 拿不到记录直接返回（`SDK/orchestrator/composition_review.py:228-230`）→ 卡死 → 问规划器 → 停 | `assured_composition_action` 返回 ask → 问人（`composition_review.py:83-95`；`event_handler.py:8754-8763`） | 同上 |
| 操作结果审查 | 同上；`_has_pending_operation_completion` 见到用完即放手（`event_handler.py:2288-2297`）→ 卡死 → 问规划器 → 停 | **没有这条路**：只认 `ACCEPT`（`SDK/orchestrator/operation_outcomes.py:578,603`；`SDK/orchestrator/operation_runtime.py:251-261`），判不下来的正式记录也一样卡死 | 补"判不下来 → 问人"；人裁决通过后按已有使用证书路径验收（证书的裁决分支是通用的，`SDK/orchestrator/assurance_validity.py:311-348,481-498`） |

- 为什么用"记成判不下来记录"而不是"另起一个按回执裁决的出口"：所有消费方（根审查、组合审查、做法审查、叶子复核、使用证书、完成度读取）都只认"正式记录 + 人的裁决回执"（`SDK/orchestrator/review_adjudication.py:19-38`）。另起"无记录也能被人裁决"的出口，要在证书（保证通道最敏感的代码）里加第二种放行依据。判不下来的记录本身不放行任何东西，只有人裁决通过才放行，安全边界不变。
- 推翻的旧注释：`assurance_review_consumer.py:294-295` 写"不伪造正式记录"。新做法不是伪造：记录来自真实的第 2 次审阅调用（回合已导入、原文已存进内容库、`assurance_review_collect.py:41-60`），只是解码规则规定"最后一次回复无法采用 = 判不下来"，记录里写明 `interpretation: NO_USABLE_REPLY` 和错误码。
- 只覆盖"回复回来了但不能用"（格式错、引用没披露的证据等可修的解读错误）。调用没回来（服务端错误、被重启打断、超时放弃）是基础设施问题，保持现有路径（根审查重切、结果审查重审），见第六节。
- 连带：叶子内容审查、做法审查的"没有结论"也会一起走到"判不下来"出口（见偏差单 2）。
- 复审次数：第 2 次调用（格式修复）本来就是新会话，不再加第 3 次（见偏差单 1）。

---

## 二、现状核对表

**结论：方案里说"已有"的东西，过时通道已经没了，其余基本在；最大的缺口是"审阅员确认 → 已验证"这条路完全没有，以及操作结果审查没有"问人"出口。**

| 项 | 现在已有（文件:行） | 缺什么 |
|---|---|---|
| A 回复格式口径 | 解码在两处：分类时 `SDK/orchestrator/assurance_review_collect.py:161`，正式导入时 `SDK/orchestrator/assurance_review_import.py:279`，都是 `ReviewReply.from_json(decode(raw))`；回复形状 `SDK/assurance/checks.py:255-299`（`schema_version` 必须为 2）；严格字段检查 `SDK/assurance/codec.py:54-59`；格式修复提示 `SDK/orchestrator/assurance_review_transport.py:731-762`；原文已整份存档（`assurance_review_collect.py:41-60`，缺口记录第 7 条已不成立） | 剥代码围栏、忽略值为空的多余字段；`claims` 字段；两处解码合成一个函数 |
| B 知识进库 | 定级 `SDK/memory/claims.py:161-195`（最多"有支持"）；入库只有系统测试观察 `SDK/memory/code_observations.py:19-123`；入库事务 `SDK/orchestrator/commit_service.py:980-1162`（由 `_accept_result` 在 :2757 调用，同一事务里先锁使用证书 :2683-2708）；矛盾规则 `SDK/verification/conflicts.py:43-59`；争议处理 `commit_service.py` `_dispute` | 审查包"本步待确认结论"；回复 `claims`；确认升级；依据类型；证据引用约束；支持集合写入；**过时判定（opt.134 已删，确认不存在）** |
| C 黑板取用 | 读工具 `SDK/context/knowledge_tools.py:13-75`（只列已验证、过滤已取代、分页、哈希续读）；网关接线 `SDK/orchestrator/event_handler.py:776-780`；被动推送 12 条 `event_handler.py:5105-5150`（`max_knowledge_items=12`，`SDK/runtime/assembly.py:113`）；上下文里的知识段 `SDK/context/context_builder.py:110-134`；`blackboard.py` 无人调用 | 执行者模板没列工具；候选与争议层、原始记录引用层；过时过滤；`编号@版本`；审查包里的争议与相关条目；审阅员信息缺口 1、2、3、8 仍在（`SDK/verification/reviewer_evidence_tools.py:180-213,276-320`；`SDK/orchestrator/leaf_acceptance.py:587`） |
| D 没有结论 | 见 1.7 表；"判不下来"复审 `assurance_review_consumer.py:270-281,389-439`；人裁决回执 `SDK/orchestrator/human_commits.py:327-364`；做法审查的"裁决题过期 → 没有结论交规划器"已有范例 `SDK/orchestrator/method_plan_reviews.py:194-245` | 第 2 次仍无法采用 → 判不下来记录；操作结果审查问人；根与组合审查裁决题过期的上报 |
| E 有效性 | 见证合同与新鲜度 `SDK/contracts/evidence_state.py:566-664`（`is_fresh_for` :652）；就绪判断复用 `SDK/graph/eligibility.py:726-800`；输入见证每轮重发 `SDK/orchestrator/hierarchical_dispatch.py:1150-1210`；**装上下文前的输入一侧已核对** `SDK/artifacts/input_bindings.py:739-757`；纪元唯一写方 `SDK/storage/htn_store.py:1552`（产品里恒为 0，前次裁决 1.2 第 4 条） | **对外操作交接前不核对见证与验收**（`SDK/orchestrator/action_commits.py:1055-1104` 只查任务状态、版本、参数、审批、上限）；上下文的知识一侧不过滤过时（随 B） |
| F 提示词 | 执行者 `worker-hierarchical-v5`（`SDK/runtime/role_templates.py:430-483`，第 448-450、462-463 行是要改的知识与结论两句）；无人机 `:582-592`；AppWorld `SDK/runtime/appworld_templates.py:10-24`；审阅员 `SDK/assurance/review_input.py:14-50`（第 17-19 行明令不许代码围栏、不许空的多余字段）；编解码版本 `SDK/assurance/reviews.py:38`；上下文组装 `context-builder-v4`（`context_builder.py:43`） | 一次升版：执行者三份、审阅员一份、上下文组装一份；规划器不动 |

---

## 三、施工清单

**结论：分 9 步（C0～C8），前 8 步只写代码和功能性用例，最后一步统一升提示词版本、对账、发版。全程不新增迁移、不重写编码清单、不改 `TOOL_SCHEMAS`。**

总表：

| 步 | 内容 | 依赖 | 迁移 | 编码清单 | 提示词版本 |
|---|---|---|---|---|---|
| C0 | 金丝雀核对 | — | 无 | 不动 | 不动 |
| C1 | 回复解码口径（A） | — | 无 | 不动 | 编解码版本随 C3 一起升 |
| C2 | 没有结论 → 判不下来记录；结果审查问人（D） | C1 | 无 | 不动 | 不动 |
| C3 | 审查包两节 + 回复 `claims`（B 前半、C 审阅员范围） | C1 | 无 | 不动 | 编解码 v3 |
| C4 | 确认入库、支持集合、过时判定、`编号@版本`（B 后半） | C3 | 无 | 不动 | 不动 |
| C5 | 黑板三层读工具、删 `blackboard.py`、上下文过滤（C） | C4 | 无 | 不动 | 执行者 v6（随 C8） |
| C6 | 审阅员信息缺口（C） | — | 无 | 不动 | 不动 |
| C7 | 交接前核对凭证与纪元（E） | C4 的 `acceptance_is_current` | 无 | 不动 | 不动 |
| C8 | 提示词统一升版、清单与文档（F） | C1～C7 | 无 | 不动 | 全部 |

### C0　金丝雀核对（半天）
1. 产品同形世界里放开知识工具（`product_world(..., allowed_tools=DEFAULT_TOOLS + ("knowledge_list","knowledge_read"))`），临时给执行者模板列上这两个工具，跑一条"执行者调 `knowledge_list`"的用例，确认原生池能把调用路由到 `tool_gateway.py:823-828`。不通就先修 `SDK/deployment/native_pools.py` 的工具路由，修法写进实施记录。
2. 核对审阅员信息缺口第 4 条（检查回执是否写明所属结果和任务）、第 5 条（必需检查是否"只有登记、没有可读回执"）。仍在的，并进 C6；不在的，在实施记录里注明"已核对、不存在"。
3. 核对 `prepare_official_review`（`assurance_review_import.py:608`）对"分类为格式错误"的回合能否走到导入（C2 要用）。

### C1　回复解码口径（A）
- `SDK/assurance/checks.py`：新增 `decode_review_reply(raw: str) -> ReviewReply`，唯一的解码入口：
  1. 去掉首尾空白后，若整段是一个代码围栏（开头一行 ```` ``` ```` 或 ```` ```json ````、结尾一行 ```` ``` ````），取围栏内的正文；围栏外还有任何文字仍按 `JSON_INVALID` 拒。
  2. `decode()` 后，逐层（顶层、`assessments[]`、`findings[]`、`claims[]`）删掉**不在形状里且值为空**（`null`、`""`、`[]`、`{}`）的键；不在形状里但有值的保留，交给原来的严格检查拒收。
  3. 交 `ReviewReply.from_json`。长度上限（`codec.py` 的 256KB 与各文本上限）不变。
- 两处调用改用它：`assurance_review_collect.py:161`、`assurance_review_import.py:279`。原文哈希仍按收到的原始字节记（不改 `raw_output_hash` 口径）。
- `assurance_review_transport.py:731-762` 的 `JSON_INVALID`、`OBJECT_FIELDS_UNKNOWN` 两条修复提示改写（说清"有值的多余字段仍不行"）。
- 不新增拒绝码；错误码表不动。

### C2　没有结论 → 判不下来记录；操作结果审查问人（D）
1. 导入器 `SDK/orchestrator/assurance_review_import.py`：
   - `_interpret`（:274-390）加参数 `no_usable_reply: str | None`。为真时不解码回复，生成记录：总结论 `INCONCLUSIVE`；每条准则 `UNKNOWN`、执行状态 `NOT_RUN`、`limitations=("REVIEW_NO_USABLE_REPLY:<错误码>",)`；清单（manifest）写 `model_verdict="INCONCLUSIVE"`、`interpretation="NO_USABLE_REPLY"`、`error_code`、`claims=[]`，其余字段照常（回合引用、原文哈希、披露）。这样证书的重判（`assurance_validity.py:627-672`）不用改。
   - `prepare_official_review`（:608）透传该参数，允许分类为 `FORMAT_INVALID` 的第 2 次回合走到导入。
2. 消费者 `SDK/orchestrator/assurance_review_consumer.py`：
   - `_prepare_format_repair`（:486-557）：第 2 次、分类为 `FORMAT_INVALID` 时，不再写"格式用完"回执，改为准备一份 `no_usable_reply=<错误码>` 的正式导入；`TURN_FAILED`（调用没回来）仍写"格式用完"，不变。
   - `prepare` 里的 `rejected()`（:310-345）：第 2 次、错误码属于 `REPAIRABLE_INTERPRETATION_ERRORS`（:560）时同样改为判不下来导入；`POLICY_CATALOGUE_MISMATCH`（审查包自己的问题）仍按拒收。
   - :294-295 的旧注释改写，说明理由。
3. 操作结果审查补"问人"：
   - `SDK/orchestrator/operation_outcomes.py:562-581,600-604`：`record.verdict is ACCEPT` 改为 `accepted_or_adjudicated(store, record)`（`review_adjudication.py:36`）。
   - `SDK/orchestrator/operation_runtime.py:251-261`：正式记录为判不下来且没有裁决 → 调编排循环新加的 `_ask_person_to_adjudicate_outcome`（`event_handler.py` 里照 :8754 的样子写，`decision_id="adjudicate-outcome:<记录编号>"`，`target_id` = 该审阅的审查对象编号，证书按它比对）；裁决"打回"按现有拒收处理（卡死 → 问规划器），不新增分支。
4. 裁决题过期的上报（三种审查同一写法，照 `method_plan_reviews.py:194-245`）：`event_handler.py:8811-8862` 的 `_ask_person_to_adjudicate` 遇到题目状态 `STALE` 时，返回"过期"，由根审查与组合审查的调用方各发一条修复请求（复用 `_request_root_review_repair` :8866、`_request_composition_repair` :8765），明细写 `{"outcome":"NO_VERDICT","reason":"裁决题在回答前过期","record_id":…}`；同一记录只发一次。结果审查过期并入卡死明细（下一条）。
5. 停止明细：`event_handler.py:8964-9006` 的 `_final_review_unreadable_detail`/`_exhausted_reviews` 改名为"审查没有结论"明细，内容改为：调用没回来而用完的审查；判不下来、裁决题待答或已过期的审查。`:2288-2297` 结果审查的"放手"条件随之改为"判不下来且裁决题已过期，或调用没回来且无重审"。
6. `method_plan_reviews.py:169-191` 的 `_no_verdict_reason` 保留，只剩"调用没回来"这一类会走到它（注释写明）。
7. 重放清单：本步不新增表、事件；`AssuranceReviewImported` 事件内容多了 `interpretation`，在 `business_replay_inventory.json` 的 `review_records` 条目备注里写明。

### C3　审查包两节 + 回复 `claims`（B 前半）
1. 合同 `SDK/contracts/resolution.py:802-822` 的 `ReviewPackage` 加两个可选字段（不在编码清单里）：
   - `claims_to_confirm`：每项 `{claim_id, content, content_sha256, evidence}`；
   - `related_entries`：每项 `{kind: "dispute"|"used_knowledge", id, version, content_sha256, content, status, source_task}`。
2. `SDK/orchestrator/leaf_acceptance.py:541-597` 的 `_package` 填这两节：结论取 `store.list_claims(result_id)`；相关条目按 1.3 的两条结构规则选，排除 `source_task == 被审步骤` 的一切；用过的知识按 `编号@版本` 取并核对当前（C4 的 `knowledge_standing`，C4 之前先只按状态）。其他用途的审查包两节为空。`_stored_package`（:599-627）已容忍同一主体的内容差异，不用改。
3. 回复形状 `SDK/assurance/checks.py:255-299` 升 `schema_version=3`，加可选 `claims`：每项 `{claim_id, confirmed: bool, evidence_ids: [...], reason}`（`reason` 1～1000 字）。规则：同一编号写两次 → 新的可修解读错误 `DUPLICATE_CLAIM`；编号不在待确认列表 → `CLAIM_SCOPE`（两码进 `REPAIRABLE_INTERPRETATION_ERRORS` 与 `_INTERPRETATION_FEEDBACK`，`assurance_review_transport.py:714-727`）；**漏写的编号算未确认，不拒收**。
4. `assurance_review_import.py` 的 `_interpret`：对 `claims` 每项用现有 `resolve_evidence_ids` 解析证据（未披露的标签照旧报 `UNEXPOSED_EVIDENCE`），清单里新增 `claims` 段：`{claim_id, content_sha256（取自审查包）, confirmed, evidence_refs, reason}`。
5. 编解码版本 `SDK/assurance/reviews.py:38` 升 `assurance-review-reply-v3`（与 C8 的审阅员指令一起进 `reviewer_policy_ref` 指纹）。
6. 叶子以外的用途：`claims` 必须为空或不写（待确认列表为空，任何一项都是 `CLAIM_SCOPE`）。

### C4　确认入库、支持集合、过时判定（B 后半）
> **2026-10-03 偏差裁决（`HTN补齐-阶段C-偏差裁决-知识支持集合.md`）**：本节凡写"支持集合""`justification_sets`/`support_members`""`insert_justification_set`"之处，一律改为知识记录自带的 `support` 字段。替换后的条文：
>
> - `knowledge_standing(store, record) -> "CURRENT" | "SUPERSEDED" | "STALE:<原因>"`：按 1.4 的四条只读 `record.support`；`support` 为空判 `STALE:no_support`（开发期不兼容）。不读 `justification_sets`。
> - 构造 `KnowledgeRecord` 前由 `_knowledge_support()` 算出 `support`（来源验收 `acceptance_id_for(任务, 结果)`；确认引用或 `evidence` 命中的产物带版本与内容哈希；`used_knowledge` 里同任务、非自身的知识带版本与内容哈希），随知识行一次 `upsert_knowledge` 写入；系统测试观察那条用 `replace` 补上后再写。**不写 `justification_sets` / `support_members`。** 删 `KnowledgeRecord.dependencies` 及其写入（`commit_service.py`、`SDK/memory/code_observations.py:118`），读工具展示（`SDK/context/retrieval.py:347`）改从 `support.knowledge` 生成同名键。`KnowledgeCommitted` 事件载荷加 `basis`、`support`。
> 5. 重放清单 `business_replay_inventory.json`：`justification_sets`、`support_members` 两条不动（缺口保留，去留归 D）；`knowledge` 条目加 `note`："支持集合记在 json 的 support 字段，与知识行同一次写入、建成后不改；是否当前为读时推出，不落库"。
>
> 用例 3 预期：第 1 条知识 `VERIFIED`、`verifier.basis=="review_confirmed"`、带记录编号；`support` 含来源验收编号与产物（编号、版本、内容哈希）；本任务 `justification_sets`/`support_members` 无行；第 2 条仍是"有支持"；验收成功；收尾评估不含 `EVIDENCE_STALE`

1. 新文件 `SDK/memory/knowledge_standing.py`：
   - `acceptance_is_current(store, mission_id, acceptance_id) -> (bool, reason)`：从 `completion_support.py:28-90` 抽出（义务状态、完成范围仍完整），`read_completion_support` 与 `current_child_supports` 改调它——**同一判定只留一份**。
   - `knowledge_standing(store, record) -> "CURRENT" | "SUPERSEDED" | "STALE:<原因>"`：按 1.4 的四条读支持集合（`HtnStore.list_justification_sets(mission, subject_kind="knowledge")`）。
2. `SDK/orchestrator/commit_service.py`：
   - `_lock_assured_acceptance`（:2683-2708）返回锁定的候选证书；`_accept_result`（:2710 起）把其中的正式记录传给 `_grade_and_project`（:2757）。
   - `_grade_and_project`（:980-1162）：读记录的清单（照 `assurance_review_runtime.py:149-163` 的读法）。一条结论升"已验证"须同时满足：记录总结论为 `ACCEPT`，或为 `INCONCLUSIVE` 且人裁决通过（`adjudicated_pass`）；清单 `claims` 里该编号 `confirmed=true`；`content_sha256` 与当前结论原文一致；`evidence_refs` 非空且至少有一项**不是审查对象本身**（结果信封）；仍先过矛盾规则（:1081-1087，矛盾照旧封顶为"有争议"）。满足的写 `KnowledgeRecord`，`verifier={"basis":"review_confirmed","record_id","review_key","reviewer_agent_id","manifest_hash","reason","evidence_refs","adjudication"}`；系统测试观察那条（:1139-1146）在 `verifier` 里补 `"basis":"test_observation"`。
   - 同一事务里调 `HtnStore.insert_justification_set(mission, "kn-<知识编号>", subject_kind="knowledge", subject_id=知识编号, members=[验收, 证据产物, 用过的知识])`；`KnowledgeCommitted` 事件载荷加 `basis`、`support_set_id`。
   - 用过的知识的解析（:1014-1023、:1144-1161）改按 `编号@版本`；`KnowledgeUsed` 事件已带版本，不变。
   - 验收前核对（:2610-2621）改调新的 `KnowledgeIndex.check`。
3. `SDK/memory/verified_knowledge.py:131-158` 的 `KnowledgeIndex.check`：解析 `编号@版本`；版本不符、`knowledge_standing` 不是 `CURRENT` 都报问题。`load` 需要带上 `store`（已有 `_store` 字段）。
4. `SDK/memory/claims.py:4-20,191-192`：模块说明与 `basis.reason` 改为"已验证来自系统测试观察或独立审阅逐条确认"；定级本身不变。
5. 重放清单 `business_replay_inventory.json`：`justification_sets`、`support_members` 删掉 `gaps`，`events` 填 `KnowledgeCommitted`，`writers` 加 `commit_service.CommitService._grade_and_project`；`knowledge` 条目备注"是否当前为读时推出，不落库"。

### C5　黑板三层读工具、删 `blackboard.py`、上下文过滤（C）
1. `SDK/context/knowledge_tools.py:13-75` 重写取数（工具名、参数、说明一律不动）：
   - `knowledge_list` 返回三层合在一个有序目录里，每项带 `layer`：
     - `verified`：`knowledge_standing == CURRENT` 的知识，带 `ref`（`编号@版本`）、`basis`（测试观察 / 独立审阅确认）、来源步骤、证据；
     - `candidate`：本任务里非"已验证"的结论（提出、审查中、有支持、有争议），带 `marker: "未验证"` 或 `"有争议，不是事实"`；
     - `raw_ref`：已验收步骤的原始记录引用——步骤编号、结果编号、产物编号与路径，**不带任何文件内容**；
     - 摘要层不开放。
   - `knowledge_read` 按编号读原文：已验证与候选给原文与出处；原始记录引用只回引用本身。过时或不存在的编号拒绝。目录哈希续读机制照旧。
   - 只读本任务（`mission_id` 由网关绑定，跨任务读不到）。
2. 删 `SDK/memory/blackboard.py`；改 `SDK/memory/__init__.py:4`；删 `T/step04/test_claims_knowledge_main_loop.py` 里对它的两处使用。
3. `SDK/orchestrator/event_handler.py:5105-5150` 的 `_gather_knowledge`：取知识后先按 `knowledge_standing` 过滤再排序；`SDK/context/retrieval.py:157-262` 与 `SDK/memory/summaries.py:20-40` 删掉没人传的 `stale` 参数（过滤只在 `knowledge_standing` 一处）。
4. `SDK/context/context_builder.py:110-134`：知识段条目写 `ref: 编号@版本` 与 `basis`；文字改为"verified 可当事实引用，引用时把 `编号@版本` 写进 used_knowledge；可用 knowledge_list/knowledge_read 查目录与原文；candidate 只是线索"。升 `context-builder-v5`。
5. 执行者模板加工具（文字随 C8）：`role_templates.py:482` 的 `tool_names` 加两个知识工具。

### C6　审阅员信息缺口（C）
只改实现，不改证据工具说明（它们也在执行池身份里）。
1. `SDK/verification/reviewer_evidence_tools.py:180-213`（`_universe`）：产物带路径；同一路径多个版本的，非现行版本标 `current: false` 与取代者。
2. 同文件 `_find`（:276-320）：`query` 也按路径匹配（说明里本来就写了"路径子串"）；每项加 `citable_now`（在初始目录里、或本回合已整段读过为真）与 `cite_rule: "整段读过才能引用"`。
3. `SDK/orchestrator/leaf_acceptance.py:587`：候选引用的内容哈希改为结果信封的指纹，与审阅员能读到的审查对象（`assurance_content_review.py:135` 的 `target`）同一个哈希。
4. C0 核对仍在的第 4、5 条，在这里修（回执写明所属结果与任务；必需检查保证有可读回执）。第 6 条（32 次工具上限）没有不够用的证据，不改。

### C7　交接前核对凭证与纪元（E）
1. `SDK/orchestrator/action_commits.py:1055-1104` 的 `_handoff_refusal` 末尾加 `_validity_refusal(action)`（首次交接与重新交接都走）：
   - 找到这次操作的效果归属步骤（复用 `operation_outcomes._effect_owner`，`operation_outcomes.py:49`）；
   - 读该步骤的有效性见证（`HtnStore.list_validity_witnesses`，`htn_store.py:1350`），对每条 `USABLE` 的见证用就绪判断同一个函数 `_witness_verdict`（`SDK/graph/eligibility.py:726`）按**当前作用域纪元**（`SDK/orchestrator/taskgraph_epochs.py:13` 的 `current_scope_epochs`，前次裁决定的唯一一份）判新鲜；
   - 见证 `support_refs` 里的验收用 `acceptance_is_current`（C4）判当前；
   - 任一不过，返回 `validity_stale:<原因>`。
2. 拒绝不扣次数、不改状态：沿用 `begin_handoff`（:888）现有"拒绝 → 下一轮再试"的处理；输入见证每轮按验收现状重发（`hierarchical_dispatch.py:1150-1210`），纪元或验收恢复后自然通过，否则卡死明细里如实写出交给规划器。
3. 错误码表 `SDK/contracts/error_table.py`：`TaskGraphBoundaryCode` 加 `VALIDITY_STALE`，类别 `REQUEST_STALE`。
4. 上下文一侧：输入已在 `input_bindings.py:739-757` 按纪元核对，本步只补一条用例钉住；知识一侧由 C5 第 3 条完成。前次裁决已定：C 与 D 之间作用域纪元在产品里恒为 0，用例里直接调 `bump_epoch` 造过期，实施记录写明这段空档。

### C8　提示词统一升版、清单与文档（F）
1. 执行者：`role_templates.py:430` 升 `worker-hierarchical-v6`；正文 :448-450 改为讲清三层、`编号@版本`、两个工具；:462-463 删"只有引用了跑过的 pytest 目标才可能被判 VERIFIED"，改为"每条结论都会交给独立审阅员逐条核对；只写有证据支持的结论，evidence 里写产物路径或检查"。无人机执行者（:582-592）共用正文，升 `v2`。AppWorld（`appworld_templates.py:10,23-24`）把"知识ID"改为"`编号@版本`"，升 `v2`。
2. 审阅员：`SDK/assurance/review_input.py:14-50` 改写一次——回复格式改成"最好不用代码围栏、不加多余字段；用了围栏或加了值为空的字段会被容忍，有值的多余字段、JSON 前后的文字、超长仍会被拒"；加 `claims` 的完整示例与逐字段含义（漏写算未确认；确认必须引用审查包证据目录里的 ev- 标签，不能只引审查对象本身）；讲清"本步待确认结论""相关条目"两节是什么、相关条目不是证据；补信息缺口第 9 条（标为"不可信外部来源"的资料：用户给的资料作为任务口径如何判，按任务要求来，不能证明的写 UNKNOWN 并说明）。
3. 规划器提示词不动（D 阶段集中升版）。
4. 钉哈希的模板测试按当前版本重生成基线（CLAUDE.md：不留旧版本）。
5. 文档：实施记录写明"未改 `TOOL_SCHEMAS`、未重写编码清单、无新迁移；提示词升版后旧任务起不来（新建任务）；纪元写方空档"；`ARCHITECTURE/`、需求与场景状态（计划第六节）同次更新；本阶段末发版一次、Host 钉版，按 3.5 版流程真机放到全部代码写完后。

---

## 四、功能性用例清单（12 条）

**结论：12 条产品同形用例，全部跑在 `SDK/testing/product_world.py` 的世界上，用 `SDK/testing/scripted_replies.py` 的分层脚本化通道；每个功能至少一条带改坏检验。**

通用构造：`product_world(tmp, LayeredScriptedProvider(planner=…, reviewer=…, worker=…), allowed_tools=DEFAULT_TOOLS+("knowledge_list","knowledge_read"))`；审阅员回复在 `review_reply(package, …)`（`scripted_replies.py:179-200`）基础上改 `schema_version=3` 并按需加 `claims`；执行者按 `worker_reply`（:208-233）的样子写文件、交结果（结论的 `evidence` 写产物路径）。两步任务用 `continue-delivery` 接上游（`product_world.py` 的世界里已有）。

| # | 用例名 | 文件（`T/product_world/` 下） | 脚本要点 | 断言 |
|---|---|---|---|---|
| 1 | `test_fenced_reply_and_empty_extra_field_are_accepted` | `test_review_reply_format.py` | 叶子审阅员回复包在 ```` ```json ```` 围栏里，并多一个 `"notes": ""` | 审阅员只被问 1 次；任务完成；`AssuranceReviewFormatRejected` 事件 0 条 |
| 2 | `test_text_around_json_still_rejected` | 同上 | 第 1 次回复前面多一句话，第 2 次正常 | 审阅员被问 2 次（第 2 次理由 `FORMAT_REPAIR`）；任务完成 |
| 3 | `test_confirmed_claim_becomes_verified_knowledge` | `test_knowledge_confirm.py` | 执行者交 2 条结论；审阅员 `claims` 只确认第 1 条（引用产物的 ev- 标签），第 2 条不写 | 第 1 条知识 `VERIFIED`、`verifier.basis=="review_confirmed"`、带记录编号；存在主体为该知识的支持集合，成员含验收与产物；第 2 条仍是"有支持"；验收成功 |
| 4 | `test_confirmation_without_independent_evidence_not_upgraded` | 同上 | 两个子情形：①确认但 `evidence_ids` 只有审查对象（结果信封）的标签；②总结论 `REWORK` 但 `claims` 全确认 | 两种都不产生知识 |
| 5 | `test_person_pass_on_inconclusive_upgrades_confirmed_claims` | 同上 | 审阅员两次都 `INCONCLUSIVE`，`claims` 确认第 1 条；测试用门面命令以用户身份复核"通过" | 裁决前知识为 0；裁决后第 1 条 `VERIFIED`，`verifier.adjudication` 非空 |
| 6 | `test_worker_reads_three_layers_and_cites_version` | `test_blackboard_tools.py` | 两步任务，第 1 步结论被确认；第 2 步执行者先调 `knowledge_list`、再 `knowledge_read` 那条已验证编号，交结果时 `used_knowledge=["<编号>@1"]` | 目录里三层都有、候选带"未验证"、原始引用层没有文件内容；`KnowledgeUsed` 事件版本为 1；第 2 步验收成功；变体：写 `"<编号>@2"` 或只写编号 → 验收以 `used_knowledge_stale` 拒 |
| 7 | `test_repaired_source_makes_knowledge_stale` | 同上 | 照 `test_repair_material.py` 的修复脚本，让第 1 步被修复（旧义务被取代） | 修复后 `knowledge_list` 不再列出该知识，`knowledge_read` 拒绝；`KnowledgeIndex.check` 报过时；知识行状态仍是 `VERIFIED`（证明是读时判定） |
| 8 | `test_review_package_has_claims_and_related_but_not_own` | `test_knowledge_confirm.py` | 第 1 步结论带主题键 `k1`、立场"肯定"并入库；第 2 步结论同键立场"否定"，且第 2 步上一次尝试也有一条结论 | 第 2 步审阅员收到的审查包：`claims_to_confirm` 恰是本次结果的结论；`related_entries` 含第 1 步那条（带版本与哈希）；不含第 2 步任何尝试的结论 |
| 9 | `test_final_review_without_usable_reply_asks_person` | `test_review_no_verdict.py` | 根终审的审阅员两次都回非 JSON 文本；测试以用户身份回答裁决题"通过" | 根终审审阅员恰被问 2 次；正式记录 `INCONCLUSIVE` 且清单 `interpretation=="NO_USABLE_REPLY"`；登记了 `adjudicate-root:` 裁决题；回答后任务完成 |
| 10 | `test_outcome_review_without_usable_reply_asks_person` | 同上（世界照 `test_operation.py`） | 发布结果审阅员两次都回带有值多余字段的 JSON；用户裁决"通过" | 登记 `adjudicate-outcome:` 裁决题；裁决后 `OperationOutcomeAccepted` 出现；改裁"打回"的变体：不验收、卡死明细里有这条审查 |
| 11 | `test_composition_ruling_stale_reported_as_no_verdict` | 同上（世界照 `test_sub_goal.py`） | 组合审查两次回复都无法采用 → 裁决题登记；测试推进计划版本让题目过期 | 题目状态 `STALE`；规划器收到一条修复请求，明细 `outcome=="NO_VERDICT"`，同一记录只一条 |
| 12 | `test_handoff_refused_when_scope_epoch_moved` | `test_validity_handoff.py`（世界照 `test_operation.py`） | 发布动作准备好后、交接前调 `HtnStore.bump_epoch`；子情形：让效果归属步骤的上游验收不再当前 | 交接被拒、原因 `validity_stale:…`、动作的交接次数仍为 0、不扣尝试次数；下一轮见证重发后交接成功（纪元情形）；验收情形一直拒绝并出现在卡死明细里 |

**改坏检验（每个功能一条，改坏后对应用例必须失败；改前先 `cp` 备份，改坏后清 `__pycache__`）：**

| 功能 | 改坏哪里 | 应失败的用例 |
|---|---|---|
| A 格式口径 | 去掉 `decode_review_reply` 的剥围栏 | 1 |
| B 知识进库 | 去掉"总结论通过或人裁决通过"条件 | 4②（以及 5 的裁决前断言） |
| C 黑板取用 | `knowledge_list` 不调 `knowledge_standing` | 7 |
| D 没有结论 | 第 2 次格式错误改回写"格式用完"回执 | 9 |
| E 有效性 | 去掉 `_validity_refusal` | 12 |

只跑这 12 条与被改文件直接对应的旧用例（如 `T/product_world/test_operation.py`、`test_sub_goal.py`、`T/step04/test_claims_knowledge_main_loop.py`）；不跑整目录、不跑全量。无关用例红了记下另行处理。

---

## 五、偏差单（6 条）

**结论：6 处计划文字和代码现实或硬约束对不上，都已给出裁决和改法；第 2、5 条改变用户看得到的行为，建议报用户知悉。**

| # | 计划原文 | 发现的事实 | 可选做法 | 裁决 | 计划文字怎么改 |
|---|---|---|---|---|---|
| 1 | 阶段 C 第 3 条："审阅员两次回复都不能采用、没有结论时……换新会话复审一次；仍没有结论就出卡片问用户" | 第 2 次调用（格式修复）本来就是新派发、新会话（`审阅升级-方案.md` 一节；`SDK/assurance/reviews.py:47`）；调用序号上限 2 写死在三个合同里（`reviews.py:89,306,323`） | ①加第 3 次调用（改三个合同、预留与工作键）；②把第 2 次调用视为"新会话复审" | ②。两次独立会话后问人，与"判不下来"同形；不改协议 | 改为："审阅员第 1 次回复不能采用时照旧在新会话重问一次（这一次即复审）；第 2 次仍不能采用，记为'判不下来'，出卡片问用户……" |
| 2 | 同条括号："最终审查、组合审查、操作结果审查都算" | 按"没有结论 = 判不下来"在导入器一处实现后，叶子内容审查、做法审查也会走同一出口：叶子由"这次尝试算失败、重做"改为问人；做法审查由"直接 NO_VERDICT 交规划器"改为先问人（过期才交规划器） | ①只对三种用途生效（导入器按用途分支）；②一律生效 | ②。按用途分支就是"同一件事两条路"；叶子判不下来本来就问人，没有结论却重做，两个出口自相矛盾 | 括号改为："（所有审查一律如此；最终、组合、操作结果三种此前会卡死，是这次的重点）"。**建议报用户知悉：叶子步骤的复核卡片会变多** |
| 3 | 阶段 C 第 1 条"重建过时的生产方与读工具的过时过滤"；表二 8"失效自动'已过时'"；补全方案 6.5"复用现有过时通道（`KnowledgeIndex.stale`）" | 验收是否当前本身就是读时推出、库里不写（见 1.4）；让验收失效的写路径有好几条 | ①在每条写路径上挂"写已过时"；②读时一个函数判定 | ②。只有一处判定、不会漏；与验收的做法一致；前次裁决已定知识过时不加纪元 | 改为："过时不落库：知识被读时由 `knowledge_standing` 按支持集合判定（来源验收是否当前、证据产物是否现行、上游知识是否当前）；读工具、上下文、`used_knowledge` 核对、审查包都只用它" |
| 4 | 补全方案 6.5 第 4 条"审阅员的读工具排除被审步骤……读过哪些条目写进审阅记录" | 审阅员已有两件证据工具，读取记录走披露回执；给知识工具要再造一套记录，且会形成"知识证明知识"的循环（见 1.3） | ①给审阅员知识工具；②相关条目写进审查包 | ②。可见范围由审查包决定，"读过的条目"即审查包内容，已钉进正式记录 | 改为："审阅员本期不给知识读工具；争议与本步用过的知识按编号、版本、哈希写进审查包'相关条目'一节（排除被审步骤所有尝试的结论）；相关条目不进证据目录，不能当证据引用" |
| 5 | 知识进库方案第 1 条"不给执行者的总结和自我解释，保持审阅独立" | 审阅员的审查对象就是完整结果信封（`SDK/storage/assurance_reads.py:199-200`；`assurance_content_review.py:135`），里面有总结和结论原文；换成去掉总结的投影要新增引用种类、改证书与披露 | ①本期改审查对象投影；②保留现状，用证据规则守住"不能拿本步说法证明自己" | ②。确认一条结论时，`evidence_ids` 必须至少有一项不是审查对象本身（C4），由程序核对引用种类，属秩序 | 改为："新增的'本步待确认结论'一节不带总结；审查对象仍是完整结果；确认结论必须引用审查对象以外的证据"。**建议报用户知悉** |
| 6 | 阶段 C 第 3 条"审查没有结论"的范围 | "调用没回来"（服务端报错两次、被重启打断、超时放弃）现在也写"格式用完"回执，与"回复不能用"同名 | ①一并当"没有结论"问人；②当基础设施故障，按 09-28 决定原地重试、不计次数 | ②。不是审阅员给不出结论，是调用没完成；问人会把基础设施问题推给用户 | 加一句："调用没回来的情形不属本条，按基础设施失败处理（见第六节去向）" |

计划第 3.5 版流程要求"先改计划再写代码"：开工前把上表第 1～6 条的改法写进计划（升第 3.6 版、修订记录写原因）。

---

## 六、不在本阶段做的

**结论：以下明确划出去，各有去处。**

| 事项 | 去处 |
|---|---|
| 作用域纪元的生产写方（桌面观察、要求修订） | D、E（前次裁决 1.2 第 3 条）；C 只接读方 |
| "要求修订让旧验收失效 → 知识过时"这一支的测试 | E（计划原文） |
| 计划读集的 `SupportSetRead` 接上或删、`manager_epoch` 删 | D（动 `contracts/htn.py`，并进 D 的那次编码清单重写） |
| 摘要层：上下文里仍推送规则截断的分支摘要与全局摘要（`event_handler.py:5159-5160`），读工具不开放摘要层 | C3+摘要 |
| 中间层组合审查、根终审产生新结论 | 不做（知识进库方案"范围"已定只做叶子） |
| 调用没回来而用完的审查（偏差单 6）按基础设施失败原地重试、设上限 | 记为后续项，归阶段 B 裁决第 9 类同一口径，另立小项；本阶段保持现有路径（根审查重切、结果审查重审、其余卡死后如实上报） |
| 审阅员信息缺口第 6 条（单次审查 32 次工具上限）、第 10 条（检查器"通过"的含义，指令里已有一句，`review_input.py:38`） | 不做；有真机证据再议 |
| 审查对象换成不含总结的投影（偏差单 5 的①） | 不做，需要时单独立项 |
| `knowledge_sharing` 开关（`SDK/runtime/assembly.py:110`）、旧 `worker-v3` 模板（`role_templates.py:47-73`）的去留 | 不在本阶段；属"旧路径清理"欠账 |
| 真机一局（两步任务入库、第二步带版本引用、修复后转过时） | 按第 3.5 版流程，全部代码写完后统一做 |
| 发版 | 本阶段代码写完后一次（换版本号、Host 钉版、部署清单），不分小批 |
