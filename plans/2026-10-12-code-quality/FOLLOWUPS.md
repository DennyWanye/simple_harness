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

## F-5 上下文请求规划器疑似死代码（归阶段 8）

- 登记：2026-10-16，阶段 6D 拆 `prepare_initial` 时查出。
- 位置：`backend/deskpet/agent/context_request_planner.py` 的 `ContextRequestPlanner`。
- 现象：生产代码里没有任何地方构造它，也没有注册 `context_request_planner` 服务（`context.py` 只声明了字段，`turn_preparer.py` 读取时恒为空）；测试也不覆盖它。
- 6D 对它的拆分由 extract_check 内联核对证明逐字等价，但没有运行期测试能观察到。
- 下一步：阶段 8 查死代码时确认无调用方后删除整个模块与 `turn_preparer` 里的读取分支。

## F-6 工具注册表超时路径引用未定义的 future（潜在缺陷）

- 登记：2026-10-16，阶段 6D 审计全分支未定义名字时查出；拆分前的 `deskpet/tools/registry.py` 原样存在，不是整治引入。
- 位置：`backend/deskpet/tools/registry_domains/prepared.py` 约 729 行，`except TimeoutError:` 分支里 `future.add_done_callback(...)`；该函数里没有 `future` 这个名字。
- 影响：预备调用超时、标记为晚到挂起、且注册了就绪回调和执行上下文时，会抛 NameError，晚到结果的回调接不上。
- 下一步：单独修（补上被等待的 future 或改用已有的任务句柄），配一条超时路径的测试。

## F-7 能力存储事务视图里三个方法定义了两次（归阶段 8）

- 登记：2026-10-16，同上审计；拆分前的 `capabilities/store.py` 原样存在。
- 位置：`backend/deskpet/capabilities/store_domains/core.py` 的 `create_skill_install_intent` / `cas_skill_install_intent` / `handoff_skill_install_intent`，类里前后各一份，后者覆盖前者。
- 下一步：阶段 8 确认哪份生效后删掉另一份。
