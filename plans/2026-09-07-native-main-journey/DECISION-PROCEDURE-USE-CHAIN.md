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

## 追加（2026-09-08 r14 后）

> 本节由独立子代理在工作树 `worktree-agent-a7db10b24f7c197f6`（基线 Host main `7cec5249`）执行并自审。只改 Host 后端，未改 Memory SDK，未 pin 新 wheel，未运行原生应用与真实模型。

### 1. 问题（native r14，真实 DeepSeek）

记录见 `NATIVE-R14-PROCEDURE-CHAIN.md`。步 1–4 全绿，步 5 FAIL：

1. `procedure_use` 绑定成功，返回体只有 `{procedure_use_id, memory_id, revision, steps, execution_authorized:false}`——**没有回显模型自己刚绑定的两步调用**。
2. 模型随后调 `file_write` 时把 `content` 改了 → `procedure_call_not_bound_step`；但 `sdk_adapters/tools.py` 把 `before_call` 里**所有** `ProcedureUseRejected` 一律映射成同一句 "Procedure use was rejected before execution."，模型无从知道期望的是第几步、什么工具、什么参数。
3. 模型自述"没匹配上，我重新绑定" → `procedure_same_run_changed_use`（同 Run 绑定不可变，设计如此）。
4. 循环至 `react_max_turns_exceeded`；期间 DeepSeek 另发 9 次空参数 `{}` 调用（模型行为，只能靠更短更可执行的纠错文案降低概率）。

根因归类：**契约正确，工效学缺失**。三层拒绝都按设计生效、审计完整、无越权执行；失败点是"模型拿不到照做所需的事实"。

### 2. 决策：契约不动，只改工效学

**不变（明确重申，任何后续 Agent 不得放宽）**

- 同一 Run 内绑定不可变：`procedure_use_store.bind` 仍以 `existing != body` 判定并抛 `procedure_same_run_changed_use`。
- 步调用逐字精确匹配：`reserve` 仍只比 `{tool, arguments_hash}`，仍按序、前一步必须成功。
- 不新增任何执行路径：`before_call` 拒绝仍返回 `EffectExecution(effect=None, …)`，物理动作零发生。
- 全部 `error_code` 字面值不变（测试与语料在断言它们）。
- 操作审计（`operation-audit.db`）调用序列、观察/恢复链、指纹门一律未动。

**改动（Host 后端）**

| # | 文件 | 内容 |
|---|---|---|
| 1 | `backend/deskpet/memory/procedure_guidance.py`（新增） | 唯一的模型可读文案层：`bound_step_calls` / `bind_next_action` / `call_rejection_public_message` / `bind_rejection_next_action`。纯函数、无 I/O、不做任何判定 |
| 2 | `backend/deskpet/memory/procedure_runtime.py` `bind_use` | 返回体新增 `bound_steps:[{ordinal, tool, arguments}]`（`arguments` 为解码后的 JSON 对象，逐字等于绑定值）、`binding_frozen:true`、`next_action` 字符串；`execution_authorized` **保留**（消费方兼容），并由 `next_action` 首句显式说明它不等于"不许执行" |
| 3 | `backend/deskpet/memory/procedure_use_store.py` `bind` | 持久化 body 的每一步增加 `arguments` 字段（**仅供回显**；匹配仍只用 `arguments_hash`），使拒绝文案在重启/重放后仍能逐字回显 |
| 4 | 同上 `bind`/`reserve` | 四个拒绝带上 `detail`：`procedure_same_run_changed_use`（已生效的全部绑定步）、`procedure_call_not_bound_step`（期望的序号/工具/参数 + 实际发出的工具）、`procedure_previous_step_not_successful`（失败的序号/总步数）、`procedure_use_already_complete`（总步数） |
| 5 | `backend/deskpet/memory/procedure_applicability.py` | `ProcedureUseRejected` 增加**关键字参数** `detail`；`str(exc)` 仍是稳定码 |
| 6 | `backend/deskpet/sdk_adapters/tools.py` | 那句唯一的不可操作文案改为 `call_rejection_public_message(error)`；`error_code` 仍是 `str(error)` |
| 7 | `backend/deskpet/sdk_adapters/procedure_use.py` | `_rejection` 在有 `detail` 时用具体文案覆盖静态 guidance（retriable 判定不变）；补 `procedure_same_run_changed_use` 静态兜底；工具描述与 `arguments_json` schema 描述加入"必须逐字重发、同 Run 不可改绑" |

**新公开文案（逐字）**

- 绑定成功 `next_action`：
  `Binding is frozen for this run. \`execution_authorized: false\` only means this record grants no extra permission by itself - you must now issue the bound calls yourself, in order: step 1 \`file_write\`; step 2 \`file_write\`, each with exactly the arguments echoed in bound_steps, copied verbatim (one changed character is rejected). Do not call procedure_use again in this run. After the last bound step this run accepts no further tool call under the binding, so answer the user directly then.`
- `procedure_call_not_bound_step`：
  `The Procedure binding for this run is frozen and cannot be re-bound. Expected next: step 1 of 2, tool \`file_write\`, with exactly these arguments: {"content": "…", "path": "record.txt"}. You sent that tool with different arguments. Re-send the expected call verbatim - calling procedure_use again in this run is rejected.`
- `procedure_same_run_changed_use`：
  `The Procedure binding for this run is immutable and is already set to: step 1 \`file_write\` with arguments {…}; step 2 \`file_write\` with arguments {…}. Issue those bound calls verbatim instead of binding again.`
- `procedure_previous_step_not_successful`：
  `Step 1 of the 2 bound Procedure steps did not succeed, so no later step can run. The Procedure binding for this run is frozen and cannot be re-bound. Report the failure to the user instead of retrying or re-binding.`
- `procedure_use_already_complete`：
  `All 2 bound Procedure steps are already done in this run, so no further tool call is accepted under this binding. The Procedure binding for this run is frozen and cannot be re-bound. Answer the user with the result.`
- 未映射码兜底：
  `Procedure use was rejected before execution (<code>). Do not retry the identical call. The Procedure binding for this run is frozen and cannot be re-bound.`

### 3. 披露口径（为什么回显参数是安全的）

回显的 `arguments` 是**模型本 Run 自己通过 `steps[].arguments_json` 送进来的字节**，不是记忆内容、不是他人主体数据、不是本 Run 尚未产生的任何事实；`_use` 仍校验 `subject == principal.actor_id`，`bind` 的 `existing` 只可能是同一 `sdk_run_id` 的先前绑定。本决策 §1.1 里带披露不变式的是**发现面** `procedure_discover`（读取前后重解析 disclosure），绑定回显不经过任何记忆读取，故不适用该不变式。结论：可以逐字回显。超过 1200 字符的参数不回显，改为"从 procedure_use 结果逐字复制"，理由是文案可读性与回执体积，不是披露。

### 4. 独立复核裁决

以严格复核者身份重读整份 diff，对照四条不变量：

| 检查项 | 结论 |
|---|---|
| 绑定不可变 | **通过**。`existing != body` 判定未动；新增的 `arguments` 字段在比较两侧对称出现 |
| 逐字精确匹配 | **通过**。`reserve` 的 `signature != {tool, arguments_hash}` 一字未改；`arguments` 只被文案层读，不参与任何判定 |
| 无新执行路径 | **通过**。`tools.py` 只替换 `public_message` 实参；仍是 `ToolResult.rejected` + `effect=None` |
| 无秘密披露 | **通过**，见 §3 |
| 审计不变 | **通过**。`operation_audit.invoke` 调用点、参数、顺序未动；`procedure_observation_*` 全链未动 |
| 稳定码不变 | **通过**。`str(exc)` 仍是原码；`_rejection` 只覆盖 `next_action`/`public_message`，不改 `retriable`/`replan_required` |
| 消费方 | **通过**。`execution_authorized` 的唯一生产代码消费者 `execution/primary_dependencies.py:331` 只校验 `procedure_discover` 的返回形（索引表只含 `task_scope_search`/`context_page_in`/`procedure_discover`），`procedure_use` 返回体不被校验，扩字段安全 |

复核中自查并已修的一点：`bind_next_action` 原稿没有说明"最后一步之后本 Run 不再接受任何工具调用"，模型会照旧计划一次核验读取再撞 `procedure_use_already_complete`；已补入该句。

**裁决：ACCEPT。**

### 5. 复核发现但本轮**不做**的事（r15 前需另行裁决）

1. **同 Run 无法在流程之后做核验读取。** `before_call` 对绑定 Run 内的**每一个**非控制工具都要求它是下一个绑定步，因此步 5 用户要求的"做完把两个文件读出来核对"在契约下不可能完成——两步做完后 `file_read` 必然 `procedure_use_already_complete`。本轮只在文案里如实告知，没有改契约。**这是 r15 步 5 的下一个阻塞项**，需要产品决定（例如：只读工具不计入绑定；或绑定完成后自动解除；或把核验写进流程步骤）。
2. **含拒绝调用的 Run 无法登记终态组。** 实测：一旦 Run 里出现"绑定前拒绝"的工具调用，该 tool 消息没有对应的 `primary_effect_identities` 行 → `primary_message_v2.representable` 为假 → `record_terminal_observation` 静默降级为 `primary-message-v1` → `registrations_for_run` 抛 `terminal_multiple_items_not_representable`，于是**该 Run 的 Procedure 观察与短索引全部拿不到**。这属于证据/因果层，不在本次工效学范围，但会让"模型第一次犯错、随后自行纠正成功"的 Run 拿不到成功计数。已在新测试里绕开（改为直接读 `procedure_uses` 表断言）。
3. **回执 JSON 直出聊天区**（既有 F02）：`bound_steps` 会把绑定参数（可能是整段文件内容）原样渲染进聊天。属既有 followup，本轮不处理。
4. **持久体积**：每步最多 16 KiB × 16 步，`procedure_uses` 单行最多多出约 256 KiB。输入侧早已有同样上限，判定可接受，未加新上限。

### 6. 控制（本工作树）

- 新增 `backend/tests/memory/test_procedure_binding_ergonomics.py`：11 项文案单测 + 1 项真实运行时集成测试，后者用真 Provider 复刻 r14 序列（绑定 → 改参数发 `file_write` → 重绑定 → 依回执逐字重放两步），断言两次拒绝仍是原码、文案可操作、两个真实文件写成、`procedure_uses` 只有 1 行、预留恰好 1/2 两步。
- 扩充 `backend/tests/sdk_adapters/test_procedure_use_rejection_surface.py`（+3 项）。
- 定向套件（15 个既有 procedure 文件 + 新文件）：末次 **88 passed / 1 skipped / 0 failed**。前两轮 `test_procedure_scope_runtime.py::test_three_real_scopes_whole_groups_qualify_and_replay_does_not_increment` 以 `asyncio.wait_for(…, 30)` 超时（`CancelledError`）失败，改动前的同一基线以完全相同方式失败，末轮同一命令通过——判定为本机计时不稳定项，非本次改动引入。
- `sdk_adapters/tools.py` 的 17 个导入方文件：**231 passed / 8 failed**，8 项经 stash 回基线逐一复验全部为既有红（`test_foreground_runtime.py` 5 项、`test_provider_runtime_refresh.py` 2 项、`test_primary_create_new_runtime.py` 1 项，均为计时类 `CancelledError`）。

## 追加二（2026-09-08，r15 前两项待决事项的裁决与实现）

> 本节由独立子代理在同一工作树 `worktree-agent-a7db10b24f7c197f6`（基线本分支 `b1ec2435`）执行并自审。用户已授权由本代理直接裁决技术取舍，不再回问。只改 Host 后端，未改 Memory SDK / Harness SDK，未 pin 新 wheel，未运行原生应用与真实模型。

上一节 §5 留下两项「r15 前需产品裁决」的阻塞项。本节逐项给出**考虑过的方案、契约依据、最终决定与复核结论**。

---

### A. 阻塞项 1：绑定完成后同 Run 无法做核验读取

#### A.1 事实

`procedure_runtime.before_call` 对绑定 Run 内**每一个**非控制工具（控制白名单只有 `procedure_use` / `procedure_discover` / `context_route` / `task_scope_search` / `task_scope_update` / `prospective_ack`）都要求它是下一个绑定步。两步做完后 `procedure_use_store.reserve` 走到 `ordinal > len(steps)` 分支，必然抛 `procedure_use_already_complete`。原生 journey 步 5 的用户原话是「新建一个本地任务…按松柏记录流程做一遍，**做完把两个文件读出来核对内容一致**」——后半句在旧契约下**不可能完成**。

#### A.2 考虑过的方案

| # | 方案 | 评估 |
|---|---|---|
| 1 | 只读工具不计入绑定（按 effect class 豁免） | 需要一份「只读」白名单或依赖 `ToolEffectClass`；豁免面比问题面大得多，且绑定进行**中途**插入任意只读调用会让「按序逐字执行」的语义变模糊。否 |
| 2 | 核验写进流程步骤本身 | 把 `read_file` 写成第 3、4 步。但用户保存的流程只有两步；为了绕过 Host 限制去改用户的记忆内容，是把产品缺陷转嫁给记忆真实性。否 |
| 3 | 核验必须放到下一轮（保持同 Run 屏障，只改文案与 journey 记录） | 满足契约，但把一个自然的单轮任务硬拆成两轮，且模型在同一轮里会先撞一次拒绝才学会。是「不改代码」的保底选项 |
| 4 | **绑定的全部步落定 `succeeded` 后，本 Run 恢复普通工具调用**（推荐项） | 见下 |

#### A.3 契约核对（方案 4 是否越界）

- **S3 Task 3（HM-AC-5）** 的原文只约束记忆侧：`explicit「以后都这样」可直接 active（只代表记忆状态，不授予执行权限）`、`Procedure 使用前检查 tool/environment/version applicability`、观察晋升的计数规则。**全文没有一句要求「绑定 Run 内禁止其它工具调用」**——同 Run 屏障是 Host 自己的实现选择，不是 SDK 契约条款。
- 屏障真正保护的是**观察归因**：`procedure_runtime._observed_outcome` 只读 `procedure_use_reservations`，`_step_sources` 只按 `reservation["call_id"]` 去 group 里取登记；未预留的调用**根本进不了归因集合**。因此「全部步成功之后再发生的普通调用」在数学上不可能改变 SUCCESS/FAILURE 判定——屏障对这一段是零收益。
- 四条不变量逐条对照：绑定同 Run 不可变（`bind` 的 `existing != body` 未动，改绑仍 `procedure_same_run_changed_use`）；逐字精确匹配（`reserve` 的 `signature != {tool, arguments_hash}` 未动）；按序执行（`ordinal = len(reservations)+1` 未动，且解锁分支排在 `procedure_previous_step_not_successful` **之后**，任一步失败仍然到不了解锁）；无绑定不得执行（`use is None` 直接放行的旧语义未动）；审计不变（`operation_audit.invoke` 调用点、参数、顺序一字未改）。

#### A.4 决定

**采纳方案 4。** `procedure_use_store.reserve` 在「全部绑定步已预留 **且** 最后一步的 effect 落定 `succeeded`」时不再抛拒绝，而是 `return None`：本次调用不预留任何步、不归属任何 Procedure 步，交给普通权限链处理，审计照常。

连带处置：

1. `procedure_use_already_complete` **退役**。它在 `reserve` 里已无可达 raise 点，故同时删掉 `procedure_guidance._DETAILED` 与 `sdk_adapters/procedure_use.py::_GUIDANCE` 中的对应条目。该码此前只在本工作树的上一提交里被引入文案层，未进入任何语料 gold 或外部消费者。
2. `bind_next_action` 末句由「最后一步之后本 Run 不再接受任何工具调用，所以直接回答用户」改为「最后一步成功后绑定即完成，普通工具调用恢复可用，可以先核验再回答」——上一节 §4 那条自查补充在本轮被这条决定推翻，记录在案。

**明确不放宽的**：绑定进行**中途**（还有步没做完）的任何非绑定调用仍然 `procedure_call_not_bound_step`；任一步失败仍然 `procedure_previous_step_not_successful`；同 Run 改绑仍然 `procedure_same_run_changed_use`。完成后重发一个与绑定步字节相同的调用会被当作**普通调用**放行——它不预留步、不进归因，与模型在绑定之前自己发同一个调用没有区别，故不额外设限。

---

### B. 阻塞项 2：含绑定前拒绝的 Run 丢失终态组

#### B.1 复现与根因（先测后改）

新增 `tests/memory/test_primary_tool_causality.py` 的定向控制与 `test_procedure_binding_ergonomics.py::test_a_run_with_a_denied_call_still_registers_its_group_and_observation`。实测链路（r14 序列，22 条 message、10 条 tool message）：

1. Host `ProductEffectExecutor.execute` 在 `before_call` 抛 `ProcedureUseRejected` 时**直接短路**返回 `EffectExecution(effect=None, …)`，从不调用 `super().execute()`；而 SDK 的 `EffectExecutor.execute` 是在自己的第一行就 `record("requested")`。于是这次调用**连一条 kind=effect 的审计 head 都没有**，`primary_effect_identities` 也没有行（`record_effect` 在 registry `invoke` 里，更靠后）。
2. `read_tool_causal_sources` 在 `_require(head is not None, "effect_missing")` 抛 `primary_tool_effect_missing` → `foreground_runtime` 兜底 `tool_sources = None`。
3. `record_terminal_observation` 的 `representable(messages, None)` 为假 → 终态降级 `primary-message-v1`。
4. `registrations_for_run` 走 `elif not isinstance(messages, list) or len(messages) != 2` → 抛 `terminal_multiple_items_not_representable`。**该 Run 的 Procedure 观察与短索引全部拿不到**——模型第一次犯错、随后照回执自行纠正成功的 Run，成功计数为 0。

这不是 Procedure 专属缺陷：effect gate 拒绝（`workspace_binding_receipt_superseded`）、前台 admission 拒绝、SDK 自己的 `authorization_denied` 都产出同形的 `effect=None`。

#### B.2 考虑过的方案

| # | 方案 | 评估 |
|---|---|---|
| 1 | 让拒绝也走完 SDK 的审计与结算（把检查挪进 registry `invoke`，使 effect 以 `rejected` 落定） | 能让下游全链无感。但 `reserve` 必须在 `mark_effect_handed_off` **之前**（`procedure_use_store.reserve` 的注释即设计冻结：「Before physical SDK dispatch」），挪进去会把预留排到 HANDED_OFF 之后，改变崩溃语义；且拒绝会因此占用一条 effect 账本行与一次 evidence commit。改动面覆盖每一次受门工具调用。否 |
| 2 | 放宽 v2 可表达性，把被拒条目**登记为**没有 attestation 的 tool 证据 | 实测直接撞 SDK 协议硬约束：`ConversationEvidenceMetadata.__post_init__` 对 `role is TOOL` 强制要求 `ConversationToolCausalLink`（`runtime/evidence_protocol.py:613`），而该 link 必须带 `terminal_receipt_id/hash`。为一次从未发生的调用铸造 terminal receipt 属于伪造。除非改 SDK，否则死路。否 |
| 3 | **被拒条目留在归档里，但不作为会话组的证据条目**（推荐项） | 见下 |

#### B.3 契约核对（方案 3 的依据）

- **S1（HM-AC-4/6）**：`冻结 Host-authenticated ConversationEvidenceMetadata：…tool causal link…；模型不能构造或扩大，缺失/非法 metadata 的 evidence 永久保存但不入短时域。` ——规则是**逐条目**的：缺 causal metadata 的条目保存但不索引；**没有一句说整组作废**。今天的行为（整组降级、整组丢失）比契约要求更激进，而且丢的是别的、完全合法的条目。
- **S5 Task 1（HM-AC-6）**：`按 user turn 开始、assistant terminal、tool call/result 配对形成 group…不截断 tool causality。` ——不截断说的是「不要把一个组切一半」。被拒条目没有 effect、没有 terminal receipt，本来就不构成一条 tool causality；把整组丢掉才是真正的截断。
- **归属定性（本裁决的核心论据）**：绑定前拒绝的那条 tool 消息，文本是 **Host 的门（Procedure/effect gate/admission）写的**，不是工具产出的。把它登记成 `EvidenceProvenance.TRUSTED_TOOL` + `EvidenceActorRole.TOOL` 是**错误描述**。它属于操作审计与 Procedure 账本（两处都已完整记录），以及终态 `messages` 的完整归档；它不属于会话因果组。
- **物理删除禁令**：整条 message 仍逐字留在终态 envelope 的 `messages` 里（终态是经认证的 Host 证据、带 receipt、hash 绑定），没有任何原始数据被删除或改写。

#### B.4 决定

**采纳方案 3。** 具体改动（全部在 Host 后端）：

| # | 文件 | 内容 |
|---|---|---|
| 1 | `memory/primary_message_v2.py` 新增 `effectless_denial(content)` | 判定一条 tool 消息是否是「绑定前拒绝」：内容必须恰好是 `{outcome, value, error_code, public_message}` 四键、`outcome == "rejected"`、`value is None`、`error_code` 非空。这是**收紧**，不是放宽 |
| 2 | 同上 `representable` | `facts` 由「与 tool 条目一一对应」改为「tool 条目的**有序子集**」；**每一条没有 fact 的 tool 条目必须通过 `effectless_denial`**。一条声称 succeeded 却没有 effect 证明的 tool 条目仍然不可表达 |
| 3 | 同上 新增 `item_ordinals(messages, facts)` | 唯一一处「哪些 transcript 序号会成为会话证据条目」的定义；`_pairs` 与 `conversation_registration` 共用，杜绝两边规则漂移 |
| 4 | 同上 `_pairs` | 跳过被拒条目；其余条目的 `transcript_ordinal`、`terminal_json_pointer`、`evidence_id`（`uuid5(contract:run:ordinal)`）全部仍用**真实 transcript 序号**，故既有数据字节不变 |
| 5 | 同上 `tool_link(envelope, group_ordinals=None)` | fact 里的 `parent_item_ordinal` 是 transcript 序号，而 `ConversationToolCausalLink` 需要**组内**序号（SDK 强制 `parent_item_ordinal < item_ordinal`）。仅当组内丢过条目时两者才不同；parent 永远是 assistant 消息、永远在组内，查表不会落空 |
| 6 | `memory/conversation_registration.py` `_group_tx` | 用 `item_ordinals` 求出每个组条目的真实 transcript 序号，manifest 的 `role`、`tool_scope_sources` 的 `scope_map` 一律按真实序号查；`item_ordinal` / `group_item_count` 仍是组内序号（SDK 约束 `item_ordinal <= group_item_count`）。全部落定的组结果与改动前完全一致 |
| 7 | `memory/primary_tool_causality.py` `read_tool_causal_sources` | `head is None` 不再直接抛，而是要求 transcript 同位条目确实是 `role=tool` + 同 `call_id` + 同 `name` + `effectless_denial`，否则抛新码 `primary_tool_unsettled_call_not_denied`；占位保持后续 `item_ordinal` 精确；整体 transcript 比对改为「扣除被拒位置后逐条相等」 |

**为什么「没有 audit head」就等于「没有物理动作」**：SDK `EffectExecutor.execute` 的第一条语句是 `record("requested")`，在 `_execute_audited`、`prepare_effect`、`mark_effect_handed_off` 之前。没有 head ⇒ `execute` 从未进入 ⇒ 没有 effect、没有派发。这是本方案全部安全性的支点，已写进代码注释。

**本轮不做**：带 head 但没有落定 effect 的拒绝（SDK 自己的 `authorization_denied` 会先 `prepare_effect` 再拒，effect 停在 PREPARED），仍然走 `effect_not_bound_or_settled` → 整组降级。当前 Host 的授权端恒 ALLOW，构造不出真实用例，故不臆造修复。记为后续 followup。

---

### C. 独立复核裁决

以严格复核者身份重读整份 diff：

| 检查项 | 结论 |
|---|---|
| 绑定同 Run 不可变 | **通过**。`bind` 的 `existing != body` 与 `procedure_same_run_changed_use` 一字未动 |
| 步调用逐字精确匹配 | **通过**。`reserve` 的 `signature != {tool, arguments_hash}` 一字未动 |
| 按序执行 | **通过**。解锁分支位于 `procedure_previous_step_not_successful` 之后，失败前缀仍然阻断；`ordinal` 计算未动 |
| 无绑定不得执行 | **通过**。解锁只发生在**已有绑定且全部步成功**之后，`use is None` 的旧路径未动 |
| 审计不变 | **通过**。`operation_audit.invoke` 的调用点、参数与顺序未动；`procedure_observation_*` 全链未动 |
| 观察归因不被稀释 | **通过**。`_observed_outcome` / `_step_sources` 只认 `procedure_use_reservations`；解锁后的调用没有预留，进不了 `verified`。新测试直接断言 `independent_successes == 1` |
| 既有 v2/v3 数据字节不变 | **通过**。全部落定的组：`item_ordinals` 恒等于 `1..n`，`_pairs` 一条不跳，`group_ordinals` 是恒等映射，`representable` 的子集规则退化为原来的相等规则。`test_primary_tool_message_ingestion` / `test_primary_short_ingestion` / `test_procedure_scope_sources` 全绿即为证 |
| 不放宽可表达性 | **通过**。没有 fact 的 tool 条目必须逐字符合 `effectless_denial`；`test_a_denied_tool_item_is_representable_but_a_success_without_a_fact_is_not` 与 `test_a_call_with_no_audit_head_must_be_a_denial_or_the_read_refuses` 是两条负控 |
| 无原始数据丢失 | **通过**。被拒 message 仍逐字在终态 `messages` 内，终态 envelope/receipt/hash 均未改 |
| 无伪造 | **通过**。方案 2（为未发生的调用铸造 terminal receipt）被明确否决并记录 |

复核中自查并已修的两点：①`effectless_denial` 初稿的 docstring 写成「仍登记为普通会话证据」，与最终采纳的方案 3 相反，已改；②`_group_tx` 初稿对 v1 路径也套用 `item_ordinals` 并复用 `primary_message_v2_source_mismatch` 作错误名，语义误导，已改为只在 v2 分支求值与校验。

**裁决：ACCEPT。**

---

### D. 控制（本工作树）

- `tests/memory/test_procedure_binding_ergonomics.py`：11 → 14 项。新增 `test_after_the_last_bound_step_succeeds_ordinary_calls_verify_the_result`（真实运行时复刻 journey 步 5：激活 `write_file` 与 `read_file` → 绑定两步 → 逐字执行 → **两次 `read_file` 核对** → 关闭 Scope；断言全 Run 零拒绝、读回内容一致、预留恰好 1/2 两步、观察 `independent_successes == 1`）与 `test_a_run_with_a_denied_call_still_registers_its_group_and_observation`（r14 全序列；断言终态是 v2/v3、facts 比 tool 条目少 1、组条目比 message 少 1、组内 tool 条目全部有 causal link、短索引与观察都跑通）。两项旧断言随决定 A 更新。
- `tests/memory/test_primary_tool_causality.py`：2 → 5 项。新增 `effectless_denial` 的 7 组纯函数断言、`representable` 的正负控、`item_ordinals` 的恒等退化控，以及负控 `test_a_call_with_no_audit_head_must_be_a_denial_or_the_read_refuses`（真实 Run，抽掉最后一条 effect head，断言 `primary_tool_unsettled_call_not_denied`，证明「藏掉一条已落定 effect 的 head」不能把成功回执洗成未证条目）。
- `tests/memory/test_procedure_recovery_runtime.py::session` 增加 `extra_registrations=` 关键字（fixture-only）。
- 定向 procedure 套件（13 文件）：**81 passed / 0 failed**（末轮；中间一轮 `test_procedure_scope_runtime::test_three_real_scopes_whole_groups_qualify_and_replay_does_not_increment` 失败一次，随后单文件连跑 12 次全绿、整批复跑亦全绿，与上一节记录的同一项间歇计时红一致）。
- 观察/终态可表达性套件（`test_primary_tool_causality` / `test_primary_tool_message_ingestion` / `test_primary_short_ingestion` / `test_primary_effect_sources_v47` / `test_primary_visibility` / `test_primary_read_api` / `test_selected_short_runtime` / `test_selected_short_sources`）：**99 passed / 4 failed**，4 项已 stash 回基线逐一复验为既有红（`test_primary_effect_sources_v47` 3 项迁移测试、`test_primary_visibility` 1 项遗忘测试）。
- 改动文件的导入方（`tests/execution/` 10 个文件）：末轮 **49 passed / 1 failed**，唯一红 `test_primary_history_outbound::…[sent_unknown]` 已 stash 回基线复验为既有红。另有 `test_primary_foreground_runtime::test_primary_none_routes_to_exact_task_and_writes_real_file[True]` 在该批次中**间歇**失败（`close()` 里 `_maintain_lease` 的 `asyncio.sleep` `CancelledError`，属既有计时红家族）：单文件跑 16/16 绿；与 `test_current_tool_pages.py` 两文件同跑时**基线 3/3 失败、带改动 1 次通过**；十文件同跑基线 3 次全绿、带改动 4 失 2 绿。判定为既有计时脆弱项，被本次修复正当增加的终态证据写入量（supersede 用例现在会走 v2/v3 而不再降级 v1）放大，非语义回归。已如实记录，未掩盖。
