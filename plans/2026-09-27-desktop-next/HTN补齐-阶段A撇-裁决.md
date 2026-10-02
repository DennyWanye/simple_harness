# HTN 补齐 阶段 A′ 裁决（2026-10-03）

- 裁决人：独立裁决子代理（Opus，只读；只写本文件，没改代码、没跑测试、没做 git 操作）。
- 对象：`HTN补齐-阶段A撇-方案.md` 第 2 版里，主会话自己推出、没有经过裁决的三件事；另加挑战员提出的"建任务时就绑定执行图"这条可选根治路线。
- 依据：方案第 2 版、`HTN补齐-阶段A撇-清点.md`、`HTN补齐计划-2026-10-02.md`、仓库 CLAUDE.md 的两条硬约束（判断交给 LLM、同一件事只留一条路径；开发期不兼容旧数据），以及下文逐条引用的源码。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`，`Host/` = `backend/deskpet/orchestration/`。

## 结论一览

| # | 事项 | 裁决 |
|---|---|---|
| 1 | 删强单代理对照臂 `htn_single_agent` | **同意**，补两点：评测包里随之变成没人用的模块一并清；计划表一第 20 项的结论要改写 |
| 2 | 删两个等待期真实模型探针脚本 | **同意删**；**理由改为**"由迁移后的脚本化用例钉住"，不能写"由产品真机局替代" |
| 3 | 删审阅员模板 `CRITIC` | **同意删**；**风险表述改写**：它不会让已有执行池或编排服务起不来，方案第一节"风险用户需知情"那条要改 |
| 4 | 建任务时就绑定执行图（挑战员可选路线） | **做**，并入 A′；取代方案里"10 处等待期分支改成显式等待期判断"那一步。这条改变了原计划的一个前提，要报用户知情并记为偏离 |

---

## 1. 强单代理对照臂 `htn_single_agent`：同意删

**核实**
- 唯一的导入方是 `SDK/evaluation/htn_executor.py:23`。`htn_executor` 本身属于用户已定删除的分层臂。Host、脚本、测试都不直接用它。
- 它直接调旧运行时 `build_agent_runtime`，权限端口是 `AllowAllAuthorization`（第 79 行一带）。旧执行池删掉以后它本来就起不来。
- 它的唯一用途是 H8 矩阵里给分层臂做"同题对照"：比较两条臂的工具集哈希必须相等，由 `H8Manifest` 冻结。分层臂没了，这个对照就没有比较对象。
- N4 冻结基线走的是 `appworld_arms`，与它无关。`appworld_arms` 已在 opt.132 删除。

**补充要求**
1. **清掉成了孤儿的模块。** 分层臂和对照臂删掉以后，下面这些模块就只剩彼此互相引用：`htn_matrix`、`htn_scenarios`、`htn_oracles`、`htn_identity`、`htn_interventions`、`htn_meter`、`htn_operation_controller`、`htn_process_recovery`、`htn_recovery`、`metered_provider`、`htn_method_cohort`、`htn_method_source`。
   - 其中做法评测两件，计划第 171 行本来就定了删。
   - AppWorld 领域本身也会变成没人用：`appworld_*` 各模块、`runtime/appworld_templates.py`、`governance/domains.py:135-147` 的 `APPWORLD_PROFILE`（它的 `role_templates={"critic": "critic-appworld-v1"}`）、`HIERARCHICAL_WORKER_TEMPLATES` 里的 AppWorld 一项、配置里的 `appworld_execute` 和 `domain_tools`。
   - 按"同一件事只留一条路径、开发期旧路径直接删"，删除那一步做完后**做一次孤儿扫描**，只被上述模块引用的一并删掉；还有别人在用的就留下，并写明谁在用。
2. **部署清单。** `SDK/orchestrator/taskgraph_deployment_manifest.json` 按文件逐个记了哈希（第 118 行就是这个文件）。删文件后要重新生成，否则所有新任务都启用不了执行图，报 `taskgraph_deployed_source_unverified`。发版脚本已经包含这一步，这里只是提醒：这一步不能跳。
3. **计划表一第 20 项要改写。** 评估报告写的是"只留 AppWorld 的分层评测臂"（用户 10-02 18:50 定）。现在用户 10-03 改为删除，所以结论变成"**AppWorld 外部评测全部移除，历史成绩留档**"。以后要再做"HTN 上线后同题对照"，得在产品同形世界上重建。这件事在报告里用一句话告诉用户即可。

---

## 2. 两个等待期真实模型探针脚本：同意删，理由要改

**核实**
- `scripts/acceptance/run_real_deepseek_wait_lifecycle.py`（433 行）、`resume_real_deepseek_wait.py`（272 行）测的是规划器的 **WAIT 决定**，也就是"已有工作在推进，我先等它"。WAIT 写在 `SDK/runtime/role_templates.py:254`，事件 `PlanningWaitRegistered/Woken` 写在 `event_handler.py:3046-3321、6944`。这是产品规划器在用的决定，不是旧路。
- 两个脚本的做法：把执行者的第一次模型请求扣住，等真实模型的规划器自己写出 WAIT 再放行。
- 两个脚本的种子都来自 `test_h1i_production_entry`，也就是旧池加共享世界。四条旧路一删，它们全都起不来。
- `resume_*` 还要借"主机测试签发接口"补签被唤醒那次的规划授权。这是测试专用的旁路，留着就是第二条路径。

**为什么不能说"由产品真机局替代"**
- 普通真机局里，模型会不会选 WAIT 不由我们决定。真机局不保证走到 WAIT，所以替代不了。
- 计划第六节定过口径："需要模型'选择'的路径，真机只断言'提供了、用了就正确'，必走路径用脚本化通道钉住。"

**改写后的表述**（写进方案第一节和第 4 步）
> 两个探针删除。WAIT 机制由 `test_h1i_wait_lifecycle.py`（15 个用例，属于"起主循环"一类）迁到产品同形世界后用脚本化回复钉住；真实模型会不会选 WAIT 不再设专门探针，真机局遇到时只核对"用了就正确"。

**附带要求**
- `test_h1i_wait_lifecycle.py` 迁移后，是 WAIT 机制唯一的保护。它必须进入"按机制挑的改坏检验"名单：至少改坏一处"唤醒条件"或"等待登记"，确认迁移后失败的用例数不少于迁移前。
- 历史证据（`WAIT复验-2026-09-21.md` 与 `.local-test-evidence/` 下的记录）原样留档。

---

## 3. 删 `CRITIC` 模板：同意删；风险表述要改

### 3.1 实际机制（逐处核实）

**① 策略库默认参数**
- `governance/promotion.py:93-96` 的 `builtin_prompt_versions()` 读 `ROLES`，当前是 `{critic: critic-v3, worker: worker-v3}`。
- `resolve_params()` 第 116 行把它写进 `prompt_versions`。

**② 启动时会不会核对？——不核对，不阻断。**
- 启动时 `event_handler.py:742` 调 `_open_policy_library()`（第 897-918 行）：
  - 库里已经有 ACTIVE 版本，`seed_policy` 原样返回旧版本，不重写（`policy_commits.py:97-100`）；
  - 只对 `CONFIG_DERIVED` 四项比较漂移（`event_handler.py:330-336`：探索名额、任务并发、老化窗口、路由），`prompt_versions` 不在其中，所以**连一条漂移记录都不会写**。
- 执行池的两道启动核对都与角色模板无关：
  - 执行池上下文身份 `_bind_context_identity`（`runtime/assembly.py:382`）比的是模型、上下文策略、分词器指纹、读工具说明哈希（`runtime/model_router.py:109-118`）；
  - 历史意图核对 `_check_intent_contexts`（`assembly.py:431`）比的是同一份上下文身份和模型调用准入指纹。
  - 记忆里"改模板会让执行池起不来"那条教训，指的是**工具说明**（会进上下文身份）和准入参数，不是角色模板。

**③ 按任务绑定**
- 任务在建立的同一事务里绑定当时的 ACTIVE 策略版本（`policy_commits.py:146-176`）。之后 `policy_for()` 只读这个版本**存在库里的参数**（`event_handler.py:928-945`），不重新从代码推导。
- 执行图的执行策略核对（`taskgraph_execution_policy.py:61`）比的是"`policy_for` 的结果 = 版本里存的参数"，两边都来自库，与代码里有没有 `CRITIC` 无关。
- 老版本参数里留下的 `critic: critic-v3` 这个键，只在调用 `template_for(CRITIC, …)` 时才会被读（`role_templates.py:643-651`）。删了 `CRITIC` 就没人再读它。

**④ 策略快照**
- `governance/policies.py:395-411` 的 `policy_snapshot()` 只经 `Orchestrator.policy_snapshot()` 调用。产品代码不调它（Host 里同名的 `read_policy_snapshot` 是工具注册表的另一回事）。
- 它的哈希变了，只会让证据目录里"首尾快照漂移"的标记变化，不阻断任何东西。

**⑤ 新建库**
- 默认参数少了一个键，策略版本号（内容哈希）会和旧库不同。新库各管各的，无害。

### 3.2 方案第一节"风险用户需知情"改为

> 删除旧审阅模板**不会**让已有执行池或编排服务起不来：启动时核对的执行池身份和模型调用准入身份都不含角色模板；策略库已有的当前版本原样保留，任务按建立时绑定的版本运行。
> 真正的影响有三条：
> ① 开发库里**还没结束、走旧审阅路径的任务**（未结束的旧式审阅意图，`mission_sources.py:154-161` 的 critic 分支）在旧审阅路径删除后会明确报错。这是删旧审阅路径本身的后果，不是删模板的后果；第 5 步"在旧版上先取消未结束任务"已经覆盖。
> ② 新建库的策略版本号会变，与旧库不同，无害。
> ③ 本轮任何源码删改都要重新生成执行图部署清单，否则所有新任务都启用不了执行图。这是常规发版步骤。

### 3.3 删除时别误伤
- `SDK/verification/critics.py` **不能整文件删**：保证通道的 `assurance_review_runtime.py`、`operation_outcomes.py`、`operation_proposal_review.py`、`verifier_router.py` 都在用它。只删其中 `critic-v3` 专用的那一段（第 105-109 行的重试提示分支）和 `CRITIC_VERSION` 常量。
- `CRITIC_TASK_CONTENT` 第 622 行借用了 `CRITIC.tool_names`，两者一起删。
- `ROLES` 删掉 `CRITIC` 后只剩 `WORKER`。`worker` 键照旧保留，它是策略里的版本键。
- `critic-appworld-v1` 随第 1 条的 AppWorld 孤儿清理一起删。

---

## 4. 建任务时就绑定执行图：做，并入 A′

### 4.1 现状：为什么会有"等待期"
- 现在的顺序是：建任务（同一事务写"要求执行图"）→ 建规划意图、造第一份规划包 → 规划请求 → 规划授权（自动模式由 Host 代签，手动模式等用户点）→ Host 每轮调 `_enable_required_taskgraphs` 绑定 → 放行规划器 → 第一次计划提交。
- 绑定之所以要等授权，**唯一原因**是 `taskgraph_policy_sources.py:61-88` 的 `_delegation()`：
  - 要有一张当前有效的规划授权；
  - 再把它作为 `planning_delegation_ref` 写进绑定策略文档（第 110 行）。
- 全库检索的结果：`planning_delegation_ref` **写进去以后再没有任何代码读它**。它只是一条出处记录。之后每次计划提交，照样由规划准入自己核对授权。
- 因为这一步依赖，产生了一整类等待期机制：
  - `taskgraph_requirements` 表和 `require_taskgraph / awaiting_taskgraph / awaits_taskgraph / current_planning_grant / missions_awaiting_taskgraph`（`taskgraph_requirement.py` 全文）；
  - `event_handler.py:4792` 的扣住规划意图；
  - Host 每轮的启用协调和故障日志（`service.py:874-904`）；
  - 读接口的 `ACTIVATION_PENDING` 状态和界面上的"执行图正在启用"提示（`LiveGraph.tsx:120,373`）；
  - 清点里 10 处"等待期合法"的假分支。
- 其中 `current_planning_grant` 和 `_delegation` 是**同一个"当前授权有效"判断写了两份**，本身就违反"同一件事只留一条路径"。
- 两份"当前纪元"计算（`hierarchical_dispatch.py:938-945` 未绑定一支和 `taskgraph_epochs.current_scope_epochs`）也是因为等待期才并存的。开工前裁决②已经要求"在 A′ 只留一份"。

### 4.2 改成建任务时绑定，会怎样
- 在建任务的同一事务里（根初始化之后）直接写绑定、启用回执和事件。绑定需要的东西在这个事务里都已经有了：
  - 任务的策略版本已在建任务时绑定；
  - 规划协议版本由 `require_taskgraph` 在建任务时就核对；
  - 本任务的规划世界由 `_dispatch_for` 按任务现建；
  - 部署验收只读已安装的源码清单。
- 策略文档去掉 `planning_delegation_ref`，`_delegation()` 和 `current_planning_grant` 一起删。
- 于是"已要求、未绑定"这个状态不再存在：
  - 上面列的等待期机制整类删除；
  - 10 处等待期分支改成"必须已绑定"，未绑定就是内部错误；
  - 纪元计算自然只剩一份；
  - 计划中"规划授权签发后启用执行图"这项组装职责（方案第二节第 1 步第 5 条）也一并消失，搬进 SDK 的东西更少，字节钉死清单里也少一项启用时机。
- 规划授权**一点没放松**：规划器照样要授权才能提交计划，手动模式照样等用户点。只是等的东西从"等授权 + 等绑定"两层，变成"等授权"一层。

### 4.3 这算不算放松了安全或改了"谁说了算"
- 绑定本身不授权任何效果。它只冻结本任务的图结构预算、候选策略、模式、目标、部署策略引用。改计划要规划授权，执行要执行授权，都在原来的入口核对（原始 TaskGraph 计划第 156 行："planning grant 只约束改计划"）。
- 原始计划 §6.5（第 353 行）要求"有效 planning 委派"，是因为当时启用是**可选的加入**，还可以把已有任务迁进来（`CAPTURED_BASELINE`）：把一个任务改成按新内核治理，需要委托人授权。
- 现在这两个前提都没了：老任务迁入已删；新任务一律按部署默认要求执行图。真正表达用户意愿的是"建任务"这条命令本身。
- 所以这一条依赖已经没有功能，只剩顺序上的纠缠。删掉它符合核心思想："秩序"由建任务这一个入口一次定好。

### 4.4 代价与风险

| 项 | 量级 | 说明 |
|---|---|---|
| 改动量 | 小～中，约 +0.5～1 天 | 删的多、加的少；同时省掉方案第 4 步"10 处改成显式等待期判断"的工作 |
| 与原计划的偏离 | 需记录 | 改原始 TaskGraph 计划 §6.5"有效 planning 委派"和 NEXT-TG-1.0 §6.4 的时序；写进计划表一"本计划新增的偏离" |
| 绑定策略文档字段变了 | 开发库旧绑定读不出 | 去掉一个字段后，`InstalledGraphPolicy` 会拒绝旧行（`TASKGRAPH_POLICY_FIELDS_INVALID`）。按开发期规则接受；第 5 步"在旧版上先取消未结束任务"已覆盖。建议顺手把内核版本号升一档，让报错说清楚原因 |
| 建任务变成"部署不合格就当场拒" | 行为变化，偏正面 | 原来部署验收没过时，任务建出来后一直等，只在日志里记一条；现在建任务直接失败，并给出具名错误。更诚实，主 Agent 能直接看到 |
| 第一份规划包改走"已绑定、零修订"的读法 | 中等技术风险 | 现在第一次计划提交、绑定后提出新做法再被唤醒时，已经在走这条读法，所以不是新路径。仍要加一条定向用例：建任务后立即造包、授权、提交第一份计划，整圈走通 |
| 建任务事务变重 | 小 | 部署验收要把已安装源码逐个算哈希。原来在启用时每个任务也做一次，现在只是挪到建任务时，总量不变 |
| 事务回滚后的缓存 | 小 | `_dispatch_for` 会把本任务的规划世界缓存起来；建任务事务回滚后，缓存里会留一个不存在任务的条目。任务号是新生成的，不会撞上，但实现时顺手在失败时清掉 |
| 测试面 | 可控 | 直接碰启用流程的测试只有 6 个文件（`production_fixture.py`、`test_taskgraph_required.py`、`test_read_history_protocol.py`、`test_worker_skills.py`、`test_captured_baseline_removed.py`、Host `test_strict_taskgraph_default.py`）。`production_fixture` 本来就要在 A′ 第 2 步换芯；Host 那个文件里"等待期"两个用例改成"建任务即已绑定" |

### 4.5 为什么现在做，而不是以后
- A′ 正要重写共用测试构造器，也要逐处改那 10 处分支。现在不做，就得先写一遍"显式等待期判断"，日后根治时再删一遍，测试构造器也要再换一次芯。
- 一起做，A′ 结束时这一整类状态就不存在了。后面的 B（卡住原因与出口对照）、G0（全业务重放覆盖清单）也少一个要覆盖的中间状态。

### 4.6 落到方案里的改动
- 第二节第 1 步第 4 条改为："建分层任务的同一事务里写执行图**绑定**和保证通道记录"。删去第 5 条"规划授权签发后启用执行图"。
- 第 4 步的"10 处等待期分支改成显式判断"改为："10 处改成'必须已绑定'；删除等待期整类机制（要求表、扣住规划意图、Host 启用协调、`ACTIVATION_PENDING`、界面'正在启用'提示、`current_planning_grant`）；策略文档去掉规划委派引用"。
- 字节钉死清单里，`enable_command_id` 的格式可以保留（它仍是绑定命令的号），但取值时机改为建任务时。
- 计划表一新增偏离一条，见 4.4。

### 4.7 可直接报给用户的一段话
> 现在新任务建好后，要先等"规划授权"发下来，系统才把它接上执行图；中间这段"等着接上"的状态，在代码里牵出了一整套等待、重试、界面提示和十来处特殊分支。查下来，接上执行图这一步要授权，只是为了在记录里写一句"是谁授权的"，后面没有任何地方再用它。规划本身照样每次都要授权，安全上不受影响。建议改成"建任务时就接上执行图"，这一整套等待机制随之删除。代价是改了原计划里"先授权再接上"的一个前提（记为偏离），开发库里没跑完的老任务需要先取消，工作量大约多半天到一天，而且能省掉本来要做的另一部分改动。本阶段一起做。

如果用户不同意，就回到方案第 2 版"10 处改成显式等待期判断"的做法。两种做法都能在 A′ 内完成，不影响后续阶段的顺序。
