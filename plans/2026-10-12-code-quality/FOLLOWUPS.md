# 代码质量整治：后续事项（followup）

整治只改形状、不改行为。整治中查出、但属于功能修复或另一阶段的事，登记在这里，不在当前切片里修。

## 2026-10-18 收尾补充处理结果（用户逐项定，分支 cq-followups 第 1～15 片）

| 项 | 用户决定 | 结果 |
|---|---|---|
| F-10 能力激活后安全监控 | HTN 之前的旧件，删 | 第 1 片删 |
| F-12 上下文尝试账本 | 删 | 第 4、7 片删整族 |
| F-16 主对话任务范围面板 | 删 | 第 6 片前后台删 |
| F-17 自愈横幅 | 甲：整条删 | 第 8 片删自动续跑整条线（监工保留） |
| F-18 旧音频通道 | 删 | 第 5 片删第一层；第 9 片删本地语音整层与首启下载器；第 15 片删实时语音通话 |
| F-19 / F-24 死代码 | 清掉 | 第 3、7 片删（约 1.1 万行后台、6 个脚本、样式件） |
| F-21 前端不可达模块 | 删旧会话界面 | 第 14 片删 ChatView 整棵（59 个源文件、32 个测试） |
| F-22 前端依赖与锁文件 | 同意重装 | 主检出按锁文件 `npm ci` 重装（旧 pnpm 副本留在 `tauri-node_modules.pnpm-bak-20261018`，确认无用后删） |
| F-23 V1 后缀 | 改 | 第 2 片改 9 个；GrowthSignalV1 等 3 个后缀与 schema_version 同号，按规范保留 |
| 新：用量环与切换模型 | 接到主对话 | 第 10～13 片：后台补用量与明细（`primary.context.usage`、`primary.context.breakdown`），前端标题栏接环、明细窗口、切换模型 |

未做、转后续：

- **F-26 伴侣卡片后台**（设计 §5.3）：2026-10-18 用户"同意，删伴侣卡片后台"（已知两个后果：伴侣提醒没有地方显示；中、高风险成长候选永远停在"等确认"）。分支 cq-companion-card 删除：五种决定服务（成长评估／激活／动作确认、回滚、遗忘）与 `companion_action_ready` 通用执行分支、详情查询 `detail_query.py`、`action_decisions.py`、控制通道 `companion_detail_get` 等 6 种消息、会话补读里的 `companion_events`、通知服务的历史开关；前端删没人调用的 `send_companion_action` 与签名挑战状态、详情消息类型与详情缓存。保留：通知投影服务与绑定后排空、`companion_action` 连接作用域、激活分发器（成长权威在用）。证明：后台相关测试 46 个文件、前端相关 12 个文件（156 条）全过；控制通道快照只少 6 种消息与 `companion_events` 字段；启动快照只少 7 个服务位；禁区比对只减不增（463 条字面量随删除消失，放行清单在 `.local-test-evidence/2026-10-18/cq-companion-card/`）；导入冒烟后台 681 个模块。
- **F-27 PPT 大纲卡后台**（设计 §5.4）：已随"删后台工作流引擎"第 2、3 片删除（host/ppt_outline.py、大纲决定处理、大纲存储）。
- **F-28 Service SDK 的 realtime 依赖**（设计 §5.1 末）：实时语音删后无人用。要改 pyproject.toml、vendor 轮子、打包清单与冻结包测试，需要 `uv lock`／`uv sync`，工作树里不跑，放到主检出单做。
- **F-29 可能已无人用的依赖**：edge-tts、faster-whisper、silero-vad、torch/torchvision/torchaudio、FlagEmbedding、transformers、sentence-transformers、peft、qwen-vl-utils。删本地语音与旧模块后要逐个核实再删（牵动打包与安装时间）。
- **F-30 旧会话界面删后的二次重扫**（设计 §6.4）：InputBar 非主对话分支、sessionsStore 收件箱与严重度、controlWs 会话补读与 `chat_v2`、`session_set_model`、`harness_inspector_*`、`companion_action` 作用域等。规模大，先出方案。
- **F-31 杂项尾巴**：ARCHITECTURE.md §15 整体过时；`scripts/e2e/fixtures/context-os-automation-manifest.json` 列了已删测试；生成样式里留下没人用的类（生成器只能内联转类）；`paths.resolve_model_dir` 与 `publish_models_to_cos.py`；`startup_profile_baseline.json` 有已删模块条目；`harness_parity_census.py` 已坏；工作树 `node_modules` 的 vite 是 8.0.8，与 `webkit_style_compare.py` 记录的 8.1.3 不同（前后同一压缩器比对时不受影响）。

## F-1 持久任务工作流引用了不存在的两个属性（缺陷，待修；2026-10-18 改写：SDK durable_task 宿主未接，接入前先修）

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

## F-2 WorkflowProviderAdapter.invoke 多传一个参数（死代码，归阶段 8）——2026-10-17 阶段 8 已删

- 位置：SDK `simple_harness/workflow/adapters.py`。调用会 TypeError；该类产品代码不用。
- 现状：挂 `# type: ignore[call-arg]` 加原因注释。阶段 8 查死代码时证明无调用方后删除。
- **已删（阶段 8）**：整个 `simple_harness/workflow/adapters.py`（两个适配器）在 SDK 与 Host 产品代码里都没有导入方，只被专测它的 tests/unit/workflow/test_activation_adapters.py 导入；按 8C-3 裁决连专测文件一起删。

## F-3 LeafAcceptanceAssembly.accept 的 producer_agent_ids 参数不生效（死参数，归阶段 8）——2026-10-17 阶段 8 已删

- 参数在函数内被覆盖，调用方传了也不生效。阶段 8 处理。
- **已删（阶段 8）**：删掉这个参数与两处调用方传的值（commit_service_domains/results.py），作者仍以结果投影里的为准，行为不变。

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

## F-8 工作流派发循环出错时可能原地空转（2026-10-18 关闭：launcher.py 随后台旧工作流引擎删除）

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

## F-11 代码恢复工作流的上下文工厂导入一个不存在的模块（2026-10-18 关闭：startup_workflows.py 整个删除）

- 位置：`backend/host/startup_workflows.py` 的 `_build_code_recovery_context`，函数内 `from deskpet.harness.adapters.legacy_execution_migration import resolve_frozen_legacy_workspace`。该模块从 2026-08 分叉起就不存在。
- 影响：code_complex、durable_task 两个工作流重启后恢复时，走到这里抛 ImportError。注释标的"死代码待删"是误标：函数在用，导入坏了。
- 关系：与 F-1（durable_task 节点引用不存在的属性）同属 durable_task 恢复链。
- 下一步：先定恢复时工作区从哪里取（这是功能修复），写能复现的测试再修。不在代码质量整治范围内。

## F-12 上下文尝试账本做好了但生产里从不构造（待接线或删除，2026-10-17 阶段 8K 登记）

- 现状：`agent/context_report.py` 的 `ContextAttemptStore` 与 `set_context_attempt_store` 在生产里没有调用方；服务槽位 `context_attempt_store` 从不登记。所以阶段 8G 做的上下文尝试挂钩在生产里一直是直通，`host/context_usage.py`、`host/control_sessions.py`、`p4_ipc.py` 读这个槽位的分支恒走"没有"。
- 同族槽位 `context_snapshot_store`、`context_segment_store`、`compression_model_resolver` 也从不登记。
- 本阶段的处理：按"做好的功能都开启"规则不删，保留槽位与读取分支。
- 下一步：由用户定——接线并默认开启（Context OS 上下文账本），还是整族删除。

## F-13 v7 深度研究的恢复适配器每次都失败（2026-10-18 关闭：v7 适配器随后台旧工作流引擎删除）

- 位置：`backend/host/startup_workflows.py` 的 `_deep_v7_context_factory`。它读服务槽位 `subagent_scheduler`，该槽位从不登记，恢复 v7 运行时抛 `subagent_scheduler_unavailable`。
- 生产不新开 v7 运行（深度研究工作流启动器恒为 None），只剩恢复入口。v7 定义是编解码钉死文件。
- 本阶段不动：后端工作流引擎整条线在"删工作流线"第 2 片里统一处理（见记忆 workflow-line-removal-slices 与 CLAUDE.md"两套引擎"欠账）。

## F-14 SDK 里没有导入方、但属于暂缓或冻结工作的模块（2026-10-17 阶段 8 登记，不删）

- `agent_orchestrator/domains/drone_sim.py`：无人机模拟世界。跨领域验收（seed_methods/drone-sim-v1、role_templates、cross_domain_acceptance）还在引用这个领域的工具名；用户 2026-10-02 定"跨领域暂时不做"。
- `agent_orchestrator/evaluation/appworld_operation_profiles.py`、`appworld_resume.py`：AppWorld 评测（冻结的评测线）。
- `agent_orchestrator/orchestrator/taskgraph_review_evidence.py`：执行图根审阅读取原始核验事实，可能是做好没接上的功能。
- 下一步：随对应主线决定接线或删除。


## F-15 记忆面板两条控制消息初始化完成后不回任何消息（缺陷，2026-10-17 登记）

- 位置：`backend/host/control_memory.py` 的 `_cc_memory_summarize_now`、`_cc_memory_thumbs_up`。
- 现象：检查通过（已初始化、开关开）以后函数直接结束，不发回执；前端等不到 `memory_summarize_response` / `memory_thumbs_up_response`。原实现随认知记忆 SDK（2026-09-10）移除，只剩前面的拒绝分支。阶段 2 记录里写的"记忆整理消息在初始化完成时什么也不做"就是这处。
- 下一步：定产品行为——随记忆 SDK 一起删掉这两条消息与前端入口，或明确回"不支持"。涉及前端，不在代码质量整治范围内。

## F-16 主对话的任务范围面板没有入口（待用户定：接入或整条下线，2026-10-17 阶段 9K 登记）

- 前端：`tauri-app/src/components/PrimaryTaskPanel.tsx` 与请求层 `src/primary/taskScopeRequests.ts`（连测试夹具 `src/primary/testing/taskScopeFixture.ts`、`graphFixture.ts`）。唯一挂载点是记忆抽屉的「任务」页签，2026-09-10 删记忆面板（4087be659）时一起断开，之后从 `src/main.tsx` 不可达；当时提交写"保留并保证可用"，但没给新入口。
- 后端：`task_scope.search / list / open_exact` 仍在服务（`backend/deskpet/memory/human_memory_api.py` 约 248～270 行）。
- 本阶段的处理：按"做好的功能不删"保留，不改。
- 下一步：由用户定——给它一个新入口（例如主对话侧栏），还是前后端整条下线。

## F-17 自愈横幅没有挂上（待用户定：挂到会话视图或连状态字段下线，2026-10-17 阶段 9K 登记）

- 前端：`tauri-app/src/code-panel/AutoResumeBanner.tsx` 与 `autoResumeBannerText.ts`，fork 起从未挂载。`controlWs.ts` 收到 `auto_resume_started / succeeded / exhausted` 时写会话状态 `auto_resume_attempts`，产品里没有任何读端。
- 后端：AutoResumeOrchestrator 默认开启（`config.toml` 的 `auto_resume_enabled = true`；`backend/host/startup_supervision.py`），仍发 `auto_resume_*` 事件。
- 本阶段的处理：保留，不改。
- 下一步：由用户定——把横幅挂到会话视图，还是连同 `auto_resume_attempts` 状态字段一起下线。

## F-18 后端旧音频通道还在（2026-10-17 阶段 9K 登记）

- 前端旧音频路径（`ws/AudioChannel.ts`、`hooks/useAudioChannel.ts`）已在 9K 删除：Realtime 是唯一语音路径（`hooks/useRealtimeVoice.ts`，`voiceAvailability.ts` 注释写明旧路径关闭）。
- 后端 `backend/host/routes_voice.py` 的 `/ws/audio` 与 `backend/pipeline/voice_pipeline.py` 仍在，前端已无调用方。
- 下一步：确认没有其他客户端后另行删除（后端改动，不在 9K 范围）。

## F-19 转换前就没人用的样式件（2026-10-17 阶段 9I 登记）

- `tauri-app/src/App.css`：没有任何文件引入（9I 裁决核实），不进产物。
- `tauri-app/src/theme/components.ts` 的 6 个导出在 9I 之前就没有引用：`tokensExport`、`segGroup`、`sectionTitleText`、`drawerScrim`、`DRAWER_WIDTH`、`drawerSurface`。
- 9I 转换后新变成没人用的 17 个样式常量已在 9I 删除（components.ts 10 个及随之没用的类型 `DarkBtnVariant`、settingsStyles.ts 4 个、messageStreamStyles.ts 2 个、chatViewStyles.ts 1 个），另把只在本文件用的 `btnStyle` 改成不导出。
- 下一步：上面这些按删除清单统一删（裁决要求 App.css 另走删除清单，不在 9I 顺手删）。

## F-20 旧系统上 4 处毛玻璃会开始生效（2026-10-17 阶段 9I 核验提示，待用户知悉）

- 生成类 FeedbackPanel-1、SkillStorePanel-12、SkillStorePanel-38、ConfirmDialog-1 原内联只写无前缀 `backdrop-filter`。发版压缩时补上 `-webkit-backdrop-filter`。
- WebKit 26.5 两种写法都认，比对无差别；Safari 18 之前的 WebKit（发版最低 macOS 13）原内联不生效，转类后会出现毛玻璃。方向与作者本意一致、影响小。
- 下一步：用户知悉即可；如要保持旧系统原样，再议。

## F-21 9K 漏列的运行时不可达模块（2026-10-17 阶段 9I 核验提示）

- 19 个已转换文件的生成类不在发版产物里：这些模块只被 `import type` 引用，运行时不可达。例如 ChatView、MessageStreamPanel、SubagentProgressPanel、MessageBubble、SessionList。
- 《阶段9-偏差单-前端不可达模块》只按 13 个模块处理，漏了这一批。
- 补充（阶段 10C）：MessageStreamPanel、MessageStreamRows、messageStreamTypes 里有编码损坏的乱码注释（UTF-8 被当 GBK），MessageStreamRows.tsx 的界面文字 `"(宸ュ叿)"` 是乱码字符串（原文应为"(工具)"）；若保留这组组件，要一起修复。
- 下一步：按前端真实导入可达性（值导入，不算 `import type`）重扫一遍，出清单；做好但没接线的功能按规则记待用户定，其余走删除清单。

## F-22 本机前端依赖与锁文件不一致（2026-10-17 阶段 9 合并后 CI 查出）

- 主检出 `tauri-app/node_modules` 是 pnpm 按 package.json 的版本范围装的（工作树借用它），CI 用 `npm ci` 按 package-lock.json 装。实测 eslint-plugin-react-hooks 本机 7.1.1、锁文件 7.0.1，两版对 set-state-in-effect 的判定互相错开，本机 eslint 干净而 CI 报 2 个错误、2 个警告（9D-13 已改代码，使两版都干净）。
- 其他依赖可能也有版本差。
- 下一步：本机改用 `npm ci` 按锁文件装（需要下载，先问用户），或把锁文件升到本机版本（同样需要下载）；在那之前，前端 lint 结论以 CI 为准，本机可从 npm 缓存离线取锁文件版本复核。

## F-23 可安全去版本后缀的命名（2026-10-17 阶段 10B 登记）

- 依据：阶段10-带版本号命名清单.md（逐项证据：引用处数与位置、五项身份核查——结构标题、kind/type 字面量、注册名与工具结构、工作流节点源码、钉死与各种清单）。
- 归阶段 11 尾巴清单（15 个，都在后端 companion 与 tools/prepared_snapshot，不碰 SDK、不碰执行构建清单，按模块整批改）：PreparedSkillToolIntersectionV1、GrowthSignalV1、CandidateFinalizeProofV1、TrustedCapabilityFailureReceiptV1、GuardBindingSnapshotV1、GuardMonitorOutcomeV1、WorkflowDataflowValidationV1、CandidateImmutableRiskFactsV1、FirstPartySkillTemplateV1、TrustedActivationDecisionProofV1、TrustedLifecycleCandidateSourceV1、CandidateBuildLaunchStateV1、LiveReflectionEvidenceV1、LiveGrowthTargetFactsV1、HostNeutralSemanticGrowthResultV1。
- 归第二轮（88 个加前端 1 个）：引用面大、使用方在深度研究线或去留未定的后端工作流引擎；其中 EvaluationVerdictV1、PreparedRunCatalogLeaseV1 去后缀会重名要另取名，PublicRunSnapshotV3 要前后端一起改。
- 注意：甲类里 23 个类的后缀与类内 schema_version / version 字段相同，属"冻结合同"命名，按 10D 的命名规范判断是否保留后缀。
- 乙类（身份或协议版本）与丙类（多版本并存，删旧版归删工作流线与两套引擎欠账）不在此项。

## F-24 阶段 10C 顺带发现的死代码候选（2026-10-17 登记）

- 疑似死脚本 6 个（文件头核对清单有理由）：backend/scripts 的 cdp_a_class_recon、cdp_a_class_run、cdp_pet_g2_test、hybrid_pet_click（连已移除的桌宠窗口）、download_bge_m3（依赖不存在的 embedder.py，下载格式与执行池读的本机量化模型不符）、setup_models（whisper 那半可能仍有用）。
- backend/agent/ 的 plan、context_manager、termination 只剩测试在用；workflow_trace、tool_context_persistence 连测试都没有；deskpet/agent/ 的 context_segment_store、context_snapshot_store 几乎没人导入。
- research_tools 的语义打分钩子 `_SEMANTIC_SCORER` 始终为 None，语义打分路径不执行。
- model_provisioner.py 的下载清单里 wemm-embedding-2b 向量模型没有使用方。
- 下一步：与 F-21 一起按真实导入可达性重扫，出删除清单；做好但没接线的功能按规则记待用户定。

## F-25 阶段 10 正则口径外的代号与修复历史（2026-10-17 登记，独立核验提示）

- 代号正则认不出的写法仍在注释里：`D6-1`、`S4-07`、`T6 冻结`、`B12`、`WI-TG-2`、`Phase 1.1.6`、`Option A (2026-06-05)`、`Step 4 (Incident Z / 事件 AG)` 等。
- 不带代号的修复历史，如 `runtime/assembly.py:95` 的"2026-10-10 用户决定（parse 重跑）……"。
- 文档字符串里的代号（约 276 行）本阶段不动，见阶段 10 实施记录执行条件 7。
- 下一步：若要继续收紧，先扩正则口径并下调基线，再按阶段 10 同样的只改注释证明逐批处理。不阻断。
