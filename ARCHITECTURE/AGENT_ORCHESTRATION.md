**最后更新：2026-09-13 07:09 CST — 已有源码原生运行全量回放。** 对16个已关闭原生运行库的全部17个Mission（含失败运行）及16个部署事件流执行只读回放，33个观察全部PASS、0差异/未覆盖/发现/读取错误，耗时0.532s；Provider调用、工具效果、Action、Context selection和事件计数前后相同。原始索引Host `.local-test-evidence/2026-09-13/p33-g/replay-all-native-v1.json`，SHA256 `376f19c68746cee73c8f186fb8b66e4aa099e3f2cb944718d2db34013b15cce5`。仅证明已存在原生运行的编排回放；执行库为清单枚举而非完整SDK执行回放，新后继UI与最终全测试观察仍待完成。P33/P34/P35整体OPEN。

**最后更新：2026-09-13 07:06 CST — 当前 doc7 正式仲裁受控链路。** 隔离 document-ui 入口新增 contextual arbitration 场景：两个实际 Worker 读取带环境限定的来源、提交有引用支持的非逐字世界候选，经正式冲突检测进入DISPUTED；实际Arbiter读取两来源及两产物、独立Critic读取仲裁报告，公开人工contextual裁决后冷重开保留原裁决且零新模型调用。14 PASS/25.55s（runner26.05s），包含真实测试路由、来源场景和隔离门；证据SDK `.local-test-evidence/2026-09-12/p33-g/g-host-doc7-route-v5.{json,log}`。首轮引用归属/世界主张混淆和后继状态断言失败保留。doc7新增Manager模板，历史doc6仍可读。此项是受控Provider软件证据，当前源码原生仲裁UI仍待验；P33/P34/P35整体OPEN，不打包/P36/推送。

**最后更新：2026-09-13 06:37 CST — 启动失败生命周期。** SDK enter失败会确定性关闭已装配runtime与Store，并保留原WorkspaceCleanupIncomplete和UNKNOWN占用；Host rebuild候选仅在enter及facade成功后发布，失败关闭候选且后续完整重试，不能直接run半初始化对象。SDK3 PASS/.32s；Host新控制4 PASS/.19s，连同生命周期/并发回归16 PASS/12.11s。首次失败恢复仍须deactivate/activate创建新service/client，不复用已关闭HTTP client；没有新增首次启动自动重试。命令与原始证据在SDK `.local-test-evidence/2026-09-12/p33-g/g-startup-failure-lifecycle-v1` / `g-host-lifecycle-affected-v2`。

**最后更新：2026-09-13 06:32 CST — 拒绝原因原生复验通过。** 源码SDK b0f8dd7 / Host d9d56461、快照v12，原生创建document-v6任务后先批准来源撤销，再批准原报告：界面明确显示结果最终未接受（FAIL/DONE）、stale_source、revoked及原版本，Claim仍UNDER_REVIEW；原layer和人审PASS历史保留。Mission mission-aa32b2525a480041，唯一Attempt，0 VERIFIED，Provider前后6/6/0，900已结算/0预留。证据 Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n6-active-revoke-v12/case-summary.json` SHA-256 `964c37865575bd3d2762247b6ff58c2cfda40082873a5e51bc4740f01f771efa`。PG78917已退出/无残留。该受控原生结果不代表真实模型质量或其他Phase3门槛关闭。

**最后更新：2026-09-13 06:26 CST — 最终拒绝原因投影。** Task结果现在直接显示持久的Result verdict/state；对于DONE/FAIL，仅从完全相同的Attempt/Task/Mission投影白名单拒绝原因与来源版本，保留先前layer通过记录和UNDER_REVIEW Claim，不从人工GRANTED推导结果接受。后端46 PASS/8.36s（SDK c8e2541隔离源码环境；旧backend venv产生1项预算usage兼容失败并保留），前端79 PASS/1.38s，typecheck PASS。新源码快照原生验证待完成；此前v11撤销runtime负例保持有效，新的显示修复尚非原生PASS。

**最后更新：2026-09-13 06:15 CST — FIRST 与 N6 新证据。** SDK b0f8dd7 / Host c6beb926 源码快照 v11 原生 N6 已验证：1/2 不确定条件允许交付且明确保留不确定性；2/3 不确定条件任务PASS但Mission为 insufficient_evidence；active-revoke先批准撤销来源、再批准原报告，原结果DONE/FAIL（stale_source）、产物REJECTED、0 VERIFIED、唯一Attempt、Provider仍6次，900已结算/0预留。三例为受控原生UI，不代表真实模型质量；分别证据 `source-ui-n6-half-v11`、`source-ui-n6-two-thirds-v11`、`source-ui-n6-active-revoke-v11` 位于 Host `.local-test-evidence/2026-09-13/p33-g/`。原生最终拒绝原因未在Task验证列表直接展示的问题仍在修复。FIRST定向56 PASS仅SDK工作树，尚未纳入该UI快照；文档冲突仲裁UI、O4全量及P34/P35整体仍OPEN。

**N6 显示修正 — 2026-09-13 05:44 CST：** 原生 n6-half 验证1/2不确定条件按原策略可交付，局限与INCONCLUSIVE均保存；UI“实际判定：满足”措辞会误导，已改为保留不确定性，Mission统一显示“通过交付判定”。新增UI反例先红，修复后26项通过；新快照原生复验待完成，不能把该措辞修正算原生PASS。

**原生边界与恢复检查点 — 2026-09-13 05:40 CST：** 新冻结 SDK c8e2541 / Host b7dc4c64 综合1127 PASS/75.26s。N1v9真模型正式交付及同源冷恢复已核对；新Host显示修复在受控原生来源指令用例验证。N4来源指令归属、错误逐字引用、矛盾证据三例原生UI符合预期，独立原始证据保存在Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n4-*-v10/`。实际OS SIGKILL后两库冷恢复2 PASS/9.59s：成功结果零重复Worker、独立Critic读产物；UNKNOWN保持原token/cost占用。仅覆盖该两边界，不覆盖完整Mission或P32逃逸进程恢复。FIRST新保护虽18PASS/0.91s，独立审查仍有系统hold丢cap和priced分别取整2项P1，修复中。P33剩余N6/active管理/O4、P34综合价值场景及P35其余门槛保持OPEN，不打包/P36/推送。

**源码与原生 UI 检查点 — 2026-09-13 05:25 CST：** N1v9 原始两文档、400000/12 原目标在 SDK c9a1f183 / Host 45c09756 源码环境完成：220.968s，正式 REPORT f6b192a3…f905、6 条 VERIFIED 逐字引用（两来源、完整表格行、完整限定单元），242431 tokens 已结算/预留0，13 次 Provider handoff。真实 UI 读报告、引用并冷启动重读，调用仍13/无重复；文档区“尚未判定”投影缺陷已修复，后端13 PASS/0.06s、前端25 PASS/0.912s及typecheck通过，新 UI 待验。动态新增已完成依赖的 Task 回放修复42 PASS/36.16s，原 v14 #14 历史43事件全覆盖/无差异；Python3.12空AST字段兼容35 PASS/0.29s，保持原生产基线。总体P33/P34/P35仍OPEN；进程kill测试仍在修复，FIRST请求保护仅helper7 PASS未集成；不打包/P36/推送。

**当前源码检查点 — 2026-09-13 04:49 CST：** P35 离线备份 20 PASS/17.29s；租约丢失恢复及取消 10 PASS/23.14s，受影响取消/恢复/租约回归 44 PASS/6.68s。N1v8 真模型仍失败：18 次实际调用、378113 tokens 已结算、当前预留 0；已定位 RUNNING 时终态 ordinal_to 为空造成 180s 错误超时。改读 SDK 持久进度的定向检查 8 PASS/8.98s，保持真正停滞超时控制；Host 六类文档场景及启动器 28 PASS/12.74s。上述为以 SDK e4da042 / Host 985e403 为基线的未提交修复证据；新原生 UI 待验，P33N1/P34/P35 整体 OPEN，不打包、不执行 P36、不推送。

**Source and native checkpoint — 2026-09-13 04:13 CST:** SDK protocol-error response parsing preserves independently valid Provider usage while still rejecting malformed tools (26 PASS/1.36s); missing/invalid usage stays unknown. Late-accounting automatic original-subject import/settle11 PASS/2.90s and receipt boundaries5 PASS/0.47s, independently reviewed. Citation repair retains failing claim/index/source/line identity without source-body reinlining3 PASS/0.46s. Broader integration v14 is still running/stalled in legacy recovery, not PASS. N1v7 was UI-cancelled after malformed-tool response without usage,170532 settled/108083 unknown held,13 physical handoffs, no successful value acceptance; original proof retained. Controlled source UI N2 delivered two28-Claim Missions (750 tokens each/zero reserve), actual long block290080 characters reached END_OF_LONG_TABLE, in-flight citation switching/CAS error and restored retry observed. N3 source supersede/revoke-reject/revoke-approve and historical read observed; cold verification in progress. N4/N6 boundary software27 PASS/10.67s including launcher identity, native cases not yet run. Whole Phase3 gates remain OPEN; no packaging/P3.6/push.

**Latest native/source checkpoint — 2026-09-13 03:26 CST:** N1v6 produced a rejected REPORT and9 proposed Claims; no formal acceptance. Rule failure from generated free-text quality criteria, then11 zero-call context-overflow retries.128156 tokens settled/currentreserved0, UI observed and exited cleanly. Repair5 software controls passed; broad1051 PASS2 regression FAIL3 optional skips. P34 fragment runtime2 failures; P35 priced rounding reserve review P1 open. N1–N6/O4 and overall P34/P35 remain OPEN. Details/current commands/evidence in Phase3 journals; no packaging/P3.6.

**Source checkpoint, 2026-09-13 03:12 CST:** integrated source checks:1037 PASS/2 legacy schema FAIL (48.27s); pre-schema15 reserved-attempt read compatibility fixed, targeted9 PASS/0.36s. Includes15 candidate controls, Mission system pool7, FIRST6, priced cold1 and missing-usage boundary2. COMPARE decisions and full immutable payloads now replay; frozen candidate deadline cannot dispatch new pending candidates. Controlled Host document fixture software2 PASS/6.21s proves28 formal citations and >256KiB paging; frontend search51 PASS/1.04s and typecheck pass. Fragment branch remains5 output-conflict failures (16 other controls passed); Mission-system runtime hooks, SUCCEEDED-missing-usage settlement, N1–N6/O4 and real P34/P35 gates remain OPEN. No release packaging/P3.6. This is an incomplete development checkpoint.

**Runtime checkpoint, 2026-09-13 02:50 CST:** default FIRST Critic tail reserve/consume/release is connected to actual production dispatch and passed6 controls/0.58s. Typed denial collection3/0.36s; true two-SQLite priced cold reopen1/0.40s. Scope excludes OS-kill, future Mission-level system pools and SUCCEEDED-without-usage late accounting. P34 joint run has3 FAIL/4 PASS/5 setupERROR; source inheritance defect identified and being repaired. N1 and remaining native/full audit gates remain open. See current journals; no packaging/P3.6.

**Source budget checkpoint, 2026-09-13 02:35 CST:** public Mission snapshot now carries current ledger usage in the same read transaction (39 SDK controls/5.12s); Host projection uses settled/current reserved values instead of historical Attempt totals (22 controls/8.42s; frontend49/0.941s). Provider tail/price primitives and durable typed denial:19 controls/1.00s. FIRST tail runtime wiring and native verification remain open; no release or completion claim. First-run collection/fixture failures retained in journals.

**Latest native checkpoint, 2026-09-13 02:25 CST:** N1 v5b (Host133aaa62 / SDKdfc9b7c) FAILED: sole Task90000/4 exhausted its attempts despite Mission400000/12. Both original sources were read in4 pages; no REPORT. Eight physical calls all settled, Mission86732 tokens/current reserved0. UI44494 reservation display is a confirmed projection bug; correction and typed denial stopping are in progress. N1–N6/O4 and P3.4/P3.5 remain open; no packaging or P3.6. See current Phase3 journal for immutable evidence.

**Current source checkpoint, 2026-09-13 02:08 CST.** Host source profile/projection16 controls and launcher19 controls pass. Full orchestration source run v4 observed180 PASS/5 FAIL (164.55s); three frozen-package inventory checks do not apply to editable installation and remain unpassed under the user-paused packaging gate. The document fixture omitted mandatory critic_review and a path assertion matched the runner ancestor; both corrected controls pass in v5 (2 PASS/4.78s). Remaining source modules v6:27 PASS/1 fixture-path FAIL (31.78s); the pure scenario-path oracle now supplies a path actually outside ignored evidence and passes v7 (1 PASS/0.04s). Production test gates remain unchanged. These are source software checks; new N1 native run remains open, v4b failure preserved. No release packaging or P3.6 work.

## Earlier checkpoint details

Source launcher identity controls: 19 passed, pytest 0.11s (wrapper 0.76s); native startup remains open. Resource identity covers metadata, not payload bytes. See the Host G journal for scope and evidence.

最后更新：2026-09-13。P3.3 G 仍在源码 UI 验收；P3.4/P3.5 仅必要接缝先行，未整体完成。当前新 Host source profile 将显式 editable SDK 的 Context 与逐请求 token 计数接到同一官方 tokenizer；旧执行池维持冻结配置，普通 wheel 模式保留原入口。`g-host-source-profile-summary-v3` 用独立源码 SDK 环境验证 profile 与文档摘要显示投影：16 passed / 0 skipped，pytest 0.34 秒、wrapper 0.98 秒；这是软件接线证据，不是原生 UI 或真实模型通过。源码启动器正在补恢复身份核验，新 N1 尚未开始；N1 v4b 真实预算失败仍保留。安装打包、发布和 P3.6 暂停。

最后更新：2026-09-13 01:04 CST。P3.3 G源码UI N1v4b已实际完成失败路径：Host e690bdcf、SDK e346689，Mission mission-61a22dea64fa4841为FAILED/budget_exhausted；真实flash35次成功、1次协议失败，已报告344854 tokens，不能说Mission花满400000。Worker1在90k Task预算下耗224780 tokens，Worker2耗115407，下一次Task预留时才拒绝。17页完整原文已实际可见，但模型一条引用错行、两条分析缺来源引用，rule正确拒绝。大页/逐请求预算与提交契约后继正在修复，N1/G未通过。原生正常退出PG2135无残留；不打包/P3.6。另修复Host诊断摘要读取：SDK摘要在detail内时不再显示空白；30项投影控制通过/8.31秒（wrapper8.97秒），实际UI后继重验尚待。见[Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。

最后更新：2026-09-13 00:31 CST。P3.3 G 的显式 SDK 源码身份已实际冷启动并从真实 UI 创建文档 Mission：Host d0c1ee4c、editable SDK 307 输入已核，manifest 分开 source_verified=true 与 installed_wheel_verified=false，Service pin 保持。N1 v3 业务仍 FAIL：长工具结果被 Context 截成预览，模型无法取得后文表格；主通过 UI 取消，正常退出 PG94878 无残留。另暴露 Critic 高频续租、取消及120秒外层超时的收尾缺口；SDK 修复的首批分页/实际 Context/取消控制39项通过，恢复与超时补证及原场景重测仍待完成。P3.3 未交付；P3.4/P3.5仅准备，P3.6和安装包打包暂停。证据与分层结果见 [Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。

最后更新：2026-09-12 23:44 CST：P3.3 G 源码开发接线新增显式 `editable-source` SDK 身份。正式 wheel 默认与 Service 0.3.13 pin 保持；仅非frozen进程、显式模式及独立source attestation可加载SDK源码。校验Git根/commit、两个生产包与数据全集hash（含新增文件）、editable安装metadata、版本及实际模块origin；文档修改不改变生产输入。main组装与RuntimeStack启动接线，编排manifest分别显示source_verified与installed_wheel_verified，不把源码当成旧wheel。71项定向控制通过/6.55秒，含真实composition启动前拒绝与依赖边界；主审及Ohm独立限定ACCEPT。实际源码冷启动与N1复验尚待完成；首次源码N1真实deepseek-flash任务因引用字段schema及Task预算不足FAIL，详见Host G journal。功能仍进行中，P3.4/P3.5未验收，打包/P3.6暂不执行。

# Agent 编排（任务编排视图）· 生产事实

- 最后更新：2026-09-12 23:05 CST

当前决定：用户批准直接运行源码Tauri UI完成P3.1–P3.5功能验收，暂停安装包构建与发布，不含P3.6。冻结环境探针新增明确不可执行原因，避免把后端应用当Python解释器；四项控制通过及独立限定ACCEPT，源码模式仍执行真实探针。源码开发启动已到orchestration_ready/available；共用日志过滤补URL query凭据脱敏，26项控制通过；真实UI任务尚待。此前安装包失败保留，下面为历史定位过程。

P3.3 G进行中：Host已接入原子文档创建、来源版本审批、绑定引用全文读取与系统结论展示。候选SDK0.11.1（源码a5c8fca，wheel49137655…）已钉版并安装，Service SDK保持原定0.3.13；本机旧venv的Harness0.7.2/Service0.3.12已对齐。Host实际安装组合定向49项、前端86项组件测试/typecheck通过。干净Host c3d4e227的macOS PyInstaller与Tauri构建成功，浏览器原件/许可证及构建身份核验通过；但原生首次启动因SDK公开延迟导入的workspace_binding_protocol未入包而失败，尚未发出本次真实flash请求。公开延迟导入已修复并在第二版真实启动越过；第二次lifespan因工作流handler源码未随包导致稳定manifest编译失败，源码收集修复的3项控制通过，仍待原生复验。第三次真实启动已越过workflow源码检查，但缺Tool目录JSON；资源全集修复14项控制通过，待第四版原生复验。不宣称P3.3交付。当前失败与后续修复见[Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。
- 计划与记录：`plans/2026-09-11-orchestrator-host-integration/`（plan 第 3 版、acceptance、journal）
- 方向依据：用户的 Phase3 计划 `plans/taskSys2/agent-orchestrator-phase3-plan.zh-CN.md`。本模块是其中 **P3.1 真实 App Mission 控制闭环** 的 Host 直连实现。
- SDK：`simple-harness-sdk` 的 `agent_orchestrator`（与 `simple_harness` 同在一个 wheel 里）。Host 钉版以 `backend/deskpet/sdk_adapters/sdk_candidate.py` 为唯一来源。

> 状态（2026-09-12）：P3.1 Host 直连路径已交付。
> - 自动化：`tests/orchestration` 107 passed，vitest 772 passed。
> - 真实 deepseek-flash 运行：HA-11 通过。
> - 原生 App 验收：HA-12 ①–⑥ 全部通过，用的是 verify bundle `f51ddc37`（debug .app 加源码后端），报告见计划目录的 `reports/native-ui-run1.md`。
> - 冻结打包的安装包没有验证（PyInstaller spec 仍停在 0.6.4），HA-22 ① 的 WebView 刷新也没有做原生验收，两者都列为遗留。

## 1. 装配位置

| 层 | 位置 | 职责 |
|---|---|---|
| 启动 | `backend/main.py` lifespan，紧跟 `_activate_product_sdk_runtime()` | 调 `deskpet.orchestration.wiring.activate_orchestration`。失败只让编排服务不可用，并记入 `startup_errors`；"未配置模型"不算故障。任何情况下都不会让后端启动失败 |
| 关停 | `backend/main.py` lifespan 关停段，在 SDK stack 之前 | 调 `deactivate_orchestration`。这只是有序关停路径：App 真实退出时后端收到的是 SIGKILL，编排的正确性不依赖这一步 |
| 服务 | `deskpet/orchestration/service.py` 的 `OrchestrationService` | 持有目录锁、provider 快照、`Orchestrator` 实例与驱动循环、SDK facade、部署清单，并做 Host 门口检查。<br>驱动循环失败时按指数退避，时长不超过 `backoff_max_seconds`；指数本身也有上限，失败次数再多也不会溢出。连续 3 次失败就重建运行时，连续 5 次标为 degraded；重建本身失败时循环不退出，只标 degraded 并写明原因。<br>degraded 时，读取、取消、审批决定、接管、评论照常可用，只拒绝新建 Mission（`orchestration_degraded`） |
| 协议 | `deskpet/orchestration/handlers.py`，`/ws/control` 中 `mission_*` / `orchestration_*` 消息 | 纯分发函数，响应格式为 `<type>_response {request_id, ok, data ｜ error_code, error}`，异常不会抛进 socket 循环。payload 不是对象时回 `invalid_request`，不会断开主对话共用的控制通道 |
| 推送 | `deskpet/orchestration/pump.py` 的 `MissionChangePump` | 用只读连接每秒查看各 Mission 的状态和最大 seq，每次写操作后也立即查一次；有变化就广播 `mission_changed` |
| 投影 | `deskpet/orchestration/projection.py` | 按白名单输出，长度有上限；动作参数超过 600 字符时，只给截断后的预览。<br>模型写的文本都标注 `source: "model"`：结果摘要与 claims、Task 目标、critic_review 摘要、审批摘要、动作理由。<br>事件只给 seq、type、时间、Task / Attempt id 与 actor_type，payload 一律不外传；唯一例外是评论事件，带评论文字。<br>产物只给工作区相对路径和 hash，不给 `storage_uri`；不暴露 intents。<br>`ui_state` 按 P3.1 的状态词汇统一推导（请求已接收／排队／运行／待验证／待人／UNKNOWN／正式交付，另有失败、已取消），列表与详情共用同一套 |
| 前端 | `tauri-app/src/views/MissionsView.tsx`、`stores/missionsStore.ts`、`stores/useMissionsFeed.ts`、`components/Sidebar.tsx`（入口"任务编排"，角标显示待审批数）、`components/WorkbenchShell.tsx`（视图 `missions`） | 界面只做投影，所有改动都走控制通道命令。<br>• 常驻订阅：`useMissionsFeed` 挂在 App 上，与视图是否打开无关。启动时拉取 status 和列表；收到 `mission_changed` 就更新 store，并节流重拉列表（最多 1 次/秒），所以侧栏角标和列表的状态词会实时变化。<br>• 事件游标：推送看到的 seq（`lastSeq`，只用来防倒退）与已加载事件的游标（`eventCursor`）分开保存，拉事件一律从游标往后。推送的 seq 超过游标时，连续分页补拉，每页 200 条，一次最多 20 页，超出后显示"加载更多事件"。时间线只显示最近 50 条。<br>• 状态词统一用后端的 `ui_state`。<br>• 详情里还有：产物（"查看产物"，按 id 读取并核对 hash；二进制和截断都会注明）、评论（"评论"、"发表评论"，评论对象是 Mission）、策略漂移提示、等待原因（人工复核、动作审批、仲裁，附开始时间）。<br>• 模型写的文字标注"模型生成，未核实" |
| 服务登记 | `backend/context.py` 的 `_VALID_SERVICES`：`orchestration`、`orchestration_pump` | — |

## 2. 数据

- 正式目录：`<user_data>/data/agent-orchestrator/`，内含：
  - `orchestrator.db`：编排库，只有 SDK 的 Commit Service 写；
  - `execution.db`：编排自己的 SDK 执行库，与主对话的 `execution-v6.sqlite3` 分开；
  - `workspaces/`；
  - `deployment-manifest.json`；
  - `.instance.lock`。
- 测试场景目录：`<user_data>/data/agent-orchestrator-test/`（见 §6），正式库一行不写。
- 两个目录都不能是软链，也不能解析到 user_data 之外。
- **单实例**：编排目录上有 `flock`。第二个进程拿不到锁，就标为 unavailable，不派发任何任务。持锁进程被 SIGKILL 后，锁由内核释放。
- **owner**：每个进程各自一个，形如 `deskpet-orchestrator-<pid>-<随机8位>`。不能共用：SDK 会把同一 owner 名下的有效租约都当成自己的。

## 3. 部署政策（固定）

| 项 | 值 | 理由 |
|---|---|---|
| `code_execution` | 由启动时的探针决定：`sandboxed` 或 `off` | P3.2 §4.3：问题不再是"允不允许执行"，而是"隔离在这台机器上证明过没有"。每次启动跑 SDK 的能力探针（8 项：读家目录、读他人临时目录、写越界、联网、向宿主发信号、完整 daemonize 的回收、输出截断、CPU 上限）；**全过才是 `sandboxed`**，否则 `off`。Host **永不使用 `process_only`**——不隔离的子进程只适合可信代码，而模型写的代码不是。<br>`off` 时（与 P3.1 相同）：`code_test` 不是已部署层；`pytest:` 条件在入口被拒；旧 Task 的 `pytest:` 由规则层判 FAIL；冲突转 DEFERRED；`run_tests` 被拒。<br>`sandboxed` 时：这些重新开放，`run_tests` 进入 `allowed_tools`，模型写的代码只在一次性副本里、经 seatbelt 运行。探针报告写进部署清单与 `status()` |
| `allowed_tools` | 三个工作区工具 | 编排 Agent 拿不到 Host 的任何工具（shell、MCP、文件系统、浏览器都没有） |
| `enabled_connectors` | 默认空；授权发布目录后含 `file_publish`；`test_config` 仅测试场景 | P3.2 P32-14：只有用户在配置里指明了发布目录、该目录存在、**且能承载硬链接**（连接器唯一的原子提交点）时，`file_publish` 才启用。三者缺一就不启用，于是 Mission 连带 `action:file_publish…` 的成功条件都不能提交——拒绝发生在门口，而不是等模型产出候选之后。不支持硬链接的卷（exFAT、部分网络盘）在授权时就被挡下，不会变成运行期的 UNKNOWN |
| `max_action_level` | L2 | 单机只有一个人，满足不了 L3 要求的两个不同的人 |
| 本机执行测试的开关 | 不提供，也不会有 | P3.2 用"隔离是否证明过"取代了"允不允许"。配置里没有任何开关能打开它；决定权在探针，结果在部署清单里可查 |

## 4. 身份与授权

- **Principal**：`local-user:<companion 本机身份 profile_id>`，显示为"本机用户"。单机没有认证，身份就等于能操作这台 App 的人。这一点登记为限制。
- **与 Host 的 auto / manual 模式分开**：auto / manual 只管主对话里的工具效果。编排的人工审批（原文 §22）必须由人来决定：auto 不会自动批准，manual 也不会弹出主对话的授权窗。
- **tenant**：固定为 `local-desktop`。SDK facade 按 tenant 检查每个对象的归属：不属于调用方的对象和根本不存在的对象，一律返回同一句 `not_found`。
- **密钥门口**：下列内容会同时按通用密钥模式和当前 provider 的密钥检查，命中就回 `secret_rejected`，一个字都不写进编排库。provider 密钥即使不是 `sk-` 形态也能拦下。
  - 新建请求里的每一个字符串：目标、成功条件、`stop_conditions`、`synthesis`、`workspace_seed`，连键名也查；
  - 审批的理由和备注、接管依据、评论。
- **seq 是全库自增**：一个编排库只有一个 tenant 时没有影响；多 tenant 共用一个库时，跳号会暴露其他 tenant 的事件量。

## 5. 恢复语义（App 退出 = SIGKILL）

| 被杀的时点 | 新进程里发生什么 | 界面 |
|---|---|---|
| 等人工复核（所有回合都已提交） | 旧 owner 的租约过期（`lease_seconds`）后，新 owner 继续验证**同一个** Attempt，不重做已提交的工作（P3.1-A07） | 复核通过后几秒内进入正式交付 |
| 模型调用中途 | SDK 回合停在 running；心跳里的 `liveness.blocked` 变为 true（`kind: provider`） | 详情里显示"回合结果未知"，可以接管：停止，或带说明重试（`mission_takeover`，basis 必填）。2026-09-12 实测（租约 2 s）：新 owner 启动后约 6 s 出现 blocked。不接管的话，Mission 一直停在 ACTIVE，观察 240 s 也没有自动收敛，所以接管入口是必需的 |
| 其他时点 | SDK 按原文 §16.4 恢复：已完成的不重跑，SUBMITTED 未验证的重新进入验证 | — |

## 6. 仅测试用的场景

- `DESKPET_ORCHESTRATION_TEST_SCENARIO=approval-action`，并且 user_data 位于某个 `.local-test-evidence/` 目录下。两个条件缺一个都不生效，也不依赖 DEV_MODE。
- 生效后：使用独立目录；provider 换成 SDK 的 approval-action 夹具，只用工作区工具；启用测试配置服务连接器 `TestConfigService`；只允许一个 Mission；界面标出"测试场景"。
- 这个场景只用于原生验收里的审批流程，它的通过不代表生产授权。
- 原生启动器 `scripts/native/launch_native_candidate.py` 会丢弃继承来的 `DESKPET_*` 变量，所以要用 `--orchestration-test-scenario approval-action` 把场景传给后端。`--userdata` 必须位于 `.local-test-evidence/` 下，后端才会承认这个场景。

## 7. 设置（`config.toml [orchestration]`）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 按 CLAUDE.md：测试阶段已完成的能力默认开启 |
| `max_concurrency` / `max_concurrent_model_calls` | 1 / 1 | 与主对话共用 provider 限额。**只在编排库第一次 seed 时进入 ACTIVE 策略**，之后修改只会记一条 `PolicyConfigDrift`，要经策略晋级才会生效 |
| `default_mission_max_tokens` / `default_mission_max_attempts` | 400000 / 12 | 代码常量（`OrchestrationSettings`），不从 config 读。**本部署不提供无上限的 Mission**：<br>• 请求里预算留空的项，由 Host 门口补上这个默认值；<br>• 用户填了的值原样保留；<br>• 0、负数、非整数一律拒绝，不会被默认值替换；<br>• 默认值在进 facade 之前补上，所以回执的 spec hash 已经包含它；<br>• `orchestration_status.mission_budget_defaults` 把默认值下发给表单占位符，详情显示实际生效的预算。<br>依据：原生验收时，留空预算的 Mission 被真实 Planner 编出 800 tokens 的 Task 预算，结果以 `budget_exhausted` 失败。裁决见计划 journal §4.4。<br>12 次的理由：Mission 级尝试次数统计的是所有 Task 的全部 Worker Attempt |

## 8. 模型与费用

- provider 在启动时对 `get_chain()` 的第一个启用项做一次快照，之后换 provider 需要重启才生效。
- DeepSeek 官方端点把 `deepseek-v4-flash` 映射为 `deepseek-flash`，status 里同时显示两个 id。
- 没有注入价目表：金额记为 null，界面显示"未计价"，不写成 0。
- **Task 预算下限**（SDK 0.9.11 起，F-ORCH-1）：
  - Graph Manager 会拒绝预算低于 `k × (base + critic)` 的 Task。k 是每个 Task 的候选数；base 是单轮最多产出的 token 数，这个部署是 8192；critic 部分只在验证政策含 critic_review 时计入，是 Critic 的预留 6000。
  - 被拒后，Planner / Manager 会收到原因（`task_budget_below_floor`）并重新规划，系统不会替它们编一个数。它们的输入里也写明了下限。
  - 下限是**预留层面**的必要条件：它只保证在预留那一刻，第一个 Attempt 和它的 Critic 都能预留得到，前提是一轮结算的用量不超过它的预留。真实模型一轮会连输入一起结算，远超 base（原生验收时一轮结算了 22003），所以预算正好等于下限的 Task，第一轮之后照样可能付不起 Critic，之后的修复和重试也不在保证之内。
  - 这与 Host 门口的默认预算（见 §7）是两道互补的保护。
- **产物的验证状态**（SDK 0.9.11 起，F-ORCH-3），在对应的提交事务里一并写入：
  - 结果被接受：VERIFIED；
  - 结果被判 FAIL：REJECTED；
  - 被取代的候选：保持 UNVERIFIED。

  注意：产物的 REJECTED 和结果的 REJECTED 意思不同。
  - 产物 REJECTED 表示它所在的结果被判了 FAIL。
  - 被取代的结果，其 `verification_state` 也是 REJECTED（verdict=superseded），但它从来没有被评判过，所以它的产物是 UNVERIFIED。

  读模型把两者并排显示时，要按上面的含义解读。
- **Attempt 的 RETRY_WAIT**：这是失败 Attempt 的终态。原文 §25.2 没有 Attempt 的 FAILED 状态，重试的时候另起一个新 Attempt。所以 Mission 结束后，个别 Attempt 停在 RETRY_WAIT 是设计如此，不是还在排队重试。

## 9. 部署清单（P3.1-A08）

`deployment-manifest.json` 与 `orchestration_status.deployment_manifest` 记录以下内容，不含密钥：
- host_commit，以及 `host_dirty`：backend、tauri-app、scripts 下有没有未提交或未跟踪的改动；打包版没有 git 时为 null；
- `simple_harness` / `agent_orchestrator` 实际导入的文件路径与版本；
- distribution 版本与钉版的 wheel sha；
- schema；
- 生效的部署政策、测试场景、设置；
- 模型。

实际导入的版本与钉版不一致时，服务标为 unavailable。这个检查在打开编排库之前做，所以版本不对的 SDK 不会去迁移编排库；这种情况下，清单只记录 `refused: pin_mismatch` 和实际导入的信息。

## 10. 限制与遗留

- 身份是自报的本机用户，没有多用户认证。
- 沙箱（P3.2）的边界，如实记下：
  - **内存与进程数只是软限制**：本机没有 cgroup，`RLIMIT_AS` / `RLIMIT_DATA` 设不进去，`RLIMIT_NPROC` 又按整个 uid 计数，所以只能采样后回收，属于事后处理；`cpu_seconds` 是**每进程**的硬限制，整次执行靠墙钟兜底。
  - **没有 uid 隔离**：没有管理员权限，同一 uid 下的残余风险靠 seatbelt 规则收敛。
  - **元数据可读**：放行 stat 才能让 venv 的软链解析正常，代价是沙箱里的代码能探测任意路径是否存在、大小与修改时间，但读不到内容。
  - **用到的是已废弃且无公开文档的接口**：seatbelt 与 `sandbox_check`（Chromium、WebKit 也在用）。每次启动由探针重新验证；探针不过就退回 `off`。
  - **回收耗时受外部工具影响**：认进程要靠 `ps`，不隔离模式还要靠 `lsof`，两者都有单次超时，但极端情况下一次回收仍可能偏慢；正确性不受影响。
  - **宿主崩溃后的逃逸进程认不出来**：金丝雀随执行目录一起删除，宿主重启时没有线索可扫（SDK journal 已登记，留待后续处理）。
- DeepSeek 价目没有注入，金额显示"未计价"。
- 策略只读：提议、评测、晋级要用 SDK CLI。
- 编排的证据目录（workspaces）不会自动清理。
- PyInstaller 打包 spec 仍停在 0.6.4，尚未跟进。
- 模型调用中途被杀之后，需要人来接管，SDK 不会自己收敛。自动恢复"结果未知"的回合，归入 P3.5 处理。租约为默认 60 s 时，"结果未知"要多久才出现，还没有在原生 App 里测过。
