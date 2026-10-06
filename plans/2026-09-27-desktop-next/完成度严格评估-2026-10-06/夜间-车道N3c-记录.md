# 夜间车道 N3c 记录（2026-10-07 夜）

工作树：`simple_harness-n3c`，分支 `night-n3c`，基线 main `47bf424a`。没有推送。
条目：N3-24、N3-10、N3-22、N3-01、N3-26。施工说明见主检出 `夜间-N3-建议裁决.md` 第二节。

## 一、逐条状态

| 条目 | 状态 | 提交 |
|---|---|---|
| N3-24 配额调大后启动即生效 | 完成 | `3404424a` |
| N3-10 删义务账本死存储层 | 完成 | `6c4d756d` |
| N3-22 车道 O 注释与目标用例收紧 | 完成 | `cdc93aa8` |
| N3-01 死锁停止分支补用例 | 完成，没有发现缺陷 | `09316c44` |
| N3-26 F04 做法差异登记 | 完成 | `531a1eee` |

每个改了 SDK 源码的提交都重生成了部署清单（`taskgraph_deployment_manifest.json`）。

## 二、N3-24 配额调大后启动即生效

改动：
- `CommitService.__init__` 末尾调 `_follow_global_quota_on_start()`：账本里已有的每个全局账户（`is_global_account` 判），上限设成当前配置的月配额。
- 建任务那处同步保留，和启动同步调同一个小函数 `_follow_global_quota()`。
- 注释、`ARCHITECTURE/AGENT_ORCHESTRATION.md` 第 11 行、设置页说明文字改成"重启后立即生效"。

取舍：同步全部月份，不只当月。理由：
- 按任务建立月份计（车道 P）。上月建立、还在跑的任务仍在上月的总账里花。只同步当月，这个任务还会被旧上限挡住。
- "全局账户上限 = 当前配置的月配额"只留这一条规则，不按月份分两种对待（同一件事只留一条路径）。
- 配额调小时也照同一条规则：余额可能为负，之后的预留被拒（多算方向）。

Host 核对：设置页只读，月配额在 `config.toml` 里改、重启 Host 生效。重启会新建编排服务和 `CommitService`，启动同步能覆盖。没有改 Host 代码。

用例：
- SDK `tests/orchestrator/test_batch2_p_global_monthly_budget.py`：原"下一个新任务才跟上"用例改为启动即跟上（9 月、10 月两个账户都跟上；原任务被旧上限挡住，重启后不建新任务就能接着花；调小也跟上）。另加一条"建任务处同步仍在"。5 条全过。
- Host `backend/tests/orchestration/test_global_budget_setting.py` 加一条：调大配额重启，不建新任务，`status().global_budget.max_tokens` 与账上上限一致。用工作树 SDK 源码跑 4 条全过；用 Host 装的 opt.166 轮子跑，新用例红（符合预期，要等发版）。

改坏：删掉启动同步那一行 → 新 SDK 用例红（1 条）。恢复后全绿。

## 三、N3-10 删义务账本死存储层

先查：`ShapeChange`、`note_shape_change`、`expansion_keys`、`shape_changes` 在 SDK `src/`、Host、前端都没有生产写方。诊断导出、前端都不读 `expansions` / `shape_changes`。

发现一处生产读方：`orchestrator/_read_set.py` 的 `obligation_state` 拿 `account.shape_changes` 当义务的语义版本。库里读出的视图这一项恒为 0（`ObligationStore.account` 不传它）。改成常量 0，内容哈希照旧。行为不变。

删除：`ShapeChange`、`note_shape_change`、`_Account.expansion_keys / shape_changes`、`ObligationLedger.expansion_keys / shape_changes`、视图字段与 `to_json` 两项、`obligation_store.persist` 的守卫。`BoundReachedReport.expansions` 是另一件事，保留。迁移文本（`obligation_shape_changes` 表名）不碰。

用例：
- `test_obligation_store.py`：删 `note_shape_change` 那段。
- `test_obligation_conservation.py`：删"被拒的形状变化不记"整条（测的方法已删）；"读完父账后父账变了"改用 `admit_demand` 让父账变。
- `test_readiness_reasons.py`：视图构造去掉两个字段（施工说明没列这份，同一次改了）。
- `test_batch2_i2_rounds_and_dead_code.py` 的源码扫描加 `ShapeChange`、`note_shape_change`、`expansion_keys`、`shape_changes`。
- 结果：上面四份加 `product_world/test_epoch_and_read_set.py` 共 233 条全过。

改坏：在 `obligations.py` 加回 `note_shape_change` → 源码扫描红。恢复后绿。

## 四、N3-22 车道 O 注释与目标用例收紧

注释与文档串：`event_handler.py` 里 `_decide` 调 `_decide_actions` 处的注释、`_decide_actions` 文档串，改成"批准撤销 / 过期在这里停；动作被拒绝 / 没生效归系统操作线（`system_operations`：重交、问规划器或具名停）"。只改了注释，没有碰隔离相关段。

用例 `tests/orchestrator/product_world/test_effect_unknown_closeout.py` 目标用例：
- `action_state` 收紧为 `== "UNKNOWN"`。
- 空转 5 轮前后，尝试数与意图数不变；空转前意图数恰好是 1（没有重交）。
- 整份 6 条全过。

查实："效果在途时不判定"的条件不在 `_decide_actions`，在 `hierarchical_dispatch.root_review_ready`（效果没落定，根审阅不开）。

改坏：
- A：`root_review_ready` 不看效果是否落定 → 判定时动作状态是 `AWAITING_APPROVAL`，收紧的断言红。原来的宽断言在这个改坏下会过。
- B：结果不明时当作"没生效"重交（同时改 `system_operations._proven_not_applied` 路径和 `operation_materialization._closed_without_effect` 守卫，只在判定之后才重交）→ 意图数变 2，新断言红。
- 只改 `system_operations` 一处时，提交被 `OP_NONAPPLICATION_PROOF_INSUFFICIENT` 拒，意图数不变，用例照过。说明有两道闸。
- 说明："空转前后不变"这一句单独构造不出只在空转期间多派意图的改坏（判定后系统马上就到空转）；它由"意图数恰好是 1"一起守住。

## 五、N3-01 死锁停止分支补用例

用例加在 `tests/orchestrator/full_target/test_wait_for_deadlock.py`：
- 借 `test_stall_asks_planner_first.stalled` 的产品同形停滞局面。用 monkeypatch 替换 `scheduling.wait_for.collect_wait_facts`，注入一张有环的图。
- 直接驱动：记停滞 → 确认（先问规划器）→ 再记停滞 → 确认 → 停。
- 断言：问过规划器一次；`stop_reason == "deadlock"`；`detail.wait_for.code == "resource_wait_cycle"`；`cycles[0].members` 是注入的环；交给规划器的请求也带同一份环的事实。
- 对照：图没有环 → `no_dispatchable_work`，`wait_for.code` 为空。
- 整份 6 条全过。没有改产品代码。没有发现缺陷。

改坏：停止处三元式两支对调 → 两条新用例都红。恢复后绿。

## 六、N3-26 F04 做法差异登记

`00-评估口径与排除清单.md` 第四节加"B 级登记（逐条）"，登记 F04：没有必须项时挂全部准则，没有拒绝整条回复。写了计划原文、现状、裁决出处、理由。没有改代码。
裁决文件还说把同一句补进 `07-补齐清单.md` 的 A32 行。那是 N3-15 的一次文档提交，本车道没有做。

## 七、留给主会话

1. 合并时部署清单会冲突：每个车道都重生成了。合并后再重生成一次。
2. `ARCHITECTURE/AGENT_ORCHESTRATION.md` 本车道改了第 11 行（全局预算）。N3-15 改第 13 行与隔离条目，可能有文本冲突。
3. N3-24 的 Host 新用例要等 SDK 发版后，在装好的轮子上才会过。
4. 设置页说明文字改了（`tauri-app/src/components/SettingsPanel.tsx`）。工作树没有 `node_modules`，前端用例没跑；原用例只断言含 `config.toml`，文字里仍有。
5. 顺手发现（没改）：`BudgetLedger.costs_report` 的 `"global"` 取库里第一个全局账户，不分月份。按月配额后应取这个任务挂的那个月的账户。
6. 顺手发现（没改）：`full_target/test_h1h_commit_guard.py::test_o03_an_unknown_publish_outcome_holds_the_plan_change_back_without_a_revision` 在基线 `47bf424a` 上就红（规划器修复被拒 `OBLIGATION_NOT_OPEN`）。与本车道改动无关；可能在车道 N1 的老红名单里，请核对。
