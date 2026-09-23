# ARP-EXEC-1.1.1 主体施工 RP-B：Context 装填、真实计量、Session 分区索引、检索与自动召回

日期：2026-09-23。分支 `arp-1.1.1`（worktree `simple_harness-arp`），上一片 RP-A 提交 `7211546d`。本记录只写本机核实过的事实与本片做出的实现决定；没做的明确写"未做"。

## 1. 交付物（SDK `src/simple_harness/agents/arp/`）

| 模块 | 职责 | 规格条款 |
|---|---|---|
| `meter.py` | `MeterBinding`（认证计数器 + `ModelLimits` + 认证回执 + P 读取器）、`NativeMeterAdapter.measure` 对最终 wire 请求计量并产出 `TokenReceipt`；`UpperBoundTokenizer` 或未声明 `count_mode` 的计数器一律 `GENERIC_TOKEN_BOUND_UNCERTIFIED` 拒绝；非文本内容块 `MEDIA_UNSUPPORTED`；O 超部署上限 `INVALID_OUTPUT_RESERVE` | §4、INTERFACES §5 |
| `context/groups.py` | `capture()` 从 Journal + `execution_effects` 账本产出 `ProtocolGroupSnapshot`：USER_ANCHOR / CLOSED_TOOL（call_id 与 tool_result 一一配对才闭合）/ OPEN_TAIL（必需）/ TERMINAL_ANSWER / HISTORY_MESSAGE；`TurnCoverage`；可选组枚举上限 8192，超出记 `omitted_optional_groups` | §4.1 步 1、6、7；CONTEXT-SEARCH C1 |
| `partition.py` | 每 Session 一个 `index.sqlite3`（分区 DDL v2 原样应用），身份行校验 session/agent/root/marker；`FileGuard`（`<root>/locks/<session>.lock` 的 flock，不可重入，忙则 `FILE_BUSY`）；index generation 按 view/chunker/embedding 指纹复用或新建；`materialize_group_locked` 一批一个 `commit_seq`，同组异 source `GROUP_HASH_CONFLICT`，向量必须带真实 embedding 回执 | §5.1、§6、J3 |
| `search.py` | `SessionSearchService`：冻结 `IndexSnapshot`（upper_commit、highwater、排除组、授权读集）；SCANNING 页空 items + 进度 + cursor，扫完后各路 top-K → 加权 RRF（`rules.rrf`，稳定五元组并列）→ 同 record 相邻 span 合并 → 固定排名；RESULTS 页只追加；cursor 为随机 256 位 token，库内只存 hash 与不可变页，重送同页不推进；过期/换代/换权分别 `CURSOR_EXPIRED / CURSOR_STALE / CURSOR_SCOPE_MISMATCH`；`read_history` 按 seq 升序、UTF-8 码点边界切片、按精确 canonical 字节数收缩、容不下即 `ITEM_TOO_LARGE` | §5.2、§5.3、CONTEXT-SEARCH C3/C5/C6 |
| `context/recall.py` | `ContextRecallCoordinator`：`build_request`（context-query-v1 三槽 query）、`start`（C0 冻结请求 + PREPARING checkpoint，同键同体重放、异体 `RECALL_BINDING_INVALID`）、`resume`（PREPARING→[WAITING_EMBEDDING]→SCANNING→FETCHING_RESULTS→READY，或 SKIPPED/BLOCKED/STALE）；query embedding 按 `recall_key/query/<fingerprint>` 只调一次；聚合器 `aggregate_context_recall` 校验页链、身份、rank 连续与总数 | CONTEXT-RECALL §1–§7、§9 |
| `indexing.py` | `SessionIndexCoordinator`：闭组入 INDEX 作业（语义键 = 代/组/source/view/chunker/embedding）；worker 重读 payload 指定记录并核 source hash，按真实 token 数切块（≤16KiB、码点边界），锁外 embedding（调用键 `job_id/1/input_hash`，已持久成功结果复用），FileGuard 下一批物化，中央 DONE + `RuntimeJobChanged`，首次物化发布 `arp_index_publications` + `RuntimeIndexGenerationPublished` | §6、J1/J2 |
| `context/composer.py` | `ArpContextPort.load` 只返回必需内容（指令 + 当前锚点 + 未闭合尾）；`ArpProviderWire.prepare_request`（原 request_preparer）运行装填器：快照 → 先按无召回分配得出 protected 后缀 → 召回 start 到终态 → `rules.allocate`（A–E 固定、F、G）→ 渲染 → 从账本恢复 tool_calls → 真实计量 → 最多 256 次收缩 → 冻结 `ContextManifest` v2 + `RuntimeContextPrepared`；同请求键重放重渲染并核对 hash，不一致 `REQUEST_HASH_MISMATCH`；任何装填错误映射为不可重试的 `ArpContextRejected`（`ProviderRequestRejectedError` 子类），不让 Turn 挂起 | §3.2、§4、§4.1 步 2–5、8；INTERFACES §1 |
| `store.py` 新增 | `put_context_locked / read_context_by_request_key`、`put_context_recall_locked / cas_context_recall_locked / get_context_recall_exact / list_pending_recalls`、`publish_index_generation_locked / active_index_publication`、`append_original_receipt_locked / read_original_receipt`（把 embedding 调用回执、时钟观察写成 `run_events` 原始事件，不建独立表） | INTERFACES §4、§6 |
| `runtime.py` / `ports.py` | 工厂增加 `MeterBinding` 必填校验、embedding 端口与部署 pin、换用 `ArpContextPort` / `ArpProviderWire`（通过 `build_agent_runtime` 新增的两个可选工厂钩子，legacy 装配对象不变）；`ArpRuntime.tick()` 跑到期 INDEX 作业并续跑非终态召回；`shutdown` 关闭分区连接 | BW10、J7 |

新增测试：`tests/agents/arp/test_arp_context_prepare.py`（7 项）、`test_arp_index_recall.py`（7 项）。夹具 `arp_fixture.py` 增加 `ExactWordTokenizer`（脚本 Provider 的真实计数规则：一个空白分隔词一个 token，声明 EXACT 并带认证回执）与缩小到测试部署窗口的 policy。

## 2. 本片实现决定（与规格文字有出入或规格未定处）

1. **装填发生在 prepare_request，而不是 ContextPort.load。** 原因：ReAct 循环在 `context.load` 之前才预留 provider 请求序号，而 ContextManifest / ContextRecallRequest 需要 `original_request_key` 与序号才能冻结。`load` 只给必需内容，wire 层的 `prepare_request` 得到完整请求身份后再装填，返回的 wire 请求就是实际发送的请求，manifest 绑定其 hash。这与 INTERFACES §1"原 request_preparer 在冻结 LLM 请求之前调用 NativeContextComposer.prepare"一致。
2. **召回分页在 prepare 内联推进到终态，不是交给 tick 异步。** 原因：ContextPort / request_preparer 是同步调用，SQLite 连接单线程，既有 `_RecallAdapter` 也是同样的内联先例。崩溃恢复语义保留：每页一次 exec CAS checkpoint，`tick()`/`resume_pending` 从 checkpoint 的 cursor 续跑同一 query（测试 `test_recall_resumes_the_same_frozen_query_after_a_crash_between_pages` 覆盖）。规格要求的"不占 LLM 物理槽位、由 tick 推进"仍是后续工作（见 §5）。
3. **扫描完成的那一页仍是 SCANNING 页**（空 items、有 next_cursor），排名在该步固定并持久，下一页才是 RESULTS。RESULTS 页不再更新 `search_queries` 行（触发器禁止 RESULTS 后再改），偏移只在 cursor 位置里。
4. **计量器：** DeepSeek 计量器的 tokenizer 文件本机缺失（见 00 盘点 §3），本片不启用该部署。`MeterBinding` 只接受声明 `count_mode` 且带认证回执的计数器，`ModelLimits` 绑定本 SDK 渲染器 / 序列化器 hash；P 通过 `prior_reserve(run_id)` 读取，缺省 0 且无基准回执（`requires_prior_output_reserve=True` 的部署没有读取器时拒绝）。测试用 `ExactWordTokenizer`：对脚本 Provider 而言词数就是精确 wire 计数，不是"用固定 token 值替代计量"。
5. **embedding 调用回执**写成 `run_events`（kind `arp.embedding_call.v1`，body 为 `EmbeddingCallReceipt` + 实际向量输出），同调用键幂等、异体冲突；query 向量从该回执重读，重启不再调用。真实费用账本（METERED）本片未接。
6. **INDEX 作业在 prepare 内 `process_due()` 同步执行**（含 embedding 调用）。HashEmbedder 下无感；真实 embedding 应由 tick worker 锁外卸载，本片未做。
7. **短中文 query（折叠后不足 3 字）**用子串命中代替 trigram 路（规格"短中文用有界 substring scan"）；words 路按 NFKC + casefold 的 `\w+` 词元。
8. **HistoryRead 的 max_bytes 按精确 canonical 页字节数收缩**：先丢尾片，再对单片按码点减半，仍容不下即 `ITEM_TOO_LARGE`。实测一片带完整元数据与 cursor 约 1070 字节，因此 `max_bytes=1024` 是诚实拒绝，不是缺陷。
9. **Manifest 中 B/C/D 段为空**（STANDALONE_CHAT 由创建模式判 N/A）；`registry_epoch=0`、`catalogue_witness_refs=[]`、`skill_refs=[]` 留给 RP-C；`owner_contract_ref` 取激活 profile 的 owner-mode pin；`authority_refs` 取 policy 的激活 authority pin。
10. **默认 ON：** `build_arp_runtime` 装配出的运行时全部走原生装填 / 计量 / 分区索引 / 自动召回，没有开关。legacy `build_agent_runtime` 行为不变（两个工厂钩子缺省即原对象）。

## 3. 测试与回归

- ARP 定向：`uv run --frozen python -m pytest tests/agents/arp -q` → **256 passed**（RP-A 236 + 本片 14 + 核验后补 6：崩溃窗口 2×2 参数化、C0 后重入、毒作业边界、缩减计数）。
- legacy `tests/agents`（排除 arp）：16 failed / 172 passed / 6 skipped，与施工前基线完全一致（失败均为本机缺 `tiktoken`）；核验修复后再跑一次，结果相同。
- `tests/execution`：本分支 17 failed / 144 passed；与基线提交 `4a4e07fd` 同环境对比结果见 §3.1；核验修复后再跑一次，仍 17 / 144。
- 召回验收形状（CONTEXT-RECALL §8 的窄形式）：目标在早期 Turn、窗口缩小到 72 词使近期后缀装不下它、扫描页 4 行需多页、必需/受保护组被排除、聚合 READY 后才进装填器、发送给 Provider 的请求里出现 `<recalled_history …>` 框住的暗号；词法与 HashEmbedder 混合两种模式各一次。

### 3.1 execution 套件基线对比

用同一 `.venv` 解释器、`PYTHONPATH` 指向基线 `4a4e07fd` 的临时 worktree 跑 `tests/execution`：基线 17 failed / 144 passed，本分支 17 failed / 144 passed，两边 FAILED 的 nodeid 集合逐项相同（`comm` 比对无差异）。结论：本片未引入 execution 套件回归；这 17 项（`test_stage_audit_schema` 等）是本机既有失败，不属本分支。

## 4. 独立核验

一轮，只读审阅（本片用的是 fable；**2026-09-23 起用户规定子代理不得用 fable 系列，默认 opus 5.5，且主体开发期间只评阻断级问题**，后续切片照此执行）。审阅报 2 条阻断、4 条重要，全部在本片处置：

| # | 级别 | 审阅发现（大白话） | 处置 |
|---|---|---|---|
| 1 | 阻断 | 同一请求在 C0 之后崩溃再重入，第二次装填会因"时钟回执同键异体"直接把 Turn 判死；召回请求的时间戳每次不同，重送也做不到"同键返回原行" | 装填器先查该请求键是否已冻结召回请求：已有则直接续跑（`resume`），不再重建请求、不再写第二份时钟回执；时钟回执键改为按 tick（含观测毫秒）；`start` 对同键异体改为报 `RECALL_BINDING_INVALID`。新增测试：C0 后崩溃 → 用同一请求再次装填 → 一行召回、READY、manifest 绑定该行，第三次重送 byte 级相同 |
| 2 | 阻断 | 首页之后崩溃（分区已建查询和首个 cursor、中央还没记 cursor）恢复时报 `CURSOR_UNKNOWN`，行永远停在 SCANNING；由 tick 恢复时 expected_groups 传 0 冻结了错误快照；聚合失败等异常不落终态；`tick()` 把所有错误静默吞掉 | 内部 CONTEXT_RECALL 查询的首个 cursor 改为可再推导（由 query_id + owner scope + 代派生），已存在的查询直接续跑；expected_groups 不再是调用参数，改由协调器按请求冻结的 highwater 重新捕获协议组并核对 group-set hash（不一致 → STALE）；`resume` 内除 `FILE_BUSY` 外任何 ArpError 都 CAS 成 BLOCKED/STALE 并写 `recall_block` 审计回执；`tick()` 只吞已记录的终态与 FILE_BUSY，其余抛出。崩溃恢复测试参数化为 首页后/第二页后 × 词法/混合，混合模式断言 query embedding 仍只调一次 |
| 3 | 重要 | 装填时内联跑全库 INDEX 作业，别的 Session 的毒作业异常会把当前 Turn 判死 | `process_due(session_id=)` 只领本 Session 的作业；每个作业独立错误边界：ArpError → 该作业 BLOCKED（reason 为错误码）、`FILE_BUSY` → 归还租约、其他异常 → BLOCKED（`INTERNAL:<类型>`）；JobResult 正文另存为 `job_result` 回执便于读回原因。新增毒作业测试：作业 BLOCKED、Turn 照常 COMMITTED |
| 4 | 重要 | 最终计量溢出触发丢弃 G 组后，`complete_turn_ids` 被清空，manifest 报 N=0 却标完整 | 改为只剔除被丢组所属 Turn；单元测试覆盖 |
| 5 | 重要 | embedding 回执把 elapsed=0、cost=0 写成事实，且调用前无登记，调用中崩溃会再打一次 | 抽出 `embedding_call.py`：调用前先写 `embedding_intent` 回执，重启发现有意图无回执记为 UNKNOWN 不再重打；索引作业按 attempt 序号换键重试（≤3），query 侧 UNKNOWN 走降级/BLOCKED；elapsed 实测；pricing 按端口声明（未声明 METERED 即合同定义的 NO_PROVIDER_CHARGE，cost=0 是合同语义），input_tokens 只在端口报告时填，否则 null |
| 6 | 重要 | "真实计量"目前只有测试计数器能绑定，生产 DeepSeek 估算器没有 `count_mode` | 本片不改生产估算器（它是估算器，不能自称 EXACT，且本机 tokenizer 文件缺失）。明确记入 §5 未做：需要给 DeepSeek 部署提供带认证回执的计数器后 `build_arp_runtime` 才能在真实部署上装配 |

处置后 ARP 定向 256 passed；legacy 两套结果与基线相同。

## 5. 未做 / 下一片

- 模型侧 `session_history.search / read` 工具与 Host 管理检索 verbs 接到 `SessionSearchService`（当前只有内部 CONTEXT_RECALL purpose 与 `read_history` 服务方法）。
- tick worker 锁外卸载真实 embedding；召回 Progress 交 tick 推进而不占 prepare 时间。
- `tools/delegate.py` 子 Agent 创建改走 `NativeCreationService`（BW01 尾项）。
- cursor / query snapshot TTL 清理、分区磁盘限额（`max_session_disk_bytes`）与 ENOSPC 背压。
- RP-C（目录 / Skill / Tool）、RP-D（生命周期 close/drain/destroy/purge、GC、retention permits）、RP-E 统一验收；Assurance 完成后接 BW09。
- 生产 DeepSeek 部署的认证计数器（带 `count_mode` 与认证回执）；没有它 `build_arp_runtime` 在真实部署上会以 `GENERIC_TOKEN_BOUND_UNCERTIFIED` 拒绝装配（核验第 6 条）。
- `scripts/verify_development_handoff.py` 的 SDK 清单在本片改动后不一致，交付时需重生成。
