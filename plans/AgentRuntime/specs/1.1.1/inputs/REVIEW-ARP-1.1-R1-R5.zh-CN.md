# ARP-EXEC-1.1 复核与 PlanAgent 补正交接

日期：2026-09-23。评审范围：原生 BaseAgent / AgentRuntime / ReAct；不包含 Pi 或其他 Runtime 适配。附件内实施指令作为待评规格，本轮没有启动业务开发、安装候选包、改变模型或执行迁移。

## 结论

**可以推进不受下述问题影响的主体编码；尚不能认定为“接口、字段、状态全部冻结，可 100% 按原包直接执行”。审阅结论为 CHANGES_REQUIRED，范围限定为本报告 R1–R5。**

1.1 已实质修复 1.0 的多数缺口，值得保留其架构及当前合同。无需重做 Q01–Q20，也无需扩展 RuntimeBackend。R1 是已复现的生命周期/SQL 冲突；R2 是已复现的语义校验缺口；R3 是自动上下文检索缺少内部合同。R4、R5 为可随同修订的小范围一致性问题。

未来 SDK 功能未实现、真实模型验收未执行，不是本次要求退回补正的原因。当前父候选、Assurance 后继、真实计量与 embedding 资源仍应在实施启动时核验，不能用本包 reference PASS 代替。

## 输入与证据范围

- 输入：`/Users/denny/Downloads/simpleharness-agent-runtime-plane-1.1-2026-09-23 (1).zip`
- ZIP SHA-256：`dc9ed85cca268cd1a8505f6757f008f886e86dd38ca5c9a116f8570ecf9a4d37`
- 对照：`plans/AgentRuntime/2026-09-23-planagent-handoff/HANDOFF-PlanAgent-ARP-1.0.md`。
- 本地解包根：`.local-test-evidence/2026-09-23/arp-1.1-review/simpleharness-agent-runtime-plane-1.1-2026-09-23/`。以下文件行号均相对此解包根。
- 本轮实际运行 `tools/verify_delivery.py`：PASS，60 个被清单核验的文件。
- 本轮实际运行 `tools/check_plan.py`：PASS，89 schemas / 1755 fields / 89 examples / 80 sdk cases。该结果表示资产与选定语义检查通过，不表示所有合同一致。
- 三个定向反例：一个 SQL 状态转换、两个 SearchPage 错误状态组合；SQLite 3.50.4；只使用包内最小父表 fixture 和参考 codec，未打开产品数据库。
- 原始反例结果：`.local-test-evidence/2026-09-23/arp-1.1-review/targeted-probes.json`；SHA-256 `3254f65e5c269209470ab479590617822e83ee114cfc62505ca090213e5039e6`。
- 包内声称的 76 reference tests / 10 mutations 属作者证据，本轮未批量复跑；真实 SDK、迁移、原生 UI、模型验收均 NOT_COVERED。

## R1 — 删除异常的隔离路径与 SQL 状态机冲突（P1）

**定位**：`implementation/JOBS-LIFECYCLE.md:63`；`sql/execution_additive.sql:167`，尤其 175–180。

文档规定：PURGING 阶段 rename 后、receipt 前崩溃，发现原目录与 trash 同时存在或身份不符，应进入 QUARANTINED。SQL 只允许 PURGING → PURGED；DRAINING 同样只允许转 PURGING。

本轮按合法路径创建 Session，执行 CREATING → ACTIVE → DRAINING → PURGING，再执行 `state='QUARANTINED', generation=generation+1, row_version=row_version+1`，得到 `sqlite3.IntegrityError: invalid session change`，行仍为 PURGING / generation=2 / row_version=4。无法直接实现文档规定的恢复分支。

**补正要求**：明确删除期间异常的唯一持久状态和恢复路径，同步 lifecycle、Session DTO、SQL、错误与事件。可选择保持 PURGING 并记录有类型的阻断原因，或定义删除隔离状态；不能只放开 PURGING → QUARANTINED 而保留无条件 QUARANTINED → ACTIVE，因为已有 destroy identity 的 Session 不应由普通索引 rebuild 恢复执行。保留相同 destroy identity、精确目录 marker 与原收费核对责任。

**关闭证据**：至少覆盖 rename 前/后与 receipt 前/后崩溃、双目录、marker 不符；异常必须可持久表示；普通 rebuild 不得让进入删除流程的 Session 复活；恢复只能操作原精确目录。

## R2 — SearchPage 可同时声明未完成与 COMPLETE_EMPTY（P1）

**定位**：`implementation/FIELD-CONTRACTS.md:27` 至 31；`reference/schema_codec.py:49` 至 62；`contracts/runtime-plane.schema.json` 的 RetrievalReceipt / SearchCoverage / SearchPage。

文档已明确：扫描中只能 PARTIAL；COMPLETE_EMPTY 要求最终排名、完整覆盖、空结果及无下一页。但 codec 只验证 phase/ranking/count/cursor 的部分关系，没有把 receipt.status 与这些字段联动。

本轮两个合法结构、错误语义的对象均被 `decode('SearchPage', canonical(value))` 接受：

| 反例 | 错误组合 | 应有结果 | 实际结果 |
|---|---|---|---|
| R2-A | phase=SCANNING、ranking_final=false、snapshot_chunks=1、scanned_chunks=0、has_more=true，但 status=COMPLETE_EMPTY | 拒绝 | ACCEPTED |
| R2-B | phase=RESULTS、index_coverage=PARTIAL、expected_groups=1、indexed_groups=0，但 status=COMPLETE_EMPTY | 拒绝 | ACCEPTED |

**补正要求**：为 status × phase × coverage × mode × returned_count × has_more 写唯一真值表，并在 RetrievalReceipt、SearchPage、ManagementSearchPage 的实际构造与嵌套 decode 中应用。明确“全查询零命中”和“某一分页为空”的区别，明确 LEXICAL_ONLY 与 SCANNING 同时发生时 status 的优先级；ContextSummary 投影采用同一规则。仅由数据库事实验证的权限/来源不需要在纯 codec 伪造，但本项是纯字段组合，能够直接拒绝。

**关闭证据**：以上两个反例被明确语义错误拒绝；正常 SCANNING/PARTIAL、最终真零命中、最终部分索引、合法词法降级均可表达并通过。不得仅以结构校验通过声称本项关闭。

## R3 — 自动 F 检索尚缺请求与完成合同（P1，规格缺口）

**定位**：`ARP-EXEC-1.1.zh-CN.md:125` 至 131；`implementation/INTERFACES.md:20`、55；`implementation/CONTEXT-SEARCH.md:17` 至 30；`contracts/runtime-plane.schema.json` 的 SearchRequest；`implementation/type-producers.json` 的 RecallItem / RetrievalReceipt。

主流程要求 composer 固定 highwater，保护 mandatory/recent 组，再发一次 F 查询。新公开 Search 返回多页 SCANNING 进度，最后才有固定排名。当前 SearchRequest 仅有 `schema_version/query/cursor/limit/max_bytes`；SessionAccess 也没有 composer highwater、排除组或原 provider-request identity。接口及 producer caller 主要列模型 history 工具和管理入口，未明确 composer 的内部调用合同。

因而实施者仍需自行决定：

1. 自动 F 的 query 从当前原始输入、任务信息还是最近消息生成；空输入、长输入如何处理。
2. 如何传递并固定 composer 的 highwater、mandatory/protected exclusions、policy/adoption、query 身份。不能让内部 search 重取 latest highwater，检索到候选 Context 之外的新记录。
3. 一次 F 查询如何在 prepare 内推进多个 SCANNING 页；500ms page budget、30s total budget、取消和重启之间如何衔接；不能把首个空进度页当零召回。
4. 最终 ContextManifest 引用哪份 RetrievalReceipt：目前 returned_count 定义为单页数量，自动 F 消费多页时需要明确最终集合、receipt 引用及 hash 对应关系。
5. 超限、INDEX_LAG、embedding UNKNOWN 的结果如何影响 provider prepare；谁持有原 query call key，如何避免重启后新身份重复 embedding。

**推荐补正**：新增内部 `ContextRecallRequest/ContextRecallResult`（或已有等价 typed adapter），由 composer 显式传上述冻结信息；外部模型工具仍使用受限 SearchRequest。规定内部 start/resume 消费同一快照及原查询身份，在确定的总预算内完成一次逻辑检索。具体名称可由 PlanAgent 决定，不需要重新询问用户。若沿用 MODEL_SEARCH purpose，明确其可信调用来源与 Turn/request 绑定；若增加 purpose，同步 SQL/Schema/codec。

**关闭证据**：超过一页扫描且目标位于后页时，实际自动 Context F 获得目标；同期间追加 Journal 不混入；mandatory/recent 不重复召回；prepare 中断再恢复不重复付费；超限有明确失败/降级而非 COMPLETE_EMPTY。测试必须经过 composer，而不仅是 history.search 工具。

## R4 — 参考排名 tie-break 与正文不一致（P2）

**定位**：`implementation/CONTEXT-SEARCH.md:24`；`reference/search_scan.py:13` 至 16、55 至 57。

正文规定同分按 `(record_id, source_hash, utf8_start, utf8_end, chunk_id)` 排序；参考扫描行没有前四项，heap 实际按 chunk_id 排序。同分且 topK 截断时，两者可能保留不同候选。参考实现不能直接作为正文算法的完整等价 oracle。

**补正要求**：让参考行携带完整 stable key 并统一 channel 和最终合并排序，或明确并统一新的唯一排序规则。增加两个同分候选、record_id 顺序与 chunk_id 顺序相反、topK=1 的决定性反例。

## R5 — 配置上限与硬限制的关系需明确（P2）

**定位**：`ARP-EXEC-1.1.zh-CN.md:164`；`contracts/runtime-plane.schema.json:250`、270；`reference/search_scan.py:42`。

正文写每页最多 256 行、cursor 最多 16；Policy 接受 max_scan_rows_per_page=4096、max_search_cursors=32；参考 advance 拒绝 page_rows>256。不是要求所有配置都直接达到上限，而是当前没有统一“拒绝超限配置 / 硬截有效值 / 这些数字仅默认值”的规则。

**补正要求**：统一 Schema 硬上限，或明示 configured/effective 的计算与 settings 展示，确保 settings 接受的配置能被实际运行入口解释。还应统一 max_candidates 与 max_query_candidates 分别在哪一层截断；不要求删除二者，但必须明确用途。

## Q01–Q20 复核状态

以下“规格可实施”仅评价文档设计，并非生产实现 PASS。

| ID | 本轮评价 | 说明 |
|---|---|---|
| Q01 | 规格可实施 | 44 kinds、逐字段子集、exact resolver 映射已补 |
| Q02 | PARTIAL | 外部分页已定义；R2 状态组合、R3 内部自动检索仍需补 |
| Q03 | 规格可实施 | policy/adoption、首次 GET、CAS、receipt 重放已补 |
| Q04 | MAPPED / 本地核验待做 | 如实标识 reported sources，不能把旧 HTN 当当前后继 |
| Q05 | ENVIRONMENT_DEPENDENCY | 原 DeepSeek/HF estimator、wire/P/O 已区分；真实部署资源待验 |
| Q06 | ENVIRONMENT_DEPENDENCY | 原 invocation/usage embedding 适配已定义；真实资源待验 |
| Q07 | 规格可实施 | 真实评价点 receipt、显式 standalone、ARP 拒绝 AllowAll |
| Q08 | 规格可实施 | creation intent、defer_drive、locked UOW 有明确顺序 |
| Q09 | 规格可实施 | unsent 取消/hold 结算后换 ordinal；UNKNOWN 继续原身份 |
| Q10 | 规格可实施 | mandatory 与可选闭组、完整历史 Turn 计数已明确 |
| Q11 | 规格可实施 | source/view/index generation 分离与 upper_commit 快照已补 |
| Q12 | PARTIAL | 算法主体明确；R3/R4/R5 尚需统一 |
| Q13 | 规格可实施 | 三类 jobs、durable call identity、lease 恢复边界已补 |
| Q14 | PARTIAL | 七集合 disposal、锁序已补；R1 删除隔离分支冲突 |
| Q15 | 规格可实施 | 唯一 catalogue owner、静态定义与动态 health 分离 |
| Q16 | 规格可实施 | install/lock/trial/admit/load/execute 责任清楚 |
| Q17 | 规格可实施 | summary/details 分页，800 文件例子解决原大小矛盾 |
| Q18 | PARTIAL | 错误/事件资产已补；R2 状态真值表仍有可执行缺口 |
| Q19 | 规格可实施 | execution/partition 分开、PRAGMA、不可变 guard、retention 已补；真实迁移待验 |
| Q20 | 规格可实施 | 保留原要求、主体后验收、native/model/platform 边界明确 |

## 给 PlanAgent 的限定任务

在 1.1 上做一轮局部修订，完成 R1–R5，更新受影响的正文、接口、Schema/SQL、参考 oracle、fixtures、case 映射、DECISIONS 和交付 hash。不要重新设计整体架构，不加入 Pi/其他 Runtime，不改既有用户范围，也不要把本报告当作启动业务实施的授权。

无需向用户重新征求命名、迁移号、默认实现位置等普通技术选择。先自行统一合同；只有实际发现两项已批准语义不能同时满足，才一次列明冲突及推荐解法。

下一版至少提交 R1、R2、R4 的窄反例关闭结果，以及 R3 的明确签名、字段来源、分页聚合、恢复和失败顺序。R3 的生产集成测试列入主体完成后的统一验收，不要求为了计划审查先做假 SDK。交付仍明确区分 reference、真实 SDK、native、model 证据。

实施者可以并行推进未受影响的合同解析、创建 UOW、既有计量适配、目录与 Skill 等主体工作；在关闭 R1/R3 前，不冻结对应生命周期和自动检索代码。不得由本次计划检查推导产品已完成，也不需要更新 ARCHITECTURE 的功能完成度。
