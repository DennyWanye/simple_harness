# 人类锚定伴生智能体：简化架构图

> 日期：2026-07-24  
> 提交快照：`2f5436efe7ce92638b84332c99c7a9714904dd9a`；共享工作树仍有在途接线  
> 说明：这是计划目标图，不代表功能已经实现。

## 一句话

Companion 负责“为什么要成长、是否值得成长”，现有 Capability 平台负责“哪个版本真正
安装和生效”，Harness 只负责“把这一次任务可靠执行完”。

## 当前代码

```mermaid
flowchart LR
    U["主消息线程"] --> P["ProductTurnPreparer"]
    P --> K["RunKernel"]
    K --> D["ReAct / Workflow Driver"]
    D --> E["Effect / Execution UoW"]
    E --> T["ToolRegistry V2"]

    CP["CapabilityPlatform + CapabilityStore<br/>execution DB 与 main 初始化已合入；runtime 接线仍在途"]
    CP --> M["CapabilityPackManager"]
    M --> S[("CapabilityStore<br/>版本与 binding")]
    CP --> T
    CP --> H["CapabilityHub<br/>发现投影"]

    L["旧 Codifier / SkillLoader 脚本旁路"] -. "仍需迁移删除" .-> T
```

当前最重要的缺口不是再加一个 Driver，而是：

1. Capability 平台的 Store 与 main 初始化已进入 master，但 OS runtime worktree、
   UI/Godot 分支 HEAD 差异和主消息组合根脏改动尚未形成稳定绿色基线；
2. 没有长期成长的证据、评测和决策 owner；
3. Capability user scope 仍以 `"default"` 为默认，不能隔离对应人类的 generation；
4. Skill instruction、Personal Workflow 和 Capability binding 尚未形成一个闭环；
5. 旧 Codifier/Loader 仍可能绕过统一版本、评测和 ToolRegistry。

## 目标架构

```mermaid
flowchart TB
    U["对应的人类<br/>主消息线程"] --> P["ProductTurnPreparer<br/>偏好 + 单次冻结 envelope"]
    P --> K["RunKernel"]
    K --> D["ReAct / Workflow Driver"]
    D --> E["Effect / UoW"]
    E --> T["ToolRegistry<br/>唯一 executable truth"]

    E -->|"客观结果"| C[("companion.db<br/>证据 / 候选包 / 评测 / 决策")]
    U -->|"纠正 / 确认 / 遗忘"| C
    C --> R["CompanionRuntime<br/>阈值 / 空闲 / 预算"]
    R --> GR["zero-tool GrowthReflector"]
    U -->|"显式创建 / 修改 Skill、Workflow"| GBA["GrowthBuildAdmissionRouter"]
    GR --> SP["StructuredGrowthProposalV1"]
    GBA --> SP
    SP --> CB[("candidate_builds")]
    CB --> BC["CompanionCandidateBuildCoordinator<br/>签 host-only permit"]
    BC --> CHILD["固定 workflow.capability_build child"]
    CHILD --> REC["host-issued durable<br/>CandidateDraftReceiptV1"]
    REC --> PACK["immutable exact candidate pack"]
    PACK --> V["独立 Evaluator + RiskPolicy"]
    V --> C

    C -->|"低风险自动请求<br/>高风险确认后请求"| A["Activation Dispatcher"]
    A -->|"install/update/rollback 统一 runtime set<br/>逐实例短 lease ACK，长 health 锁外等待"| CP["CapabilityPlatform lifecycle façade"]
    CP -->|"短临界 publish_lock → CatalogGate<br/>+ publish-intent saga"| M["CapabilityPackManager"]
    M --> S[("CapabilityStore<br/>唯一 version / binding 权威")]
    M --> T
    S --> H["CapabilityPlatform + Hub<br/>同一锁内重验 ToolSet 并只组合一次"]
    T --> H
    H -->|"PreparedRunCatalogLease<br/>Run stamp + ToolSet + provisional pin"| P
    M -->|"Operation Receipt"| A
    A -->|"对账成功才标已生效"| C

    C --> N["Notification Outbox"]
    N --> DB["SessionDB<br/>context_visibility=exclude"]
    DB --> U
    U -->|"查看详情"| Q["CompanionDetailQueryPort<br/>owner fence + Vc/Platform token vector + 分页"]
    Q -->|"redacted lineage"| C
    Q -->|"typed current binding read"| CP
```

详情页的 Platform token 不是单个 user token，而是排序后的依赖 vector。authority row
永不物理删除，create/delete/recreate 都单调增加 lifecycle version；只有从未出现的 key
才是 `exists=false/version=0`。比如
`remove_override` 同时依赖“user override 已移除”和“builtin fallback 仍是原版本”，所以
两行都必须进入 vector；任一行在分页中途变化都返回 `detail_changed`。

## 四个唯一权威

| 问题 | 唯一回答者 |
|---|---|
| 为什么要改变、证据是什么、是否通过评测 | `companion.db` |
| 哪个能力版本已安装、当前 binding 是什么 | `CapabilityStore` |
| 哪些工具此刻真的可执行 | `ToolRegistry` |
| 本 Run 能发现和冻结哪些能力 | `CapabilityHub` |

## 隐式偏好什么时候变成长期偏好

```mermaid
flowchart LR
    E1["独立场景 1"] --> R["recent"]
    E2["独立场景 2"] --> R
    R -->|"有效 distinct context = 2<br/>不晋升"| R
    E3["独立场景 3"] --> R
    R -->|"默认阈值 = 3<br/>无 conflict / decay / tombstone"| L["long-term"]
    DUP["重复同 context<br/>衰减 / 冲突 / 已遗忘"] -. "不计数" .-> R
```

出厂配置键是
`preference_promotion_independent_context_threshold=3`（合法范围 2..10）；S-2 直接使用该
默认值，不依赖测试时临时改配置。

## 三种能力身份为什么必须分开

```mermaid
flowchart LR
    O["OwnerBindingSetStamp<br/>一个 owner/scope 已提交的 Store bindings"] --> OM["Manager receipt / 激活回滚<br/>CatalogGate / 详情 / owner 恢复"]
    R["RunCatalogContentStamp<br/>本 Run 的 run/project/user/builtin 选择<br/>+ raw builtin/plugin/MCP + exact build hash"] --> RS["RunStart / PreparedToolSet<br/>snapshot lease / child / refresh"]
    R --> P["ProcessCatalogStamp<br/>process id + 本进程 Registry/Skill/MCP revision"]
    P --> PM["只证明这次进程内物化"]

    O -. "Hub 合成时是输入之一" .-> R
```

简单说：

- `OwnerBindingSetStamp` 回答“这个人的这一层 binding 已提交成什么样”；
- `RunCatalogContentStamp` 回答“这一次任务最终到底拿到了哪些能力”；
- `ProcessCatalogStamp` 回答“这份完整任务目录在当前进程里是怎么物化的”。

raw host 工具必须带真正的 `execution_build_identity`：core 来自嵌入的构建/source manifest，
plugin 来自安装包 digest，MCP 来自 adapter/server artifact 与配置 hash。只有名字和 schema
相同、但 handler 代码已变时，旧 Run 必须拒绝恢复，不能猜“应该还是同一个工具”。

## 一次冻结如何避免半旧半新

```mermaid
flowchart TD
    A["Preparer 构造候选 PreparedToolSet"] --> B["一次 prepare_run_catalog_lease<br/>publish_lock → CatalogGate"]
    B --> C["逐项重验 spec / adapter / schema / build identity"]
    C -->|"任一已变化"| X["整次 retry / fail<br/>不创建 Run"]
    C -->|"完全一致"| D["只组合一次 Hub catalog"]
    D --> E["写 RunCatalogContentStamp + lease intent<br/>安装 provisional process pin"]
    E --> F["RunCreate + RunStart + lease adopt<br/>同一 DB transaction"]
    F -->|"确定回滚"| G["撤 intent / receipt / pin"]
    F -->|"commit unknown"| H["ReadyGate 关闭<br/>查 exact durable facts"]
    F -->|"确定提交"| I["AfterStartCommitHandshake"]
    I --> J["验证或恢复 exact pin"]
    J --> K["打开 ReadyGate"]
    K --> L["Driver 才能启动"]
    L --> M["success / failure / cancel"]
    M --> RI["terminal UoW<br/>Run terminal + delivery intent + release receipt"]
    RI --> N["commit 后撤本 Run pin<br/>最后 member 再清共享 runtime"]
    RI --> TF["TerminalDeliveryFence<br/>冻结 owner / dependency / epoch + exact receipt"]
    TF -->|"正常"| OUT["物理投递 message / history"]
    TF -->|"profile 已切换"| PEND["保留在原 profile inbox pending"]
    TF -->|"forget / delete"| DROP["tombstone / discard"]
    Z["只关闭消息页 / session"] -. "不是 terminal，不释放" .-> L
```

root Run、child Run 和 capability refresh 都走这一个形状。child 必须先拥有自己的 pin；
refresh 必须先在 Companion 追加 immutable `snapshot_generation`，让 execution refresh record
引用它，after-commit 绑定为 current 并打开 continuation Gate；新 snapshot ready 后才释放旧
pin。遗忘通过 all-generation root 撤销每一代，不能只查 Run 初始快照。

## 一次模型调用如何可靠落账

```mermaid
flowchart LR
    K["Kernel 冻结 RunStartSnapshot"] --> W["ExecutionWriteLane<br/>串行长寿命连接；FULL durability"]
    W --> C["provider claim 提交"]
    C --> S["start：把请求交给冻结 transport<br/>物理 dispatch 边界"]
    S --> A["有界 dispatch-start ack<br/>随后释放短 lease"]
    A --> P["completion：等待长响应"]
    P --> O["immutable outcome 提交"]
    O --> F{"返回类型"}
    F -->|"Final"| T["Kernel 既有 terminal UoW"]
    F -->|"ToolBatch(1/N)"| B["真实 boundary / goal / turn-fence /<br/>batch-attempt / N×prepared"]
```

`Final` 和 `ToolBatch` 的提交图不同，不能再概括成“统一四事务”。Writer connection 复用只
减少连接开销，不合并 crash boundary，也不降低 `synchronous=FULL`。

## 一次低风险成长的流程

```mermaid
flowchart TB
    A["真实纠正 / 失败"] --> B["记录证据"]
    B --> GR["zero-tool GrowthReflector"]
    U["显式创建 / 修改请求"] --> GBA["GrowthBuildAdmissionRouter"]
    GR --> SP["StructuredGrowthProposalV1"]
    GBA --> SP
    SP --> CB[("candidate_builds")]
    CB --> BC["host permit"]
    BC --> CHILD["固定 Builder child"]
    CHILD --> REC["host-issued CandidateDraftReceipt"]
    REC --> C["immutable exact candidate pack"]
    C --> FZ["一次冻结 case input + read-tool fixture<br/>old / candidate 共用同一 hash"]
    FZ --> P["Execution Permit<br/>safe_auto 或 user_authorized"]
    P --> D["独立回放与回归"]
    D --> E{"RiskPolicy"}
    E -->|"失败/不确定"| F["保持旧 binding"]
    E -->|"高风险"| G["等待用户确认"]
    E -->|"低风险全绿"| H["Activation Request"]
    G -->|"确认"| H
    H --> I["Stage candidate / 冻结 rollback target<br/>不注册、不启动、不绑定"]
    I --> R["Prepare Runtime Set<br/>先持久化 operation + set hash + 实例数"]
    R --> J["每个实例一个短 Barrier<br/>重验 → claim → Job/session start-ACK"]
    J --> K["逐实例释放 Barrier<br/>只等待同一个 health response"]
    K --> K2["全部 health 通过后最终短 Barrier<br/>重验 epoch / set 完整性"]
    K2 --> L["统一锁序：publish_lock → CatalogGate writer<br/>关闭该 owner key"]
    L --> M["DB intent → pointer swap → DB binding + Manager receipt"]
    M --> N["释放 barrier；Companion 按 epoch 对账"]
    N --> GUARD["low-risk 自动激活同事务建 guard<br/>24h / threshold=1 / exact rollback plan"]
    GUARD --> O["下一 Run 使用新版本"]
    GUARD -->|"host critical incident"| LOCK["exclusive Barrier → publish_lock<br/>CatalogGate writer/close → 同锁核对 binding"]
    LOCK --> Q["同事务 quarantine + 唯一 rollback request<br/>Gate 保持 closed 到 receipt 对账"]
    Q -->|"update"| Q1["same-owner previous stable"]
    Q -->|"builtin override"| Q2["remove_override → exact builtin"]
    Q -->|"genesis"| Q3["disable"]
    BAD["provider/network/模型质量/普通 tool error"] -. "不能触发" .-> GUARD
    NEWB["后续 binding-changing receipt"] -->|"同事务先 supersede 旧 guard"| GUARD2["新 low-risk auto 才建新 guard<br/>人工 activation 不建"]
```

候选包在进入评测前还必须经过统一资源/路径门：流式限制文件数、大小、压缩比和路径长度；
Windows 上拒绝 drive/UNC、ADS、尾随点/空格、设备名、link/reparse 与 Unicode/大小写别名，
逐 entry 落盘前后都重验 containment。

代码/hook 评测的每个 case 也复用同一原则：

```mermaid
flowchart LR
    C["领取一个 evaluation case"] --> F["短 Barrier<br/>重验候选/Execution Permit/case epoch"]
    F --> A["durable launch claim<br/>Job/runtime start-ACK"]
    A --> W["释放 Barrier<br/>等待长结果"]
    W --> S["完成前再验 epoch"]
    S -->|"仍有效"| R["提交 case result"]
    S -->|"已遗忘/失效"| X["丢弃结果 + 精确清理<br/>不得领取下一 case"]
```

代码类候选有两次不同的确认：

```mermaid
flowchart LR
    E["确认 1：允许评测 exact code<br/>无 OS 沙箱"] --> T["只运行一次评测"]
    T -->|"评测通过"| W["仍未激活<br/>activation runtime 启动数=0"]
    W --> A["确认 2：允许激活 exact code<br/>以后可被调用；Job 只管生命周期"]
    A --> H["才允许 runtime prepare / health / publish"]
    H --> X["以后若要发送/删除/付费<br/>仍需该次 action confirmation"]
```

评测 launch row 直接以 `claimed` 持久化，不保留会卡死的 durable `prepared` 中间态：
claim 事务提交前崩溃就是“没有 launch、没有启动”；提交后崩溃则按唯一 claimed row 恢复。

## Skill 与 Workflow 如何保持简单

```mermaid
flowchart TD
    PACK["一个不可变 Capability Pack"]
    PACK --> S["SKILL.md<br/>只教模型怎么做"]
    PACK --> T["function / MCP / local-runtime Tool<br/>真正执行动作"]
    PACK --> W["Personal Workflow JSON<br/>只保存声明式 DAG"]
    S --> A["manifest allowed-tools<br/>必须等于 frontmatter"]
    A --> P["PreparedSkillInvocationScopeV1<br/>冻结 exact spec / schema / build / effect refs"]
    P --> D["Driver 可见 schema<br/>base ToolSet ∩ active scopes"]
    P --> X["ToolExecutor 实际执行<br/>base ToolSet ∩ active scopes"]
    T --> R["ToolRegistry → Effect/UoW"]
    R --> D
    R --> X
    W --> I["固定 workflow.personal_v1 解释器"]
```

- `SKILL.md` 不再直接执行 `script.py`。
- Personal Workflow 不生成 Python，也不动态注册 Driver/Profile。
- 同一个 Skill 始终使用同一个 `pack_id`；用户看到一个 Skill，内部保存不可变版本。
- 当前 `summarize-day/recall-yesterday` 虽写了 `memory_recall`，但仓库还没有这个 handler。
  目标会新增真实 production ToolSpec：模型只传 `query/limit`，owner/session/as-of 来自 Run
  冻结范围；它走 `recall_readonly`，SQL 写入为 0，不触发旧 Retriever 的 salience/touch。
- 评测不会用 production memory：`EvaluationReadToolAdapterV1` 把同一 production spec 映射
  到 old/candidate 共用的 frozen fixture/hash，live SessionDB/Retriever 调用为 0。
- Profile 切换会切换 owner 可见的 Registry/Loader/MCP 集合；旧 Run 依靠 frozen snapshot
  和 runtime lease 完成，新 Run 只看当前 owner。
- 遗忘不等待卡住的网络调用：只在 dispatch 前后短暂检查 revocation epoch；遗忘提交后，
  迟到结果会被丢弃。
- 冷启动先读取 dormant descriptor；profile 与 quarantine 对账完成前，不注册旧 ToolSpec、
  不挂载 Skill root，也不启动 MCP/local runtime。

## Reminder V2 如何替换旧内存工具

```mermaid
flowchart LR
    OLD["legacy.list_reminders.v1<br/>进程内 list"] -->|"Task 13 durable cutover<br/>retire，不保留 alias"| NEW["Companion phase"]
    NEW --> C["core.reminder_create.v2"]
    NEW --> L["core.reminder_list.v2"]
    NEW --> X["core.reminder_cancel.v2"]
    C --> R[("reminders + occurrences")]
    X --> R
    C --> MR[("reminder_mutation_receipts<br/>owner + stable effect id")]
    X --> MR
    MR -->|"Companion 已提交、execution 未 settle 后重启"| RE["只读原 receipt<br/>补 settle，不再修改"]
    L --> R
```

source/effect/build manifest 用 authority phase 同时验证 legacy production 与 companion test
组合；切换前 create/cancel 不可见，切换后旧 list 名不可见。create reminder id 由 owner +
stable effect id 确定，cancel 重放先查 mutation receipt，所以跨库崩溃不会重复创建、取消或
误增 schedule version。

## 任一 Companion Run 想执行外部动作时

```mermaid
flowchart LR
    FG["点击消息打开的前台主线程"] --> POL["IrreversibleEffectPolicy<br/>只信 host effect manifest"]
    BG["delegated background Run"] --> POL
    POL --> A["Host 冻结 PreparedToolSet<br/>高风险 / unknown 进入 confirm_only"]
    RE["reflection / evaluation"] -->|"高风险工具直接不可见"| ZERO["可见数 = 0"]
    A --> B["模型准备 tool call"]
    B --> C["Driver 验 snapshot<br/>强制 OR authorization index"]
    C --> C2["原 call/effect 创建 DecisionOpen"]
    C2 --> D["后台 Run 等待<br/>Companion job 释放 lease"]
    D --> E["message-panel 确认卡<br/>window-scoped action credential"]
    E -->|"拒绝 / 过期 / stale"| F["同一 call 结束<br/>真实 effect = 0"]
    E -->|"只发 decision_id + allow"| G0["ActionDecisionService<br/>owner-fence 后重建原后台 session"]
    G0 --> G["恢复原 durable boundary"]
    G --> H["execution grant<br/>一次性、精确 hash"]
    H --> I["统一短锁序<br/>RevocationBarrier → publish_lock → Gate read<br/>复验 owner / binding"]
    I --> J["claim_tool_call<br/>原子消费 grant + claim effect"]
    J --> K["begin → start → 有界 ack<br/>先持久化 handoff_state / receipt<br/>再释放短读锁"]
    K --> L["completion 等长响应<br/>返回前再验 epoch"]
    K -->|"ACK 后遗忘/撤销"| M["status=unknown<br/>started_may_complete<br/>inflight_effect_may_complete"]
    M --> R["best-effort cancel / query<br/>只写 reconciled receipt"]
    L -->|"撤销后迟到"| S["suppressed outcome hash<br/>不标成功 / 不继续"]

    X["auto_mode / 历史授权 / delegated grant"] -. "不能放行 confirm_only" .-> C2
    Y["main / code panel credential"] -. "不能提交成长卡 action" .-> E
```

关键点：确认不是“再创建一个发送任务”，而是恢复刚才暂停的同一个调用。这样重启、重复
点击或网络异常都只能落到同一个 effect receipt。只有 durable NotStarted proof 才能证明物理
dispatch 为 0；claim 后崩溃且没有该 proof 时保守按 may-complete。ACK 之后已交给外部
transport 的动作可能仍在远端完成，DeskPet 在既有 status=`unknown` 上另存
`handoff_state=started_may_complete` 与
`completion_disposition=inflight_effect_may_complete`，只阻止新的 dispatch/后续 chained
effect，丢弃迟到 completion；cancel/reconcile 只形成审计 receipt，不能假装已撤回或把原
effect 改回成功。

## Shared secret 为什么不能冒充当前窗口

```mermaid
flowchart LR
    WS["一般 shared-secret WS"] --> CH["challenged lease<br/>TTL + 每进程上限"]
    FLOOD["伪连接洪泛"] -->|"只逐出最旧 challenged"| CH
    RUST["Rust 真实 WebviewWindow label<br/>Ed25519 private key 只在 Rust"] --> CAN["control-command-canonical-v1<br/>Rust / TS / Python 同一 binary vectors"]
    CAN --> TX["单一 Companion transaction"]
    CH --> TX
    TX --> ACT["challenged → active"]
    TX --> REV["撤同一真实 label/scope 的旧 active"]
    TX --> SEQ["消费 seq/nonce + 写 command receipt"]
    MAIN["main"] -->|"只能 identity_bind"| RUST
    MSG["message-panel"] -->|"只能 companion_action"| RUST
```

首个有效 signed command 才会 promotion；伪造 label、旧 backend key、跨 scope/连接重放都
不能占 active lease，也不能改变 IdentityReady。

## 忘记一条证据时，共享版本怎么处理

```mermaid
flowchart TD
    F["forget attempt A"] --> Q{"同版本还有独立 B support？"}
    Q -->|"B 已 passed + decided + active<br/>receipt 匹配 current binding"| KEEP["只撤 A support<br/>version 继续服务"]
    Q -->|"B 仅 eligible"| ISO["立即 quarantine + rollback<br/>B 可继续评测但不能直接激活"]
    ISO --> NEW["新 decision<br/>expected quarantine generation + support hash"]
    NEW --> REL["短 exclusive 关 Gate<br/>逐实例短 shared ACK / 锁外 health<br/>最终短 exclusive receipt + released"]
    Q -->|"没有独立 source"| STOP["永久不可 release<br/>回滚或禁用"]
```

“包的 bytes 还能保留”不等于“这个版本还能服务”；只有独立、已对账的 support 才能支撑
active version。

## 真人 E2E 的启动与清理

```mermaid
flowchart TD
    S["ScenarioId<br/>稳定 user-data"] --> L1["LaunchId-1<br/>稳定 helper + 专属 Job Object"]
    L1 --> M1["动态 manifest<br/>初始 PID + late descendants + listener owners"]
    M1 -->|"Status 动态追加 → Stop<br/>graceful + KILL_ON_JOB_CLOSE"| C1["多维 scope survivor=0<br/>cleanup-result"]
    C1 --> L2["LaunchId-2<br/>同 Scenario 重启"]
    L2 -->|"同样进入独立 Job"| C2["PID/Job 全消失<br/>端口释放 + 内存记录"]
```

`ScenarioId` 用来验证“重启后状态仍在”，`LaunchId` 只代表一次启动。每次启动都必须有新
LaunchId；`Status` 会把 ready 后才出现的 embedder/MCP/local-runtime/WebView 等后代按
Job/PID/create-time/parent/command-line 复核后追加。`Stop` 关闭专属 Job，并按 repo、
user-data、临时配置和端口再次审计，绝不按进程名广杀。
