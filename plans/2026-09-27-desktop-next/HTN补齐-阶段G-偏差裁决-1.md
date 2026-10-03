# HTN 补齐 · 阶段 G · 偏差裁决 1：会被改的 25 张表怎么记

- 裁决人：独立偏差裁决子代理（只读代码与文档；除本文件外没有改任何文件、没有跑测试；只在临时目录开过一个空库看 25 张表的主键、归属列、列类型与触发器，看完已删）。日期 2026-10-04。代码以 `htn-g` `1c93ac83` 为准，行号都是"约"。
- 依据：实施记录"G-0""G-1""G-偏差单 1"；`HTN补齐-阶段G-开工裁决与施工清单.md`（裁决 G-1、G-4、G-7，第三节 G-2～G-5，第四节"不做"）；主计划第 3.20 版阶段 G 一节与第二节"阶段 G 开工裁决新增的偏离"；原计划 v1.4 §16.1、§16.2（`plans/taskSys2/升级planV1/v1.4/simpleharness-full-target-1.4/complete-plan.zh-CN.md`）；原 TaskGraph 代码级计划 §10.1、§10.3（`plans/TaskGraph/v1/simpleharness-taskgraph-code-execution-plan.zh-CN.md`）；代码 `SDK/storage/source_records.py`、`SDK/storage/store.py`（`open`、`transaction`、`append_event`）、`SDK/observability/business_replay.py`、迁移 41 的全局触发器、`SDK/orchestrator/event_handler.py` 的 `_durable_watermark`、`SDK/api/facade.py::events`、`SDK/orchestrator/planner_views.py`；仓库根 `CLAUDE.md`。
- 路径缩写同施工清单：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`，`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`。

---

## 〇、结论

**选 B（存储层统一记"整行变化"），但要附带 8 个条件（第三节）才算满足原计划与裁决 G-1。** 偏差单原文写的 B 方向对，但有三处不补会出事：

1. **幂等键只按内容摘要算会吞改动**：同一行 A→B→A→B→A，第 4 次和第 2 次的内容完全一样，`append_event` 遇到同键会直接返回旧事件，这次改动就丢了。
2. **没有"前像"，校验就是自证**：表里的行是什么，事件里就抄什么，重建当然和表一致。必须让每条改动带"改前哈希"，重放时逐条接链，才能抓到绕过存储层的写入。
3. **领域事件不再接受重放校验，"静默改动"会被藏起来**：按 A，漏发领域事件会被重放抓到；按 B 就抓不到。要用"同事务有没有领域事件"这条秩序检查补回来（第三节第 6 条）。施工清单里的改坏 G-08、G-18 也要靠这条才抓得到。

另外，B 会让事件条数和体积大约翻一倍，下面这些读事件的地方要同步改，否则会把记账事件当成"有进展"，或者塞进界面：空转水位、对外事件页、规划视图里"全量读事件再按类型筛"的循环。

**不需要问用户**：这是技术口径，不碰用户已定的范围和产品决定。要告诉用户的一件事：G 工期从 15～18 天降到约 11～13 天（第六节）。

---

## 一、B 是否满足原计划 §16.1 和裁决 G-1 的本意

| 要求 | 原文 | B（加上第三节的条件）能不能做到 |
|---|---|---|
| v1.4 §16.1 | "每一次正式变更记录足够 payload 或受保留保护的不可变引用，纯 reducer 重建" | **能。** 会被改的表，每次改动都在同一事务里记一条带改后整行的事件；只增表照第 1 批点名。重建只做"每个主键取最后一版，并逐条接链"，是纯函数：不调模型、不读时钟、不读外部 |
| v1.4 §16.2 | 事件、投影、回执在同一事务提交；reducer 不按当前时钟重判 | **能。** 提交前在同一事务里追加；重建不取时钟 |
| TaskGraph §10.1 | 完整内容放在同事务的不可变记录里，事件引用它；不能声称单靠旧事件内容就能重建全部 | **不冲突。** 只增表仍按"引用 + 哈希"点名，会被改的表带内容 |
| 裁决 G-1"会被改的表" | "建行事件必须带整行内容……每次改动都有事件且内容足以算出改后的列……幂等键必须区分同一行的每一次改动" | 前两句由构造保证。**第三句要靠条件 4**（幂等键里加"追加前的事件序号"），原偏差单没写这一条 |
| 裁决 G-1 的依据"同一件事一条路径" | 源记录只此一份；不要"事件里一份、表里一份"两份账 | 会被改的表本来就允许事件带整行（G-1 原话），B 没有新增重复。**但 B 不能再要求领域事件也带整行**（A 的做法），否则同一份内容会在领域事件和整行事件里各写一遍。所以领域事件的内容补全要作废（第四节） |

**本意上 B 少掉的一块**：原计划写的是"事件溯源"，期望业务事件本身完整，表只是事件的投影。B 实际上是"变更日志"：先有表，再把变化抄进事件。从"能重建、能审计"看两者等价，§16.1 也没有要求重建只能读领域事件（偏差单这一判断没错）。但 A 有一个附带好处：漏发领域事件会让重放不一致，而消费者（保证通道、执行图后续通知、界面）靠的正是领域事件。B 要用条件 6 把这一层补回来。补的是"有没有"，不是"内容对不对"：领域事件改为只当"给消费者的信号"，内容不再作为重建依据，也不再要求补全。这符合 CLAUDE.md"Harness 只管秩序"。

---

## 二、风险逐条（委托方点名的各项 + 读代码新发现的）

| # | 风险 | 读代码看到的事实 | 处理 |
|---|---|---|---|
| R1 | 同一事务里先插后删的行 | `source_records.name_written_rows` 遇到提交时已不存在的行会直接跳过 | 会被改的表按"前像 / 后像"判断：之前不存在、现在也不存在，就不记；之前存在、现在不存在，记成删除（条件 2） |
| R2 | 没有主键的表 | 25 张都有显式主键（5 张是联合主键：`method_instances`、`obligations`、`plan_revisions`、`sources`、`validity_epochs`），没有 WITHOUT ROWID，没有 BLOB 列（STRICT 表，只有 TEXT、INTEGER、REAL） | 清单守护加一条"折叠表必须有显式主键"。临时记录按**主键**记，不按 rowid（rowid 在同一事务里删了再插可能被复用）。主键被改，算成"旧键删 + 新键写" |
| R3 | 没有任务归属的行 | 23 张有非空 `mission_id`；`planning_decisions` 只能经 `planning_requests.request_id` 找到任务，`verifications` 只能经 `results.result_id` 找到 | 清单给这两张写 `owner` 关联；找不到归属就抛错、让事务回滚（静态守护保证不会发生）。**会被改的表绝不按"本事务唯一任务"兜底，也绝不记到部署时间线**（条件 3） |
| R4 | 部署时间线 | 第 1 批把"找不到任务的只增行"记在 `deployment` 上 | 会被改的表不进部署时间线。全局表（做法、策略）在 G-6 也可以走同一机制、归部署时间线（第四节 G-6 的建议） |
| R5 | 事务回滚与保存点 | 临时表在同一连接的 temp 库里，`ROLLBACK`、`ROLLBACK TO` 会一起撤掉临时记录（第 1 批也依赖这一点）。生产里有 4 处保存点：`assurance_work.atomic`、`assurance_final_writer._promote_methods`、`taskgraph_store`、`taskgraph_attempt_inputs` | 机制本身成立。要加一条保存点用例（R1 与 R5 合用一条），防以后有人把临时记录挪到 Python 内存里 |
| R6 | 幂等键的确定性 | 第 1 批的键是 `source-records:{任务}:{内容摘要}`；只增行的键唯一，所以不会撞。会被改的行可能来回改成一样的内容；`append_event` 同键时**静默返回旧事件** | 键里加"本事务追加前 `events` 的最大序号"（由库状态决定，仍然是确定的）；改为直接插入，撞键就报错，不走"同键返回旧事件"（条件 4）。前后哈希相同的空改动不记（条件 2），所以命令重放时不会多出事件，G-4"命令重放内容逐字节一样"仍然成立 |
| R7 | 触发器在迁移时、只读打开时的行为 | `Store.open` 先跑迁移再装临时触发器，所以迁移里的改写不会被记；`open_readonly` 不装，什么也不写；`library_copy` 后只读打开 | 迁移改动库结构会改清单，也就改了口径摘要，旧任务报"范围外"。只改数据、不改结构的迁移会让链断、报不一致，这是如实的；这类迁移必须同时让清单升版，或进"迁移补写例外"。不另加机制 |
| R8 | 事务外的写入串进下一个事务 | 在同一连接上、不在 `transaction()` 里的写入（测试夹具常见）也会触发临时触发器，记录会留到**下一个**事务被点名，归错事务。第 1 批同样有这个隐患 | 每个最外层事务开头清空临时记录；事务外的写入改由链检查报不一致（条件 5） |
| R9 | 体积 | 偏差单实测：一个三条要求的任务，按提交去重的整行快照 138 KiB，事件内容约翻一倍。热点：租约续期（每次续期改尝试行，约 1.3 KiB）、预算账户每次预留或结账都改、规划决定三次进度各带约 12 KiB 正文 | 可以接受，先记整行，不做逐列差分。空改动不记（条件 2）。G-8 报告里每个任务给出"整行事件条数 / 字节"；超过偏差单实测量级 3 倍再考虑只记改动的列。长时间跑的任务按租约续期线性增长，报告里写明 |
| R10 | 与领域事件"两份事实" | 领域事件内容（如 `MissionCreated` 里的预算是请求值）可能与行不一致；按 A 会去补齐，按 B 没人核对 | 口径写死：**重建只读整行事件和点名；领域事件是给消费者的信号，内容不作为重建依据，也不要求补齐**。消费者要事实就读行（现状大多如此）。"有没有领域事件"由条件 6 核 |
| R11 | 保证通道屏障触发器直接写的事件 | 46 张源表上的任务级触发器、`validity_epochs` 自己的触发器会在 SQL 里改 `validity_epochs` 的保证通道行，并写 `AssuranceEvidenceChanged`；迁移 41 的全局触发器给**每个没结束的任务**各写一条 | ①屏障改 `validity_epochs` 是触发器里的语句，照样会触发临时触发器，被整行事件记下（要有用例钉住）。②`AssuranceEvidenceChanged` 照旧只当消费者信号。③它是触发器自动写的，**不算**条件 6 里的"领域事件"。④**它不能参与归属判断**，见第五节附带发现 1 |
| R12 | 重放时的折叠顺序 | 单写者（`BEGIN IMMEDIATE`），事件序号就是提交顺序；一个事务里每个主键只记最终状态一次 | 按 `events.seq` 依次折叠；一个事务内不需要排序。逐条核"改前哈希 = 折叠到此刻的哈希"，最后整表与库**逐列精确比对**（时间列也比：整行事件抄的就是库里的值。"时间只核先后"的放宽对折叠表作废） |
| R13 | 读事件的地方把整行事件当进展或塞给界面 | `_durable_watermark` 把"最近一条不在 `OBSERVATION_EVENTS` 里的事件"当进展；约 :2924 另一处按"不是心跳"取最新事件；`facade.events` 原样分页给 Host，前端 `missionsStore` / `MissionsView` 每页拉 50 条做时间线；`planner_views` 每轮全量读本任务事件再按类型筛（约 :177 `list_events` 默认上限 10000，约 :289 `iter_events`），每条都要解析 JSON | 存储层记账事件（整行事件、点名事件）归入"不算进展"的一组；对外事件页过滤掉它们，序号游标照常前进；"全量读再按类型筛"的循环改成在 SQL 里按类型筛。否则：只续租的空转轮会被当成有进展（停滞判断失灵），界面时间线被刷屏，每轮规划解析的数据翻倍（曾经出过 CPU 跑满） |
| R14 | 全局触发器使事务里出现多个任务 | 见第五节附带发现 1 | 一并修 |

---

## 三、B 的机制规格（施工照做；在第 1 批同一处收口）

1. **一条事件，不是两条。** 第 1 批的 `SourceRecordsWritten` 扩成 `RowsWritten`：每个事务每个任务一条，内容 `{named:[{table,key,content_hash}], changed:[{table,key,before,after,row}], with_events:[类型…]}`。`named` 放只增表与回执账（照旧只带哈希）。`changed` 放会被改的表：`before`、`after` 是前后整行哈希，`after` 为空表示删除，`row` 是改后整行。同一个钩子、同一套临时表、同一个事件，这就是"一条路径"。开发期不兼容，第 1 批的用例和改坏 G-17 跟着改名。
2. **临时记录与前像。** 会被改的表装插入、更新、删除后的临时触发器，按主键记"本事务碰过哪些键"（同一个键只记一次）。第一次碰到时，把**原列原类型**的前像存进该表的临时影子表；插入就记"之前不存在"。提交前按主键读现行行，`before` / `after` 都用第 1 批的 `row_identity` 在 Python 里算（不走 SQLite 的 JSON，避免浮点格式不一致）。前后相同（包括前后都不存在）就跳过。
3. **归属。** 会被改的行取本行 `mission_id`，没有这一列的按清单 `owner` 关联去找（`planning_decisions`→`planning_requests`，`verifications`→`results`）；找不到就抛错。不兜底，不进部署时间线。
4. **幂等键。** `rows-written:{任务}:{追加前 max(seq)}:{内容摘要}`，直接插入，撞键就报错。
5. **事务边界。** 最外层事务开头清空临时记录。事务外的写入不再串到下一个事务，由第 7 条的链检查报出来。
6. **无静默改动（秩序检查，不判语义）。** `with_events` 是本事务里本任务的领域事件类型，来自临时事件触发器；不算在内的有：存储层记账事件，以及触发器自动写的 `AssuranceEvidenceChanged`。v3 检查：凡是 `changed`（以及会被改的业务表之外的 `named` 业务行，回执账除外）非空、`with_events` 却为空的事务，报"静默改动"，计为不一致。确属内部记账、没有消费者的写入，在清单该表写 `silent_ok: "<理由>"` 后放行。已知候选：规划决定的三种进度行、执行图收敛作业的 `WAITING→WAITING` 版本号自增、`grow`、尾部预留、`propose_action` 再投递、用量导入、只续租。每一处由施工按"有没有消费者或界面需要它"决定：补一条只带编号的信号事件，或者写 `silent_ok` 并给出理由。完成评估时逐条看理由。
7. **v3 改成一个通用折叠。** `FOLDERS` 和清单里的 `rebuilder` 删掉（25 张表同一个折叠，一条路径）。按序号逐条折叠，每条先核 `before` 等于折叠到此刻的哈希（链），再核 `after` 等于 `row` 的哈希；最后整表按归属取本任务的行，逐列精确比对。`_folded_table` 现在按 `WHERE mission_id=?` 取行，要改成按归属关联取，否则 `planning_decisions`、`verifications` 取不到。
8. **读事件的地方（R13）。** `RowsWritten` 进"不算进展"的一组（`_durable_watermark` 和约 :2924 那处）；`facade.events` 过滤掉它；`planner_views` 两处改成 SQL 按类型筛。保证通道消费者、执行图后续通知按类型分派，遇到未知类型照旧忽略，施工时核一遍。

---

## 四、施工清单第三节怎么改写

总规矩不变："每批结束时该批的表在 v3 报告里由'未覆盖'变'一致'"。B 下，**机制本身在一批里做完（并进 G-2 开头），G-2～G-5 剩下的是逐批核对、处理'静默改动'、修与重放无关的原子性缺陷、两库边界和崩溃切点。**

### G-2　规划与计划（改为：整行变化机制 + 规划与计划各表；3 天）
- **新增（机制）**：第三节第 1～8 条全部；清单第 3 版：会被改的表加 `owner`（两张）和可选的 `silent_ok`，删 `rebuilder`，折叠表"时间只核先后"作废；`check_inventory` 加"折叠表有显式主键""没有 `mission_id` 的必须写 `owner`"两条。
- **保留**：`PlanningDecisionEvaluated` 的幂等键改成"决定编号 + 序号 + 状态"（重放已不受影响，但同键会让第二次评估的领域事件被吞，消费者看不到，同时会触发"静默改动"）；"由计划网络文档推出的只增行与网络文档一致"的核对（这是 TaskGraph §10.3 的图重建，与 B 无关）；`initialize_root` 里根任务语义、要求第 1 版仍由点名覆盖。
- **作废**：`MissionCreated` 补实际生效预算 / 成功标准 / 协议 / 领域 / 策略、取消与失败补终报；`TaskCommitted` 补完整任务；义务建行补签名、燃料、谱系、授权；`planning_requests`、`planning_human_requests`、`plan_revisions`、`method_instances`、收敛作业的"建行补字段"；新事件 `PlanningDecisionRecorded`（由第三节第 6 条按"补信号或写 `silent_ok`"决定，不再带正文）；`initialize_root` 的"义务建行事件带整行"（义务表已被整行事件记下）。
- **改为**：收敛作业 `WAITING→WAITING`、`propose_action` 再投递分支（含同分支写的规划来源链接）：按第三节第 6 条处理。
- 用例：保留 `::test_planning_and_plan_tables_rebuild_after_repair_and_amendment`、`::test_a_second_evaluation_of_the_same_decision_is_not_swallowed`；新增机制用例（都在 `T/product_world/test_business_replay.py`）：
  - `::test_a_row_changed_back_and_forth_is_logged_every_time`（同一行 A→B→A→B→A，每次都有一条改动、链连续）；
  - `::test_rows_inserted_and_removed_in_one_transaction_leave_no_trace`（同一事务先插后删；外加一次保存点回滚）；
  - `::test_an_idempotent_replay_adds_no_row_events`（同一命令重放，事件条数不变）；
  - `::test_an_out_of_band_write_breaks_the_chain`（另开连接改一行，再走一次正常改动，v3 报不一致）；
  - `::test_a_silent_change_is_reported`（测试事务里只改一行任务表、不发领域事件，v3 报不一致；写了 `silent_ok` 的表放行）；
  - `::test_rows_are_owned_by_their_declared_mission_while_other_missions_are_live`（两个没结束的任务，其中一个在登记做法的事务里写了没有 `mission_id` 的行，归属正确，见第五节）。
  - 在 `T/full_target/test_between_cycles_host_duty.py`（现有水位用例所在文件）加一条：只续租的空转轮水位不动。
- 只跑：`test_business_replay.py`、`test_business_replay_inventory.py`、`test_between_cycles_host_duty.py`、`test_requirements_amend.py`、`test_repair_replace_method.py`。

### G-3　审阅与有效性（改为 1 天）
- **保留**：根终审切包 `RootReviewCoordinator.cut` 写包与发 `HierarchicalRootReviewCut` 并成一个事务。这是原子性缺陷，与重放口径无关；B 下点名是自动的，包永远"有主"，所以断言改成下面 G-07 的写法。`VerificationLayerRecorded` 键改成"结果:层:序号"（理由同 G-2 的 `PlanningDecisionEvaluated`）。
- **作废**：`goal_resolutions` 补结论全文、`claims` 建行与各次改动补列、`knowledge` 补正文、`VerificationLayerRecorded` 带完整明细（只留改键）、`bump_epoch` 补 `bumped_by` 明文。
- **新增**：用例里核"屏障触发器改 `validity_epochs` 保证通道行时被整行事件记下"（R11①）。
- 用例：`::test_review_and_validity_tables_rebuild_after_root_rejection_and_observation_flip` 保留。
- 只跑：同原清单。

### G-4　执行、记账与两库边界（改为 2 天）
- **保留**：两库对照（只读）；K04、K11、K12 三条切点照原表执行，断言里的"每个 `usage_ref` 只有一条 `UsageImported`"改成"每个 `usage_ref` 在整行事件里只有一次'未知→已知'以外的写入"；`artifacts` 的迁移改写只作用于旧库（例外类）。
- **改为**：`UsageImported`。裁决 G-7 要的"事件带消费回执（编号 + 当时按多少算 + 是否未知）"已经由 `imported_usage` 的整行事件做到，不另起一份内容。导入所在的事务里如果已经有清单列出的记账事件，就不加；没有的，按第三节第 6 条补一条只带 `subject_id` 和编号的 `UsageImported`，或者写 `silent_ok`。`grow`、尾部预留的建立与释放同样处理（原来要新增的 `BudgetReservationGrown`、`BudgetTailHeld/Released` 不再必加，也不带内容）。
- **作废**：`BudgetReserved` 补账户与工具调用数；`MissionCreated` / `TaskCommitted` 补完整限额；`AttemptCreated`、`ResultSubmitted`、`ActionProposed`、`ApprovalRequested` 等建行事件补整行；`artifacts` 建行补整行。
- 用例：照原清单（K04、K11、K12、`::test_execution_and_budget_tables_rebuild_with_an_operation`）。
- 只跑：同原清单。

### G-5　保证通道与回执账（改为 1 天）
- **保留**：`record_review_interruption` 的回执与事件写在同一事务里（不然回执点名会落到部署时间线）；迁移补写只作用于旧库（例外类）。
- **作废**：`AssuranceCloseoutEvaluated` 补评估正文（`assurance_closeouts` 已被整行事件记下）。注意 `AssuranceCloseoutEvaluated` 是"只是观察"类事件：定期收尾重评时如果行没变，按第三节第 2 条不记，不会让事件每轮增长。
- 用例：照原清单。

### G-6（不在本单范围，给建议）
做法、策略等全局表也走同一机制，归部署时间线，"全局"一节同样用通用折叠。这样 `MethodLibraryAttributed` 就不必带内容；`MethodLibraryCleared` 按第三节第 6 条决定留不留。G-13、G-14 相应改成"全局表的改动没被记下 / 静默清空被报出"。G-6 开工时按此做，属本裁决的自然延伸，不另开偏差单。

### 改坏条目（`T/acceptance_assets/mutations.json`）

| 编号 | 原意 | B 下 | 改成什么 → 由哪条用例抓到 |
|---|---|---|---|
| G-05 | 规划决定进度分支不发事件 | **改写** | 临时触发器只装插入、不装更新 → `::test_planning_and_plan_tables_rebuild_after_repair_and_amendment`（折叠结果与库不一致） |
| G-06 | `MissionCreated` 写请求预算而不是生效预算 | **作废原意，编号改用** | 幂等键去掉"追加前序号"，只留内容摘要 → `::test_a_row_changed_back_and_forth_is_logged_every_time` |
| G-07 | 根终审切包与点名分两个事务 | **保留，改断言** | 同原意 → 用例在两次写入之间注入崩溃：不得留下"本事务没有领域事件的包"（第三节第 6 条报"静默改动"） |
| G-08 | `VerificationLayerRecorded` 键改回"结果:层" | **保留** | 同原意 → 第二次验证的领域事件被吞，事务成了静默改动，G-3 用例抓到 |
| G-09 | `import_usage` 不发 `UsageImported` | **改写** | 提交前不跳过前后哈希相同的行 → `::test_an_idempotent_replay_adds_no_row_events` |
| G-10 | "未知→已知"覆盖不发事件 | **改写** | 提交钩子按"本事务碰过的键"记，不再核"之前存在、现在存在"（先插后删的行被记成存在 / 删除漏记）→ `::test_rows_inserted_and_removed_in_one_transaction_leave_no_trace` |
| G-11 | K12 的导入写在故障边界之外 | **保留** | 同原意 |
| G-12 | 审阅被打断的回执移回事件事务之外 | **保留** | 同原意 |
| G-18 | `PlanningDecisionEvaluated` 键改回"决定编号" | **保留** | 同原意 → `::test_a_second_evaluation_of_the_same_decision_is_not_swallowed`（静默改动 + 消费者少一条） |
| G-19 | — | **新增** | v3 不核链（不比 `before` 与折叠当前值）→ `::test_an_out_of_band_write_breaks_the_chain` |
| G-20 | — | **新增** | `_durable_watermark` 把 `RowsWritten` 当进展 → `test_between_cycles_host_duty.py` 新用例 |
| G-21 | — | **新增** | v3 不核 `with_events` → `::test_a_silent_change_is_reported` |
| G-22 | — | **新增** | 归属判断把触发器写的 `AssuranceEvidenceChanged` 也算进"本事务的任务" → `::test_rows_are_owned_by_their_declared_mission_while_other_missions_are_live` |

G-17 照改名后的事件保留。G-8 第 3 条"改坏执行器跑 G-01～G-18"改为"G-01～G-22"。

---

## 五、附带发现（第 1 批已有，本批一并修）

1. **只增行的兜底归属会被全局唤醒事件搅乱。** `source_records.install` 的临时事件触发器把本事务里**所有**事件的任务都记进 `source_tx_missions`（只排除 `SourceRecordsWritten`）。迁移 41 的全局触发器在登记做法（规划提交同一事务里的 `register_method`）时，会给每个没结束的任务各写一条 `AssuranceEvidenceChanged`。只要同时有两个以上没结束的任务，"本事务唯一的任务"就不成立，没有 `mission_id` 的只增行就会落到部署时间线。`planning_admission_checks` 正好在规划提交事务里写。`verify_mission` 对没有 `mission_id` 的表只看本任务点名的行，`verify_library` 只核"被点名一次"，所以**两边都报一致，这个错不会被发现**。G-1 的用例都是单任务，所以没抓到。另外，裁决 G-1 原文是"没有 `mission_id` 的只增表**按清单写明的关联键**找归属"，实现改成了"本事务唯一任务"兜底，这本身就偏离了裁决。**改法**：①临时事件触发器排除触发器自动写的 `AssuranceEvidenceChanged`；②`input_manifests`、`approval_decisions`、`planning_admission_checks`、`budget_tail_transfers` 照 G-1 原文在清单写 `owner` 关联；"本事务唯一任务"兜底只留给回执账（部署级回执本来就该落在部署时间线）。用例与改坏见 G-22。
2. **事务外的写入会串到下一个事务被点名**（R8），第 1 批同样存在，随第三节第 5 条一起修。
3. **`SourceRecordsWritten` 已经在对外事件页和水位里露出来了**（R13），随第三节第 8 条一起修。

---

## 六、工期

G-2 3 天（含机制）+ G-3 1 天 + G-4 2 天（两库边界与三条切点不变）+ G-5 1 天，合计约 7 天；原估 9.5 天。再加 G-0、G-1 已用约 3.5 天，G-6 1 天、G-7 2 天、G-8 2 天，**G 合计约 11～13 个工作日**（原报 15～18 天）。比偏差单估的"省约 7 天"少，因为第三节第 2、5、6、8 条和第五节第 1 条是偏差单没算进去的。

---

## 七、要改的文字

### 施工清单 `HTN补齐-阶段G-开工裁决与施工清单.md`
- 裁决 G-1"会被改的表"一段末尾加："（偏差裁决 1 改：由存储层统一记'整行变化'，见 `HTN补齐-阶段G-偏差裁决-1.md`；领域事件不再要求带整行，内容不作为重建依据。）"
- 第三节 G-2～G-5、改坏条目按本文第四节改；第四节"不做"里"事件里复制整行"一行改为"领域事件里复制整行（会被改的表由存储层记整行变化，只增表只点名）"；G-11 与第三节末"工期合计"改为 11～13 天。

### 主计划第二节"阶段 G 开工裁决新增的偏离"第一条后加一条
> - 会被改的业务表不逐表补领域事件：由存储层在每个事务提交前统一记"整行变化"（与只增表点名合为一条 `RowsWritten`，带改前/改后哈希和改后整行，幂等键含追加前序号，空改动不记）；重放按序号接链、通用折叠、逐列精确比对；领域事件只作消费者信号，内容不作为重建依据，另核"改了业务行的事务必须有本任务的领域事件"（确属内部记账的写明理由放行）。依据原计划 v1.4 §16.1；裁决见 `HTN补齐-阶段G-偏差裁决-1.md`。

### 主计划修订记录（第 3.21 版，可直接粘贴）
> - **第 3.21 版（2026-10-04）**：阶段 G 偏差裁决 1（`HTN补齐-阶段G-偏差裁决-1.md`）改入：会被改的 25 张表不再逐表补领域事件、逐表写折叠函数，改由存储层统一记"整行变化"，与第 1 批的只增表点名合为一条 `RowsWritten` 事件（带改前/改后哈希与改后整行；幂等键含追加前事件序号；空改动、同事务先插后删不记）；归属只认本行 `mission_id` 或清单写明的关联，不按"本事务唯一任务"兜底（顺带修第 1 批全局唤醒事件搅乱归属的隐患）；v3 改为一个通用折叠，逐条接链并逐列精确比对，折叠表"时间只核先后"作废；新增"无静默改动"秩序检查（改了业务行的事务须有本任务领域事件，内部记账写理由放行）；空转水位、对外事件页、规划视图不把记账事件当进展或展示。施工清单 G-2～G-5 的"领域事件补整行"各项作废，改坏 G-05、G-06、G-09、G-10 改写，新增 G-19～G-22；G 工期改为 11～13 个工作日。

---

## 八、需要问用户的

**无。** 都是技术口径，不碰用户已定的范围、产品决定和硬约束。告知用户一句即可：G 工期从 15～18 天降到约 11～13 天。
