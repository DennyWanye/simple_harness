# S6 Task 1/2 — 唯一主对话实现准备

状态：P1 最小连接复用已实现；P2 无scope admission已实现，runtime闭环待共享terminal owner协调。未接UI、未启动App/真实Provider。实测与交叉点见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。

工作树 `/Users/denny/projects/simple_harness-s6-primary-preparation`，分支 `feat/human-memory-s6-primary-preparation`，固定 base `4eb1eb7c7fb27f4f40e4e62621e820acfef9693a`。main 此后推进不自动纳入本分析；实施前只检查相关文件 drift，禁止把 main 的 dirty changes 混入。

交付索引：[公共契约](CONTRACTS.md) · [正负验收与V0 lineage](ACCEPTANCE.md) · [41份源码/原计划SHA-256](SOURCE-INDEX.json)。

## 1. 权威与范围

原计划位于 sibling Memory SDK 的 `plans/2026-08-29-human-memory-digital-twin/`：
- `slices/S6-ui-and-program-verification.md` Task 1：Workbench 唯一聊天入口、stable primary、Session catalog/new/switch/rename/delete/project-group 在 production 不可达。
- Task 2：active/recent TaskScope、permission-first search→exact open→bounded views、binding Manual 明确确认 / Auto 来源只读。
- `slices/V0-verification-and-spikes.md` Task 2、`verification/v0-closure.md`：沿冻结 Session lineage，不删除历史 testcase，不复活旧 active oracle；TC-GS-03～10 保留。
- 父 `acceptance.md` HM-AC-1/3/4/6/7/8 与 Task 5–8 的全部 program 门仍有效。Task 1/2 完成不等于 S6/program 完成。
- 本文件的具体新 DTO/文件名称、分页上限是实现选择，不冒充既有 wire 或修改 sealed testcase；新增聚焦检查作为实现回归，不增删 V0 required scenario。

本次只准备 Task 1/2 及其必需 transport/auth/send/projection 支撑。Memory graph、sealed audit、Prospective/Procedure调度、S5c语义/质量由原 Task 3/4 或 Dirac 另线负责。不能以 S5c 尚在准备为由让普通聊天创建假 TaskScope，也不能用 legacy chat 暂代 HUMAN primary。

## 2. 现状：可复用与确切缺口

| 编号 | 当前代码事实 | 实施结论 |
|---|---|---|
| G1 | `HumanMemoryProgramStore.initialize_subject` 对 subject 派生 stable UUID，并以 writable primary 唯一约束初始化；service `open_primary` 返回 primary_ref/receipt_ref/hash | 直接复用。UI不生成primary、不开新Session、不将primary写入legacy sessions表 |
| G2 | `main.py` control连接读取query `session_id`、调用`assert_not_primary_authority`，注册/替换以session为key的socket | 不能把activeSid换成primary_ref后继续原socket。复用default control transport的本连接verified bind，不移除旧primary fence |
| G3 | 当前`human_memory_request`走shared-secret control，再用`local_owner_auth()`返回固定subject/principal/authority字符串；它不在`PRIVILEGED_CONTROL_KINDS`，没有连接级primary附着、窗口command签名链 | 固定单机subject本身不是多租户bug；缺的是把“已验证Host用户”的声明绑定当前socket/窗口/epoch，并独立管理reconnect。必须在UI启用前闭合，见CONTRACTS.md |
| G4 | `QueueTurnRequest.scope_ref`必填；`enqueue_turn`先`_assert_owned_scope`；底层queue允许task_scope_id=None和binding_revision=0；runtime/context/tool ports却拒绝无scope、并总造HOST_INITIAL ROUTED_TASK | 普通无任务聊天不能直接用现public queue。必须加primary admission与UNROUTED/standalone production分支，不创建占位scope，不放宽project effect |
| G5 | `queue.control`在处理时取current_snapshot，没有用户看见的expected Run/generation | 延迟“停止A”可能作用到刚启动B。新增exact expected_run_ref/generation，跨run stale拒绝；重连不盲重放control |
| G6 | `primary.open`只有identity；无primary消息/queue/active/recent分页和订阅；`list_evidence`不是chat DTO，只是limit1000/max10000原始证据读取 | 必须建普通消息公共投影与cursor，不能把evidence全表直接送UI |
| G7 | foreground execution_session_id是Run派生ID；`main._foreground_conversation_entrypoint`为Memory身份FK创建隐藏session；现`HarnessPublicReadService`按legacy messages/session/root查询 | UI必须靠Host验证host_run↔sdk_run↔execution_session↔primary绑定。隐藏FK session不得出现在catalog/导航；不删除它、不让UI选它，不把primary塞入该FK |
| G8 | App持有activeSid与switch回调；Sidebar直接import SessionList；ChatView独发legacy四类hydration；InputBar空态new_session:true，其余chat_v2/slash；controlWs处理session_switched且离线可排队任意其他帧 | 移除生产CRUD依赖链、新bootstrap/store/send/hydration，同时保护设置/Skill/Artifact/voice入口，不只是隐藏按钮 |
| G9 | task_scope.open_exact返回ResumePackage/source/hash/drift，未改变active route；search是candidate；没有公开active/recent list；live_probe可由request提供 | inspect与resume action分离；live filesystem drift必须由Host真实probe生成，不能采用UI自报为权威 |
| G10 | v41 queue已有resume_paused底层方法，但public control及runtime没有完整pause-resume入口 | “继续TaskScope”实现为exact route intent+新queued turn；不得把它宣传为恢复旧PAUSED Run。若保留暂停按钮则必须明确PAUSED行为，不伪造resume成功 |

## 3. 设计决策与实现顺序

### P0 — 固定边界和输入（无业务改动）

读 `SOURCE-INDEX.json` 的基线SHA；核对跨仓candidate/依赖。保留所有原TC文件、V0 hash引用和当前revision；当前TC-HM-X01内部分早期candidate pin是历史冻结条目，不得改成任意最新版本来通过，应沿既有candidate admission另记exact本轮包。把G1～G10映射到下述PR级子块，每块测试通过后再接UI。

### P1 — 复用现control已验证连接（主审修订）

保留 `/ws/control`，使用default transport上的既有 `companion_profile_bind`。该入口已经核验Rust签名、main窗口、identity_bind scope、challenge/connection/control epoch/request_seq，并比对Host `LocalAuthSnapshotProvider`。因此不新增socket、账户、Rust/TS scope或每读重签。

新增Host `memory/control_binding.py`只记本连接bind成功后的FrozenOwnerIdentity和connection ID。每条human_memory_request admission重验现IdentityReadyGate的owner/binding epoch与durable active lease的process/connection/control epoch；同owner重连也会撤销旧lease，因此只检查全局ready不足。未bind、另一连接只广播ready、unbind、rechallenge、旧lease都拒绝。原签名bind的防重放仍由既有primitive负责；普通消息重试由delivery_key+intent hash负责，不增加逐读取nonce。

认证通过继续使用原单本地owner namespace与稳定authority_ref，避免重连改变证据幂等hash。旧primary-ID fence、项目effect exact授权和原body authority字段拒绝全部保留。future UI在同一已绑定socket发送HUMAN请求；不能从仅收到identity ready广播的另一个socket继承权限。本轮不接UI。

### P2 — 普通聊天admission与execution完整性（Task1硬前置）

本轮最小复用现service `enqueue_turn` / `queue.enqueue`，将scope_ref设为可省略/None；显式scope仍严格校验，空串/非法类型不视为无scope。未来附件/resume DTO在该入口增量扩展，当前没有另造primary.enqueue alias。新UI不用“先create task再enqueue”。复用v41 queue的None scope和已有append→queue幂等；同delivery_key相同内容返回原turn，改变文本/attachments/route intent应冲突，不静默丢内容。

`foreground_runtime_ports.py`新增 PrimaryForegroundContextPort，按candidate是否有Host批准scope选择context port；无scope从primary durable已提交上下文/已有context assembly取得bounded snapshot，不能读legacy session history或用空history假装完成。`foreground_runtime.py`分开两条路径：已有授权task沿当前HOST_INITIAL A；普通turn以initial_route=None/UNROUTED启动，交现context_route adjudication收敛五种route。只取消无scope分支的错误假设，不取消task分支的binding exact校验。

同时审 `foreground_runtime_ports.ProductForegroundToolPort`、`foreground_queue.record_sdk_terminal`、`SqliteSdkTerminalObserver`、`evidence_ingress.py` 对scope必需的假设：standalone保留永久primary evidence、host/sdk Run terminal与outbox lineage，跳过“仅TaskScope语义closure”，不能伪造binding/task ID；route在Run内真正创建/恢复task后按receipt绑定执行effect/closure。v41允许None不等于其它链路已完成。需要新增持久化的**派生**primary context/read projection使用新迁移文件（实施时从当前末号分配），不改已封口v41/v44/v46 migration checksum。

这是S6 Task1必需依赖，不由UI绕过。Dirac S5c可提供同等public primary admission/observation接口；若另线已承接此块，复用其commit并只保留一个owner；当前尚无该交付证据，默认本方案P2负责最小接线，不假设S5c已实现，不修改其Prospective/Procedure调度。

### P3 — primary公共消息投影、队列与恢复

新增 `backend/deskpet/memory/primary_projection.py` 与对应derived checkpoint/store。事实链：primary user evidence + foreground_turns + durable host/sdk binding + SDK allowlisted public messages/effects/terminal → ordinary privacy projector → primary DTO。从`composition.py`新增**typed public message分页reader**，不能复用`read_closure_run_facts`的`str(ContentBlock)`作为聊天正文。

保留现ProviderPublicProjectorV1/ToolPublicProjectorV1策略与display exclusion。先写durable投影再通知；通知只作为水位提示，refresh/reconnect可完全补齐。跨DB以可重放receipt/high-watermark处理，不承诺SQLite跨库原子事务；delivered revision不能超过已持久可读projection。详情refs、反压和cursor见CONTRACTS.md。

provider内部session、raw reasoning、private metadata、memory sealed evidence均不进入普通投影。project/task refs只显示current auth可读集合；授权变化或suppression后旧cursor/cache失效，不能从重播复活正文。message-ID按durable来源稳定；optimistic发送按delivery_key合并receipt，队列busy是QUEUED而不是发第二Run。

### P4 — TaskScope阅读与精确继续

新增`task_scope.list`（recent分页）、`primary.state`（active+queue）；复用search/open/view/evidence_groups/evidence_page。新`TaskScopePanel.tsx`包含active/recent/search区域；点击候选只进入inspect状态，取得source/hash receipt后显示README/STATUS、revision、roots、next/blocker、drift与page-in入口。

“继续此任务”产生`TaskResumeIntent`（exact scope_ref/source hash/open receipt）；Host校验ownership/current source后写入queued turn冻结的用户意图，随后沿现context_route RESUME_EXISTING执行，不由前端设置active_scope或切session。stale source重新读并显示变化，不自动换成相似候选。只读inspect不得写scope status/Run。Manual root picker→propose→显示challenge→decide；Auto provenance仅只读。当前公开`binding.append`绝不作为前端确认快捷方式。

### P5 — Workbench与输入迁移

新增 `types/memoryProgram.ts`、`chat/primaryProtocol.ts`、`stores/primaryConversationStore.ts`、`hooks/usePrimaryConversation.ts`。Store分清auth/bootstrap/disconnected/ready、identity、projection、queue、draft；不再拥有session CRUD。App主流程以primary receipt完成bootstrap，非空或失败只显示明确加载/错误/重试，不fallback new_session。

改App/WorkbenchShell/Sidebar：删sessionProps/onSwitchSid和SessionList import；chat导航改“对话”，TaskScopePanel属于该会话的任务阅读区域；skills/artifacts/settings/更多入口保持。可保留历史SessionList文件供历史测试，但production import graph必须不可达。

改ChatView：以primary snapshot渲染MessageStreamPanel；移除sessionHydrationCommands、project catalog回灌、session_switched监听；只新协议可改变primary消息/active task。InputBar通过注入PrimaryTurnSender发请求、draft失败保留，移除HUMAN new_session/chat_v2/continuation selector。旧UI/store可保留给历史兼容测试，不能在HUMAN运行时分支继续发旧命令。禁止把primary_ref当legacy SessionStore key调用现CRUD/ensure_session。

### P6 — 聚焦验收→原V0 affected/full surface（实施阶段）

按ACCEPTANCE逐一执行。自动化使用隔离DB/精确新build；cold start/reload/断连/单主对话/两个TaskScope/exact resume与必需保留功能最终需真实UI/provider。已执行的P1/admission回归见IMPLEMENTATION；其它case仍NOT_RUN，无machine PASS。

实施完成按原S6 Task8由主执行者处理ARCHITECTURE/DoD及适用machine gate；用户已授权隔离worktree分块源码提交及ARCH状态；不改main、不merge。S6 Task3～8未交付不得称program完成。

## 4. 文件与owner清单

| 子块 | 现有文件 | 新文件（建议） |
|---|---|---|
| transport/auth | backend/main.py；只读复用既有companion/control_ingress与credential/store，Rust/TS不改 | backend/deskpet/memory/control_binding.py；未来UI协议adapter |
| primary APIs | backend/deskpet/memory/{human_memory_api.py,human_memory_service.py,human_memory_program.py}; backend/context.py的ServiceContext接线 | backend/deskpet/memory/primary_projection.py |
| standalone execution | backend/deskpet/execution/{foreground_queue.py,foreground_runtime.py,foreground_runtime_ports.py,evidence_ingress.py}; backend/deskpet/sdk_adapters/composition.py; main HUMAN composition | 新迁移及projection/context持久化单元（只追加，末号实施前分配） |
| unique UI | tauri-app/src/{App.tsx,components/WorkbenchShell.tsx,components/Sidebar.tsx,views/ChatView.tsx,code-panel/InputBar.tsx,code-panel/controlWs.ts,stores/sessionsStore.ts} | types/memoryProgram.ts; stores/primaryConversationStore.ts; hooks/usePrimaryConversation.ts; components/TaskScopePanel.tsx |
| task reads/binding | backend/deskpet/task_scope/{search.py,projections.py}; backend/deskpet/memory/human_memory_service.py; 现binding authority | TaskScopePanel内受控binding组件 |
| verification | backend/tests/memory/; backend/tests/execution/; frontend既有tests; testcase/index.md只读 | test_primary_control_binding.py; test_primary_turn_admission.py; test_primary_projection.py; test_primary_conversation_api.py; test_primary_foreground_runtime.py; primaryProtocol/primaryConversationStore/TaskScopePanel/Workbench primary tests |

## 5. 保留其他UI功能的边界（不能借cutover删功能）

| 功能 | Task1/2必须做的迁移 | 留在原后续任务的内容 |
|---|---|---|
| Settings/账号/onboarding/autostart/连接错误 | 保持原service auth、settings路由；无primary时设置仍可配Provider，准备好后重试bootstrap | 不重做设置产品 |
| Provider/model picker、ContextRing/Inspector | primary级默认模型仅影响未来turn；每Run继续冻结provider。读取Host public usage，不取legacy session估算；无值显示unavailable | S5c模型质量/召回指标不在UI虚构 |
| global Skill/Tool discovery/install | TC-GS-03～10仍active；目录全局、权限/激活/运行时snapshot不变。Chat slash转受信primary turn/既有typed Skill action，不调用旧Session新建 | 不重做Skill installer |
| 附件 | text附件必须纳入delivery intent hash、sanitizer与原size/type校验，再交context assembly；不静默丢attachments，不只发text | 新媒体类型另行范围 |
| 工具轨迹/permission popup/Run详情/Artifact | 按Host绑定的SDK Run ref转换现read DTO；授权精确decision/nonce/version，只有一个popup owner；Artifact view不因移除SessionList消失 | 新graph/sealed audit属于Task3/4 |
| Realtime通话 | 保留独立用户点击启动/挂断、voice Session归Service SDK，不能创建可见聊天Session或绕过primary Agent写路径 | 不把voice-only会话伪装成Agent turn；语音→Agent如不支持需明确显示 |
| Memory/Trace/反馈 | 原入口保留；普通privacy过滤与session依赖必须转primary或显式unavailable理由，不能渲染跨owner旧cache | MemoryGraph和purpose-bound审计按Task3/4完整实现，不提前声称PASS |
| TaskScope roots/ProjectInspector | 展示bound roots/revision、missing/drift，Manual追加沿现authority；移除旧“新建同项目会话/在项目中继续” | 不默选首个root、不提供绕过exact authority的搬目录操作 |
| 暂停/停止/取消 | 由exact host_run+generation控制；queued仍可查看。区分TaskScope继续与旧PAUSED Run恢复 | 不把底层resume_paused方法当成已完成UI/API产品 |

以上任一必需功能迁移无正式接口时，Task1 cutover不得假装完成；先补接口或明确阻断该验收项，不能改V0 oracle绕过。
