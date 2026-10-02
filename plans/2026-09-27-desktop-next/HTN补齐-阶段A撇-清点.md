# HTN 补齐 阶段 A′ 只读清点（2026-10-03）

- 起因：`HTN补齐-阶段A撇-方案.md`（第 1 版）被挑战员判"暂不能执行"，要求先做只读清点。
- 做法：只读源码、`grep`/静态导入分析、`git log`；没改代码、没跑测试。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`，`SH/` = `sdk/simple-harness-sdk/src/simple_harness/`，`T/` = `sdk/simple-harness-sdk/tests/`，`Host/` = `backend/deskpet/orchestration/`。
- 工作树状态说明：阶段 A 第 1 条的删除改动在工作树里未提交；本清点按当前工作树读。

## 〇、先说结论

1. **"95 处"实为 48 处真调用**：95 行 = 48 处调用 + 45 行导入 + 2 处定义（两个同名 `taskgraph_enabled`，一个在 `SDK/orchestrator/taskgraph_dispatch.py:31`，会对未知内核版本报错；一个在 `SDK/storage/taskgraph_source_events.py:11`，多查一次表是否存在、不报错——这本身就是"同一件事两条路"）。
2. **48 处分三类：② 等待期合法 10 处，① 不要求执行图 38 处，③ 纯老数据 0 处**（另有 4 处子条件只为老数据存在，见第一节末）。
3. **② 不能改成"未绑定即报错"**：产品新任务在"已写要求书、未绑定"期间就要造第一份规划包——规划请求要先有包，规划授权要先有请求，绑定又要先有授权。造包时读 `scope_epochs`、`occurrence_outcomes` 等，走的正是未绑定那支。改成报错会让产品新任务死锁。
4. **`mission_coverage(assured=False)` 已不存在**：`verification/mission_coverage.py` 在 opt.134（`a23e7660`）随"严格引用选 A"整文件删了。方案里 (c) 这一项要划掉。
5. **测试影响面远大于方案里的 56/37**：SDK 599 个测试文件中有 225 个碰到 (a)(b)(c)(d) 至少一条，共 1697 个用例。按 (a) 计 154 个文件，按 (b) 计 174 个文件。差别来源见第二节第 4 小节。
6. **N4 冻结 runner 已经跑不起来了**：`.local-test-evidence/2026-09-16/a96-grok/run-a96-grok46.py:180` 导入的 `evaluation.appworld_arms` 在 opt.132（`1e968dab`，10-02）已删。本轮要删的入口里没有一个是冻结 runner。
7. **三份要求书构造并不字节一致**：只有一条成功条件时，Host 写 `AllExpr([一条])`，SDK 默认构造写裸 `CriterionExpr`，内容哈希不同。SDK 测试世界用的第三份 `root_review.root_requirements` 和产品完全不是同一份文档。详见第四节。

---

## 一、`taskgraph_enabled` 分支表（SDK src，48 处调用）

三类的定义（按挑战意见）：
- **①不要求执行图**：假分支今天只被"部署没写要求书"的分层任务走到，也就是 SDK 测试/评测的 `install_hierarchical(planning=...)` 世界、AppWorld 分层臂、Host `strict_taskgraph=false`。产品新任务绑定之后永远走真分支。删除前要先把这些调用方迁走；删除后改成"必须已绑定"。
- **②等待期合法**：产品任务"已写要求书、尚未绑定"时也会走到假分支，而且假分支的行为是对的。要保留，改成显式判断 `awaiting_taskgraph`：未绑定且不在等待期才报错。
- **③只为老数据**：假分支只服务开发库里在要求书机制之前建的、未绑定的老任务。

"等待期能走到"的依据：
- `Host/service.py:1240-1261` 在建任务的同一事务里写要求书。
- 规划请求在建意图时就造包，见 `SDK/orchestrator/event_handler.py:4591-4620` 的 `_hierarchical_planner_package`，包里读 `scope_epochs`（4083 行）和 `planner_views.occurrence_outcomes`（`planner_views.py:77`）。
- 授权签发后，`Host/service.py:874-904` `_enable_required_taskgraphs` 才绑定。
- 绑定前，规划意图在 `event_handler.py:4792` 被 `awaits_taskgraph` 挡住、不派发；所以等待期内不会有尝试、审阅、结算发生。

### `SDK/runtime/mission_sources.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 209 | `_worker` | 原生池取执行者请求来源：绑定任务从冻结输入清单取，未绑定直接用意图配置 | ① | 无条件走冻结清单；未绑定报 `REQUEST_SOURCE_STALE` |

### `SDK/storage/taskgraph_source_events.py`（1 + 定义）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 11 | `taskgraph_enabled`（定义二） | 与 `taskgraph_dispatch` 版同义，多查一次表是否存在，不核内核版本 | —（③ 子条件：查表是否存在只为老库结构） | 删，统一到一个定义 |
| 27 | `record_source_change` | 未绑定就不记"来源已变"事件。规划授权在绑定前签发（`planning_admission_store.py:214`），走的就是这条 | ② | 保留跳过；改成"等待期跳过，未绑定且非等待期报错" |

### `SDK/storage/htn_store.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 1568 | `bump_epoch` | 首次抬有效期：绑定任务从 1 起，未绑定从 0 起。**src 里没有任何调用方**，只有测试调 | ① | 固定从 1 起；顺带核实是否整函数只剩测试在用 |

### `SDK/api/taskgraph.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 178 | `_require_enabled` | 执行图读接口：未绑定且有要求书时回 `ACTIVATION_PENDING`，否则回 `NOT_ENABLED` | ② | 保留 `ACTIVATION_PENDING`；`NOT_ENABLED` 只剩"非等待期的未绑定"，可改为内部错误 |

### `SDK/orchestrator/resolution_commits.py`（2）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 632 | `accept_review` | 内容验收时，按真实结果核对冻结输入身份 | ① | 无条件核对 |
| 897 | `_record_accepted_outputs` | 同上，钉住验收来源 | ① | 无条件钉住 |

### `SDK/orchestrator/planning_admission_commits.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 300 | `commit_planning_revision` | 绑定任务提交计划时装执行图参与者；未绑定不装，由 `plan_commits.py:347` 按要求书拒绝 | ① | 无条件装参与者；未绑定一律拒（并入 `TASKGRAPH_REQUIRED_NOT_BOUND`） |

### `SDK/orchestrator/event_handler.py`（13）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 1636 | `_release_unknown_grants` | 绑定或保证通道直接返回；否则释放 `ProviderBudgetGuard` 的挂起额度 | ①（产品是保证通道，假分支随 (c) 一起失效） | 删整段旧释放 |
| 1680 | `_prepare_terminal_ledger` | 任务结束时的账：绑定走执行图读账；未绑定再分保证通道和旧结清 | ②（等待期取消任务时走未绑定→保证通道那段） | 留"绑定 / 等待期→保证通道"两段；第三段旧结清随 (c) 删 |
| 4900 | `_upstream_inputs` | 绑定从执行图上下文取上游输入，未绑定读意图配置 | ① | 只留执行图 |
| 4967 | `_taskgraph_tool_handoff` | 未绑定不做工具交接复核 | ① | 无条件复核 |
| 4978 | `_taskgraph_mount_rules` | 未绑定不给挂载规则（`_bind_workspace` 的 `graph_rules is None` 支） | ① | 无条件取规则；`_bind_workspace` 删 `None` 支 |
| 5712 | `_import_usage` | 绑定或保证通道走执行图读账，否则走旧导入 | ①（随 (c)） | 只留执行图读账 |
| 5766 | `_settle_service_if_known` | 未绑定且非保证通道时，按已知用量结清服务意图（P2.3l） | ①（随 (c)；③ 子条件：无守卫的旧意图） | 删旧结清支 |
| 5848 | `_settle_if_known` | 结算失败时：绑定转为"挂起"，未绑定再抛 | ① | 无条件挂起 |
| 6412 | `_collect_plan_decision` | 已编译决定的续接：绑定时核汇合任务 | ① | 无条件 |
| 6661 | `_collect_plan_decision` | 预准入是否允许汇合预览 | ① | 无条件允许（仍要求装了预览） |
| 7004 | `_collect_plan_decision` | 绑定用已装的图结构预算并走执行图来源；未绑定用默认预算 | ① | 只留执行图 |
| 9102 | `_stop_consecutive_after_handoff_unknowns` | 未绑定先结清已知用量再丢尝试 | ① | 删结清 |
| 9158 | `_give_up_blocked_attempt` | 同上 | ① | 删结清 |

### `SDK/orchestrator/hierarchical_dispatch.py`（8）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 662 | `for_commit` | 绑定用已装的派发对象，未绑定新建一个（调用方：`completion_inputs.py:211` 尝试级、`commit_service.py:3107` `judge_mission`） | ① | 只留已装对象；未绑定报 `TASKGRAPH_DISPATCH_ASSEMBLY_REQUIRED` |
| 746 | `_read_network` | 已有计划时：绑定从执行图历史读网络，未绑定从语义表读（无计划时在 743 行已提前返回种子网络） | ① | 只留执行图历史 |
| 941 | `scope_epochs` | 绑定读 `current_scope_epochs`，未绑定按见证逐个读 | ②（等待期造第一份规划包时读，`event_handler.py:4083`） | 保留未绑定读法，仅限等待期 |
| 992 | `read` | 绑定时读执行图结算视图 | ②（等待期造包、实时视图会读） | 同上 |
| 1082 | `occurrence_outcomes` | 绑定读执行图结局，未绑定读完成状态 | ②（`planner_views.py:77` 造包时读） | 同上 |
| 1130 | `resolved_occurrences` | 无计划时：绑定校验种子网络，未绑定直接返回空 | ② | 同上 |
| 1229 | `resolution_policy_for` | 未绑定且已带有效期就原样返回 | ②（随 `read`） | 同上 |
| 3534 | `build_command` | 计划提交命令带上已装的图结构预算 | ① | 无条件带 |

### `SDK/orchestrator/taskgraph_dispatch.py`（2 + 定义）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 31 | `taskgraph_enabled`（定义一） | 查绑定表；内核版本不对就报错 | —（③ 子条件：版本报错只防旧内核数据） | 保留为唯一定义，另加 `require_bound` |
| 71 | `require_taskgraph_unfenced` | 未绑定直接放行 | ① | 无条件查围栏 |
| 83 | `require_taskgraph_attempt_handoff` | 非尝试或未绑定直接放行 | ① | 只按种类放行 |

### `SDK/orchestrator/accounting_recovery.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 238 | `_import_hold` | 晚到记账：绑定或保证通道走执行图读账；否则无准入指纹的旧意图直接放弃，有指纹的走旧恢复 | ①（随 (c)；③ 子条件：无指纹旧意图） | 只留执行图读账 |

### `SDK/orchestrator/leaf_acceptance.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 449 | `accept` | 绑定时按审阅来源取语义绑定，否则读当前语义 | ① | 无条件读审阅来源 |

### `SDK/orchestrator/taskgraph_requirement.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 66 | `awaiting_taskgraph` | 等待期的定义本身 | ② | 保留；成为 ② 各处唯一判断 |

### `SDK/orchestrator/commit_service.py`（10）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 537 | `require_taskgraph_handoff` | 未绑定直接放行 | ① | 无条件复核 |
| 568 | `_emit` | 终结事件是否同时写执行图终结记录 | ① | 无条件（保留"图外辅助任务"例外） |
| 968 | `_close_attempt` | 未绑定在关尝试时顺手结清 | ① | 删结清 |
| 1442 | `_cascade_stop` | 非保证通道且未绑定时顺手结清 | ①（随 (c)） | 删 |
| 1546 | `create_attempt` | 绑定先经执行图准备冻结输入 | ① | 无条件 |
| 1931 | `rehandoff_service_intent` | 绑定时在事件里多记创建键与输入身份 | ① | 无条件记 |
| 1942 | `rehandoffs_of` | 绑定按全表计次，未绑定按一页事件计次 | ① | 只留全表计次 |
| 2133 | `mark_attempt_lost` | 未绑定顺手结清 | ① | 删 |
| 2165 | `mark_attempt_timed_out` | 同上 | ① | 删 |
| 2235 | `_settle_subject` | 绑定先复核执行者与工具效果 | ① | 无条件复核 |

### `SDK/orchestrator/root_review.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 1351 | `request` | 绑定时给每条贡献附验证证据 | ① | 无条件附 |

### `SDK/orchestrator/taskgraph_action_settlement.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 43 | `settle_resolved_actions` | 遍历所有任务，跳过未绑定的 | ②（等待期任务和已结束的老任务都要跳过；这里是过滤，不是兼容） | 保留过滤 |

### `SDK/orchestrator/plan_commits.py`（2）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 782 | `_check_structure` | 绑定时追加执行图投影检查 | ① | 无条件 |
| 1141 | `_withdraw_retired_demands` | 绑定时扣掉独立需求 | ① | 无条件 |

### `SDK/orchestrator/action_commits.py`（1）
| 行 | 函数 | 分支在做什么 | 类 | 删/留后改成 |
|---|---|---|---|---|
| 1138 | `_resolve_action` | 结清失败时：未绑定抛，绑定改为挂起 | ① | 无条件挂起 |

### 数量汇总

| 类 | 处数 | 分布 |
|---|---|---|
| ② 等待期合法（保留，改成显式等待期判断） | 10 | `hierarchical_dispatch` 5、`taskgraph_source_events` 1、`api/taskgraph` 1、`event_handler` 1、`taskgraph_requirement` 1、`taskgraph_action_settlement` 1 |
| ① 不要求执行图（迁走调用方后删假分支） | 38 | `event_handler` 12、`commit_service` 10、`hierarchical_dispatch` 3、其余 13 处各 1～2 |
| ③ 只为老数据 | 0 | 另有 4 处**子条件**只为老数据存在：`taskgraph_source_events.py:13` 查表是否存在、`taskgraph_dispatch.py:33` 旧内核版本报错、`accounting_recovery.py:243` 无准入指纹的旧意图、`event_handler.py:5772` 无守卫意图的旧结清 |
| 合计 | 48 | 另有导入 45 行、定义 2 处，合计 95 |

补充：
- ① 里有 5 处是"绑定 **或** 保证通道"的组合判断（`event_handler.py:1636/5712/5766`、`commit_service.py:1442`、`accounting_recovery.py:238`）。产品是保证通道，所以这 5 处的假分支在"保证通道必装"（删 (c)）之后就自然死了，不依赖删 (b)。
- 老数据与 ① 走的是同一批假分支，谓词分不出来。按"开发期不兼容旧数据"，③ 不必单独保留。真机前要先取消开发库里未绑定的旧任务（方案第 6 步已写）。
- **可选的另一条路**（只记录，不在本清点里裁定）：如果让绑定发生在建任务时，② 就能整类消失。代价是"启用命令要求先有规划授权"这一前提得改。这要由主会话判断。


---

## 二、四条旧路的使用面

### 1. 四条旧路落在哪些代码上

**(a) 旧执行池与旧检索**
- `SDK/runtime/assembly.py:554-556`：`native is None` 时调 `build_agent_runtime`，权限端口是 `AllowAllAuthorization`。
- `Orchestrator(cfg, provider)` 不传 `profiles`，或者传的 `RuntimeProfile` 不带 `native_plane`，都会走到这里。见 `event_handler.py:465-473`。
- `SH/agents/runtime.py`：
  - 231 行建旧 `SessionRetriever`；
  - 241 行和 380-430 行是 `_RecallAdapter`；
  - 490 行起 `base-agent-index-pump`；
  - 620 行是 `retriever` 属性；
  - 900-904 行注册旧 `SessionHistoryTools`。
- 原生平面下这些也会被建：`SH/agents/arp/runtime.py:258` 调的仍是 `build_agent_runtime`，只换了上下文和历史工具工厂。
- 相关文件：`SH/agents/memory/retrieval.py:97`（旧 `SessionRetriever`）、`SH/agents/memory/__init__.py:8,17`（导出它）、`SH/agents/tools/session_history.py`。
- 两个同名类要分清：原生池用的是 `SH/agents/arp/retriever.py:28` 的 `SessionRetriever`，不在删除范围。`SH/agents/memory/embedding.py`（`HashEmbedder`）有 4 个 ARP 测试在用，也不能连带删。
- 评测里直接用旧运行时的：`SDK/evaluation/htn_single_agent.py:79`。

**(b) 未绑定执行图的分层任务**：即第一节 ① 类的 38 处假分支。Host 侧还有：
- `Host/settings.py:64,138` 的 `strict_taskgraph`；
- `Host/service.py:868` 的开关判断。

**(c) 不走保证通道的旧审阅**（核实后的现状）
- 旧根审阅员：
  - `event_handler.py:10104` `_ask_root_reviewer` 里非保证通道那一段（10138 行之后）；
  - 原始裁决收集（6049、9057 行按 `role == "root_reviewer"` 分支）。
- 任务级裁判：`event_handler.py:10927` `_judge`、`_evaluate_criteria`、`needs_critic`、`CommitService.judge_mission` 和 `criteria_judgment`。另外 `commit_service.py:3107` 为 `judge_mission` 调 `for_commit`。
- 自拟组合记录：`composition_review.py` 非保证通道路径上的 `_record`、`_outcomes`（273 行以后）。
- 自签许可：`composition_review.py` 的 `_witness`（自签有效性见证）；叶子侧还有 `leaf_acceptance.py:719-757` `_record(assured=False)` 本地自组正式记录。
- `mission_coverage(assured=False)`：**已不存在**，opt.134 删了 `verification/mission_coverage.py`。
- 只供旧审阅用的提示词模板：
  - `SDK/runtime/role_templates.py:563` `ROOT_REVIEWER`，只有 `_ask_root_reviewer` 的非保证通道段用；
  - `:78` `CRITIC`（任务级裁判和非保证通道叶子审阅用；保证通道在 `_run_critic` 的 8531 行就转走了）；
  - `:621` `CRITIC_TASK_CONTENT`（同上，只在非保证通道叶子审阅用）；
  - `SDK/runtime/appworld_templates.py:11` `critic-appworld-v1`（AppWorld 分层臂未装保证通道时用）。
- **风险**：`CRITIC` 在 `ROLES` 里，`governance/policies.py:395-411` 和 `governance/promotion.py:94-96` 把 `ROLES` 的提示词版本写进策略身份。删 `CRITIC` 会改策略身份，与"改模板会让已有执行池起不来"是同一类风险，要在发版那步单独核。

**(d) 测试/评测共享世界**
- `Orchestrator.install_hierarchical(planning=...)`（`event_handler.py:1250`），或直接 `HierarchicalDispatch(store, commit, planning=env)`。
- 产品走的是 `install_hierarchical_deployment`（`event_handler.py:1291`），每个任务一份世界，带开工条件。

### 2. 统计口径

- 范围：
  - SDK 的 `tests/**/*.py`（599 个测试文件，`def test_` 共 5384 个），加上 60 个辅助模块；
  - `scripts/**`；
  - `src/agent_orchestrator/evaluation/*.py`；
  - `__main__.py`；
  - Host `backend/**/*.py`。
- 方法：先给每个文件打文本信号，再沿本地导入（辅助模块、被导入的测试模块）传递"世界结构"信号。被导入的测试模块只传结构信号，不传"起主循环"信号。
- 各条旧路的判定：
  - (a)：起了 `Orchestrator(` 但整条导入链上都没有 `native_plane` / `build_arp_runtime`；或者直接调 `build_agent_runtime(`；或者直接碰旧检索符号。
  - (b)：建了分层任务（`install_hierarchical(planning=`、`HierarchicalDispatch(`、`create_mission(` 等），没有绑定（`enable_taskgraph_contract` / `install_taskgraph(` / `production_fixture`），而且经过提交或主循环路径。只建任务行、只测编译和规划的，记为"不受影响"。
  - (c)：没装保证通道（无 `install_assurance` / `_assured_fixture` / `assured_loop` 等），并且直接碰旧审阅符号，或者在 (d) 世界里走审阅。
  - (d)：用 `install_hierarchical(planning=` 或 `HierarchicalDispatch(` 建共享世界。
- 归类规则：
  - 起 `Orchestrator` 的归**主循环**；
  - 不起主循环、手工提交的归**提交层**；
  - 只直接建旧运行时（`build_agent_runtime`）、不碰编排的归**运行时层**，对应方案里"换构造器"的 `arp_fixture`，属于可换构造器一类；
  - **只测旧路本身**的 5 个文件，是逐个读过之后人工指定的。
- 误差：文本信号会有个别误判，估计在 ±10% 以内；逐文件表里的"说明"列给了依据。"混合"指同一文件里既有主循环用例，也有 `build_world` / `committed` / `CommitService(` 手工提交的用例，迁移时要按用例拆分。

### 3. SDK 测试：汇总

| 归类 | 文件数 | 用例数 | 去向 |
|---|---|---|---|
| 主循环 | 102 | 762 | 可换产品同形构造器；其中 22 个是"混合"文件（共 461 个用例），文件里的手工提交用例按提交层处理 |
| 提交层 | 95 | 771 | 不能靠换构造器迁移，只能重写成主循环用例或删掉 |
| 运行时层（换 `arp_fixture`） | 23 | 143 | 换成原生运行时构造器；`tests/agents/` 下 21 个，另有 `tests/execution/test_execution_v9_to_v10_migration.py`、`tests/orchestrator/p33/test_g_workspace_paging.py`，都是直接调 `build_agent_runtime` |
| 只测旧路本身 | 5 | 21 | 随删除一起删 |
| 不受影响（只建未绑定任务行，测编译和规划） | 15 | 347 | 不动；删 (b) 后如果建任务时就要求绑定，要核一遍造行方式 |
| **合计碰到至少一条** | **225**（不含"不受影响"） | **1697** | |

按旧路分（同一个文件可以同时碰几条）：

| 旧路 | 文件数 | 用例数 | 其中 主循环 / 提交层 / 运行时层 / 只测旧路 |
|---|---|---|---|
| (a) 旧执行池与旧检索 | 154 | 1037 | 100 / 27 / 23 / 4 |
| (b) 未绑定分层任务 | 174 | 1471 | 80 / 93 / 0 / 1 |
| (c) 旧审阅 | 141 | 1119 | 72 / 67 / 0 / 2 |
| (d) 共享世界 | 145 | 1182 | 79 / 65 / 0 / 1 |

还有 6 个文件只有部分用例测旧路本身（混在其他用例里）：
- `test_root_review_coordinator.py`：旧根审阅员原始裁决的收集；
- `test_htn_end_to_end.py`：`_judge` 相关用例；
- `test_root_review_evidence.py`；
- `test_root_review_user_goal.py`；
- `test_role_prompts_single_copy.py`：有 1 条钉住 `ROOT_REVIEWER` 只登记一份；
- `test_planner_rounds_in_flight.py`：有 1 处用到 `root_reviewer` 角色名。

辅助模块（不计入上表）共 13 个碰到旧路：
- `decision_loop`（c）、`leaf_world`（bcd）、`knowledge_helpers`（bcd）、`helpers_step07`（bcd）；
- `operation_runtime_fixture`（bc）、`_operation_world`（bc）、`_assured_loop`（abd）、`_subgoal_world`（abd）、`_deploy`（a）；
- `production_fixture`（acd：**已绑定执行图，但用旧执行池、没装保证通道**）、`crash_seed`（acd）；
- `htn_world`、`_method_plan_world`：只是规划世界本身。

### 4. 与方案里"56 个文件 / 37 个文件"的差别从哪来

| 方案里的数 | 方案的口径（推断） | 本清点的数 | 差别来源 |
|---|---|---|---|
| 56 个不装执行图（另 16 个装） | 直接写 `install_hierarchical` 或分层构造的测试文件，按直接文本计 | 直接写 `install_hierarchical(planning=` 或 `HierarchicalDispatch(` 的测试文件 46 个，其中已绑定 2 个；算上导入传递后，(d) 有 145 个文件，已绑定 15 个；(b) 有 174 个 | ①方案只数"直接写"的，没算经 `build_world`、`leaf_world`、`knowledge_helpers`、`helpers_step07`、`htn_world` 等导入的；②(b) 不只来自 (d) 世界：`test_plan_commits`、`test_completion_*` 一类用 `CommitService.create_mission` 手工建分层任务，从来不碰 `install_hierarchical`，但同样经过未绑定的提交分支（碰 (b) 而不碰 (d) 的有 44 个文件） |
| 5 个共用构造器：`build_world` 18、`_assured_fixture` 18、`decision_loop` 12、`htn_world` 11、`test_h1i_production_entry` 6 | 按"谁导入它"计 | 导入 `test_h1i_production_entry` 的有 38 个（测试 + 脚本），`test_htn_end_to_end` 31 个，`htn_world` 29 个，`_assured_fixture` 22 个，`leaf_world` 16 个，`scripted_plans` 15 个，`production_fixture` 14 个，`decision_loop` 10 个 | 方案漏了 `leaf_world`、`knowledge_helpers`、`helpers_step07`、`operation_runtime_fixture`、`scripted_plans` 这几个提交层构造器；`test_h1i_production_entry` 被低估最多 |
| 约 37 个依赖旧执行池 | `build_agent_runtime` 直接调用（33 个）加旧检索（约 4 个） | (a) 154 个 | 方案没算"`Orchestrator(cfg, provider)` 不带原生平面"这种情况：直接写 `Orchestrator(` 的测试文件 82 个，只有 1 个带 `native_plane`。删 `native is None` 后，这 81 个连同经导入使用它们的文件都起不来 |
| 95 处 / 17 个文件 | `grep` 行数 | 48 处调用 / 17 个文件 | 95 里含 45 行导入、2 处定义 |

另外，方案第 5 步说"约 37 个依赖旧执行池的测试，随第 3 步迁到原生池"，但**第 3 步的产品同形世界本身也必须跑在原生池上**。而现有执行图组的 `production_fixture` 用的就是旧池、也没装保证通道。所以 (a) 的迁移和 (b)(d) 的迁移是同一件事，不是两步。

### 5. Host 侧

| 文件 | 碰到 | 说明 |
|---|---|---|
| `Host/settings.py` | b | `strict_taskgraph` 设置项（62-64、138 行） |
| `Host/service.py` | b | `_require_strict_taskgraph`（861-872 行）里 `not self.settings.strict_taskgraph or self._taskgraph is None` 那一支。产品启动一律装分层部署、执行图、保证通道和原生池（302-312、950-956 行），旧池已在 09-30 删掉（301 行注释），所以 (a)(c)(d) 都不碰 |
| `backend/tests/orchestration/test_strict_taskgraph_default.py`（4 个用例） | b | 第 1 个用例钉"显式 false 才关"，第 2 个里 `off`/`uninstalled` 两段测开关关闭，这两部分随删开关一起删（只测旧路本身）；第 3 个（协调员绑定等待中的任务）、第 4 个（等待期说明）测的是 ② 等待期，保留 |
| 其余 Host 测试和代码 | — | 没有调 `install_hierarchical(planning=`、`build_agent_runtime`、旧审阅符号的；`native_plane` 相关测试都是原生池，不受影响 |

### 6. SDK 测试逐文件表

列说明：
- **碰到**：a/b/c/d 对应上面四条旧路，"—"表示只建任务行、不经分支。
- **用例**：`def test_` 的个数。
- **说明**："已装保证通道"表示 (c) 不适用；"已绑执行图"表示 (b) 不适用；"混合"表示文件里有手工提交用例，要拆开处理。

**`tests/agents/arp/`**（1 个文件，5 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_arp_assurance_acceptance.py` | abd | 提交层 | 5 | 已装保证通道 |

**`tests/agents/`**（24 个文件，143 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_agent_close.py` | a | 运行时层 | 9 |  |
| `test_agent_lifecycle.py` | a | 运行时层 | 1 |  |
| `test_agent_open_scope.py` | a | 运行时层 | 5 |  |
| `test_build_agent_runtime.py` | a | 运行时层 | 8 |  |
| `test_cancel_turn.py` | a | 运行时层 | 9 |  |
| `test_context_journal.py` | a | 运行时层 | 12 |  |
| `test_context_real_provider.py` | a | 运行时层 | 1 |  |
| `test_create_many_batches.py` | a | 运行时层 | 9 |  |
| `test_delegate_tool.py` | a | 运行时层 | 17 |  |
| `test_delegation_e2e_mock.py` | a | 运行时层 | 1 |  |
| `test_delegation_e2e_real_provider.py` | a | 运行时层 | 1 |  |
| `test_input_queue.py` | a | 运行时层 | 8 |  |
| `test_long_context_capacity.py` | a | 运行时层 | 1 |  |
| `test_provider_response_durability.py` | a | 运行时层 | 5 |  |
| `test_provider_wire.py` | a | 运行时层 | 3 |  |
| `test_recall_untrusted_frame.py` | a | 只测旧路 | 4 | a：旧召回适配器本身 |
| `test_same_runtime_lease_recovery.py` | a | 运行时层 | 1 |  |
| `test_session_memory.py` | a | 只测旧路 | 12 | a：旧检索/召回本身 |
| `test_session_memory_real_embedding.py` | a | 只测旧路 | 1 | a：旧检索真嵌入 |
| `test_slice5_recovery.py` | a | 运行时层 | 7 |  |
| `test_slice5_review.py` | a | 运行时层 | 6 |  |
| `test_tool_output_length_recovery.py` | a | 运行时层 | 9 |  |
| `test_turn_limits.py` | a | 运行时层 | 7 |  |
| `test_turn_resume_identity.py` | a | 运行时层 | 6 |  |

**`tests/execution/`**（1 个文件，5 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_execution_v9_to_v10_migration.py` | a | 运行时层 | 5 |  |

**`tests/integration/`**（1 个文件，3 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_protocol_failure_usage.py` | abcd | 提交层 | 3 |  |

**`tests/orchestrator/full_target/assurance_exec/`**（30 个文件，114 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `_assured_loop.py` | abd | 主循环 | 0 | 已装保证通道 |
| `_deploy.py` | a | 主循环 | 0 | 已装保证通道 |
| `_method_plan_world.py` | — | 不受影响 | 0 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `_operation_world.py` | bc | 提交层 | 0 |  |
| `_subgoal_world.py` | abd | 主循环 | 0 | 已装保证通道 |
| `test_a_assurance.py` | abd | 提交层 | 18 | 已装保证通道 |
| `test_assured_planner_unknown_bounded.py` | ab | 主循环 | 2 | 已装保证通道 |
| `test_c07_host_api.py` | ab | 主循环 | 2 | 已装保证通道 |
| `test_c_assurance.py` | ab | 主循环 | 6 | 已装保证通道 |
| `test_c_integration.py` | ab | 提交层 | 1 | 已装保证通道 |
| `test_check_policy_lossless_mapping.py` | ab | 提交层 | 7 | 已装保证通道 |
| `test_check_policy_projector_port.py` | a | 主循环 | 2 | 已装保证通道 |
| `test_disclosure_batch_message_order.py` | ab | 提交层 | 1 | 已装保证通道 |
| `test_e_assurance.py` | abd | 提交层 | 8 | 已装保证通道 |
| `test_evidence_recheck.py` | abd | 主循环 | 3 | 已装保证通道 |
| `test_fixed_authority_access_key_per_source.py` | ab | 主循环 | 1 | 已装保证通道 |
| `test_method_plan_policy_projection.py` | ab | 提交层 | 2 | 已装保证通道 |
| `test_method_review_gate.py` | abd | 主循环 | 6 | 已装保证通道 |
| `test_mission_final_sees_root_effects.py` | bc | 提交层 | 1 |  |
| `test_planner_chooses_and_proposes.py` | abd | 主循环 | 6 | 已装保证通道；混合：另有手工提交用例 |
| `test_planning_budget_one_ladder.py` | abd | 主循环 | 3 | 已装保证通道 |
| `test_review_second_opinion.py` | ab | 提交层 | 4 | 已装保证通道 |
| `test_review_turn_retry_e2e.py` | ab | 提交层 | 4 | 已装保证通道 |
| `test_root_review_adjudication.py` | abd | 提交层 | 2 | 已装保证通道 |
| `test_root_review_rework_is_a_generic_request.py` | abd | 提交层 | 3 | 已装保证通道 |
| `test_settlement_held_not_fatal.py` | ab | 提交层 | 1 | 已装保证通道 |
| `test_stateful_assurance.py` | ab | 提交层 | 1 | 已装保证通道 |
| `test_subgoal_outputs.py` | abd | 主循环 | 2 | 已装保证通道 |
| `test_subgoal_planning.py` | abd | 主循环 | 12 | 已装保证通道 |
| `test_v_assurance.py` | ab | 主循环 | 16 | 已装保证通道 |

**`tests/orchestrator/full_target/`**（107 个文件，1399 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `decision_loop.py` | c | 提交层 | 0 |  |
| `leaf_world.py` | bcd | 提交层 | 0 |  |
| `test_adjudication_question_never_replans.py` | abcd | 主循环 | 1 |  |
| `test_after_handoff_unknown_bounded.py` | bcd | 提交层 | 5 |  |
| `test_appworld_hierarchical_worker.py` | abcd | 主循环 | 5 | 混合：另有手工提交用例 |
| `test_business_replay_inventory.py` | bcd | 提交层 | 3 |  |
| `test_composition_review_consumer.py` | abcd | 主循环 | 3 |  |
| `test_compound_gate_no_pseudo_cycle.py` | — | 不受影响 | 53 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_criteria_driven_write_step.py` | abcd | 主循环 | 6 |  |
| `test_domain_schema_gate.py` | ab | 主循环 | 4 |  |
| `test_finalizer_output_ports.py` | bcd | 提交层 | 12 |  |
| `test_fixed_task_allowance_config.py` | a | 主循环 | 2 |  |
| `test_flat_mode_removed.py` | ab | 主循环 | 3 |  |
| `test_h1h_action_handoff.py` | bcd | 提交层 | 5 |  |
| `test_h1h_authority_matrix.py` | abcd | 主循环 | 9 |  |
| `test_h1h_authority_throughput.py` | abcd | 主循环 | 1 |  |
| `test_h1h_authority_vs_action_approval.py` | abcd | 主循环 | 3 |  |
| `test_h1h_commit_guard.py` | b | 提交层 | 6 |  |
| `test_h1h_commit_interleaving.py` | bc | 提交层 | 1 |  |
| `test_h1h_missing_assembly_planning.py` | ab | 主循环 | 1 |  |
| `test_h1h_no_nanojev_process.py` | abcd | 主循环 | 1 |  |
| `test_h1h_nonmutating_collect.py` | abcd | 主循环 | 2 |  |
| `test_h1h_operation_alias.py` | bcd | 提交层 | 5 |  |
| `test_h1h_operation_current_gates.py` | bc | 提交层 | 5 |  |
| `test_h1h_operation_live_boundaries.py` | bcd | 提交层 | 2 |  |
| `test_h1h_operation_matrix.py` | — | 不受影响 | 3 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_h1h_operation_tenant.py` | — | 不受影响 | 2 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_h1h_operation_two_real_producers.py` | bc | 提交层 | 1 |  |
| `test_h1h_p02_compiler_cycles.py` | bcd | 提交层 | 1 |  |
| `test_h1h_p03_compiler_data_coverage_resources.py` | bcd | 提交层 | 1 |  |
| `test_h1h_preview_compiler_refusal.py` | abcd | 主循环 | 7 |  |
| `test_h1h_preview_purity.py` | abcd | 主循环 | 1 |  |
| `test_h1h_request_binding.py` | b | 提交层 | 2 |  |
| `test_h1h_retired_method_unknown_action.py` | bcd | 提交层 | 1 |  |
| `test_h1h_retired_running_work.py` | b | 提交层 | 1 |  |
| `test_h1h_scoped_critic_policy.py` | abcd | 主循环 | 4 | 混合：另有手工提交用例 |
| `test_h1h_wiring.py` | bcd | 提交层 | 12 |  |
| `test_h1i_commit_recovery.py` | abcd | 主循环 | 3 |  |
| `test_h1i_decision_replay.py` | abcd | 主循环 | 2 |  |
| `test_h1i_deferred_repair_resume.py` | abcd | 主循环 | 1 |  |
| `test_h1i_preview_recovery.py` | abcd | 主循环 | 1 |  |
| `test_h1i_production_entry.py` | abcd | 主循环 | 7 |  |
| `test_h1i_raw_artifact_collector.py` | abcd | 主循环 | 3 |  |
| `test_h1i_wait_lifecycle.py` | abcd | 主循环 | 15 |  |
| `test_h1i_wait_snapshot.py` | abcd | 主循环 | 1 |  |
| `test_h3_method_selection.py` | bcd | 提交层 | 4 |  |
| `test_h4_graph_repair_commits.py` | bd | 提交层 | 8 |  |
| `test_h4_package7_entry.py` | abcd | 主循环 | 1 |  |
| `test_h4_repair_adapter.py` | bcd | 提交层 | 8 |  |
| `test_h4_retained_completion.py` | bcd | 提交层 | 2 |  |
| `test_h4_retry_runtime_entry.py` | abcd | 主循环 | 6 |  |
| `test_h6_oracle_bound_evaluation.py` | bcd | 提交层 | 4 |  |
| `test_h6_source_recovery.py` | abcd | 主循环 | 1 | 混合：另有手工提交用例 |
| `test_h7_real_solver_commit.py` | abcd | 主循环 | 1 |  |
| `test_h8_formal_root_completion.py` | abcd | 主循环 | 2 |  |
| `test_hierarchical_event_flow.py` | abcd | 主循环 | 58 | 混合：另有手工提交用例 |
| `test_hierarchical_judgment_tree.py` | abcd | 只测旧路 | 2 | c：_judge 的旧合并树 |
| `test_htn_and_or_shared_goal.py` | — | 不受影响 | 33 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_htn_deployment_wiring.py` | bcd | 提交层 | 42 |  |
| `test_htn_end_to_end.py` | abcd | 主循环 | 187 | 含 _judge/旧审阅用例；混合：另有手工提交用例 |
| `test_htn_grounding_partial_order.py` | — | 不受影响 | 67 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_htn_novel_method_admission.py` | — | 不受影响 | 51 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_input_manifest_same_source.py` | bcd | 提交层 | 6 |  |
| `test_inspect_leaf_patch_input.py` | bcd | 提交层 | 6 |  |
| `test_leaf_owns_linked_criteria.py` | bd | 提交层 | 5 |  |
| `test_lease_lost_is_redone.py` | abcd | 主循环 | 2 |  |
| `test_method_proposals.py` | — | 不受影响 | 42 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_nested_compound_composition.py` | abcd | 主循环 | 5 | 混合：另有手工提交用例 |
| `test_nested_compound_refinement.py` | abcd | 主循环 | 8 | 混合：另有手工提交用例 |
| `test_output_port_claims.py` | abcd | 主循环 | 18 | 混合：另有手工提交用例 |
| `test_plan_commits.py` | b | 提交层 | 100 |  |
| `test_planner_package_refs_and_fields.py` | bcd | 提交层 | 73 |  |
| `test_planner_package_single_layer.py` | abcd | 主循环 | 9 |  |
| `test_planner_rounds_in_flight.py` | c | 提交层 | 2 | 1 处 root_reviewer 角色名 |
| `test_planning_decision_enablement_contract.py` | abcd | 主循环 | 7 |  |
| `test_planning_protocol_switch.py` | ab | 主循环 | 14 | 混合：另有手工提交用例 |
| `test_planning_request_retry_scope.py` | abcd | 主循环 | 3 |  |
| `test_progress_acceptance_2b.py` | abcd | 主循环 | 4 | 混合：另有手工提交用例 |
| `test_progress_acceptance_2b_repair.py` | abd | 主循环 | 3 | 已装保证通道 |
| `test_provider_grant_rehandoff.py` | abcd | 主循环 | 4 |  |
| `test_read_only_leaf_policy.py` | abd | 主循环 | 13 |  |
| `test_read_only_leaf_write_guard.py` | abcd | 主循环 | 14 |  |
| `test_read_only_rewrite_bound.py` | abcd | 主循环 | 2 |  |
| `test_real_provider_hierarchical_smoke.py` | abcd | 主循环 | 3 |  |
| `test_repair_request_failure_facts.py` | abcd | 主循环 | 4 |  |
| `test_repeated_failure_early_stop.py` | abcd | 主循环 | 7 |  |
| `test_repeated_planner_question.py` | abcd | 主循环 | 2 |  |
| `test_resolution_commits.py` | bcd | 提交层 | 108 |  |
| `test_retry_after_regeneration.py` | abcd | 主循环 | 1 |  |
| `test_revoked_generation_cleared.py` | bcd | 提交层 | 1 |  |
| `test_role_prompts_single_copy.py` | c | 提交层 | 7 | 1 条钉 ROOT_REVIEWER 登记 |
| `test_root_review_coordinator.py` | abcd | 主循环 | 75 | 含旧根审阅员原始裁决收集用例；混合：另有手工提交用例 |
| `test_root_review_evidence.py` | abcd | 主循环 | 30 | 含旧根审阅包用例；混合：另有手工提交用例 |
| `test_root_review_repair_library.py` | bcd | 提交层 | 5 |  |
| `test_root_review_user_goal.py` | bcd | 提交层 | 8 | 含旧根审阅用例 |
| `test_root_reviewer_prompt.py` | c | 只测旧路 | 2 | c：旧根审阅员提示词 |
| `test_run_loop_inflight_planning.py` | abcd | 主循环 | 1 | 混合：另有手工提交用例 |
| `test_runtime_lost_native_retry.py` | bcd | 提交层 | 7 |  |
| `test_seed_methods.py` | — | 不受影响 | 62 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_service_intent_provider_blocker.py` | abcd | 主循环 | 5 | 混合：另有手工提交用例 |
| `test_stall_asks_planner_first.py` | bcd | 提交层 | 11 |  |
| `test_subgoal_levels.py` | — | 不受影响 | 6 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_terminal_unknown_release.py` | bcd | 提交层 | 6 |  |
| `test_unclaimed_port_is_a_rejected_result.py` | abcd | 主循环 | 1 |  |
| `test_unknown_usage_upper_bound.py` | b | 提交层 | 6 |  |
| `test_v14_runtime_closure.py` | abcd | 主循环 | 10 | 混合：另有手工提交用例 |
| `test_verify_workspace_inputs.py` | abcd | 主循环 | 7 |  |

**`tests/orchestrator/full_target/fixtures/htn/`**（1 个文件，0 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `htn_world.py` | — | 不受影响 | 0 | 只建未绑定任务行、只测编译/规划，不经分支 |

**`tests/orchestrator/full_target/operation_completion/`**（18 个文件，97 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `operation_runtime_fixture.py` | bc | 提交层 | 0 |  |
| `test_completion_compound_commit.py` | bcd | 提交层 | 1 |  |
| `test_completion_consumers.py` | bcd | 提交层 | 3 |  |
| `test_completion_contract.py` | abd | 提交层 | 20 | 已装保证通道 |
| `test_completion_data_dispatch.py` | abcd | 主循环 | 1 |  |
| `test_completion_pending_stall.py` | ab | 主循环 | 1 |  |
| `test_completion_plan_commit.py` | b | 提交层 | 5 |  |
| `test_completion_scope_compiler.py` | — | 不受影响 | 11 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_completion_spec_approval.py` | b | 提交层 | 13 |  |
| `test_completion_storage_integrity.py` | — | 不受影响 | 4 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_operation_intent_sources.py` | — | 不受影响 | 5 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_operation_payloads.py` | — | 不受影响 | 5 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_operation_t0_t3_runtime.py` | bc | 提交层 | 1 |  |
| `test_operation_workspace_projection.py` | — | 不受影响 | 2 | 只建未绑定任务行、只测编译/规划，不经分支 |
| `test_scoped_content_commit.py` | b | 提交层 | 3 |  |
| `test_scoped_content_integrity.py` | b | 提交层 | 5 |  |
| `test_scoped_reconciliation.py` | bc | 提交层 | 8 |  |
| `test_system_operation_intents.py` | bc | 提交层 | 9 |  |

**`tests/orchestrator/full_target/taskgraph_exec/`**（20 个文件，40 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `crash_seed.py` | acd | 主循环 | 0 | 已绑执行图 |
| `production_fixture.py` | acd | 主循环 | 0 | 已绑执行图 |
| `test_captured_baseline_removed.py` | acd | 主循环 | 2 | 直接测执行图要求/开关；已绑执行图 |
| `test_commit_atomicity.py` | acd | 主循环 | 2 | 已绑执行图 |
| `test_convergence_advanced_event.py` | bcd | 提交层 | 2 |  |
| `test_convergence_wake_terminal_mission.py` | bcd | 提交层 | 2 |  |
| `test_execution_view.py` | acd | 主循环 | 1 | 已绑执行图 |
| `test_late_accounting_quiet.py` | acd | 主循环 | 2 | 已绑执行图 |
| `test_mission_sources.py` | cd | 主循环 | 4 | 已绑执行图 |
| `test_old_domain_recovery.py` | acd | 主循环 | 1 | 已绑执行图 |
| `test_process_recovery.py` | acd | 主循环 | 2 | 已绑执行图 |
| `test_production_seed.py` | acd | 主循环 | 1 | 已绑执行图 |
| `test_read_history_protocol.py` | acd | 主循环 | 5 | 已绑执行图 |
| `test_revision_read_cache.py` | acd | 主循环 | 1 | 已绑执行图 |
| `test_source_change_replan.py` | acd | 主循环 | 1 | 已绑执行图 |
| `test_successor_with_taskgraph.py` | acd | 主循环 | 1 | 已绑执行图 |
| `test_taskgraph_required.py` | acd | 主循环 | 4 | 直接测执行图要求/开关；已绑执行图 |
| `test_terminal_event_transaction.py` | — | 不受影响 | 1 | 直接测执行图要求/开关 |
| `test_wait_on_method_instance.py` | acd | 主循环 | 3 | 已绑执行图 |
| `test_worker_skills.py` | cd | 主循环 | 5 | 已绑执行图 |

**`tests/orchestrator/gap_phase1/`**（3 个文件，16 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_code_knowledge.py` | bcd | 提交层 | 8 |  |
| `test_context_consumption.py` | bcd | 提交层 | 7 |  |
| `test_result_output_contract.py` | bcd | 提交层 | 1 |  |

**`tests/orchestrator/host_support/`**（2 个文件，14 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_create_mission_entry.py` | ab | 主循环 | 5 |  |
| `test_facade.py` | a | 主循环 | 9 |  |

**`tests/orchestrator/p32/`**（1 个文件，2 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_startup_failure_lifecycle.py` | abcd | 主循环 | 2 |  |

**`tests/orchestrator/p33/`**（11 个文件，96 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_g_critic_dispatch_recovery.py` | a | 主循环 | 1 |  |
| `test_g_large_read_context.py` | abcd | 主循环 | 11 |  |
| `test_g_replay_audit_plugin.py` | b | 提交层 | 19 |  |
| `test_g_workspace_paging.py` | a | 运行时层 | 12 |  |
| `test_p33_domain_binding.py` | b | 提交层 | 5 |  |
| `test_p33_domain_prompts.py` | ab | 主循环 | 3 | 混合：另有手工提交用例 |
| `test_p33_g1_create_sources.py` | ab | 主循环 | 9 | 混合：另有手工提交用例 |
| `test_p33_publish_source_guard.py` | bc | 提交层 | 10 |  |
| `test_p33_result_evidence_gate.py` | bcd | 提交层 | 1 |  |
| `test_p33_source_runtime.py` | a | 主循环 | 4 |  |
| `test_p33_sources.py` | b | 提交层 | 21 |  |

**`tests/orchestrator/p34/`**（1 个文件，5 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_mission_runtime_profile.py` | ab | 主循环 | 5 | 混合：另有手工提交用例 |

**`tests/orchestrator/p35/`**（14 个文件，44 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_admission_collection_runtime.py` | abcd | 主循环 | 1 |  |
| `test_admission_estimator_candidates.py` | a | 主循环 | 1 |  |
| `test_admission_slot_change.py` | abcd | 提交层 | 1 |  |
| `test_first_protected_tail_hooks.py` | abcd | 提交层 | 4 |  |
| `test_first_request_guard_integration.py` | abcd | 提交层 | 1 |  |
| `test_multi_profile_load.py` | abcd | 提交层 | 2 |  |
| `test_priced_budget_cold_reopen.py` | abcd | 提交层 | 1 |  |
| `test_provider_accounting.py` | abcd | 提交层 | 5 |  |
| `test_provider_accounting_boundaries.py` | abcd | 提交层 | 3 |  |
| `test_provider_budget_guard.py` | abcd | 提交层 | 7 |  |
| `test_provider_budget_identity.py` | abcd | 提交层 | 4 |  |
| `test_provider_budget_recovery.py` | abcd | 提交层 | 7 |  |
| `test_succeeded_missing_usage_boundary.py` | abcd | 提交层 | 2 |  |
| `test_tail_and_priced_budget.py` | abcd | 提交层 | 5 |  |

**`tests/orchestrator/p36/`**（1 个文件，1 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_functional_acceptance.py` | bcd | 提交层 | 1 |  |

**`tests/orchestrator/step02/`**（3 个文件，11 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_attempt_charge_by_fault.py` | abcd | 主循环 | 3 |  |
| `test_cli_demo.py` | ab | 主循环 | 2 |  |
| `test_commit_service.py` | abcd | 主循环 | 6 | 混合：另有手工提交用例 |

**`tests/orchestrator/step04/`**（6 个文件，15 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `knowledge_helpers.py` | bcd | 提交层 | 0 |  |
| `test_artifact_versions.py` | bcd | 提交层 | 2 |  |
| `test_blackboard.py` | bcd | 提交层 | 1 |  |
| `test_disputes_on_leaf_steps.py` | bcd | 提交层 | 3 |  |
| `test_retrieval_context.py` | bcd | 提交层 | 5 |  |
| `test_step04_review_round1.py` | bcd | 提交层 | 4 |  |

**`tests/orchestrator/step06/`**（1 个文件，3 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_step06_review_fixes.py` | bcd | 提交层 | 3 |  |

**`tests/orchestrator/step07/`**（6 个文件，24 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `helpers_step07.py` | bcd | 提交层 | 0 |  |
| `test_action_execution.py` | bcd | 提交层 | 2 |  |
| `test_action_ledger.py` | bcd | 提交层 | 15 |  |
| `test_approvals.py` | a | 主循环 | 1 |  |
| `test_human_review.py` | bcd | 提交层 | 4 |  |
| `test_waiting_view.py` | bcd | 提交层 | 2 |  |

**`tests/orchestrator/step09/`**（1 个文件，7 个用例）

| 文件 | 碰到 | 归类 | 用例 | 说明 |
|---|---|---|---|---|
| `test_policy_library.py` | abc | 主循环 | 7 | 混合：另有手工提交用例 |

---

## 三、非测试入口：删 (a)(b)(c)(d) 后哪些起不来

"最近一次提交"取自主仓库 `git log -1`。2026-09-23 的 `0c3abfdb` 是 SDK 源码整体并入主仓库的那次提交，只能说明"最晚改于 09-23 或更早"。
"最近被引用 / 被用"取自 `plans/` 和 `.local-test-evidence/` 里的引用。

| 入口 | 干什么用 | 碰到 | 删后状态 | 最近一次提交 | 最近被引用 / 被用 | 与 N4 Grok 冻结基线配对的关系 |
|---|---|---|---|---|---|---|
| `SDK/evaluation/htn_hierarchical.py`（`execute_hierarchical`） | H8 分层臂：建真实任务，起 `Orchestrator`（`RuntimeProfile` 不带原生平面），经 `htn_domains.py:151` `install_hierarchical(planning=world)` 装共享世界，不装保证通道，根要求书用 `root_review.root_requirements`（147 行） | a b c d | 起不来 | 10-02 `e6773629`（opt.124） | `删旧平面模式-第三刀施工清单.md`（10-02）把它列为"已有分层臂"；没有找到 09-22 以后的实跑记录 | 不是冻结 runner。它是 N4 之后"HTN 上线后再跑同题对照"唯一现存的 AppWorld 分层执行路径 |
| `SDK/evaluation/htn_domains.py` | 给 H8 臂装规划世界 | d（b c 随调用方） | 起不来 | 09-23 导入 | 同上 | 同上 |
| `SDK/evaluation/htn_executor.py` | H8 单局执行器，分派单代理臂和分层臂；`RuntimeProfile("default", meter, …)` 不带原生平面（153 行） | a（b c d 随分层臂） | 起不来 | 10-01 `a3dc67d0`（opt.113） | — | 同上 |
| `SDK/evaluation/htn_single_agent.py` | H8 "强单代理"对照臂，直接调 `build_agent_runtime`（79 行） | a | 起不来（旧运行时） | 09-23 导入 | `HTN补齐-开工前裁决-2026-10-02.md` 把它列为旧执行池入口 | 同上（对照臂） |
| `SDK/evaluation/htn_batch.py` | H8 批量跑和冻结清单（`--freeze-only`） | 经 `htn_executor` 碰 a b c d | 跑局起不来；只冻结时看导入链是否还在 | 09-23 导入 | `主体编码检查点-2026-09-22.md` | 同上 |
| `SDK/evaluation/htn_deployment.py` | H8 部署配置 | 经导入碰 a b c d | 同上 | 10-02 `2db8df6e`（opt.129） | `HTN补齐-阶段A删除-小结.md`（10-03） | 同上 |
| `SDK/evaluation/htn_method_cohort.py`、`htn_method_source.py` | H6 做法评测队列和来源 | 经导入碰 a b c d | 起不来 | 10-01 `a3dc67d0` | `HTN补齐计划-2026-10-02.md` 第 171 行已定"评测晋级连同脚本删掉" | 无 |
| `scripts/acceptance/run_v14_runtime.py` | V1.4 真实运行入口：H8 单例探针、完整矩阵、H6 队列 | 经 `htn_*` 碰 a b c d | 起不来 | 09-23 导入 | `主体编码检查点-2026-09-22.md`；`HTN补齐计划` 第 171 行要删其中的晋级用法 | 同 `htn_hierarchical` |
| `scripts/acceptance/prepare_h8_deployment.py` | 只写 H8 冻结配置，不调模型 | 仅经导入 `htn_batch` | `htn_*` 删了就导入失败 | 10-02 `2db8df6e` | `主体编码检查点-2026-09-22.md` | 同上 |
| `scripts/acceptance/run_real_deepseek_wait_lifecycle.py`、`resume_real_deepseek_wait.py` | H1-I"等待"生命周期真实模型探针；复用 `test_h1i_production_entry` 的种子，也就是旧池加共享世界 | a b c d | 起不来 | 09-23 导入 | `WAIT复验-2026-09-21.md`（09-21） | 无 |
| `scripts/assurance_seams/*.py`（18 个接缝脚本和 `_assured_fixture.py`） | Assurance 1.1 各接缝的独立验收脚本。`_assured_fixture.AssuredRuntime` 直接调 `build_agent_runtime` 加 `AllowAllAuthorization`（221 行）；世界由 `test_plan_commits`、`test_scoped_content_commit` 手工提交搭成，未绑定执行图；已装保证通道 | a b（`evidence-tools`、`final-writer`、`four-consumer`、`purpose-builders`、`recovery` 另碰 d；`check-binding`、`check-use` 只建任务行） | 起不来。`_assured_fixture` 还被 15 个 SDK 测试文件导入，是第 2 节"提交层"的大头 | 10-02 `a23e7660` / `1e968dab` / `53938dbb`（随删除批量改过） | `.local-test-evidence/2026-09-23/assurance-1.1-verify/` 下的变异日志（09-23）；`删旧平面模式-第三刀施工清单.md` 提到 | 无 |
| 命令行 `python -m agent_orchestrator mission create` | 用 `Orchestrator(cfg, provider)` 开库并建一条任务；不装部署，所以建的是未绑定、没装保证通道的分层任务 | a b（c） | 起不来：旧池没了；"没装保证通道就拒绝建分层任务"也会把它拒掉 | 10-02 `1e968dab`（opt.132 删平面演示） | `agent-orchestrator-incremental-build-plan-phase2-zh-CN.md`（09-11）；测试 `step02/test_cli_demo.py`、`step09/test_policy_library.py` | 无 |
| 命令行其余子命令：`mission get/events/cancel`、`attempt get`、`artifact show`、`approval *`、`replay`、`policy *` | 只开库读写，不起主循环 | — | 不受影响 | — | — | 无 |
| 不受影响的脚本 | `run_*_old_runtime.py` 四个（这里的"旧运行时"指执行内核 0.7.2 的审计，与旧执行池无关）、`recall_use_installed.py`、`run_operation_audit_consumer.py`、`verify_full_runtime_stage.py`、`run_h6_method_evaluation.py`（只读库）、`scripts/build/*`、`check_*.py`、`verify_release_gate.sh` | — | 不受影响 | — | — | 无 |
| 评测包其余模块 | `appworld_*`（观察、操作、打分、服务）、`experiment.py`、`context_challenge.py`、`htn_matrix.py`、`htn_scenarios.py`、`htn_oracles.py` 等 | — | 本身不碰；如果 H8 臂删了，`appworld_*` 里只被分层臂用的部分会变成没人用 | — | — | 无 |

**N4 结论**：N4 冻结 runner 是 `.local-test-evidence/2026-09-16/a96-grok/run-a96-grok46.py`，跑 S/R/D/F 四臂，靠的是 `evaluation.appworld_arms.execute_arm`（180 行）。这个模块在 opt.132（10-02）已随平面旧臂删掉。所以：
- 冻结 runner 现在已经不能在当前 SDK 上原样重跑；
- 本轮要删的入口不会让 N4 的配对"再坏一次"；
- 但如果用户还打算以后用 AppWorld 跑"HTN 上线后对照"，现存唯一的分层执行路径就是 `htn_hierarchical`、`htn_executor`、`run_v14_runtime` 这一组（H8 臂）。删 (a)(b)(c)(d) 时，它要么改造成"原生池 + 部署世界 + 绑定 + 保证通道"，要么删掉。

**要用户裁定的入口**（按影响从大到小）：
1. **AppWorld H8 分层臂**（`htn_hierarchical` / `htn_domains` / `htn_executor` / `htn_batch` / `htn_deployment` 和 `run_v14_runtime` / `prepare_h8_deployment`）：改造还是删？改造需要的东西包括 AppWorld 领域的保证通道部署口、原生池计数器、执行图绑定；`critic-appworld-v1` 模板随 (c) 删。
2. **H8 强单代理对照臂** `htn_single_agent`（旧运行时）：改到原生运行时还是删？它和第 1 项同生死才有意义。
3. **Assurance 接缝脚本** `scripts/assurance_seams/` 和 `_assured_fixture`：迁到产品同形世界还是删？注意 `_assured_fixture` 同时是 15 个 SDK 测试文件的构造器，删脚本不等于能删它。
4. **H1-I 等待真实模型探针**两个脚本：删，还是改到产品同形世界？
5. **命令行 `mission create`**：删掉这个子命令，还是改成走"桌面部署组装"？后者要命令行也能拿到原生池和计数器。
6. H6 做法评测（`htn_method_cohort` / `htn_method_source`）：`HTN补齐计划` 第 171 行已经定了删，这里只提醒"随 A′ 一起删"。

---

## 四、搬组装时必须字节不变的东西

"字节不变"的理由有两条：
- 这些串进了库里的回执号、命令号、内容哈希或审计事件；
- 改了会让已有任务的幂等重放对不上，或者让同一件事在新旧两边算出两个号。

### 1. 命令号与回执号

| 用途 | 现在的格式 | 位置 | 备注 |
|---|---|---|---|
| 保证通道根安装 | `command_id="host-native-root"` | `Host/assurance.py:24, 66-68` | SDK 测试仿写用的是 `"install"`（`T/orchestrator/full_target/assurance_exec/_assured_loop.py:158`），已漂移 |
| 检查策略批准（内容） | `host-check-policy:<scope_id>` | `Host/assurance.py:180-185` | |
| 检查策略批准（终审） | `host-check-policy:mission-final:<root_scope_id>` | 同上 | |
| 检查策略批准（操作提议） | `host-check-policy:action-proposal:<scope_id>` | 同上 | |
| 检查策略批准（操作结果） | `host-check-policy:operation-outcome:<scope_id>:<effect_key>` | 同上 | |
| 检查策略批准（做法计划） | `host-check-policy:method-plan:<task_id>:<contract_revision>:r<requirements_revision>` | `Host/assurance.py:306, 313` | SDK 测试仿写 `_assured_loop.approve_method_policies` 用的是 `host-check-policy:method-plan:<task_id>`，少了后两段，已漂移 |
| 检查策略批准回执号（Host 预查用） | `"assurance-check-policy-approval:" + fingerprint({"mission","tenant","principal","command"})` | `Host/assurance.py:186-189`，与 SDK `orchestrator/assurance_check_policy.py:127-134` 是同一公式的两份拷贝 | 搬进 SDK 后应直接调 SDK 的那一份 |
| 内容完成映射自动确认 | `"host-auto-completion-" + sha256(f"{mission_id}:{revision}:{content_hash}").hexdigest()[:32]` | `Host/service.py:791-802` | 键里的 `revision`、`content_hash` 取自快照的 `operation_workspace.requirements_ref` |
| 规划授权自动签发 | `host-auto-planning:<request_id>` | `Host/service.py:849` | SDK 测试仿写 `decision_loop.auto_grant` 用的是 `grant-<request_id>`，主体是 `Principal(loop._owner)`，签发时机是"在建意图的同一事务里"，而 Host 是在两轮之间的宿主职责里签发。三处都已漂移 |
| 执行图启用 | `taskgraph-enable:<mission_id>:taskgraph-exec-v2` | 已在 SDK：`taskgraph_requirement.enable_command_id`，`KERNEL_VERSION` 见 `taskgraph_policy.py:25` | 不用搬 |
| 执行图要求书来源 | `"deployment_default"` | SDK `taskgraph_requirement.DEPLOYMENT_DEFAULT`；Host `service.py:872` 用默认值调用 | 不用搬 |
| 租户 | `"local-desktop"` | `Host/service.py:53` | 进了任务行和全部回执的身份 |
| 主体 | `Principal(f"local-user:{profile_id}", "本机用户")` | `Host/wiring.py:56` | `principal_id` 进了要求书的 `authority_subject`，会影响内容哈希；它属于 Host 认证，**不搬**，只作为参数传进组装 |

### 2. 批准来源字串

| 字串 | 用在哪 | SDK 接受的集合 |
|---|---|---|
| `"HOST_LOSSLESS_AUTO"` | 检查策略批准，包括做法计划（`Host/assurance.py:204, 317`） | `api/facade.py:156`：`HUMAN` / `HOST_LOSSLESS_AUTO` |
| `"HOST_AUTO_PERMISSION"` | 内容完成映射确认（`Host/service.py:808`）、规划授权（`:850`） | `api/operation_completion.py:60`、`api/facade.py:82`、`api/planning_authorization.py:128`、`orchestrator/operation_completion.py:179`（带对外效果时拒绝） |

### 3. 命令体和要求书的构造（逐字段）

- **检查策略批准命令体**（`Host/assurance.py:197-209`）：
  - 字段：`mission_id`、`command_id`、`requirements_ref`、`completion_scope`、`candidate_mapping`、`approval_source`；
  - 只有非 `CONTENT` 用途才带 `purpose`，只有带效果时才带 `effect_key`；
  - 做法计划命令把 `completion_scope` 换成 `planning_subject`，并带 `purpose: "METHOD_PLAN"`（312-317 行）；
  - 映射本身由 SDK 的 `lossless_scope_mapping` 和 `lossless_planning_subject_mapping` 产出，Host 不增不减；
  - 没有保证通道的任务（`AssuranceStore.lane != "ASSURANCE_1_1"`）和已结束的任务跳过。
- **内容完成映射提议**（`Host/service.py:803-807`）：
  - `{"schema_version": 1, "mission_id", "requirements_ref": {id, revision, content_hash}, "mode": "CONTENT_ONLY", "content_criterion_ids": [所有 required 条件], "effects": []}`；
  - 另带 `expected_requirements_ref`；
  - 触发条件：权限模式为 `auto`、任务状态 `CREATED`、没有一条条件以 `action:` 开头、工作区状态是 `CONFIRMATION_REQUIRED` 且 `editable`；每轮最多处理 50 条（`LIMIT 50`）。
- **规划授权**（`Host/service.py:847-851`）：`{"operation": "issue", "mission_id", "request_id", "command_id", "approval_source"}`，走门面的 `planning_authorization`。
- **根任务初始化**（`Host/hierarchical.py:118-167`），要和建任务、写要求书在同一事务里，见 `service.py:1240-1243`：
  - 根任务号 `desktop-root-<mission_id>`，义务号 `desktop-duty-<mission_id>`；
  - `recursion_fuel=8`；
  - `requester={"kind": "mission_root"}`，`evidence={"mission_id", "requirement_refs"}`；
  - `semantic_scope="mission"`；
  - `contract_hash = content_hash_of({"task_type": definition.to_json(), "parameters": {"goal": mission.goal}})`；
  - 条件号 `c-user-<n>`。
- **桌面规划世界**（`Host/hierarchical.py:13-92`）。下面每一项都进了类型引用哈希或契约哈希：
  - 五个类型名：`desktop.user-goal`、`desktop.sub-goal-1`、`desktop.sub-goal-2`、`desktop.prepare-delivery`、`desktop.continue-delivery`；
  - 层级：0 / 1 / 2；
  - 参数模式 `desktop.goal-parameters` v1，主体 `{"fields": [{"name": "goal", "type": "string", "required": true}]}`；
  - 输出模式 `desktop.workspace-outputs` v1，主体 `{"fields": []}`；
  - 子目标签名的英文原句：`"A part of the user's goal; its goal parameter says what this part has to achieve."`；
  - 续接目标前缀：`"Continue from an accepted upstream delivery: "`；
  - 执行者引用 `desktop.workspace-worker` v1，哈希取 `{"tools": list(allowed_tools)}`；
  - 能力 `workspace.prepare`，领域 `"desktop"`；
  - 类型主体的键与顺序：`name/form/signature/ports`，可选 `input_ports`、`refinement_level`；
  - 根签名的覆盖条件：只取非 `action:` 的条件，全是 `action:` 时才回退到全部。
- **开工条件**（`Host/hierarchical.py:175-187`）：要有要求书修订，并且 `OperationCompletionReader.read_requirements` 能读到完成映射；读到 `OP_REQUIREMENT_MAPPING_MISSING` 就返回"未就绪"。
- **执行图部署口**（`Host/service.py:913-922`）：`TaskGraphDeploymentPorts(tenant_id, principal, graph_budget=DEFAULT_PROJECTION_BUDGET, deployment_acceptance=InstalledHtnWiringAcceptance())`。
- **保证通道部署口**（`Host/assurance.py:100-110`）：
  - `select_profile` 一律返回 `AssurancePolicy()`；
  - `requirements` 用 Host 的 `root_requirements`。

**留在 Host、不搬的**：
- `host_fingerprint()`：它绑的是 Host 提交号和 SDK wheel 哈希；
- 通知推送（`notify`）、原生池真实计数器、权限模式读取、设置页；
- `_require_strict_taskgraph` 里的设置开关（随删 (b) 一起删）。

### 4. 三份要求书构造的差异

| 项 | Host `root_requirements`（`Host/hierarchical.py:95-115`） | SDK `mission_spec_requirements`（`SDK/orchestrator/assurance_assembly.py:188-226`） | SDK `root_review.root_requirements`（`SDK/orchestrator/root_review.py:386-401`，条件来自 `root_criteria` 的 330-384 行） |
|---|---|---|---|
| 谁在用 | 产品：根初始化，同时作为保证通道部署口的 `requirements` | 只在部署口没给 `requirements` 时当默认值；**SDK 测试的保证通道世界（`_assured_loop`、`_deploy`）都没给，所以用的是它** | `evaluation/htn_hierarchical.py:147`、`T/.../scripted_plans.py:115`、`T/.../test_h1i_production_entry.py:76`，即测试和评测的根要求书 |
| 输入 | `mission.success_criteria` | `spec.success_criteria` | 根语义绑定的 `goal_signature.coverage_criteria`，为空时取 `requirement_refs` |
| 修订号 | `"req-<mission>-1"`，固定修订 1 | 同左 | `RequirementsRevisionId(f"req-{mission}-{revision}")`，修订号作参数传入 |
| 条件号 | `c-user-<n>` | `c-user-<n>` | 绑定里的名字。产品根的 `coverage_criteria` 只含非 `action:` 条件，所以和 Host 的条件集合可能不同 |
| 来源 | `USER_EXPLICIT` | `USER_EXPLICIT` | `DERIVED` |
| 条件原文 | 用户原话 | 用户原话 | 合成句 `"<name> is covered by the accepted contributions of <task>, judged as a whole"` |
| 类别和判法 | `REQUIRED_OUTCOME` / `SEMANTIC`，证据策略取默认 | 同左 | 同左，但证据策略为 `RequiredEvidencePolicy(independence_required=True, coverage_statement="judged by the MISSION_FINAL review over the children's acceptances")` |
| 成功表达式 | **一律 `AllExpr`，只有一条也包一层** | 一条时是裸 `CriterionExpr`，多条时是 `AllExpr` | 同 SDK 默认构造 |
| `authority_subject` | `principal.principal_id` | `principal.principal_id` | 不填（`None`） |
| 没有条件时 | `AllExpr(())` 抛 `ContractError` | 抛 `AssuranceError("FACTORY_REQUIREMENTS_MISMATCH")` | 抛 `ContractError`（AER §6.2） |
| 与 Host 字节一致吗 | — | **两条及以上一致；只有一条时不一致**（表达式不同，内容哈希不同）。它的文档串写着"byte for byte"，对一条的情况不成立 | 完全是另一份文档 |

对搬迁的含义：
- 产品只用 Host 那份，所以搬进 SDK 时以它为准，字节不变。
- SDK 默认的 `mission_spec_requirements` 要么删掉，要么改成调同一份；`root_review.root_requirements` 不能再当"根要求书"给测试世界用。
- 否则产品同形世界会在单条件任务和全部根要求书上和产品分叉，第 2 步的代表用例证明不了"同一条路"。
- 改默认构造会改变现有测试世界里单条件任务的要求书哈希，这一点要写进实施记录。
