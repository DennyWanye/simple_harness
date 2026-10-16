# 代码质量整治：后续事项（followup）

整治只改形状、不改行为。整治中查出、但属于功能修复或另一阶段的事，登记在这里，不在当前切片里修。

## F-1 持久任务工作流引用了不存在的两个属性（缺陷，待修）

- **登记**：2026-10-16，阶段 5 类型检查清零时查出（偏差单：阶段5-偏差单-类型清零.md 第二件，独立裁决选"本阶段不修，转交"）。
- **位置**：SDK `simple_harness/workflows/durable_task/nodes.py`。
  - 批准后发放授权时读 `call.effect_type`；
  - 整理工具结果时读 `call.args_hash`。
- **问题**：`PreparedToolCall`（同目录 state.py）是只有 4 个字段的冻结数据类：stable_call_id、tool_name、arguments、raw_arguments。没有这两个属性，走到就抛 AttributeError。
- **复现路径**：
  - 批准状态为 approved 且有一个以上工具调用时，走到 `effect_type` 那一行就崩；
  - 有一个以上工具结果时，走到 `args_hash` 那一行就崩。
  - 现有测试没有覆盖这两条路径。
- **影响**：直接抛错，不会悄悄写坏数据。durable_task 已在 Host 产品里注册（backend/host/startup_workflows.py 注册了 "durable_task"），是在用的产品代码。
- **现状**：两行各挂一个只针对该行的 `# type: ignore[attr-defined]`，注释指向本条。
- **修之前先确认**：
  1. "删工作流线"第 3 片会不会整体删掉 durable_task。会删就关掉本条，不修。
  2. 定语义：效果类型从工具目录取还是从提案取；参数哈希按哪种规范化算。这是功能修复，按功能开发流程走（先写能复现的测试）。
- **不许做**：为了让测试过而写固化错误行为的测试。
- 另有一张同内容的任务卡片已开出，可以从卡片直接起一个会话处理。

## F-2 WorkflowProviderAdapter.invoke 多传一个参数（死代码，归阶段 8）

- 位置：SDK `simple_harness/workflow/adapters.py`。调用会 TypeError；该类产品代码不用。
- 现状：挂 `# type: ignore[call-arg]` 加原因注释。阶段 8 查死代码时证明无调用方后删除。

## F-3 LeafAcceptanceAssembly.accept 的 producer_agent_ids 参数不生效（死参数，归阶段 8）

- 参数在函数内被覆盖，调用方传了也不生效。阶段 8 处理。

## F-4 恢复协调器隔离用例偶发失败（观察）

- 登记：2026-10-16，第 4 次全量回归。用例：SDK tests/orchestrator/full_target/test_recovery_coordinator.py 的"不一致任务被隔离、其余照常"。
- 现象：断言"被隔离任务的数据足迹前后完全一致"不成立（某张表的计数或事件序号变了，原输出被截断）。单独、整份各重跑两次都通过。
- 为什么登记：它断言的是隔离完整性（被隔离任务不该被任何后台步骤碰到）。偶发失败可能是负载下的计时问题，也可能是真实竞争。
- 下一步：后续全量回归再出现时，用 `-vv` 拿到完整差异，看是哪张表变了、谁写的。

## F-5 上下文请求规划器疑似死代码（归阶段 8）——2026-10-17 阶段 8 已删

- 登记：2026-10-16，阶段 6D 拆 `prepare_initial` 时查出。
- 位置：`backend/deskpet/agent/context_request_planner.py` 的 `ContextRequestPlanner`。
- 现象：生产代码里没有任何地方构造它，也没有注册 `context_request_planner` 服务（`context.py` 只声明了字段，`turn_preparer.py` 读取时恒为空）；测试也不覆盖它。
- 6D 对它的拆分由 extract_check 内联核对证明逐字等价，但没有运行期测试能观察到。
- 下一步：阶段 8 查死代码时确认无调用方后删除整个模块与 `turn_preparer` 里的读取分支。
- **已删（阶段 8）**：查清调用链。读取分支所在的 `ProductTurnPreparer.prepare_context` 只被 `ProductTurnPreparationService` 调，后者只被 `ProductVenueRunAdapter` 用；`ProductVenueRunAdapter` 的文档串写明"休眠、不在生产链上"，`host/sdk_execution.py` 写明它已被 SDK 执行路径取代；生产代码里没有任何地方构造 `ProductTurnPreparer` 或 `ProductVenueRunAdapter`。所以"开关默认开、规划器没注册、主对话照常能用"的疑问有了答案：生产根本不走这条链。整条休眠链一起删：context_request_planner.py、product_turn_open.py 整个文件；turn_preparer.py 只留 `TurnInput`、`ProductDomainCommand`、`PRODUCT_ROUTE_OWNED_TOOL_NAMES`；venues.py 只留 `KernelRunClient` 与 `VenueRunHandle`；服务登记表删掉只被这条链读的 3 个槽位；只测这条链的测试与性能脚本一起删。属行为变更（删除），单独提交，阶段末交独立核验签字。

## F-6 工具注册表超时路径引用未定义的 future（潜在缺陷）——2026-10-16 阶段 8 已修

- 登记：2026-10-16，阶段 6D 审计全分支未定义名字时查出；拆分前的 `deskpet/tools/registry.py` 原样存在，不是整治引入。
- 位置：`backend/deskpet/tools/registry_domains/prepared.py` 约 729 行，`except TimeoutError:` 分支里 `future.add_done_callback(...)`；该函数里没有 `future` 这个名字。
- 影响：预备调用超时、标记为晚到挂起、且注册了就绪回调和执行上下文时，会抛 NameError，晚到结果的回调接不上。
- 下一步：单独修（补上被等待的 future 或改用已有的任务句柄），配一条超时路径的测试。
- **已修（阶段 8）**：改用登记进 `_late_prepared_calls[effect_id]` 的那个 future；用例 backend/tests/test_prepared_late_timeout.py 修复前报 NameError、修复后通过。属行为变更，阶段末交独立核验签字。

## F-7 能力存储事务视图里三个方法定义了两次（归阶段 8）——2026-10-17 阶段 8 已删

- 登记：2026-10-16，同上审计；拆分前的 `capabilities/store.py` 原样存在。
- 位置：`backend/deskpet/capabilities/store_domains/core.py` 的 `create_skill_install_intent` / `cas_skill_install_intent` / `handoff_skill_install_intent`，类里前后各一份，后者覆盖前者。
- 下一步：阶段 8 确认哪份生效后删掉另一份。
- **已删（阶段 8）**：类体里后定义的覆盖先定义的，前一份（转交给仓库的 `_..._tx(self._db, …)` 版）从来调不到；仓库类 `CapabilityStore` 经 setattr 转绑的也是后一份（自持事务版）。删掉前一份，行为不变。用例 backend/tests/capabilities/test_store_tx_install_methods.py 删前删后都通过（锁住"事务视图与仓库类上是同一个自持事务版函数"）。

## F-8 工作流派发循环出错时可能原地空转（潜在缺陷，阶段 7 核验发现）

- 位置：backend/deskpet/workflows/launcher.py 的 `_dispatcher_loop`（约 975～990 行）。
- 现象：`dispatch_due_work()` 一直抛异常、而下一个截止时间已经过去时，等待超时算出来是 0，循环原地反复转。原来异常被静默吞掉，看不出来。
- 阶段 7 给这处补的日志先用 debug，避免每圈一条带堆栈的警告刷满日志。
- 建议：出错后按次数退避（例如 1、2、4 秒，封顶 30 秒），修好后把日志改回 warning。配一条"派发持续失败不空转"的用例。

## F-9 只被测试使用的产品接口（160 个，阶段 8C-3 登记）

- 登记：2026-10-17。8C-3 用语法树重新分类后，160 个符号在产品代码里没有调用方，但测试借它们驱动或核对在用代码（例：`get_command` 16 个测试、`claim_job` 11 个测试）。
- 清单：阶段8-删除清单-8C3.md 的"保留"一节。
- 不删的原因：连测试一起删会丢掉对在用代码的覆盖。
- 建议：以后逐个判断。确实只为测试存在的，挪到测试辅助模块（如 `agent_orchestrator/testing/`）；接口本身该给产品用而没接上的，查清是不是漏接。

## F-10 能力激活后的安全监控做好了但没接进产品（待接线，2026-10-17 阶段 8K 登记）

- 位置：`backend/deskpet/companion/activation_guard.py`。
- 现状：只有它调用 `record_capability_guard_incident`（capability_activation.py:653）；生产里能力激活在跑（production_pipeline.py:1746、:1880），但没有任何地方构造它。
- 依据：阶段8-偏差单-不可达代码.md 独立裁决第 4 条；用户 2026-09-27 定"做好的功能都开启"。
- 下一步：按功能开发流程接线并默认开启（先写能复现"激活后出事故被记录"的测试）。不在代码质量整治范围内。

## F-11 代码恢复工作流的上下文工厂导入一个不存在的模块（缺陷，2026-10-17 登记）

- 位置：`backend/host/startup_workflows.py` 的 `_build_code_recovery_context`，函数内 `from deskpet.harness.adapters.legacy_execution_migration import resolve_frozen_legacy_workspace`。该模块从 2026-08 分叉起就不存在。
- 影响：code_complex、durable_task 两个工作流重启后恢复时，走到这里抛 ImportError。注释标的"死代码待删"是误标：函数在用，导入坏了。
- 关系：与 F-1（durable_task 节点引用不存在的属性）同属 durable_task 恢复链。
- 下一步：先定恢复时工作区从哪里取（这是功能修复），写能复现的测试再修。不在代码质量整治范围内。

## F-12 上下文尝试账本做好了但生产里从不构造（待接线或删除，2026-10-17 阶段 8K 登记）

- 现状：`agent/context_report.py` 的 `ContextAttemptStore` 与 `set_context_attempt_store` 在生产里没有调用方；服务槽位 `context_attempt_store` 从不登记。所以阶段 8G 做的上下文尝试挂钩在生产里一直是直通，`host/context_usage.py`、`host/control_sessions.py`、`p4_ipc.py` 读这个槽位的分支恒走"没有"。
- 同族槽位 `context_snapshot_store`、`context_segment_store`、`compression_model_resolver` 也从不登记。
- 本阶段的处理：按"做好的功能都开启"规则不删，保留槽位与读取分支。
- 下一步：由用户定——接线并默认开启（Context OS 上下文账本），还是整族删除。

## F-13 v7 深度研究的恢复适配器每次都失败（归"删工作流线"第 2 片，2026-10-17 登记）

- 位置：`backend/host/startup_workflows.py` 的 `_deep_v7_context_factory`。它读服务槽位 `subagent_scheduler`，该槽位从不登记，恢复 v7 运行时抛 `subagent_scheduler_unavailable`。
- 生产不新开 v7 运行（深度研究工作流启动器恒为 None），只剩恢复入口。v7 定义是编解码钉死文件。
- 本阶段不动：后端工作流引擎整条线在"删工作流线"第 2 片里统一处理（见记忆 workflow-line-removal-slices 与 CLAUDE.md"两套引擎"欠账）。
