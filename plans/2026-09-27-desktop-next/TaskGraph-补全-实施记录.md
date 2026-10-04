# TaskGraph 补全 — 实施记录

方案：`TaskGraph-补全-方案.md` 第 3.2 版（评估 `TaskGraph-补全-方案-评估-1.md`、`-评估-2.md`）。分支 `tg-1`（worktree `simple_harness-a4`），基于 main `e2d869a3`（SDK opt.150）。用户 2026-10-04 同意开工。

方案评估做了两轮：第 1 轮阻断 5、重要 11、建议 5，全部改入（第 3.1 版）；第 2 轮只核阻断，第 1 轮的 5 条到位，又引出 5 条小阻断，照评估员给的文字原样改入（第 3.2 版）。按"核验最多两轮、只挡大错"不开第三轮，第 3.2 版即施工依据。

## 第一批　错误码表第二步：一轮故障按类型码处理

### 一-1　改动
- `SDK/contracts/error_table.py` 新增一节：`RoundFaultHandling`（`CORRUPT_STOP` / `RETRY_IN_PLACE`）、`RoundFaultCode`（8 个码）、表 `ROUND_FAULTS`、带码异常的共同基类 `CodedFault`、`round_fault_handling(error)`——只看异常对象上的类型码：登记过的按表；没带码、或带了没登记的一律原地重试（码照实带出）。
- 清单只追决定秩序的码：原来 `classify_round_fault` 按异常文字包含判"损坏"的 5 项，加上原来靠 `isinstance(GraphIntegrityError)` 判损坏的执行投影排不出先后（`projection_not_orderable`）和计划意思读不全的两种（`semantic_binding_missing`、`root_not_identified`）。其余内部码只作消息，不改变秩序，不归类。
- 带码异常：`storage/taskgraph_store.GraphIntegrityError`（历史完整性）、`graph/projection_validation.GraphIntegrityError`（投影排不出先后）、`storage/taskgraph_attempt_inputs.AttemptInputIntegrityError`、新 `storage/taskgraph_history_sources.SourceIntegrityError`、新 `storage/store.StoredResultCorrupt`（原来是一句"corrupt stored result"的 `StoreError`）、新 `orchestrator/event_handler.ServiceTurnIdentityMismatch`（两处）。`hierarchical_dispatch.PlanIntegrityError` 的码按实例给，构造时转成登记过的 `RoundFaultCode`，没登记当场报错。异常文字原样保留（只给人看）。
- `orchestrator/failure_classes.classify_round_fault` 改为返回 `(CORRUPT|RETRY, 码)`，只调 `round_fault_handling`，删 `_CORRUPT_CODES`；一轮故障事件 `MissionRoundFault` 加 `code` 字段。计划完整性停止的报告 `detail.code` 改为直接取类型码（原来对非投影错误是按文字冒号切出来的）；投影错误的码沿用 `projection_not_orderable` 字面，执行图那两处读它的地方不变。

### 一-2　用例
- 新 `T/full_target/test_round_fault_codes.py` 4 条：表全集；源码扫描（基类里有 `CodedFault` 的类必须写 `code = RoundFaultCode.<登记过的名字>`，扫到 8 个）；扫描能抓住没登记的码与没写码的类；分类函数源码不读异常文字，行为三种（登记的损坏码 → 当轮停；文字里写着损坏码、对象上没码 → 原地重试；带未登记码 → 原地重试并带出码）。
- `T/product_world/test_round_faults.py`：启动绑定那条用例原来抛一句带码文字的普通异常，改为抛 `ServiceTurnIdentityMismatch`；"计划历史损坏只停它自己"那条把等主循环结束时的异常先记下、先断言任务状态，再断言主循环没崩（否则改坏时报的是等待超时、执行器判"无效"）。
- 相关定向用例 368 条：367 过；1 条是阶段 G 就记过的老失败（`test_hierarchical_event_flow.py::test_a_mission_whose_root_meaning_does_not_read_back_stops_through_the_planner_branch`，任务停在"已创建"，主分支同样失败）。
- 改坏 TG3-01（表里"历史完整性"改成原地重试）KILLED。改坏状态下任务按原地重试走、要 2 分钟才按上限停，用例只等 30 秒——查过日志，没有出错漏出按任务边界。
