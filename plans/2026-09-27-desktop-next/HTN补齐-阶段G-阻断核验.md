# HTN 补齐阶段 G 阻断核验（只读）

核验对象：分支 htn-g，`165dd8b4..f9646e03`（SDK src、Host、前端）。只报阻断级问题。没有跑测试，只用 grep、读代码，并在只读副本上查了表结构和事件分布（开发数据副本 root2：2026-10-01，1.5GB；本阶段大语料库 r47）。

## 结论：2 个阻断项（都是"很可能"）

### 阻断 1（很可能）：Host 任务诊断改成扫全库，扫描期间整个后台卡住

- **在哪**：`backend/deskpet/orchestration/diagnostics.py:351` 调 `verify_library(store)`。`service.py:1442` 在 `store.read_view()` 里同步执行这一步，`handlers.py` 把它直接放在 asyncio 事件循环上跑，编排主循环也在同一个循环上（`service.py:244 create_task`）。被调用的 `business_replay.py:261-276 verify_library` 会做三件事：
  - 解析全库每一条 `RowsWritten`；
  - 对所有点名表（含 `commit_receipts`）的每一行调一次 `row_identity`，每行发 2 次 `PRAGMA table_info`、算一次规范 JSON 哈希；
  - 再跑一遍 `verify_global`。
- **触发条件**：用户在界面打开某个任务的诊断（或导出支持包）。阶段 G 之前这一步只读单个任务的数据（`coverage_report`、单任务事件），现在扫的是整个库。
- **后果**：开发数据副本里 `commit_receipts` 已有 216 万行，绝大多数是保证通道时钟回执 `assurance-clock:*`。主循环只要有没结束的任务，每一轮都会写一条时钟回执，阶段 G 起每条回执还会在部署时间线上多一条 `RowsWritten`（r47 里部署时间线有 2.4 万条，各任务只有几百条）。所以扫描时间会随库龄一直涨，在真实库上很可能要几十秒到几分钟。这段时间里主循环、心跳续租、所有 ws 请求都停住，可能导致租约过期、尝试被判丢失。前端 30 秒超时后用户再点一次，又排进一次全库扫描。另外，`unnamed` 列表会把阶段 G 之前所有未点名的行逐条做成字符串，旧库上要多占几百 MB 内存。
- **建议修法**：Host 诊断只调 `verify_mission`。全库一节（`verify_library` / `verify_global`）只留在命令行 `replay` 和导出流程里，而且放在读视图之外、只读副本上、线程里执行（参照导出时 `taskgraph_history` 的做法）。`row_identity` 里的两次 PRAGMA 按表缓存。

### 阻断 2（很可能）：`RowsWritten` 混进"按任务读事件"的通用接口，10000 条截断提前触发、每轮全量解析变重

- **在哪**：`storage/store.py:591` 的 `list_events(mission_id)` 默认 `limit=10_000`，`iter_events` 不过滤类型。偏差裁决 1 R13 只改了 `planner_views` 两处，下面这些仍然按"读前 10000 条再在 Python 里按类型筛"：
  - `event_handler.py:6116/6137`：规划器被拒计数、宽限判断；
  - `root_review.py:557` 与 `hierarchical_dispatch.py:2153/2195`：根审切包、被取代的包；
  - `action_commits.py:1412`：取最新一条 `MissionCriteriaJudged`；
  - `obligation_commits.py:261`：需求准入去重计数；
  - `commit_service.py:1357`、`human_commits.py:610`、`assurance_review_import.py:141`；
  - Host `service.py:1506`。

  另外，主循环每轮对每个到期任务都会跑 `collect_triggers`、`advance_method_reviews`，里面有 5 次以上 `iter_events` 全量扫描，每次都要解析 `RowsWritten`。`RowsWritten` 带的是改后整行，attempts 一行平均约 9KB。
- **触发条件**：长任务。大语料里 `RowsWritten` 约占一个任务事件数的 27%，事件字节数约是原来的 3 倍。开发数据里已有单任务 8.7k、28k 条事件的例子，有了 `RowsWritten` 后，原本 7k 多条领域事件的任务就会越过 10000 条。
- **后果**：越过上限后，这些读取方看不到最新的事件，结果出错：
  - 规划被拒次数停止增长或不再清零：要么一直重试，要么提前判失败；
  - 根审切包与被取代的判断出错：可能重复切包或卡住；
  - 准入去重计数偏少：可能重复写入。

  10000 条截断本来就存在，阶段 G 让它在普通长任务上就会碰到；同时每轮全量解析的数据变成约 3 倍，这正是 09-28 CPU 跑满那类热路径。
- **建议修法**：在 `Store.list_events` / `iter_events` 的 SQL 里默认排除 `RowsWritten`（加一个显式参数 `include_rows_written=False`）。只有 `business_replay` 用自己的 SQL 读它，所以一处改动就能覆盖上面所有调用方。

## 核过、没发现阻断的范围

- **source_records 事务钩子**：
  - 嵌套事务只在最外层 `begin`/记账；保存点回滚（`assurance_work.atomic`、`taskgraph_store._savepoint`、`method_promotion`）会连同临时表一起回滚，前像不会串；
  - 异常路径会 ROLLBACK，下一个事务开头清空临时表；
  - 记账写入 events 后，events 上没有会改折叠表的触发器；
  - 归属方面：除 `owner` 写明的四张表外，业务表的 `mission_id` 都是 NOT NULL；关联父表没有生产删除；
  - 幂等键用的是追加前最大序号，不会撞；
  - 只读打开、备份、迁移这几条路径都不装触发器（`_source_naming` 在迁移之后才置位，`open_readonly` 不装）；
  - 生产代码里没有其他连接写编排库的折叠表。
- **迁移 41**：
  - 删掉的 7 张表，生产代码没有引用（只剩 `htn_schema.TABLES` 常量，仅测试使用）；新库里没有触发器或视图还引用它们；
  - 34 张加了不可改删守卫的表，grep 不到 UPDATE、DELETE、REPLACE 或 `ON CONFLICT DO UPDATE`；唯一一处 DELETE（`method_library_store.clear`）删的是全局折叠表，不在守卫范围内；
  - 全局触发器的终态集合与 `MissionStatus` 一致；`assurance_changes` 也按同一集合放行"没有唤醒事件"的情况。
- **business_replay v3**：
  - `silent_ok` 只豁免"必须带领域事件"这一条，接链、哈希、逐列比对照常做；同一事务里混了非豁免表就照报；
  - 范围外（OUT_OF_SCOPE）不算一致，命令行返回 1，部署身份 NOT_RUN 不当通过；
  - 全局一节对部署时间线用同一个折叠核对；
  - 两库对照中"迟到用量"不计入不一致，但未知用量是按上限记账的，不会少算。
- **行为修复**：
  - 验完提交前完成范围过期 → 归档为被取代：新增的 except 排在旧分支前面，两类异常没有继承交叉，其他错误码照旧抛出；
  - `BudgetTailReleased`：在结清事务内写，键是额度编号，幂等；
  - "还在等原样返回"只在不就绪分支里；`_transition` 不会出现同状态迁移；
  - `put_action` "内容没变不写"：没有读 `actions.updated_at` 的地方；
  - `ActionReconciliationUnavailable`：与改动作行在同一事务；
  - `method_library.clear` 的事件归部署时间线；
  - `VerificationLayerRecorded`、`PlanningDecisionEvaluated` 的新键：没有按旧键格式查找的地方。
- **Host 与前端**：`_replay` 全部用 `_mapping` / `_safe_scalar` 取字段，前端用 `asRecord` / `asList` 和 `count()` 兜底，字段旧或缺时不会崩。
