# Simple Harness UI 当前架构

## 2026-09-06 Primary Cytoscape viewport repair

Last updated: 2026-09-06. Three frontend files address the reproduced half-height
scroll-pane clipping: responsive canvas, first/explicit reveal, wheel page scroll
with button zoom. Layout-only reveal state survives owner-key graph remount; graph
authority/invalidations are unchanged. Actual WebKit 1000x700/800x560 oracle:
original 8 failures, candidate22 checks pass; focused frontend9 pass/1 optional
API-fixture skip, typecheck/build/ESLint pass. Native exact-build verification is
coordinator-owned and pending; not a renderer-engine diagnosis or full HM-AC6 PASS.
Dirac fixed-byte review of c9907e14: scoped ACCEPT, no P0/P1; eight evidence hashes verified.
Isolated `feat/cytoscape-native-canvas` from65a604f8, not yet integrated here.
See [scoped result and evidence](../plans/2026-09-06-cytoscape-viewport/RESULTS.md).

## 2026-09-05 Native graph blocked by SDK credential false positive

Last updated: 2026-09-05. Actual21c55cf9/native18120 with installedMemory0610
created the requested preference, then public history rejected terminal tool names
as credentials. Memory controls became unavailable before graph interaction.
Native acceptance remains FAIL; successor SDK repair and rerun are pending.
See [native evidence and boundaries](../plans/2026-09-05-s6-cytoscape-display/NATIVE-0610-BLOCKER.md).

## 2026-09-05 Cytoscape primary graph display source candidate

Last updated: 2026-09-05. PrimaryMemoryPanel adds a default-available graph tab over verified HUMAN primary.memory.graph. Real canonical nodes/relations only; local Cytoscape3.34.2 with keyboard/text selection, filter, zoom, pan and details. Pending/unknown forget, owner change and real completion hints invalidate old data. API-fixture browser renderer verification passes; coordinator native verification remains pending.
See [scoped results](../plans/2026-09-05-s6-cytoscape-display/RESULTS.md) and [contract](../plans/2026-09-05-s6-cytoscape-display/CONTRACT.md).

## 2026-09-05 Primary cognitive panel connected locally

默认主对话入口已接认知记忆面板及真实HUMAN API；current signed owner限制读写，
同owner隐藏/重挂载保留未决动作，换owner清空。匹配forget ACK同步清历史/detail再补读。
父视图组合24项、tsc及定向lint通过；backend已独立限定ACCEPT。尚未native真测，
完整进程重启不保留UI内存动作ID；自然语言纠正/全闭环仍待完成。
见[组合记录](../plans/2026-09-05-cognitive-controls/COMBINED.md)。


> 最后更新：2026-09-05（认知面板与主对话接线，native待验收）

## 2026-09-05 Cognitive controls frontend leaf

新增独立 PrimaryMemoryPanel 与 CognitiveRequests，复用 bound HUMAN 通道；页面显示记忆
内容，真实 memory_id/revision 仅用于请求，使用 primary_ref/action_id/status=applied 契约。父组件保留 requests
实例可跨隐藏/重挂载保存未知动作；同 owner 显式重试原 payload/key，verified owner 更换
清空。匹配 ACK 后 onForgotten 先同步通知主层撤下 history/detail，再补读记忆。
13 项前端聚焦、tsc、定向 eslint 通过。backend/PrimaryChatView 组合与 native 验收由主负责，
不是已接通的生产忘记闭环；图只用于 USER 显示，不进入 Agent。接口、命令和 ignored
证据索引见 [前端契约](../plans/2026-09-05-cognitive-controls/FRONTEND-CONTRACT.md)。

## 2026-09-05 Primary 授权布局后继候选

从 `1862e383` 小改：Primary PermissionPopup portal 至 body，避开运行区域的滚动与
containing block；Workbench 显式传当前 chat 可见性，隐藏时撤下 popup 及其 Escape handler，
运行订阅和未决授权保持原样。旧 ChatView 的 popup 默认仍按原位置挂载。未改授权默认、
exact target、未知 ACK 或自动重试规则。

真实 WebKit 26.5 浏览器加载实际 Workbench/Primary/PermissionPopup/CSS，仅替换网络边界：
1000×700、800×560 两套布局场景通过，含参数展开、滚动、含 transform 容器压力控制、
补读/重挂载/重连、切页和后继授权；截图与真实鼠标命中均检查。修前压力控制按钮越出视口、
隐藏视图 Escape 否决分别实测红，修后绿；未加 transform 的浏览器基线没有复现 native
消失，故不能将压力控制等同 native 根因证明。前端59、backend decision18、tsc/lint 通过。
原生 `dfdaec4a` 的视觉 FAIL 保留，主负责新 candidate 的可见鼠标点击复验；本片不运行 Provider。
命令/原始证据索引和hash见 [RESULTS](../plans/2026-09-05-primary-sdk-decisions/RESULTS.md#portal-layout-candidate-after-1862e383)。

## 2026-09-05 Primary 精确 SDK 授权独立候选

`feat/human-memory-primary-decisions`（base `5da24d6f`）接入 authenticated HUMAN
`primary.decisions.list/respond`。主对话权限卡通过 App 已 bound 的 ControlChannel
读取实际当前 Run 的 SDK decision；不使用 secondary controlWS 的旧权限补读/ACK。
提交前重验当前 Host run/generation 与连接 scope，复用 SDK exact decision API；旧未认证
permission_response 对 Primary binding 拒绝。UI 仅允许本次 allow/deny，区分 expired，
超时不自动重发，重挂载/重连/通知补读，不把 Primary ID 当 Session ID。

真实生产授权策略 + installed SDK + SQLite + signed HUMAN scope + 实际 scheduler wake
的确定性 fixture 完成 challenge→批准→项目文件 effect→终态；受影响 backend 66 passed，
最终新增聚焦 18 passed，前端后继 36 passed + typecheck。批准后并发补读不能吞超时错误，已补红绿；独立 review 待完成；
未起 native/真实 Provider，不是 S6/program PASS。Carver 的 WAITING 通知须另行组合。
SDK read_decision 是 public port，但旧 open-decision 列表仍是 Host 内部 SDK SQL；本片未扩
私有 SQL，仅限制返回最多32，不能声称底层扫描有界。停止结果历史缺口维持独立未闭合。
详见 [契约与测试边界](../plans/2026-09-05-primary-sdk-decisions/CONTRACT.md)。

## 2026-09-05 隔离组合原生观察

Host `87c42b43` / SimpleHarness Primary P18120 的真实原生输入已得到 gpt-5.5 回复，
草稿清空、队列回空闲、TaskScope=0。正常退出重启看到两条消息恢复，账本无已完成调用重发。
解锁后第二轮追问已通过且实际出站包含原用户/助手历史；新项目因控制工具注册缺失失败，
目录和路由指引修复后，`5da24d6f` 已实际到达 SDK 工具授权等待；Primary 未显示该授权卡，
因此新建项目仍失败。原生停止该等待后 Host STOPPED / SDK cancelled / 授权 cancelled，界面回空闲。
尚未显示历史停止原因，批准/拒绝/过期恢复与 Manual binding 仍待接线，不能据此声明完整遗忘或 S6 完成。
固定候选、失败启动记录和本地证据哈希见 [INTEGRATION](../plans/2026-09-05-s6-primary-preparation/INTEGRATION.md)。

## 2026-09-05 工具活动调用关联修复

真实 `ProductDeliveryAdapter` → presenter → WebSocket 的 `tool_call` 补齐 SDK
`call_id`，与既有 `tool_result` 一致；PrimaryRunPanel 才能关联执行中/返回两种状态。
既有真实 SessionDB 投影用例修前因缺字段失败，修后 delivery 文件 **14 passed**。
仅为事件契约验证，尚未原生工具操作验收。原始日志在该隔离树 ignored
`.local-test-evidence/2026-09-05/primary-tool-identity/`：red SHA-256
`558cf677e0abb183892a374aa992dffa8190da30c32872f1c42d14238f6bc09e`，green
`f374d45aa1a832cf5c888b78fa022f02ac39cdea4b2c0a2fd154f44805163a71`。
命令：`PYTHONPATH=$PWD/backend /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/test_product_delivery_adapter.py -q -p no:cacheprovider`。

## 2026-09-05 单主对话前端候选（尚非完整 S6 cutover）

`feat/human-memory-primary-ui` 从 Host `c183fe70` 建立独立树，仅修改前端与本节/PROJECT_STATUS。
Workbench 默认入口为 `PrimaryChatView`，Sidebar 不再挂载 Session catalog/CRUD；原 ChatView/SessionList
保留历史代码与测试，不由 Workbench 生产路由挂载。设置、技能中心、产物库入口保留。

主对话复用 App 已有 `ControlChannel` 的 signed `companion_profile_bind` 连接，只有当前连接真实 bound
才读取 `primary.open/state/messages.page`；global identity_status 不作为本 socket 授权。晚挂载复用当前
连接 bound cache；disconnect/unbind/rechallenge 清 cache。未知 enqueue 的 delivery_key 在同 owner
rechallenge/重连时保留，只有重新 bound 确认 owner 改变才丢弃。无 `chat_v2` fallback 或伪造 Session ID。

delivery 在内存中累计 ACK 未知状态：一次超时后，即使后续同 key 重试明确未发出或被拒绝，也不能
推断最初未入库，仍保留原 key，直至匹配的有效 ACK 或 verified owner 变化。首次明确未发出仍允许
修改草稿重新提交；该本地状态不写入 wire DTO。三个决定性用例修前 2 failed/1 passed，修后均通过，
controller/requests 聚焦 25 passed、typecheck/受影响文件 lint 通过；原始证据在 ignored
`.local-test-evidence/2026-09-05/primary-ui-retry/`。Dirac 独立固定源码复核 ACCEPT：原重试探针、
29 项聚焦测试及 verified owner 切换负控制通过；未执行原生 UI/Provider 复验。

输入框仅在 request_id/operation/delivery_key 关联的完整 enqueue receipt 后清草稿；拒绝、超时、断连
保留草稿，无自动 mutation 重发。停止固定按钮渲染时的 Host run_ref/generation，并核验响应目标、
receipt 与 outcome；拒绝、过期、superseded、already_terminal 不当作控制已受理。

历史为服务端过滤的非流式 durable page：默认扫描十 turns、前端每页最多20消息，preview1024 Unicode
codepoints；详情每次4096 codepoints，逐段替换。opaque revision 不是 privacy epoch，不跳过服务端
suppression 检查；`human_memory_changed` 立即清页/详情、取消旧 read，再补读，保留在途 queue ACK。
有界兜底最多12次，真实事件/focus/手动刷新恢复预算；耗尽不伪报运行失败。队列 truncated 显示“至少 N”。

权限 popup 和 bounded 工具活动按 Host 返回的真实 `execution_session_ref` + `sdk_run_ref` 关联，
权限补读指定 execution session，保留原 request/decision/nonce/version；不将 primary_ref 代成旧 sid。
模型入口进入当前 foreground 使用的全局 Provider 设置，不发旧 `session_set_model`。附件选择 UI 保留，
queue 附件尚未支持时明确拒绝发送并保留草稿/附件；slash 命令同样明确提示尚未接通。

**项目交互/恢复未验证**：旧 `project_directory_request` 卡为 live-only，若生产使用该路径，事件先于
mapping、同 Run 重挂载或重连会丢卡（条件性 P1）。Carver 源码接线确认动态 `context_route.create_new` 不触发该卡；Manual append 只返回
`context_route_binding_authorization_required`。真实待接路径是 `binding.manual.propose(scope_ref,root)`
→ `binding.manual.decide(challenge_ref,decision)` → `route.resume_existing`。主协调已暂停旧目录
pending API 扩展；以上 Manual/route UI 尚未实现或真测。当前目录卡仅显示“已提交，等待运行确认”，
不把后端 logger 当成功 ACK。TaskScope/Context Inspector、完整 Artifact 卡、primary slash/附件/
Realtime 等保留项也仍未接完。

验证：聚焦前端100 passed + PrimaryRunPanel 实际权限组件集成1 passed；typecheck 通过。
新增 primary 源文件 lint 通过；受影响 InputBar 的 `react-refresh/only-export-components` 单项红在
`c183fe70` 原文件复现（旧 `_testing` 导出），未放宽规则。原始日志在 ignored
`.local-test-evidence/2026-09-05/primary-ui/`，测试契约见
[`UI-CONTRACT.md`](../tauri-app/src/primary/UI-CONTRACT.md)。未起 App/Provider，未改 backend/SDK，
不声称首价值真测通过、完整 S6 cutover 或 program/full-audit gate PASS。

## 1.0 Project-scoped Capability Center（2026-08-29）

- Skills 视图把当前 `session_id` 传给 Capability Center；后端不信任前端给出的 Project/scope，而是从
  durable Session binding 重建可信 Project identity，再查询 exact `owner_key=sdk-runtime` + Project scope。
  Session 切换会重新加载目录，projectless 或 workspace 不可用时不会回退到全局 managed Skill。
- 安装授权仍是 action-time 用户决定：Agent 可以自主操作其余开发 UI，但不能替用户点击外部 GitHub 代码的
  “允许一次/始终允许”。拒绝、过期或重启后的决定由同一 durable request/nonce 结算，不应重复弹出，也不应
  出现 `Decision was not found`。
- 隔离 macOS debug App 的真实安装完成后，Capability 数量从全局 125 变为当前 Project 的 128；列表和详情
  显示 `plan-bs`、`plan-task`、`plan-test` 均为“健康”，版本
  `0.0.0+git.4d8c803ba03b`，详情 Scope 为 `project` 并展示 exact source commit、64 个文件 hash 与 manifest
  SHA。该 UI 证据证明当前 Project 可见性，不外推尚未执行的跨 Project/full-surface 矩阵。
- 消息 InputBar 不再使用模块级永久命令缓存。挂载/Session 切换会按当前 `session_id` 拉取命令，输入 `/`
  打开下拉时再次刷新，因此刚安装的 Project Skill 立即可见。当前 exact debug App 已真点击 Project 会话并
  输入 `/plan-`，下拉同时显示 `plan-bs`、`plan-task`、`plan-test`；输入未发送，原始截图留在 ignored
  `.local-test-evidence/2026-08-29/project-skill-slash-catalog/`。
2026-08-29 安装收敛复测：统一能力中心不再用空 user scope。当前源码 bundle 冷启动后显示能力 130 项，
搜索 `plan-` 精确显示全局安装的 `plan-bs/plan-task/plan-test`，兼容“旧 Skill Store”的已安装页也显示相同
三项。新建后的第二个普通 Session 输入 `/plan-` 同时出现三个命令；当前授权栏为 Auto，模型为
`deepseek-v4-flash`。原始截图保存在 ignored
`.local-test-evidence/2026-08-29/auto-skill-install-current-build/`。

## 0.9 Project-scoped Session UI（2026-08-25）

- `SessionList` 消费 bounded Project/Session catalog，按 Project 分组并保留独立“无项目会话”区；空 Session
  在创建后立即可见，重启后也由 durable catalog 恢复。每个 Project 分组提供“新建同项目 Session”，顶部
  分别提供“新建普通会话”和“添加项目”。既有项目 Session 没有换 Project/改 root 的入口。
- `ProjectPickerDialog` 复用系统目录选择器，但选择结果先交给后端 strict resolve/stat/Git-root preview。
  Git 子目录默认预览并绑定仓库根，用户可显式把所选子目录注册为独立 Project；非 Git 目录保持自身。
  前端只提交创建 intent/project id，不把显示路径直接提升为工具 authority。
- `ProjectInspector` 在宽屏为只读 rail、窄屏为可展开条，显示 Project 名、`project_root`、必要时单独显示
  `execution_root`、Git 状态，并提供复制路径、打开目录和新建同项目 Session。目录缺失时才提供受控 relocation；
  relocation 更新 Project 路径，不修改当前或其他 Session 的 immutable binding。
- projectless Session 明确显示“普通会话”和“在项目中继续”；后者创建新的项目 Session 与 bounded handoff，
  原 Session 仍留在无项目区。Session Store 保存后端返回的 typed project/binding view model，不再使用历史
  `project_root/project_name` 假字段猜归属。
- macOS 当前 debug `.app` 真人验证了 Git 子目录预览提升、Project 分组、同项目新建、projectless 分区、
  Inspector 与完整进程重启后零消息 Session/绑定恢复。另一个隔离 Session 通过真实
  `deepseek-v4-flash` 模型调用 `builtin:run_shell/read_file/write_file`：绑定根内成功，`../unrelated`
  读写均被拒绝且无越界落盘。原始截图分别在 ignored
  `.local-test-evidence/2026-08-25/project-scoped-sessions-ui/current-debug-bundle/` 和
  `.local-test-evidence/2026-08-25/project-scoped-sessions-real-model/`；Windows 已移为后续非阻断工作。
- 2026-08-26 当前 debug `.app` 又以 v32 旧数据 fixture 验证 v33 一次性重置：首次启动先清空旧
  Project/Session/消息及其 Run/上下文/投影数据，侧栏显示空状态；全局 Provider 配置、Keychain 凭据引用和
  磁盘项目文件保持。随后从 UI 新建 `real-project` Session，真实 `deepseek-v4-flash` 返回
  `V33-RESET-OK`；完整 app/backend 重启后新 Project、Session、消息和 context usage 均恢复。相同 Session
  还真实执行相对路径 `pwd`、`cat preserve.txt` 和测试文件写入，cwd 与落盘位置都只在绑定目录。
  原始证据位于 ignored `.local-test-evidence/2026-08-26/run-20260826-v33-reset/s-ps-08-bundle/`。
- 2026-08-26 当前 debug `.app` 进一步完成 missing-root relocation 全链：目录缺失后两个 Session/历史仍可见，
  开发请求显示友好阻断且不创建 Provider/Tool effect；无关目录被 `project_identity_mismatch` 拒绝；活跃 Run
  期间不提供 relocation，重启恢复后该 Run 以 `workspace_unavailable` 终止；同身份目录恢复后两个 Session ID、
  历史和只读 Inspector 均跨完整重启保持。`SessionList` 还修复了冷启动响应早于 listener 安装造成的偶发侧栏
  空白竞态。原始证据位于 ignored `.local-test-evidence/2026-08-26/project-scoped-sessions-missing-root/`。
  其余冻结 UI 场景已于 2026-08-27 在同一最终 gate 中补齐，macOS release UI DoD 为 PASS。
- TC-PS-06 随后用 `execution_kind=explicit` 的公开 fixture 真测 `project_root != execution_root`：Session 在
  左侧始终归入 `project-root` 分组，展开 Inspector 同时显示两个不同绝对路径；三个真实
  `deepseek-v4-flash` root Run（含完整 app/backend 重启后一次）均以 execution root 为 `pwd`，只在那里
  读取 canary 和写入输出。Project-only canary 的相对读取返回不存在，Project root 无新增文件，也未自动
  创建、发现或删除 worktree。原始证据位于 ignored
  `.local-test-evidence/2026-08-26/project-scoped-sessions-root-split/`。
- 当前 debug `.app` 还真实验证了授权模式切换：关闭“Agent 全开模式”后，search、describe、activate 与最终
  `write_file` 逐项弹窗，点击允许后写入成功；重新开启后，同一链路无弹窗成功。两轮模型都复制唯一的顶层
  `schema_hash`，最终文件只位于当前 Project root。原始截图留在 ignored
  `.local-test-evidence/2026-08-25/project-scoped-sessions-manual-activation-fix/`。
- 2026-08-29 当前 macOS `.app` 在用户选择目录 `/Users/denny/projects/生成视频` 新建 Session
  `21030143…` 后，输入 `/plan-` 即显示 user-global `plan-bs/plan-task/plan-test`。候选不再依赖页面启动时的
  永久缓存；每次开始新的 `/` 输入都会刷新后端同一 global catalog。点击 `/plan-test` 后 slash accepted
  receipt、正文加载、真实 `deepseek-v4-flash` Provider Run 与终态消息均回到同一 Session。截图和日志位于
  ignored `.local-test-evidence/2026-08-29/global-skills-default-workspace/`。
- 2026-08-27 最终 gate 补齐 S-PS-01～S-PS-08：当前 debug `.app` 完成 Project 注册、同项目新建、
  projectless bounded handoff、侧栏/Inspector/复制/打开/Finder、缺失根与同身份 relocation、v33 全新安装式
  重置以及完整重启恢复。最新真实模型 Run `c2a0022…` 在 relocation 后仍以绑定目录执行 `pwd`、读和写；
  distinct execution-root fixture 的三个当前 Run 只使用 execution root。project-bound Run 不再暴露进程级
  `mcp:filesystem`，避免其固定根绕过 Session authority；非文件 MCP 不受影响。Windows 不在本轮范围。
- 最终独立审计补测真实点击了外部非 Git 目录注册、删除目录错误态、新建同项目 Session、复制路径、Finder
  打开和窄屏 Inspector。后端同时把 Session rename 纳入 catalog revision fence；500 × 200 fixture 在分页间
  新增、删除、重命名后会拒绝旧游标，刷新后的 200 条结果无重复、遗漏或误归组。

## 0.8 Provider 模型目录刷新（2026-08-24）

- 会话模型选择器的目录仍以 backend `models_list_response` 为唯一权威，不复用设置弹窗临时探测结果、
  不维护第二份硬编码模型列表。每次打开“模型与参数”弹窗都会重新请求当前 Provider 链的目录，响应到达后
  共享 `sessionModelsStore` 会驱动已打开的下拉框即时更新。
- Provider 新增、编辑、删除或重排后的 `providers_changed` 权威广播会同步触发目录刷新，避免控制通道初次
  连接时 Provider 尚为空而把空 catalog 缓存到整个 socket 生命周期。弹窗按需刷新作为断线重连、跨页面
  和历史缓存遗漏的补充恢复路径。
- 中转站目录暂时只有 model id、没有结构化上下文能力元数据，因此所有具备 Context 能力的模型统一开放
  128K / 256K / 512K / 1M 四个用户预算档位，默认选择 256K；图片、Embedding、语音等非 Context 模型
  不显示。该值控制产品预算与压缩边界，不宣称 Provider 原生上限；Provider 拒绝超限请求的风险仍存在。
- 消息页模型按钮只展示当前实际生效的模型名称（可附上下文长度），不展示“默认模型”“跟随 Provider”或
  “Global Chain”等绑定实现概念。解析顺序为会话显式模型、最近实际运行模型、当前会话 Provider 的默认模型、
  未固定 Provider 时的当前链目录默认模型；弹窗的空绑定选项同样直接使用实际模型名，并去除重复同名项。
  头部不再展示面向开发者的 Session ID 标签；空会话时也不会残留只有边框和 padding 的孤立短横杠。
- 自动化证据：Provider 设置既有聚焦回归 `23 passed`；临时上下文策略 backend `21 passed`、
  模型按钮/弹窗/Provider 联合回归 `30 passed`；TypeScript `tsc -b --noEmit` PASS。当前源码 debug `.app`
  的 macOS Computer Use 真测确认：冷启动顶部显示 `gpt-5.6-sol`，弹窗显示同名模型与默认 256K；筛选
  `gpt-5.4` 后可真实选择 `gpt-5.4 · OpenAI`，取消后顶部恢复 `gpt-5.6-sol`。原始截图仅保存在 ignored
  `.local-test-evidence/2026-08-24/model-display-real-ui/`。Session ID 标签移除另经重新构建的当前 debug `.app`
  冷启动截图确认，证据在 ignored `.local-test-evidence/2026-08-24/header-empty-session-id-removal/`。

## 0.7 Provider 设置可靠性与 SDK 冷启动（2026-08-21）

- Provider 新增/编辑不再把“WebSocket 已排队/调用过 send”误当作保存成功：控制通道未连接或底层
  `send()` 失败时弹窗保持打开并就地显示错误；发送成功后也要等后端
  `settings_providers_added/settings_providers_updated/providers_changed` 权威确认才关闭，保存期间按钮显示
  “保存中…”。因此后端拒绝或断线不会再造成“弹窗消失但列表没有 Provider”的假成功。
- 模型自动获取有 20 秒 UI 超时，断线和无响应都会恢复按钮并给出明确错误。编辑已保存 Provider 时前端
  只发送 `provider_id + base_url`，后端仅在 URL 与该 Provider 的冻结配置精确匹配时从 App 专属
  Keychain 解析密钥；密钥不回传前端，也不会被错误发送到修改后的 URL。
- SDK Runtime 冷启动会先把递归冻结的 Tool schema 完整 `thaw_json` 后再计算 catalog 指纹；
  `sdk_runtime_catalog/provider_binding/tool_authority/tool_inventory/prepared_authorization_policy` 均是
  `ServiceContext` 正式槽位。真机隔离启动已验证 `product_sdk_runtime_ready`、SDK `0.1.5`、
  `phase=open`，不再因嵌套 `mappingproxy` 或未声明服务槽位降级为“模型服务尚未就绪”。
- 2026-08-21 macOS Computer Use 复测：已保存 DeepSeek Provider 的 `/models` 返回 HTTP 200，按钮退出
  “获取中”；新增无敏感信息的本地 fixture 后列表立即出现 `Local Fixture`。原始截图/日志位于忽略目录
  `.local-test-evidence/2026-08-21/sdk-context-authority-run-2-cold/evidence/`。
- SDK Tool 授权现在由桌面 ingress 从 durable open decision 生成既有 `permission_request`；实时
  WAITING 与控制通道重连都复用同一 fenced `decision_id/nonce/version`，前端批准后走 SDK
  `decide_authorization` 恢复原 Run。Computer Use 使用新 `DeepSeeker/deepseek-v4-flash` 完成真实
  `chat/completions → run_shell(pwd) → 允许一次 → tool result → 第二次 chat/completions → done`，
  终态思考组自动折叠、Context usage 显示 18k/800k。最终截图仅保存在 ignored
  `.local-test-evidence/2026-08-21/deepseeker-provider-tool-e2e/final-pass.png`。

## 0.6 Context Inspector 单一事实源（2026-08-21）

- 前台 SDK Run 只准备一次冻结 Context snapshot；同一份 snapshot 驱动 RunStart、首个物理 Provider
  request 与 Inspector 的公开构成。Persona、owner-scoped Memory、已选择 Skill、conversation
  allowlist 历史、附件、项目/任务快照与冻结 Tool catalog 均在该边界组装；普通文本没有可信 Skill
  selection 时明确为空，不回退到 legacy 动态猜测。
- `context_breakdown_request` 只读取 SessionDB 中的 durable public snapshot 和 Context usage authority，
  不再动态 probe legacy persona/facts/V2 registry/history/project。请求与响应同时校验 Session、request
  correlation、snapshot id/version 和独立 usage version；切换 Session 或晚到冲突帧 fail closed。
- Inspector 只展示 default-deny redactor 产生的有界分类、计数、token 来源与公开预览；隐藏 reasoning、
  凭据、敏感 header、原始工具参数/结果和私密附件内容不进入公开投影。没有 snapshot 或 Provider usage
  时明确显示 `unavailable/binding_only/legacy_incomplete`，不把 `0/0` 伪装成实测。
- SDK provider invocation 的真实 usage 通过 durable projection receipt 幂等写入 SessionDB，再广播严格
  递增版本；attempt 与 snapshot 分别保留版本轴，重启可恢复。Provider、model、Run/request/attempt 和
  catalog lineage 均来自 per-Run 冻结 binding，不再读取 legacy `last_usage`。

## 0.5 macOS IME 与 Enter 发送边界

- `InputBar` 的 Enter 发送门禁不再只依赖 React `nativeEvent.isComposing`。macOS WebKit 在用户按
  Enter 确认中文候选时，可能先发 `compositionend`，再把同一次 Enter 作为非 composing 的
  `keydown` 交给 React，旧逻辑因此会把候选确认误判成消息发送。
- 当前生产输入框同时检查组件级 composition 状态、标准 `isComposing` 与 WebKit IME sentinel
  `keyCode=229`；`compositionend` 后另保留一个只消费候选确认 Enter 的短暂 latch。候选确认只把
  文字写入草稿，随后一次独立 Enter 仍按原约定发送，Shift+Enter 与 Slash dropdown 行为不变。
- 回归测试覆盖 active composition、WebKit 229、`compositionend → Enter` 以及下一次正常 Enter。
  InputBar/ChatView 聚焦 `26 passed`、TypeScript typecheck 和 debug `.app`/DMG build PASS。Computer
  Use 已验证新构建输入框、候选 UI、草稿清理及零消息发送；自动控制层不能触发 macOS 全局输入源
  切换，精确“简体拼音候选 → Enter”仍需在中文输入源激活时做一次最终人工确认。

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

simple_harness 的用户界面现在以暗色为默认外观。主窗口背景、功能面板、弹窗、输入框、卡片、
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
- 消息页当前只有一个电话式 Realtime 开始/挂断按钮；应用挂载不会申请麦克风或连接，显式点击后才创建
  音频资源并连接 `/ws/realtime-voice`。旧 `/ws/audio` 不会恢复。自动化已覆盖开始、barge-in、挂断与清理，
  真实 Provider 连续多轮通话仍待验收，因此 UI 接线不等于 release PASS。
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
