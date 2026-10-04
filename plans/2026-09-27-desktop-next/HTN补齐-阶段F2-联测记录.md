# HTN 补齐 · 阶段 F2 联测记录

- 开工：2026-10-04 23:50（SDK opt.152 发版之后）
- 依据：`HTN补齐计划-2026-10-02.md` 阶段 F2、`HTN补齐-阶段F-开工裁决与施工清单.md`
- 工作区：`simple_harness-a4`（分支 `tg-1`）；长跑用的是开工时的源码快照，不受之后改动影响
- 路径缩写：`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`

## 一、准备

| 事项 | 结果 |
|---|---|
| 发版后在开发数据副本上试启动 | 已做（发版时）：库结构能升到第 43 版；但旧开发库的编排服务起不来（执行池准入身份自阶段 C 起不兼容旧意图，不是本次发版造成）。按"开发期不兼容旧数据"不处理 |
| 新建编排数据目录 | `.local-test-evidence/2026-10-05/f2-ui/`（配置取自仓库现行 `config.toml`，只加日卡转发主机、模型别名、隔离发布目录三行） |
| 来源表按 TaskGraph 补全改过的地方刷新 | 24 行全部有现行生产方与用例；1 行的用例改了名（第 15 行"需求方"，共用用例改名后重新指向）；守护用例通过 |
| 改坏清单按 TaskGraph 补全改过的地方刷新 | 2 条的原文锚点随第四批移动（"摘要不看验收是否仍站着""完成范围过期时只放下不归档"），已改对；全清单重跑见第二节 |

## 二、自动化

### 2.1 执行图 12 个定点改坏（全部抓到）

逐条定了"改哪里、绑哪条用例"，写进 `T/acceptance_assets/mutations.json`（M01～M12），用 `scripts/acceptance/run_mutations.py` 执行；只认行为断言失败。

| 编号 | 改坏的内容 | 场景 | 抓到它的用例 |
|---|---|---|---|
| M01 | 提交时不再复核规划许可仍有效 | 许可与提交竞态 | `full_target/test_h1h_authority_matrix.py::test_a04_…[expire]` |
| M02 | 读操作来源出错时当作没有操作 | 退役操作结果仍不明 | `full_target/test_h1h_operation_matrix.py::test_o01_…read_error_is_not_empty` |
| M03 | 离线重建每个修订都取最新修订 | 历史结构准确读取 | `product_world/test_random_sequences.py`（随机序列的重建对照） |
| M04 | 取上游产出时不看是谁产的（只有先后关系的上游文件也混进输入） | 先后关系与数据关系分开 | `full_target/test_input_manifest_resolution.py::test_only_the_data_predecessor_of_a_mixed_pair_enters_the_manifest`、`test_versioning_v2_manifest.py::test_an_order_only_predecessor_contributes_no_file` |
| M05 | 必需输入端口没接线时当作没有输入 | 合法的空与没取到 | `full_target/test_input_manifest_same_source.py::test_a_required_port_the_plan_drew_no_edge_for_is_not_an_empty_success` |
| M06 | 提交前重读来源时不比各来源读数 | 并发改计划 | `full_target/test_h1h_commit_guard.py::test_o08_…`、`test_h1h_commit_interleaving.py` |
| M07 | 换做法时碰到被退休做法的孩子就删 | 共用保留 | `full_target/test_htn_and_or_shared_goal.py::test_one_branch_changing_its_method_keeps_the_shared_step_and_the_other_branch_edges`（两个方向） |
| M08 | 派发代次变了仍当作可派发 | 晚到结果 | `full_target/test_readiness_reasons.py::test_a_moved_dispatch_generation_is_a_stale_binding` |
| M09 | 已取消直接当作已结清 | 先后关系按真实结清放行 | `full_target/test_readiness_reasons.py::test_a_cancelled_predecessor_whose_real_work_is_not_settled_does_not_release_clean_up`（**新写**） |
| M10 | 缺方法定义/证据/谓词表时不报"未检查" | 待展开与检查完整 | `full_target/test_compound_gate_no_pseudo_cycle.py::test_validate_delta_says_when_it_could_not_check_preconditions` |
| M11 | 计划提交命令号每次现造 | 进程强退 | `full_target/taskgraph_exec/test_process_recovery.py::test_process_exit_reuses_original_reply_and_commit_identity` |
| M12 | 复合步骤不再按形态拦下 | 复合步骤入口出口 | `full_target/test_readiness_reasons.py::test_a_compound_task_needs_refinement` |

过程中的两点如实记录：

- M06 第一次选的改坏点（最终提交处比对预览时的网络哈希）被别处的两道检查兜住，没被任何用例单独抓到；当时显示"抓到"的那条用例本身是老红用例（见 2.4），不算数。改到"提交前重读来源、逐项比读数"那一处后，被两条用例抓到。原改坏点属于多重保险里的一道，不单独立用例。
- M07 与 TaskGraph 补全第三批的 TG3-05 是同一处改坏、同一条用例（清单里两条都留，注明相同）。

### 2.2 崩溃切点逐点执行

清单 `T/acceptance_assets/crash_points.json` 里归联测执行的各行：

| 切点 | 做法 | 结果 |
|---|---|---|
| K01 计划提交事务中途 / K02 提交后派发前 | 已有用例复跑（进程强退） | 通过 |
| K03 执行侧已收到工作、编排没记回执 | **新写**：产品同形世界里走到切点 → 崩 → 同一数据目录重开 | 通过：这一步始终只有一次尝试、一条派发意图，任务完成 |
| K05 产物已提交、审阅前 | 已有用例复跑 | 通过（用例里一处接口调用随第四批改了签名，已改对） |
| K06 审阅已存、结论未提交 | 叶子一半：已有用例复跑；目标结论一半：**新写** | 通过：重开后审阅员被问到的次数与不崩时一样多 |
| K07 验收已提交、下一轮没收到 | 已有用例复跑 | 通过 |
| K08 对外操作：交接已记、调用前 | **新写** | 通过：重开后查到确实没发出去，用原来那个动作再交一次；动作始终一条，文件只发布一份 |
| K09 对外操作：外部已生效、本地没记 | **新写** | 通过：重开后查到原来那份，不再发 |
| K10 换做法时旧外部动作结果未知 | 已有用例复跑 | 通过 |
| K13 对账处出错 | **新写** | 通过：只记成那个任务这一轮的故障，主循环不抛；错误过去后照常对上 |

新用例在 `T/product_world/test_crash_recovery.py`（6 条）。说明：交接的租约没到期时对账不碰它（那次调用可能还在路上）；用例里把库的时钟拨过租约，不真等。

### 2.3 联测里发现并修掉的缺陷

| # | 现象 | 原因 | 处理 |
|---|---|---|---|
| 1 | 改要求后沿用的一步按新版重审，审阅员判不下来、请人裁决；人判"通过"后这一步仍不算数，三次后被错当成"打回"交给规划器 | 裁决回执里"裁决的对象"写成了任务，而内容审阅的对象是那份结果；写新版验收时使用凭证只认对象是结果的裁决 | 裁决回执的对象改写结果（`event_handler._ask_carried_ruling`）。新用例 `product_world/test_carried_review.py::test_a_re_review_nobody_can_settle_asks_the_person_and_their_pass_counts`；改坏 TG4-06 |

（TaskGraph 补全发版时留下的两件"只有单元级用例"的事，本节与下一条一并补上产品级用例：上面这条，以及 `product_world/test_shared_steps.py::test_the_branch_that_reuses_a_step_can_change_its_method_and_keep_sharing_it`——点名共用的一方换做法后仍共用，被共用的那一步不取消、不重做，任务完成。）

### 2.4 老红用例清零（22 条）

主干上长期红、与近期改动无关的用例，联测时一并处理（分诊由子代理做，只动测试）：

- 删 6 条：钉死过时迁移版本号的 2 条、"旧模式不写新表"2 条、"旧库升级保留旧行"1 条（开发期不做旧数据兼容）、只针对已被取代的第 1 版种子做法的 1 条（主题已不存在，备选做法的行为另有用例）。
- 改夹具 15 条：原来在手工拼的库上直接调存储接口，而现行代码要求任务先有保证通道档案、先绑定执行图、正式审阅记录只能经审阅导入写入；改成在产品同形世界里按产品方式建任务（需要审阅记录的真跑完一个任务），断言主题不变。其中 1 条的期望值随 10-03 的口径改了（纪元第一次推进到 1）。
- 改期望 1 条（`test_h1h_commit_guard.py::test_o03_…`）：发布结果不明时，改计划照旧等对账、不出新版本；阶段 B 起登记的对账器能给出"确实没发生"的证明，这个新事实不是那次改计划当初依据的事实，于是改计划被退回规划器（仍不出新版本）。用例改成两段：服务问不到时一直等；服务答"没发生"后被退回。
- 另 1 条同因用例（`test_review_candidate_refs.py`）同样改到产品同形世界。

遗留一处可清理的旧代码（不是缺陷）：`planning/htn/seed_methods/appworld/` 里的第 1 版做法已被第 2 版取代、永远取不到，按"旧路径直接删"应清掉，记入后续。

### 2.5 规模边界

`full_target/test_projection_integrity.py::test_a_projection_exactly_at_a_bound_passes_and_one_over_is_refused`（**新写**）：节点数、边数等于上限照常通过，多一个按"到上限"拒绝且不截断。超限各维度（节点、边、深度、扇出）的拒绝原有用例已覆盖。

### 2.6 随机动作序列 2000 步

4 个种子 × 500 步、每 20 步关库重开一次（每个种子 25 次），七种动作（新增、细化、接受、撤回要求、取消、换做法、关库重开）。结果见第四节。

### 2.7 全清单改坏重跑

清单全部条目重跑一遍（TaskGraph 补全之后的代码上）。结果见第四节。

## 三、真实模型与真机

（进行中，见第四节之后的更新）

## 四、结果汇总

（长跑结束后填）
