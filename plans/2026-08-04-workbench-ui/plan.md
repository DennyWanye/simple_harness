<!-- plan-status: finalized -->
<!-- challenge-rounds: 7, findings-closed: 38, final-verdict: PASS (NEW_CRITICAL_FINDINGS: 0) -->
# Plan：Workbench UI 改版（去桌宠、工作台化）

> 验收标准：acceptance.md「Workbench UI 改版」节 WB-1..WB-12。
> 调研输入：两份代码侦察报告（前端迁移面 / 后端会话 API 与 Rust 窗口面），全部论断已有 file:line 证据。

## 主要矛盾

**companion 权限作用域与控制连接身份的迁移**。现状三处硬耦合：

1. `code-panel/controlWs.ts:224-231` 用 `window.location.hash.startsWith('#/message-panel')` 决定
   `requested_window_label` / `requested_scope` / 控制会话 sid——内嵌进主窗后 hash 消失，连接
   身份静默退化为 `label=main / scope=general`，companion challenge 失效；
2. Rust `webview_permissions.rs:13-18` 白名单只允许 `("message-panel","companion_action")`，
   `("main","companion_action")` 被显式拒绝（:81 有断言 + 单测）——凭据签发直接被拒；
3. 后端 `main.py:10712-10717`：同 session_id 的第二条非 aux 控制连接会把旧连接以 4002 踢掉。

这三处不解干净，工作台合并后的表现是"看起来能聊天，但 companion 特权 IPC 全线
`window_control_scope_denied`、会话回灌不一致"——静默降级，最难排查。全部任务排序
与测试深度向此倾斜（T3 最先做、单独验证）。

## 关键架构决策（含备选与权衡）

- **D1 双连接保留，不做两套 WS 合一**。App 的 `ws/ControlChannel.ts`（identity_bind/aux 槽，
  relay 身份绑定 + SettingsPanel lastMessage 顺风车）与 `code-panel/controlWs.ts` 单例
  （companion_action，sid=`message-panel-main`）在单窗内共存——两者 session_id 不同，
  不触发 4002 踢线（`main.py:10712` 只踢同 sid 非 aux）。备选"合一成单连接"是大手术
  （App.tsx:966-1308 巨型 switch + sessionsStore 双状态源重构），回归面失控，列为后续
  独立重构，不进本 release unit。**controlWS 的 sid 字符串 `message-panel-main` 保持不变**
  （避免后端会话过滤/投影链路任何抖动），仅加注释说明历史命名。
- **D2 布局壳不引路由库**。view state（`chat|skills|artifacts|settings`）自第 5 轮起
  提升至 App 层 useState 下传 WorkbenchShell（Toolbar 存活期需要够到它；D2 反对的是
  路由库，不是 state 归属层级），
  无深链需求（Tauri 桌面应用，`main.tsx` 仅保留 `#/slashtest` 测试路由）。业界 React 工作台
  （VS Code/Slack 形态）在无 URL 语义时同样用本地视图 state——适配分析：本项目单窗、
  无浏览器历史语义，路由库前提（URL 可分享/前进后退）不存在，不用。
- **D3 chat 视图常挂载**（其余三视图按需挂载）：MessageStream 的 WS 订阅/滚动位置/运行投影
  在切页时不应反复冷启动；skills/artifacts/settings 无常驻状态，unmount 即可。
- **D4 产物列表数据源（挑战轮已闭环）**：已查证 backend/main.py 的 msg_type 分发链
  无任何 artifacts 列表命令 → **确定走 Rust 新增 command `list_artifacts()`**
  （扫 `<user_data>/artifacts`，返回 name/path/size/modified_at 倒序），打开/显示文件夹
  复用 `artifact_ops` 既有 command。不改 Python 后端（artifacts 目录与
  `paths::user_data_dir()` 已在 fork 修复中对齐）。
- **D5 浮层组件页面化用"宿主模式"而非重写**：SettingsPanel / CapabilityCenterPanel 保留
  组件本体，新增 `variant: "overlay" | "page"` 渲染分支（page 模式去 backdrop/fixed 定位，
  填满内容区）。备选"重写为独立页面组件"改动量大且回归设置项，弃。

## 解剖麻雀：companion 凭据链（同类改动通用模式）

`InputBar/CompanionCard 交互 → controlWS.send_companion_action（controlWs.ts:1922+，需 companion challenge）
→ invoke("get_window_control_credential")（process_manager.rs:769，用调用窗口的 label）
→ authorize_window_control_scope(label, scope)（webview_permissions.rs:13-18 Rust 白名单）
→ Ed25519 签发 → **后端验签（第 4 环，挑战轮补上）**：
`backend/deskpet/companion/control_credentials.py:24-26` 的
`ALLOWED_WINDOW_SCOPES = {("main","identity_bind"), ("message-panel","companion_action")}`
→ verify() :163 assert_window_scope + :178 比对凭据内 window_label →
`control_ingress.py:1248` 对特权命令硬编码 `expected_window_label="message-panel"`。
通用模式：**任何跨窗口迁移的特权功能，必须同时改四处——前端身份声明（controlWs 常量）
+ Rust 白名单 + Python 后端白名单/期望标签 + 两侧单测——且以真机一次 companion 动作
作为链路级验证**。只改前三处会得到"Rust 签发成功、后端 window_scope_denied"的静默降级，
恰是本主要矛盾警告的失效形态。T2 按此执行，T8 的 ChatView companion 交互复验全链。

## 关联验收标准

WB-1..WB-12 全覆盖；任务→AC 映射见各任务标注。

## 文件影响清单

> 路径约定：前端文件均省略 `tauri-app/` 前缀；backend/ 为仓库根下后端。

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| backend/deskpet/companion/control_credentials.py | 后端凭据白名单 | companion_action label 迁 main（挑战轮 P0）|
| backend/deskpet/companion/control_ingress.py | 特权命令验签 | :1248 expected_window_label 迁 main |
| backend/tests/companion/test_window_control_credentials.py | 后端单测 | 断言翻转（绑定 acceptance 删除例外）|
|------|------|----------|
| src-tauri/tauri.conf.json | 窗口/打包配置 | 主窗普通化；删 message-panel 窗定义 |
| src-tauri/Cargo.toml | Rust 依赖 | 删 macos-private-api feature、window-vibrancy（未使用） |
| src-tauri/src/webview_permissions.rs | 权限白名单 | companion_action 迁到 main label + 单测 |
| src-tauri/src/commands.rs | IPC commands | 删 4 个 message-panel command + dock_impl；open_directory_dialog parent→main |
| src-tauri/src/lib.rs | 装配 | 删 4 条注册、click_through 注册、mod click_through；tray 文案；Destroyed 注释更新 |
| src-tauri/src/click_through.rs | 点击穿透 | 整文件删除（前端零调用，侦察证实） |
| src-tauri/src/window_geometry.rs | 几何持久化 | 删 pin_size 逻辑（用户可缩放后必须持久化真实尺寸）；单测更新 |
| src-tauri/capabilities/default.json | 窗口权限 | windows 数组去掉 message-panel |
| src/main.tsx | 入口路由 | 删 #/message-panel 分支 |
| src/code-panel/controlWs.ts | companion WS | :224-231 hash 判定→显式常量（label=main, scope=companion_action, sid 不变） |
| src/App.tsx | 顶层 | 删 pet state/effects/JSX（325-502、1464-1764、2229-2360、2282-2296 等侦察圈定块）；删自建 messages/handleSend/slash（1764/321/137-139）；挂 WorkbenchShell |
| src/components/WorkbenchShell.tsx | 新 | 侧栏+内容区壳、view state |
| src/components/Sidebar.tsx | 新 | 导航项+底部设置+SessionList 容器 |
| src/components/SessionList.tsx | 新 | 从 MessagePanelRoot.tsx:1039-1270 会话下拉抽出 |
| src/views/ChatView.tsx | 新 | MessageStreamPanel(embedded)+InputBar+hydration+Harness 开关+ContextRing+模型按钮 |
| src/views/SkillsView.tsx | 新 | CapabilityCenterPanel page 宿主 |
| src/views/ArtifactsView.tsx | 新 | 产物列表（D4 决策规则） |
| src/views/SettingsView.tsx | 新 | SettingsPanel page 宿主 |
| src/components/SettingsPanel.tsx | 设置 | variant 分支；删 petModels/currentPetModelId/onPetModelChange props |
| src/components/CapabilityCenterPanel.tsx | 技能 | variant 分支；SkillStore 互跳改视图内切换 |
| src/components/Toolbar.tsx | 顶栏 | 退役：入口移侧栏/设置页，连接状态徽章移侧栏底部状态区 |
| src/message-panel/MessagePanelRoot.tsx | 旧面板 | 拆解后整文件删除（窗口三件套 :934-966/:983 直接丢弃） |
| 删除文件 | — | PetCanvas.tsx、petCharacter.ts、petTransform.ts(+test)、pet-engine/、pet-anim/、pet-state/、petModels.ts、public/assets/pet/、components/ChatHistoryPanel.tsx（死代码）、DialogBar.tsx、UserBubble/PetWorkingBubble/PetDebugOverlay/PetSupervisorBubble/PetCelebrationBubble/PetDNDBadge |

## 任务清单（按依赖排序）

### T1 — 主窗普通化 + 几何持久化适配 [WB-1, WB-10]
- tauri.conf.json：main 窗 `decorations:true, transparent:false, skipTaskbar:false,
  maximizable:true`，删 maxWidth/maxHeight，`width:1000,height:700,minWidth:800,minHeight:560`，
  `title:"Simple Harness"`；`app.macOSPrivateApi` 删除；Cargo.toml tauri features 去
  `macos-private-api`；删 `window-vibrancy = "0.6"`（侦察证实全仓零引用）。
- window_geometry.rs：删 `pin_size()`（:274-289）及其两处调用（含 on_move :255），
  Resized 事件直接进 debouncer（lib.rs:213/224 已有 label=="main" 门）；常量 MIN_W/MIN_H
  调整为 800/560 并同步单测。旧值兼容语义**维持现状：越界拒绝返 None 走 conf 默认**
  （window_geometry.rs:59-61 现行为，挑战轮澄清：不是 clamp）——单测断言"旧 500×640
  记录因低于新 MIN 被拒、回退 conf 默认"，此为预期迁移行为，写入注释。
  behavior-change 绑定：MIN 常量与单测翻转对应 acceptance WB-1 窗口新规格。
- 验证：cargo test --lib window_geometry；tauri dev 启动普通窗、拖拽缩放后重启恢复。

### T2 — companion 权限与连接身份迁移（四处同改）[WB-4 前置，主要矛盾]
- Rust：webview_permissions.rs 白名单 `("message-panel","companion_action")` →
  `("main","companion_action")`；更新 :77-84 单测（main 允许、message-panel 拒绝、其他拒绝）。
- **Python 后端（挑战轮 P0 补入）**：
  - `backend/deskpet/companion/control_credentials.py:24-26`：ALLOWED_WINDOW_SCOPES 中
    `("message-panel","companion_action")` → `("main","companion_action")`（identity_bind 行不动）；
  - `backend/deskpet/companion/control_ingress.py:1248`：特权命令
    `expected_window_label="message-panel"` → `"main"`；
  - `backend/tests/companion/test_window_control_credentials.py`：断言同步翻转——
    behavior-change 绑定 acceptance「only-add 显式删除例外」条款（message-panel 窗口移除），
    非改绿迁就实现；
  - **SQL CHECK 约束迁移（第 5 处硬编码，第 2 轮挑战 P0）**：
    `companion/migrations/001_companion_v1.sql:56-61` 的 `profile_control_leases` 建表
    CHECK 写死 `(window_label='message-panel' AND scope='companion_action')`，且
    schema.py 按 user_version 永不重跑已应用迁移 → 必须新增
    `007_companion_window_label_main_v7.sql`：SQLite 不能 ALTER CHECK，按规范重建表。
    **照抄 001 原约束的双臂结构只改允许对**（第 3 轮挑战修正）：
    `(status='challenged' AND window_label IS NULL AND scope IS NULL) OR
    (status<>'challenged' AND window_label='main' AND scope IN
    ('identity_bind','companion_action'))`——不许丢 challenged 臂（丢了虽不崩，但
    "challenged 行必须 NULL 标签"的不变量被静默放宽）。
    重建清单：全列照抄 + WITHOUT ROWID + 部分唯一索引 `uq_profile_control_active` 重建
    + **外键子表 `profile_control_commands` 的复合外键（001:88）**——schema.py 迁移包裹
    （foreign_keys=OFF + 事后 foreign_key_check :132）使重建流程安全，007 验证项显式
    断言 foreign_key_check 零违例。INSERT SELECT 拷数据时仅改写非 challenged 行的
    `window_label`（'message-panel'→'main'）；challenged 行标签本为 NULL，无需也不得
    改写；`requested_window_label` 审计列**故意不改写**（无 CHECK、仅审计语义，007
    注释写明防过度清洗）。`COMPANION_SCHEMA_VERSION` 6→7；schema.py MIGRATION_RESOURCES
    加条目；`test_store_transactions.py` / `test_window_control_credentials.py` fixture 同步。
    正确预言（修正第 1 轮）：崩点在 CHECK 层；007 后 challenged 行照旧 NULL，提升为
    active 时写入 ('main','companion_action') 满足新约束，不崩。
- 前端：controlWs.ts:224-231 hash 判定删除，改模块常量 `REQUESTED_LABEL="main"`、
  `REQUESTED_SCOPE="companion_action"`、`CONTROL_SESSION_ID="message-panel-main"`
  （历史命名，后端 main.py:6459/:7295 硬编码映射，不改名）。
- 验证（链路级，不许只验签发）：cargo test webview_permissions；
  `uv run pytest backend/tests/companion/` 全目录全绿（第 5 轮扩围：007 动了
  test_store_transactions fixture，回归左移到落地当刻）；
  真机一次 companion 特权动作端到端成功（无 window_scope_denied 日志）。

### T3 — 删除 message-panel 窗与 Rust 面 [WB-2]
- tauri.conf.json 删第二窗定义；capabilities/default.json windows→["main"]。
- commands.rs 删 :210-305（dock_impl + 4 command + emit_panel_visibility）；
  `open_directory_dialog` :313-317：现码已有 main 回退（挑战轮澄清：此项是清理死引用，
  非修复），删除 message-panel 分支只留 main。
- lib.rs 删 4 条注册（:109-112）；:230-245 Destroyed 分支注释改写（exit(0) 保留）。
- main.tsx 删 :32 isMessagePanel 与 :62-72 懒加载分支。
- App.tsx 删 `leftPanelOpen`/togglePanel/可见性 effect（:523-558）与「▶ 消息」按钮 JSX（:2229-2278）。
- 验证：cargo check 零 error；grep -r "message_panel\|message-panel" src src-tauri 仅剩
  controlWs sid 常量与注释。

### T4 — 桌宠前端全删 [WB-9]
- 删文件：components/{PetCanvas,petCharacter,petTransform,petTransform.test,PetWorkingBubble,
  PetDebugOverlay,PetSupervisorBubble,PetCelebrationBubble,PetDNDBadge,UserBubble,DialogBar,
  ChatHistoryPanel}.tsx|ts、pet-anim/、pet-engine/、pet-state/、public/assets/pet/。
  **petModels.ts 不归 T4**（第 5 轮挑战：SettingsPanel.tsx:53 类型级引用，删除三件套
  ——文件、retiredSupervisor 测试引用、petmodels grep 词条——整体划归 T11 同一原子提交）。
- App.tsx 删侦察圈定的 pet 专属块：state :325-502（mouthOpenY/liveRef/observer refs/petSm/
  celebration/dnd/occlusion/milestone）、effects :1464-1764（DevTools 注入/observer tick/
  S3 轮询/typing tracker/边缘吸附/activity listener）、audio-lip_sync 中 mouthOpenY 写入点
  （:1319/:1388/:1434/:1454/:1903 改为删除或仅保留音频播放不驱口型）、JSX :2282-2360、
  fps(:133/:1954) 与 petError 条中 pet 专属分支（relay recover 复用部分保留）、
  petModelId/availableModels(:838-860)、handleNewTopic 死代码(:1802-1821)。
- SettingsPanel.tsx **不在 T4 改动面**（第 4 轮挑战：单写者=T11）：pet props 与「桌宠形象」
  区块的删除划归 T11 一并做；T4 时点 T6 已拆 App 挂载、无调用点，App 侧零工作。
- 删对应测试（pet-anim/__tests__ 全部、pet-engine/__tests__、petTransform.test、
  App 相关 pet 测试；SettingsPanel.retiredSupervisor.test.tsx 的 petModels 引用清理
  **归 T11**）；vitest 配置 coverage include 更新（原 pet-anim/pet-state 范围失效）。
  注：PetCelebrationBubble/PetDNDBadge 实际位于 pet-anim/ 目录（随目录整删）。
- petError 条（App.tsx:2175-2214）**新落点**：relay 供给分支（:1207/:1267/:2081）保留，
  以 WorkbenchShell 顶部横幅形态渲染（bannerStyle("danger")），桌宠列定位样式弃。
- 验证：`grep -riE "petcanvas|pet-anim|pet-engine|petcharacter|pettransform" src`
  零命中（允许 CHANGELOG/plans 历史文档）；**petmodels 词条延至 T11 自验 + T16 复验**；
  vitest 全绿。

### T5 — click_through 删除 [WB-9]
- 删 src/click_through.rs、lib.rs mod 声明(:12)与注册(:56)。验证：cargo check。

### T5b — Rust list_artifacts command [WB-7 前置]（组 R 尾）
- artifact_ops.rs 新增 `list_artifacts()` command：读 `paths::user_data_dir()/artifacts`
  目录（不存在返回空数组），返回 `[{name, path, size, modified_at}]` 按 modified_at 倒序；
  lib.rs 注册。单测：临时目录三文件排序 + 目录缺失空数组。
- 验证：cargo test --lib artifact；T12 前端只依赖此 command 的返回 shape。

### T6 — WorkbenchShell + Sidebar [WB-3, WB-11]
- 新组件：WorkbenchShell（flex 行布局：Sidebar 固定 240px + 内容区 flex:1 min-width:0）、
  Sidebar（Logo、三导航项、底部 ⚙️ 设置 + 连接状态徽章，样式全走 theme/tokens + dark 套件，
  当前项高亮用 `dark.accent`）。App.tsx 根 div 去 transparent/drag-region，挂 WorkbenchShell。
- **视图 prop 合同 + stub（第 3 轮挑战 P0 补入）**：T6 交付时同时创建四个 view 文件的
  stub（空内容占位）与 WorkbenchShell→视图的 props 通道显式合同：
  SettingsView ← {getChannel, lastMessage, secret, relayAdapter, onConfigChanged,
  autostart}（App :2734 现有 props 原样下传 + 第 7 轮补第六项：useAutostart 状态下传，
  供 T11 的自启开关落位）；SkillsView ← {channel: permissionChannel,
  互跳 state 本地化}；ChatView ← {activeSid, secret}；ArtifactsView ← 无 App props
  （invoke 自足）。组 V 各任务只在自己的 view 文件内填充实现，不回头改 App.tsx。
- **旧浮层挂载拆除归 T6**（同一次 App.tsx 编辑内完成，不留双实例）：App.tsx :2363
  CapabilityCenterPanel、:2374 SkillStorePanel、:2734 SettingsPanel 三个 overlay 挂载
  及其 open state（:834/:937/:938）删除，改为 WorkbenchShell 内视图渲染。
- 其余全局浮层（PermissionPopup/ExternalWaitDialog/ClarificationDialog/StartupOverlay/
  OnboardingWizard/toast 角标/ModelDownloadBanner/ContextBreakdownModal）保持 App 层不动
  （**点名排除上述三项**）。
- T6 的 App.tsx 同次编辑连带（第 4 轮挑战 P2，noUnusedLocals=true 边界即红）：
  Toolbar 存活期的 onSettings/onSkillStore 回调（App.tsx:2673-2674）重接——**机制写死
  （第 5 轮）：view state 提升到 App（`useState<WorkbenchView>`）下传 WorkbenchShell**，
  回调即 setView("settings"/"skills")；对 D2 无实质违背（D2 反对路由库，非 state 层级）；
  孤儿声明**只删 handlePetModelChange**（唯一消费 :2744 随挂载拆除；第 6 轮更正：
  availableModels/petModelId 另有 :856 petModel 派生供 PetCanvas JSX :2283/:2285 消费，
  归 T4 的 :838-860 块一并删，T6 不动）。
- 验证：新增 WorkbenchShell.test（四视图切换、当前项高亮、800×560 下无横向溢出——jsdom
  断言样式而非真布局，真布局归真机测试）。

### T7 — SessionList 侧栏组件 [WB-5]
- 从 MessagePanelRoot.tsx 抽出为 SessionList.tsx，**按标识符列举抽取物**（挑战轮修正：
  勿按行号字面切）：`loadSessions`（:618-620）、sessions_list/session_deleted/
  session_renamed 监听 effect（**:743-759——此段是会话数据链路不是语音，必迁**）、
  `switchToSession`（:644-651 + hydration effect :653-667）、`startNewTopic`（:622-642）、
  `deleteSession`（:663-674）、`finishRename`（:703-722）、`switchToDefault`（:610-616，
  deleteSession 依赖）、pickerOpen/DEFAULT_SID 等随迁 state、下拉 JSX（:1039-1270）；
  hydration effect 实际 :651-661。
  真语音块 :761-793（toggleRecording/audioMessage）**不迁**。复用 topicTitle.ts；
  空态引导文案。挂在 Sidebar「会话」项展开区。
- 验证：SessionList.test（列表渲染/空态/切换回调/新建发包 shape——mock controlWS）。

### T8 — ChatView [WB-4]
- 抽取 MessagePanelRoot.tsx 内容区，**按标识符列举**：JSX 结构 :1417-1478
  （HarnessInspectorPanel 开关默认关 + MessageStreamPanel embedded + InputBar
  sessionId=activeSid）**加上其依赖的派生逻辑 :794-880**（chatMessages/companionEvents
  派生，依赖 `src/petText.ts` 的 `forPet`——第 5 轮更正：文件在 src/ 根下、不在
  message-panel/ 内，无需迁移，仅更新新文件 import 相对路径；petText.test.ts 保留）；头部条：当前会话标题、模型按钮（ChangeModelModal）、ContextRing、
  🐞 Harness 开关。
- mic 按钮：InputBar 旁禁用占位（title="语音输入待中转站 Realtime 接入"）。
- **连接状态源（挑战轮 P1）**：ChatView 的连接/错误显示一律以 `controlWS.state()` 为源
  （chat_v2 的实际通道），后端未就绪时显示状态条 + 重试入口（承接 acceptance 非功能
  「错误态」条款）；Sidebar 底部徽章聚合两源（ControlChannel + controlWS）取最差态显示，
  避免"徽章已连接、发送却拒发"的分叉。
- CompanionDetailModal/ConfirmDialog 随迁。秘钥轮询用 App 已有 secret（删
  MessagePanelRoot:255-274 重复轮询）。真语音块 :761-793（toggleRecording/audioMessage）
  不迁（语音不在范围；:743-759 会话监听 effect 归 T7 迁移，见 T7——两处口径一致）。
- App.tsx 删自建 messages(:321-323)/大 switch 中仅供 pet 与自建消息数组的分支
  （:966-1308 收缩：保留 session_switched/task_session_started 的 activeSid 同步、
  budget/context toast、relay、权限弹窗依赖的分支）、handleSend(:1764)/slash(:137-139,
  :1830-1899)/底部输入条 JSX(:2399-2665)/latestAssistant(:1959)。
  slash：挑战轮已核实 **InputBar.tsx 自带完整 slash 支持**（SlashDropdown/ArgHintBar/
  输入历史，InputBar.tsx:24-25/:94-98/:210-222/:448-452），无需任何迁移；App 的
  SlashDropdown 状态直接随删除面走。
- 验证：ChatView.test（发送走 controlWS、hydration 四连发、禁用 mic 存在）；真机一条消息
  往返 + 一次 companion 动作（麻雀链路复验）。

### T9 — MessagePanelRoot 退役 [WB-2]
- T7/T8 抽取完成后删除 MessagePanelRoot.tsx；message-panel/ 目录**六件迁移清单**
  （第 2 轮补全、第 5/6 轮更正）：sessionHydration.ts(+test)、topicTitle.ts(+test)、
  messageVisibility.ts(+test)、HarnessInspectorPanel.tsx(+.css+.test)（ChatView JSX 直接包含）、
  HarnessRunGraph.tsx（其内部依赖）、projectDirectoryState.ts(+test)
  （MessageStreamPanel 目录确认链路依赖）——统一迁至 src/chat/；petText.ts 在 src/
  根下不属本目录，不迁（见 T8）；
  harnessInspectorModel.ts 已核为全仓零引用死码，**显式删除**。更新全部 import。
- 验证：tsc 全绿；grep message-panel 路径引用为零。

### T10 — SkillsView + SkillStore 页面化 [WB-6]
- CapabilityCenterPanel 加 `variant:"page"`（去 backdrop/fixed，尺寸 100%）；
  onOpenLegacySkillStore 改为视图内切换（SkillsView 内部 state: center|legacy-store）；
  **SkillStorePanel 同步加 page variant 或核实其样式可直接内嵌**（若自带 fixed/backdrop
  需同款处理），真机验证兜底。
- 验证：既有 CapabilityCenterPanel.test 适配 variant；真机列表渲染 + 详情打开。

### T11 — SettingsView [WB-8]（SettingsPanel.tsx 单写者；组 F 序列内、T4 之后——第 6 轮改排）
- SettingsPanel 加 `variant:"page"`；**同一原子提交内按序完成**（第 4/5 轮挑战合并划界）：
  删 petModels/currentPetModelId/onPetModelChange 必填 props 与「桌宠形象」区块 →
  清 SettingsPanel.retiredSupervisor.test.tsx 的 petModels 引用 → 删 petModels.ts 文件 →
  自验 `grep -ri petmodels src` 零命中；lastMessage/getChannel/secret 由 App 继续下传
  （App 的 ControlChannel 保留，顺风车不断）；自启开关从 Toolbar 移入设置页
  （useAutostart 已在 App，prop 下传）。
- 验证：逐设置项渲染 snapshot 对比（除桌宠形象区块外无缺失）；真机改一项保存生效。

### T12 — ArtifactsView [WB-7]
- 按 D4 决策规则定数据源；卡片复用 ArtifactCard 子卡样式（File 卡为主），操作接
  artifact_ops 既有 command（artifact_open/artifact_show_in_folder）。空态文案。
- 验证：ArtifactsView.test（列表/空态/动作 invoke shape）；真机打开一个真实产物。

### T13 — Toolbar 退役与状态迁移 [WB-3]
- 删 Toolbar.tsx；连接状态徽章（getConnColor/getConnLabel 逻辑）移 Sidebar 底部；
  记忆/Trace/反馈入口移侧栏次级区（「更多」折叠组）；退出按钮移窗口层（普通窗有系统
  关闭钮，tray 仍有退出）；自启开关归 T11。IconButton/ToggleButton 原语迁 components/ui.ts。
- 验证：入口逐一可达（真机点检表）。

### T14 — 品牌与托盘 [WB-1]
- lib.rs tray 文案：显示桌宠→显示主窗、隐藏桌宠→隐藏主窗、退出 DeskPet→退出 Simple Harness。
- 验证：真机托盘菜单三项行为正确。

### T15 — 主题合规扫描 [WB-11]
- 脚本：`grep -nE "#[0-9a-fA-F]{3,8}"` 扫描面（第 2 轮挑战扩大）：
  components/Workbench*、views/、components/Sidebar*、components/SessionList*、
  components/ui.ts（T13 新建）、CapabilityCenterPanel/SettingsPanel 的 **variant 分支
  新增行**（git diff 范围内）——白名单排除 theme/ 本身；新增/改造组件零硬编码色值。
- 验证：脚本零命中入 CI 可复跑的验收命令清单。

### T16 — 测试构建全绿 + 文档同步收口 [WB-12, DoD]
- vitest run 全绿（删除面测试清理 + 新增组件测试）；cargo test --lib 全绿；
  `uv run pytest backend/tests/companion/` 全绿；npm run build 通过；cargo check 零 error
  零新增 warning。
- **文档同步（DoD 承接，第 2 轮挑战 P1）**：ARCHITECTURE/UI.md 删除 §0 桌宠渲染节、
  重写为工作台架构描述；根 ARCHITECTURE.md 目录树/渲染行更新（pet-* 目录移除、
  src/chat/ 与 views/ 加入）；README 技术栈行与差异清单更新。
- **冷启动粗测对照**（P2 承接）：改版前后各一次 `dev.sh 启动→窗口可交互` 秒表记录
  （±3s 内视为不劣化），入账。
- 验证：命令输出入账（phase-4 账本）。

## 执行分组（phase-3 并行依据，第 2 轮挑战重排）

**App.tsx 声明为单写者文件**：所有编辑 App.tsx 的任务（T3 前端面/T4/T6/T8/T13）必须在
同一执行序内串行，禁止跨 worktree 并行编辑（三方合并冲突 + 行号漂移必然发生）。

- 组 R（Rust+后端，串行）：T1→T2（含 SQL 迁移 007）→T3(Rust 面：conf/capabilities/
  commands.rs/lib.rs)→T5→T5b
- 组 F（前端主序，串行——App.tsx 单写者；**单一权威序列，第 7 轮合并表述**）：
  T3(前端面：main.tsx 路由分支 + App.tsx :523-558/:2229-2278 删除)→T6（壳 + petError
  横幅插槽 + 视图 stub/props 合同 + 旧浮层挂载拆除）→T4（删除面，行号以 T3/T6 落地后
  实际代码重新定位，不用 pre-T3 坐标盲删）→**T11**→T7→T8→T9→T13
- 组 V（视图页面化，T6 后可并行，不碰 App.tsx——挂载点由 T6 预留）：**T10‖T12**
  （T12 另依赖组 R 的 T5b）。T11 在组 F 序列内（见上，T4 之后；SettingsPanel.tsx
  单写者=T11）。
- 跨组显式依赖边：**T8 的真机 companion 复验依赖组 R 完成 T2**（controlWs.ts 由 T2
  独写无文件冲突，但验证必须在 T2 全链落地后执行）。
- 收口（串行）：T14→T15→T16
- petError 横幅：T6 交付插槽（WorkbenchShell 顶部 banner 区域 + props），T4 只做搬迁。
