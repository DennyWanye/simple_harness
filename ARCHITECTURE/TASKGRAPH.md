最后更新：2026-10-07 CST（推后必补第 3 批，待发版 SDK `opt.171`；记录 `plans/2026-09-27-desktop-next/完成度严格评估-2026-10-06/推后第3批-*.md`）。
- **执行图端口（R1）**：声明要转换格式而没有转换器时按名拒绝，绑定上的 `converter_ref` 删除（T04）；输入端口补"有序列表"与"按键映射"（映射按数据要求声明的键绑定，重复键拒绝）（T05）。大计划不设持久准备阶段，仍在一个事务里完成准备与激活（H02，B 级 #49）。
- **保证通道与知识（R2）**：已批准的完成规格进申请单审查材料（A11）；历史视图按当时那一版要求显示准则，读不回来按"来源不可用"拒绝（A22）；知识记录带 `modified_by`（谁、哪个任务与步骤、何时），被反驳与被取代都在同一次写入里记（K07）；恢复第 3 步给被隔离任务只读列出在等的保证待办（最多 64 项），被隔离任务自己的行不写（A21，B 级 #50）。
- **预算、资源申请、审阅积压、监控（R3）**：预算加"执行者数""搜索次数"两维（迁移 48；GPU 不做 B 级 #51）；执行者申请更多资源走"提案 → 只核上限与额度 → 规划器判"，执行者单回合工具上限 = 网关上限 + 共用余量 8（`TOOL_ANSWER_MARGIN`，与审阅员同一个），用完次数时能看到"可以申请"的拒绝话（H10，B 级 #52）；审阅积压时按事件加审阅并发，上限 `max(审阅数, min(2×审阅数, 模型名额))`（桌面默认 2），并暂停开新的规划轮最长 600 秒，并发上限默认值与准入身份不变（H12，B 级 #53）；监控补八项指标，全部按事件或表行计数（U05）。
- **编排消息公开合同（R4）**：执行图主画面、回合详情、任务列表、任务详情、事件、待批准、通知七个读动词补公开 schema，Host 转发前、前端收到后按同一份核（U09）。
- **顺带修 8 条老红**（都是测试替身或钉住的基线没跟上，产品没问题）：接力生产者假派发缺写入冲突检查、迁移备份名与版本号写死、提示词钉哈希基线停在旧版、空转用例假编排器缺开工关口。

最后更新：2026-10-07 CST（推后必补第 2 批，已发版 SDK `opt.170`；记录 `plans/2026-09-27-desktop-next/完成度严格评估-2026-10-06/推后第2批-*.md`）。
- **策略行经存储层、知识按分层距离挑选**：见 `AGENT_ORCHESTRATION.md` 同日条目（T09、K06，B 级 #46）。

最后更新：2026-10-06 CST（严格评估后补齐第 2～4 批，合并中，待发版 SDK `opt.166`；记录 `plans/2026-09-27-desktop-next/完成度严格评估-2026-10-06/``第2批-车道L-记录.md`、`第2批-车道J-记录.md`）。**八份对外合同进 SDK**（原计划附录 E）：`graph/schemas/*.schema.json` + `graph/view_contracts.py`，三种视图经严格编解码返回；收敛视图合同升 v2（加必填 `blocked_notifications`，界面 `PlanChangePanel.tsx` 在读它）。**改要求时允许改目标**：`{op: "goal", statement}` 作为一次要求修订；Host 入口尚未放行。**子目标跨版本沿用**：现状自下而上，自上而下未做（偏差单待用户定）。**死锁具名停止**：`scheduling/wait_for.py` + `MissionStopReason.DEADLOCK`（原计划 §10.5）。明细见 AGENT_ORCHESTRATION.md 同日条目。

最后更新：2026-10-06 CST（严格评估后补齐第 1 批，SDK `opt.165`）。**部署验收门首次可写 VALIDATED**：`scripts/acceptance/taskgraph_gate.py` → `taskgraph_manifest.py validate --gate`，读取方核验 `acceptance_evidence`，Host 启动只认 VALIDATED（原计划 §0.3 / §16；`TASKGRAPH_REAL_MODEL`、`INDEPENDENT_REVIEW` 记 PENDING）。**执行图秩序按类型码**：`SharingRefused` / `AmendmentRefused` / `CodedStoreConflict` 取代按异常文字切码；只读接口 9 个码登记进错误码表。**清单绑定按输入版本号**（迁移 44）：`tg_attempt_identity_guard` 恢复相等。明细见 AGENT_ORCHESTRATION.md 同日条目与 `plans/2026-09-27-desktop-next/完成度严格评估-2026-10-06/第1批-车道A-记录.md`。

最后更新：2026-09-28 CST（NEXT-TG-1.0 第三批）。任务页执行图改为读 SDK 的 `execution_snapshot` / `execution_detail`（与 `taskgraph.snapshot` 同一读取令牌；已要求执行图但尚未启用的任务读图报 `ACTIVATION_PENDING`；`diff` 请求不存在的修订报 `REVISION_NOT_FOUND`）。上面 2026-09-26 那条"界面改用 Host 运行视图"已不成立：`mission_live_graph` 已删除。

最后更新：2026-09-27 CST（NEXT-TG-1.0 第 2A 批）。**新任务默认走严格执行图**：Host 在创建事务内写入执行图要求（SDK 迁移 29 `taskgraph_requirements`，只增不改），规划授权到位后由系统协调器以任务派生的命令 id 启用执行图（自动授权后、手动授权后、每轮循环重试；拒绝记部署故障，任务等待、绝不退回无执行图规划）；未绑定前规划派发暂缓，绑定前提交计划被拒 `TASKGRAPH_REQUIRED_NOT_BOUND`；`[orchestration] strict_taskgraph = false` 是唯一关闭方式；旧任务不转换。部署清单 `taskgraph_deployment_manifest.json` 已按**真实上游证据**合法重建：隔离桌面环境真实 HTN 核心链任务 `mission-a22c9fc39b8abed8`（写 NOTES.md → 候选 → 操作审查 → 界面批准 → file_publish → 发布字节与验收产物一致 → 完成，SDK opt.39）+ 冷重放回执逐字节一致；生成/核验脚本 `sdk/simple-harness-sdk/scripts/build/taskgraph_manifest.py`（4 个反例全部拒绝），证据 `.local-test-evidence/2026-09-27/batch2a-upstream/`。真机小验收（opt.41，保障层开）：新任务要求写入→系统启用→规划提交携带执行图修订→完成；`taskgraph.snapshot/why_not_ready/diff/convergence` 走真实控制通道均有回复。期间修复：规划请求/判定的范围纪元摘要不计保障层自身证据纪元 `assurance:mission`（否则执行图任务每次规划回复都判过期）。TaskGraph 完整验收（42 场景等）仍 NOT_RUN。上面 2026-09-26 那条"真实任务恒为 NOT_ENABLED"只对本批之前创建的任务仍成立。

最后更新：2026-09-26 CST。产品界面的执行图不再走本文的严格读取（`taskgraph.snapshot` 等）：真实任务从未开启执行图内核，读取恒为 `NOT_ENABLED`。界面改用 Host 只读的运行视图 `mission_live_graph`（见 UI.md、AGENT_ORCHESTRATION.md）；严格读取动词仍保留在后端。

最后更新：2026-09-23 CST（Assurance 第九段，Host/UI 接线）。`development/taskgraph-host-overlay/` 的 7 个 Host 源文件（`backend/deskpet/orchestration/{handlers,hierarchical,service,taskgraph}.py`、`tauri-app/src/stores/taskgraphStore.ts`、`views/MissionTaskGraph.{tsx,css}`、`views/MissionsView.tsx`）已覆盖进根 Host，随后在其上接 Assurance（`service.py`/`handlers.py`/`hierarchical.py` 有 Assurance 增量，见 ASSURANCE.md）。根 Host 改钉 `0.13.0.dev20260923+assurance.1`（含 TaskGraph23 + HTN 最终源码 + Assurance）。前端 875 vitest 通过；overlay 自带的 `MissionsView.tsx:616` react-hooks/refs eslint 报错保留未改。TaskGraph 后置 42 场景等仍归 Assurance 第 10 项一次完整验收。

最后更新：2026-09-23 CST。TaskGraph用户限定的主体阶段验收 PASS：最终SDK taskgraph.23 + Host UI2。HTN DeepSeeker真实CONTENT_ONLY Mission正式COMPLETED，107.08秒/11物理调用/49646tokens/0未知；4只读入口及同候选rebuild前后1Attempt/6intents/106events/1revision/11calls不变。原生实际点击通过当前/历史/why/diff/convergence及错误恢复；后台启动先消费8条原followup，UI读取前后114events不变、0新增调用。实际修复根评审测试证据缺失、Mission judge视图身份误挡、React key重复和深色对比度。完整42/变异/stateful/legacy/H1/统计/完整UI验收依用户指令后置，完整acceptance仍NOT_RUN；共享Host/HTN未覆盖。独立交接核验full/round1 PASS、0阻塞；2026-09-23 02:47 CST已成功通知第三部分接入开发。详见[主体结果](../plans/TaskGraph/V1.2.1/MAIN-RESULTS-2026-09-23.md)与[后续交接](../plans/TaskGraph/V1.2.1/TASKGRAPH-MAIN-HANDOFF-2026-09-23.md)。下文旧检查点为历史。

最后更新：2026-09-23 CST。TaskGraph主体已进入用户限定的小规模验收，当前候选taskgraph.23。真实DeepSeeker验证暴露并修复两处接线缺口：根评审缺原测试回执、Mission judge视图ID误作Attempt ID；23候选正在复验，尚未通知第三部分。重型42/变异/stateful/全H1/统计/完整UI矩阵依用户指令后置整体集成，完整acceptance仍NOT_RUN；本阶段判定见[主体阶段验收](../plans/TaskGraph/V1.2.1/MAIN-ACCEPTANCE-2026-09-23.md)。共享HTN/Host未覆盖。下文旧时点均为历史。

最后更新：2026-09-23 CST。TaskGraph候选19整体IN_PROGRESS。修复冷启动先恢复工作区后装配TaskGraph的顺序：SDK可信startup_assembly与Host首启/rebuild现早于运行池恢复。原Worker物理响应后强退的单窗口通过，原collector/recover结算且0重复调用；Host复制UNKNOWN库首启/重建保持原5物理调用、预算占用与围栏。窄独立只读挑战无已证实P0/P1/P2；非主体整体审计或产品验收。597包文件一致，acceptance NOT_RUN。 详见[执行日志](../plans/TaskGraph/V1.2.1/EXECUTION-JOURNAL.md)候选19条目。下文旧检查点为历史。

最后更新：2026-09-23 CST。TaskGraph当前冻结候选18（597包文件，acceptance NOT_RUN），整体IN_PROGRESS。新增终态不再开工作、离线replay严格JSON修复；候选17取消围栏场景通过，候选18离线历史/3个Commit写点回滚/原回复撤权后幂等/2个真实进程退出窗口有具名局部证据。正式SDK用例已开始建设，非42场景全覆盖或产品验收；没有扩大回归或替换共享Host/HTN。详见[执行日志](../plans/TaskGraph/V1.2.1/EXECUTION-JOURNAL.md)最新候选17/18条目。下文旧记录为历史。

最后更新：2026-09-23 CST。整体 IN_PROGRESS；隔离工作源码已超过冻结候选 taskgraph.16。新增修复：启用 TaskGraph 的首次 epoch 失效从0推进到1；损坏通知持久 BLOCKED且保留原文，下一条有效消息仍可领取；损坏事件游标拒绝推进且不会使故障记录再次索引报错。两项窄诊断及一个原 legacy 用例通过，5文件语法检查通过；未运行批量回归。候选16的来源/通知同事务及收敛期间撤回授权已有具名局部证据，撤权后仍 revision1/READY fenced、原回复 COMMIT_REJECTED，无重新规划。正式42场景、真实模型、原生UI及独立评审仍 OPEN。当前源码与候选证据不可混用，详见[执行日志](../plans/TaskGraph/V1.2.1/EXECUTION-JOURNAL.md)。下文旧候选记录均为历史。

# TaskGraph 执行网络

## 2026-09-22：运行中工作项保守门禁定点检查

候选 taskgraph.10 环境运行原 H1-H `test_h1h_retired_running_work.py`：`2 passed in 1.53s`。未过期和已过期的 foreign lease 都被完整纳入 RuntimeWorkSnapshot，PlanCommit 均拒绝为 `RUNNING_WORK_UNRESOLVED`；图版本、Attempt 状态、lease owner 和 lease expiry 均保持不变。该检查验证原收敛保护边界，不等于 TaskGraph 真实模型/在途 SDK 取消验收。


## 2026-09-22：Selection 公共提交定点核对

候选 taskgraph.10 环境运行单项 Selection 公共提交检查 `test_oc2_selection_winner_commits_preparation_not_effect_or_action`：`1 passed in 1.25s`。检查确认 Selection winner commit 保持 PREPARATION 语义，不发布 Action/Effect，不把候选材料直接当正式完成。该检查来自原 Selection/Completion 合同，尚未证明 TaskGraph 与真实模型/UI 的整体 Selection 场景；TaskGraph 联合 DATA+Selection 挂载仍需独立场景接线。


## 2026-09-22：正式 DATA 消费类型与非空输入接线诊断

候选 `0.13.0.dev20260922+taskgraph.10` 增加桌面部署的正式 `desktop.continue-delivery` primitive 类型，声明 `delivery` 输入端口；原 `desktop.prepare-delivery` 合同保持不变。隔离 Host 两节点脚本走通：上游接受 `delivery` 输出后，下游实际读取同一已验收文件，完成 Mission；TaskGraph Attempt 冻结 manifest 含 1 个 DATA binding，upstream 为正式 `NOTES.md` artifact。未声明的 `upstream.txt` 被正确排除，确认不会从 workspace overlay 猜测输入。

当前 wheel SHA-256 `7bc0fab6a1a3c90946b700d6c6b3e11d6356ba33dd309edf8bb2651cc009d164`；manifest `34b133affa5ccf18830bb10a079f7d9bc815e6479d22d954c4d9b476c84725fd`；596 包内文件匹配。SDK 补丁保持 `58440d069aed66e0d8c0e35d05ed3fe79ffc7964cf415ee182f450cc32e26083`；Host 补丁 `eb8ea902c9a237cc4d995c498c4cfefc505515bc0a438f3f927e55ae6e395cf3`。证据只在 ignored `candidates/taskgraph-10/{data-v2.log,data-result-v2.json,data-observed-v2.json,package-identity.json}`。这是 RoleScriptedProvider 的接线诊断，真实模型/UI及 Selection 独立验收仍未运行。

下一步继续核对 Selection 材料与 DATA 的联合挂载，以及运行中取消/UNKNOWN恢复；主体完成前不做大批量回归。整体仍 PARTIAL，TG-A–E OPEN。


## 2026-09-22：原 H4 修复进入 TaskGraph 收敛并原事务提交（局部接线验证）

候选 `0.13.0.dev20260922+taskgraph.9` 的隔离 Host 已走到真实验证失败 → 原 H4 repair request → 原方法库替代方法 → Planner REPLACE_METHOD → 持久 fence/收敛 READY → 同一原回复恢复 → 原 PlanCommit revision 2 与收敛 APPLIED。使用 SDK RoleScriptedProvider；这是具名的接线诊断，不是模型、取消在途调用、UNKNOWN、共享消费者或完整 TG-A–E 验收。旧方法失败为诊断预设，失败证据保留。

主体修正：原 H4 受影响复合目标进入方法可见性/适用性读取；preview/普通提交/solver 提交统一 TaskGraph 命令身份；候选完整消费原 binding_rewrites，fence 前复用原语义与已验收工作检查；保留全 Mission UNKNOWN 保守提交门禁。旧 HTN 非 TaskGraph 分支保持原提交身份。taskgraph.6 的完成后冷启动只读诊断亦通过：Attempt/intent/event/revision/input/用量条数不变，零新增 SDK 调用。

当前 wheel SHA-256 `7173e342f577a094d51b4517cacf6e3f75107c90e8551e87eeed3e2783b7bdba`；manifest `83152194773a151e62df07df1efca038e78f9d3e584f58d1fde01af79a696bd6`；596 包内文件匹配。源码补丁 SDK `58440d069aed66e0d8c0e35d05ed3fe79ffc7964cf415ee182f450cc32e26083`、Host `23533a82f91d2d32ae1e957169cf5f552dfa6b2750543564240da112943f11e1`。原始证据仅 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-9/{repair.log,repair-result.json,repair-observed.json,package-identity.json}`，冷读证据在 taskgraph-6/cold-read-result.json。taskgraph.7/8 失败诊断保留。

继续集中完成非空 DATA/Selection、运行中取消与恢复等主体核对，之后才做正式验收；未跑大批量单元/回归，未替换共享 Host/HTN 源码、环境或 DB，定时任务保持删除。整体 PARTIAL，TG-A–E OPEN。


最后更新：2026-09-23 CST。

## 2026-09-22：原提交与单叶执行闭环已到达（具名局部验证）

候选 `0.13.0.dev20260922+taskgraph.6` 的隔离 Host 已实际走通：原方法准入/规划授权 → 原 PlanCommit 同事务 APPLIED、完整图记录与历史读回 → Attempt 冻结输入与物化回执 → Worker 原工具写文件/结果收集 → Critic 原内容审查与实际用量结算 → 根审查 → Mission COMPLETED → 完成后当前读图。使用 SDK RoleScriptedProvider，本地单叶 CONTENT_ONLY 接线检查；不是产品 E2E、真实模型、非空 DATA 或全部 TG-A–E 验收。

修正初始 compound 无实体 Task 时的完整语义绑定来源；修正 Critic 通过原 Attempt 关联 Task 账户的读取，有 grant 的原物理调用仍严格验证。工作区默认代码检查在首个诊断中发现未提供测试文件，后续探针通过原 workspace_seed 提供一个读取 NOTES.md 实际字节的检查；仅该工作区内 1 项，未跑 SDK/Host 批量单测或回归。旧 Critic fixture 没有按新 task_content_scope 返回 criterion id、根角色脚本缺失均已修正探针，保留失败证据，不记为产品缺陷。

最新 wheel SHA-256 `bad4f55555097b58268f968229cc605e10a8d6452be45ebc3fd1f007673ce156`；manifest `c9eb835cf429c5f23be329c11aebfe7b89daaf9d0d2ae45d99b928e089234890`；部署身份 `1f942a426638d2019901719802fbc4cc6c87efc6cdcbb64ec997a4d742d06f4f`，596 个包内文件与冻结清单一致。证据 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-6/{dispatch-r4.log,dispatch-r4-result.json,dispatch-r4-observed.json,package-identity.json}`。SDK 补丁 `733543be78e42a2fb4fd40c716bbe562e7106dba3bd090b1e68f9bbdc4c87f1e`；Host 补丁 `535ea05891330c01e3fc37cab263751a87350b2bbc84658a3bf6f4c80ecb7e36`。

仍需非空 DATA/Selection、收敛/取消/UNKNOWN 与冷恢复接线核对，以及主体完成后的正式验收和原生 UI。共享 Host、HTN 源码、环境和 DB 未替换，旧候选和 manifest 保留；整体任务继续，TG-A–E 不因本局部结果关闭。

## 2026-09-22：实际授权与初始读图入口通过，继续原计划提交（整体未完成）

独立候选 `0.13.0.dev20260922+taskgraph.4` 已通过具名入口检查：实际 SDK 方法生成/准入 → 原 planning request → Host 原授权事务启用 TaskGraph → 当前初始图读取。使用 SDK RoleScriptedProvider，仅验证本地接线；真实模型与原生 UI 未运行，不能当作产品验收。595 个包内源码文件与冻结清单一致；wheel SHA-256 `b09725de105ea604c25dacc137dd489a44c9711de89072643be2e0d2dda0ca42`，manifest SHA-256 `55b5a59a36b710f8b76c8de46b46bd357ad931033a21c69d24ddaee641cddfc5`。本地证据：`.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-4/{entry.log,entry-result.json,package-identity.json}`；其中旧探针的 model_execution=NOT_RUN 指没有真实模型请求，实际存在 scripted SDK invocation。

本次修正原默认 workspace TargetRules 的读取、原 Commit 内 dispatcher 接线、完整 DATA workspace 输入冻结及原事务物化回执、初始 epoch 0 来源一致性，以及无 orchestrator grant 的原 SDK 用量导入。缺失用量/价格仍保留原预算；有 grant 的调用继续原严格核验。新输入绑定为 v2，保留 v1 读取；旧候选和 codec manifest 未覆盖。

继续提交时发现初始 compound 根已有原语义绑定但没有实体 Task，原 TaskGraph 预览只枚举实体 Task 而漏掉根。已统一为读取原 Mission 全部当前语义绑定，正在沿同一入口验证。原 PlanCommit/APPLIED/历史读取尚未通过；派发、收敛、恢复与 Host UI 仍待完成。主体阶段无批量单测/回归，无真实模型请求；共享 Host/HTN 环境、DB 与候选未改动，定时任务未重建。

## 2026-09-22：独立安装包与入口接线继续推进（整体未完成）

补齐 HTN 完成来源进入 TaskGraph 预览/提交的 acceptances 通道：原 completion spec、scope、贡献、outcome binding 通过原 Store 身份校验读取，同一快照核对当前正式内容/效果状态和 dirty 记录。修正初始语义 compound 尚无实体 Task 时的预算读取：保留 Mission 账户，实体 Task 才要求对应 Task 账户；缺失 primitive 仍拒绝。相关每次1–2文件类型检查通过。

独立候选 `0.13.0.dev20260922+taskgraph.1` 已构建并安装到 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-1/env`，当前 Host 环境未变。wheel SHA-256 `cbf114418258b347d57f085367d46179e11c929d16441734bfd9c0a34972ed45`；候选 manifest SHA-256 `a59ee79c4f6ebca62d5365a089fbb7d71514996c7abf29bee865363dff6b64d0`；部署身份 `7e9642cc1876323e367d5fde93ced3ef2fec3e8e0bb3a0641cd628c1f3a5c1f8`。594 个包内文件与冻结清单逐字节一致，安装位置和 package-owned reader 验证通过。构建时的2个版本文件改写单独记录，未覆盖旧 codec/候选。`taskgraph_acceptance=NOT_RUN`，不是功能验收通过。

安装入口检查首先发现隔离 Host 缺少 sdk_adapters；补充只读捕获712个Host源码/配置文件，采集前后hash一致，原已捕获文件无漂移。隔离 Host 已绑定新包的版本/hash/manifest、pyproject和lock，主Host未变。原 start 和新Mission创建已实际到达，未启用后台driver或调用真实Provider。入口探针先因漏传原 facade 必需的 request_id 被拒，补完成要求后遇到空方法库正常进入 synthesis，尚无可授权的 planning request；未伪造request/grant/APPLIED来绕过。该探针没有完成授权→读图，不计ENTRY_PASS或产品验收。保留 `entry.log`、`entry-r2.log`、`entry-r3.log` 与本地库；后续须沿真实方法产生/规划请求入口继续，不把探针前置不足误记成产品故障。

本轮没有批量单测、回归、H6/H8评测或真实模型/UI测试。仍待真实授权→原Plan Commit→派发/收敛/恢复→图视图全链验收及最终应用接入；未完成整体TaskGraph。局部检查点不撤销持续推进授权，定时任务没有重建。

最新源码补丁：SDK `5508e48e4c50cb463cc8b812b194a647e07052c63636659b527bd0100be04124`；Host `81b87195908bdee094431a6fe588e6a2cf2b50b1c572b36d63fa1737773698b5`。打包 provenance、依赖采集与原始入口证据保存在上述 ignored 目录。

## 2026-09-22：接入 HTN 最终核心交付（当前源码 WIP，未验收）

HTN 已正式交接 READY_FOR_TASKGRAPH_WIRING，旧“只能离线准备/仍等待 ready”仅为历史。交接范围为 V1.4 核心集成和原生完整效果闭环；语义回放仍 PARTIAL，H6/H8 大评测依用户要求移出阶段，不记作通过或重新启动。

当前工作区为 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/working/{sdk,host}`。只读捕获当前 dirty SDK/Host，532 个 SDK 构建输入与原 manifest 核对，采集前后源 hash 不变；528 个 wheel 包内文件核对，版本改写有原 manifest 依据。原 HTN wheel 为 `0.13.0.dev20260922+htn.1`，SHA-256 `af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`。原始采集/合并清单位于该隔离目录。

完整 TaskGraph 增量已三方合并到该交付，8 个 SDK 冲突已处理；保留 H4 repair 分支、原 Action 完整负证明及单 Mission world。TaskGraph 迁移改为追加 25，保留 HTN 的 23/24；codec 新增 v5，未覆盖旧 manifest。固定装配、执行/预览/派发/baseline/恢复和 policy 读取改为解析目标 Mission dispatcher。完成判断接入正式内容/效果/scope，界面区分等待效果审查、需对账及真正完成，不以 Action.SUCCEEDED 或旧 Task.status 推断整体完成。

隔离 Host 已接默认可信部署 reader 和真实规划授权事务内启用。部署 reader 校验派生包完整源码身份及原 HTN 验收证据索引；派生 TaskGraph 验收状态仍 NOT_RUN。构建冻结脚本已准备但未执行，未制造 TaskGraph PASS 或用户授权。尚未构建/安装 TaskGraph wheel、迁移真实库或启动产品。只进行解除接线问题所需的局部类型/语法/导入检查，没有 pytest/vitest/大批量回归/模型/UI 验收。

剩余工作：最终源码跨层一致性审计、派生包冻结及安装、真实执行/收敛/恢复与 Host 界面验收。SOURCE_MAP_COMPLETE 尚待完整入口审计；H1_H_READY 采用本次具名核心接线范围 READY，原历史全门不关闭；TG_A_VALIDATED 和 TG-A–E 整体验收仍 OPEN。用户授权持续有效，不以局部检查点结束整体任务。不使用子代理或 plan-task；定时任务已删除，不重建。

当前 SDK 补丁 SHA-256 `92ef69db21950eef31c729abb56534d37110d2da37e7bb472f523638ae6a57c1`；Host 补丁 SHA-256 `bcb43062175618f814d3db152cdf6e24e1f4181159cf9b386ed59500e07a2c16`。对应 `HTN1-INTEGRATION-WIP-SDK.patch` / `HTN1-INTEGRATION-WIP-HOST.patch`，是源码身份而非完成度。

详细源码进度见 [主体进度](../plans/TaskGraph/V1.2.1/BODY-WORK-IN-PROGRESS.md)，历史组件结果不覆盖当前派生代码。

## 已验证的隔离组件

原冻结 SDK 22 项迁移之上，隔离注册第23项 TaskGraph 原DDL（9表、25触发器）；23只是本快照可用号，不是最终迁移号。真实Store验证新建/升级、旧行/产物保留、真实HTN非空Plan Commit、合同多版本/方法退役、命令重放零写及SIGKILL恢复。

纯NetworkDocument编解码复用七种原合同，v4 manifest锁定10个实际源码，旧实验manifest保留。PreviewBinding校验12个显式来源通道和完整字段；这不证明来源真实或授权成立。结构diff只比较完整不可变对象，拒绝跨Mission、同revision冲突及不可能的全文hash组合。

revision pins算法按每occurrence、每method和每adopted child slot生成精确完整集合。member binding_hash是原 `TaskSemanticBindingV1.content_hash()`，不是嵌套 `contract_hash`。验证拒绝缺失、多余、重复、错身份/版本/hash/枚举及bool混用。

原历史来源检查在同一真实Store.read_view中按精确revision读取不可变binding、draft、children、memberships、ORDER、DATA和requirements；不使用latest或当前退役状态改写历史。真实双SQLite连接交错验证一致快照。候选network的adoption装饰不能冒充同版本持久合同，文档必须绑定实际存储字节。

**历史父检查点：156 PASS / 3.31s，Ruff PASS，5个生产文件mypy PASS。** 分类：迁移7、非空HTN迁移2、network20、preview77、diff26、pins13、原来源11。源文件测试前后不变；独立审查针对这些组件，不是整个TG-A验收。工具102项是另外的工具验证，不与156项相加冒称SDK完成。

## 尚未接通的生产链

认证Host启用policy → 真实H1来源/preview/admission → 原Commit同事务写入APPLIED、revision record、证书、pins与事件 → 完整历史read_revision → 当前执行来源/派发输入关联 → 收敛/通知 → Host只读视图/UI → 真模型与最终验收。

上述已验证的父检查点source helper不验证revision record、parent chain、certificate、policy、event、receipt和caller授权；纯pins算法不写数据库。不得把这些局部PASS解释为可执行历史、图恢复授权、H1-H全门或42场景完成。能力尚未实现完整，默认开启要求的完成条件未达到。

## 身份与证据

- 已验收组件的父variant：`4415a4430ed5334115c0d7a1dad847d96d6d420111da871784ad701b522e8d0c`，1287个文件；原冻结基线及中间检查点均保留。
- XML SHA-256：`ed219f96f1b0b38677135636554732cfe86ed5cdf8af859509f8df854d344466`。
- 原始证据索引（本机ignored）：`.local-test-evidence/2026-09-21/taskgraph/complete-plan-prep/history-pins/evidence-index.json`，SHA-256 `fc39757e2024daad2aace5296ccdeb414369520734a7181ac6e2efaee0cd7600`。日期目录沿用该连续run的开始日期。
- [执行记录与剩余门禁](../plans/TaskGraph/V1.2.1/EXECUTION-JOURNAL.md)；[并行及接入规则](../plans/TaskGraph/V1.2.1/PARALLEL-PROGRESS-2026-09-21.md)。Git只保存源码补丁、文字结果和证据索引/hash；XML、日志、数据库不提交。

## 2026-09-22：Operation 合同读取核对更正

原 `HtnStore.get_task_semantics` 缺失时通过 `_one` 抛出 `StoreConflict`，不会返回 None。先前判定存在 AttributeError 缺口有误，新增判空已撤销；该核对不记为功能修复或验收。原 `_close_attempt` 已对 TaskGraph 保留未结算预算并保留 SUBMITTED intent，取消顺序未改动。继续主体联合接线核对。


## 2026-09-22：DATA + Selection 联合入口脚本化切片通过（主体仍未验收）

候选 `0.13.0.dev20260922+taskgraph.10` 的隔离 Host/SDK 场景已完成 `SELECTION_DATA_ENTRY_PASS`。场景实际创建两个候选 worker 和两个 synthesis 流程，最终 synthesis 同时读取已接受 DATA manifest 与两个候选材料挂载；候选材料使用不同 hash/mount，未被误写为正式 DATA。最终 Mission 状态为 `COMPLETED`，`actual_workspace_verified=true`，冻结 DATA 数量为 1，冻结输入回读包含正式 `NOTES.md` 以及两个不同 candidate-inputs 内容。

证据只保存在 ignored 路径：
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data.log`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data-result.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data-observed.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data-userdata/`

这是 `SDK RoleScriptedProvider` 的真实隔离运行栈接线诊断，`real_model_execution=NOT_RUN`、原生 UI=`NOT_RUN`，不关闭 TG-A 或产品验收门禁。没有因此启动大批量测试；运行中 cancel/UNKNOWN recovery、完整 H1/部署验收 reader、最终 SDK/Host 安装和 TG-A–E 仍待主体编码收口后统一验收。候选 10 的 wheel/manifest/deployment identity 沿用本文件前述冻结值，未覆盖 codec manifest；当前隔离源码补丁 SDK=`4689867f01f1367cd5d1b27f37015922dd60e76d9bc40aebe12b0384f5006936`、Host=`a94f70ce244f878b17fd28b582e5fd9b44f921b348fa135fc6d973941600d413`。


## 2026-09-22：严格 DATA 物化器接入生产 workspace binder（候选 11，定向切片通过）

发现并修复了一个主体缺口：`artifacts.versioning.materialise_v2()` 虽已实现，但 TaskGraph 的 `_bind_workspace()` 仍把 DATA 当作普通 `inputs` 交给旧的 `WorkspaceManager.create()`；现在首次 TaskGraph workspace 构建改为由冻结 `InputManifest` 调用 `materialise_v2()`，恢复 ACTIVE workspace 仍先验证原文件，不覆盖篡改证据。非 TaskGraph Attempt 保持旧路径。

候选 `0.13.0.dev20260922+taskgraph.11` 重新运行同一隔离 DATA+Selection 场景，计数式探针记录 `materialise_v2_calls=6`，并得到 `SELECTION_DATA_ENTRY_PASS`：Mission=`COMPLETED`、`actual_workspace_verified=true`、冻结 DATA=1、两个候选材料均被 synthesis 读取。候选身份：wheel SHA-256 `56d05ae4136e7e4f689bfbc7972b16f4f712c7281c63efe4990f251dd7c80e61`；candidate manifest `a7c95d4015eee92a6096369c140292bd5c070bf63e7e7e9088dcec1899946a5a`；deployment id `652e674c3f889b959a81c9cf87f00d18fb3a6ff2bcccc7ded556c15b5d831374`；596 个包内文件匹配；`taskgraph_acceptance=NOT_RUN`。

证据只在 ignored 路径：
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-v2.log`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-result.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-observed.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-userdata-v2/`

该切片仍是 `SDK RoleScriptedProvider` 的隔离接线诊断，真实模型与原生 UI 均 `NOT_RUN`，不关闭 TG-A 或产品验收。源码检查只覆盖两处定向 `py_compile`；没有启动大批量测试。


## 2026-09-22：DATA 物化归属校验与候选 12 定向切片

在 TaskGraph `_bind_workspace()` 调用冻结 `InputManifest` 的 `materialise_v2()` 前，新增 fail-closed 身份核对：每个 manifest artifact 必须同时匹配当前 mission、manifest 声明的 producer task 以及冻结 `content_hash`；缺失、复用 ID、跨任务或内容漂移均拒绝物化。非 TaskGraph 路径不变，ACTIVE workspace 恢复仍先验证已有文件。

候选 `0.13.0.dev20260922+taskgraph.12` 已在隔离环境构建：wheel SHA-256 `d13e763f31b2b167ed38edac9aeeea3a3279cbfc2f894d35efd78b7ffe31db35`；candidate manifest SHA-256 `201164318407923121b7261cf44bed5b2eeae906f0496cd8a89f1e3b6430615e`；deployment id `a90d10767eaa47c205f3ade892d094137c4fcfb5edba908cce511289cdf1ba2e`；596 个包内文件匹配；`taskgraph_acceptance=NOT_RUN`。

同一 DATA+Selection 隔离窄探针通过：`SELECTION_DATA_ENTRY_PASS`、`mission_status=COMPLETED`、`actual_workspace_verified=true`、`materialise_v2_calls=6`、冻结 DATA=1；仍为 `SDK RoleScriptedProvider`，真实模型和原生 UI 均 `NOT_RUN`。证据只保存在 ignored 路径：
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-v3.log`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-result.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-observed.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-userdata-v3/`

本轮仅运行 `event_handler.py` 定向 `py_compile` 和上述窄场景；没有启动 pytest/vitest、全量、回归、H6/H8、真实模型或 UI。当前隔离源码检查点：SDK 补丁 SHA-256 `414424eaafe841fb72dc1d6dd5a91090eda892bace8730a7b5b4b841a71113a6`；Host 补丁 SHA-256 `a94f70ce244f878b17fd28b582e5fd9b44f921b348fa135fc6d973941600d413`。这只是主体接线进展，TG-A 至 TG-E、H1 完整部署来源、最终安装和产品验收门禁仍保持 OPEN。


## 2026-09-22：候选 12 Host 身份回写

候选构建脚本为隔离 Host 写入 `taskgraph.12` 的 package identity 常量；重新保存后的当前源码检查点为 SDK 补丁 SHA-256 `414424eaafe841fb72dc1d6dd5a91090eda892bace8730a7b5b4b841a71113a6`、Host 补丁 SHA-256 `4428d2346d2bbc6b16858363fbe8bf6b891eb562a4e411fb2b5017ae8ede07ae`。该 Host 变化只反映候选身份，不改共享 Host，也不改变候选 12 的 `taskgraph_acceptance=NOT_RUN`。


## 2026-09-22：候选 12 部署 manifest 读取核对

在候选 12 环境直接调用 package-owned `InstalledHtnWiringAcceptance._read()`，成功核对部署身份 `a90d10767eaa47c205f3ade892d094137c4fcfb5edba908cce511289cdf1ba2e`、完整源码 inventory 与 `taskgraph_acceptance=NOT_RUN`。这只是安装包来源完整性检查，不是 H1 或 TaskGraph 验收 PASS；没有创建 Store、启用 policy 或启动产品。


## 2026-09-22：DATA 与 Overlay/Selection 分层物化（候选 13）

继续审计发现：`overlay_attempt_inputs()` 会在冻结 manifest DATA 之外加入已接受生产者文件和 Selection 材料；若 TaskGraph workspace binder 把全部 upstream 都交给严格 `materialise_v2()`，这些额外挂载会被丢失。现已修正 `_bind_workspace()`：先依据冻结 manifest 计算正式 DATA 路径，正式 DATA 仍只由 `materialise_v2()` 写入；其余已验证、同 mission/producer task/content hash 的 overlay/Selection artifact 通过受控 `WorkspaceManager.create(inputs=...)` 写入。所有 upstream 同时增加 mission 与 producer task 归属校验。

候选 `0.13.0.dev20260922+taskgraph.13` 已构建：wheel SHA-256 `a713bb2414c6d0960bf87bc05c48fa5b0be3ca03601e22bab6db5e59dce1d75b`；candidate manifest SHA-256 `354d3b44981a91c889a039459cbad27e58b4f24947f7045ab142389cd4327afa`；deployment id `0fb49b02a39ec7a30fc29333f09234d99b7a739b9e8ce6bbd23973af6e05b11b`；596 个包内文件匹配；`taskgraph_acceptance=NOT_RUN`。候选 13 的同一 DATA+Selection 隔离窄探针通过：`SELECTION_DATA_ENTRY_PASS`、`mission_status=COMPLETED`、`actual_workspace_verified=true`、`materialise_v2_calls=6`、冻结 DATA=1。

本轮只运行两文件 `py_compile`、Ruff `E9/F` 语法检查和该窄场景；真实模型、原生 UI、大批量回归仍未运行。当前源码检查点：SDK `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host `4428d2346d2bbc6b16858363fbe8bf6b891eb562a4e411fb2b5017ae8ede07ae`。证据位于 ignored 路径 `.../candidates/taskgraph-13/selection-data-v4.log`、`selection-data-result.json`、`selection-data-observed.json` 和 `selection-data-userdata-v4/`。


## 2026-09-22：候选 13 窄探针确认已启用 TaskGraph 路径

候选 13 的 `selection_data_probe.py` 通过 `service.planning_authorization()`；Host 现有授权入口在首次 active grant 后自动调用 `enable_taskgraph_contract()`。该隔离数据库的 `taskgraph_policy_bindings` 已记录 mission 与 `taskgraph-enable:<mission>` 收据，随后同一场景在 policy 已启用的 TaskGraph 路径完成 `SELECTION_DATA_ENTRY_PASS`。之后再次显式启用返回预期的 `TASKGRAPH_POLICY_ALREADY_BOUND`，说明一次性 CAS/幂等保护生效，不计作功能失败。


## 2026-09-22：候选 13 Host 身份回写（最终当前检查点）

候选 13 构建脚本将隔离 Host 的 SDK candidate 常量更新到 `taskgraph.13`；重新保存后的当前源码检查点为 SDK `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`、Host `b5b79889da11f5f5b984223dd7ce8b0de3b87802e01ccf80c26fbee3aa797768`。Host 变化只属于隔离候选身份，未改共享 Host。


## 2026-09-22：分离规划授权与 TaskGraph 显式启用（隔离源码，未验收）

审计发现 Host `planning_authorization()` 在 active grant 后自动调用 `enable_taskgraph_contract()`，会把规划委派误当成 TaskGraph kernel 启用，并可能绕过计划要求的显式认证启用及已有 ACTIVE 图的 baseline/quiescence 门禁。已在隔离 Host 副本移除该自动旁路；规划授权现在只返回原始授权 receipt，TaskGraph 仍只能通过认证的内部 `enable_taskgraph_contract(mission_id, command_id)` 入口启用。

定向 `py_compile` 与 `git diff --check` 通过；未运行 pytest/vitest、批量回归、模型或 UI。新的隔离源码检查点：SDK 补丁 SHA-256 `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host 补丁 SHA-256 `18efd001f70ed60f049c329eb1b8aab03310366724cfb3430445d80063e7c6e7`。候选 13 的历史窄探针仍保留为旧自动启用行为的证据，不能继承为当前源码验收。


## 2026-09-22：显式启用修复后的候选 14 冻结

隔离候选 14 已重新构建以包含“规划授权不自动启用 TaskGraph”的修复：版本 `0.13.0.dev20260922+taskgraph.14`，wheel SHA-256 `d63744850fd5a0a08f347f86fea061cadf6b5369804b6cad1824839cf2a30bc1`，candidate manifest SHA-256 `fddb04b082bc0e9b03831eda1ce1c50317c1aa5023bdf0b3746a0b614e15cee2`，deployment id `54eb14a6d7cf43a3ffa61fda4f0870bbe8f19de6a20c1889ce1601d0ff33b289`，596 个包文件匹配；TaskGraph acceptance 仍为 `NOT_RUN`。候选构建同时把隔离 Host 的 SDK identity 更新为候选 14。当前源码检查点：SDK `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host `7ebf681e2674b96a867c3177a617accf4c590bafdd9af27a7fc11ca673a9cb78`。未运行批量测试、模型或 UI。

## 2026-09-23：最终 HTN 接入与候选 15 冻结（主体接线检查点）

HTN 最终交接已在独立只读捕获中核对为 `READY_FOR_TASKGRAPH_WIRING`：SDK 源码 `/Users/denny/projects/simple-harness-sdk-h1h-impl`，HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da`；Host 源码 `/Users/denny/projects/simple_harness`，HEAD `6c457908e49757035c21c6dc2b252415107a494c`。最终捕获清单为 SDK 1404 个文件、Host 285 个文件；HTN wheel `0.13.0.dev20260922+htn.1` 的 528 个包文件与 dirty SDK 源码逐项匹配，wheel SHA-256=`af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`，HTN manifest SHA-256=`f77d817d265053e89711643310483be2687bd8b2f308c454ab980a89d6207809`。HTN 自身语义回放保持 `PARTIAL`（23 个未知事件类型、1 个未覆盖字段、UI ledger 未对齐）；这不被 TaskGraph 误报为全门通过。

TaskGraph WIP 已在独立副本完成三方合并：SDK 92 个文件、补丁 SHA-256=`38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host 12 个文件、当前补丁 SHA-256=`0d9f82f2c46432badf659a5a1b31a5a9d5ea646159a74b6196978c1d919f4b7b`；无冲突。最终 HTN 接口定向 `py_compile` 已覆盖完成效果读取、操作完成读取、运行中工作、计划提交、层级派发、TaskGraph 运行时/来源等入口；Host 关键入口亦通过定向 `py_compile`。Host `planning_authorization()` 已移除自动启用 TaskGraph 的旁路，显式认证的 `enable_taskgraph_contract()` 保持独立，避免规划授权绕过 baseline/quiescence 门禁。

候选 15 已在该最终 HTN 接线副本构建并锁定：版本 `0.13.0.dev20260922+taskgraph.15`；wheel SHA-256=`f7470f35708187f298da94c06774e12ebb41bcb04f6570c34de791bc17dba5d5`；candidate manifest SHA-256=`a54beaabc1acaf417e55dbf2f14e536658a9db057efadd06eb97bc0d8fdbecae`；deployment id=`30d2a234fd058a996c659cd97454c91b4864dcf564f6d81c6d72748e743b3b92`；596 个包文件匹配。随后从候选环境直接调用 package-owned `InstalledHtnWiringAcceptance._read()` 成功返回上述部署身份、HTN 来源与 `taskgraph_acceptance=NOT_RUN`；该读回只证明来源/身份一致，不是 TaskGraph/H1 验收 PASS。窄读回记录在 ignored `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-15/package-acceptance-read.json`。

本轮仍未启动 pytest/vitest、全量/回归、TG-A 至 TG-E、H6/H8、真实模型或原生 UI；共享 Host、HTN 工作树、依赖、DB 和定时任务均未修改。主体跨层接线和最终接口审计继续进行，SOURCE_MAP_COMPLETE、TG_A_VALIDATED、H1 完整部署来源及产品验收门禁保持 OPEN。
## 2026-09-23：候选 15 最终 HTN 接线窄场景通过

在最终 HTN 源码与 TaskGraph WIP 的独立合并副本上，用完整 Host 包装副本（共享 Host 只读复制，候选 15 的 12 个 Host 改动和 vendor 身份覆盖）运行单一 `DATA+Selection` 接线探针。显式流程为：真实 Mission 创建 → Completion Spec → 原方法合成/规划请求 → `planning_authorization()` → 显式 `enable_taskgraph_contract()` → 原 PlanCommit/revision → 两个 Worker 产出 → Selection synthesizer 实际读取两个候选 → Critic/root review → Mission `COMPLETED`。结果为 `SELECTION_DATA_ENTRY_PASS`，`actual_workspace_verified=true`，`materialise_v2_calls=6`，冻结 DATA=1，6 个 Attempt，真实模型和 UI 均 `NOT_RUN`。候选 15 的 package-owned deployment manifest 仍报告 `taskgraph_acceptance=NOT_RUN`；本探针是主体接线的隔离脚本证据，不提升 TG-A/H1/产品验收门禁。

原始结果仅保存在 ignored `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-15/full-host/selection-data-{observed,result}.json` 及同目录日志；未修改共享 Host、HTN、依赖、DB 或定时任务。
## 2026-09-23：显式启用门禁窄核对

在同一完整隔离 Host 包装副本的独立数据库中，单场景探针在 `planning_authorization()` 返回 active 后先查询 `taskgraph_policy_bindings`，绑定数为 `0`；随后仅通过认证内部 `enable_taskgraph_contract()` 写入绑定并完成同一 DATA+Selection 流程。结果仍为 `SELECTION_DATA_ENTRY_PASS`、Mission `COMPLETED`。这确认规划授权与 TaskGraph kernel 启用已经分离；没有把 planning grant 当作 TaskGraph 已启用，也没有改变共享 Host。
## 2026-09-23：提前执行的定向用例（不证明主体完成）

主体完整性尚未确认时，提前在最终 HTN+TaskGraph 独立 SDK 副本使用其冻结依赖运行 10 个直接相关现有测试文件（wiring、operation admission/current/live/tenant、retired running/unknown action、H1-I production entry、terminal UNKNOWN release、input manifest resolution）。结果 `164 passed in 11.52s`，JUnit 原始文件仅保留在 ignored `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-15/targeted-htn-taskgraph.junit.xml`。这不是 42 组 TaskGraph 验收、stateful、真实模型或 Host/UI 全门；全量回归仍未启动。
## 2026-09-23：回归与证据记录更正

在运行中取消、UNKNOWN 恢复及冷恢复链路仍需核对时启动 `tests/orchestrator/full_target`，不符合用户“主体编码全部完成后才允许大规模回归”的要求；已停止扩大测试，返回主体实现。两次 full_target 均未完成，多个失败尚未归因。已定位的一项 H1-I 恢复失败及后续报告错误包含 ENOSPC；该单文件随后 5 PASS，不能据此将所有失败归因于磁盘或宣布整轮通过。10 文件 164 PASS 仅保留为当时的局部结果。

清理时误删了本轮 candidate15 下 pytest-tmp、full-host/explicit-enable-userdata-v2、explicit-enable-userdata、selection-data-userdata-v5（含原始数据库），违反证据保留要求；之前“未删除原始 TaskGraph 证据”的记录不实，现予更正。已保留的日志和结果 JSON 不等同于可冷恢复的数据库证据；相关场景数据库复核/冷恢复记 NOT_COVERED。停止进一步删除，旧候选、源码与共享 HTN/Host 数据未在该清理中删除。完整验收、42 场景和原生 UI 仍 OPEN。
