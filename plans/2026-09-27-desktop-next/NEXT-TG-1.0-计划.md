# SimpleHarness 下一阶段实施计划：以严格 TaskGraph 驱动任务推进

**编号：NEXT-TG-1.0｜日期：2026-09-27｜基线：Host 4a1678ae / 内嵌 SDK opt.32**

**性质：提交给用户与 WorkAgent 的增量实施方案，不是代码补丁、测试报告或发布授权。**

## 0. 结论与本次裁定

下一阶段不重写编排框架。保留现有 HTN、TaskNetwork、CommitService、Allocator、Dispatcher、Assurance、Operation 和 Agent Runtime，补齐它们之间尚未成为默认路径的合同。

本阶段的主目标是：

> 编排 Agent 提出计划或修复提案；CommitService 校验并提交；严格 TaskGraph 提供当前合法工作与约束；原调度循环自动推进。只有确实需要新的语义规划时才请求 Planner。用户看到的执行图，来自这条实际执行链的 SDK 正式只读接口。

本方案对 WorkAgent 六批草案作以下调整：

1. 保留缺陷修复为第一批；严格 TaskGraph 从第三批前移为第二批。
2. **把“确定性的推进判定”加入第二批**，不能只修清单、打开 TaskGraph，再继续使用分散且可能重复的 Planner 唤醒条件。
3. 执行图前移为第三批，先把实际执行路径与正式读取接口对齐，再扩大产品入口。
4. 原产品入口补齐移至第四批。发布目录、参考资料、聊天发起任务、委派、熔断/续跑与路由，全部复用同一条正式任务链。
5. 第五批保留，但拆成 **5A：MISSION 来源绑定** 和 **5B：统一能力目录与 Skill 产品入口** 两个可分别签收的小片。不能因此把它们永久移出本阶段。
6. 第六批收口跨层场景。关键反例从第一批起逐步执行，不把安全性全部押到最后。
7. **本阶段不引入 JEV，不新增判断用模型、独立调度服务、事件总线、第二套任务状态机或第二套效果账本。**
8. 新功能在交付后的新任务上默认启用；这不等于替用户自动批准效果、不等于把来源缺失视为满足，也不等于无条件增加模型调用。

---

## 1. 真实基线与证据边界

### 1.1 本次直接核对的远端基线

| 项目 | 值 |
|---|---|
| 开发仓库 | `DennyWanye/simple_harness` |
| 固定 Host commit | `4a1678aef365f39e06cccf2b1d3a0d6552c04b71` |
| 内嵌 SDK 路径 | `sdk/simple-harness-sdk/` |
| Host 锁定 SDK 版本 | `0.13.0.dev20260925+opt.32` |
| Host 声明 SDK wheel SHA-256 | `0480aadc5465a0565a0fcfd9126af271567686d71dc4b831c9dd9cd014ec3044` |
| Host 声明候选 manifest SHA-256 | `7c5777b9c5665891302f5d4d3e750fdf1ccf3b4da0fdfabd8cf61d834e21e110` |
| Host 声明 SDK_SOURCE_COMMIT | `9e6686fe7c952a9ea3136fb4a15e5fb4f1514a94` |

上述 SHA 来自本次远端 `sdk_candidate.py`，**不是本次重新下载 opt.32 wheel 后计算的结果**。WorkAgent 开工时须校验本地真实 wheel、已安装模块及当前 dirty 文件。

独立 SDK 仓库不作为本阶段施工来源。不能用其 opt.27 覆盖内嵌 opt.32。

### 1.2 必须保留的本机改动

用户提供的 Handoff 报告：任务过程视图尚未提交，并明确要求暂不提交。本次没有读取这些 dirty 字节，也没有运行用户本机的调试应用。

WorkAgent 应在第一项工作中记录：Host HEAD、内嵌 SDK 文件清单、dirty 文件列表与内容哈希、锁定 wheel/manifest、实际导入位置。`story.py`、`missionStory/`、任务页相关改动均按真实 dirty 状态保留；不 reset、clean、覆盖、自动 stash 或擅自提交。备份仅需受影响源码与差异，不复制数据库、node_modules 和模型缓存。

本轮“后端 12、前端 958、后端 26/427、真机点击通过”等结果均为 **WorkAgent 报告的历史结果**，本方案不把它们写成本次复跑 PASS。

### 1.3 已直接核对、会影响计划的事实

- 当前 `_cycle_inner()`、`_decide()` 已有规划、补充分解、延后修复、审阅、分配与收尾入口；`allocate_v2()` 已消费 `EligiblePrimitiveTask`。因此不需要重写 Scheduler。[S04–S06]
- `TaskGraphNotifications` 已接原事件与持久 followup，其注释和实现明确：真正派发仍留在原调度循环，不由通知消费者另起执行器。[S07]
- `enable_taskgraph_contract()` 已存在，要求受信调用、真实部署来源和当前规划授权；现有 SDK 还支持受约束的 `CAPTURED_BASELINE`，不是“技术上只能在第一版计划前启用”。**本阶段的产品策略主动收窄为：只对新任务、在首次正式计划提交前启用，不自动迁移旧任务。**[S02–S03]
- 自动启用还有一个审计细节：当前启用事件写 `actor_type="human"`。Host 自动调用时应按实际系统委托记录，不能冒充人点击。[S02]
- `input_result()` 在没有声明 DATA requirement 时返回 None；`resolved_inputs()` 将它变为空 manifest；`admissions()` 又在 result.manifest 为 None 时补空。前两者有“合法无输入”的原始意图，最后一处是危险回退。必须按输入合同分类修复，不能把所有空 manifest 都禁止。[S08–S10]
- 来源 reader 区分安装来源证明与 TaskGraph 验收；`taskgraph_acceptance=NOT_RUN` 是允许的来源记录值。不能通过写一个 PASS 解决来源失配，也不能让验收结果反过来改变已测试制品。[S01]

---

## 2. 目标责任分工：一条执行链，不新增另一套框架

| 组件 | 负责什么 | 不负责什么 |
|---|---|---|
| 编排 Agent / Planner | 理解目标；提出分解、方法选择、结构修复；也可答 WAIT/NO_CHANGE | 不直接写 Task 状态、图表、Acceptance 或启动 Worker |
| CommitService 及已有专用提交服务 | 检查授权、版本、读集、预算、合同、在途工作；唯一逻辑提交权威 | 不在数据库写事务中调用模型或外部工具 |
| TaskGraph / TaskNetwork | 正式结构、采用关系、历史版本、依赖、输入关联、就绪判定 | 不成为证据真假、效果事实、费用的第二个权威 |
| 推进规则 `progress.py`（拟新增内部模块） | 将受信事实映射为 FAST / WAIT / SLOW / STOP 及下一动作建议 | 不调用模型，不写数据库，不签发执行许可 |
| 原 Allocator / Dispatcher | 在已准入工作中按资源、预算、并发策略分配并交接 | 不根据 UI 颜色、文本理由或任意 READY 字符串执行 |
| Worker / Runtime | 执行获准工作，产生真实输出、调用与费用回执 | 不自行宣告义务满足或重放未知效果 |
| 独立 Verifier / Assurance | 审阅指定要求、输入与结果，提供正式审阅及当前有效性依据 | 不替用户改变要求，不通过自身文字授予效果权限 |
| SDK 正式只读接口 | 将上述事实投影为计划图、执行过程与诊断 | 不产生业务状态、不触发模型调用、不靠 Host SQL 拼事实 |

“TaskGraph 自己推进”在产品上意味着：**原运行循环自动消费 TaskGraph 结果**。不是给 TaskGraph 新增一个独立进程或另一套任务队列。

---

## 3. 推进判定：修正此前过度简化的 Fast / Slow 规则

### 3.1 四条总规则

**FAST：**当前已批准计划、合同和确定性政策足以决定下一合法动作，不需要新的语义规划。包括派发、规则允许的内容返工、请求既定审阅、消费审阅结果、组合验收与安全收尾。

**WAIT：**当前条件尚未满足，或权威事实暂时不完整。等待、核对、恢复或请求已有的人为确认；不能把未知变成“没有发生”。

**SLOW：**需要新的语义判断、分解或计划修复提案。进入原 Planner/MethodSynthesizer 路径，但仍受原授权与累计预算限制；回复可为 NO_CHANGE/WAIT，不强制改图。

**STOP：**当前范围禁止再发起新的业务工作，例如已取消、硬截止、不可恢复的策略禁止或需隔离的图完整性错误。停止业务不等于停止迟到结果导入、费用核对与效果收敛。

**FAST 不是零模型调用。** Worker、独立 Verifier 仍可能调用模型；节省的是不必要的主编排模型再规划。

**计划版本改变也不自动等于必须叫 LLM。** 已批准方法的纯编译、同一提交的幂等重放或既有确定性选择，可以按现有系统路径完成；不得为“统一叫 Slow”额外增加模型轮次。

### 3.2 不采用一个 Mission 级互斥开关

假设 C 分支缺必需生产者，D 是独立且已经合法就绪的分支：

- C 可产生 SLOW 修复请求；
- D 仍可走 FAST；
- C 的依赖后继受相应 fence 约束；
- 只有影响范围不清、共享资源/效果无法排除，或现有合同要求全 Mission 保守阻断时，才扩大阻断。

因此函数按 **任务出现位置 / 待分解复合目标 / 根收尾范围** 判断，原循环汇总为一组动作。不要写成 `if any_ready: FAST elif any_wait: WAIT elif ...`，否则 READY 可能掩盖硬阻断，WAIT 也会掩盖另一分支真正需要的修复。

### 3.3 最小内部合同（拟新增，不是已有公共 API）

`orchestrator/progress.py` 中放一个纯函数与严格不可变类型即可；复杂来源适配确需拆分时再增加薄的 `progress_sources.py`，不先建“引擎平台”。

```text
ProgressDecision
  route: FAST | WAIT | SLOW | STOP
  scope_ref: 原 Mission / Task / Occurrence / root 的类型化引用
  action: 原受控动作类别
  reason_code: 固定规则码
  basis_ref: 本次所用计划、合同及相关来源读集的引用
  issue_key: 需要持久工作时的稳定业务问题身份，否则 null
  wake: 既有事件/定时/用户动作的等待条件，否则 null
```

`ProgressDecision` 是建议和诊断，不是新的任务状态、不是真实预算，也不是 capability token。下游 Commit 和 handoff 继续复核当前事实。

### 3.4 规则优先级

按每个范围执行：

1. 查当前生命周期与硬安全限制；已停止范围不发新工作，但继续原核对/收尾。
2. 查结构完整性、来源覆盖、权限、效果未知和 fence；相关缺失不准入。
3. 查已存在的工作：在途 Attempt、原 Planner/Review intent、待导入审阅、持久修复/收敛。已有工作先复用/核对，不重复创建。
4. 查明确的结构性问题：待细化目标、完整读取证明的缺失生产者、当前方法不适用等；有授权、有额度才请求原规划入口。
5. 查合法可执行工作、合法同合同返工、既定 Review/组合/closeout；交给原执行器。
6. 其他明确等待返回 WAIT，并保留唤醒原因。
7. 不能归类的状态返回 **WAIT + SYSTEM_DIAGNOSIS_REQUIRED** 或现有隔离错误。绝不默认 `SLOW` 并称其为 fail-closed。

第 4、5 步对不同范围可以同时产生动作；最终资源分配仍由原 Allocator 决定。

### 3.5 最小判定表

| 受信事实 | 路由 / 动作 | 不允许的处理 |
|---|---|---|
| primitive 完整准入，尚无冲突中的 Attempt，有可分配资源 | FAST / DISPATCH | Gate 自己创建 Worker 或绕过最终授权 |
| 上游已正式接受且合法 DATA 已绑定 | FAST / REEVALUATE_AND_DISPATCH | 仅凭 Worker 声称完成释放依赖 |
| 上游仍运行，或已完成但正式审阅/导入尚未结束 | WAIT / 原完成与审阅事件 | 无 READY 就重规划 |
| Review 要求修改内容；合同/输入绑定不变；原政策允许且累计次数未耗尽 | FAST / REWORK | 改写旧请求或重新开户清零失败次数 |
| 内容/Review 格式重问还在原额度内 | FAST / 原有界修复路径 | 再建另一个 Review Job 重置额度 |
| 未细化 compound 且其父方法已允许展开 | SLOW / 原细化入口；已有确定性方法可复用原算法 | 给 compound 创建 Worker Attempt |
| 必需端口无生产者，且合同及全量关系读取完整 | SLOW / 修复输入或分解 | 用空 manifest 顶替 |
| 查询失败、缺 reader、导入延迟、证据 UNKNOWN | WAIT / 核对或系统修复 | 当作永久缺失，或让 Planner 自签事实 |
| 已获准且明确的方法不适用、Verifier 正式 finding 指向结构问题 | SLOW / 有界修复 | 将建议直接写成新结构 |
| 某 Task 返工额度耗尽，但原 Obligation/任务政策仍允许方法修复且预算可用 | SLOW / 原修复入口 | 无条件开启无限 PlanRevision 续命 |
| 原责任的硬尝试/预算上限已经耗尽 | STOP 或原已支持的等待额度路径 | 通过换 TaskId/Method 重置累计限制 |
| 资源忙、限流且政策允许等待、审批未完成 | WAIT / 指定资源或审批变化 | 请求 Planner 猜测授权 |
| Effect/Operation 结果未知 | WAIT / RECONCILIATION | 180 秒超时或费用按上限计入就重发动作 |
| 图损坏、跨 Mission 引用、旧 generation | STOP 对应业务范围 / 原隔离诊断 | 默认转 Slow 让模型“修数据库” |
| 所选必需子项已接受，但尚欠组合或根审阅 | FAST / 请求既定组合或根审阅 | 把所有绿色节点直接计作 Mission 完成 |
| 根要求及必需效果满足，原收尾政策允许 | FAST / 原唯一 finalizer | UI 直接写完成 |
| 取消/终止后有迟到回执 | STOP 新业务 + 原核对继续 | 丢费用、丢真实效果，或复活旧工作 |

Worker 的“缺输入”报告、Verifier 的“建议重规划”都是问题线索；先由原来源/合同/正式审阅绑定核对。**不新增一套公开 Verifier verdict 枚举**，将现有结果适配成内部问题分类即可。

### 3.6 持久唤醒、幂等与累计限制

- 复用原 events、planning requests、dispatch intents、TaskGraph followups 和收敛 job；不建新队列或新的 Task 状态表。
- 一个稳定问题只对应一个活动规划请求。issue_key 以 mission、受影响范围、问题类、相关事实身份、合同/局部 generation、已批准逻辑 round 构成。
- **不要**以当前时间、所有事件的最新 cursor、无关分支 plan revision 变化或每次心跳构造新的问题身份。
- Planner 提案仍绑定其真实 base revision；旧提案过期时按原规则拒绝并受控重新观察。请求重建不重置同一责任的费用、失败和修复上限。
- WAIT 必须有具名唤醒来源；事件驱动为主，沿用有界周期核对兜底。检查与登记等待之间发生事件，不能丢失唤醒。
- 需要创建请求时，原持久 request/intent/receipt 与消费回执保持原事务或已定义的持久交接关系；不能先 ACK 再把动作留在内存。
- 没有实际新增请求、状态变化或持久结果，返回 progressed=false。重复查询、重复拿到同一幂等 receipt 不算进展。
- 仅在路由/原因变化或真正产生动作时记录可观测事件。Gate 自己的观察日志、心跳和租约刷新不能触发自己反复唤醒。

---

## 4. 新的六批顺序与签收目标

| 批次 | 工作 | 依赖 | 本批结束必须看到什么 |
|---|---|---|---|
| 开工检查 | 固定 opt.32 + dirty；记录已有失败和环境 | 无 | 可复核的施工基线，dirty 未被覆盖/提交 |
| 1 | 真实缺陷与基础反例修复 | 基线 | 输入链同源；局部拒绝不破坏其他 Mission；真实哈希测试可解释 |
| 2A | 合法制品来源与新任务严格启用 | 1 | 新任务无法回落非严格路径；首次计划前有正式绑定 |
| 2B | 四类推进规则接入原循环 | 2A、原调度与审阅 | 正常/返工自动推进；结构问题有界修复；WAIT 不空转 |
| 3 | SDK 正式执行过程投影 + 默认执行图 | 2 | 图展示真实 TaskGraph 和执行因果，Host 不直读 SDK 表 |
| 4 | 产品入口与已完成能力接通 | 2；相关 UI 依赖 3 | 从聊天/设置/资料入口到同一正式 Mission 链 |
| 5A | ARP MISSION 精确来源 | 2、3 的正式来源契约 | Mission Agent 不再靠 standalone 分类避开来源合同 |
| 5B | 同部署统一目录与 Skill 入口 | 5A；原 Catalog/Skill 能力 | 所有模型池看到同一目录准入/版本/撤销事实 |
| 6 | 同制品跨层验收和交付 | 1–5 | 小型端到端场景、真机、制品身份和遗留账逐条闭合 |

2A/2B、5A/5B 是同批内的小片，不要求新建独立项目。第三批的 UI 合同可与第二批并行设计，不能先在 Host 又写一套临时 SQL 上线。

---

## 5. 第一批：先修正在运行路径上的缺陷

### 5.1 局部收集失败隔离

位置：内嵌 SDK `orchestrator/event_handler.py` 的 SUBMITTED 收集分支。

- 两处 `intent.id` 改为真实 `intent.intent_id`。
- 不增加 `except Exception: pass` 来伪造可靠性。已知业务拒绝保留待核对状态和具名原因；非预期错误仍需可见。
- 使用两个真实 Mission、原 Store、原循环。A 的收集分别触发 BudgetError 和 CommitRejected；B 仍能前进/完成。
- 检查 A 的原调用与费用不丢失，不因日志异常又生成第二个调用；重启后不重复创建请求。
- 在必要时定位拒绝是否可重试；永久拒绝不能每轮无界重试并刷日志。

### 5.2 输入链“同一份”的完整修复

目标链：

```text
完整读取 Task 输入合同与 DATA 关系
  → resolver 返回成功/等待/非法/来源不可读
  → 同源 Readiness
  → EligiblePrimitiveTask
  → Attempt + InputManifest + DispatchIntent 原子关联
  → workspace materialization
  → handoff 当前性复核
```

必须比较的核心身份：mission、task、occurrence、Task contract、局部 binding revision、dispatch generation、manifest hash、具体接受/产物 hash 与相关当前来源读集。无关全局图版本变化不自动废掉未受影响的合法 Attempt。

实施要求：

1. 将“无 DATA requirement”的特判收回统一 resolver 成功路径：还要检查必需输入端口是否确实没有数据要求，不得仅凭 requirement 列表为空。
2. 合法无输入任务允许得到明确成功的空 manifest；结果等待/错误/来源不可读不得得到空成功。
3. `admissions()` 禁止 `manifest is None → empty`。不完整结果返回原 typed refusal。
4. readiness 与 admission 不得分别解析出不同输入；可将精确 manifest 身份纳入原报告读集/绑定，不为此重建一套授权对象。
5. 冻结之后每次真正新 Attempt/合法新请求使用原身份分配规则；恢复只读取原冻结输入，不取 latest。
6. handoff 前检查权限、fence、generation 和相关源当前性。单纯比较 hash 不等于授权检查。

至少保留以下反例：合法无输入通过；必需端口无边拒绝；生产者未接受等待；解析来源失败不变空；已检查的 manifest 被替换为空/另一任务清单拒绝；冻结后撤权/换 generation 拒绝；无关分支更新不误杀。

### 5.3 CI 的真实哈希问题

WorkAgent 报告 Node 22 本机 958 通过，旧 CI Node 20 三项 SubtleCrypto 失败。本轮未复现，不能预先宣判为测试环境问题。

- 使用相同锁文件、相同 canonical 测试向量，对仓库当前 CI 环境与本机环境分别核对。
- 定位 TextEncoder/Uint8Array/ArrayBuffer 与 WebCrypto 的实际 realm、字节边界及测试初始化顺序。
- 可修复测试环境的 realm 装配或产品 bytes 传递，但必须保持真实 SHA-256 和跨语言 golden vectors。
- 不允许 stub `digest()` 返回期望值、不允许删除身份测试、不允许只升 Node 而不说明既有失败的原因与支持策略。
- 对真实 Tauri WebView 的凭据绑定至少做一次定向确认。CI 修绿不等于原生认证链已经通过。

### 5.4 保护 opt.28–32 已确认行为

将 WorkAgent 指定的以下现有回归加入本次受影响集合：分步职责、分步审阅范围、原有有界合成重问、遗留名额处理、上限结算与收尾重算。

尤其保留三种不同事实：

```text
原始用量：KNOWN / UNKNOWN
记账依据：ACTUAL / UPPER_BOUND
外部效果：SUCCEEDED / FAILED / UNKNOWN（沿原账本）
```

费用按上限计入不证明动作已完成；释放本地模型名额不证明远端没有执行；不将已有内容调用恢复政策无条件套到有副作用工具或 Operation 上。[S14]

---

## 6. 第二批 A：合法重建来源，并默认启用严格 TaskGraph

### 6.1 来源清单到底证明什么

区分三件事：

1. **源码/包身份**：当前安装字节是什么。
2. **上游证据来源**：本候选由哪些真实 HTN 接线与恢复证据派生。
3. **本候选行为验收**：当前 TaskGraph、Assurance 和 Host 实际通过了哪些场景。

把 597 个 hash 换成 726 个正确 hash，只能解决第 1 件事。没有第 2、3 件事，不能自称完整接线或产品验收通过。

### 6.2 清单生成流程

由 WorkAgent 在仓库内复用现有冻结脚本；如不存在合适脚本，再新增一个确定性的构建/核验命令。它是构建工具，不是新的服务。

**步骤 1：冻结实际施工输入。**
记录当前 Host commit、相关 dirty 内容、内嵌 SDK 输入清单、依赖锁、所有已锁 codec 的版本与 hash。不得回到 opt.27，也不得只记录 HEAD 而忽略 dirty。

**步骤 2：核验上游证据原件。**
解析真实 upstream wheel/manifest/source inventory/核心 Mission receipt/cold replay receipt，核对内容 hash、来源关系和作用范围。旧 `semantic_replay=PARTIAL` 保持 PARTIAL。

本地原件缺失时，不许复制旧文档中的 64 位字符串充当可核验来源。明确列出缺项；若需要重建上游证据，走隔离环境中原已有 HTN 核心链的实际复验，产出新的回执。无法取得的来源仅阻断其消费者，不阻止其他纯逻辑实现。

**步骤 3：确定派生差异。**
输出 parent → candidate 的文件新增/删除/变化清单，并标识对输入准入、计划提交、恢复、Assurance、费用的影响。上游证据只支持其原范围，不自动给变化后的代码签收。

**步骤 4：对构建 staging 的实际安装内容生成 inventory。**
严格按 reader 覆盖 `agent_orchestrator/` 与 `simple_harness/` 的运行时文件。排除清单本身和 reader 明确排除的缓存；不得忽略变化最大的目录来“过校验”。锁定 codec 字节变化要登记新 codec 版本，不覆写旧 manifest。

**步骤 5：生成无自引用的身份。**
生成 `source_files`；按原 canonical 算法对不含 deployment_id 的清单求 deployment_id；最终 wheel 构建完成后，再把 wheel hash 写入包外 candidate manifest。不要把 wheel 自身 hash 放进该 wheel 内部文件形成循环。

**步骤 6：在全新隔离安装目录验证候选 W。**
运行原 `InstalledHtnWiringAcceptance`，核对包集合、字节、实际导入位置与 Host candidate pin；用真实上游证据生成，不替换 reader、不 monkeypatch `_read()`。来源反例至少含缺文件、同名文件变字节、清单摘要变更、证据来源缺失。

**步骤 7：在同一个 W 上完成本批小型行为验收。**
来源允许派生检查不等于行为通过。启用、非空 DATA、首次 Commit、重启、当前/历史严格读取都必须真实经过 W 的实现。

**步骤 8：不改 W，直接将 W 提升为 Host 候选。**
安装和测试后核对相同 wheel hash。验收结果放在包外、绑定 W 的测试与签收清单中。为写 `VALIDATED` 而重新打包将改变身份，应作为新候选重新验证；本方案默认保留包内 `taskgraph_acceptance=NOT_RUN`，在包外分别记录已通过的小范围与仍未运行的大矩阵。

**每次构建都执行同一生成/核验链。** 第二批修好清单后，第三至五批继续修改 SDK，必须生成新的候选及其清单；不能让清单再次停在第二批。各批证据分别绑定自己的候选，最终第六批只签收最后一组 Host + wheel + 锁文件/配置/目录身份。构建已完成后不得原位改包内源码或以旧 wheel hash 解释新源码。

### 6.3 谁签什么

| 责任人/程序 | 签收内容 |
|---|---|
| 确定性构建脚本 | 输入清单、来源核对、候选制品 hash；不签“产品完成” |
| 原测试驱动 | 实际场景输出、失败、原始日志与候选身份 |
| WorkAgent | 代码到合同到测试的对应关系与未完成项 |
| 独立审阅者 | 核对来源与差异、反例、生产路径，不用文档结论代替证据 |
| 用户/既有发布流程 | 是否部署到用户使用环境、合并/发布；本计划不自动代办 |

### 6.4 新任务默认启用的精确时序

现有启用依赖实际规划授权，因此不要在 `create_mission()` 最后一行盲调用后吞掉异常。

```text
新任务创建（同事务建立正式要求、根责任，绑定“本任务要求严格 TaskGraph”）
  → 原 CompletionSpec 批准流程（auto/manual 沿当前政策）
  → 持久原初始 PlanningRequest/授权申请所需的 seed 信息
  → 原有效规划授权到位
  → Host 以真实委托 caller 发独立、幂等 EnableTaskGraphContract
  → SDK 同事务写绑定 + 启用回执 + 事件
  → 首次正式 PlanCommit
  → primitive 准入、派发与后续严格路径
```

初始授权所需的请求/seed 可以先存在；它不是已经采用的正式 PlanRevision。等待启用期间不得绕到非严格 PlanCommit/Worker dispatch。若实际源码还有其他前置来源，WorkAgent 在 source-map 中说明并保持同样的“先来源后消费”顺序，不能伪造授权消除循环。

实施约束：

- 新 Mission 的 required 标记由创建工厂/部署配置持久绑定，不来自模型、前端布尔值或临时环境变量。
- 自动授权与手工授权后的继续路径调用同一个启用协调方法；启动恢复也可重试同一命令。
- 启用命令 id 从新 Mission 与部署合同稳定派生；并发两次只产生一个有效绑定与回执。
- 首次 PlanCommit、普通 dispatch 以及必要的结果消费都验证 required 标记与实际 binding，不能只在 UI 显示 ON。
- 当前 SDK `_delegation()` 要求特定有效决策权限。不得为通过启用而擅自扩大 grant；缺权限沿原授权流程处理。
- 自动启用仍是独立于规划授权的受信系统动作。事件记录 SYSTEM/HOST_DELEGATED 与真实 principal 关联，不能记录 HUMAN_CLICK。
- 启用失败表现为明确等待/部署故障，不退回旧执行链；已绑定任务重启不重新绑定，不替换历史启用来源。
- 旧任务不自动启用、不回填假历史。本阶段不开发旧任务迁移，不额外建设永久兼容栈；现有可读取历史保持只读，无法支持的旧合同明确返回错误。不得擅自删除历史或停止用户正在运行的任务。

### 6.5 证明“真的消费了严格图”

不能只断言 policy 表多一行。必须同时观察：

- 正式 PlanCommit 产生对应 revision record/pins；
- Attempt/intent 绑定精确输入与来源；
- 故意去掉必需来源/更改 generation 后，真正 handoff 拒绝；
- 恢复不新造计划或调用；
- `taskgraph.snapshot/why_not_ready/diff/convergence` 对该新任务给出原实现的有效响应；
- 禁掉新内核的必需来源，测试应失败，而不是 fallback 后依然完成。

---

## 7. 第二批 B：让四类推进规则真正接管“何时再规划”

### 7.1 实施落点

| 位置 | 本片改动 |
|---|---|
| `orchestrator/progress.py`（拟新增） | 内部类型、判定表、纯函数；复用已有枚举与类型化引用 |
| `orchestrator/event_handler.py::_decide` | 从原就绪/运行/Assurance/Operation 事实取得各范围决策，消费 FAST/WAIT/STOP |
| `_start_planning`、`_refine_open_compounds`、`_request_method_synthesis` | 新任务、待展开、无方法等场景统一经过 SLOW/已有工作判定，不各自绕过防重 |
| `_retry_deferred_planning`、`_retry_deferred_repair` | 同问题持久身份、重新授权与费用上限；不因每轮或重启重复请求 |
| `scheduling/allocator.py::allocate_v2` | 继续接收真实 admissions，保持公平、背压与并发约束；不移入 LLM |
| `taskgraph_notifications.py` 及原 followup | 只唤醒重新观察/收敛/组合；actual dispatch 仍在原循环 |
| 原 Commit/dispatch/handoff | 独立复核当前授权、输入、generation、budget、在途状态 |

先将现有判断映射到一张表，再抽取重复分支。不要先建一个新规则引擎，最后发现旧 `_request_method_synthesis` 仍在并行唤醒模型。

### 7.2 Planner 收到什么

沿原请求包附加有界、系统生成的修复上下文，不新增通用工具协议：

```text
当前真实 plan / requirements 引用
受影响的 occurrence 与原因
证据/原始 finding 引用及来源覆盖
已存在的合法输入/Acceptance
必须保留的已完成贡献与共享需求
仍未知的 Operation / 在途工作
当前允许的已有 PlanningDecision 与 repair kinds
同一问题的累计尝试、额度和仍在途的请求
```

Planner 只看到本次授权范围内需要的信息。它不能删掉难要求、取消仍被其他消费者需要的共享任务，或把旧的外部动作换一个 ID 再执行。

### 7.3 第二批最小验收

1. 三步 A/B→C，包含至少一个非空 DATA 和一个纯 ORDER；完成一项后不额外请求主 Planner，合法时自动启动 C。
2. B 内容被要求返工一次：Task/合同/计划不变，新 Attempt 合法生成；不同尝试和费用均保留。
3. 明确结构缺口产生一次原 Planner 请求；重复事件、反复 tick、重启不重复开轮。
4. C 等数据、D 独立 READY：C WAIT，D 仍推进；C 需结构修复、D 未受影响时同样不无故阻断 D。
5. 在途 Review/待导入结果不误判死锁；没有实质变化的 tick 不记 progressed=true。
6. 未知操作结果只核对，不重发；费用上限结算不改变该效果状态。
7. 首次提交前、创建 intent 前后、物理返回到导入之间分别中断；按原事实恢复，保持不重复执行/不丢费用。

这些是本批阻断门，不是新的大矩阵。可映射并扩展现有 TaskGraph/Assurance/OCC 测试，避免同一功能建三套题库。

---

## 8. 第三批：SDK 驱动的默认执行图

### 8.1 两种边，不混进同一个调度图

**计划结构层**继续使用严格 TaskGraph 的 refinement / ORDER / DATA / adopted method 等含义。

**执行过程层**展现实际 Attempt、AgentTurn、Review、RepairRequest/Decision、PlanRevisionCommit 和 Operation 回执之间的因果关系。

例如同合同返工：

```text
步骤 B
 ├─ Attempt 1 → Review 1（返工）
 │                  └─ rework-of → Attempt 2 → Review 2（通过）
 └─ 当前合法贡献 → 后继步骤
```

结构修复则显示：

```text
Review/受信问题 → RepairRequest → PlannerDecision → PlanRevision 2
                                                └─ 影响的后继工作
```

视觉上可以画“绕回步骤”的回路；底层应是不同执行实例间的有向因果边。**不得将 B→B 或 B→其上游的展示回边写进调度 DAG。** 两次 Attempt 不能合并成一个历史节点，旧失败不能被新版成功覆盖。

### 8.2 API 放在哪里

在 SDK `agent_orchestrator.api` 的 TaskGraph 读取边界下增加组合只读接口，例如：

- `taskgraph.execution_snapshot`
- `taskgraph.execution_detail`

名称为拟新增；可按现有命名风格调整，但不得由 Host 重新定义合同。

实现复用 `TaskGraphReadApi` 的认证、结构读取和 token；SDK 内部增加薄的执行投影模块，调用原 Store、Assurance、Operation 与受信运行回执读取器。`story.py` 仅作为已验证字段/交互的迁移参考，不能继续充当产品权威查询。

Host 只做认证运输、错误映射和显示；`live_graph.py` 与 `story.py` 的 SDK 表直接读取从新主路径移除。旧代码是否删除按 dirty 保护和真实调用图决定，不能只把文件改名保留原 SQL 旁路。

### 8.3 最小返回合同（拟议）

```text
TaskGraphExecutionViewV1
  schema_version = 1
  mission_id
  view_mode = CURRENT | HISTORY
  read_token = 复用原 TaskGraph 读取 token，不重定义原字段
  graph = 原 TaskGraphViewV1，token 必须完全相同
  execution_cut
    observed_at_ms
    imported_through_seq
    runtime_source_watermarks[]
    coverage = COMPLETE | PENDING_IMPORT | SOURCE_UNAVAILABLE
  execution_nodes[]
  execution_edges[]
  next_cursor
  complete
```

节点至少包含系统稳定 ID、kind、精确 task/occurrence/attempt/intent/AgentTurn/review/operation 引用中适用的部分、对应 plan revision、已记录状态、时间字段和一句话摘要。不用一个全可空的大字典伪装所有实体；按 kind 使用小型判别联合。

摘要包含 `text + source_ref + source_kind`，来源限实际工具/结果/正式审阅/规划理由。短摘要推荐上限 240 字符；不要为显示每个节点另开模型调用，也不要声称知道模型“心里怎么想”。

过程边使用独立枚举，如 `attempt_of/review_of/rework_of/repair_requested/decision_for/committed_as/supersedes`；不冒充 ORDER/DATA。分页暂未返回端点时以明确的引用/未展开标记表示，不能丢边假装图完整。

默认每页 100，最大 200 个执行条目；计划结构仍服从已有 GraphStructureBudget。上限是分页/展示合同，不允许截掉安全判定所需事实。

### 8.4 同一读取时点的正确含义

不能声称“先读 orchestrator.db，再读各 execution.db，就获得了一个跨库原子快照”。

本阶段采用最小实现：

1. 在原 orchestrator Store 的一致读事务中读取严格图、业务状态、已经可信导入的执行映射/回执及事件水位，构造共同 read_token。
2. 缺少 Attempt→AgentTurn→Review 等映射时，由原 collector/importer 追加有来源的关联事实；不是 Host 根据时间、角色文本或 ID 前缀猜 join。
3. Runtime 尚未导入的内容显示 PENDING_IMPORT；取不到来源显示 SOURCE_UNAVAILABLE，不解释为空、无操作或成功。
4. 展开原始回合细节时，SDK 按快照中的精确 ID/hash/watermark 读取冻结 Journal/CAS 内容并复核当前披露权；不得补进快照之后的 latest 文本。
5. 对多运行库明确给出来源水位向量，不声称所有物理调用恰好发生在同一墙钟时刻。

为避免昂贵新快照存储，先采用原不可变记录 + token 绑定的 keyset 分页。游标必须绑定 mission、视图、token、排序键和授权范围，并受服务端验证；来源变化无法维持该 cut 时明确 `SNAPSHOT_CHANGED`，前端重取，不混页。

读取当前权限不被冻结的历史权限替代。ACL/撤权变化后不得继续披露旧敏感页面；当前状态与历史结构/历史执行时间轴明确区分。

### 8.5 私密信息与展示白名单

默认返回：角色、步骤职责、已展示的文本回复、获准工具名、脱敏参数与结果摘要、提交摘要、正式 Review verdict/finding、Planner 对外解释、用量事实与记账依据。

不返回：密钥、token、Authorization/header、`.env` 内容、原始内部路径或未授权文件、系统/开发提示词、隐藏思考、未向该用户披露的材料。工具参数/结果通过字段白名单和现有脱敏器；需要正文时走原权限与精确产物读取接口。

`story.py` 的实际白名单尚未在本会话读取，因此 WorkAgent 需以其 dirty 实现做逐项对照；本计划不声称它已经满足上述要求。

### 8.6 前端交付口径

- 默认主标签为 **执行图**，时间线为次要标签；结构视角可以作为执行图内部切换/折叠。
- 步骤节点显示“状态 + 一句话进展”；Attempt/Review/修复过程按需展开。
- 用户点节点后查看真实对话、工具、审阅理由及对应证据，不靠 ID 名字猜过程。
- 正常等待与异常阻断区分；显示具体原因，例如“等上游验收”“等批准”“结果核对中”，不把全部都画成失败。
- 在首次启用尚未完成时，通过正式接口显示 ACTIVATION_PENDING/原具名错误及原因；不伪造已经启用的图，不回落 Host SQL。
- 仅相关实质事件触发合并刷新；心跳不重拉大详情。结构未变化不重新布局，状态未变化不重画整卡。
- 断线、慢读、分页跨状态、权限变更均有明确信号；旧图保留时标 stale，不继续宣称当前有效。
- 过程读接口不能触发模型调用、预算预留、重试或业务事件。只读审计访问日志如现有政策要求可保留，但不计作任务进展。

---

## 9. 第四批：补齐产品入口，不建立旁路

| 条目 | 本阶段处理 | 验收重点 |
|---|---|---|
| 发布目录设置入口 | 恢复到现有 FilePublishConnector 的正式设置/授权路径 | 不存在目录、卷能力不支持、与源/证据目录重叠都拒绝；设置可撤销且不伪造已发生效果 |
| 中途新增/撤回参考资料 | 接原 Source 生命周期与版本入口，不只是改前端数组 | 在途 frozen manifest 不改写；新输入或当前性变化交原 Gate/Commit 处理；受影响与无关分支分开 |
| 主 Agent 在聊天里发起任务 | 一个受控 tool adapter 调用与 UI 相同的 create/create_with_sources facade | caller/scope/budget 从真实会话获得；重放只建一个 Mission；新任务默认严格 TG；原聊天收到 task handle 与状态 |
| 委派工具 | 逐项接回原 Agent 工厂、委派 ticket 与预算，不在 TaskGraph 外另跑一个业务图 | 模型可见集合等于当前可执行集合；无法交付的工具先不向模型曝光，并在能力盘点中具名保留欠项 |
| 熔断/自动续跑 | 从用户关闭的监工 Agent 开关中解耦，使用确定性政策 | 监工仍关闭；断路器与恢复实际生效；UNKNOWN 效果不自动重发；续跑受原累计限额约束 |
| 模型路由 | 将 SDK 已有规则接入 Host 的真实角色/能力选择与运行池；缺少配置/来源明确阻断 | Worker/Verifier/Planner 的实际 provider 与配置一致；换模型不改变旧冻结请求、不降级独立审阅或绕计量 |
| 多任务并发 | 开启原有能力，按当前真实资源和 provider 配额执行，不强行串行也不盲目放大上限 | 受控测试额度为 2 时能推进两个任务；配额为 1 时有界排队、公平、不挤掉结算与审阅 |
| 多候选择优/仲裁 | 接已有 Selection/Conflict 路径与预算条件，不另造选择系统 | 默认能力可用，由已有明确触发和限额决定是否运行；不对每一步强制多开候选或额外仲裁模型 |
| 写着开启但无读取方的配置 | 每个列“来源→消费者→生效证据”，连接或移除假开关 | 不能只改配置默认值为 true 就标完成 |

模型可见但调用必定“不可用”的工具不是“已开启”；先隐藏属于防止误导，不算完成。若它是本阶段必交功能，阶段末仍未接通须明确记缺项，不得用下架抵消承诺。

聊天 Agent 发起后台 Mission 与该 Mission 内部委派是两种操作：前者创建原正式 Mission，后者遵守该 Mission 已批准的 TaskGraph/任务范围。不能用通用 `spawn_*` 工具绕过步骤、验证、效果合同。

用户明确关闭或排除的功能仍保持其决定：监工代理、实时语音、被明确禁用的官方端点路径、自然语言遗忘、两人审批、产品策略晋升、JEV。默认开启规则不自动撤销这些更具体的限制。

---

## 10. 第五批 A：完成 ARP MISSION 模式，不只改 profile 字符串

目标：使来自 Mission 的 Agent 创建、上下文、冻结和交接确实消费 Mission 权威来源；独立聊天保持其真实独立语义。

### 10.1 最小来源集

复用原已定义引用与端口，至少包括：

- 创建原 dispatch intent 与受信 caller、任务角色、授权范围；
- Mission / Task / Occurrence、合同 revision、采用方法与原计划出处；
- 本次 Attempt 的冻结 InputManifest，或该角色明确不适用的正式理由；
- 审阅角色的 ReviewPackage / purpose / completion scope / criteria pins；
- 当前 generation/fence、输入披露权、Operation/效果限制与现行策略。

Planner、MethodSynthesizer、compound reviewer 不存在 Worker Attempt，不能伪造一个来满足通用接口；按角色使用判别联合。原 Reviewer package/currentness 是 Verifier 来源，不拿 Worker manifest 充数。

### 10.2 接入顺序

1. 在原 Orchestrator adapter 产生角色化来源；不由 Host 将任意 dict 贴上 MISSION 标记。
2. 创建时保存精确 binding 与来源证据，缺失则具名拒绝。
3. Context 装填使用该 binding；最终 wire 准备后冻结 manifest/计量，不在冻结后追加“最新图”。
4. handoff 前通过原权威再检查权限、local generation、相关输入/合同及 fence。
5. 同一创建键恢复读原输入；不能跨任务、跨 occurrence 或变更 profile 重放。
6. 将新 Mission profile 默认切到完成接线后的 MISSION；不能在拒绝时静默改回 STANDALONE_CHAT。

至少测试：错 mission、错 occurrence、输入过期、撤权、旧 generation、审阅包错目的、Planner 没 Worker Attempt 的合法路径、创建中断、冻结后恢复、无关分支更新不误杀。

---

## 11. 第五批 B：统一能力目录后开放 Skill 入口

统一的含义是：**同一部署 realm 的目录、包版本、准入和撤销有一个权威管理者**。不同执行池可以有不同缓存与能力兼容结果，但不能各自批准一份同名技能。

- 复用已有 Catalog/managed install/Skill lifecycle，去掉将 `profile_id` 当目录权威分界的装配假设。
- 不新建“全局技能数据库”与旧目录竞争；如果需要移动物理归属，明确原 owner 与新的只读客户端连接。
- 一个技能导入一次，256K/512K、thinking/non-thinking 及其他获准模型池读取同一 skill/version/admission identity。
- 每个池仍按自身能力核对工具支持和 tokenizer/renderer 约束；共享目录不等于所有池都能执行所有技能。
- 暂停/撤销后的下一次使用重新授权；已 handoff 的调用仍按原核对协议处理，不改写其历史请求。
- 完成目录一致性、授权及使用前复核后，再开放相应明确列举的 `agent_*` 产品动词；不要仅靠扩大 WebSocket 前缀让所有写操作穿透。
- 源码中已经完成的 SCRIPT 执行适配应复用，不恢复早期 `script_runner=None` 作为本轮“已知状态”。缺沙箱/模型等真实资源时显示准确原因，不伪装可执行。[S13]

签收：导入一次→多个池同版本可见→分别执行与计费→同一次撤销在所有池下一次使用生效→重启后仍一致；跨 owner/未准入版本/未授权工具被拒。

---

## 12. 第六批：小型跨层验收与交付

### 12.1 核心端到端场景

| 场景 | 必须经过的真实组件 | 核心断言 |
|---|---|---|
| E1 多步内容 | Host 原创建→严格 TaskGraph→Allocator→Worker→Assurance→根完成→执行图 | ORDER/DATA 正确；完成事件不多开 Planner；最终根要求全覆盖 |
| E2 一次内容返工 | 原 Result/Review/Attempt | 同合同重试不换计划；两次尝试/两次审阅可见，旧失败保留 |
| E3 一次结构修复 | 原问题→规划请求→受信提案→收敛→PlanCommit | 只有必要范围改变；无关已接受贡献保留；同问题不重复唤醒 |
| E4 必需效果 | 原 Spec→准备→Proposal Review→真实批准→受控 connector→Outcome Review→根收尾 | 实际效果仅一次；UNKNOWN 不重发；费用与效果分离 |
| E5 强制退出/恢复 | 原冻结请求、调用账本、导入和 startup/replay | 无重复业务效果，重复投递回执幂等，迟到费用不丢 |
| E6 多 Mission 隔离 | 两个真实 Mission 和共享资源池 | A 被拒/等待/修复时，B 在获准资源内继续；不靠整个服务重启兜底 |
| E7 图与权限 | SDK 两类图读口、分页、细节、UI | 同 token/明确覆盖；重试回路非调度边；撤权不漏数据；只读不触发模型 |
| E8 Runtime / Catalog | MISSION 来源、共享目录、多池、Skill | 精确来源拒绝矩阵；同版本/准入/撤销跨池一致 |

E1–E6 可以复用少量固定任务与故障点，不要求八套全新大型模型题集。确定性 fixture 测机制；真实模型和原生点击测实际线路与交互，两者分开记结果。

### 12.2 测试纪律

- 先写具名反例，再改该段代码；每个小片只跑相关测试。
- 独立审阅按 Handoff 最多两轮，只挡阻断项；风格建议入后续账，不拖成重设计。
- 建议将六批视作同一交付序列，中间以定向证据签收，正式合并前/后各一次全量回归；若用户选择分批独立合并，则每次按原既定合并门禁执行，不能口头假设旧全量结果仍覆盖新字节。
- 既有失败必须逐 nodeid、失败类型、同环境前后对比，不能只说“还是 26 个”就证明没有新失败。
- 真机使用隔离的数据目录；无权限不读取生产凭据、不向真实外部收件人/账户做副作用测试。
- 关键机制测试可以替换模型传输与受控 connector 服务，不得替换 Commit、来源 reader、授权、Graph、费用或官方 Review importer 来过门。
- 真实模型、桌面点击、上游证据、各 OS 能力未运行即 NOT_RUN/BLOCKED，不用组件测试代替。
- 原 TaskGraph42、完整变异/stateful、大统计矩阵继续按用户已决定的后置顺序保留；不本轮强迫展开，也不从目标需求中删除。

### 12.3 阶段 Done

必须同时满足：

1. 新任务默认绑定严格内核，无静默降级。
2. 正常推进与同合同返工不反复叫 Planner；结构问题通过受控入口有界处理。
3. UNKNOWN、撤权、错误来源和旧 generation 不获得执行许可。
4. 执行图来自 SDK，计划与尝试/审阅/修复身份真实对应；Host 不再直读新路径的 SDK 表。
5. 产品入口默认可用且只走正式链；不保留“模型可见但必定不可用”的误导工具。
6. MISSION 来源与统一目录两个缺口均具名签收；未完成不能宣称整个阶段完成。
7. 同一冻结 wheel、Host、配置与测试证据可追溯；新源清单没有伪造历史通过。
8. 保留本机未提交工作，只有用户授权才提交/合并/安装/发布。

---

## 13. 对 Handoff 七节五个待定问题的直接答复

| 问题 | 本方案裁定 |
|---|---|
| 来源清单如何合法重生？ | 按 §6：真实上游证据核对→捕获实际字节→确定性生成→同候选安装验证→行为验收→原 wheel 提升。清单证明与验收分开，大矩阵仍 NOT_RUN |
| 执行过程 API 放哪里、怎么对应时点？ | 放 SDK TaskGraph API 边界，内部聚合原运行与审阅权威；主快照一个原 Store cut，运行细节用精确 pins 和来源水位，绝不假称跨库原子时刻。见 §8 |
| “已做好都开启”怎么落地？ | 本阶段纳入产品入口、真实并发、路由、委派、选择/仲裁可用性和假开关清理；按来源/资源/预算触发，保留用户明确禁用项。见 §9 |
| 是否同步独立 SDK 到 opt.32？ | **不作为当前前置，也不在本次规划中执行公共同步。** 开发权威继续是 Host 内嵌 SDK；在本阶段候选签收后，再按明确公共发布授权导出 SDK 子目录并核对字节/测试/敏感材料。不要为同步而回退或制造双主分支 |
| 第五批拆分或后移吗？ | 拆成 5A/5B，可分别核验；排在 TaskGraph 与正式视图之后，仍在本阶段收口前完成。若现实条件阻断，要记明确阻断和影响，不以“后续”抹掉缺口 |

---

## 14. WorkAgent 开工时的第一份回报

不重复询问本文件已经裁定的架构问题。只需提供：

- 实际 HEAD/dirty/wheel/导入身份与 Handoff 是否一致；差异及保留情况。
- 第一批三个修复的准确函数位置、反例 nodeid 与最小变更范围。
- 当前 TaskGraph 启用所需原授权入口、正式首次 PlanCommit 入口及其先后关系。
- 当前所有 Planner/MethodSynthesizer/修复唤醒入口，哪些将消费同一推进规则。
- 所有“已做好未进入产品”的功能，逐项写真实消费者、开启条件与阻断；不只列开关值。

沿 Handoff 既有节奏分批汇报并按用户授权推进；计划交付本身不授权把当前 dirty 任务视图提交到远端，不要求用户重复回答“是否引入 JEV”等已定问题。

---

## 15. 本次来源索引

以下远端文件统一固定在 Host commit `4a1678aef365f39e06cccf2b1d3a0d6552c04b71`。本次是**关键条款与代码的定向读取**，不是全仓审计；文档历史测试值保留为原作者记录。

| ID | 路径 / 本次读取范围 | 用途 |
|---|---|---|
| S00 | `backend/deskpet/sdk_adapters/sdk_candidate.py` 23–42；GitHub main branch 元数据 | opt.32 与 Host 基线 |
| S01 | `sdk/simple-harness-sdk/src/agent_orchestrator/orchestrator/taskgraph_deployment.py` 1–140 | 来源清单算法及 NOT_RUN 边界 |
| S02 | 同目录 `taskgraph_policy.py` 1–270 | 一次性启用、原事务、审计 actor、captured baseline |
| S03 | 同目录 `taskgraph_policy_sources.py` 1–270 | 规划授权依赖、策略与部署来源 |
| S04 | 同目录 `event_handler.py` 3270–3550 | 循环、真实 intent.id 缺陷、现有规划/合成防重 |
| S05 | 同文件 11940–12160 | `_decide`、组合/根审阅、Assurance 收尾与 allocate_v2 |
| S06 | `sdk/simple-harness-sdk/src/agent_orchestrator/scheduling/allocator.py` 230–440 | 原 allocator 与准入边界 |
| S07 | `.../orchestrator/taskgraph_notifications.py` 1–190 | 原持久 followup 与循环的分工 |
| S08 | `.../orchestrator/hierarchical_dispatch.py` 1441–1540 | 无声明 DATA 的 None 语义 |
| S09 | 同文件 1908–1990 | `resolved_inputs` 与合法空输入 |
| S10 | 同文件 2832–2870 | `admissions` 中 manifest 缺失补空 |
| S11 | `sdk/simple-harness-sdk/src/agent_orchestrator/api/taskgraph.py` 1–210 | 正式只读 API、未启用状态、原 token/来源结构 |
| S12 | `plans/TaskGraph/V1.2.1/TASKGRAPH-MAIN-HANDOFF-2026-09-23.md` | 主体阶段与完整验收分离、启动顺序、旧来源不自动继承 |
| S13 | `plans/AgentRuntime/2026-09-23-arp-body/HANDOFF.md` 起始部分 | ARP 已交付能力、SCRIPT 后续已接通，避免退回早期状态 |
| S14 | `ARCHITECTURE/AGENT_ORCHESTRATION.md` 1–102 | opt.28–32 分步职责、上限结算与恢复修订 |
| S15 | `plans/2026-09-25-mainflow-optimization/PLAN.zh-CN.md` 起始部分 | 当前开发兼容/保留政策与已知产品接口限制 |
| S16 | `plans/taskSys2/升级planV1/v1.4/simpleharness-full-target-1.4/complete-plan.zh-CN.md` 起始部分 | 不缩减最终目标；TaskNetwork 与 Evidence/Operation 的权威分离 |
| S17 | 用户当前消息中的 WorkAgent Handoff | 本机 dirty、任务过程视图、用户决定、六批草案与报告结果；未冒称读取本机字节 |

**未执行事项：**本次没有重新运行 opt.32 全量测试、重新计算其完整制品 inventory、启动 Tauri、访问真实模型/账号、修改仓库、提交 dirty 改动或同步公共 SDK。上述工作属于后续实施与验收，不在本文件中提前标 PASS。
