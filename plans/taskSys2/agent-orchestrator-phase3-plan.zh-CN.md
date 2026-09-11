# Phase3：让已完成的编排内核成为真实可用的 Agent 产品

**版本：P3-v1.0｜评估日期：2026-09-11｜交付类型：源码差距评估 + 可逐步实施计划**

> 起点：用户确认 `agent-orchestrator-incremental-build-plan.zh-CN.md` 已作为 Phase2 完成，正在进行真实 UI 接入与测试。本文不把项目退回 BaseAgent，也不把 Phase2 重新拆开建设。
>
> 目标：保留已有 Mission / Task / Attempt / Proposal / Commit / Blackboard / Verifier / Budget / Trace，实现 **真实 App → 真实执行 → 有依据的验收 → 可解释的反馈调整 → 可靠交付**。
>
> 本文的 Phase3 是用户的下一轮工程阶段，**不是**《完整设计》§28 中“第三阶段：规模化与安全”的同义词。旧 Phase2 已覆盖原文四阶段对应的第2—9步；新 Phase3 是产品闭环、跨领域适用性与真实验证的增量。

## 0. 证据、范围与限制

### 0.1 两份目标材料与已完成基线

- **D：**用户本轮上传 `agent-orchestration-layer-complete-design(1).md`，保留其31章、术语、状态机、Proposal/Commit、分层验证和验收结构。
- **T：**用户本轮上传 `agent-orchestration-theory.zip`。已解压并读取其13个章节；引用写成 `T03`、`T13§7` 等，对应源文件及小节。它是理论资料，不是项目完成记录。
- **P2：**`agent-orchestrator-incremental-build-plan.zh-CN.md`，按用户确认作为已完成工程基线。不是重新要求执行它的第2步。

正文 `[Rxx]` 是本轮读取的仓库源码，完整路径、commit、读取区间与来源记录在附录和 `sources.json`。新增函数、表、协议、测试和UI交互均是**拟议实现**，不是声称仓库已有。原文没有定义的具体字段、发布门槛和顺序均明确属于本计划的工程约定。

### 0.2 仓库快照

| 仓库 | 本轮结果 | 可以确认 | 不能据此确认 |
|---|---|---|---|
| Harness SDK | `ae8f0ec70eaffc33033f7541db2b0af82641f629` | SDK源码0.9.7、编排包0.9.0；旧计划2—9步有对应实现说明与模块 | 当前App实际安装的wheel、全部故障测试或真实任务成功率 |
| Service SDK | `47f372adc641d8d3516599dd21cb94cf5955d6a7` | Service源码0.3.12；BOM固定Harness0.6.2、Memory0.5.2；Unix短RPC仍是旧入口 | Host是否使用另一条本地分支/直连接入路径 |
| `simple_harness` Host | 当前GitHub连接返回404 | 这次访问未建立 | 仓库不存在、Host未实现UI、或者Phase2没完成 |

版本来自实际版本文件与BOM。[R01][R02][R19][R20]

本轮做了定向静态源码审阅与设计材料对照；未下载并运行当前SDK完整测试，未执行真实Provider、沙箱、UI或生产操作。没有修改远端仓库。旧 `INTEGRATION_STATUS.md` 仍描述0.6.2，不能代替实际部署清单。[R18]

### 0.3 什么叫这一阶段可验收

四个状态分别记录，不混成一个“已完成百分比”：

1. **已实现：**Phase2代码和用户完成报告。
2. **已接通：**真实Host的有效路径和安装制品可复验。
3. **已验证：**指定环境、场景、故障切点具有真实结果。
4. **已改善：**与冻结基线相比，在真实任务中有足够的质量/成本/稳定性证据。

本计划不给尚未测试的代码承诺“100%无故障”。用可执行的退出门槛代替：安全与一致性断言不通过不得开放对应能力；效果证据不足不得晋级策略。样例/fixtures证明机制，不证明模型研究能力。

---

## 1. 对当前状态的判断

### 1.1 不需要重建的能力

当前已具备独立 `src/agent_orchestrator/`，源码更新记录覆盖：单任务验收闭环、静态DAG、Blackboard/冲突/综合、动态改图、多Mission/模型/背压、人工审批/动作核对、Replay/归因/评测、策略候选/审批晋级。[R03]

因此，Phase3不新建第二个调度器、第二个Blackboard、第二套预算或通用Agent类。沿用两个权威边界：

```text
编排事实：Mission / Task / Attempt / Claim / Budget / DAG
    → 原 Orchestrator Commit Service

执行事实：BaseAgent / AgentTurn / ProviderInvocation / ToolEffect
    → 原 SDK Runtime / UoW

产品UI：查询投影、发送受控命令、展示正式结果
    → 不直接写上述数据库
```

### 1.2 当前真正值得补的差距

| ID | 代码事实 / 证据 | 与目标材料的距离 | Phase3处理 |
|---|---|---|---|
| G01 | Service BOM停留在旧依赖；Unix无Mission RPC；实际Host路径未知 [R19][R20] | 有内核不等于App已使用同一套内核 | P3.1固定真实链路、能力协商与制品闭包 |
| G02 | `MissionApi.get/cancel/events`是内部接口，无显式principal参数；create未映射MissionSpec的全部可选项 [R04][R05] | 对外暴露需要身份、字段完整性、分页和可恢复查询 | P3.1加窄的鉴权适配，不让UI直接透传内部对象 |
| G03 | ToolGateway主要是四个工作区/pytest工具；明确不声称网络隔离 [R09] | 独立目录≠可安全执行任意生成代码的隔离环境 | P3.2实现真实隔离和第一个可审计产品动作 |
| G04 | Claim的VERIFIED提升以pytest覆盖为主要确定性路径；formal层未部署 [R07][R08] | 还不能用同一语义覆盖文档研究、事实核查等领域 | P3.3领域验收与证据适用性，保持旧代码策略 |
| G05 | `explorer`在声明模板列表，但不在`ENABLED_TEMPLATES`；未知role回退worker [R10] | 有Explorer角色名，不等于看到不同探索材料 | P3.4确认真实Provider输入而不是只改标签 |
| G06 | RuntimeProfile无Context工作配置；装配未传tokenizer/context_policy/embedding [R11][R12] | BaseAgent支持的Context能力未必在编排链路生效 | P3.4每profile显式配置和端到端选择回执 |
| G07 | 会话召回正文构造成`MessageRole.SYSTEM` [R13] | 数据来源与指令权威边界需要补强 | P3.2先封住权限/指令升格，P3.4完整验证低信任Context表示 |
| G08 | 已有固定候选数/老化/规则分配；不是完整多路线方法证明 [R14] | 理论要求区别探索、利用、反证和综合，不能只复制候选 | P3.4受控选择策略、互补产物组合与部分成果复用 |
| G09 | 配置可把`max_concurrent_model_calls`抬高到逻辑并发×候选数 [R11] | 真实硬件容量不能为避免排队而隐式扩大 | P3.5区分资源等待与失联，遵守实际物理槽位 |
| G10 | Connector要求强权威lookup；演示采用TestConfigService [R15][R16] | 测试服务的幂等性质不能自动迁移到真实服务 | P3.2按真实动作能力逐个开放；无权威查证保持UNKNOWN |
| G11 | 已有Replay/evaluation/promotion，规则改进明确是启发式 [R03] | 模块和机制测试不能证明真实质量提升 | P3.6用真实案例、版本与独立标准做受控晋级 |

G02是远程边界需补的接口条件，不是已经证明Host存在越权漏洞；G07是已观察到的信任表示风险，不是本轮已经复现了攻击；G08不是否定现有动态DAG和多候选；没有全量审计大文件，也不以搜索未命中证明能力缺失。

### 1.3 最优先的顺序

**先真实链路和安全 → 再跨领域验收 → 再有效搜索协作 → 最后用真实负载与评测确认收益。**

不要现在重写BaseAgent、换数据库、上多级Manager、增大Agent数量或引入MCTS来掩盖上述差距。现有状态机、事件与账本继续使用。

---

## 2. Phase3由六个可用功能版本构成

| 版本 | 完整新能力 | 演示结束时用户能做什么 |
|---|---|---|
| P3.1 | 真实App Mission控制闭环 | 创建任务，看正式DAG/进度/证据，取消或审批；断线重开还能继续 |
| P3.2 | 隔离执行与真实受控交付 | 在隔离环境产出文件，经内容绑定审批发布到指定目录 |
| P3.3 | 非代码任务与证据闭环 | 给真实资料，拿到引用可回读、冲突可解释、验收有依据的报告 |
| P3.4 | 有效动态搜索与知识组合 | 看到不同路线的真实探索、局部改图、失败片段复用和再次验收 |
| P3.5 | 真实负载与长任务恢复 | 多个Mission共享少量模型槽位，排队/背压/等待/重启都能收敛 |
| P3.6 | 实际效果与制品发布 | 用任务证据比较版本，定位失败，验证升级/回滚，受控发布 |

每个版本内部可以分PR，但不得将“写schema、做后端、接UI”分别宣布成三个完成步骤。上述每行必须一起交付接口、持久状态、运行行为、UI、测试和证据。

推荐按表顺序验收；工程上可并行准备测试，但不提前开启依赖后续安全门槛的产品能力。P3.1当前UI工作直接并入，不另建一套控制台。

---

## 3. P3.1：真实 App Mission 控制闭环

**对应：D§3/5/15–17/22–24；T09/T12。**

### 3.1 完整用户场景

用户在当前App提交一项小目标 → 得到稳定Mission ID → UI呈现Plan、Task、Attempt和验证状态 → 用户中途刷新/关闭窗口 → 重开后继续查看 → 需要人工时提交审批/评论 → 最终查看有来源的结果。

第一条真实Provider场景只开放已获准的只读资料或现有已验证安全环境。P3.2未通过前，不让任意生成的代码在带真实凭证的Host进程中执行。

### 3.2 将现有UI联调收敛成一条权威链

```text
现有UI / 页面状态
    │ typed command / snapshot + event page
    ▼
实际Host transport adapter（H0定位）
    │ 认证Principal、会话与Mission关联
    ▼
Service兼容适配或已有已验证直连适配（二者只选实际主路径）
    │ Mission / Approval / Artifact API
    ▼
现有 Orchestrator + CommitService
    │ stable attempt→agent→turn binding
    ▼
现有 AgentRuntime / Provider / Tool ledger
```

**H0不是额外开发阶段：**在这个功能版本内记录Host实际commit、入口路由、服务装配、UI store、事件消费和审批处理的`symbol → file → test`映射。当前访问失败不阻止SDK/Service接口工作，但Host安装版验收缺失时不能把P3.1标完成。已有实现保留，只补缺口。

### 3.3 代码变更

- 修改SDK `api/missions.py`：新增对外受控请求适配或Facade；显式映射已开放的`untrusted_sources/synthesis/conflict_reserve_tokens`等字段。未知字段拒绝，暂不开放的字段明确报错，不静默忽略。保留原内部API兼容路径。[R04][R05]
- 新增 `api/read_models.py`：按principal过滤的Mission、Task、Attempt、Claim、Artifact只读投影；返回`through_seq`、graph_version与状态版本。读取快照及对应游标应来自同一读事务。
- Service新增 `orchestration_contracts.py`、`orchestration_service.py`；`transports/unix.py`仅分派新类型请求。业务提交仍交SDK，不在Service中复制Mission状态机。
- 若实际Host已直连新SDK：优先把该路径收敛到同一Facade和合同，不强制绕远Service后又保留一条不一致的写路由。
- 更新Service的依赖、BOM、lock和准确的制品来源；候选版本在打包时确定，不伪造版本号/hash。不能靠禁用BOM、`--no-deps`或调PYTHONPATH实现“兼容”。
- Host启动记录`DeploymentManifestV1`：host_commit、distribution版本和wheel hash、schema版本、有效功能集、模型与context profile版本；不包含密钥。

拟议外部操作集合：

```text
orchestrator.v1.mission.create
orchestrator.v1.mission.snapshot
orchestrator.v1.mission.events
orchestrator.v1.mission.cancel
orchestrator.v1.artifact.read
orchestrator.v1.approval.decide
orchestrator.v1.human.comment
```

已有Host端点语义一致时使用显式映射，不必须改名。`create`返回持久命令回执，不阻塞等待整个Mission；进程生命周期由Host管理，不随HTTP/Unix连接关闭。审批提交只唤醒适用的既有等待流程。

### 3.4 合同和一致性

```python
# 拟议合同示意：字段仍需在真实模块中严格校验和版本化。
@dataclass(frozen=True, slots=True)
class MissionCommandV1:
    command_id: str
    mission_id: str | None
    kind: str
    expected_version: int | None
    payload: Mapping[str, JsonValue]

@dataclass(frozen=True, slots=True)
class MissionViewV1:
    mission_id: str
    through_seq: int
    graph_version: int
    snapshot: Mapping[str, JsonValue]
```

Principal来自认证连接/Host，不接受模型或UI自由指定tenant。所有读、取消、审批、artifact范围读取均检查对象归属；不能因为用户猜出mission_id就有权限。对外artifact使用不可变ID和hash，不接受任意本机绝对路径。

事件页绑定Mission与游标，服务设最大page_size。初次读取snapshot到`through_seq=N`，再读`seq>N`；UI按event_id去重、按seq/版本应用，有缺口则重取快照。单Mission seq不是全局顺序；跨Mission时间线通过独立列表游标定义。

UI显示“请求已接收／排队／运行／待验证／待人／UNKNOWN／正式交付”而不把模型文字“完成了”当状态。Token streaming是临时展示，不等于已提交Output；正式通知使用稳定ID去重。

### 3.5 交付与退出门槛

实际安装App中的一项Mission从创建到正式产物完整可操作；完成本步8项验收与已有Phase2回归。记录两个区别：Host UI自己重连成功；Host/服务进程重启后后端也恢复成功。没有H0实际映射和安装版证据时，最多标记“SDK/Service已就绪，Host待验证”。

---

## 4. P3.2：隔离执行与真实受控交付

**对应：D§14/20–22；T10、T13的工具、补偿、安全与人工介入。**

### 4.1 完整用户场景

在App请求生成一份报告或补丁 → 在隔离执行环境处理并验收 → UI显示不可变产物与目标目录 → 用户批准具体内容 → 发布到用户明确授权的目录 → UI展示实际回读hash和动作回执。

第一个真实动作选**发布一个新的、版本化报告文件**，不选付款、删除、替换系统配置或任意Shell。动作级别按现有L0–L3政策注册；越过沙箱写到用户工作目录时按明确授权的L2处理，不因文件看似无害就默认拥有目录权限。

### 4.2 为什么优先于扩大任务种类

当前 `run_pytest()` 在Host子进程运行，仅有cwd、环境白名单、进程组和超时；文件明确不承诺网络隔离。[R09] Worker能写测试代码时，其程序能力不等于几个受限文件工具。因此“独立Workspace”要从目录组织进一步落实为真实OS/运行环境边界。

### 4.3 代码变更

- 新增 `runtime/sandbox.py::SandboxExecutorPort`：启动、状态查询、终止进程树、收集有界输出、读取产物。返回execution ID、image/environment digest、有效限制与回执。
- 将 `tool_gateway.py::run_pytest()` 的生产执行路径改为该Port；保留明确命名的`process_only`适配，仅用于可信测试/开发，生产未配置隔离器时拒绝执行未受信任代码。
- 在开放真实外部资料之前，先修正已观察到的召回原文SYSTEM表示，或通过有效的输入适配拒绝此类高权限投影；P3.4再完成完整Context策略和回归。不能等到搜索优化阶段才处理指令/数据边界。[R13]
- `artifacts/workspace.py`登记workspace ID、base snapshot、只读输入、可写输出和清理条件；不是把一个目录字符串当沙箱证明。
- 第一部署可由Host提供经过验收的隔离进程/容器适配：非特权身份、仅挂载指定workspace、无宿主HOME/密钥/容器控制socket、默认无网络、CPU/内存/PID/输出限制、明确进程树取消。部署环境未知，不能在本计划宣称用户已经有该适配。
- 新增 `runtime/connectors_publish.py`：实现现有`Connector`协议及可回读的发布回执；接入原`ActionExecutor`，不得由Worker直接写目标目录。[R15][R16]
- 对已存在的approved/outbox/handoff/UNKNOWN逻辑不重写；只补真实连接器合同测试、实际target规范化和错误映射。

### 4.4 发布动作与回执

```text
CandidateArtifact(hash=A)
    → required verification PASS
    → action candidate(target + version + hash A)
    → human approval bound to exact action/version/content
    → begin_handoff：权限、版本、预算、receipt复核
    → connector.publish(idempotency_key)
    → authoritative receipt / UNKNOWN
    → final product status
```

推荐初版不覆盖已有任意文件：目标名称包含稳定action identity，写入临时文件并经同文件系统原子、不覆盖的发布过程完成，设置目录访问约束和symlink防护。正文hash、目标canonical path、服务回执必须匹配。文件已经存在但内容不一致是冲突，不应覆盖成“幂等”。

关键：现有 `Connector.lookup()` 的None语义只有在实现满足协议的**权威查询承诺**时才可以表示未开始；最终一致查询、暂时查不到、网络失败都只能保持UNKNOWN。新适配若无法满足强合同，应新增版本化Lookup结果类型或明确拒绝supports_reconciliation，而不是假设None安全。[R15][R16]

发布后取消Mission不撤销已发生事实。需要移除/修改已发布内容时生成新的补偿提案和审批；删除等L3能力本轮默认禁用，仅跑拒绝和双审批机制测试。环境重启不能让远端在途动作自动变成“未发生”。

### 4.5 退出门槛

本步8项安全/动作验收全部通过；实际沙箱能力探针、受控目录发布及回执丢失恢复均有证据。UI能展示“产物已生成但尚未批准发布”和“已真实发布”的区别。TestConfigService演示继续回归，但不能用它代替真实连接器验收。

---

## 5. P3.3：非代码 Mission 与证据闭环

**对应：D§10–15/20；T04、T13§6–9/领域专项。**

### 5.1 完整用户场景

用户提供几份带版本的真实文档，要求比较方案并写出报告。系统完成：Planner划分资料核查/比较/整合 → Worker引用来源 → Critic核对遗漏和矛盾 → 领域检查/必要人工审阅 → 只把符合既定依据的结论写入正式知识 → 返回可点击原始来源的报告。

初版新增**文档/资料研究领域**，保留已完成的代码领域。SQL、网页行为、数学形式化等注册为后续适配候选；本轮不声称全领域支持，也不为了“形式化”而强装Lean。研究材料先用用户提供且已获准的资料；开放网页抓取需另外通过工具网络与来源准入验证。

### 5.2 不是降低Verifier要求

当前Claim的VERIFIED主要依赖通过且覆盖引用目标的pytest；其他可信引用更多是SUPPORTED。[R08] 对代码任务这条路径保留；不能让文档任务创建一个无关pytest来取得VERIFIED，也不能因为Critic说“不错”就直接形成事实。

保留原文“Schema→规则→独立Critic→测试/实验→形式化→必要人工”的层次。不是每项任务都执行六层，而是Task声明哪些层必要；未部署或未执行的必需层不能PASS。[D§14][R07]

### 5.3 代码变更

- 新增 `domains/profiles.py::DomainProfileV1`：允许工具、输入/产物类型、Planner要求、Verifier adapter配置、证据类型、完成规则。创建Mission时冻结profile版本，Planner不能临时把验证器换成更宽松的。
- 增加 `verification/evidence_resolver.py`，真实解析artifact/source/tool-run/knowledge引用；绑定租户、Mission、内容版本、来源、可见性与适用范围。
- `memory/claims.py::grade_claim()` 改为从**有效的验证与覆盖回执**推导等级；旧pytest策略保留为legacy兼容适配。`tool-run:`或`knowledge:`前缀本身不是证据存在和适用的证明。[R08]
- `verification/verifier_router.py`在保留V1政策的前提下，新增版本化检查规范和adapter registry。新领域可在规则层执行引用完整性/来源hash/覆盖核对，在外部检查层执行领域测试或查询，Critic独立审阅。V2的外部检查与旧`code_test`显式映射，不把旧字段悄悄改义。
- 更新 `contracts/models.py`、TaskProposal解析、`CommitService`接受路径：把criterion→claim→evidence→verifier的适用绑定纳入提交复核。
- UI展示每条结论的证据/适用范围/审阅记录；明确区分SUPPORTED、VERIFIED、DISPUTED、未知。不得把来源的指令信任与事实可验证性合成一个布尔值。

拟议证据检查记录：

```text
CriterionAssessmentV1
  criterion_id / task_contract_revision
  claim_id / claim_revision
  output_ref / output_hash
  evidence_refs + source_versions
  verifier_adapter_id + version
  checked_scope
  verdict: PASS | FAIL | INCONCLUSIVE | NEEDS_HUMAN
  receipt_id / provenance
```

这是新审阅载体，不是新增Task终态。INCONCLUSIVE映射为补证据、SUPPORTED或人工流程，不强行通过，也不把原文Task状态机改成另一个框架。

### 5.4 证据语义的底线

- “测试通过”只能支持真实覆盖到的要求和主张；测试名称/路径匹配不等于逻辑内容关联。
- 用户提供或网页中的材料在指令层是不可信数据，但它包含的事实仍可以被独立核验；不能因内容来源不受信任就丢弃全部信息，也不能因被验证就提升其指令权限。
- VERIFIED意为在记录的范围、版本和验收政策下得到验证，不意为永恒真理。
- 旧资料与新资料冲突：保留双方、记录版本/时间/条件并进入Conflict Task，而不是多数表决。
- 摘要、合成报告必须记录用了哪些知识；组合后的产物重新验收。
- 同一个版本的证据撤回或取代，新的使用/验收必须重新检查；历史事实不重写。

### 5.5 退出门槛

旧代码Mission仍通过回归；一份真实报告的全部关键主张能回读对应源段落或明确标注证据不足；伪造引用、无关测试和隐藏条件能被验收拒绝。完成本步8项测试。对无法验证的开放式结论，正确的不确定性报告也是合法交付，不宣称统一形式证明。

---

## 6. P3.4：让动态DAG成为有效搜索，而不只是图会变化

**对应：D§6–11/15/17/19–20；T02–05/07–08/10。**

### 6.1 完整用户场景

使用P3.3的资料研究或复杂代码任务：Explorer提出不同解释；Exploiter推进当前路线；Critic给出反证；Manager依据反馈增加缺失前置/替换失效工作；Connector找可复用发现；Synthesizer组合有效片段；Verifier检查最终结果。

UI必须能回答：**为什么改图？停了哪条路线？复用了哪份成果？哪些输出没有被采纳？**不以新增节点数或Agent数展示“智能程度”。

### 6.2 沿用Phase2，不重写任务图

已有TaskGraphChange及add/supersede/retarget/priority/pause/role操作继续使用。正式DAG仍通过Commit Service。用户/Worker提供的新信息先成为带来源的事件或Proposal，不直接覆盖图JSON。[R03][D§15]

任务显示树、执行依赖DAG、知识血缘是不同关系。不同候选路线优先用同一Task的不同Attempt表达，复杂分解继续用DAG；本轮不引入全面AND–OR/Proof-DAG数据库，也不宣称达到形式化证明的组合保证。[T02]

若接受用户新增限制：新增显式要求修订提案，记录旧/新版本与影响范围；不得改变已终态Task历史。正在执行的旧工作收敛后替换/重验，无关分支继续。权限扩大或预算追加仍走既有审批/Commit，不能当成普通聊天自动生效。

### 6.3 真正启用角色上下文和模型工作配置

- `context/context_builder.py`修复Explorer模板声明与启用列表差距；同时检查调用者是否实际提供candidate knowledge，不能只把字符串加入tuple。[R10]
- 将角色指令、未验证资料、已验证知识、执行历史分开序列化；Verifier保持独立证据包，不只看Worker总结。
- `runtime/model_router.py::RuntimeProfile`增加序列化的`context_profile_ref`，由装配解析为tokenizer、工作输入预算、输出预留、embedding/rerank及检索策略。
- `runtime/assembly.py::assemble_orchestrator_runtime()`实际把这些注入BaseAgent `AgentRuntimePorts`，并把配置版本写入每次dispatch/Context receipt。[R11][R12]
- 修正 `_RecallAdapter` 把历史原文构造成SYSTEM消息的表示：使用Provider支持的数据块/低权威消息及清晰provenance；不要伪造tool result，也不要把所有数据改成“真实用户指令”。工具权限仍在程序层落实。[R13]
- 每个新的Provider请求重新检查工作预算，已冻结请求的恢复不重新检索。全量Journal不回灌，检索选择不再次作为新事实归档；不把Context压缩用作清零预算的手段。

上下文工作预算没有跨模型通用最优数值。固定的是**经评测的模型部署×任务类别配置上限**，内容动态选择、不填满；本计划不指定32K或256K为科学最佳值。

```text
本轮输入预算 = min(工作配置上限, 模型输入限制,
                   模型总窗口 - 输出预留 - 安全余量)
```

将当前Task合同、直接依赖、真实未决状态和硬约束列为必需内容；旧对话、候选线索按相关性选择。放不下必需内容时应明确阻塞/拆资料，不静默丢关键要求。

### 6.4 受控候选选择与部分成果复用

保留原有默认`FIRST_VERIFIED`行为；新增仅供指定Mission/Task政策启用的`COMPARE_THEN_SYNTHESIZE`候选选择。它是已有Attempt之上的决策记录，不是第二套Task状态机。

```text
同一个Task
  ├── Attempt A：路线A、候选A
  └── Attempt B：路线B、候选B
          ↓
      独立检查与证据整理
          ↓
   选择有效候选 / 组合已验证片段 / 继续有限探测
          ↓
   CandidateArtifact C → 再验收 → 一次正式Commit
```

- 新增 `planning/candidate_selection.py`，记录候选集合、预算、选择模式、截止条件、各候选理由和结果；不要让最先到达的自然语言决定胜出。
- 修改既有接受/取消兄弟Attempt位置，使默认行为不变，仅对显式模式延迟选择；等待候选有上限，不能为追求比较永远不交付。
- 整体失败Attempt中的有效局部片段可以生成独立Claim Proposal，经验证后复用；不因此改写原Attempt为成功。
- 合成产物是新候选，不能直接继承输入产物的PASS。
- 未选路线的消耗保留；它们不应成为最终交付“必须全部完成”的依赖。最终采用清单列出实际交付Task/Artifact和必要依赖，选择关系不冒充完成事实。
- 共享知识的事实等级不因被多个相同Agent复述而上升；复用证据独立性和适用条件纳入选择。

### 6.5 动态决策要改变实际行动

Manager输入限定为根目标、当前合同、触发结果、相关子图、失败、证据、剩余预算和允许操作。执行反馈区分：缺前置、实现错误、方法前提不成立、权限不足、结果未知、无新进展。系统先检查故障类型，不把每个失败都变成“继续拆小”。

复用原§29.3优先级作为基线，不立刻改成学习模型。增补可观察的进展信号：关键依赖解锁、有效反证、非重复路线、可复用知识；所有模型分数只是建议。新实验与最终验证都占同一Mission预算，不能靠换角色或拆Task增发额度。

### 6.6 退出门槛

一个受控开放任务至少展示一次有证据的局部改图、一次跨分支复用和一次最终综合验收；满足本步8项测试。角色差异要从真实Provider请求和产物观察，不靠UI颜色。对照评测若显示多路搜索没有收益，保留简单模式，不强制启用复杂搜索。

---

## 7. P3.5：真实负载、长任务与恢复

**对应：D§16–19/23；T06/09–11。**

### 7.1 完整用户场景

至少三个Mission共用少量真实模型槽位，包含一个慢Verifier、一个等待人工的任务和一个长Context任务。用户可以继续查看/控制；局部服务退出后恢复；没有因为资源排队而失联重跑，没有因为UI关闭而丢失Mission。

逻辑Agent数量与物理模型并发分开。建议试验起点为2个模型槽、2个Verifier槽及至少3个Mission，只是压力场景参数，不是性能承诺或统一最佳配置。

### 7.2 尊重部署的硬资源上限

当前`OrchestratorConfig.__post_init__`会为候选数量提高模型并发配置，属于需要修改的真实行为。[R11]

- 不自动提高显式硬上限；超过容量时排队或拒绝不合法配置，保留用户给定值。
- 新增 `scheduling/resource_admission.py`，区分逻辑Attempt准入与实际Provider/Verifier/Tool槽位。物理槽预留不是第二次费用扣减。
- 同一部署全局槽位上限与每profile上限同时约束；多个runtime pool不能各拿一份“全局上限”导致总并发翻倍。
- 队列等待、模型执行、工具执行、人工等待、外部UNKNOWN各有原因和计时策略。排队时间不是Agent失联；总Mission期限仍可包含排队并触发明确停止。
- 等待槽位后再次检查取消、授权与请求版本。不能先把Provider记为已交接，再无限等内部队列。
- 若跨进程运行，槽位和claim须共享持久身份或由唯一资源服务承担；仅有进程内Semaphore不能称为跨进程全局限制。

### 7.3 背压不是只画队列曲线

保留原Backpressure六项限制和高低水位/滞回。实际检查“待验证积压→Worker减速→暂停低价值扩展→优先验证/冲突→队列排空”。预留最终验收资源，避免所有额度消耗在探索。

保留公平/优先级老化，但控制命令优先于普通LLM消息。取消后的旧回执可以作为已发生事实结算，不能批准新的业务结果。

### 7.4 跨库与副作用恢复

编排库和各profile执行库仍是不同事务域。恢复凭稳定dispatch identity、agent/turn/result绑定和receipt核对，不声称跨库原子事务。

- SDK已有turn结果：收集原结果，不能新建另一个Agent重做。
- 编排SUBMITTED：继续未完成验证；适用版本变化时重新检查，不重新执行Worker。
- 编排租约过期：先检查SDK执行权和未知动作；符合原LOST规则后才新Attempt，不能仅凭UI超时重派。
- UNKNOWN动作：保持原业务键核对；不能因Mission结束或服务重启认定未发生。
- 长Context：保留合法Journal与冻结请求；检索索引是派生数据，不是唯一恢复副本。

备份先停止新准入并在已知安全切点记录清单，再用数据库支持的WAL一致方式备份编排库和各执行库，并连同Artifact manifest、有效schema/config/receipt保存。恢复到隔离副本演练；实际外部副作用不会随数据库回滚而撤回，恢复前需要核对外部动作。

### 7.5 退出门槛

满足本步8项压力/恢复测试；硬上限、费用归属、队列上限有真实指标；UI区分排队、运行、待验证、待人、UNKNOWN。没有全平台压测时只发布已测试平台与并发范围。

---

## 8. P3.6：真实评测、可解释回放与受控发布

**对应：D§23/28–30；T12及T13的贡献、信誉、策略更新。**

### 8.1 完整用户场景

从App选择一项失败任务查看完整事件/证据链，生成脱敏支持包；在独立测试环境比较Phase2基线和Phase3候选；看到收益或不足；通过门槛后审批候选策略与制品，发生问题能够回滚。

不重做已有Replay、Evaluation和Policy Registry。源更新已包含这些功能以及“规则改进不是训练模型”“数据不足拒绝晋级”的保护。[R03]

### 8.2 三类评测，不混为一个分数

| 类型 | 证明什么 | 不证明什么 |
|---|---|---|
| 机制/故障测试 | 身份、状态、预算、去重、权限、恢复断言 | LLM能解决开放任务 |
| 真实模型任务评测 | 在固定任务/预算/模型条件下的实际质量、成本和延迟 | 所有未来任务或所有模型的性能 |
| 安装App端到端 | 用户实际入口、字段、状态、审批、事件和产物可用 | 每个算法都优于基线 |

预先冻结至少代码、文档研究、动态搜索三类任务；小样本可以先用于冒烟，但晋级必须按预先登记的评价规则与证据充分性决定。不得在看到结果后挑选有利案例或只统计成功重试。

指标包含：正确交付、误报完成、关键约束遗漏、证据覆盖、跨分支复用、独立验证误判、完成时间、实际费用、用户接管次数和恢复结果。创建了多少Task、跑了多少Agent不是目标。

### 8.3 代码变更

- `observability/evaluation.py`增加版本化domain test cases和判定器，但保留对高风险/生产连接器的拒绝。需要真实只读/沙箱执行的评测建立显式允许的测试适配，不关闭原保护。
- `traces.py`和`replay.py`纳入Phase3新增的来源、Context选择、候选选择、资源等待、UI命令和domain validation事件；旧事件无覆盖时明确not_covered，不回填不存在的历史。
- `governance/gates.py`与promotion保持原审批链。新功能政策版本只对新Mission生效，在途任务保持绑定。安全、预算和权限不能作为在线Agent可修改策略。
- 完整数据分开标real/fixture/unknown；统计结果保留基础设施失败及产品可用率，再单列模型有效样本，不能悄悄删除失败抬高成功率。
- 支持包包含源/制品版本、有限脱敏输入引用、事件、产物hash、Verifier依据、费用；不包含密钥或默认导出用户全部历史。

### 8.4 Replay与新实验必须拆开

理论手册T12既讨论重放，也讨论新策略下复现。这里显式提供两种不同功能：

```text
history replay：折叠已经发生的Event；不调用模型/工具/连接器。
re-evaluation：在新测试Mission中用新策略再执行；新预算、新产物、新结果。
```

这是接口语义澄清，不取消理论提出的任一能力。不能用“Replay”按钮偷偷执行现实操作。

### 8.5 制品与发布

- Phase2基线和Phase3候选分别冻结源码commit、wheel和Host包；BOM只记录实际可获取、hash匹配的候选。
- 先在副本执行迁移、验证旧Mission读取/恢复及新schema；升级不可逆数据时不能承诺旧二进制直接打开新库。
- 每项新协议与schema使用显式版本；当前源包含多份旧状态说明，发布时同步真实兼容矩阵，不覆盖历史事实。
- 回滚分代码/配置回滚、数据库恢复、外部动作补偿三类，不混用。
- 新策略证据不足时保留原策略；有评测界面不意味着允许自动在线调参。

### 8.6 退出门槛

本步8项测试通过，P3.1–P3.5及原Phase2回归在安装候选上累计通过；真实案例的结论与范围可复查。最终交付Release Decision：支持的领域/平台/并发/连接器、未开放能力、已知限制、升级/回滚步骤。

---

## 9. 横向约束：所有步骤都必须满足

### 9.1 不引入两份正式状态

UI store、Service缓存、向量索引、可观测视图都只是投影。正式Task图、Knowledge和预算仍通过原CommitService。若增加新读模型，其重建依据和event coverage必须声明。

### 9.2 不以改名代替能力

“Worker→Explorer”“过程日志→知识”“目录→沙箱”“测试通过→所有主张正确”“模型服务名字变化→真正换模型”“API返回→任务完成”，都不是有效验收。

### 9.3 来源一致与原文边界

- BaseAgent逻辑身份保留；Task和Attempt仍区分。理论中的可替换Agent执行单元，通过已有AgentTurn/Attempt绑定实现，不推翻已完成的基础类。
- 原文描述租约过期新Attempt；实际执行仍需先协调SDK的有效租约和UNKNOWN。声明两个租约层的处理边界，不把它们折叠成一个超时重试。
- 任务图修改保留原Task终态与历史；新要求用新修订/替代工作，不直接复活COMPLETED。
- 不把理论的Proof DAG等同于本项目已有数学证明保证；不把动态探索变成所有候选路线都必须完成。
- 本阶段Blackboard是团队知识，不引入用户长期Memory SDK；Service历史BOM里的Memory依赖另作兼容处理，不自动传入BaseAgent。

### 9.4 代码组织

继续模块化单体。当前`planning/`只有planner和manager，`scheduling/`只有allocator和backpressure；不要因为原文目录列出search_controller/scheduler，就把它们误写成当前已有文件。相关运行职责已集中在事件协调链路，新增窄模块时显式标为新增。`event_handler.py`、`commit_service.py`已较大，只在已有函数边界增加窄的coordinator/adapter，不再将六个Phase3功能全部塞入单个文件，也不先做全仓重写。新增测试先覆盖行为，再移动代码；外部ABI/receipt/hash语义不因拆文件变化。

### 9.5 Schema与事件演进

不得编辑已发布旧迁移文件或checksum。实现时读取真实schema descriptor，再递增分配新版本；本计划不预占一个可能已被后续提交使用的数字。编排库与execution库版本独立。新增类型采用严格字段集、canonical编码、identity+payload hash幂等冲突检查。

---

## 10. 每一步的交付内容与执行方式

每个版本一次性交付：实现代码、固定配置、至少一个完整UI场景、自动化正负测试、安装制品、事件/产物证据、失败说明与恢复/回滚步骤。

建议新增 `tests/orchestrator/phase3/` 及Service对应测试。Host测试路径待H0实际发现。下列命令中，Phase3新测试目录只在相应PR实现后才可运行；不是当前已有脚本。

```bash
# 在已按实际仓库lockfile建立的隔离开发环境中。
# 1. 原有回归目录已在当前SDK仓库发现。
python -m pytest tests/agents tests/orchestrator

# 2. 相应Phase3代码及测试落地后，才运行下面的新目录。
python -m pytest tests/orchestrator/phase3

# 3. 对同一测试集合重复做installed-wheel验证；
#    干净环境不允许源码路径遮蔽已安装包。
# 4. Host使用实际已有测试框架运行UI端到端，不在本文虚构npm脚本。
```

真实Provider/沙箱/外部动作测试明确opt-in和预算授权，不在普通单元测试中悄悄联网、花费token或写用户工作区。

一次场景证据至少保存：

```text
DeploymentManifest + scenario input hash
Mission/Task/Attempt/AgentTurn/command/action identities
事件游标与图版本变化
候选产物hash和采纳清单
Verifier实际结果/缺口/覆盖
Provider/model/context/tokenizer版本
实际调用费用和UNKNOWN
UI操作与可见状态（含重连/重启）
PASS/FAIL/NOT_RUN及具体限制
```

---

## 11. 48项Phase3验收场景

下表均为**待执行**；详细动作、期望和证据要求见 `phase3-acceptance.json`。不得把本文件生成或JSON检查称为SDK测试通过。

### P3.1：真实 App Mission 控制闭环

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| P3.1-A01 | UI创建一次 | IDs贯通、正式结果有验证依据；不是仅显示聊天回复 |
| P3.1-A02 | 断线与重开 | 不重建Mission、不重派已提交Attempt、不重复正式通知 |
| P3.1-A03 | 重试相同命令 | 同内容回旧receipt，异内容冲突；不重复创建/扣费 |
| P3.1-A04 | 越权读取和操作 | 全部拒绝，不泄露对象存在性或内容 |
| P3.1-A05 | 状态快照竞争 | 每个正式状态可收敛，缺口触发重新获取；不跳过未见事件 |
| P3.1-A06 | API字段不丢失 | 合法字段完整入库/回读；未开放字段明确拒绝，不静默丢弃 |
| P3.1-A07 | 人工等待可恢复 | 不重做已通过验证；旧nonce和过期版本不可用 |
| P3.1-A08 | 安装版本真实一致 | manifest对应实际import/distribution/BOM，不靠PYTHONPATH或旧INTEGRATION_STATUS证明 |

### P3.2：隔离执行与真实受控交付

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| P3.2-A01 | 沙箱真实有效 | 拒绝范围与资源限制符合SandboxSpec；不能仅检测cwd |
| P3.2-A02 | 超时无遗留 | 沙箱/子进程被回收，输出有界；不是只取消等待Future |
| P3.2-A03 | 审批后发布 | 只发布批准的不可变版本；实际文件可回读且hash一致 |
| P3.2-A04 | 内容变化重批 | 旧批准无效；新候选需新批准 |
| P3.2-A05 | 丢失发布回执 | 按稳定业务键核对原文件；无第二份额外产物或重复修改 |
| P3.2-A06 | 非权威查询不可重发 | 保持UNKNOWN；不把查不到当CONFIRMED_NOT_STARTED |
| P3.2-A07 | 拒绝权限放大 | 不执行；拒绝和依据进入审计；L3双审批测试不等于开放付款/删除 |
| P3.2-A08 | 恢复与补偿分离 | 事实保留；补救拥有独立身份/授权，不以改Task图冒充现实回滚 |

### P3.3：非代码任务与证据闭环

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| P3.3-A01 | 真实文档报告 | 每项关键结论可定位到源hash/段落，独立验收有记录 |
| P3.3-A02 | 伪造引用被拒 | EvidenceResolver失败；不凭前缀升级SUPPORTED/VERIFIED |
| P3.3-A03 | 无关测试不能洗白 | 没有criterion/claim适用绑定，主张不能VERIFIED |
| P3.3-A04 | 证据不足有出口 | 可返回明确局限/人工升级；不伪造PASS，不无穷返工 |
| P3.3-A05 | 冲突证据保留 | 双方带范围进入DISPUTED/Conflict Task；不多数投票 |
| P3.3-A06 | 不可信材料仍可核查 | 命令不执行；事实可以经独立验证，在限定范围取得状态 |
| P3.3-A07 | 兼容原代码领域 | 旧政策语义与回放不变；未部署formal层仍阻止接受 |
| P3.3-A08 | 失效证据不继续采用 | 按相关依赖版本拒绝接受或复验；历史不改写 |

### P3.4：证据驱动的动态搜索与跨分支组合

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| P3.4-A01 | 真实角色差异 | 实际Provider输入按角色不同；未验证材料均带标签 |
| P3.4-A02 | 局部动态修复 | Manager提交局部GraphPatch；新前置被执行，无关成果不重跑 |
| P3.4-A03 | 旧Proposal及迟到结果 | 不覆盖当前图；可重用部分需验证，预算不清零 |
| P3.4-A04 | 多个候选择优组合 | 按已批准选择策略保留片段、合成新Candidate、再验收；不机械拼接 |
| P3.4-A05 | 失败尝试的有效片段 | 局部提案可单独验收复用；原Attempt失败事实不改变 |
| P3.4-A06 | Context真实生效 | 使用各自tokenizer/工作预算；保护必需资料，记录实际选择和request hash |
| P3.4-A07 | 历史不升格指令 | 作为受控数据渲染，不能生成权限/控制命令；不使用SYSTEM身份承载原文 |
| P3.4-A08 | 有界探索会停止 | 按原预算、停滞和图深度限制停止/调整；不无限生成新节点 |

### P3.5：真实负载、长任务与恢复

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| P3.5-A01 | 物理槽位不抬高 | 实际并发不超过2；逻辑并发不改硬上限 |
| P3.5-A02 | 排队不算失联 | 不把排队Attempt标LOST，不增加重复执行；总Mission期限仍有效 |
| P3.5-A03 | 验证背压闭环 | 队列有界、Worker减速，冲突/验证保留额度并可排空 |
| P3.5-A04 | 预算不能双花 | 单事务预留正确；未知支出不释放；等待槽不是第二次计费 |
| P3.5-A05 | 交叉故障恢复 | 恢复身份与receipt一致；已完成任务不重跑；UNKNOWN先核对 |
| P3.5-A06 | 控制面及时生效 | 新非法handoff被拒绝；在途事实可结算；UI明确未完成与未知 |
| P3.5-A07 | 长Context不丢关键要求 | 保留任务约束/未决状态；冻结请求恢复不重新检索；历史可回读 |
| P3.5-A08 | 多库备份恢复 | 库身份和产物hash对齐；未能核对的外部动作不自动重发 |

### P3.6：真实评测、可解释回放与受控发布

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| P3.6-A01 | 真实任务对照 | 相同任务边界/预算；标明real/fixture，保留全部失败和人工判定 |
| P3.6-A02 | Replay不重新执行 | Provider/工具/连接器调用计数不变；不支持字段明确not_covered |
| P3.6-A03 | 贡献链可解释 | 能到Task/Attempt/Agent/证据/Verifier/上下文版本，非胜出探索也有成本 |
| P3.6-A04 | 评测环境隔离 | 拒绝；真实只读/沙箱评测不绕过原test-only保护 |
| P3.6-A05 | 统计不足不晋级 | insufficient/拒绝；不因平均分略高上线 |
| P3.6-A06 | 策略审批和在途固定 | 新Mission用新版本，旧Mission保持绑定；回滚可追踪 |
| P3.6-A07 | 制品级累计验收 | 不是source-only；记录平台/依赖/实际模型/源和wheel hash |
| P3.6-A08 | 支持包脱敏与发布结论 | 不含密钥、未授权历史；发布明确支持范围和仍禁用能力 |


## 12. 与两份目标材料的覆盖关系

这是一张“保留与增强”矩阵，不把Phase2已完成内容标成缺失。全部章节映射见 `phase3-traceability.json`。

| 目标材料主题 | 当前基础 | Phase3增强位置 |
|---|---|---|
| Mission、核心循环、Single Writer | 已有API/Commit/事件闭环 | P3.1真实UI与身份；P3.5跨连接与恢复 |
| 动态DAG、Task/Attempt、Planner/Manager | 已有图变更与角色执行 | P3.4实际路线调整与组合，不重建图服务 |
| Allocator、预算、背压 | 已有规则与预算层次 | P3.5实际槽位和等待/失联区分 |
| 角色、多模型、Context | 已有profile与任务包 | P3.4真实配置/输入生效，修Explorer回退 |
| Blackboard、Claim、Verified Knowledge | 已有分层、冲突与来源 | P3.3跨领域证据；P3.4有效片段复用 |
| 验证、可信度与仲裁 | 已有分层执行、Critic、人审 | P3.3领域检查与范围，不能凭引用字符串升级 |
| Workspace、Tool、安全、HITL | 已有目录工具与动作协议 | P3.2真实隔离与实际审批交付 |
| Trace、Replay、Evaluation、策略 | 已有回放/评测/策略候选 | P3.6实际任务与安装制品，保持数据不足不晋级 |
| Proof DAG、MCTS、层级Manager等理论 | 是理论能力，不构成此阶段缺陷 | 保留后续研究，不设为Phase3强制交付 |

### 暂不实施

完整MCTS/复杂Beam搜索、数百Agent扩容、分布式多级Manager、未经数据验证的学习型模型、实际付款与破坏性删除、用户长期Memory SDK接入、一次性部署所有数学/科学/企业验证器。资料中的这些能力不是被删掉，而是没有证据支持它们优先于当前真实链路问题。

## 13. 推荐立即执行的工作

**先完成P3.1，不开始新一轮全框架重写。**把正在进行的UI测试整理成一条版本可证明、身份明确、断线可恢复的实际Mission链路，同时关闭未达到隔离门槛的危险执行能力。

然后完成P3.2的真实隔离与一次安全交付，再做P3.3的真实资料报告。到这里，你的系统就从“代码任务为主的编排能力”走向“能通过App完成个人实际事务的可验收产品”。P3.4再证明多Agent与动态改图的价值，P3.5和P3.6决定能安全运行多大、能否发布以及如何持续改进。

**完成Phase3的标志不是模块数量增加，而是：一个真实用户目标能被可靠接收、合理拆解、受控执行、根据证据改计划、经过适用验收，并交付可追溯的真实结果。**

---

## 附录A：本轮实际读取的源码

读取区间为GitHub源码行号，工具引用编号仅用于核查本次会话。部分大文件只读所列区间，不表示逐行完整安全审计。固定commit下的完整来源URL保存在 `sources.json`。

| 引用 | 仓库 / 文件 | 读取区间 | 本次用途 |
|---|---|---|---|
| R01 | `DennyWanye/simple-harness-sdk` / `src/simple_harness/version.py` | 1-end | 0.9.7；源码版本，不是已安装版本 |
| R02 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/version.py` | 1-end | 编排包0.9.0 |
| R03 | `DennyWanye/simple-harness-sdk` / `CHANGELOG.md` | 1-210 | 旧计划第2—9步的source-candidate实现说明；不是本轮测试报告 |
| R04 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/api/missions.py` | 1-end | create/get/cancel/events；create仅映射部分MissionSpec字段；读侧无显式principal参数 |
| R05 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/orchestrator/commit_service.py` | 1-285 | MissionSpec默认task_kind=code；单逻辑提交者；预算、状态和事件 |
| R06 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/orchestrator/event_handler.py` | 1-255 | 编排装配入口及故障点；仅读定向区间 |
| R07 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/verification/verifier_router.py` | 1-240 | 分层检查；required未执行不能PASS；formal_check未部署 |
| R08 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/memory/claims.py` | 1-220 | 当前VERIFIED提升依赖pytest覆盖；引用解析需要与真实解析回执闭合 |
| R09 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/runtime/tool_gateway.py` | 1-235 | 四个工作区工具；pytest子进程；明确不声称网络隔离 |
| R10 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/context/context_builder.py` | 1-255 | 11项任务包；ENABLED_TEMPLATES未启用explorer；派发时冻结 |
| R11 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/runtime/assembly.py` | 1-260,285-500 | 模型并发配置可被抬高；装配未传tokenizer/context_policy/embedding |
| R12 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/runtime/model_router.py` | 1-165 | 实际模型profile/路由；profile暂未携带Context工作配置 |
| R13 | `DennyWanye/simple-harness-sdk` / `src/simple_harness/agents/runtime.py` | 322-400 | 召回数据构造成SYSTEM消息；需避免将历史升格为指令 |
| R14 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/scheduling/allocator.py` | 1-300 | 已有候选数量、规则优先级、老化与背压；不是空白调度器 |
| R15 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/runtime/actions.py` | 1-175 | 审批后的动作交接与UNKNOWN核对；lookup语义要求强 |
| R16 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/runtime/connectors.py` | 1-185 | Connector要求权威lookup；TestConfigService是测试服务，不是生产许可 |
| R17 | `DennyWanye/simple-harness-sdk` / `src/agent_orchestrator/contracts/models.py` | 1-260 | 六个合同和固定验证层名称 |
| R18 | `DennyWanye/simple-harness-sdk` / `INTEGRATION_STATUS.md` | 1-end | 仍为历史0.6.2集成说明，不能当作本次当前部署证据 |
| R19 | `DennyWanye/simple-harness-service-sdk` / `src/simple_harness_service/transports/unix.py` | 180-275 | 已有短RPC只包含health/start/continue/get/cancel |
| R20 | `DennyWanye/simple-harness-service-sdk` / `src/simple_harness_service/compatibility-bom.json` | 1-end | Service0.3.12固定Harness0.6.2与Memory0.5.2 |

## 附录B：理论资料实际文件

- `01_agent_orchestration_basics.md`，SHA-256 `6bbdf2293d19d1f727e1d9ec627c44a42b5735023217023b75162c8ff9b0043c`。
- `02_task_tree_dag_proof_dag.md`，SHA-256 `be821252166de9b34bc0378dd4fcec4aa17270bccc0d9ba0cdcf101d85b5220c`。
- `03_agent_search.md`，SHA-256 `8cdd1e8fead0936a7683c86c38326f9115b318b11d071cf7056dfeed3285fec7`。
- `04_tree_search_blackboard_memory.md`，SHA-256 `5392e4aed14c76ed2fd88955019624ba2837b4ddbd8eb929e030005c0e7831e4`。
- `05_allocator_scheduler.md`，SHA-256 `440b66ab323638406f16bd498718d5c9846ca67fb74772bc900bbdd6ec4aa98c`。
- `06_agent_lifecycle.md`，SHA-256 `f55517cf586a8fea77b3551455faac3de7c1729d325b59b8dc1b7fe5932920e4`。
- `07_control_roles.md`，SHA-256 `98865ecfe3de64fb2ca20ddaf5614f556404a978f4d0c325dec61469496f571b`。
- `08_workflow_control_loop.md`，SHA-256 `5e737938afa6891f26c383d711be02c438df575c6c3c31b2c27303d28895d846`。
- `09_state_event_durable_execution.md`，SHA-256 `01e9929efc821ae7af8f73bd8487834831dd1af8d3d86b8086476aa1b33c16d6`。
- `10_concurrency_conflict.md`，SHA-256 `4d86435a2fd407fdb28e14f5e8af85c5fcf0943d47cd7fae7fc98af6adde0519`。
- `11_budget_cost_backpressure.md`，SHA-256 `d191fa5e23ec5e6c8540b2ce16e521fc50737fec5d86887094dcbc5f82ea1825`。
- `12_observability_tracing_evaluation.md`，SHA-256 `c117968c53f307beb6a5fee8ec12407c8aa60e4ba7e008a8d139160c8ac2255b`。
- `13_remaining_knowledge_map.md`，SHA-256 `229f6317fec5a5fef4cc94282ed84734dd4f549ab2cf9287bb1475ec9e19813f`。

## 附录C：交付状态

本轮生成并检查的是计划与机器可读清单。48项Phase3验收全部标记NOT_RUN；未执行真实仓库测试、Provider请求、UI、迁移或生产动作。Host当前源码读取仍受访问条件限制，其具体文件映射和安装验收是P3.1的必要门槛。
