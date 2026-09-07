# 决策备忘：Procedure 真实使用链（HM-AC-5 / S3 Task 3）——r24/r25 FAIL 根因与最小修复

> **独立子代理分析，主代理复核后执行。** 2026-09-07 深夜。Host 仓 `simple_harness` main `5a460f49`，Memory SDK `simple-harness-memory-sdk` main `89d67b6`（0.6.24）。只读代码、计划记录与 ignored 证据库（`sqlite3 -readonly`），未改源码，未运行原生应用，未调用模型。

## 0. 结论（一句话）

r24/r25 的「`procedure_discover` 返回 0」不是发现工具坏了，而是**上游分类把待定流程压成了 episode（提示词 v5，已由 v5.1/v6 消除）**；但在消除之后，主线上仍然留着一条**产品缺陷**：模型能触达的两个查询面对「已采用、尚未使用过」的 Procedure 都不可见——`procedure_discover` 只看 draft/eligible（SDK `backends/procedure_discovery.py:42`），typed recall（含 0.6.24 向量通道）只看 active/reinforced **且**要求 applicability 指纹已绑定并等于当前 Run 指纹（`sqlite_v5.py` `_cognitive_recall_state_allowed` / `_cognitive_recall_type_authority_allowed_unlocked`）。r8/r9 的真实库就是实证：`procedure「整理文件」 lifecycle=active, applicability_fingerprint=unbound:procedure-applicability:v2`，被向量世代嵌入（vector_count=2）却任何一面都召不回。**推荐唯一最小修复：SDK 侧把发现面的 lifecycle 白名单扩到 active/reinforced，并把子串匹配换成与 typed recall 同源的 `typed_recall_query_terms` 词项匹配；Host 侧只改工具描述与 PERSONA 一句话。** 向量通道不动。

## 1. 实现链（问题 1）

### 1.1 `procedure_discover`

| 层 | 位置 | 行为 |
|---|---|---|
| 工具注册 | `backend/deskpet/sdk_adapters/procedure_discovery.py:9-19` | `procedure_discover`，schema `{query, after}`，描述写死 "Find **draft/eligible** saved Procedures … by a **substring** of their name or steps" |
| 直接内核 | `backend/deskpet/sdk_adapters/tool_authority.py:114-126` `SDK_DIRECT_TOOL_KERNEL` | `procedure_discover`/`procedure_use`/`context_route`/`task_scope_search` 无需 `tool_search` 即可见（r24/r25 start inventory 与 Provider projection 都确认 12 工具含两者，NATIVE-R25 第 3 段） |
| Host 运行时 | `backend/deskpet/memory/procedure_runtime.py:28-47` | 校验参数、解析当前 disclosure、经操作审计 `operation_audit.invoke(manager, "discover_procedure_drafts", … query, after, limit=8, max_bytes=32768)`，前后 disclosure 不变才返回 `{kind: procedure_draft_preview, execution_authorized: False, candidates[...] , next_after, omitted_oversize}` |
| SDK 入口 | `core/manager.py:1324-1329` → `backends/sqlite_v5.py:6093-6098` → `backends/procedure_discovery.py:81-131` | **检索方式：既不是 typed recall，也不是 FTS，也不是 applicability 指纹，更不是向量。** 是「按 `memory_id` 顺序全表扫 personal scope 的 procedure head（`:102-106`），逐条 `read_candidate` 重建规范 payload，再做 `query.casefold() in (name + '\n' + steps).casefold()` 子串匹配（`:112`）」 |
| 候选门 | `backends/procedure_discovery.py:42` | `lifecycle_state not in ("draft", "eligible_for_activation") → None`；`core/procedure_discovery.py:34-35` `ProcedureDraftCandidate.__post_init__` 同样只接受这两个状态 |
| 下一轮复核 | `backend/deskpet/execution/primary_dependencies.py:262, 302-314` | 历史里的 `procedure_discover` 结果被重新校验为 `procedure_drafts` 依赖，`HistoryProcedureDraftBinding`（SDK `core/history.py:91-104`）在下一次 send 前重查来源 |

### 1.2 `procedure_use` 与后续观察

- 注册：`backend/deskpet/sdk_adapters/procedure_use.py:17-27`（`memory_id/revision/steps[{text,tool,arguments_json}]`）。
- 绑定：`procedure_runtime.py:49-71` → SDK `read_procedure_use_target`（`sqlite_v5.py:6100-6175`，接受 **draft/eligible/active/reinforced** 四态，`:6140-6142`）→ Host `ProcedureUseStore.bind`（`procedure_use_store.py:50-109`）：步骤文本 hash 必须与 revision 一致（`:59-60`），用当前 Run 的真实工具快照算 applicability 指纹（`procedure_applicability.py:29-68`），且必须有已路由的 active TaskScope 与单根绑定（`procedure_route.py:21-68`）。
- 每次物理工具调用前 `before_call` 预留步骤（`procedure_runtime.py:73-92`）；终态组由 worker `observe_group` 收口（`:120-178`）→ SDK `prepare/record_procedure_observation`。首次观察把 UNBOUND 指纹绑定为本次指纹（`sqlite_v5.py:6331-6335` `reason_code="procedure_applicability_bound"`），低风险成功计数 2→eligible、3→active（`:6375-6397`）。
- Host 持久表在 **Host state DB**（`runtime_composition.py:42` `ProcedureUseStore(state_db_path…)`；表 `procedure_uses / procedure_use_reservations / procedure_use_effects / procedure_observation_journal`，`procedure_schema.py:17`）；操作审计在同目录 `operation-audit.db`（`procedure_runtime.py:22-23`）。r9 的 SDK 库里没有 `procedure_uses` 表是正常的，取证时不要查错库。

### 1.3 当时为什么返回 0

1. **库里根本没有 procedure head。** r24 Host `b2da14da` 的分析协议是 v5：真实模型对「记录待定的松柏记录文件流程、不执行」只提了 episode（`plans/2026-09-07-procedure-draft-classification/CONTRACT.md` "Observed problem"；REMAINING.md:10 "已从r24公开实际响应确认只有episode，非编译器降级"）。r25 沿用 r24 userdata，Host `d86e4805` 也不含 v5.1（`git merge-base --is-ancestor 342e2722 d86e4805` → 否），于是 `procedure_discover("松柏记录")` 全表扫描零候选，SDK 如实返回 `candidates=[]`（NATIVE-R25 第 2 段）。这一步发现工具本身工作正常。
2. **即使当时有 DRAFT，也只有在模型起的 `name`/`steps` 字面包含模型选的 `query` 时才命中**（`procedure_discovery.py:112`）。测试只覆盖了 `'记录'`（Host `tests/memory/test_procedure_discovery_runtime.py:30`）与 `'publish'`/`'not-present'`（SDK `tests/integration/test_procedure_discovery.py:17,57`），没有中文同义/分词用例。

### 1.4 向量通道落地后这条路径有没有受益

**没有，而且不会自动受益——discover 走的是完全不同的查询面。**

- 向量世代只嵌入通过 `_cognitive_recall_state_allowed(allow_contested=True)` 的 head（`sqlite_v5.py:2783-2802`），procedure 只有 `{active, reinforced}`（`:5655-5660`）→ **DRAFT 从不进向量世代**，`cognitive_vector_text('procedure')`（`features/cognitive_vector.py:75-80`）对草稿无用。
- typed recall 的 `vector` lane 只给「已通过全部资格门」的候选打分（`sqlite_v5.py:4880-4890` 注释 "Every eligibility gate above has passed"；`_CognitiveVectorLane.score` `:18738-18763` 用 `eligible_refs` 限定）。procedure 的资格门还包括 `_cognitive_recall_type_authority_allowed_unlocked`：`applicability_fingerprint != UNBOUND 且 ∈ Host current_fingerprints`；Host `current_fingerprints`（`procedure_runtime.py:94-118`）只从既有 `procedure_uses` 行推导，且需要当前 Run 已路由到 active scope（`memory_standalone` 路由下 `resolve_procedure_route` 抛错 → 返回 `()`）。
- 因此 Host「typed 计划恒请求 VECTOR」（`human_memory_v7.py:408-413`）对 Procedure 首次使用毫无影响。**实证**：r8/r9 库（`.local-test-evidence/2026-09-07/native-70c5ef89/primary-ui-9gbpm518/userdata/data/human_memory_v7.db`，只读查询）`procedure|active|explicit_user|source_bound|整理文件|unbound:procedure-applicability:v2`；r9 typed plan types 含 procedure、召回 8 项，无一 procedure（NATIVE-R9 第 5 行）。

可见性矩阵（当前主线）：

| Procedure 状态 | `procedure_discover` | typed recall（含向量） | 模型能否拿到 memory_id/revision 去 `procedure_use` |
|---|---|---|---|
| DRAFT / ELIGIBLE（uncertain、reported_steps；v4 `analysis_proposal_v4.py:147`） | 是（子串） | 否 | 只靠子串命中 |
| ACTIVE + UNBOUND（用户明确采用，v4 `:147` adoption→ACTIVE） | **否**（`:42`） | **否**（指纹门） | **无任何入口** ← r8/r9 数据 |
| ACTIVE + 已绑定指纹（至少用过一次） | 否 | 仅在同工具/同环境、已路由 active scope 的 Run 内 | 是（`RecallCandidate.source_ref=memory_id, source_revision`，`sqlite_v5.py:5317-5320`；经 `typed_context_use.py:148-152` 进入 fragment） |

## 2. r24/r25 三类现象的根因分类（问题 2）

| 现象 | 根因分类 | 证据 | 09-07 后状态 |
|---|---|---|---|
| **记录可见但 discover 为 0** | ① 提示词（分析协议 v5 把"待定可复用流程"分成 episode）；② 测试设计（把记忆列表里出现条目等同于"已存 Procedure"，列表不显示类型/lifecycle，NATIVE-R24 第 1 段自己也注明）；③ 产品缺陷（发现面只做子串，且不含 ACTIVE-unbound） | CONTRACT.md "Observed problem"；REMAINING.md:10；`procedure_discovery.py:42,112` | ① **已消除**：v5.1 `342e2722`（`analysis_proposal_v5_1.py:15` "uncertain… 由编译器保存为 DRAFT"），v6 继承 v5.1 全部指令（`analysis_proposal_v6.py:66-69`），真实模型正/反例各 1 次通过（MODEL-RESULTS）；原生 durable job 路径未验。② 未变（建议 r10 用 DB 而不是列表判定）。③ **未消除**，见 §3 |
| **重复 tool_search、停止后才补出 context_route** | 测试环境/设计：第二条输入一次要求「建任务 + 找流程 + 执行 + 读文件核验」；模型 `context_route(create_new)` 后为寻找文件工具连打 4 次 `tool_search`（`procedure_discover` 在直接内核里，不需要 tool_search，`tool_authority.py:114-126`），随后 Provider transport error / unknown_settled 卡了 9 分钟到用户手动停止（NATIVE-R24 "后续精确审计更正"）。UI 旧的「等待授权」是 S5b 弹窗时代的残留 | NATIVE-R24 §"实际结果"、§"后续精确审计更正" | 「等待授权」场景**已消除**（auto 模式不再弹窗，`tool_authority.py` auto→`explicit_only=False`，用户 09-07 决定）；Provider 断流属环境，非 Procedure 链缺陷；tool_search 连打是延迟工具目录下的模型行为，非本链问题，不在本决策内修 |
| **任务状态 ↔ Procedure 状态混淆**（r25 第一问调 `task_scope_search`，把任务 active 当流程草稿 active） | ① 提示词：PERSONA 当时没有区分；② 测试设计：r24 任务标题与记忆都含"松柏记录"，同名双实体；③ 产品：库里没有 procedure，模型只能找到任务 | NATIVE-R25 第 2 段；`git merge-base --is-ancestor dda154d6 d86e4805` → 否 | ① **已消除**：`dda154d6`（09-07 01:52）加入 `primary_context.py:34-36` "A TaskScope and its lifecycle state are not a stored Procedure or its adoption state. For a stored workflow candidate, use procedure_discover…"，已在 `70c5ef89`（r9 包）中；未在原生正向验证。③ 随 ① 与 §3 一起消除 |

综上：09-07 的改动消除了 r24/r25 的**直接**失败原因（分类、任务/Procedure 提示、授权弹窗），但没有触及发现面本身；只要 r10 用「以后就按这两步」这种最自然的采用句式，`procedure_discover` 仍会返回 0（ACTIVE-unbound 不在白名单），会再次 FAIL。这是必须先修的阻塞项。

## 3. 唯一推荐的最小修复（问题 3）

原则：不改向量世代/manifest（草稿入向量要改 stale 判定与 4 项 0.6.23/0.6.24 测试，不是最小）；不放宽 typed recall 的指纹门（S3 设计"applicable recall"语义正确，SDK `test_typed_recall_v6.py:944` 已冻结）；把「候选发现」与「适用召回」两个面分清：**发现面 = 任何可用状态的候选 + 词项匹配；召回面 = 已绑定且当前适用。**

### 3.1 SDK（Memory 0.6.25 候选，约 45 行源码 + 约 90 行测试）

| 文件 | 改动 | 估计 |
|---|---|---|
| `src/simple_harness_memory/backends/procedure_discovery.py:42` | lifecycle 白名单改为 `("draft", "eligible_for_activation", "active", "reinforced")`（与 `read_procedure_use_target` `:6140-6142` 一致；`inapplicable/revised/superseded` 仍排除） | 1 行 |
| 同文件 `:112` | 匹配改为：`terms = typed_recall_query_terms(query)`（`features/lexical.py:46`，与 typed recall/CJK 修复同源的 `\w` 词 + CJK bigram）；命中条件 = 子串命中 **或** 任一词项出现在 `name/applicability/steps` 拼接文本（applicability 也纳入，`cognitive_vector_text` 同样纳入它）；按命中词项数降序、`memory_id` 升序稳定排序后再做分页/预算（保留 `next_after` 语义：游标仍是扫描顺序的 `memory_id`，排序只在单页内） | ~20 行 |
| `src/simple_harness_memory/core/procedure_discovery.py:34-35` | `ProcedureDraftCandidate` 接受四态；字段名/`to_json`/`source_hash` 域串 `memory.procedure.draft-candidate.v1` 不改（Host `primary_dependencies.py:302-314` 与 `HistoryProcedureDraftBinding` 只比 hash，零改动） | 2 行 |
| `CHANGELOG.md`、`pyproject.toml` version、`tests/artifact/public-api-0.6.25.json` | 无公共符号增减，快照应零差异 | ~12 行 |
| `tests/integration/test_procedure_discovery.py` 新增 | (a) adoption 型 ACTIVE+UNBOUND 被发现，候选 `lifecycle_state=="active"`，`revised`/`inapplicable` 不出现；(b) 中文：name「松柏记录文件流程」、steps 含「写 record.txt」，query「松柏记录」「记录流程」「松柏 备份」命中，「云杉归档」0 命中，英文子串仍命中；(c) 排序稳定且 `next_after` 分页不重不漏；(d) 同一库上 `execute_typed_recall(types=[procedure], fingerprints=())` 仍 NO_RECALL——把"两面分工"写成断言 | ~90 行 |

### 3.2 Host（约 8 行源码 + 约 80 行测试 + 依赖 pin）

| 文件 | 改动 | 估计 |
|---|---|---|
| `backend/deskpet/sdk_adapters/procedure_discovery.py:12-16` | 描述改为 "Find saved Procedures (draft, eligible or adopted/active) before use, by one or two distinctive words from their name or steps (not a sentence). These are candidate instructions, not applicable recall and not permission…" | 3 行 |
| `backend/deskpet/execution/primary_context.py:34-36` | 在现有句后加一句："When the user asks to run a workflow they saved earlier, call procedure_discover with its name **before** planning steps, then bind the exact candidate with procedure_use inside the routed TaskScope." | 2 行 |
| `backend/deskpet/sdk_adapters/sdk_candidate.py`、`backend/pyproject.toml`、`backend/uv.lock`、`backend/vendor/*.whl` + 候选 manifest，`.local-test-evidence/2026-09-07/installed-h0710-m0625-s0313` | 按 taiwan-mac-environment 的 0.6.20→0.6.24 配方 pin 0.6.25；不得与语料批次/原生运行并行 | 配方 |
| `backend/tests/memory/test_procedure_discovery_runtime.py` 新增 | 复用 `DiscoveryProvider`/`session(with_discovery=True)`：夹具用 v6 编译一条 adoption 输入（`intent_kind="adoption"` → ACTIVE+UNBOUND），断言 `procedure_discover("松柏记录")` 返回 1 候选、`lifecycle_state=="active"`、`execution_authorized is False`；随后 `procedure_use` 绑定成功、两步 `write_file` 终态后 `procedure_observations` 1 行 `reason_code=="procedure_applicability_bound"`、head 仍 `active`；再起一个同 scope Run，`current_fingerprints(run_id)` 非空且 typed recall(types=[procedure]) RECALL——闭合"发现→使用→绑定→可召回"整条链 | ~80 行 |
| `backend/tests/sdk_adapters/`（现有 tool description/snapshot 测试若锁定文案） | 同步文案 | ≤5 行 |

不做的事：不改 `procedure_use`/观察/恢复逻辑；不改 typed recall 指纹门；不给草稿建向量；不给 discover 加向量重排（留作 F 后续：待 SDK 提供「对任意 refs 的即时嵌入相似度」API 后再议）。

### 3.3 单测控制（执行顺序）

1. SDK 定向：`tests/integration/test_procedure_discovery.py`、`test_typed_recall_v6.py::test_procedure_recall_requires_exact_host_applicability_fingerprint`、`test_procedure_observation_repository_v5.py`、`test_cognitive_vector_generation.py`、`test_typed_recall_cognitive_vector.py`、`artifact/test_public_api_snapshot.py`——全绿后打 wheel。
2. Host 定向：`tests/memory/test_procedure_discovery_runtime.py`、`test_procedure_scope_runtime.py`、`test_procedure_recovery_runtime.py`、`test_procedure_adoption.py`、`test_procedure_draft_classification.py`、`tests/execution/test_procedure_draft_closure.py`、`tests/sdk_adapters/`（工具目录/描述快照）。已知红项（`taiwan-mac-environment` 列出的 12 项短索引夹具红等）不计。
3. 401 runner 的 installed-version 断言（`testcase/.../test_typed_recall_fixture_authorities.py`）随 pin 一起改。

### 3.4 原生 r10 旅程脚本（对话文案 + 预期证据）

环境：新 bundle（Host main 含 §3.2，M0.6.25），隔离 userdata，gpt-5.6-luna 主通道（记录 `provider.kind`），auto 模式，`launch_native_candidate.py` 在 `run_resource_bounded.py` 下启动；驱动用 `ax_click.sh` + 设置 AXTextArea value（无 computer-use）。所有判定以 SDK 库、Host state DB、`operation-audit.db`、`native.log`、工作目录文件为准；记忆列表只作旁证。

| 步 | 用户输入（原样） | 预期行为 | 预期 DB/日志证据 |
|---:|---|---|---|
| 1 | 请记住我以后常用的一个流程，名字叫“松柏记录”：第一步在当前工作目录写 record.txt，内容是当天的记录；第二步把同样内容再写一份 backup.txt。以后我说按松柏记录做，就按这两步做。 | 助手复述；后台 v6 提案 adoption | SDK 库 `cognitive_memory_heads` 1 行 `memory_type=procedure`；`procedure_records.name` 含“松柏记录”、`steps_json` 2 步、`applicability_fingerprint='unbound:procedure-applicability:v2'`；`cognitive_memory_revisions.lifecycle_state='active'`；`cognitive_vector_generations` 最新 active 世代 `vector_count>=1`；`native.log` `memory_outbox.applied` 1 次 |
| 2 | 我还有一个没定下来的想法，叫“云杉归档”：先把目录里的文件列个清单，再把清单写进 archive.txt。先记着，别执行，以后再说。 | uncertain → DRAFT | 第 2 条 procedure head，`lifecycle_state='draft'`，不进向量世代（`vector_count` 不因它增加）——这是 r24 的场景，改为可判定 |
| 3 | 帮我把“收到材料以后，我们再开始进行校对工作”改得简洁些。 | 普通回答，无召回 | `typed_recall_requests` 不新增或决策 no_recall |
| — | Cmd+Q 正常退出，确认进程退出，同一 userdata 冷启动 | 长期记忆隔离（r9 留给 r10 的严格隔离） | `native.log` 新进程启动、`startup complete`；资源记录 remaining=[] |
| 4 | 查一下我以前存的“云杉归档”流程草稿，现在是什么状态？只查，不要执行。 | 模型调 `procedure_discover`（不是 `task_scope_search`），报告 draft 且不执行 | `operation-audit.db` 1 行 `discover_procedure_drafts`；`native.log` 工具 `procedure_discover` 成功、结果 `candidates` 1 项 `lifecycle_state='draft'`、`execution_authorized=false`；无 `procedure_use`、无文件效果；无 `task_scope_search` 调用（有则记为提示词回归，不判 FAIL 但记录） |
| 5 | 新建一个本地任务叫“松柏九月”，在你管理的工作目录里，按我以前存的“松柏记录”流程做一遍，做完把两个文件读出来核对内容一致。 | `context_route(create_new)` → `procedure_discover("松柏记录")` 返回 1 候选 `active` → `procedure_use(memory_id, revision, 2 步 write_file)` → `write_file`×2（auto 无弹窗）→ `read_file`/`file_read`×2 → 终答一致 | Host state DB：`procedure_uses` 1 行（`memory_id`=步 1 head，`target_revision`=1，`steps` 2 项）、`procedure_use_reservations` 2 行、`procedure_use_effects` 2 行；`operation-audit.db` 依次 `discover_procedure_drafts`、`read_procedure_use_target`；工作目录 `record.txt` 与 `backup.txt` 存在且 SHA-256 相同；`native.log` 无 `waiting`/权限弹窗，无 `MalformedToolArgumentsError` |
| 6 | 这次做得怎么样，流程有没有记下来？ | 普通回答（不要求召回） | 等待后台 worker：SDK 库 `procedure_observations` 1 行 `outcome='success'`、`attributable=1`、`applicability_fingerprint`=本次指纹；`procedure_records` 新 revision `applicability_fingerprint` 已非 unbound、`bound_hazard='none'`；head `lifecycle_state` 仍 `active`；Host `procedure_observation_journal` `applied` 1 行；`operation-audit.db` `prepare_procedure_observation`、`record_procedure_observation` 各 1 行 |
| 7（可选） | 接着松柏九月，再按松柏记录做一次，文件名后面加“-2”。 | 同 scope 第二次使用；此时 typed recall 若被触发可命中 procedure（指纹已绑定） | `procedure_uses` 第 2 行（不同 sdk_run_id）；若有 `typed_recall_results`，`result_items` 含 `memory_type='procedure'` 且 lane 含 `full_text` 或 `vector`；`independent_successes=2`（仍 active，rank 不降） |
| — | Cmd+Q 退出 | | resource.json parent 0、remaining=[]、峰值/时长；四份 SHA-256（launch.json、native.log、resource.json、human_memory_v7.db、Host state DB） |

PASS 判定：步 4 与步 5/6 全部证据齐备，且 `procedure_discover` 两次真实返回非空、`procedure_use` 真实绑定、文件真实写入并核验、观察真实落库绑定指纹。任一环节靠聊天内容推断均不计。步 7 不计入 PASS 条件。负控：步 4 不得出现任何文件效果；步 2 的 draft 不得在步 5 被误选（候选按词项命中排序，"松柏记录"词项对"云杉归档"0 命中）。

## 4. 风险与边界

- 白名单扩到 active/reinforced 后，`procedure_use` 对 ACTIVE 目标绑定时指纹 UNBOUND → `bind` 允许（`procedure_use_store.py:69`），首次观察绑定指纹（`sqlite_v5.py:6331-6335`）；已有测试 `test_procedure_scope_runtime.py:125-144` 覆盖 draft→eligible→active 的计数路径，ACTIVE 起点的"只绑定不升级"分支需新增断言（§3.1 (a)/§3.2）。
- 词项匹配会让候选变多；页预算/`omitted_oversize` 逻辑不变，`limit≤8`。
- 不改 typed recall 指纹门，因此「memory_standalone 路由下问'我有什么流程'」仍只能靠模型改调 `procedure_discover`（PERSONA 已引导）；这是设计口径，不是缺陷。
- 本分析未拿到 r24/r25 的原始库（`.local-test-evidence/2026-09-07/native079619` 本机不存在），r24 "只有 episode" 依据 CONTRACT/REMAINING 的公开响应审计记录；r8/r9 库为本机只读实查。
