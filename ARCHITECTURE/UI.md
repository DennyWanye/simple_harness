# Simple Harness UI 当前架构

> 最后更新：2026-08-12（Workbench r15 修复后真机复测）

## 0. Workbench 工作台架构（2026-08-05 改版落地）

- 主窗为普通桌面窗口（系统标题栏，默认 1000×700，min 800×560，进 Dock/任务栏），
  桌宠渲染全链路（pet-anim/pet-engine/PetCanvas/petCharacter/petTransform）与
  message-panel 第二窗口已删除（acceptance「Workbench UI 改版」节，行为契约 B1-B13）。
- `tauri.conf.json`、HTML/CSS 与 React 挂载前背景均为不透明工作台口径；前端依赖锁不再
  包含 Live2D/Cubism/Pixi，后端也不再解析或广播角色表情/动作标签。
- 布局：App 层 `useState<WorkbenchView>` → `components/WorkbenchShell.tsx`
  （Sidebar 240px + 内容区）；四视图 `views/`：ChatView（常挂载，消息面板内容区迁入，
  含 Harness 巡检/模型切换/ContextRing/CompanionDetailModal/InputBar）、SkillsView、
  ArtifactsView（Rust `list_artifacts` command 数据源）、SettingsView（SettingsPanel
  page variant）。会话列表 `components/SessionList.tsx` 挂侧栏。
- 双控制连接保留：App ControlChannel（identity_bind）+ controlWs 单例
  （companion_action，label=main——五处硬编码已迁移：前端常量/Rust 白名单/
  Python 白名单/ingress 标签/companion.db 迁移 007）。连接徽章双源取最差态。
- 托管账户登录及其 `AuthAdapter`/登录注册事件/侧栏账户入口均已删除。identity_bind 直接
  使用 Rust 签名的本地 profile 快照；Provider/API Key 新手引导是本地配置，不是账户登录。
- message-panel/ 目录退役，公共件迁 `src/chat/`（sessionHydration/topicTitle/
  messageVisibility/HarnessInspectorPanel/HarnessRunGraph/projectDirectoryState）。

## 1. 主题事实

DeskPet 的用户界面现在以暗色为默认外观。主窗口背景、功能面板、弹窗、输入框、卡片、
按钮、标签页和遮罩统一从 `tauri-app/src/theme/tokens.ts` 与
`tauri-app/src/theme/components.ts` 取得语义化颜色和组件样式。

简单说，页面不再各自决定“这里用白色还是黑色”，而是共同使用同一套暗色颜料。页面只需
说明这里是“面板”“卡片”或“次要文字”，主题层负责给出实际颜色。

## 2. 当前覆盖范围

- 主窗为普通工作台窗口（见 §0）；聊天/Harness 观察区即 ChatView，无独立消息窗。
- Memory、ContextTrace 和 Context usage 保留原有暗色布局。
- 设置、Provider、新手引导、能力中心、Skill Store 和反馈页已统一为暗色。
- 授权、澄清、外部等待和审批中心等共享弹窗使用同一套暗色面板、遮罩和控件。
- 设置页不再展示已退休的 Harness Supervisor 与自动恢复开关；恢复由当前事件驱动的
  Harness 生产链路负责，不再给用户一个已经失效的旧入口。
- 原“对话超时”设置改名为“Agent 有效执行预算”，并明确说明等待确认、文件夹选择和外部
  操作时暂停；兼容读取原 `chat_turn_timeout_minutes` 持久化键。
- 模型选择弹窗在真实 Provider 模型目录上提供即时文本筛选；输入模型 id 或展示名称的
  任意片段即可收窄下拉选项，无匹配时明确显示空结果。模型选择仍由用户在筛选结果里确认，
  不会因为筛选文本自动改写当前 Session 绑定。
- 标题栏展示的当前模型来自 Session 持久化绑定。新话题继承来源 Session 的模型与参数，
  历史会话和应用重启通过独立 hydration 请求重新加载绑定，不再短暂或永久回退到 Provider
  默认模型。
- 设置页的数据目录分成“当前生效目录”与“下次启动目录”两个事实：Rust 端把偏好写入稳定的
  bootstrap pointer，下一次进程启动再切换，不会在当前进程中伪装已生效；外部
  `DESKPET_USER_DATA_DIR` 固定目录时明确拒绝 UI 改写。Agent 预算请求带 `request_id`，避免
  页面初始化读取响应与用户保存响应串台；Provider 删除必须经过确认对话框。
- 运行期 backend 不可用时，普通 Workbench 不再被启动失败全屏遮罩替换；App 保留主界面并
  显示 `RuntimeBackendBanner`，ChatView 和侧栏仍按两条控制连接的最差态 fail closed。空 secret
  与半开 WebSocket 握手均有有界失败和显式重试入口。
- 生产 Harness 的 prepared tool 结果会在 last-mile 开关启用时生成 artifact envelope；消息持久化
  标记 `artifact_card`，前端卡片动作只接受 Tauri 白名单内路径。第一方 file tool 的当前 Run
  workspace 纳入白名单，但没有开放任意文件系统路径。

## 3. 边界

- 主题层只负责视觉样式，不拥有业务状态、Harness 状态或权限决策。
- 隐藏的 provider reasoning 不会因为 UI 改造而展示；消息页只显示用户可见的公开执行进度。
- 语音按钮当前明确禁用，等待后续 Realtime 接入，不会偷偷启用旧语音链路。
- 新页面应优先复用语义化 token 和共享组件样式，避免重新写独立的纯白背景。
- 左侧运行图、消息流 Agent activity 和右侧 durable steps 现在按 `(session_id, root_run_id)`
  订阅同一个 `HarnessPublicSnapshotStore`。默认图直接渲染后端 semantic phases，不再从 raw
  activity record 猜阶段；每阶段可独立折叠，当前阶段默认展开。
- 工具在所属阶段内以一行名称/动作/状态/耗时显示；输入按需展开，结果第二层展开且默认收起。
  UI 只读取 default-deny 的 public input/result；v2 或未知 schema 只显示最小安全 fallback。
- snapshot 使用 response-driven 1.2 秒 singleflight 轮询；切换 Session/Run 或关闭 WS 会取消旧
  root task，完整 terminal 后停止轮询，details 使用独立 slot/cursor。晚响应按 request id 丢弃。
- Context Usage 的 measured/compacted/binding-only 状态来自同一 durable reducer；无样本时显示
  Session 绑定模型与“尚无用量”，不再短暂显示全局默认模型。

## 4. 验证状态

- 当前自动化：Vitest `539 passed`；Rust `79 passed`；companion `647 passed / 10 skipped`，以
  `backend/.venv/bin/python -m pytest backend/tests/companion -q` 从仓库根执行；TypeScript、
  Vite production build、`cargo check` 均 PASS。旧的 `cd backend && uv run pytest
  tests/companion/ -q` 会因 package root 不在 `sys.path` 收集失败，不再作为有效入口。
- Workbench 主题验收已固化为
  `python3 scripts/acceptance/workbench_ui_theme_audit.py`，扫描 11 个工作台自有文件，
  当前零字面量 hex 色值。
- 当前源码 Windows 真机：设置页显示“Agent 有效执行预算”及暂停说明；临时设为 1 分钟后，
  Godot 项目目录选择卡片等待超过 2 分钟仍保持“需要你确认”，验收后已恢复 15 分钟。
- 当前源码 macOS 实机：普通单窗 Workbench 在 800×560 下完成 30 会话、80 字符长标题、
  30 产物与四视图切换；会话删除即时态/重启/删至零/空态新建全部通过。设置页 Provider、
  预算、数据目录、自启均完成修改→重启保持→恢复原值→无残留闭环。Kimi3 真链路创建文件后，
  ArtifactCard 的打开与 Finder 定位均通过；运行期 backend 故障恢复后，Kimi3 HTTP 200 并收到
  `S13 恢复成功`。红钮与 Cmd+Q 两条退出路径均全清 backend/8100 并恢复窗口几何。
- macOS 托盘仍是本轮唯一 UI 能力阻断：Computer Use 无法附着 SystemUIServer/ControlCenter，
  因此 TC-WB-10 步骤 1～4、TC-WB-14 与 TC-WB-16 托盘路径不得判 PASS。TC-WB-12 步骤 7
  冷启动旧基线对照已由用户在 2026-08-12 明确移除，不再启动带 Live2D 的历史提交。
- 已退役的单钥匙 Keychain 模块、renderer IPC/TypeScript binding 与 Rust `keyring` 依赖均已
  移除；Tauri launcher 不会在 backend spawn 时读取任何 legacy 单钥匙槽。Provider 凭据由
  backend registry 按需解析，避免 macOS 启动或打开设置页弹 Keychain 授权框。显式开发环境
  `DESKPET_CLOUD_API_KEY` 仍可由子进程继承。
- 后端故障注入验证了未连接状态条、侧栏最差态、明确发送失败与重试入口；并修复
  supervisor 在首次 respawn 遇端口占用后永久退出的问题，现会按 2 秒间隔最多重试 5 次，
  故障释放后可自动恢复连接。
- 运行链路检查：backend `/health=200`、Vite `200`，embedding worker 存活。
- 模型筛选与恢复聚焦回归：前端 `3 files / 33 tests passed`，TypeScript PASS；Windows
  实机输入 `kimi` 后目录只显示 Kimi 系列，选择 `kimi-k3`、新建话题并重启后，标题栏均保持
  `kimi-k3`。
- Session/run visibility 最终验证：跨会话 full-surface 前端 `8 files / 116 tests passed`；
  S-SRV-1～S-SRV-5 Windows 真机矩阵全部 PASS。长上下文任务按阶段显示且工具归属正确，
  child 失败时顶层明确显示“主 Agent 已接管并完成”，停止后的晚到结果不会恢复运行态或污染
  另一 Session。
