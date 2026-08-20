# Simple Harness UI 当前架构

> 最后更新：2026-08-21（macOS App 专属数据目录与 Provider 持久化闭环）

## 0.4 macOS App 数据与 Provider 命名空间隔离

- Simple Harness 的 classic 用户数据目录与 Tauri bundle identifier 对齐为
  `com.dennywanye.simpleharness`；macOS 配置文件固定在
  `~/Library/Application Support/com.dennywanye.simpleharness/config.toml`。Rust supervisor
  把同一路径通过 `DESKPET_USER_DATA_DIR` 钉给 Python backend，设置页“当前生效/默认路径”显示
  同一事实。历史通用 `~/Library/Application Support/deskpet` 不再作为本 App 默认目录。
- Rust portable 判定要求安装目录已存在 `.deskpet-portable` sentinel。Cargo `target/debug` 和 macOS
  `.app/Contents/MacOS` 不会因为可写就自动创建邻接 `userdata`，因此重构/清理构建产物不再清空
  Provider 或分裂 SessionDB。
- 正常启动不再从通用 `deskpet/config.toml` 静默恢复 endpoint；空 registry 保持为空并要求用户明确
  配置。Provider API key 的新 Keychain service 为 `com.dennywanye.simpleharness`。升级时只按当前
  canonical config 中的精确 provider id 从旧 `deskpet` service 读取一次并复制到新 namespace，
  不枚举、不删除旧凭据，不影响其他应用。
- 2026-08-21 已把当前有效 DeepSeek profile 非破坏性复制到 App 专属目录。两次新 debug bundle
  启动及一次完整冷重启均只加载 `deepseeker-myself`；设置页只显示 `deepseeker官方`，默认模型
  `deepseek-v4-pro`，API key 为已保存状态，真实 `GET https://api.deepseek.com/models` 返回 200；
  chinzy 未复活。原始证据位于 ignored
  `.local-test-evidence/2026-08-21/provider-persistence-macos/`。

## 0.3 SDK 公开思考过程

- ChatView 保留 user/assistant/progress/tool 的 canonical Run identity；MessageStreamPanel 将同一
  Run 的公开工作叙述和工具调用/结果聚合为一个“思考过程”。优先显示 Provider 主动输出的公开
  assistant content；DeepSeek 工具回合的公开 content 为空时，Delivery 仅按公开 tool name 生成
  有界工作叙述，再展示工具卡。全局隐藏开关过滤后没有公开进度和工具时不渲染空组。
- starting/waiting/running 默认展开并显示递增耗时；completed/failed/cancelled 自动折叠，用户可
  反复展开/收起。最终 assistant 回复保持独立。历史 hydration 默认折叠；无 durable task
  projection 的 SDK Run 使用同 Run 用户消息到最终回复的边界，重启前后耗时不会缩短为工具区间。
- tool result 的 durable public envelope 保留 canonical
  `succeeded/failed/partial/rejected/unknown`；web 结果不再用 `status=completed` 覆盖真实失败。
  前端 hydration 按 outcome → legacy ok/status 的优先级恢复颜色与公开错误，未知值 fail closed。
- 展示面只接收模型主动公开的 assistant content、基于公开 tool name 的宿主工作叙述与既有工具公开摘要；Provider 私有
  `reasoning_content`、reasoning token、原始响应和 Chain-of-Thought 均不会进入 UI 或 Session。

2026-08-20 真机 DeepSeek 验证：运行中“思考中 · 5 秒”自动展开，`memory_recall`、
`memory_search` 和两次 `read_file` 全部成功；19 秒终态自动折叠、点击可复看，完整重启后仍为
19 秒且四组工具保持 `ok`。最终代码的真实 tool-only Run 验证公开叙述会出现在对应工具卡前，15 秒终态
折叠，完整重启后点击仍可恢复全部叙述与工具状态。原始证据位于 ignored
`.local-test-evidence/2026-08-20/`。

## 0.2 升级后真实 UI 回归收口

- 能力中心搜索或类别变化时，选中项同步切换为当前筛选结果的首项；右侧详情不会继续显示
  已被过滤掉的旧能力。`ppt` 真机筛选已验证列表首项与详情均为 `ppt_create`。
- 侧栏“更多”展开项使用完整的图标 + 文字行按钮，`记忆管理 / ContextTrace / 反馈问题`
  在 1000×700 默认窗口均可直接辨认，不再依赖 tooltip。
- 记忆管理 IPC 继续通过 control WS，但后端数据源以当前 `SessionDB` 为权威；旧
  `memory_store` 服务键只作兼容。加载完成后空会话显示“暂无对话记忆”，不会永久停在 `…`。
- 已知的 SDK Runtime/Provider 未就绪错误在 UI 映射为“设置 → LLM Providers”的恢复指引；
  未识别的 HTTP/Provider 诊断仍保留原文，技术细节继续写后台日志。
- 新 Provider 的隐藏 ID 由紧凑 UUID 派生为 `provider-<23 chars>`，满足后端 kebab-case 且
  最多 32 字符的契约；前端提交前仍做同一规则的防御校验，不再把 36 字符标准 UUID 送到后端。
- Provider 新增、编辑、删除和排序成功后，后端会关闭旧 SDK ingress、重建冻结的 Provider
  adapter 并重新开放 Companion ingress；首个 Provider 添加后可立即聊天，配置变更不再要求重启。
- SDK 每次 Run 前把冻结的 Provider adapter 与当前 Session 的 provider/model 绑定对齐，并在该
  Run 内串行锁定；标题栏选择 `deepseek-v4-flash` 时，真实出站不再继续使用默认
  `deepseek-v4-pro`。
- OpenAI-compatible 工具回合把 assistant `tool_calls` 持久化到 SDK Message metadata，下一轮
  还原为标准协议；DeepSeek 不再因“空 assistant + 孤立 tool result”返回 HTTP 400。没有显式
  项目绑定的 SDK 工具统一使用 `<user_data>/workspace`，相对 `write_file` 不再落到 backend cwd。
- SDK Run 的 capability snapshot 直接取 77 项产品工具目录，不再使用只含 60 项的 legacy v2
  registry；`agent`、`spawn_subagents`、`workflow_spawn` 等 17 项动态编排/上下文工具会进入真实
  Provider 请求。SDK EffectExecutor 的调用与结算复用现有 `RunPresenter` 投影，工具调用卡、结果卡
  和对应的 assistant/tool 协议消息会同时实时显示并持久化，历史会话重载后仍可恢复。
- OpenAI-compatible Provider 返回 HTTP 200 后若包含违反 SDK typed contract 的工具调用字段，
  Product adapter 会把该异常归一为确定的 `provider_protocol_error`，而不是让 coordinator 误记为
  outcome unknown 并把普通会话留在 `sdk_run_waiting`；其他意外异常仅记录无 payload/secret 的
  类型化诊断 breadcrumb。
- Tauri、Cargo 与前端包的产品版本统一为 `0.6.0-beta.9`，设置“关于与更新”读取到同一版本。

## 0.1 SDK Run 终态投影

消息页的运行开始、成功和失败事件统一按 Host canonical `root_run_id` 更新同一条
`run_projections`。SDK 内部 `product-sdk-*` ID 不直接暴露给 UI；因此 SDK 运行完成或失败后，
输入区和右下角状态会一致回到 `发送/空闲`，失败则显示可见的 `run_failed`，不会继续显示停止
或工具执行中。

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
- ChatView 标题栏与 SessionList 会话行中的完整 Session ID 使用 `user-select: all`；用户双击
  标识时会选中整个 ID，而不是只选中 UUID 连字符分隔的一段。文本仍保持省略显示且不触发
  会话切换。
- 设置页的数据目录分成“当前生效目录”与“下次启动目录”两个事实：Rust 端把偏好写入稳定的
  bootstrap pointer，下一次进程启动再切换，不会在当前进程中伪装已生效；外部
  `DESKPET_USER_DATA_DIR` 固定目录时明确拒绝 UI 改写。Agent 预算请求带 `request_id`，避免
  页面初始化读取响应与用户保存响应串台；Provider 删除必须经过确认对话框。
- 模型选择器连接后请求 Provider 的实时 `/models` 目录；成功结果既用于当次下拉列表，也经
  Provider Registry 原子刷新 `config.toml` 的模型缓存。缓存用于重启/网络失败 fallback，不会
  自动改变用户选择的默认模型，也不会因为目录同步让现有会话绑定失效。
- 模型身份提示由已解析的当前 Session Provider 生成 task-scoped Persona fragment；平台级
  cache-stable Persona 中的占位符不会暴露给模型。当前源码重启后，真实 UI 的精确模型询问
  回复 `kimi-k3`，后台同一 Run 也记录 `model=kimi-k3` 与 HTTP 200。
- 运行期 backend 不可用时，普通 Workbench 不再被启动失败全屏遮罩替换；App 保留主界面并
  显示 `RuntimeBackendBanner`，ChatView 和侧栏仍按两条控制连接的最差态 fail closed。空 secret
  与半开 WebSocket 握手均有有界失败和显式重试入口。
- 生产 Harness 的 prepared tool 结果会在 last-mile 开关启用时生成 artifact envelope；消息持久化
  标记 `artifact_card`，前端卡片动作只接受 Tauri 白名单内路径。第一方 file tool 的当前 Run
  workspace 纳入白名单，但没有开放任意文件系统路径。已有文件可通过只读
  `register_artifacts` 进入同一标准信封，无需创建旁路 JSON 或改动原文件。
- 权限弹窗只有 ChatView 一个生产订阅者；App 根层不再重复订阅同一 control channel。
  队列以 `decision_id`（旧事件回退 `request_id`）去重，成功回复后的 identity 进入有界 replay
  fence，因此同一已处理请求不会因服务端重放再次弹出。停止仍发送 Run interrupt，拒绝当前
  decision；取消 Host 的签发不要求 Provider/模型或 workspace 继续可用，所以失败 Run 上的停止
  不会因重复 provider preflight 把聊天连接击穿。应用退出时未终态 durable Run 保留给下次启动
  恢复，不伪装成已完成。点击“拒绝”会持久化 `decision=deny/status=denied`；生产 Driver 把对应
  control outcome 结算为 `authorization_denied`，不会再准备 delegate 或创建 child。当前 macOS
  真机 root `996390c79f1b5c03967f7f42dc408f29` 已验证界面、数据库与事件流三者一致。

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

- 2026-08-20 Computer Use 真实点击/输入回归：首次设置跳过、会话新建、模型参数、Harness
  观察、能力/操作视图、产物库刷新、记忆、ContextTrace、反馈、Provider 编辑取消、设置和真实
  发送失败路径均已覆盖。最终构建真机复测通过能力 `ppt` 详情同步、更多菜单文字、记忆空态、
  `v0.6.0-beta.9` 版本与 Provider 中文恢复指引。原始截图/日志仅保存在忽略目录
  `.local-test-evidence/2026-08-20/manual-computer-use-regression/`；截图 SHA-256：
  `04207049…d05f`、`30f60dab…15fa`、`348c8b66…ac45`。
- 2026-08-20 DeepSeek Provider 真机闭环：添加时生成
  `provider-c90fd2eb0f2349f99603b81`，保存无 `invalid provider id`；真实
  `POST https://api.deepseek.com/chat/completions` 两次返回 200。编辑显示名触发
  `product_sdk_runtime_provider_refresh_ready` 后无需重启继续回复；会话切到
  `deepseek-v4-flash` 后收到精确 `FLASH_MODEL_OK`，再次重启仍恢复 6 条消息和该模型绑定。
  本地截图索引 `.local-test-evidence/2026-08-20/provider-runtime-fix/chat-flash-model-pass.jpeg`，
  SHA-256 `9a32004d2d29a6bbf6906a0371dc4548fd244589125cf68563331f1f04592a0a`。
- 2026-08-20 DeepSeek Tool 最终干净会话闭环：Computer Use 坐标点击发送创建文件请求，账本
  `product-sdk-b28eb5…` 的四个 Provider turn 全部 succeeded，目标均为
  `deepseek-v4-flash`；`run_shell → write_file → read_file` 三个 effect 全部 succeeded，生成
  `<isolated-user-data>/workspace/deepseek-final-e2e.txt`，内容精确 `FINAL_TOOL_OK`（13 bytes，
  SHA-256 `9f370645eacf4c4380cea0b30029fdeeb38b858fdbcb48511dce642987c8e440`）。最终截图
  `.local-test-evidence/2026-08-20/provider-runtime-fix/final-deepseek-tool-e2e-pass.jpeg`，SHA-256
  `ca3b9bafa2da0e7adf14e96635cd250a43f0b497e5356d1da9763e2ebed3cfc4`。
- 2026-08-20 Agent 工具目录/消息投影真机闭环：隔离 Session
  `233737e1-acbe-4f02-b190-d9334eb262f2` 通过 Computer Use 真实点击发送并调用
  `tool_search(query="agent")`；UI 同时显示调用卡与 `✓ ok` 结果卡，DeepSeek 从当前请求直接工具表
  确认 `agent`、`spawn_subagents`、`workflow_spawn` 均可用。最新 Provider request 携带 77 项工具，
  effect 为 `succeeded`，SessionDB 持久化 assistant `tool_calls` 与 role=`tool` 结果。截图索引
  `.local-test-evidence/2026-08-20/provider-runtime-fix/agent-tools-visible-pass.jpeg`，SHA-256
  `37637ffdb3a430264856cd62d93a202873cffb87cc49ca310af4f3a3f22767c8`；相关聚焦自动化
  `71 passed`。
- 2026-08-20 `sdk_run_waiting` 修复复测：同一 Session 首次项目调查在 DeepSeek HTTP 200 后因
  adapter 异常被误分类为 unknown/waiting；修复后真实调查连续完成 5 个工具 effect 并输出完整
  结果，重启加载最终代码后再次真实调用 `agent_reach_read`，界面显示 `✓ ok`、
  `FINAL WAIT FIX OK` 并恢复空闲。最新 Run/Provider/effect 分别为
  `completed/succeeded/succeeded`；截图
  `.local-test-evidence/2026-08-20/provider-runtime-fix/sdk-run-waiting-fixed-pass.jpeg`，SHA-256
  `14b3185c2a5d49da445f243b6695024fe507829e9c29ea3352b1f841a7277afc`；聚焦自动化
  `72 passed`。
- 本轮工具/Provider/路径聚焦自动化 `62 passed`，另有 adapter/catalog/file 组合验证
  `56 passed`；Python compile PASS。
- 本轮聚焦自动化：Provider 前端 `36 passed` + TypeScript PASS；Provider IPC/SDK Runtime
  热刷新后端 `15 passed`；debug `.app`/DMG 重建 PASS。
- 聚焦自动化：Vitest `20 passed`，TypeScript PASS；window-control credential pytest
  `10 passed`，Python compile PASS；debug `.app`/DMG 构建 PASS。

- 2026-08-20 聚焦回归：SDK 多轮消息组装 `4 passed`；SessionList/ChatView `15 passed`；
  TypeScript `tsc -b --noEmit` PASS。该轮未执行真实桌面双击 E2E。

- 当前自动化：Vitest `540 passed`；Rust `79 passed`；companion `647 passed / 10 skipped`，以
  `backend/.venv/bin/python -m pytest backend/tests/companion -q` 从仓库根执行；TypeScript、
  Vite production build、`cargo check` 均 PASS。旧的 `cd backend && uv run pytest
  tests/companion/ -q` 会因 package root 不在 `sys.path` 收集失败，不再作为有效入口。
- 本轮 Harness 可靠性聚焦回归 `256 passed / 2 skipped`：覆盖既有文件 artifact 登记、
  ReceiptStore 缺席时的 refs 保留、分块 append、跨平台文件名、prepared effect、last-mile 与
  runtime recovery；前端全量 Vitest、TypeScript 与 Vite production build 均 PASS。
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
- 当前源码 K3 复杂任务 Run `04a477a3fbbb5e3eb2045e6b11006b55` 通过真 UI 创建并登记
  `harness-k3-artifact-check.txt`：只出现一次 `write_file` 权限确认，`register_artifacts`
  不弹写权限；两张 ArtifactCard 的 TextEdit 打开与 Finder 定位均通过，文件内容与 SHA-256
  对账一致。模型身份修复后的独立 Run `97f01117fd50506fbe10da0444577fbd` 在界面回复
  `kimi-k3`，与后台出站模型一致。
- fresh profile `.testenv/harness-reliability-20260813` 的真实复杂任务首个 child
  `caf7d550a7705da89cba6b731b9d1c4c`、child `child-2ec4cceeec4df4bb19531561ab0e1e31`
  已闭环：原生目录选择、一次 workflow 权限确认、7 项 unittest、CLI、自检、一次
  `register_artifacts`、5 张 ArtifactCard、9/9 completed 和空闲终态均可见。授权后约 1.2 秒
  状态从弹窗直接恢复“工具执行中”，未再残留“等待授权”。内部工具循环曾重复投影 5/9→6/9；
  当前后端按公开 node/attempt 去重，同 attempt 不再用私有 task id 制造重复阶段，相关回归
  `76 passed`。但该 root 随后误派两个验证 child，故不能把首个 child 的成功扩大成 root 一次收敛。
  后续 fresh profile root `f0a514f061cb56cebaa498a4a1447b24` 已用单一 durable child 真测
  双算法计算与三次 shell 回执，最终 `count=467/sum=234168/MATCH=True`，只有一个 child、零 verify
  nudge、零追加 spawn。终态观察面也已按 aggregate terminal 收束历史 running phase，显示
  “结果/记录已结束”；授权等待新增安全关联日志。历史取消态现在还会用父 root 的 terminal Session
  projection 收束 child workflow 卡，并合并同源 public trace；真机重启后只显示一张
  “已取消 / 6/9 / 67% / 耗时未记录”卡。父 root 终态仅收束仍未终结的 child 卡；child 消息已有
  明确终态时优先显示自身结果。Session `782f283d-0ac0-4016-b979-e6f79e7582f6` 重启复验中，首个
  child 保持“已完成”，第二个 provider failure child 正确显示“失败 / 5/9”，不再被父 root 的
  completed 覆盖。
- ProjectDirectoryCard 按所选路径识别平台分隔符：POSIX/macOS 使用 `/`，Windows 使用 `\`，并
  正确处理 `/` 根目录。macOS 真 UI 通过原生 Open sheet 选择 Desktop 后，确认卡显示
  `/Users/denny/Desktop/harness-path-test`；测试未点击创建，磁盘确认目标目录不存在。
- macOS 托盘已由用户在当前打包版现场确认：三项文案、隐藏/显示、托盘退出与非默认几何重启
  恢复均 PASS；托盘退出后独立检查主进程/backend/8100 零残留，当前版本重启日志与
  1100×750 截图确认几何恢复。托盘 UI 动作证据来源明确为用户现场手测，不伪造 Computer Use
  菜单截图。TC-WB-12 步骤 7 冷启动旧基线对照已由用户在 2026-08-12 明确移除，不再启动带
  Live2D 的历史提交。
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
