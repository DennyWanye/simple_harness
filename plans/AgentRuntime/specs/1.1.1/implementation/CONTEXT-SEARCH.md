# Context、搜索与索引：确定性执行细节

## C1. ProtocolGroupAdapter

输入是固定agent/session/highwater的原Journal和ledger；从`build_units`保留instructions/user/tool unit差异。一个CLOSED_TOOL组由该assistant response中的有序call IDs和对应最终工具消息组成；缺一项保持OPEN_TAIL。terminal answer以原Turn committed/result receipt确认；模型文字“结束”不够。USER_ANCHOR无call，闭合是输入已持久；OPAQUE_REQUIRED来自原Provider协议adapter，`indexable=false`。

group_id使用原protocol_group_id或系统稳定投影 `{agent_id, turn_id, original append IDs, ordinal, group_kind}`；生成一次在原Journal append事务形成closing receipt。source_hash只覆盖原record exact refs；更换合法redacted view改变view_hash，不改source。源seq gaps必须能解释为隐藏/其他kind/非本Turn，而不是按连续数字自行填消息。

必需组数不受optional enumeration cap隐藏。为每个已读历史Turn生成TurnCoverage.required_group_ids；无法完整枚举其所有组则该Turn不计N。一个当前Turn中100个闭组不必全留下；旧anchor与最近open tail仍按原序呈现。检索内容不能被追加为新原始Journal再embedding。

## C2. 预算构造与section排序

Policy section_soft_caps必须恰含A/B/C/D/E，各值非负；fixed_soft_max只对optional总量生效。顺序固定hard优先，其后A:0/B:1/C:2/D:3/E:4；section内部按正式输入关系 current subject=0/direct required input=1/reviewer feedback=2/shared current evidence=3/historical background=4，再canonical exact ref排序。特例核心工具Schema属于E hard，不能为凑数删掉实际发送定义。

分段charge必须来自固定renderer/meter的可加上界；若只能final render精确计量，则试装按保守adapter charge，最后闭环缩减；不得把结果标为true-token最优N。final bytes限额先于Provider发送，同一错误不能转成静默降为字符估计。

## C3. 一次Search的完整算法

1. Scope固定到当前Session，不接受任意AgentID。冻结root、control_generation、active_index_generation、authority readset、closed-group高水位和完整expected集合。
2. 以FileGuard→短exec读取→partition事务创建query snapshot，upper_commit冻结。存实际query hash、exclusions（完整group IDs）、view/embedding/chunker、limits、expires。没有索引也生成明确来源错误或PARTIAL snapshot，不做COMPLETE_EMPTY默认。
3. QUERY embedding使用原call key `session+query_cursor_identity+embedding_fingerprint`一次，持久result ref后resume复用；调用失败允许的policy才用LEXICAL_ONLY，mode被冻结不在游标中途换。
4. 每页按session_chunks.rowid升序扫描`index_generation=? AND commit_seq<=upper_commit AND rowid>after`；过滤source seq≤journal_highwater和全部exclusions，不以recent_start排除活跃Turn早期闭组。读取最多min(256,8MiB/dim/4)行；一页不持跨请求SQLite游标。
5. 精确seq/ID路线校验实际原记录，不通过字符串前缀猜。words=regex Unicode letters/numbers/underscore/path token，NFKC+casefold，≤16 unique tokens；MATCH各term双引号且内部双引号加倍，OR组合。trigram输入包含≥3 Unicode字符才启用；否则用casefold substring页扫。FTS仅缩减候选，必须在相同frozen view重新判断，不用全库bm25。
6. exact score=命中ID/seq数量；words score=unique literal token命中数；tri score=unique trigram命中数；vector=normalized f32点积（score floor0.35是政策起点，非通用相似度真理）。各路同score按 `(record_id, source_hash, utf8_start, utf8_end, chunk_id)` 排序，持久bounded heap topK。没有该route可用而policy允许降级则明确mode/degradations。
7. 未扫描完只返回SCANNING空items＋scan counters＋cursor；不能在Response做“没有历史”的文案。超总30s或cursor总页上限256返回INDEX_SCAN_LIMIT并保留诊断，不宣称COMPLETE。
8. 扫描完成，各路先按Policy.max_query_candidates=K截断；rank从1开始，RRF `sum(weight/(60+rank))`，weights exact1/words.6/tri.5/vector.7。union≤4K→按(-RRF_score, 完整stable key)全局截Policy.max_candidates=M；此算法被完整定义，不声称找到所有相关内容。正式read/授权/源hash复核以后再封装RecallItems。
9. 同record、相同source/view/provenance/scope的重叠或相接span合并，超过16KiB则保留rank较高原span不合并。合并后重新meter charge；不把旧charge相加冒充新成本。
10. 固定最终结果表，后续RESULTS页稳定顺序；新增索引不改变旧rank。返回必须再次验证authority。撤权/换gen/销毁使cursor失效；再次查询是新snapshot，不修改旧页。

单query内page token不可变：第一次消费旧token完成一步并保存对应Response和next token，同token重送不再推进；预算和扫描计时不重置。缓存页需同scope读权；过期可清理，不能作为业务事实源。SearchPage body_bytes按完整canonical编码直到长度稳定（最多4次），超预算减少完整items或返回ITEM_TOO_LARGE，不能切JSON字段。

## C4. index coverage

每个闭组无可索引文本也必须记录`chunk_count=0,state=EMPTY_VALID`及closing source receipt，并计入expected覆盖。向量必须逐chunk有真实embedding receipt；零向量是错误，不算vector ready。索引progress不能用最大seq声称中间全部完成，必须从完整group/source集合与每chunk状态生成set hash。

index generation变化条件：chunker/view policy/embedding fingerprint改变；Journal旧source变更是损坏。分区允许旧/新generation同时存在，不允许同generation对相同chunk主键写不同hash。CURRENT采用由central arp_index_publications唯一指针判定；partition只是READY物理数据。

`IndexSnapshot`绑定upper_commit的查询不会看到后来同generation增加的chunk；所有indexed_groups/session_chunks/session_vectors带append commit_seq，同一物化批次用同seq。各路可见性过滤一致。旧generation未删除前不代表仍可被新cursor采用。

## C5. 唯一状态真值表（R2）

本表是 RetrievalReceipt、SearchPage、ManagementSearchPage、ContextSearchPage、ContextSummary 的唯一来源。receipt v2保存query_result_count（SCANNING为null）、page_offset、returned_count和has_more；Page同名字段必须一致。

| phase | index_coverage | mode | query_result_count | 单页/后续要求 | status |
|---|---|---|---|---|---|
| SCANNING | 任意 | 任意 | null | returned=0,offset=0,has_more=true,PROGRESS | PARTIAL |
| RESULTS | PARTIAL | 任意 | n≥0 | 完整冻结索引已扫，按n分页 | PARTIAL |
| RESULTS | COMPLETE | LEXICAL_ONLY | n≥0 | 按n分页；零也显式词法范围 | LEXICAL_ONLY |
| RESULTS | COMPLETE | HYBRID/EXACT_ONLY | 0 | offset=returned=0,has_more=false,APPEND_FINAL | COMPLETE_EMPTY |
| RESULTS | COMPLETE | HYBRID/EXACT_ONLY | n>0 | 0≤offset<n,returned>0,offset+returned≤n,has_more=(offset+returned<n) | COMPLETE |

先判扫描，再覆盖，再词法降级，最后零命中。结果status表示**整个固定查询在声明route范围内**，不是当前页有无items。EXACT_ONLY只能用于显式精确查询，不能冒充语义搜索完整。COMPLETE不保证找到所有现实相关材料，只保证本合同指定的冻结候选算法完整运行。

RESULTS的rank_ordinal必须从page_offset+1连续；正n不生成空耗尽页，最后一页重送原样，多余cursor用具名错误拒绝。SCANNING可以snapshot_chunks=0但尚未最终定稿，不产生COMPLETE_EMPTY。UNAVAILABLE使用Error，不生成假的RetrievalReceipt；NOT_REQUESTED只用于无检索/明确SKIPPED的Context aggregate。所有嵌套decode必须递归应用命名DTO语义，不只验证最外层结构。

SearchCoverage: scanned≤snapshot，vector_ready≤indexed≤expected；COMPLETE要求indexed=expected且HYBRID时vector_ready=expected。RESULTS必须ranking_final且scanned=snapshot。Receipt的indexed_closed_groups与coverage.indexed_groups相等；RESULTS searched_closed_groups等于indexed_groups。真实来源/访问完整性仍由生产resolver证明，纯codec只验证字段组合。

Context自动召回使用独立ContextRecallResult聚合；参见CONTEXT-RECALL.md。ContextSummary从这个aggregate投影状态，再单列manifest实际选中数；不能把F裁剪成0条解释为query无命中。

## C6. HistoryRead

read精确命令读取原Journal，不依赖向量。首次固定seq range、source highwater、caller用途与readset。每片最长min(max_bytes -完整metadata费用, 可用UTF8正文)，仅在完整codepoint边界结束；末尾不能产生零字节无限cursor。max_bytes连一个codepoint及header都容不下返回ITEM_TOO_LARGE。下一游标保存seq与offset，前一页重送原样，不重读latest。

已授权但隐藏的记录只报告允许披露的hidden range；不能泄露敏感记录的长度/hash给无权用户。访问变化终止游标，已经合法曝光的历史不改写。空字符串记录作为合法完整slice start=end=0；多字节截断与invalid UTF8分别测试。

## C7. 上限统一（R5）

配置採用**超硬上限就拒绝**，不默默截配置：max_scan_rows_per_page≤256、max_search_cursors≤16、max_retrieval_ms≤500、max_query_total_ms≤30000。settings提交/批准/采用三处跑同一Policy codec；GET返回实际配置值，不维护另一个猜测effective值。运行页可因剩余行/8MiB/时间更短，这不是把非法4096配置偷偷当256。

max_query_candidates=每路channel heap的K（1..512）；max_candidates=各路K经RRF并集后的全局M（1..512）；max_recall_items=composer最终F最多数（0..128）。K与M不要求相等。span合并发生在已选全局M集合内，代表项保留最小rank的完整stable key、score=max成员分数，重新计量再连续编号，不回填被M淘汰项。每页search limit≤32只是分页大小，不是K或M。旧profile配置以其冻结版本解释，新profile采用时须满足本硬上限，不改写旧policy hash。
