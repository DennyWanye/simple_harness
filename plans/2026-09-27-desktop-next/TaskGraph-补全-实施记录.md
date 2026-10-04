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

## 第二批　小清理

### 二-1　改动
- **删试用次数**：`registry.py` 的 `note_trial_use` / `trial_uses` / `MethodCandidate.trial_uses` 与计数表、`taskgraph_plan_sources.py` 规划来源摘要里的 `trial_uses` 删除；用例 `test_htn_novel_method_admission.py` 两条计数用例合成一条"候选标明是试用范围"。
- **删需求方读取接口**：`TaskGraphStore.read_active_consumers` 删除；来源表 P15 的差异说明改写（偏离 22）。
- **写入目标默认规则**：核实无误、不改代码——没有显式规则时 `hierarchical_dispatch.target_rules_policy()` 给出 `TASK_WORKSPACE_V1`，启用执行图时 `taskgraph_policy_sources.installed_policy` 把它按内容摘要冻结成 `target_policy_ref`，读不到层级派发或部署策略即 `SourceUnavailable('taskgraph_installed_policy_source_missing')`。偏离 23 定稿。
- **"有没有绑定执行图"只留一个判断**：`taskgraph_enabled` 删；`require_bound` 是唯一的判断，没绑定抛 `NotBoundError`、内核版本不认抛 `KernelUnsupportedError`（都是带码异常，登记在一轮故障表里，按"数据损坏、按名停"）。7 处调用：
  - 补记账扫描（`accounting_recovery._import_hold`）、结清已核清动作（`taskgraph_action_settlement.settle_resolved_actions`，顺带改成逐任务包进一轮故障边界）、保证通道内容审批扫描（`deployment/duties.py`）、执行图读页面（`api/taskgraph._require_enabled`，答"此任务没有执行图"）、启动时跳过不服务的任务（`event_handler._domain_unreadable`）、停任务时的预留处理（`_prepare_terminal_ledger`）、关口（`_refuse_unsupported_contract`，按名停 `unsupported_unbound_mission`）——都调 `require_bound`，不该报错的捕获 `NotBoundError` 照原意处理。
  - 用例 `test_terminal_event_transaction.py` 对已不存在的 `taskgraph_dispatch.taskgraph_enabled` 打桩的那一行删掉（桩本来就没有作用）。
- **计划完整性停止也停"已创建"的任务**：`_plan_integrity_stop` 遇到还在"已创建"的任务先进规划再停（与关口同样做法）。阶段 G 记下的老失败 `test_a_mission_whose_root_meaning_does_not_read_back_stops_through_the_planner_branch` 由此修好。
- **删做法建议**：`registry.suggest_for`、`SuggestionReason`、`MethodSuggestion` 与提做法上下文的 `suggested_method_refs` 删除。核实：它列的是本任务里的旧版本做法（规划器引用就被判"做法过期"、白丢一轮，2026-10-03 产品同形世界跑出来过）和已暂停 / 不可取用的做法；跨任务的做法目录在阶段 C3 已有，按"一条路径"删。`statement_similarity` 随第三批删 `SharedGoalIndex` 一起删。

### 二-2　与方案的施工差异
- 方案写"全局扫描逐任务包进一轮故障边界，一个没绑定的老任务只报它自己"。实跑发现：没绑定的老任务会被扫描每轮报一次一轮故障（它没结清的预留永远在，每轮都扫到），而且改由一轮故障那条路停下，不再是关口按名停——两条路停同一件事，日志每轮一条警告。原来那几处"跳过"其实是有意的决定（没绑定的预留永远不按零用量结清），不只是宽容老数据。所以改成：判断只剩 `require_bound` 一个函数，扫描捕获带类型的 `NotBoundError` 照原意跳过，按名停只走关口一条路。方案的本意（删 `taskgraph_enabled`、一个判断、不连累别的任务）做到了。

### 二-3　用例与改坏
- 相关定向用例 266 条：265 过；1 条老失败（`p35/test_provider_accounting_loop.py::test_a_call_queued_for_the_only_slot_is_unbilled_and_a_cancel_never_hands_it_off`，主分支同样失败，与本批无关）。
- 改坏 TG3-02（补记账扫描不再捕获"没绑定"）KILLED：老任务改由一轮故障停、不是关口按名停，`test_unbound_legacy_mission.py` 变红；该用例文件头的改坏说明同步改写。TG3-03（计划完整性停止不处理"已创建"）KILLED。
