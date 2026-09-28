# NEXT-TG-1.0 第六批：E1–E8 跨层场景覆盖（2026-09-28）

确定性测试（脚本化模型回复，测机制）与真实线路（真实模型 / 真实控制通道 / 真实桌面应用，测线路）分开记。
"未运行"一律写明，不用组件测试代替。

| 场景 | 确定性测试（SDK / Host） | 真实线路证据 | 结论 |
|---|---|---|---|
| E1 多步内容 | `full_target/test_progress_acceptance_2b.py::test_abc_runs_to_completed_without_asking_the_main_planner_again`（链式三步含数据依赖与顺序依赖，完成后不再叫规划器）；`taskgraph_exec/test_execution_view.py` | 2B.7 `mission-cde0847b29d7177f` 完成；opt.41 `mission-8cb840d96b04beba` 完成；本批 `mission-e8a294d104b20b8f`（DeepSeek 真实模型，默认思考档，opt.59→60）：方法合成 → 执行 → 审阅 → 修补规划 → 再执行 → 审阅（两次）→ 终审 → **完成** | 通过 |
| E2 一次内容返工 | `taskgraph_exec/test_execution_view_edges.py`（返工回路因果边、旧失败保留）；2B.10 沿用的同合同返工测试 | 第三批只读核对：ABS 任务返工三次，4 次执行、4 次审阅、3 个修补请求在执行图里全部可见；本批 `mission-e8a294d104b20b8f` 第一次尝试的审阅作废后同一步骤再做一次通过，旧尝试保留 | 通过 |
| E3 一次结构修复 | `test_progress_acceptance_2b_repair.py::test_one_structural_gap_opens_one_planner_round_across_repeated_ticks`、`::test_a_cold_reopen_does_not_ask_the_planner_again_for_the_same_gap`；`test_progress_acceptance_2b.py::test_a_branch_that_needs_structural_planning_does_not_block_the_independent_d` | 真实模型未运行（未专门构造结构缺口的真实任务） | 机制通过；真实线路未运行 |
| E4 必需效果 | 操作完成/审查相关 `full_target/operation_completion/*`、`assurance_exec/test_assured_action_settlement.py`、`test_progress_acceptance_2b_repair.py::test_settling_an_unknown_charge_at_its_upper_bound_leaves_the_unknown_action_alone` | 2A 上游核心链 `mission-a22c9fc39b8abed8`：界面批准 → 受控发布一次 → 发布字节与验收产物一致 → 完成；第三批只读核对 MIN 任务含 3 次审查 + 1 次发布操作 | 通过 |
| E5 强制退出/恢复 | `taskgraph_exec/test_process_recovery.py`（回复与提交身份复用、尝试边界的计费）；`test_arp_mission_mode.py`（创建中断、冻结后恢复） | 本批：`mission-e8a294d104b20b8f` 在第一次尝试的内容审阅调用途中 `kill -9` 后台 → 重启：执行尝试不重复创建（每个 Agent 恰好 1 个会话、1 份来源回执，共 8 个 Agent）；被打断的审阅记为等待原调用核对、不重发，本步重做一次后通过并判定成功。**真机发现缺陷**：那条等核对的审阅意图在 opt.59 进程里让收尾停在"排空中"约 21 分钟；再重启一次后，原有"任务停止后回收"流程把它判失败，收尾就绪、费用按上限 294912 计入，任务 **完成**。为了不依赖重启，SDK opt.60/61 让"只等无法回答的原调用核对、费用未知"的审阅意图不再阻塞已判定任务的收尾（确定性测试经收尾同一判定覆盖；这条新路径本身未在真机上单独复现——真机那次完成是重启回收促成的）（证据 `e5-before-kill.txt`、`e5-after.txt`） | 通过（修复后） |
| E6 多任务隔离 | `test_progress_acceptance_2b.py::test_c_waiting_for_data_does_not_hold_back_the_independent_d`（任务内）；`test_a_refused_agent_creation_stops_that_intent_only_never_the_loop`（一个意图被拒不打断循环） | 本批：两个等人重新提交操作的旧任务一直处于活动状态，新任务照常规划、执行并完成（`mission-e8a294d104b20b8f`）；另一个测试任务因目标里含"发布/上线"字样，自动确认按设计留给人确认，未影响其他任务 | 通过 |
| E7 图与权限 | `taskgraph_exec/test_execution_view.py`（同一读取令牌、分页、篡改游标拒绝、只读无副作用）；Host `test_taskgraph_execution_reads.py`（归属校验、拒绝码映射） | 第三批真实控制通道只读 4 个真实任务，回合详情 9 次 COMPLETE，输出扫描无系统提示、思考、密钥、内部路径 | 通过；界面点击未运行 |
| E8 运行时 / 目录 | `test_arp_mission_mode.py`、`taskgraph_exec/test_mission_sources.py`（来源拒绝矩阵）；`test_arp_shared_catalogue.py`、Host `test_skill_catalogue_host.py`（同版本/准入/撤销跨池一致） | 真实控制通道：四个原生池一次安装同版本、退役同步（`batch5/skill-probe.json`）；真实模型任务的执行者在"256K 思考"池以任务模式创建，会话记录了来源回执 | 通过；技能被任务 Agent 实际调用未运行（执行者工具集尚不含技能工具，记欠项） |

真机桌面点击：两次请求桌面应用操控权限均被拒绝，全部场景的界面点击**未运行**。
