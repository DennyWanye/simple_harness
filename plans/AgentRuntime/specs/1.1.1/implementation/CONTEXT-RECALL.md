# 自动 F：ContextRecall 内部合同（R3，ARP-EXEC-1.1.1）

本文件是本次新增内部接线合同，不是新的 Runtime、检索服务、执行账本或 SDK 已实现声明。外部模型/管理工具继续使用受限 SearchRequest；**模型不能填写 highwater、排除组、调用身份或访问授权**。所有类型完整字段见 runtime-plane.schema.json，字段 producer 见 field-producers/type-producers。

## 1. 唯一 caller 和签名

原 `AgentProviderWire.prepare_request` / 原 request_preparer 在冻结 LLM 请求之前调用原 `NativeContextComposer.prepare`。它先从原 ordinal allocator 保留本次 prepare slot，捕获 `ContextSourceSnapshot` 和 `ProtocolGroupSnapshot`。纯 composer 只产出 A–E、mandatory、protected 后缀、query 请求，**不写库、不调用 embedding**。

```python
class ContextRecallCoordinator:                    # 由原 runtime/assembly 注入
    async def start(self, access: SessionAccess,
                    request: ContextRecallRequest) -> ContextRecallProgress | ContextRecallResult: ...
    async def resume(self, recall_key: str, access: SessionAccess,
                     *, now_ms: int, clock_receipt_ref: Pin[receipt]) -> ContextRecallProgress | ContextRecallResult: ...

class SessionSearchService:                        # 同原 history service，不另建引擎
    def start_frozen(self, access: SessionAccess,
                     request: ContextRecallRequest,
                     snapshot: IndexSnapshot,
                     query_call_receipt: OriginalCallView | None,
                     query_id: str) -> ContextSearchPage: ...
    def resume_frozen(self, access: SessionAccess,
                      recall_key: str, cursor: str) -> ContextSearchPage: ...
```

`ContextSearchPage.cursor_purpose=CONTEXT_RECALL`，新增 purpose 仅允许上述系统 caller。复用原授权的 MISSION_CONTEXT/RETRIEVAL 或 STANDALONE_CONTEXT/HISTORY 评价，不 mint 新授权。public search cursor 不能用于该内部 purpose，反之亦然。

Provider prepare 得到 Progress 时持久等待并让原 tick 推进，**不占 LLM 物理槽位、不用忙循环在一笔事务内跑完**。当前 SDK 的异步返回可用既有 pending/preparer result adapter；该 adapter 必须保存 recall_key，并明确不调用模型。不是把 Progress 当 RecallResult 或空 F。

## 2. 请求身份和字段来源

| 字段 | 唯一来源/冻结规则 |
|---|---|
| original_request_key / provider_request_ordinal / turn_id | 原 Provider request allocator 的稳定 prepare slot；尚无最终 payload hash也可预留身份。不从模型输出生成 |
| recall_key | 系统 `sha256(canonical({kind:'context-recall-key-v1', root_incarnation, session_id, original_request_key}))`；不是新的业务Operation身份 |
| request_hash | ContextRecallRequest 全部canonical字节，不含自身 hash；写 arp_context_recalls 后不可改 |
| agent/session/root/control_generation | 原创建/Session binding，与可信 SessionAccess精确核对 |
| policy_ref / adoption_revision | **本次 prepare捕获**的effective policy/adoption；普通设置之后改变不重写该请求 |
| source_snapshot_ref/hash / group_snapshot_ref | 原 ContextSourceReader、ProtocolGroupAdapter产生的完整不可变快照；采用原artifact/CAS元数据/retention，不伪造Worker Result |
| journal_highwater / expected_group_set_hash | composer 捕获的高水位与可索引完整闭组集合；内部search不得另取latest |
| mandatory_group_ids / protected_group_ids | §4.1第1–3步的实际装填；去重后稳定排序，hash=canonical(sorted(union))；排除列表不受模型控制 |
| authority_readset_hash | 当前真实授权与Assurance用途检查得到的读集，不能用空对象补 |
| query_text/hash/parts | 本文件§3的确定性构造，不新问模型 |
| embedding_resource_ref/fingerprint | 当前批准资源的精确不可变定义；缺失仅在既有批准降级规则许可时为null |
| limits | 有效Policy与本版本硬边界的明确计算（§4），不让page逐次扩大 |
| created_at/deadline/clock_receipt | 原持久clock observation；deadline=created_at+total_budget_ms，重启不延长 |

`original_request_key`、`(turn_id,ordinal)`、`recall_key`均唯一。重复同键同hash读原工作；异hash为RECALL_BINDING_INVALID，不覆盖旧记录。请求引起的无效化或新ordinal采用1.1 §3.3原no-send/settlement合同；UNKNOWN embedding不是新身份自动重复付费的依据。

## 3. 自动 query：唯一 recipe=context-query-v1

三个槽依次为CURRENT_INPUT、TASK_GOAL、LATEST_FEEDBACK，各只选一份当前请求实际可读来源；不存在的可选槽省略，不从全部历史拼最新猜测。CURRENT_INPUT 是当前原始HostInput可检索文本；TASK_GOAL仅当前Task正式目标说明；LATEST_FEEDBACK是当前Turn最后一条已授权非模型内部思维的工具/Verifier反馈文本。相同时按原消息顺序，不能混入highwater之后的新记录。

对每段 `normalized=' '.join(text.split())`，空段省略。截取只用于query，**不改变Context中原始必需输入**：CURRENT_INPUT最多2048个源字符，超过时首1536＋` … `＋尾512（分隔符额外3字符计入query总上限）；TASK_GOAL首1024；LATEST_FEEDBACK首768。以`[KIND]\ntext`连接，最多4096 Unicode字符且16384 UTF-8 bytes。每段QueryPart保存原source_ref、head/tail字符数、截断标志、实际选段text_hash。没有NFKC改写原输入；词法route规范化仍沿原检索定义。

三个槽全部为空→持久 SKIPPED/EMPTY_QUERY/NOT_REQUESTED，不分配cursor，不调用embedding。recall_token_ceiling=0 或 max_recall_items=0→RECALL_DISABLED，同样不用embedding。不能用字符串“当前任务”构造一个看似有意义的查询。

## 4. 时间、候选和调度上限

- page_rows=Policy.max_scan_rows_per_page≤256；实际页再受8MiB向量页上限和剩余行约束。
- page_budget_ms=Policy.max_retrieval_ms≤500；包括该页索引读取、评分、短锁等待和物化。不能保证抢占单条原生C调用，但必须检查每行/页，原SQLite busy_timeout不得超过本次剩余预算。
- total_budget_ms=Policy.max_query_total_ms≤30000；包括开始、embedding排队/调用、全部scan/results页、聚合。deadline存入请求，停机时间计入。读权/clock回退按原根水位策略拒绝，不重置。
- scan最多256页；RESULTS最多ceil(512/32)=16页；同cursor重送不重复计页。max_search_cursors≤16计**逻辑查询**，internal recall占一个，不是每page占一个。初次embedding尚未完成的中央recall也占slot；与分区query_id合并计数，不能双算或漏算。
- `channel_top_k=Policy.max_query_candidates`；各路最多K；RRF合并后截 `global_candidates=Policy.max_candidates`；二者独立且均≤512。不在每路用global M再次截断。
- 全部最终candidate聚合≤2MiB且≤global M；达字节限报RECALL_AGGREGATE_LIMIT，不能截断后称全候选。F随后由纯composer依tokens和max_recall_items筛选，未选候选不是检索失败。

一个tick最多一个page/recall，沿原轮转/每Session工作上限；没有新增线程调度器。page完成后释放FileGuard与所有SQL游标，embedding永远锁外。

## 5. 两库/调用协议（不声称跨库ACID）

| 阶段 | 动作 / 事务与崩溃恢复 |
|---|---|
| C0 | 原exec UOW写arp_context_recalls PREPARING，冻结request/同query_id；原request slot归属检查。命令重送返回原行 |
| C1 | FileGuard→exec短读校验→partition创建精确IndexSnapshot（用request highwater/expected集合）；先持久partition receipt，再在exec行附其ref。中央ACK前崩溃按同snapshot/query identity取回；孤儿无权成为新snapshot |
| C2 | 原Provider/embedding协调器按`recall_key/query/<embedding_fingerprint>`找/建**一次**调用与原预算预留。实际调用先持久，结果与费用沿原ledger；exec只存引用。中央绑定前崩溃以同call key读取，不能再提交一次 |
| C3 | 成功则HYBRID；明确失败且获准降级则LEXICAL_ONLY；UNKNOWN且允许降级可以冻结LEXICAL_ONLY，但保留原UNKNOWN/hold/迟到结果核对，后来不得splice向量改变该query。不允许降级则等待同call或具名BLOCKED |
| C4 | 模式选定后partition search_queries启动SCANNING；所有读受原snapshot/upper_commit/highwater/exclusions。缓存页和next cursor一笔partition CAS；exec记原cursor回执后让tick继续。同token重送返回原页 |
| C5 | 收齐SCANNING与RESULTS页，验证request/query/snapshot/policy/exclusions一致、页链不重叠、rank从1连续且最终count等于总候选。最终排名前绝不向composer提供临时items |
| C6 | exec UOW写不可变ContextRecallResult/result_hash，READY或SKIPPED；绑定页refs/聚合candidate hash。正式采用的范围/片段须copy/pin原Journal/CAS到原blob root；临时DB只是派生缓存 |
| C7 | 纯composer用聚合候选计算F与扩展G，重叠剔除且不再query；wire恢复toolcalls→final meter→原request/reserve＋manifest冻结。manifest.retrieval_receipt_ref指**aggregate结果**，不是末页。recalled_chunk_ids是其实际被发送子集 |

`arp_context_recalls`仅协调，不能设置Provider调用成功或已结算。其首次PREPARING/WAITING_EMBEDDING状态没有新业务effect。所有访问和递增沿原锁序Catalogue→Orch→FileGuard→exec→partition；不能持exec反调Orch，不能在有partition写锁时await。

Query完成后候选缓存清除不破坏历史manifest：aggregate JSON与Pin在exec保留，实际发送片段和request bytes按原保留策略。页缓存销毁后可诚实显示历史page正文不可读；不能因此重新查询latest来“复原”。不把整个搜索缓存永久转为新事实库。

## 6. Aggregate及ContextSummary

`RetrievalReceipt`仍是**单页**；新`ContextRecallResult`是**一次逻辑自动检索的全部结果**。READY必须含所有最终候选，candidate_set_hash=canonical(candidate_items) hash；page_chain_hash=有序完整page refs hash，page refs用原artifact元数据，实际query储存保证幂等。

READY的status按统一R2真值表、完整query总数计算；有结果但F预算放不下，仍是COMPLETE/LEXICAL_ONLY/PARTIAL，不变成COMPLETE_EMPTY。`ContextSummaryView.recall_count`取实际manifest选中数，`retrieval_summary.candidate_count`取aggregate，必须 selected≤candidate；ContextSummary使用同一status函数，不能拿当页空items自行推状态。

缺页、重复rank、跨snapshot、错误ref、candidate hash不符均RECALL_AGGREGATE_MISMATCH。聚合只保存明确的出处，不认模型claimed evidence。

## 7. 异常唯一处置表

| 条件 | 自动F / prepare | 原调用身份及计费 |
|---|---|---|
| 真零命中且完整排名与coverage | READY/COMPLETE_EMPTY；合法空F | 已发生调用照记 |
| 索引落后但冻结索引已完整扫描 | READY/PARTIAL＋INDEX_LAG，可用最终候选；不可填COMPLETE | 同query，下一请求才可新snapshot |
| embedding明确不可用，allow_lexical_degradation=true | 原query开始扫描前冻结LEXICAL_ONLY | 不再付费调用替代embedding |
| embedding UNKNOWN，允许降级 | 同query可转词法模式一次；unsettled refs保留 | 原UNKNOWN继续核对，不能释放hold/删除唯一收费事实 |
| embedding UNKNOWN，不允许降级 | Progress WAITING_EMBEDDING；deadline到 BLOCKED/EMBEDDING_UNAVAILABLE | 只恢复同call，不新key |
| 任意扫描/聚合预算到顶（无最终可信候选） | 持久SKIPPED，status=PARTIAL，skip_code=INDEX_SCAN_LIMIT或RECALL_AGGREGATE_LIMIT；F为空，仍可用必需A–E/G继续 | 不是COMPLETE_EMPTY；deadline不重置；只读自动F是可选材料，不屏蔽必需来源缺失 |
| 暂时没有cursor slot | Progress等待原prepare，最迟deadline按INDEX_SCAN_LIMIT跳过 | 不先开启新embedding；不能超配slot |
| 权限/来源readset变化、purge、index换代、request取消 | STALE/BLOCKED，拒绝当前prepare；原Q09决定是否允许安全新ordinal | 已发生费用照记；旧UNKNOWN不变 |
| 用户普通settings更新 | 原request使用原policy/adoption，下一request用新值 | 不重新embedding |
| 进程退出/lease到期 | 启动扫描上述非终态行；恢复原CAS request/query/cursor/call，时间预算照旧 | 不因重启换query/embedding身份 |

明确优先级：来源安全/取消检查先于timeout fallback；UNAVAILABLE且不许降级的embedding在deadline处为BLOCKED，不可借“可选扫描超时”绕过禁止词法降级的政策。已选择词法且budget到顶则正常SKIPPED/PARTIAL。

## 8. 允许修改和验收边界

新增目标模块 `src/simple_harness/agents/context/recall.py`；修改原composer、wire prepare adapter、SessionSearchService内部入口、原exec UOW/ARP schema、Session tick和精确retrieval resolver。原history模型工具输入不增加scope字段。原费用/UNKNOWN、协议冻结和Assurance曝光入口不被替换。`implementation/INTERFACES.md`列完整签名。

参考`reference/context_recall.py`只演示composer调用内部start/resume、冻结扫描和原call-key的恢复规则；使用受控call事实，不冒充SDK/真实embedding。**生产验收必须经实际composer→真实原request_preparer→SessionSearchService→原Store/ledger**。至少目标在第二扫描页、同期间追加不混入、锚点/最近排除、prepare退出恢复无重复提交、限制不足不宣称零命中、跨页聚合身份错误拒绝。此验收在BODY_WIRED后执行，不把生产功能尚未实现当计划无法开发。

## 9. 中央 checkpoint 的完整类型与原 Store 接口

`arp_context_recalls.progress_json` **只能编码 ContextRecallCheckpoint**，不是任意字典。所有字段均必填：

```text
schema_version=1; recall_key; request_hash; phase; query_id
index_snapshot_ref|null; mode|null; query_call_key|null; embedding_invocation_ref|null
cursor_token|null; page_refs[]
scan_pages_committed; result_pages_committed; scanned_chunks; snapshot_chunks
next_result_offset; created_at_ms; deadline_ms; last_observed_at_ms; next_wake_at_ms
last_clock_receipt_ref; terminal_error_code|null
```

生产 owner 是原 exec UOW 的 `put_context_recall_locked(request, checkpoint)` 与 `cas_context_recall_locked(recall_key, expected_row_version, checkpoint, result)`；query/partition 页 owner仍为原 SessionSearchService。方法名可以映射到已存在等价函数，但**不能嵌入自行 commit 的 facade**。C0首次进PREPARING，checkpoint中的query_id由系统同事务分配；query_call_key在批准真实resource后决定，原调用尚未派发不等于已执行。

- Request body/identity/deadline在C0后不可改。checkpoint与中央列中的phase、request_hash、query_id、deadline必须相同；mode一旦从null选定不可变，snapshot_ref非null后不可变。原call引用只绑定同query_call_key，不覆盖晚到事实。
- `len(page_refs)=scan_pages_committed+result_pages_committed`；每份页ref只能追加一次，重放旧cursor必须核对原页hash后返回，不重复计数。result offset仅由已确认结果页的数量前进。
- PREPARING不能带已有page/cursor/mode。SCANNING/FETCHING_RESULTS/READY必须有snapshot与mode；READY至少有一个RESULTS页（合法零命中也有该最终空页），cursor为空。BLOCKED/STALE必须有具名terminal_error_code，其他phase没有该错误字段值。
- elapsed取原可信时钟；`last_observed_at_ms`可晚于deadline以保存实际超时观察，但不得回拨至created_at之前。next_wake不得超deadline；终态不再唤醒。deadline不因lease、cursor重送、程序重启延后。
- terminal result immutable；STALE/BLOCKED没有可用于F的成功结果。旧UNKNOWN调用继续由原账本核对，不能因中央协调工作终止丢弃费用。
- 短事务的checkpoint writer核对上述跨字段与前后缀规则；SQL负责主键、不可变列、阶段和终态保护，不声称JSON_VALID已校验这些业务语义。

读取统一为 `get_context_recall_exact(original_request_key)`；原tick `resume_pending`加入该中央表非终态扫描，沿原公平批次上限，不新增job kind或SUMMARY调用。ContextRequest被取消/Session destroy时先封新调用，再将相关recall置STALE并保持原call refs；PURGE的完整集合证明包含这些非终态recall、未ACK页和未决原调用。无搜索工作时不得用空成功recall伪造执行过查询。

### 9.1 聚合查什么，哪些不能只靠字段证明

`aggregate_context_recall(request, snapshot, ordered_page_refs, read_scope)`是C5唯一聚合器：用原exact artifact/receipt resolver读取每页，核对来源属于该query_id及前后cursor。然后逐页应用同一SearchPage语义验证器；检查Session、generation、query hash、highwater、source snapshot、policy、authority读集、index generation/upper_commit、exclusions相同；progress只能在RESULTS之前、扫描计数单调、最终coverage各结果页相同；每页offset连续、rank不重叠、最后has_more=false，总量吻合。原请求缺scope、页不完整、相同ref异body、超过上限都拒绝聚合，不读latest补页。

`reference.context_recall.collect_frozen_pages`提供上述纯字段/顺序部分的窄oracle；authority、实际page issuer和cursor链核对是生产resolver/Store验收，不以构造测试字典替代。

### 9.2 Blob 与临时分区生命周期

C0冻结request、source/group快照时通过**原pin/保留协议**登记临时context-prepare root；如果原Store和CAS不同库，按既有prepare/receipt桥在read之前完成绑定，不能先读后补假的root。页正文和最终candidate由分区缓存服务，copy到已有CAS后才写其exact元数据引用；C6写结果时将保留责任从prepare root移到原ContextManifest/request root。C6前退出按同recall key恢复，C6后退出复用结果不重搜。被跳过或失效的准备在原请求安全终止、查询调用结算或转交原核对责任后释放临时root。正式上下文和审阅仍依赖的字节不随分区删除；无临时分区依赖的正式pin不阻止删除索引。

### 9.3 clock receipt的生产入口

`capture_context_recall_clock_locked(conn, recall_key, tick_id, observed_wall_ms)`由原runtime tick在实际读取原ClockPort之后调用。它使用原command/receipt writer，保存精确body `{kind:"context-recall-clock-v1",recall_key,tick_id,observed_wall_ms,previous_highwater_ms}`；previous_highwater来自已持久checkpoint或C0创建事实，不来自模型/请求体。首次与同tick重送按原receipt幂等，同tick异body冲突；新tick记录真实新观察。没有“原本就存在该recorder”的假设，这是coordinator内需实现的窄生产方法，不增加独立clock表或网络时间服务。

恢复比较实际观察与持久highwater，回退则CLOCK_ROLLBACK/BLOCKED并保留费用/原call，不能重置deadline；期限后的实际now可写入terminal checkpoint。这个机制检测已观察本地时钟回退，不承诺防御拥有本机时钟/磁盘任意修改权的攻击者。`resume`直接接真实now_ms与精确clock_receipt_ref，不依赖未定义的OriginalClockObservation类型。
