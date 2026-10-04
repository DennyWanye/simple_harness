# TaskGraph 补全第一、二批 — 完成评估

- 评估对象：分支 `tg-1`（worktree `simple_harness-a4`），基线 main `e2d869a3`，代码提交 `c3a5ad8d`（第一批）、`f62487d3`（第二批）；`git diff e2d869a3..HEAD -- sdk backend tauri-app`（25 个文件，Host 与前端没有改动）。
- 对照：`TaskGraph-补全-方案.md` 第 3.2 版第 1、2 节与第 7 节偏离；`TaskGraph-补全-实施记录.md` 第一批、第二批（含"与方案的施工差异"）。
- 方法：只读代码与文档，**没有跑任何测试**（用例通过情况照实施记录，本次没有复跑）。行号按 `f62487d3`。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`。

## 结论

**需补 3 项**（都小：1 处注释、1 处用例、1 处方案文字）。主体按方案做到了：一轮故障只按异常对象上的类型码查表、不读异常文字；带码异常有共同基类，源码扫描守住"码必须登记"；三种异常的处理与用户 2026-09-28"基础设施故障原地重试"一致；第二批七项都落了。施工差异可以接受。

## 一、第一批逐项

| 方案要求 | 结果 | 代码位置 |
|---|---|---|
| 清单只追决定秩序的码；其余一句话写"只作消息" | 做到 | 8 个原有判断依据（原文字包含的 5 项 + 原 `isinstance` 判的投影排不出先后 + 计划意思读不全两种）进 `RoundFaultCode`；实施记录一-1 写了"其余内部码只作消息，不改变秩序"。`refusal_charges_planner`（`SDK/contracts/error_table.py:63`）与派发拒绝分支本来就按登记过的码查表（读事件里的结构化码，不读异常文字），不用动 |
| `"corrupt stored result"` 改成带类型的码 | 做到 | `SDK/storage/store.py:53` `StoredResultCorrupt`，抛出处 `:1810` |
| 错误码表加"一轮故障怎么处理"一列 | 做到 | `SDK/contracts/error_table.py:163` `RoundFaultHandling`、`:168` `RoundFaultCode`、`:187` 表、`:201` `CodedFault`、`:207` `round_fault_handling` |
| `classify_round_fault` 只看类型码、不读异常文字 | 做到 | `SDK/orchestrator/failure_classes.py:162`，只调 `round_fault_handling`；旧的文字包含表 `_CORRUPT_CODES` 与 `isinstance` 分支删除 |
| ① 登记过的码按表 | 做到 | 10 个码全部是"数据损坏、当轮停" |
| ② 带码没登记：开发时源码扫描变红，运行中原地重试、事件写明码 | 做到 | 扫描 `T/full_target/test_round_fault_codes.py`（基类里写了 `CodedFault` 的类必须有 `code = RoundFaultCode.<登记过的名字>`，另一条证明扫描能抓到没登记的码和没写码的类）；运行时 `MissionRoundFault` 事件新加 `code` 字段（`SDK/orchestrator/event_handler.py:3571`）。`PlanIntegrityError` 的码是按实例给的，构造时 `RoundFaultCode(code)` 当场校验（`SDK/orchestrator/hierarchical_dispatch.py:274`），只有两个工厂函数调用，两个码都登记了 |
| ③ 不带码：原地重试、上限兜住 | 做到 | 同上函数；"到上限按名停"由原有 `T/product_world/test_round_faults.py::test_a_persistent_store_fault_stops_its_mission_by_name` 守住 |
| 抛码处只换成带类型的异常，不改判断逻辑 | 做到 | 历史完整性 `SDK/storage/taskgraph_store.py:71`、投影 `SDK/graph/projection_validation.py:110`、尝试输入 `SDK/storage/taskgraph_attempt_inputs.py:24`、来源 `SDK/storage/taskgraph_history_sources.py:26`、审阅回合身份 `SDK/orchestrator/event_handler.py:221`（抛出处 `:2113`、`:2121`）。异常文字原样保留。查过把异常文字包一层再抛的地方（`raise X(f"...{error}")`），没有包住上述带码异常的，行为没有被悄悄改成原地重试 |
| 计划完整性停止报告的码改取类型码 | 做到 | `event_handler.py:1605`、`:1611`（原来对非投影错误是按文字冒号切的） |
| 用例与改坏 | 做到 | 表全集、源码扫描、扫描能抓住、分类不读文字四条；改坏 TG3-01（`T/acceptance_assets/mutations.json`）登记，实施记录写 KILLED |

## 二、第二批逐项

| 方案要求 | 结果 | 代码位置 |
|---|---|---|
| 删恒为 0 的试用次数 | 做到 | `registry.py` 的 `note_trial_use` / `trial_uses` / `MethodCandidate.trial_uses`（`SDK/planning/htn/registry.py:786`）与计数表删；来源摘要 `SDK/orchestrator/taskgraph_plan_sources.py:162` 不再带。全仓库 0 处残留 |
| 删需求方读取接口 | 做到 | `TaskGraphStore.read_active_consumers` 删；来源表 P15（`T/acceptance_assets/seams_current.json:290`）差异说明改写，现行生产方本来就是三处现行代码 |
| 写入目标默认规则定性 | 做到（不改代码） | 实施记录二-1 核实；偏离 23 定稿 |
| "有没有绑定执行图"只留一个判断 | 做到（施工差异见第三节） | `taskgraph_enabled` 删，SDK / Host / 前端 0 处残留；`require_bound`（`SDK/storage/taskgraph_store.py:58`）是唯一判断，抛带码的 `NotBoundError`（`:47`）/ `KernelUnsupportedError`（`:54`）。7 处调用：`SDK/orchestrator/accounting_recovery.py:262`、`SDK/orchestrator/taskgraph_action_settlement.py:37`（顺带逐任务进一轮故障边界 `:47`）、`SDK/deployment/duties.py:245`、`SDK/api/taskgraph.py:178`（答"此任务没有执行图"）、`event_handler.py:2065` `_domain_unreadable`、`:1703` `_prepare_terminal_ledger`、`:1546` 关口。打桩那一行已删（`test_terminal_event_transaction.py`）。**`SDK/orchestrator/taskgraph_notifications.py` 的过时注释没改**（需补第 1 项） |
| 重查事件集不含"计划修订已提交" | 做到（记偏离 24，不加） | — |
| 删按字面相似的共享建议 | 按方案推到第三批 | `statement_similarity` 仍在 `registry.py:808`，随第三批删 `SharedGoalIndex` |
| `suggest_for` 定去留 | 做到（删） | `suggest_for`、`SuggestionReason`、`MethodSuggestion`、提做法上下文的 `suggested_method_refs` 全删，0 处残留。方案的判据是"阶段 C3 全库目录已按目标类型列出同样内容就删"：全库目录确实按目标类型列（`SDK/orchestrator/method_library.py:183` `directory`）；它列的旧版本做法规划器引用就被判过期，删掉符合"一条路径"，可以接受 |
| 计划完整性停止也停"已创建"的任务（实施记录列在第二批） | 做到 | `event_handler.py:1615`：先进规划再停，与关口同样做法；改坏 TG3-03 登记 |

## 三、施工差异的裁定：可以接受

方案写"全局扫描逐任务包进一轮故障边界，没绑定的老任务只报它自己"；实际做成"扫描捕获带类型的 `NotBoundError` 照原意跳过，按名停只走关口一条路"。

- 合口径：判断只剩 `require_bound` 一个函数，没有第二个"是/否"问法；调用方捕获的是带类型的异常，不读文字。
- 比方案原文更合"一件事一条路径"：照方案原文，没绑定的老任务会每轮被扫描报一次一轮故障并由一轮故障那条路停掉，和关口按名停成了两条路；而且没绑定的预留本来就是有意不按零用量结清（"宁可多算"），跳过是原意，不是宽容旧数据。
- 不连累别的任务这一点仍守住：`T/product_world/test_unbound_legacy_mission.py` 与改坏 TG3-02。
- `NotBoundError` / `KernelUnsupportedError` 在表里登记为"数据损坏、当轮停"，只作万一漏到一轮边界时的兜底；关口每轮先于其他工作运行（`event_handler.py:3689`），正常走不到。不算第二条路。

但按 2026-10-03 的规则（冲突先改方案再写代码），方案文字和用例写法还停在原文（需补第 3 项）。

## 四、需补项

1. **过时注释没改**：`SDK/orchestrator/taskgraph_notifications.py:79` 仍写 "Events before the explicit seed/captured baseline are outside coverage."——"迁入基线"阶段 A 已整删，方案第二批"绑定判断"那一行要求顺手改掉，两次提交都没碰这个文件。
2. **结清已核清动作的扫描新加的逐任务边界没有用例守住**：`SDK/orchestrator/taskgraph_action_settlement.py:47` 把每个任务的那一份包进 `_round_boundary`，这是新行为；但 `T/product_world/test_round_faults.py::test_a_fault_in_a_global_scan_is_one_missions_round_fault` 只参数化了执行图通知与保证通道两处，`mutations.json` 也没有对应改坏。方案的改坏要求是"去掉逐任务边界 → 变红"。补法：给这条参数化用例加一处 `action_settlement`（或另加一条改坏）；若认为不值得，就在实施记录里写明并删掉这层边界，不要留一段没人守的新代码。
3. **方案文字没随施工差异改**：`TaskGraph-补全-方案.md` 第 2 节"有没有绑定执行图"一行还写"全局扫描逐任务包进一轮故障边界……改坏：去掉逐任务边界 → 变红"，第 7 节也没有对应偏离。应把该行改成现行做法（扫描捕获 `NotBoundError` 跳过、按名停只走关口、改坏 TG3-02），并在第 7 节补一条偏离（第 27 条）。

## 五、看到但不算需补

- `event_handler.py:1626` `_plan_integrity_stop` 最后一支注释还写 "or not yet planning"，"已创建"现在已在上面处理，注释略旧，不影响行为。
- 源码扫描只认直接把 `CodedFault` 写进基类的类；子类在实例上给码（`PlanIntegrityError`）靠构造时校验兜住，目前只有这一个，够用。
- `SDK/deployment/duties.py:245` 外层仍是 `except Exception`（原来就是），内核版本不认的任务也会被当成"不走保证通道"跳过；与改前行为相同，不是本批引入。
