# DeskPet — 全局项目状态与架构完成度

> **最后更新**：2026-08-13

## 2026-08-13 里程碑：Execution 单一写入 authority 收口

- **第二个 `execution_runs` 写入者已删除**：原生 `WorkflowRunStore.claim()` 通过无 SQL 的
  checkpoint adapter，把通用 Run 的 running 同步加入 caller-owned SQLite 事务；adapter 缺失或
  写后异常时，Workflow lease/version 与 Execution status/version 会一起回滚。
- **authority gate 恢复可信**：局部变量承载的静态只读 SELECT 不再误报，动态 DML 仍 fail closed；
  bundled Git 同时支持 macOS/Linux `bin/git` 与 Windows `cmd/git.exe`。四个新增 transaction
  starter 已按阻断 root、有效执行预算配置/恢复、项目 workspace 重绑定逐项审核，保持强类型 API，
  不引入通用 opcode。
- **当前验证**：聚焦 `48 passed`；authority enforce-target PASS（DML `1`、UoW starters `57`、
  run map `1`、supervisor task `0`、presenter `1`、legacy `0`）；可靠性门禁后端 `58 passed`、
  前端 `14 passed`，`HARNESS_RELIABILITY: PASS`。扩大 Harness 为
  `905 passed / 14 failed / 4 xfailed`，较修复前减少 2 个失败。剩余 14 项是 2 项代码体积预算、
  1 项 parity fixture 漂移、10 项不可达历史 Git 锚点、1 项 R6 旧 cutover fixture，因此尚不宣称
  Harness 全量绿。本轮按用户决定跳过冷启动性能对照，Realtime 继续关闭。

## 2026-08-13 里程碑：复杂 Harness 输出契约、精确授权与 Kimi 真机闭环

- **一次性授权恢复为精确调用语义**：“允许一次”不再把 TaskGrant 带入后续 continuation，只有
  “本会话始终允许”才复用授权；每个人工 decision 具有独立 grant instance，连续相同
  `workflow_spawn` 不再发生 TaskGrant 身份碰撞。Auto 模式仍保留确定性幂等 identity。
- **权限 UI 以精确结算事实为准**：前端收到 `permission_response_applied`、同一 Run 的下一条
  durable decision，或精确匹配 `run_id + call_id` 的 `tool_result` 后才关闭弹窗并推进任务；工具
  结果 public frame 已补 stable `call_id`，decision prompt 的 `params.call_id` 兼容旧投影。过期/冲突
  响应保留弹窗并显示真实错误，ControlChannel 重连后重新拉取 pending decisions。
  “停止当前任务”仅发送 interrupt，收到 cancelled ACK 后按 root Run 清除当前及排队 decision；
  失败则保留弹窗与错误，不再出现停止已生效但权限卡残留的状态分裂。
- **child 自动收尾与 confirm-only 边界已修复**：child terminal signal 的后台 task 成功/异常都会
  唤醒 reconciler，异常带 traceback 进入结构化日志；`confirm-only` 在未来 decision identity 尚未
  生成时保持 wait，不再误判 deny。真实 `kimi-k3` root
  `88efdacaeb5a585794aa18231647b717` 在 child
  `child-09c8eb64c4334860aa7df03b470da714` 返回 `CHILD_DONE` 后无需用户追问，自动执行第二次
  `run_shell` 并输出 `ROOT_AUTO_CONTINUATION_PASS`。
- **权限卡延迟真机闭环**：最终 Run `35459a155afc57b49e7158c7dbea635d` 的 `run_shell_9`
  durable outcome 在批准后约 58 ms succeeded；0.5 秒 UI 快照已显示 `✓ ok`、权限卡已消失，而
  Kimi 最终 `PERMISSION_CARD_SETTLED_PASS` 约 7.2 秒后才提交，证明不再等待 provider 续答或
  迟到 ACK。
- **纯文本 durable child 能有限收敛**：objective 明确禁止工具、只需返回文本且无写入/测试义务时，
  单次 `end_turn` 即可完成；有文件、命令或测试要求时仍必须提供 effect/receipt。真机 `kimi-k3`
  root `4a976f0aa972525a899753cd7c630fec` 串行启动两个 child
  `child-a0a1262669335fafdcf779b61001c5ff`、`child-feea8f055f616ffa1920c22d85968034`，
  两次“允许一次”均 ACK 成功、两个 child 与 root 均 completed，UI 最终显示 `A_OK / B_OK` 并回到
  “空闲”；独立重复授权 root `84e99cc1d7c75f50b8421e37096d4107` 也 completed。
- **任务输出契约已进入生产链**：普通 durable child 启动前必须声明精确 `output_refs` 与可选
  `scratch_refs`；Host 冻结 workspace 与非可变内容摘要并绑定 launch ticket/start snapshot。
  prepared file target 与 Artifact 注册执行前拒绝越界；终审要求声明产物存在且不是 symlink、scratch
  全清、其余工作区摘要不变。新 Run fail closed，历史无契约 Run 只读兼容并明确告警。契约冻结、
  拒绝和终审日志均带稳定 `contract_id`。不透明 shell 的持久越界由终审捕获；同一 shell 内创建后
  删除的瞬时文件仍需未来 OS 级文件事件审计，当前不通过解析命令文本伪装保证。
- **Kimi 复杂真机复验 PASS**：第一次 Run 因 profile adapter 丢契约被主动停止并保留 FAIL 事实；
  修复后 root `1be3666915615161b1a9fac699aaf3e8`、child
  `child-f16d23bf03ac0c5f152609abc12f12df` 均 completed。child 读取 CSV，在声明 scratch 中生成并
  运行 Python，产出 `summary.json`/`REPORT.md`、校验总额 `422.00`、清理 scratch，并只注册两项
  Artifact；持久化审计 `passed=true / baseline_matches=true / missing_outputs=[] /
  retained_scratch=[]`，UI 有两张真实 ArtifactCard，根任务独立复核后 completed。空白新会话也
  已真机确认继承当前可见历史会话的 `kimi-k3`，不再回退到 transport 默认模型。
- **当前验证**：本轮新增聚焦后端 `65 passed`、前端权限弹窗/Hook `11 passed`；前端全量
  `556 passed`；TypeScript、
  Python 编译、execution build manifest、diff check 和 Tauri debug bundle build PASS。扩大检查发现并
  修复 `state.db` v27 marker 在补跑旧 v15/v16 migration 后误降到 26 的问题。扩展 Harness 目录
  加入五项硬退出测试后复跑为 `895 passed / 4 xfailed / 16 failed`；Execution→Memory 依赖方向
  失败已关闭，剩余
  16 项是既有 AgentLoop/ReAct 结构预算、旧 commit/fixture 可达性、Git fallback 与 parity 漂移，
  不宣称 Python Harness 全量基线绿色。
- **崩溃恢复门禁已建立**：`scripts/acceptance/harness_reliability_gate.py` 用单命令固定验证授权、
  effect handoff、child→parent、终态投影与重连回放；其中五项会在精确事务 hook 上对独立
  解释器发送真实 `SIGKILL`，重启后验证 SQLite 完整性及外部写/signal/delivery 唯一。当前后端
  `58 passed`、前端 `14 passed`，
  `HARNESS_RELIABILITY: PASS`。Reconciler 的 lane/worker 瞬时失败现在记录结构化关联字段并在默认
  1 秒后 one-shot 重试，关闭时取消 timer，不引入常驻 supervisor。Inspector 的 state.db
  keyset reader 已移回 Execution 自有只读边界，移除 Execution→Memory 越层依赖。
- **真实 Tauri supervisor E2E 已闭环**：macOS Workbench 内真实点击“允许一次”，在外部 shell
  effect 已写 START、尚未完成时 `SIGKILL` backend；Tauri 主进程不退出并约 2 秒拉起新 backend。
  原 Run `efb4a75a2d5759b99b492ed77748278c` 最终 completed，marker 只有一组 START/END，UI 无需刷新
  即显示恢复终态并回到“空闲”，死进程权限卡同步清除。恢复账本同时终结 effect 和 attempt 为
  `unknown / started_may_complete`，不重复执行不可判定的外部副作用；Session 重映射、终态 live
  delivery 和恢复竞态日志误报也已关闭。原始证据仅存本地 gitignored 目录，等待后续 NAS 归档。

## 2026-08-12 里程碑：Workbench last-mile、设置、恢复与托盘真测全部收口

- **ArtifactCard last-mile 的 root 与 child 自动桥接已实现**：生产 `execute_prepared` 生成
  artifact envelope，SessionDB 记录根 Run 的 `artifact_card` 投影；相对路径只在当前可信
  workspace 内解析。当前源码把 prepared receipt/artifact metadata 与 durable effect completion
  原子提交并在提交后 ack；child 完成事务会验证 artifacts/refs/SHA-256、发出
  `workflow.artifact_card` delivery event，并把 artifacts/refs 放进 parent terminal signal。Tauri
  白名单包含 `<user_data>/workspace/`。历史 macOS 根 Run 的 TextEdit/Finder 打开定位均 PASS；
  fresh-profile child `child-2ec4cceeec4df4bb19531561ab0e1e31` 已真机投影五张 ArtifactCard，
  DB artifact event 与 parent terminal signal 的 5 个 refs 和本地 SHA 精确一致，E2E 已闭环。
- **复杂任务交付链补强**：新增只读 `register_artifacts`，可把可信 workspace 内已经存在的文件
  直接登记为标准 artifact envelope（路径、大小、SHA-256），不创建旁路 JSON、不复制或改写
  原文件；未启用 ReceiptStore 时 artifact refs 仍进入 prepared execution metadata。跨平台
  basename 同时修正了 macOS/Linux 上 Windows 路径卡片标题显示完整路径的问题。
- **长参数与权限等待补强**：`write_file` 真实支持 `mode=append` 的 ≤3000 字符分块写入，
  `run_shell` schema 拒绝超长命令并引导文件正文走分块工具。权限请求只由 ChatView 订阅，按
  decision identity 去重并阻止已处理事件重放，消除同一 decision 多弹窗导致“停止后仍继续问”的
  前端根因；提交 live decision 后会把仍等待授权的 root projection/session 推进为 `running`，
  清除已处理 decision，且不覆盖 durable terminal。相邻前端回归 `37 passed`、TypeScript PASS。
  失败 Run 的停止 Host 不再依赖可用 Provider/workspace，避免重复 preflight 击穿 control
  WebSocket；durable Run 的进程退出恢复语义保持不变。
- **设置持久化已闭环**：Provider 删除确认、Agent 预算 request-id 关联、macOS 稳定数据目录
  bootstrap pointer、“当前/下次启动目录”诚实展示均落地；Provider、预算、目录、自启完成真 UI
  修改、完全重启、恢复和无残留验证。
- **运行期 backend 故障留在 Workbench 内**：新增运行时故障横幅；空 secret 与半开 WebSocket
  握手有界失败，ChatView/侧栏显示最差态，发送 fail closed、重试恢复。故障释放后 Kimi3
  HTTP 200 并精确回复 `S13 恢复成功`。
- **本轮验证**：Vitest `540 passed`、Rust `79 passed`、companion
  `647 passed / 10 skipped`、TypeScript、Vite build、`cargo check` 均绿；红钮、Cmd+Q 与托盘
  三条退出路径主进程/backend/8100 全清，重启几何一致。托盘三项文案与隐藏/显示由用户在当前
  macOS 打包版现场确认，退出终态和 1100×750 几何恢复由独立检查、启动日志与截图交叉验证。
- **本轮新增代码回归**：Harness/ToolRegistry/Artifact/last-mile/recovery 聚焦套件
  `256 passed / 2 skipped`，取消/恢复相邻套件另有 `40 passed / 4 xfailed`；前端全量
  `540 passed`，TypeScript、Vite production build 与当前源码 Tauri debug app 构建 PASS。
- **复杂任务可靠性与日志补强（当前源码，真机主链 PASS）**：工作区未选择只投影为 retryable tool
  outcome，不再击穿 control WebSocket；实时 socket/peer/final/context-usage 各自尽力投影，durable
  SessionDB 结果不被断线反向改写；final 后 trace 关闭记 OK。backend/stdlb 与 structlog 统一为
  单行 JSON，日志路径统一遵循 `DESKPET_USER_LOG_DIR`/portable/user-data 解析，20 MiB × 5 轮换；
  两类日志在 JSON 渲染前共用字段级与文本级脱敏，关联 ID/阶段/耗时继续保留，回归 `12 passed`；
  macOS/Linux diagnostic archive/reveal 已补平台原生命令。相关 workflow/effect/ReAct/
  RunPresenter/trace/observability 聚焦重跑 `231 passed`；但 Python 全量仍有 `79 failed`（同时
  `7473 passed / 48 skipped / 4 xfailed`），因此不宣称全量基线绿色。
- **fresh-profile 复杂任务首轮：核心执行 PASS、terminal FAIL、根因已修**：真实 Workbench UI
  使用 `kimi-k3` 选择隔离 workspace，control WebSocket 保持连接，child
  `child-1a1c22e7a629b36c0c04f262af4b7f53` 完成分块写入、17 项 unittest、CLI、自检与一次
  `register_artifacts`；独立复跑为 `17/17 PASS`，5 文件 SHA 和四项指标全部匹配。但 terminal
  将 `write_file` 的 `sha256=null` provisional envelope 误当最终 artifact，9/9 后报
  `workflow_engine:frontier_failure`，父 Run 又重复尝试委派。现已排除非登记的 provisional
  envelope，并为 `register_artifacts` 保持 refs/digest 精确相等；相邻回归 `102 passed`。同时
  真机再次确认 UI 后台执行时长期显示“等待授权”、进度停在 5/9→6/9；这些问题在第二轮得到
  真机/账本闭环，详下一项。
- **fresh-profile 复杂任务第二轮：child/产物 PASS，root 收敛 FAIL；第三轮已修复闭环**：root `caf7d550a7705da89cba6b731b9d1c4c`、child
  `child-2ec4cceeec4df4bb19531561ab0e1e31` 使用 `kimi-k3`，原生选择隔离目录后只确认一次
  workflow 权限；约 1.2 秒恢复“工具执行中”，最终 9/9 completed、五张 ArtifactCard、空闲。
  真实生成 5 文件，独立 `7/7 unittest PASS`、CLI/JSON 为 `count=10/sum=55`；执行 child 只有一个
  `register_artifacts`，artifact event 与该 child 已投递 parent terminal signal 均携带同一 5 个 SHA。
  同一 attempt 内重复 5/9→6/9 的私有工具循环已改为按公开 node/attempt 去重；路由提交早期失败
  的日志不再以未赋值 frontier 覆盖原异常，进度/engine/log 相邻回归 `76 passed`。但该 root 在首个
  child 成功后仍因 spawn scoped evidence UNKNOWN 误触发 verify gate，并追加两个失败验证 child，故
  root 收敛不能判 PASS。当前源码改为按 spawn ticket/command/terminal signal/audit 的精确 lineage
  投影 child committed receipts；第三轮 fresh Run `f0a514f061cb56cebaa498a4a1447b24` 只有
  `child-0abd7fb98c3faf61504a7f96085c493f`，三次真实 shell 分别枚举、容斥与显式交叉核验，结果
  `count=467/sum=234168/MATCH=True`，root/child completed，零 verify nudge、零第二次 spawn。
- **Harness 终态观察与授权日志补强**：真机发现 root 已 completed 时，语义 phase 的历史 running
  状态仍让观察面显示“正在委派/4/5”。当前前端以 aggregate terminal 收束未结 phase/substep/tool，
  运行图终态显示“结果/记录已结束”，不再标当前进行中；backend 在权限/澄清/目录/外部等待事件
  发出时写 `harness_blocking_ui_event_emitted`，只记录 event/session/run/request/tool 关联字段，不记录
  敏感 params。后端聚焦 `239 passed`、前端聚焦 `69 passed`、TypeScript 与 Vite build PASS。
- **workflow_spawn 拒绝语义已真机闭环**：旧 root `074bf4d51e545627ae188d623cafde89`
  在 UI 点击拒绝后虽然 decision 已是 `denied`，仍错误发出 delegate 并创建 child，根因是
  `control_delegate` 的 deny outcome 结算后仍无条件 `_prepare_control_event()`。当前 Kernel 覆盖
  `deny/denied` 归一化，ReAct Driver 对已结算 control outcome fail closed；修复后 root
  `996390c79f1b5c03967f7f42dc408f29` 的 decision=`denied`、child=0、ticket=0、无
  `child_accepted`，最终界面明确显示 `authorization_denied` 且未创建 durable child。扩大后端回归
  `328 passed`。相邻的历史取消投影缺口也已关闭：前端用父 root 的 terminal Session projection
  收束 child workflow 卡并合并同源 public trace；重启后会话 `5ec83cb7-51d8-46a8-a7ed-c13de60fcf59`
  只保留一张“已取消 / 6/9 / 67%”卡，缺少可靠计时时显示“耗时未记录”，不再显示“进行中”或
  重复终态卡。前端相关回归 `57 passed`，TypeScript 与 debug bundle build PASS。
- **失败 child 收敛与终态展示补强**：ReAct Driver 只对 durable terminal=`failed` 的
  `workflow_spawn` child 记录有界脱敏 objective 签名；语义相近的再次委派在 launch ticket 前
  阻断，首次反馈模型、第二次以 `delegate_convergence_exhausted` 停止，实质不同的 child 放行。
  前端同时修正 Root-only Session projection 的优先级：child 消息自身终态优先，Root 终态只收束
  陈旧 running/waiting 卡。真实 Session `782f283d-0ac0-4016-b979-e6f79e7582f6` 重启后，completed
  child 与 `workflow_node:llm_proposal:provider_failure` failed child 分别显示“已完成”和
  “失败 / 5/9”。后端扩大回归 `332 passed`；前端聚焦 `44 passed`、TypeScript 与 debug bundle
  build PASS。扩大套件曾暴露 replay 测试在 UOW close 前未 drain parent child-signal owner；按生产
  shutdown 顺序补齐后，同一 `332` 项退出零 pending-task/closed-database 告警。
- **macOS 项目目录确认文案已修复**：ProjectDirectoryCard 不再硬编码反斜杠，POSIX/macOS 用
  `/`、Windows 用 `\`，并覆盖根目录。真实 Open sheet 选择 Desktop 后显示
  `/Users/denny/Desktop/harness-path-test`，未创建该目录；证据保存在隔离测试 profile。
- **当前源码 macOS 真 UI 复验**：历史 Kimi Session 曾因 catalog 不再包含该绑定而在 provider
  preflight 失败；旧取消路径会再次解析缺失模型并使聊天断线。修复后真点击“停止”，界面从
  “工具执行中”回到“空闲”，左下角保持“已连接”，后端没有
  `SessionProviderUnavailable`/ASGI 异常。目录恢复后，当前源码以 `kimi-k3` 完成 Run
  `04a477a3fbbb5e3eb2045e6b11006b55`：真实 UI 触发 `write_file` → 单次权限确认 →
  `register_artifacts`，两张 ArtifactCard 均能用 TextEdit 打开并在 Finder 定位；文件为 14 B，
  SHA-256 `c96a2f4aec81c7e0d4ddaceb068ecaf030477e1c273bd4ab70ca1fe9197c4706`。
- **Provider 实时模型目录持久化**：`models_list` 成功取得 `/models` 后，把去重目录原子写回
  对应 Provider 的 `config.toml` 缓存；写入前复核 incarnation/revision/base URL，相同目录不
  重写，且缓存刷新不改变 Provider identity。显式默认模型若不在 live 目录则拒绝持久化而不
  静默换模。Provider/IPC/Session authority 聚焦回归 `88 passed`；r11 隔离测试配置已从实时
  HTTP 200 目录写入 155 个模型并确认包含 `kimi-k3`，测试 Provider 默认模型单独设为
  `kimi-k3`。
- **运行时模型身份不再泄漏占位符**：平台级 Persona 继续使用稳定占位值维持 prompt cache，
  但每个任务另注入受保护的 task-scoped runtime model fragment；TurnPreparer 使用已解析的
  Session Provider，而非全局默认 Provider。重启当前源码后，真实 UI 询问实际模型精确 ID，
  界面回复 `kimi-k3`；同一次 Run `97f01117fd50506fbe10da0444577fbd` 的后台日志确认
  `model=kimi-k3` 且 `/v1/chat/completions` HTTP 200。
- **Kimi K3 历史复杂工程烟测核心 PASS；当前 fresh E2E 经第三轮闭环**：root
  `cb74467c06f35eaebdf7bfe316b9fff8` 与 child
  `child-4dad77bbfafec0b8428852dde382d9eb` 完成项目目录确认、durable workflow、分块写入、真实
  unittest/CLI 和结果自检；独立复跑 `9/9 OK`，四个关键指标精确匹配。child 内一次
  `register_artifacts` 的 outcome 含 5 个正确文件/哈希，但旧 child `artifact_refs_json=[]`；根 Run
  `1bf5014d7ee55573ba2a797c391032ef` 补偿登记后 5 张 ArtifactCard 正常。当前源码已修 child
  metadata/effect/terminal/delivery 原子桥接，以及目录前置和关闭 WebSocket 的恢复边界；当前
  第二轮已确认 child/Artifact 主链，但 root 误派验证 child；第三轮已用精确 child receipt 回传确认
  单 child 一次收敛。旧 r15 profile 还观察到
  恢复历史 Run 时因 ToolSpec catalog 漂移反复抛
  `ToolCatalogMutationError`，已作为独立 followup，不与新 profile 的能力验证混为一谈。
- **剩余边界**：产品/testcase 层的 18 个场景已收口；TC-WB-12 步骤 7 已由用户明确移除，禁止
  再次启动带 Live2D 的历史基线。plan-test gate 仍暂停，不宣称机器门 READY。
- 本轮继续遵守用户要求：暂不使用 plan-test skill，不写 gate ledger，不宣称机器门 READY。

## 2026-08-09 里程碑：登录方式改为手动 provider，relay 与 default 会话双双下线

- **托管账号登录（relay）整套移除**。前端删 18 个 relay 源文件（adapter/modal/edition/
  错误文案/provider 桥接与注册/账号面板/bindings），后端删 `llm/relay_provider_ops.py`、
  两条 `settings_providers_*` relay 消息、`config.relay_managed_provider`、
  `.env.relay*`。产品只剩一条路径：用户在设置页手填 **baseUrl + apiKey**，
  key 仍只落 OS keychain（`config.toml` 存 `api_key_ref`，永不明文）。
- **身份改为纯本地 profile**。`RegistryRelayAuthSnapshotProvider`（读 keychain relay
  token → 调 `/v1/me`）换成零 I/O 的 `LocalAuthSnapshotProvider`。**WBUI-DEF-AUTH-01
  由此结构性消失**——没有远端 token，就没有过期与刷新，也就没有"未绑定 profile
  永久卡在『正在恢复身份…』"。
- **保留会话 `default` 移除**。原先它是"应用的兜底会话"，代价是三处生命周期特判
  （clear 只清内容不退役 / 墓碑化自愈 / 纪元接管自愈）。现在会话一律用户新建、
  生命周期同构：`clear` 对所有会话统一退役，属主一经绑定不可改绑。
  **WBUI-DEF-COMP-01 的修复随载体一并删除**——其成因（relay↔local 身份迁移推进
  `binding_epoch`）已不存在：本地 `profile_id` 恒为 `legacy_local_profile`，
  `changed_owner` 恒 False，纪元不再前进。
- **空态入口**：无会话时在输入框直接发消息，由 InputBar 发一条
  `chat_v2 { session_id: "", new_session: true }`，后端派 uuid 新会话再投递并回推
  `session_switched` + `chat_v2_user_echo`。**不复活任何固定 sid**。
- 验证：`npm run typecheck`（`tsc -b`，见下条）+ `vitest 551 passed`；backend pytest
  分块跑并**逐条对比 stash 前 baseline**，失败集合完全一致、零新增失败。
- **顺带纠正一个长期假绿灯**：`tauri-app/tsconfig.json` 是 solution 式配置
  （`"files": []` + project references），裸跑 `npx tsc --noEmit` **一个文件都不检查、
  永远 exit 0**。已加 `npm run typecheck`（`tsc -b --noEmit`）为唯一口径。
  该发现使 r9 的 S12 tsc 证据作废，详见
  [r9 审计遗留](../plans/2026-08-09-workbench-ui-r9-audit-followup.md) §8。

## 2026-08-04 里程碑：桌面游戏操作与小窗口 Context 稳定性修复

- 修复 Session `2e69be7e-0b16-4bfb-a774-d61e588c5ec3` 暴露的双故障：桌面输入工具不再以
  `concurrency_safe` 并发进入全局非阻塞键盘锁；screen/window 工具共享全局桌面 input lane，
  `EffectBatchExecutor` 以引用计数资源锁跨工具批次、跨 Run 串行排他效果。
- `window_key` 新增单事务短序列（最多 12 步/12 秒），支持按住、组合键、按键后暂停和独立纯暂停
  步骤，完整预校验、失败即停并保证释放按键；旧单键调用保持兼容。
- 截图结果从普通 tool JSON 中移除 base64，只把有界元数据留在文本并以多模态 `image_url`
  附件交给模型，10 MiB 上限和独立 attachment budget 防止截图字符串挤爆文本 Context。
- AgentLoop 压缩器按 Run 冻结的模型窗口派生；provider-chain lossless 预检仍超限时，普通 Run
  会先执行一次目标保真压缩再出站，而 coverage/no-compressor 路径继续 fail closed。
- 自动化首次联合回归 `215 passed`；补齐纯暂停步骤后聚焦 `43 passed`、最终联合复测
  `219 passed`。Windows 真机首次 Run `bd8f676ef4215e558b2d911bfcf76735` 完成 Godot 启动、单次
  D→W 序列和前后截图，32K Kimi 在 70% 线触发压缩后继续完成；独立最终 Run
  `9dd6243c306753b7894bc7ca1c3646b8` 仅执行一次 A→暂停→S `window_key`，随后截图并完成，
  无 `window keyboard input is busy` 或 `provider_context_budget_exceeded`。

## 2026-08-03 里程碑：Session 模型一致性与语义运行视图

- Provider/Session authority 升级为 incarnation/config revision/binding epoch 的 durable CAS；显式
  stale/disabled/model-missing 绑定 fail closed，未绑定 Session 才允许 global chain。启动 migration、
  Registry load 与 reconcile 完成前，产品入口和后台 router 都不开放。
- 主 Agent 与会话附属 LLM 共用类型化 `ProviderWorkloadContext` 和完整 callsite inventory；跨
  Session 维护使用显式 `BackgroundModelPolicy`。附属 breaker 按 credential/account/model/workload/
  endpoint 隔离，后台错误不再结算 Root。仅 DEV 可加载的 consume-once fault script 已接入真实
  provider 边界，生产发现注入 env 会拒载。
- Context Usage v24 使用 immutable sample/materialized state；measured、compacted、binding-only
  来源明确。冷恢复没有样本时显示 Session 绑定模型和“尚无用量”，不再拿全局默认模型生成假 0 值。
- ReAct 的真实 `context_compacted` 事件现已穿过 Collaborator、Driver、Runtime 和 canonical
  presenter 进入同一 usage authority；Run 入口把精确 measured sample ID 冻结到 durable payload，
  producer 原样携带，presenter 只做 Session + sample ID 精确读取，不再用时间猜测并发 lineage。它
  还把每次压缩的确定性结果 sample ID 写回后续 React 工具 boundary，重启后从最新 lineage
  继续。它保留 source/sample lineage 与实际 Session 模型；不再只在旧
  `AgentLoop` 内可见。已有项目卡片的原生选择结果现在直接作为项目根目录，前端不再显示无意义的
  子文件夹名输入，也不会把建议名重复拼接成 `root\\existing-project`。
- workflow/state ledger 之上新增 schema v3 public read model：一致 read cut、完整 keyset、签名
  cursor、total/completeness、default-deny tool/provider projection。前端运行图、消息 activity 与
  durable steps 共用一个 Session/root snapshot store，工具输入/结果分层折叠，未知 schema 安全降级。
- 根结果和阶段由纯 reducer 重建；blocked 只认 `RunBlockSignalV1`，child 失败后只有完整
  terminal→FailureReport/failure-set→replacement Attempt→root terminal 链才显示“已接管并完成”。
  A2 只读复跑真实 Godot Root 得到 366 facts、6 phases、29 个唯一逻辑工具（23 shell）、
  `completed_with_recovery`、`projection_complete=true`；child 原始 failed 保留。
- DEV provider fault 在 durable claim 后、物理 transport 前注入；Session auxiliary、child main、
  detached maintenance 分别使用 Root、child correlation、request correlation，禁止用一个伪 Root
  身份覆盖三类 workload。工具在 StartedAck 后被停止时，物理 completion 仍被观察；effect 先持久化
  为 `unknown/started_may_complete`，terminal Reconciler 只做晚到隔离结算并绝不恢复 Driver；ready
  evidence 直到 durable terminal 决策后才 acknowledge，running/CAS loser 可重试。
- 最终自动化证据：跨会话 full-surface smoke 后端 `29 passed`、前端 `116 passed`；child
  provider 生产接线、故障注入、dispatch 与 Root recovery 联测 `66 passed`；受影响后端、前端
  全量回归及 TypeScript/Vite production build 均通过。当前增量的 canonical compaction 与已有
  目录合同聚焦回归另计后端 `7 passed`、前端 `21 passed`，并已包含在最终重跑中。
- S-SRV-1～S-SRV-5 Windows 真机矩阵已全部执行并 PASS：覆盖 Kimi 冷恢复、≥10 轮长上下文、
  child 失败后 Root 接管、后台 401/402 隔离、双 Session 并发停止与晚到结果隔离。正式 S-SRV-3
  Root 为 `573cf15ecffb561493b2590bcb785368`，child 保持 failed，Root 以
  `completed_with_recovery` 收口；截图、DB ledger 与 Gate 记录位于对应 plan 的 final5 run-dir。
  当前有效 Root 分别为 S-SRV-1 `32f9e2fe01f5568d9ca56ef0b17b4ada`、S-SRV-2
  `04999765753a5342aa9f7b4619b0fd38`、S-SRV-4-401
  `e6acd3abbe85519ca1f6736329bb3aa3`、S-SRV-4-402
  `11dac2d13ae954ddae8c3cde9b3658cf`；S-SRV-5 重启后 A 仍 cancelled、B 仍 completed，晚到结果
  未重新进入消息流。

## 2026-08-03 里程碑：多步骤任务模型绑定与终态收口修复

- 顶层 Run 的 Host Context 现在冻结有序的 `provider_id + model_id`，durable child 继承精确
  模型绑定，不再从可变 Registry 默认值重新选择模型；修复 Session 已选 `kimi-k3`、child 却
  静默落到 `sf-glm-5.2` 并因余额不足失败的问题。历史 provider-only Run 保留兼容恢复路径。
- child terminal 事件无条件唤醒 reconciler，attached 终态信号可立即投递并恢复父 Run；终态后
  的迟到 progress 幂等忽略。Provider 402/余额与 Relay 凭据故障使用稳定公开错误引用，前端显示
  可理解的中文说明，同时保留技术审计事实。
- durable 完成门禁识别生产工具 outcome 的 `state=success`，并先剔除“禁止修改 / without
  editing”等否定动作，再判断真正的写入和测试义务；正确的只读最终回答不会因关键词误判循环到
  proposal budget 或模型上下文上限。
- 控制工具进入失败重规划时，loop guard 已预填失败 outcome 的 batch 不再继续启动 child；历史
  数据若同时保留 pending delegate 与不同 outcome，迟到 child terminal 会保留首个权威 outcome、
  原子确认 signal 后继续恢复，不再以 `outcome already recorded with different value` 阻断后端
  lifespan。effect-ready 定向恢复也不再等待或取消已在运行的 provider owner，owner 结束后由
  一次性回调重新唤醒恢复器，避免正常的长模型调用被 5 秒 reconciliation budget 误判为启动
  失败。ReAct/失败重规划/Harness 启动/Kernel 组合回归 `185 passed`。
- 聚焦自动化：Harness/Workflow 后端相关套件与前端 Sessions Store、TypeScript、Vite build
  均通过。Windows 真机 Session `6db8e119-d960-4bcd-b010-bf738b0b66c8` 使用 Kimi 运行同一条
  `workflow.durable_task` 只读请求：root `2d28545be2db599bbbda04040fb08db3`、child
  `child-5cc4942f4d8f89735ff7be9da4f66289` 均为 `completed`；两者冻结绑定和全部 11 次模型调用
  均为 `kimi-k3`，terminal signal `attempts=1` 且已投递，UI 显示完整步骤和最终标题报告。
- 新建本地项目的目录确认增加 Host 前置门禁：可信用户消息、child objective 或 `plan_steps`
  命中新项目创建时，`workflow.durable_task` 只能在 `user_path` 绑定后 spawn；用户在聊天里写的
  绝对路径仍须原生卡片确认。目录选择和外部等待不再下放到不能 durable suspend 的 native
  child capability snapshot，修复 child 直接执行后返回 `external_wait_must_be_staged_by_harness`。
- 项目目录卡片新增 `use_existing` 模式：新空目录仍 fail closed 拒绝非空目标，明确继续半成品时
  可确认已有目录。`workflow.db` schema v28 把安全的一次性换根扩展为
  `task_default | existing → user_path`，仍要求无 child、无写 effect 并递增 binding version。
- 聚焦自动化通过：目录/child/Harness/schema 后端 `167 passed`；前端全量 Vitest 与 TypeScript
  编译通过。
- capability fingerprint 不再包含会随项目目录变化的运行时 scope 逻辑；新增独立的
  `SqliteCurrentExecutionScopeAuthority`，冻结 catalog/身份但从 durable Run 读取当前 workspace。
  Companion identity bind 成功后会立即唤醒 Harness 恢复，修复启动扫描先于身份就绪后永久沉睡。
- Provider 已明确返回的 `408 / 425 / 429 / 5xx` 现在结算为可重试响应，并以新 invocation identity
  仅重试一次；不再把 Kimi `engine_overloaded_error` 误记为 handoff unknown，再在 native workflow
  节点重放时触发 `invocation id already names a different dispatch`。额度/余额类 `402` 不自动重试。
- Windows 真机 Session `2e69be7e-0b16-4bfb-a774-d61e588c5ec3` 使用冻结的 `kimi-k3` 完成
  `F:\projects\jurassic-park-escape` Godot Demo：子 workflow 遇到一次 Kimi 429 后父 ReAct 接管，
  生成完整项目并由下载的 Godot 4.2.2 找出、修复 `pixel_art_items.gd` 编译错误；最终 root
  `a3a63c99fca45b0ca91d1192d69bb378` 为 completed。编辑器实际打开 `Main.tscn`，DEBUG 窗口显示
  可移动玩家、生命值/目标/背包 HUD 和程序化公园地图。聚焦后端 `217 passed`、前端全量
  `912 passed`，TypeScript 编译通过。

## 2026-08-02 里程碑：Harness 运行图与项目目录上下文收口

- Root 的 15 分钟限制从“消息收到后的整轮墙钟”改为 durable 有效执行预算。模型、工具和
  Driver 实际工作才累计；目录选择、授权、澄清、外部操作和 child 等待进入 `waiting` 后暂停，
  恢复同一 Run 时继续使用剩余预算。WebSocket presentation 不再拥有取消 Run 的权力。
- `workflow.db` schema v27 新增 `execution_run_active_budgets` 及状态迁移 trigger；到期由
  HarnessReconciler 领取可重放 expired claim 并以
  `active_execution_budget_exhausted` 交 RunKernel 收口。设置页同步改名为“Agent 有效执行预算”。
- 自动化覆盖“执行 4 秒、等待 1 小时、恢复后只剩 6 秒”、到期 claim 崩溃重放、schema 迁移、
  presentation 不再墙钟取消；相关跨层后端回归 `426 passed, 2 skipped`。
- Windows 真机使用 Session `18a521e9-946a-4e56-bd63-445dc8e75097`、Run
  `610daa0f7e7a5b6b9ef095b5ba938bb9` 验证：临时设为 1 分钟预算后触发 Godot 项目目录卡片，
  保持不选择超过 2 分钟，UI 仍显示“等待中 / 需要你确认”；durable ledger 为 `paused`，仅累计
  `0.084s`。验收后预算恢复 15 分钟，测试 Run 经启动 Reconciler 收敛为 `cancelled`，未创建项目文件。

- 左侧 Harness 默认视图改为真实运行图：步骤按箭头串联，当前节点高亮，工具节点作为缩进
  分支；图内滚动跟随当前节点，外层不再自动滚到底。图下方保留唯一一套可折叠步骤详情，点击
  图节点会直接展开对应输入和结果。相邻准备动作在图上合并成用户阶段，详情则标明原始记录数；
  原始工具名和 `settled` 等技术状态不再出现在默认图中。六层账本、ReAct 和原始 JSON 继续默认收在
  “技术记录”。
- 已取消任务明确提示“不会继续执行”。真实 Windows 消息窗口使用现有 7 步 Godot Run 验证：
  图可见、工具节点可选中，展开后显示 `project_directory_select` 输入与归一化结果。

- `project_directory_request` 不再保存为消息窗口级单例，改为按 `session_id` 分区；渲染同时
  校验当前 Session、当前选中 Run 和 `waiting` projection，旧项目请求不会泄漏到新话题。
- Run 恢复、切换、完成、失败或取消后，目录操作卡片从消息流移除；历史仍保留 Agent 说明和
  普通工具记录。Windows 实测新空 Session 无卡片，切回已取消的 Godot Session 也无卡片。
- 用户确认的 `user_path` 持久化在 `workflow.db/execution_task_work_contexts`，并成为该 Session
  的当前项目上下文；后续顶层 Run 自动继承到 Host、Agent prompt 和工具上下文，后来的空
  workspace Run 不会覆盖它，其他 Session 隔离。创建另一个新项目仍要求模型重新调目录工具。
- 新增 Session/Run 可见性、运行图交互和身份状态重放回归测试；前端全量
  `101 files / 906 tests passed`，
  TypeScript 与 Vite production build PASS；项目上下文继承相关后端测试 `149 passed`。
- 修复运行图热更新后消息输入框可能永久停在“正在恢复身份…”：`controlWs`
  缓存并向新订阅者重放最近 Companion 身份状态，不再依赖组件恰好收到一次性广播。
  聚焦回归 `41 passed`、TypeScript PASS；真实 Windows 窗口确认已登录账号，刷新重连后输入
  “回复一个：收到”，Run 从理解请求走到任务完成，消息区实际收到“收到 🐾”并回到空闲。
- 修复 Session 模型选择在“新话题”或应用重启后回退到默认 GLM：新 Session 在启动 Run 前
  原子继承来源 Session 的 Provider/模型/参数绑定；历史切换与冷启动增加独立绑定 hydration，
  标题栏与实际出站模型使用同一持久化事实源。模型弹窗新增按 id/名称即时筛选。
- `kimi-k3` 的中转站接口只接受 `temperature=1`；OpenAI-compatible 网络边界现在统一规范化
  该模型的温度，并在 at-most-once 调用失败时保留 Relay HTTP 错误正文用于诊断。聚焦回归：
  后端 `63 passed, 2 skipped`，前端 `33 passed`，TypeScript PASS。真实 Windows Run 使用
  Session `074623b0-d91b-495e-9b67-b91c04859ee1`、Run
  `a60eca6bffd0551e801f2de9abf5ea3d`；持久化 invocation 为
  `relay-cloud / kimi-k3 / completed`，消息区收到 `KIMI3_RUN_OK`，重启后仍显示 `kimi-k3`。

## 2026-07-30 里程碑：多步骤任务启动状态与公开进度修复

- 原生 Workflow child 首次领取 lease 与恢复接管时，均在同一 SQLite 事务中把通用
  `execution_runs` 原子同步为 `running`，只写一次 `started_at`，重复领取保持幂等；
  修复原生 `workflow_runs` 已运行、Harness 却长期显示 `queued`/未启动的问题。
- Kernel 预创建 child attach 后立即唤醒 Workflow dispatcher；周期扫描只作安全兜底。
  进度事件写入后也立即唤醒 Harness delivery reconciler，不再等待下一轮后台扫描。
- `durable_task@v1` 进入公开进度映射，消息页可显示九个稳定阶段；循环的
  `llm_proposal/tool_execution` 按 task identity 的 SHA-256 短摘要区分事件，避免后续迭代
  被幂等键误去重，同时不暴露原始 task id 或隐藏 reasoning；公开标签稳定为“多步骤任务”。
- 启动恢复兼容精确的 pre-runtime-authority capability fingerprint 并 CAS 迁移；已有
  cancel/continuation intent 拥有的 Run 不再导致整套 Harness 初始化失败。
- 取消恢复补齐：precreated Workflow 的通用 Run 已是 `cancel_requested` 时，恢复幂等
  收敛原生 Workflow；取消中/已取消父 Run 的迟到 attached child signal 被确认但不会再
  唤醒 Driver，正常终态父 Run 的同类回执仍保留为一致性错误。
- 右侧消息流只展示产生工具调用的 Provider 轮次中模型明确返回的公开 `content`，样式与
  普通助手消息一致；工具调用和结果继续使用现有工具信息框。`Agent 工作记录`、步骤编号、
  输入/结果字段、RunKernel/Canonical 状态和原始 JSON 不再进入默认消息流，完整审计事实
  只保留在左侧 Harness Inspector。没有工具调用的最终模型文本继续走原有 assistant 消息，
  避免重复；历史 `<think>`/reasoning 内容仍会过滤。无阶段详情的旧 Workflow 进度不显示
  重型卡片，只输出任务名、状态、当前步骤和简短进度，并按匹配 child Run 的 durable 终态
  从“进行中”更正为“已取消”并停止计时。
- 右侧消息流的公开执行记录现在直接读取持久化账本，而非前端临时步骤卡：
  `execution_provider_invocation_outcomes` 提供 root/child 的公开 `content`，
  `workflow_effects` 提供 child 实际执行的工具输入和 outcome。公开说明按时间显示为普通
  assistant 消息；工具输入和结果统一使用现有工具信息框，结果默认收起并可点击展开。
  Provider outcome 新写入会优先保存结构化 `model_dump`，旧 `ChatResponse repr` 继续兼容；
  `reasoning_content` 与 `<think>` 块不进入公开消息流。
- 多步骤任务的右侧消息流现在把 child checkpoint 的 `todos / active_step_id /
  proposal_state.messages` 与 `workflow_effects` 按稳定 call id 关联：顶部显示总步骤和当前
  步骤，每一步可独立展开，收纳该步公开说明与工具；工具行压缩为一行，二次展开才显示输入
  和结果。新 `workflow_spawn` 支持 2～8 个简短 `plan_steps`；旧任务只有一个总 objective
  时，按已持久化的公开 assistant/tool call 序列恢复可读步骤，不展示或推断隐藏思维。
- 新建本地项目的位置选择改为 Agent 工具交互，而非 Session/输入框设置：
  `project_directory_select` 在任何项目写入和 child workflow 前暂停同一 Attempt，消息流显示
  轻量卡片，让用户选择父目录、修改子文件夹名并预览最终路径。后端拒绝非空目标、路径穿越、
  Windows 保留名和非法字符；durable workspace 只允许在无 child、无写 effect 时执行一次
  `task_default → user_path` CAS 迁移，随后原 Run 恢复，后续文件工具和 child 继承新目录。
  输入框不再显示常驻“项目文件夹”，Session 也不会记住或自动套用上一次目录。pending request
  按 Session 隔离，并只在当前选中 Run 仍为 `waiting` 时显示；切换或离开等待态后卡片直接移除。
- 左侧 Harness Inspector 默认视图改为“Agent 执行过程”：首屏只显示当前状态、用户需要
  做什么，以及“理解请求 → 开始任务 → 决定使用工具 → 使用工具 → 整理回复 → 任务完成”的
  轻量时间线。每一步点击后才显示可读输入和结果；工具输入优先显示公开的真实参数，不再显示
  `raw_arguments_ref`。六层生产链、ReAct、Run/Provider/Canonical、原始 JSON 与错误码完整
  保留在默认折叠的“技术记录”中。
- Workflow stage、summary progress 与 artifact 消息现在端到端保留 canonical
  `root_run_id`；user、assistant 与工具消息始终显示完整 Session 历史，只有 workflow
  progress/stage 跟随当前选中 Run，缺少 Run 身份的旧 workflow 记录 fail closed。停止父
  Run 后，child 的 durable `cancelled` 终态会覆盖旧的 `running/waiting` 读模型并停止
  计时，后续新 Run 不再继承上一任务的部分进度。
- 自动化证据：相关 Workflow/Harness 后端组合 `174 passed`；消息流、WebSocket 与
  Harness 前端聚焦 `108 passed`，TypeScript + Vite production build PASS。唯一主实例
  PID `13260`，backend `8100`、Vite `5173` 与 `/health=200`；`workflow.db` 中
  `execution_runs` 无非终态记录，启动后没有新增 Run provider invocation（独立记忆
  reflection 仍可按自身计划运行）。真机 Session
  `dfcbaac7-c330-4e81-af63-b5abb10055f5` 真机确认右侧为普通 Agent 说明、原有
  `read_file` 工具信息行和普通最终回复；左侧默认显示 8 步轻量时间线，`read_file` 步骤可
  展开真实 `path` 输入，技术记录保持折叠。Inspector 改造聚焦 `39 passed`，production
  build PASS。本轮停止语义补充验证为后端相关组合 `76 passed`、前端全量
  `100 files / 889 tests passed`、TypeScript 与 Relay production build PASS。真实 Session
  `5574fd84-e2d5-4207-a5c5-47e79f19ded0` 中，父 Run
  `3f3bf02322745544ab3d41d15e9f1b21` 启动 `durable_task` child 后点击停止，父子均持久化为
  `cancelled`，界面保持“已取消 / 空闲”且 12 秒内步骤数、更新时间和 `6/9` 进度不再变化；
  新 Run 启动时旧 child 卡片和进度均不可见，数据库非终态 execution/workflow Run 均为 0。
  Session 历史筛选回归补测为前端全量 `100 files / 890 tests passed`、TypeScript 与 Relay
  production build PASS；同一真实 Session 切换 Run 时，5 条历史请求和 5 条取消回复始终
  可见，只有所选 Run 自己的 `6/9` Workflow 进度出现。本轮持久化公开 trace 修复后，
  相关后端 `83 passed`，前端全量 `100 files / 892 tests passed`，TypeScript 与 Relay
  production build PASS。真实 Session `5574fd84-e2d5-4207-a5c5-47e79f19ded0` 的 Godot
  Run 回放出 18 次 Provider 记录和 13 个 child workflow tool effect；Windows 消息窗口
  实际显示普通 Agent 说明、`file_write` 等工具输入框及默认折叠的结果，点击结果后可看到
  `bytes_written=8381`、`path=scripts/GameWorld.gd` 的真实 outcome。本轮步骤绑定补充验证为
  后端相关 `57 passed`、前端全量、最终聚焦 `22 passed` 与 TypeScript/Vite production build PASS；同一 Godot
  历史 Run 的 checkpoint 读取到 11 条公开操作说明和 13 个 child tool effect，Windows
  消息窗口实际恢复出 11 个可折叠操作步骤，每步显示工具数量，整体保持轻量消息样式；
  终态为取消/失败且所有已观察操作都成功时，额外显示未完成的“验证与交付”终止步骤，不再
  形成“已取消但 11/11 全完成”的矛盾状态。Agent 项目目录工具补充验证为后端相关
  `186 passed`、前端全量 `100 files / 897 tests passed`、TypeScript 与 Vite production
  build PASS。

## 2026-07-30 里程碑：桌宠透明主界面与消息栏宽度修复

- 撤销主桌宠窗口的整块深色背景，恢复透明桌面效果；Live2D 角色画布与点击区域从旧版
  “贴右角色列”改为主窗口水平居中。
- 独立消息窗口默认宽度从 `440px` 调整为 `700px`、最小宽度调整为 `640px`，Harness
  观察区打开时不再把右侧消息流和输入框挤成窄栏。
- 验证证据：前端全量 `99 files / 882 tests passed`，TypeScript 与 Relay production
  build PASS；当前源码 Windows 实机确认透明桌面、人物居中、消息窗口实际
  `701×602px`，左右两区和输入框均正常，backend/Vite 均为 `200`。

## 2026-07-29 里程碑：全局 UI 暗色统一

- 新增暗色语义主题层，主窗口、设置、Provider、新手引导、账户、能力中心、Skill Store、
  反馈及共享授权/澄清/等待/审批弹窗不再各自维护白色页面样式。
- Memory、ContextTrace、Context usage 和消息页原有暗色设计保持不变；工具轨迹继续使用
  紧凑工具 UI，公开执行进度不伪装成隐藏“思考过程”。
- 设置页删除已退休的 Harness Supervisor 与自动恢复入口；语音按钮继续明确禁用，等待
  Realtime 接入。
- 验证证据：前端全量 `99 files / 882 tests passed`，TypeScript 与 Relay Vite production
  build PASS。当前源码 Windows 实机检查主窗口、设置、能力中心、Skill Store、反馈、账户和
  独立消息页均为暗色；主窗口输入区恢复正常，backend `/health=200`、Vite `200`，
  embedding worker 存活。

## 2026-07-29 里程碑：工具轨迹与公开执行进度重新分离

- 消息页恢复工具调用/工具结果原有的紧凑工具 UI；工具开始和结束不再各自生成一张重复的
  紫色“思考过程”卡。
- 只有模型主动对用户公开的工作说明才作为“执行进度”保留，使用普通紧凑消息样式直接展示；
  历史 `reasoning-summary:*` 线协议继续兼容，但 UI 不再把它称作“思考过程”。
- 同一条公开说明先流式显示、再落为 durable 进度时，前端会替换临时气泡，不会重复显示。
- 旧会话中已经保存的“准备使用某工具/某工具已完成”固定模板在显示层过滤，原始 durable
  记录不删除；重新打开旧会话也只看到工具轨迹，不再看到重复摘要。
- 原始 provider reasoning 仍只投影无文本 activity 信号，不发送、不保存，也不要求 Agent
  输出隐藏思维链。
- 回归证据：后端 Presenter / parity / history `75 passed`，前端消息流与 WS `42 passed`，
  Harness census `141 items / 0 unmapped`，TypeScript + Vite production build PASS。真实
  DeskPet 窗口重载旧会话后，旧固定模板卡为 0，原工具轨迹与“隐藏执行进度”开关仍可见；
  隔离源码实例 backend `/health=200`。WebView 自动化无法把焦点下钻到文本框，因此未把
  未实际发送的新消息标成真机通过。

## 2026-07-29 里程碑：Harness 常驻 Supervisor 删除

- 删除 `HarnessSupervisor` 与它的 50ms 全局协调轮询。现在没有常驻 Harness 协调任务；
  durable 事件只唤起一个可合并的短生命周期 `HarnessReconciler`，当前积压处理完即退出。
- 连续 500 次触发只保留一个短任务；关闭超时会先取消并确认零残留，再关闭 Driver。
  启动时会逐页清空 child command、signal、recoverable Run 与 delivery，不再停在前 16 条。
- delivery 即时积压会处理到空；失败重试使用有退避的一次性计时器，到期才再次唤醒，
  不恢复常驻轮询。Workflow Driver 每个活动 Run 的事件跟随仍属于该 Run 唯一
  `LiveRun.task`，不是全局协调 authority。
- 严格子 Agent 攻击回归 `180 passed`；最终完整 Harness
  `788 passed, 4 xfailed`。authority 为 DML `1`、run map `1`、supervisor task `0`、
  presenter `1`、transaction starter `53`，manifest/source 无漂移。

## 2026-07-29 里程碑：PPT 冻结 Provider 与可编辑文字校验收紧

- `ppt_pro` child workflow 的 LLM 与 reranker 不再读取进程全局默认值，而是从自己的
  execution Run 恢复冻结 provider plan；缺 Run、缺 UOW、空计划或 provider 不存在均
  fail closed。
- 可编辑文字门禁允许真实渲染器把 `6.3%` 等数字提升为独立原生 callout，同时按页面视觉
  顺序和出现次数核对数字。重复数字缺失、两项指标乱序、上下文缺失及省略号截断不会被
  独立数字误掩盖；editable contract `154 passed`，PPT 相邻全量 `359 passed`。

## 2026-07-28 里程碑：Run 预留与可折叠执行摘要完成

- 新顶层消息在 Provider、Host 和 Context 准备前先广播确定性的
  `chat_v2_run_reserved(session_id, run_id, request_id, turn_id, task_scope_id)`。前端立即把
  本地用户气泡绑定到该 Run；边界尚未建立时发送的后续消息留在本地延迟队列，等
  `chat_v2_run_started` 给出可信 conversation boundary 后再按原 `request_id/turn_id`
  发送为同一 Run 的 continuation，不会误建第二个顶层 Run。
- 本地发起的新 Run 总是成为前台选择；当前 Run 终止后，界面自动切到最新仍在执行的 Run，
  真正独立的后台 Run 保持后台。Harness Inspector 在 projection 仍为 `starting` 时显示
  “正在建立 durable Run…”，不再把尚未落盘误报成 Session 归属错误。
- 无 authority 的新 root 不再共用 `session:<sid>` Context CAS key，而是使用入口已确定的
  `task_scope_id`；同 Session 两个真正独立的新 root 因而不会争用同一初始 Context snapshot。
- 消息页新增可单条折叠、可全局隐藏的“思考过程/结果判断”卡。它只保存可公开的执行摘要：
  来自模型已公开 narration 与工具生命周期，显式清除 `<think>` 块；原始 provider reasoning
  只产生无内容 activity 信号，隐藏思维链不会发送到 UI。
- 摘要以 `projection_kind=workflow_progress`、`context_visibility=exclude` 和稳定
  `reasoning-summary:*` identity 写入所属 Run 的 SessionDB，历史加载可恢复，但普通 Context
  组装不会自动摄入。Agent 只有显式调用只读 `run_details_inspect(run_id)` 才能 page-in
  同 Session 的公开摘要、脱敏工具输入/结果与最终回答；该工具绝不返回隐藏思维链。
- 聚焦回归：后端 Run/Context/摘要查询/build identity `81 passed`；前端连续发送、WS、
  摘要卡和 Inspector `59 passed`；Python compile 与 TypeScript/Vite production build
  PASS。当前源码 Tauri 真实点击复现使用 Session `default` /
  Run `d832ba2ca5be5746b0512c0712eed1a8`：连续发送 UUID 和“帮我打开 Godot 游戏编辑器”后，
  两条消息始终可见，第二条从“等待 Agent 读取…”变为“Agent 已读取”；工具轨迹和多条摘要
  实时出现，全局隐藏/恢复与单卡折叠均通过。单次授权后 `godot__detect` 成功；后续 Godot
  启动链仍按既有工具授权与 nonce 规则独立推进，不作为本里程碑的启动成功证据。

## 2026-07-28 里程碑：运行中输入收口为单一续接操作

- 消息页输入区收口为“一个文本框 + 一个动态发送/停止按钮”。当前选中任务可续接时，
  placeholder 与状态行明确说明消息会发送到当前任务并在安全边界读取；新话题和语音入口
  移到标题栏图标，不再与主输入动作并排竞争。
- 用户续接消息气泡新增真实状态：durable FIFO 接管后显示“等待 Agent 读取…”，原 Driver
  完成 conversation bind 后由 Harness continuation observer 投影“Agent 已读取”；绑定失败
  或绑定前取消显示失败。观察投影异常不影响 durable Run。
- `chat_v2_error` 若属于某条 continuation，只更新该消息状态，不再把仍在运行的目标 Run
  错误标成 failed。
- 聚焦验证：前端输入/WS/消息气泡 `43 passed`，RunKernel continuation `63 passed`，
  Harness bootstrap + execution continuation UoW `45 passed`，main task-scope/continuation wiring
  `29 passed`，TypeScript + Vite production build PASS。当前源码 Tauri 已检查实际布局：
  输入区只保留唯一动态主按钮，标题栏显示新话题与语音图标，Harness 字体设置保持不变。

## 2026-07-28 里程碑：Prepared Tool JSON 边界收口

- `PreparedToolCall.arguments_json()` 成为 Frozen JSON 到 Host canonical JSON 的统一递归
  投影；Harness event、Code Workflow、subagent、capability receipt/retry、fingerprint 和
  ToolRegistry handler 不再浅拷贝 `final_params`，内部不可变 tuple 不会泄漏到外部 JSON。
- `DriverRuntime` 在发布 `tool_requested` 后才允许执行；投影/严格 JSON 校验失败会在零物理
  调用下形成 `tool_argument_projection_invalid` 工具失败并进入既有模型重规划，而不是把
  根 Run 直接标成通用 `driver_failed`。外部非法 tuple/set 等仍 fail closed。
- 聚焦自动化 `125 passed`。当前源码 Tauri 在同一 `default` Session 以新 Run
  `5735337ea3a254c1acd739a5aad5070d` 真实启动既有 GemCollector Godot 编辑器并
  `completed`；ledger 中 `process_start.argv` 为 JSON list，日志四类同源错误均为 0。
  旧失败 Run `88b2305bce215a13a6fbecc205b3dcf3` 保持 `failed`，未改写历史。

## 2026-07-28 里程碑：旧 Catalog 僵尸 Run 与超大工具向量化收口

- `ReActDriver.prepare_recovery` 与恢复流内的确定性 `tool_catalog_stale` 现在共用永久故障
  结算：释放 lease、保留原错误码、一次性把旧 Run 标为 `failed`，并拒绝让尚未绑定的用户
  continuation 继续进入不兼容快照。现行 `HarnessReconciler` 不会对同一个旧 Run 做常驻重试。
- 记忆 fanout、实时 enqueue、backfill 和最终写入四处共同限制向量候选：只处理
  `user/assistant` 普通文本且单条不超过 32,768 字符；tool、截图 data-URI/base64 和超长
  payload 被跳过，启动回填会清理历史无效向量。
- 聚焦自动化 `78 passed`。当前源码 Tauri 对原故障 Run
  `ddec38ff523959fe8455b1446af59072` 真机恢复只记录一次 catalog mismatch，随后 durable
  状态为 `failed`、终态 error code 为 `tool_catalog_stale`，之后无重复恢复；记忆库
  `eligible_missing=0`、`ineligible_embedded=0`，未再出现数百段截图 embedding。
  Windows UI 显示“已连接”、不再显示“努力工作中”，输入框可真实输入且发送按钮恢复可用。

## 2026-07-28 里程碑：同 Session 类型化指代可复用已有任务工作区

- 新 root 默认保持任务隔离；“这个/刚才/之前/已有/继续”等表达先转为 typed
  `TaskReference`，候选必须有稳定 root/task 身份。只有唯一 `resolved` 才加载该 Run；
  `ambiguous/missing` 不注入任意原始历史，而是让主 Agent 根据候选摘要向用户确认。
- 精确 root 优先于 scope fallback，共享 scope 的不同 Run 不会串历史；带未知名称的单候选
  不能自动命中。最终只取所选 Run 末 `12 rows / 16,000 chars`，截断提示计入硬预算。
- 历史工作区只由 Host 可信当前 workspace 与历史 `task_scope_id` 推导，并验证目录存在、未越界
  后注入精确 Windows 路径和项目标记；模型不能用参数扩大范围。Context 明确要求打开/继续/运行
  任务复用已有内容，不重新创建。
- `run_shell` 对尾随 `&` 的 GUI 启动自动脱离标准流，超时清理不再因 GUI 子进程继承 pipe 而
  永久等待；工具描述同时引导模型优先使用 `process_start/app_launch`。
- 聚焦回归 `222 passed`，修改模块 `py_compile` PASS。当前源码 Tauri 真人 E2E root
  `6959c1e036b35182a07f6f631321bf8d` 找到并直接启动既有
  `task-1455b19de08db0a680401ec9d4928c32/GemCollector`，没有 `workflow_spawn`，启动后继续
  `screen_capture/screen_key`；真实 `Gem Collector (DEBUG)` 窗口显示游戏运行与
  `Score: 100`。本项未把“吃完所有黄色点”误记为通过。唯一 provider 为 `sf-glm-5.2`，
  无 HTTP 402、Ollama fallback 或 provider unknown。

## 2026-07-28 里程碑：GLM-5.x 长流式响应不再被固定 180 秒截断

- OpenAI-compatible SSE 的固定 180 秒整次响应 deadline 改为“已解析模型事件之间最多
  180 秒无进展”的滑动 deadline。content、reasoning、tool call、usage 和 final 都会续租；
  Relay heartbeat 不会续租。网络静默仍由 120 秒 read timeout 限制，root Run 仍保留
  15 分钟产品总时限。
- 聚焦自动化覆盖持续进展超过单个 deadline、真正无进展超时和底层 async generator 关闭；
  Provider/Coordinator/ReAct/Workflow 联合回归 `119 passed, 2 skipped`。
- 当前源码 Tauri 真人测试 Run `b6c640910fcc576aab64db60e97668fa` 使用唯一
  `relay-cloud/sf-glm-5.2`。Provider invocation
  `2f9599de22f6e4fc6d89ba3fd48fce7c8f92aef30ac4b4c82aa80ff55b8a3d07`
  从 dispatch 到 outcome 约 184 秒，越过旧上限后正常 `completed`；UI 从“努力工作中”
  回到完整回答，未出现 HTTP 402、Ollama fallback、ReadTimeout 或
  `provider_dispatch_unknown_after_handoff`。

## 2026-07-28 里程碑：Harness Agent 执行时间线与 Provider 输入投影完成

- 左侧 Harness 监控新增按 durable 时间排序的 `Agent 执行时间线`，仍严格使用
  ProductTurnPreparer → RunKernel → Driver/Profile → AgentLoop/Provider →
  Tool Executor → Canonical 投影六层。每一步显示输入、结果、状态、时间和可观察决策，
  完整结构按需展开；不建立第二套执行状态，也不暴露或伪造模型隐藏思维链。
- 在不删除六层时间线和原始技术字段的前提下，新增“当前真实生产链路”和“ReAct 循环视图”。
  前者列出当前真实代码路径并说明 `prepare_direct_run` 是跳过旧 IntentTriage/plan gate 的
  兼容直通方法，不是独立思考层；后者通过稳定 tool call id 精确连接每轮
  Provider 判断、工具行动、工具观察和 `Driver.signal` 回灌。每条记录同时标记原始账本来源
  与界面解释来源，handoff 断连同时展示用户可读原因和未删减的原始错误。
- Harness 左栏底部新增持久化横向字体设置，范围 `100%～170%`、首次默认 `125%`，统一缩放
  标题、状态、正文、错误与原始 JSON；普通/全屏模式共用且不影响右侧消息区。当前源码 Tauri
  已真人验证 `140% → 160% → 140%` 即时生效并保留设置。
- workflow schema 升到 v24，新增不可变
  `execution_provider_invocation_inputs`。Provider claim 后保存经过 Trace/memory 双重
  redactor 的输入投影；64 KiB 内保存完整结构，超限则保存有界摘要。Inspector 通过现有
  UoW left join 读取；旧 Run 没有投影时明确说明只剩 request hash。
- 当前源码 Tauri 真人点击 E2E 通过：Session `default` / Run
  `eb91d2c52c2856ccb23d0c4b306af2a1` 使用唯一 provider chain
  `relay-cloud/sf-glm-5.2`，真实发送“请只回复：Harness输入可见测试完成。不要调用工具。”
  并收到指定回复。新 Provider 详情显示 `message_count=90`，最后一条 user message 与输入
  完全一致，且不再出现旧账本缺失提示；本轮没有 HTTP 402。
- 复杂只读审计 Run `535ada60c2f1567fbc1c81b2e2a0020b` 真机验证 5 轮 ReAct：
  `tool_search → tool_describe → tool_activate → run_shell → Provider`。前四轮工具调用均按
  稳定 call id 与 Action/Effect 账本精确配对，`run_shell` 成功后结果回灌；第 5 轮因
  `RemoteProtocolError` 在 handoff 后断连而 fail closed，非 HTTP 402，provider chain
  只有 `relay-cloud/sf-glm-5.2`，未使用 Ollama。UI 同时保留原有 15 步时间线。
- 修复失败终态只回给任务发起窗口导致多窗口状态分裂：`chat_v2_error` 现在投递给 originator
  与同 Session peers，Live2D 同时以 canonical terminal `run_event` 兜底关闭工作气泡；
  输入栏对已终止的 failed/cancelled Run 回到“空闲”，但 Harness 继续保存原始错误。当前源码
  真机复验 Run `dbcfdc43cc4c583a97c629892e237fc9`：Live2D 不再显示“努力工作中”，
  消息面板左下角为 `✓ 空闲`，Inspector 仍显示 `tool_context_persist_failed`。
- 本项聚焦回归：后端 main task-scope wiring `29 passed`；前端 InputBar `13 passed`；
  TypeScript + relay Vite 完整构建 PASS。
- 聚焦回归：后端 Provider/Schema/Main wiring `55 passed`；前端消息面板 `4 files / 22 passed`；
  TypeScript project build 与 `git diff --check` PASS。

## 2026-07-28 里程碑：GLM 驱动的 Godot durable task 真机闭环

- 当前源码 Tauri 使用 `relay-cloud/sf-glm-5.2` 完成真实 UI E2E。最终 Root Run
  `7e292f4a88b05ef999ec0e9de2b13c6b` 由 DeskPet 自行检索能力、创建多文件 Godot 项目、
  执行 import/headless/runtime 验证、根据真实报错修复 `project.godot`、autoload 和绘制
  warning，并以 `completed` 终态收口。较早的 root
  `bb12960856bb5353a1ee1efc0654fcf6` 虽然其 child
  `child-6e8cbb73daab69f07350d38b2018150c` 已完成项目，但父 Run 在验收端误关应用后于 final
  provider handoff 记为 unknown，不能作为完整成功 Root 证据。
- 最终生成项目位于测试用户目录的
  `workspace/task-1455b19de08db0a680401ec9d4928c32/GemCollector`。独立启动 Godot 后真实
  渲染 `Gem Collector (DEBUG)`，玩家、10 个宝石、计分、倒计时和 `TIME UP` 状态均可见，
  窗口持续运行完整 60 秒。当前 Computer Use 的瞬时按键不能形成 Godot 所需的物理按住事件，
  因此移动与 `R` 重开不记为已获自动化证据。整个生成和修复过程均由 DeskPet/GLM 完成，
  验收端未代写项目。
- 能力检索改为多词 OR 匹配并按命中比例排序；`run shell command execute` 现在能稳定返回
  `builtin:run_shell`。错误的裸 capability 名会得到唯一候选建议，连续两次
  `tool_describe` 失败后由 loop guard 强制回到短检索，避免模型无限猜测
  `run_command`/`write_file`。
- durable child 固化父 Run 选定的 provider/model 和能力快照；provider 连接前超时可协调安全
  重试，流式墙钟超时不会直接丢失 child checkpoint，恢复继续使用原 provider，不会切换到
  本地 Ollama。
- Context 压缩器、durable workflow proposal 压缩和前端 Context 压缩线统一为“不晚于模型
  窗口 70%”。压缩结果进入 checkpoint 后再继续执行；压缩调用失败只保留原上下文并继续任务，
  不把 Run 打成失败。`sf-glm-5.2` 的实时启动证据为
  `context_window=1000000 threshold=0.70 trigger_tokens=700000`。
- Harness 左侧观察区在原始六层账本上提供面向用户的任务名、当前动作、等待原因、最近进展、
  “你现在需要做什么”和折叠的技术详情；技术字段仍可展开排障，但不再作为默认阅读入口。
- 本轮最终聚焦自动化：Context/压缩/checkpoint/resume/授权/能力检索 `159 passed`；Harness 与 Context 前端
  `4 passed`；TypeScript project build PASS。真实运行日志未出现 HTTP 402 或余额不足，
  provider chain 仅启用 `sf-glm-5.2`。

## 2026-07-27 里程碑：Run 超时收口、可信 Shell cwd 与 Godot 任务接地

- 产品 15 分钟 turn timeout 不再因错误的 `_send_chat_final(iterations=...)` 调用再次抛错；
  它会取消 exact root Run、发送结构化 timeout 终态，并结算被取消的 provider invocation。
  transport 前取消记 failed，handoff 后取消/流关闭记 unknown，不再遗留永久 claimed。
- `run_shell` 授权与真实 subprocess 共用可信 cwd。默认 cwd、相对 cwd 和 Shell `HOME`
  都绑定任务 workspace；写范围开启时禁止 cwd 逃逸，修复 backend 下生成字面量
  `~/Desktop/...` 假目录的问题。
- Godot 能力包升到 `1.0.4`，覆盖游戏引擎、2D/3D Demo、角色移动、跳跃、敌人 AI、
  GDScript 及 `Gobot` 常见误拼。Profile Catalog 明确要求多文件软件/游戏项目在首次
  effect 前选择 `workflow.durable_task`，歧义技术名先检索/确认。
- Inspector schema v2 新增只读 activity 派生视图，直接显示当前动作、等待对象、最近进展、
  超过 60 秒的停滞、最近错误和产物数；仍以 execution ledger 为唯一事实源。
- 真人测试继续暴露出 Relay 单回合返回 608 个重复工具调用且末项工具名为空；ReAct 现于
  durable admission 前将单批限制为 32，并把超量/空名称分别转换为
  `provider_tool_batch_too_large` / `tool_call_name_missing` 结构化失败，允许模型重规划，
  不再由 `ContractValidationError` 把 Driver 直接打成 failed。
- 自动化：最终后端/能力包组合回归 `180 passed, 1 skipped`；Inspector 前端
  `3 passed`；TypeScript `tsc --noEmit` PASS。
- 当前源码 Tauri 真人测试 Session `35db286a-7a69-4014-b56f-3dca3ddaa289`：
  原始 `gobot 4` 游戏请求先运行 capability search，正确识别为可能的 Godot 4 误拼并在
  写盘前确认，未调用 Go；确认后 `workspace_prepare` 成功落到
  `backend/userdata/workspace/task-69bacdf574a2ab8c95041c2fbbcbc743`。Inspector 实时显示
  `provider_response`，并在 75 秒无新 durable 进展时显示停滞时长。
- 重启验证时，Godot 包因本地已经安装过内容不同的不可变 `1.0.3` 被正确拒绝；源码版本前进到
  `1.0.4` 后，当前源码 backend 健康启动。随后在同一 Session 通过真实点击创建 Run
  `e77ec5a26b2e5d59a8d72458d1a2cffd`：Relay 连续返回纯文本且 `tool_calls=0`，未能进入
  `workflow_spawn`，因此不把 Godot 项目生成记为通过；但整个过程没有再出现空工具名
  `ContractValidationError`。从 UI 停止后，Run 以 `cancelled/user_interrupt` 收口，
  provider invocation 从 `claimed` 结算为带 dispatch ack/outcome ref 的 `unknown`，
  capability scope 成对 unpin，没有悬空 claim。

## 2026-07-27 里程碑：单一 Context/Run 入口与授权资源修复完成

- Text/Voice 生产入口从 `prepare_context` 直接进入 `prepare_direct_run → RunKernel`；
  `route_intent/plan_decision` 只保留兼容代码。旧 IntentTriage 不再抢先回答、短路、
  澄清或生成第二份 plan，主 Agent 成为普通会话唯一认知 authority。
- failed/cancelled 根 Run 通过 durable `session_terminal` sink 幂等写回所属 Session；
  下一轮 Context 组装前 read-through。投影绑定 Session epoch，修复前旧失败只在
  Run `auth_epoch` 与当前 epoch 相同时回填。
- `workflow_spawn` 授权 selector 精确绑定 root/catalog/profile，并仅从可信 host context
  取得 workspace；全局授权资源审计无空 selector。空资源契约归一为
  `authorization_scope_missing`，不再直接形成 `driver_failed`。
- Deferred capability 支持唯一裸名称规范化；歧义继续 fail closed。
- 聚焦回归 `177 passed`；Harness 分组除两项当前脏工作区冻结清单漂移外，
  `274 + 146 + 193 passed`。真实源码 Tauri E2E Session
  `e9d345e4-55da-4de7-a33b-8156076ea26d` / Run
  `6048fc2701b75ec383550753a758040c` 完成原始中文命令，六层均 completed，并在
  `C:\Users\Administrator\Desktop\春天的散文.txt` 写出真实文件。

## 2026-07-27 里程碑：Harness 细粒度观察、Context 历史与 Provider dispatch 修正完成

- 主消息页现在每次打开都默认显示工具调用轨迹，历史 localStorage 隐藏值不再让新一轮
  工具过程永久不可见；用户仍可用顶部工具按钮临时隐藏/显示。
- 新增默认展开的左侧 `Harness 运行观察`：按所选顶层 Run 展示
  ProductTurnPreparer → RunKernel → Driver/Profile → AgentLoop/Provider →
  Tool Executor → Canonical 投影六层消息。它通过
  `harness_inspector_snapshot` 读取现有 UoW 账本，不维护第二套状态机。schema v2 展示
  ProductTurnPreparer 三阶段输入/输出/起止时间/耗时、provider output/policy、tool
  prepared/effect/outcome 与 canonical payload/correlation；所有层和记录默认折叠，
  展开后直接显示完整原始信息，不再判断或遮罩敏感字段。
- 左栏改为固定高度内部滚动和窄型滚动条；仅在用户接近底部时自动跟随，向上阅读时保留位置并
  提示新轨迹。消息增长不再持续撑高页面。
- Harness 顶部不再铺开 Run 标签；左栏始终只渲染当前选择的一个 Run，未选择或原选择失效时
  自动落到当前 Session 最新 Run。内置下拉框列出该 Session 全部 Run，可切换查看历史记录；
  “全屏查看详情”以同一份实时账本打开近全屏弹层，并自动展开所有层和步骤详情。
- 消息内容区顶部原有的 Run/任务标签栏也已移除；Run 浏览和切换统一收口到 Harness 下拉框，
  避免页面顶部重复占用空间。
- 右上角圆形 Context 入口展开后显示项目名、根目录、Context 组成及 token 时间折线图。
  state.db schema v22 新增 durable `session_context_usage_history`，持久化 provider 实际
  Context 大小与 compaction 前/后 token，历史 Session 重启后仍可恢复；详情展开后直接显示原文。
- workflow schema 升到 v23，新增不可变 provider invocation audit。coordinated
  OpenAI-compatible dispatch 在 durable handoff 后不再内部重试；读超时/cancel/断连
  记录 exact exception type/reason/message 并 fail closed。`ConnectTimeout` 明确表示连接
  尚未建立，现记录为 `transport_not_sent` 并由 coordinator 最多安全重试一次。
- 用户 Session `501eeac7-650a-4982-96f9-c52adebb718b` 的 Run
  `9a84a2f9a22a56e89040620bfa9ed63c` 有 12 次 provider invocation；前 11 次 completed，
  最后一次约 10.066 秒后以 exact `ConnectTimeout` 被旧逻辑误归类为
  `provider_dispatch_unknown_after_handoff`。该历史账本保持原样供审计，新运行应用上述
  未发送判定和单次安全重试。
- 本次增量验证：后端 Harness/Provider/Context/迁移组合 `84 passed`；前端 Inspector、
  Context ring/modal `11 passed`；TypeScript project build 通过。
- 当前源码 Tauri 真人点击验收通过：新 Session
  `b558c5e5-01a7-4c01-9ab3-09f5406cf460` / Run
  `977690de63995aae93403723577516f1` 经真实 Relay 返回“Context验收通过”；六层轨迹显示
  ProductTurnPreparer 三阶段的真实起止时间与耗时；后续按用户决定改为详情展开后默认显示
  全部原始信息。右上角圆环由 0% 更新到 1%，弹层在窄消息窗内显示实际项目根目录、
  `4,985 / 950,000 tokens`、Context 组成与单点历史图；state.db v22 中存在对应
  `provider_attempt` 持久化采样。
- 用户历史 Session `c41a922c-0e19-4506-9c6d-9e82d6e24413` 的错误来自 Run
  `03c4c8862aa0523dbd81bb7dec20b780`：唯一 invocation 在 handoff 后约 10.034 秒进入
  unknown。旧 schema 没保存异常类型；结合当时 10 秒 connect timeout，最可能是
  `ConnectTimeout`，但只能作为推断，不能伪报为确定事实。新 v23 运行会保留 exact 原因。
- 用户 Session `cec00990-d308-4179-b30f-3dfaa6f83aef` 的 Run
  `d6f571d1df735c25be774351b3e59221` 是另一类错误：6 次 provider invocation 全部
  completed，但最后一次耗时 422.8 秒，超过旧 Context OS scope 的 300 秒 orphan TTL，
  后续 `tool_activate` 因 scope 被回收而失败。修复后 ReAct Driver 在完整 provider 回合
  pin scope；必要时从同一 Run 的 durable snapshot 精确恢复。`tool_describe` nonce 仍
  一次性并绑定 exact scope/revision/capability，但不再另设 60 秒模型思考期限。
- 新终态写入结构化 `failure_layer/failure_code`，六层面板不再靠错误文本正则猜发生层；
  历史事件缺字段时明确标注“旧事件未记录子层”。工具与 Attempt 同时显示原始账本状态和
  continuation/effect 观察终态，避免把 stale `running/prepared` 误读成正在执行。
- 自动化：本轮后端相邻组合 `150 passed`；前端全量
  `95 files / 863 tests passed`，TypeScript 与新增/重写模块 scoped ESLint 全绿。
- 当前源码 Tauri 主消息页真人点击 E2E PASS：面板默认打开、折叠/重开、工具卡默认显示、
  “新话题”创建 Session、发送后创建/选中新 Run、执行中状态与最终空闲均已验证。
  Session `f20e3b55-b720-49f3-b1eb-40296d24c853` / Run
  `2cff9d444730553bbfd5bc1c3d7e2b0f` 的真实 `memory_search` 显示两次 provider
  completed、工具 outcome succeeded、Attempt continuation succeeded，最终回答 `5`。
- 本轮又在当前源码 Tauri 中先打开 `cec00990…`，确认六层历史审计为 9 个工具成功、
  2 个工具失败和 422.8 秒 provider 回合；随后真人点击“新话题”创建 Session
  `db2369a4-a450-4285-980c-008f967bffff`，发送真实 `workspace_prepare` 请求并完成 Run
  `c439af02fa015b21b5b701ee6b97901a`。UI 从“思考中”回到“空闲”，默认工具卡与六层
  完成态可见；日志确认 provider scope 成对 pin/unpin 且 backend 来自当前源码目录。

## 2026-07-27 里程碑：Companion 长期成长 Task 0～16 完成

- 成长信号不再由前置正则判断。每条消息先正常 committed，`ProductTurnPreparer` 的模型
  预处理返回 typed `growth_signal_kind`，host 只做枚举/身份/幂等校验并单调提升 outbox
  语义；它不改变顶层 `agent.general`、Profile ticket 或 RunKernel 的 Driver 选择。
- 评测失败会把 frozen report、FailureSet、候选失效、reservation 释放、成长事件与下一轮
  reflection job 原子提交；下一轮仍由同一模型吸收失败原因并重新规划，最多两轮。
  genesis reservation 可按 version CAS 重开，迟到评测不能复活已遗忘候选。
- 主消息页“新话题”恢复为真实新 session：点击即分配空 UUID、绑定 exact Companion
  owner、更新默认 route 并切换两个主消息 peer；携带草稿时首条消息只投影到新 session，
  空白点击不启动 AgentLoop。查看 legacy 历史也不会尝试认领旧 session；普通误写仍以
  typed `companion_session_read_only` 在 Run 启动前拒绝。
- 该修复通过聚焦 backend `62 passed`、frontend `32 passed` 与 TypeScript 编译；并在
  全新当前源码 Tauri 进程中完成主消息页真人点击 E2E：空白“新话题”即时切换新 UUID，
  带草稿“作为新话题发送”再次切换另一 UUID，首条 user bubble 即时显示且真实 Relay
  最终回复落在同一新 session。开发态热更新后的旧 WebSocket/store 不作为交付证据。
- S-1～S-5、S-8 已通过真实 Tauri 主消息页点击/输入 E2E（`6/6 PASS`）：
  built-in 低风险成长及回滚、三独立上下文偏好晋升、Reminder 草稿与重启去重、
  单次详细例外、高风险 external-send 在 Auto 下仍等待确认。S-6/S-7/S-9
  确定性自动化 `3/3 PASS`。
- 最终自动化：Companion `639 passed`；Skill/Memory `128 passed`；Harness
  `636 passed, 4 xfailed`；Capability/Workflow `308 passed`；Frontend
  `93 files / 853 tests passed`；TypeScript、`cargo check`、Rust `78 passed`。
  deterministic smoke 为 `639 + 40 passed`，`DECISION: SHIP`。
- Harness authority 保持 DML owner=1、transaction starters=53、legacy survivor=0，
  AgentLoop AST=3800，R4.5 raw/adjusted/core/Kernel 为
  `140270/139761/45032/1277`，public operations=6、unknown=0。
- 最终 worktree 仅剩 `F:/projects/deskpet`；三个上游 Capability worktree 已逐一确认不再
  挂载且路径不存在。所有测试/Tauri 树按命令行、worktree、配置、PID/create-time 与
  Job identity 精确清理，最终 survivor=0；未按进程名广杀。
- [Companion 架构](./COMPANION_GROWTH.md) ·
  [真人结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/manual-results.md) ·
  [最终审计](../plans/2026-07-24-human-anchored-companion-growth/evidence/task16-delivery-audit.md)

## 2026-07-26 里程碑：Companion 生产编排补齐；真实价值门被 Relay 402 阻断

- S-6/S-7 安全自动化通过 `11 + 42 + 26 + 4` 项回归；Windows Job-scoped
  launcher 聚焦 `12 passed`，具备源码 backend、单一 Vite、DEV clock、离线
  PID/create-time/ancestry cleanup、PID reuse 审计和 MAX_PATH fail-fast。
- `s1/l06` 已真实拉起当前源码 backend 到 ready；结束时精确清理 23 个冻结身份，
  释放 9449.92 MiB，18100/15173 listener=0、survivor=0。
- 后续 `s1/cg-s1s8-main-l19` 已用 Computer Use 在真实主消息页发送精确长期纠正。
  当前源码确认把 committed message 原子写入 ingress outbox，dispatcher owner-fenced
  消费为 `MESSAGE_INGRESS`、创建 reflection job，并由 Product Harness native terminal
  sink 写入同 root 的 `run.final`。聚焦 `56 passed`，l18 启动暴露并修复
  `ServiceContext` 白名单遗漏；l18 精确清理 survivor=0、释放 11004567552 bytes。
- Task 13 follow-up 已把 reflection typed result → candidate build → frozen independent
  evaluation → RiskPolicy/activation dispatcher 接到唯一 production composition root；
  后台 Run 继续复用 RunKernel/Driver，且补齐 provider unknown、failure/replan、精确 build
  claim、启动 manifest 并发读。生产提交锚点 `e8290719`；Companion 全量最终复核为
  `588 passed`。
- `s1/cg-s1-growth-l44` 已在真实 `DeskPet · 消息` 主消息页发送两条明确纠正，
  `message:default:42/43` 均形成 GrowthEvent 并进入生产 reflection Run；真实 Relay
  返回 HTTP 402 `account balance insufficient`，两项 job 在 attempt 3 后
  `failed/background_run_failed`，所以不能形成真实 candidate/evaluation/activation
  正向价值样本。S-1 为 PARTIAL/BLOCKED，S-2～S-5/S-8 未执行；Task 15、Task 16 与整个
  计划仍未完成。
- l44 结束后按专属 Job/PID/create-time 清理 22 个记录进程，释放
  `9795608576` bytes，unknown scope=0、survivor=0；没有用协议直注或脚本回放替代真人 E2E。

## 2026-07-25 里程碑：Companion Task 14 确定性质量门完成

- 唯一 `companion.db` 已演进到 schema v4 并由 `CompanionStore` 持有，owner-domain 数据按
  profile generation 隔离，写事务统一 `BEGIN IMMEDIATE + WAL + FULL`。
- 新增唯一 `GrowthAuthorityRouter`、durable phase/journal、generation ingress gate
  和共享 `RevocationBarrier`。Task 13 已在 Product ingress 关闭期间完成
  `legacy → preparing → companion` 单指针切换，当前恢复为
  `companion/generation=5`；marker 后只可前滚或进入 `paused`，不存在双写窗口。
- 新增 typed `[companion.growth]`（完成项默认 ON、独立 pause），以及 Rust 内存
  Ed25519 signer、跨 Rust/TS/Python canonical command、可信 Relay/local owner 和
  `IdentityReadyGate`。`main/identity_bind` 与 `message-panel/companion_action` 独立
  challenge/lease/seq，shared secret 不能伪造 mutation authority。
- state.db v21 已加入 session owner、消息 ingress outbox、Companion excluded projection
  route/outbox 与 redaction receipt；workflow.db v22 已加入原子 RunStartSnapshot、
  Provider invocation/outcome、Run/effect/delivery fence 与 terminal extension receipt。
  所有 execution writer 共用 `ExecutionWriteLane`；Provider claim-before-transport、
  handoff unknown、provisional retract 和最后物理 effect fence 已接生产组合根。
- Task 4 的 `PreferenceResolver` 已随 Task 13 投入生产：scope-first request override、显式纠正、
  recent/long-term 双层偏好、三独立场景晋升、衰减/冲突、同事务 forget 重算和 winner-only
  Run dependency。旧 JSON 只按 `legacy_local_profile` 幂等导入并保留为只读升级残迹，
  当前 Relay owner 不会取得其归属。
- Task 5 的 `CompanionRuntime`、严格 Clock seam、durable foreground busy gate、原子
  budget claim、safe-only lease recovery 与有界 shutdown 已进入生产。profile coordinator
  先以 `start_prebound()` 验证 exact durable owner/generation/binding epoch，再冻结
  `IdentityReadyGate`；失败会暂停 Runtime，不能开放 Companion chat。
- Task 6A 已完成 checked tool build/effect authority、hash-covered confirm-only、
  Auto 不可绕过的 explicit decision + one-shot grant、Executor 双重校验、readonly
  `memory_recall` scope、background Kernel adapter 与三阶段 dispatch port。生产
  CatalogGate/owner runtime lease 和 MCP/local-runtime adapter 仍由 Task 7 + Task 6B 接线。
- Task 6B 的 product-neutral 三阶段 dispatch slice 已完成：Registry prepare 不产生 effect，
  ToolExecutor 只在 start 越过物理边界，started/not-started/unknown 使用既有 execution
  handoff receipt，ACK 后立即释放短 scope fence、completion 在 fence 外等待。生产
  CapabilityPlatform authority/主组合根注入仍随 Task 7 收口。
- Task 7 第一批已合入 owner-aware Capability schema v2、workflow schema v18、
  run/process catalog stamp、CatalogGate/runtime-set 协议和 17 个独立 v2 Skill Pack；
  slash 已回到主消息 Harness，Loader script 执行面关闭，并修复 Windows 深层 immutable
  pack 路径校验。独立完整性审计仍发现 frozen Skill 三入口、同事务 exact lease、
  Store-backed runtime ledger 与 Personal Workflow interpreter 等 blocker，因此本批只记
  foundation，不把 Task 7 标为完成。
- Task 7 Skill/Workflow 分片已把生产 Loader 收窄为 legacy user，并以独立 managed
  projection 发现 17 个 first-party pack；三入口使用 typed frozen scope，v2 schema 离线
  自包含，Personal Workflow 的 topology/effect/checkpoint seam 已建立。完整解释器属于
  Task 10，catalog/runtime authority 仍在 Task 7 收口。
- Task 7 runtime/authority 集成已把 durable execution scope authority 和
  `CapabilityStoreRuntimeSetLedger` 注入生产 Platform；Run/ToolBatch 不再有第二份 scope
  事实源，owner runtime generation、set/member、launch claim、start/health/abort/recovery
  已落同一 Capability Store。native Job/MCP adapter 与最终 projection commit 仍由 Task 9
  activation saga 补齐，当前缺失时 fail closed。
- Task 8 Candidate 生产组合已加入 Companion schema v3、workflow schema v19 receipt 与
  v20 exact material：
  显式 Skill/Workflow admission、严格 proposal/fence、确定性 candidate identity、
  durable builder coordinator、child terminal + immutable draft receipt/material 原子提交，
  candidate-only Builder output、四来源安全 materializer、Manager validated-ref 复核，以及
  Store 单事务 candidate composition 均已落主线。reserved child scheduler/ToolSet 已随
  Task 13 authority cutover 注入生产组合。
- Task 9 已加入 immutable risk facts、checked-in evaluation suites、冻结只读 memory、
  逐 case durable evaluation execution、确定性 RiskPolicy、activation saga/guard 与唯一
  platform façade。可执行候选必须有 host-issued decision、exact code digest 和专用 risk
  ack；通用 Auto 不可替代。Task 13 已接入 Manager 静态 lifecycle prepare seam；真正
  active pointer 仍只由 Manager 与 CapabilityStore binding CAS 修改。
- Task 10 的 Personal Workflow interpreter、workflow schema v22、single-capture Skill
  selection/activation、typed turn authority、child independent ReadyGate 与 execution
  fence 已完成。生产 `AgentLoop` 和既有 `ContextAssembler/SkillComponent` 现统一注入
  Manager-backed `SkillPackSnapshotResolver`，managed projection 只保留发现/selection
  职责；RunContext owner identity 与 exact snapshot-ref recovery 已耐久化并 fail closed。
- Task 11 的 Companion schema v4、V2 Reminder create/list/cancel、occurrence CAS、
  daily frequency reservation、overdue expire-no-catch-up、delegated draft 与 exactly-once
  delivery outbox 已完成。外部动作即使 Auto 开启仍等待独立确认，确认后恢复原
  Run/decision/call/effect；缺 receipt 时 unknown 且不重发。三个 V2 handler 已进入生产，
  `legacy.list_reminders.v1` 与进程内 Reminder writer 已退休，不保留旧名 alias。
- Task 12 已把 Companion notification/outbox 投影到当前 owner-generation 的默认主消息
  Session：route relocation、clear 后 absent replay、redaction tombstone、live/history
  同 envelope、owner switch fence 与 provisional stream 清理均已接入。详情只经
  owner-fenced `companion_detail_get` 分页读取，并由 Companion `Vc` 与完整 Platform
  `VpVector` 双读保护；前端已有成长卡片、详情 modal 和相互隔离的 evaluation/activation/
  action confirmation。重要 growth 事实即时通知，普通偏好进入确定性日摘要；owner
  generation 删除会先审计再 supersede 旧卡，投影崩溃立即释放精确 claim 重试。冷启动
  identity challenge 竞态已用连接内缓存和有界 restore 重试收口。可信 bind 会恢复现有
  owner inbox，或创建新的空 UUID inbox 并让前端切换；已有内容的 legacy `default`
  session 不会被 Relay owner 认领。
- Task 13 已删除旧 `SkillCodifier/CandidateProposal/ToolPathRecorder`、Presenter/Voice
  codify 回调、裸 candidate confirm 入口和进程内 Reminder 实现。启动顺序固定为
  Capability rehydrate → Companion composition → Product Harness（ingress closed）→
  cutover → projection/runtime adapter → ingress open。历史 Capability ToolSpec 只允许
  精确重算 fingerprint v1 后 CAS 迁移，未知漂移继续 fail closed。生产 `SkillLoader()`
  不再隐式挂载用户目录；非空 legacy Skill 在缺少 immutable pack 发布 receipt 时会在
  marker 前零部分导入并阻断，而不是清单化后错误切换。
- Task 14 已用 32 项可执行故障矩阵、真实 ProductVenue/Kernel 性能组合、隐私/权限门和
  construction audit 收口确定性验收。Companion 全量 `540 passed`、组合根
  `39 passed`，smoke 输出 `DECISION: SHIP`；四类 p95 回退为
  `+5.442%～+6.202%`。Kernel 逻辑拆出 product-neutral Admission collaborator 后物理
  LOC=1277，仍保持唯一六个公开操作；Companion construction 的
  raw/adjusted/core=`129997/129488/44063`，unknown=0。Task 15 正在执行真实 provider
  与主消息页价值矩阵。
- durable catalog 现为 stdio MCP 冻结 DeskPet adapter、真实 launcher 或 npx package
  bundle/package-lock 与 launch config digest；无法证明 host build identity 的 MCP tool
  可以被发现，但不能进入 durable Run。
- 自动化：Task 1 聚焦及 legacy 回归 `195 passed`；Task 2 Python `98 passed`、
  前端聚焦 `14 passed`、Rust `78 passed`；Task 3 backend `250 passed`、前端
  `16 passed` + tsc。真实源码 Tauri 主消息页验证双 WebView active lease、durable
  start/manual waiting/cancel terminal，并在设置页开启 auto 后对账
  `mode=auto/generation=1`；Tauri/Vite 清理后 `survivor=0`、8100/5173 无监听。
  Task 4 相关组合 `178 passed`，Facts 回归 `62 + 21 passed`；Task 5 focused
  `59 passed`、扩大回归 `182 passed`；Task 6A 分组 `31 + 65 + 84 + 24 + 4 passed`，
  扩大组合 `151 passed`，既有 timing case 隔离重跑通过。Task 7 第一批回归为
  Companion `210 passed`、Capability `165 passed`、Skill/slash `83 passed`、
  execution build manifest `7 passed`，相关 pytest survivor=0。Task 7 剩余 catalog/
  runtime authority、Task 6B production dispatch 与通知 UI 已由后续任务补齐；反思业务和
  正式 cutover 仍待完成，不能把本里程碑标成完整成长闭环。
  Task 10 最终门为后端 `443 passed`、前端 `11 passed + tsc`，主消息页真人问答返回
  27。Task 11 Reminder/外部确认组合 `102 passed`、Companion 全量 `394 passed`、
  MCP identity/catalog `23 passed`；主消息页真人键入 `Reply only 35: 70/2=?` 后真实
  provider 返回 35，Run durable completed。最终 Tauri 32-PID 树释放
  10055626752 bytes，8100/5173 释放且 survivor=0。
  Task 12 backend `126 passed`，前端 `8 files / 88 tests passed` 且 tsc 通过；真实
  主消息页由生产 growth event→forget 链生成 `growth_forget` 卡片，真点击详情 audit
  页看到权威 `audit_events` 事实，F5 history 恢复且 outbox delivered。最终 Tauri
  32-PID 树释放 11108134912 bytes，端口 listener=0 且 survivor=0。
  Task 13 聚焦 `166 passed`、Companion `476 passed`、Skill/Preference `127 passed`、
  Capability `251 passed`、Workflow `53 passed`、前端 `851 passed` 且 tsc/manifest/
  diff check 通过；R4.5 LOC/transaction-starter 预算门转 Task 14 收口。真实源码 Tauri
  Relay mode 在空历史 owner inbox `26f5276d-69e9-42b0-ba65-b4a8988b86d6` 真人点击输入
  “请只回复：Task13主消息页通过”，UI 回复“Task13主消息页通过 ✅”并回到空闲，
  `chat_v2_final` 使用同一 session id。最终精确清理 roots `16040/13620` 的 21 个进程，
  释放 9749762048 bytes private memory，survivors=0，8100/5173 listeners=0。
  Task 6B dispatch slice 聚焦与邻接回归 `108 passed`。
  Task 7 runtime/catalog 聚焦 `18 passed`、关键组合 `155 passed`；Companion 扩展为
  `259 passed`，Capability 仍为 `165 passed`。Harness authority/parity/LOC gate 已按当前
  单一 UoW/LiveRun 事实刷新并通过；超时 pytest 精确清理 survivor=0。
  Task 8 foundation 组合 `140 passed`，生产 Candidate/Builder 组合 `39 passed`；
  Companion 全量 `335 passed`、Capability 全量 `249 passed`、Harness 相邻按文件
  `4 + 62 + 4 + 14 passed`。Capability 监控树 survivor=0、释放 982175744 bytes private
  memory；所有超时大组合均按 exact PID/create-time 清理为 survivor=0。
  Task 10 最终组合为后端 `443 passed`、前端 `11 passed + tsc pass`；真实源码 Tauri
  主消息页点击问答返回 `27`、状态回到“空闲”，durable Run completed。E2E 精确清理
  19-PID 树、释放 9653006336 bytes private memory，`8100/5173` 无监听，survivor=0。
- [当前架构](./ARCHITECTURE.md#16-伴生智能体成长能力现状) ·
  [Companion 模块](./COMPANION_GROWTH.md) ·
  [执行计划](../plans/2026-07-24-human-anchored-companion-growth/plan.md) ·
  [Task 0 基线](../plans/2026-07-24-human-anchored-companion-growth/evidence/baseline.md) ·
  [Task 3 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task3-results.md) ·
  [Task 4 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task4-results.md) ·
  [Task 5 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task5-results.md)
  · [Task 6A 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task6-results.md)
  · [Task 6B dispatch 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task6b-dispatch-results.md)
  · [Task 7 foundation 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-foundation-results.md)
  · [Task 7 Skill/Workflow 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-skill-workflow-results.md)
  · [Task 7 runtime 集成结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-runtime-integration-results.md)
  · [Task 8 foundation 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task8-foundation-results.md)
  · [Task 8 生产集成结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task8-production-integration-results.md)
  · [Task 9 评测与激活结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task9-evaluation-activation-results.md)
  · [Task 10 最终验证结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task10-runtime-verification-results.md)
  · [Task 11 Reminder 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task11-reminder-results.md)
  · [Task 12 通知与详情结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task12-notification-results.md)
  · [Task 13 cutover 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task13-cutover-results.md)

## 2026-07-24 里程碑：单主 Session 通用行动与可执行能力包（full-audit 部分完成）

- 生产入口只有一个主 Session；每条普通新消息创建独立顶层 Run 并固定
  `profile=agent.general`。运行中续聊进入原 root 的 durable FIFO。窗口只是 Run 投影，
  不再存在 Code/普通模式路由。
- `ProductTurnPreparer` 向父模型提供真实 PreparedToolSet、能力目录与 Profile Catalog
  职责；模型通过 `workflow_spawn` 选择 child Profile。Host 签发一次性 durable
  `ProfileLaunchTicket`，`RunKernel` 只按固定 root Profile 或 ticket 绑定 Driver，
  不读取原文做正则/领域分类。
- TaskGoal、PlanVersion、Attempt、完整 FailureSet、profile ticket、能力
  revision/binding/operation/runtime lease 和 running-root continuation 均进入同一个
  `workflow.db`（schema v16）。失败回到同一 `agent.general` 父模型创建新 Attempt；
  取消/恢复以唯一 `LiveRun.task`、`start_lock`、`driver_lock` 和 Run CAS 收敛。
- Manual 任务级授权与 Auto 直行共用 durable grant/Receipt；Auto 只省略 DeskPet 授权
  等待，不绕 Windows UAC/外部登录，也不跳过能力完整性、测试和健康检查。
- 可执行能力包支持 local JSON 子进程 tool、受管 MCP、版本化激活/回滚和当前 root
  catalog refresh；无匹配能力时模型可启动 `workflow.capability_build`，验证后在同一
  root 立即调用生成工具。builtin Godot 样板当前为 `1.0.2`。
- 当前自动化：聚焦 `206 passed`；backend
  `5961 passed, 16 skipped, 9 deselected, 4 xfailed`；Frontend
  `85 files / 820 tests` + tsc；Rust `73 passed` + build/check；Godot pack
  `13 passed, 1 skipped`；capability smoke、construction/authority gate 全 PASS。
  严格 last-mile 首轮因 Node PATH 缺失诚实 `skip`，显式注入 `DESKPET_NODE` 后
  7/7 PASS、0 skip，`DECISION: SHIP`。Windows Computer Use 已完成 VS-1/VS-2：
  模型在同 root 查询并激活真实工具，Auto 下完成 27-byte 文件与 PowerShell/Git Bash
  双重校验；首次校验失败由同一父模型吸收 failure set 后重规划成功。Manual、Auto 与精确
  launcher 树清理已有部分证据（最终 13/13 PID 退出、释放 8533.9 MiB，主 8100 保留）。
  B-1～B-7、S-1～S-6 完整矩阵尚未结算，且 B-4B 需要用户亲自处理 Secure Desktop
  UAC，因此本里程碑仍不标完成。
- [实现/验收计划](../plans/2026-07-23-universal-action-and-capability-packs/plan.md) ·
  [手工矩阵](../testcase/2026-07-24-universal-action-capability-platform/manual-test.md) ·
  [Harness 当前事实](./AGENT_HARNESS.md)

## 2026-07-22 里程碑：Agent Harness R7 全量门禁与主消息线程真机闭环

- 模块状态：**R7 完成**。点击桌宠“消息”进入的主消息页 S-1～S-7 全部真人 PASS，覆盖只读解释、同线程追问、DeepResearch、PPT durable 审批、重启恢复、busy 输入阻止/取消隔离和工具失败诚实投影。
- 这是当时的 R7 验收边界：主消息页一次只提交一个任务，计划中的 Code 并行工作台未做
  专项真测。该产品形态已被上方 2026-07-24“单主 Session、多独立 root、无模式切换”
  架构取代；仅保留为历史证据。
- 最终门禁：Harness `512 passed, 4 xfailed`；Workflow `703 passed`；完整 backend `5736 passed, 19 skipped, 9 deselected, 4 xfailed`；TypeScript/Vite/Vitest、Cargo 和 last-mile 全绿，`DECISION: SHIP`。
- authority/parity/复杂度：parity 141/141、unmapped 0；唯一 DML/run map/supervisor/presenter；LOC raw/adjusted/core/Kernel=`33,633/33,124/5,948/896`，public ops 6、unknown 0；性能/内存 comparison 11/11 PASS（token writes 0、metadata 7,512 bytes/run、10k strong refs 0）。
- [最终架构图](../plans/2026-07-20-agent-harness-simplification/target-architecture.md)已保存；[R7 结果与截图索引](../plans/2026-07-20-agent-harness-simplification/evidence/r7-main-thread/results.md)可复核。

## 2026-07-21 里程碑：Agent Harness R6 生产切换与旧链删除

- 模块状态：**R6 已默认启用，新请求只走 Product Venue → RunKernel；生产 activation 为 `open/generation=1`。**
- Text、Voice、Code、DeepResearch、PPT、Subagent/Team child 共用可信 RunContext、一次 route、ReAct/Workflow 双 Driver、唯一 EffectBatchExecutor、UoW 与 Presenter。
- 删除 AgentLoop legacy tool/subagent runtime、全局 subagent registry/waiter、DeepResearch/PPT 专用 production starter 与 AutoResume `_run_chat` 旁路；WebSocket 断开只分离 presentation，不取消 durable Run。
- provider/model/session/product 配置在启动前冻结；Context OS 把本轮 `PreparedToolSet + eligibility` 持久化到 Run，Text/ReAct 与 Code 恢复均重新校验 identity/policy/visibility/schema，Code effect 使用完整 trusted execution context；Workflow context 与 ReAct provider-loop 就绪后才开放服务，错误 fail closed。
- R7 已补齐 Tauri 稳定 `client_request_id/client_turn_id` 的生成/复用、主线程 busy 提交阻止与 S-1～S-7 Windows UI 矩阵。
- 自动化门禁：Harness `495 passed, 4 xfailed`；parity `141/141, unmapped=0`；LOC raw/adjusted/core/Kernel `32,913/32,404/5,949/900`，Kernel public ops `6`、unknown `0`。
- 最终结构图保存在 [`target-architecture.md`](../plans/2026-07-20-agent-harness-simplification/target-architecture.md)，完整验收证据见 [`r6-results.md`](../plans/2026-07-20-agent-harness-simplification/r6-results.md)。

## 2026-07-20 里程碑：DeepResearch v7 简化编排、过程可见与文件交付

- 模块状态：**新 run 默认 `deep_research/v7`；核心 Tokio 真实 UI Spike PASS，完整多类别发布矩阵 PENDING。**
- v7 主图缩为六节点，manager 拆题后为每个子方向启动独立 research child，逐项做质量分类、
  有界诊断续跑和统一综合；v1-v6 保留恢复兼容。
- 真实 run `aa61dcc...` 为 engine completed / business partial：4 child 中 3 valid、1 insufficient；
  `dr-2` attempt 2 成功，最终报告 5 来源/3 域并唯一投递报告、Artifact、final_assistant。
- Spike 连续定位并修复三类生产缺陷：Search Gateway 全空时缺少可验证 URL 兜底、
  FetchDocument/legacy extract 契约漂移、workflow final 正文被宠物气泡 `(完成)` 覆盖。
- 门禁：后端相关 `76 passed, 2 deselected`；前端 `24 passed` + TypeScript；Computer Use 真输入、
  真发送和最终报告气泡已验证。证据见
  `plans/2026-07-19-deepresearch-simplification/spike/result.md`。
- AC-9～AC-11 增量真测：run `9c42a6a...` 在单张卡显示 4 个真实方向与逐项状态，attempt 2
  原位更新且通用子代理面板无重复行；最终标准文件卡四个操作均经真实点击。原报告继续位于既有
  `DeepResearch` 目录，另存副本与原文件 SHA-256 相同；同 userdata 重启后仍恢复 4 个方向和唯一文件卡。
- 最终增量门禁：后端 focused `81 passed`、默认配置/注册/恢复相邻套件 `142 passed`、前端 focused
  `80 passed`、TypeScript、Vite production build、目标 lint 与 Computer Use TC-3 PASS。应用当前仍以源码 backend 运行，供继续体验。
- 已知边界：本次只完成一个语义类别的正向技术调研 spike；来源权威性和政策/市场/无证据/恢复矩阵
  留待后续正式发布验收，不能用同题重跑冒充 distinct 场景。

## 2026-07-18 里程碑：DeepResearch v6 默认发布与答案契约稳定性完成

- 模块状态：**新 run 默认 `deep_research/v6`，完整 T11/T12/T13、发布验收与 release identity fixture 受控提交均 PASS**。
- 真实 source-Tauri 场景在 5.08s 内完成，并从国家统计局返回 `140828万人` / `954万人`。
  通用官方归档发现、有界抓取、ref-only 证据、完整性门禁、terminal commit、session
  投影、artifact 渲染和同目录重启均经过真实链路。
- 重启后仍只有一条最终回答和一个 artifact，启动恢复投递数为 0；历史恢复不再展示
  artifact 原始 JSON，引用已使用可访问的 `www.stats.gov.cn` 地址。
- 门禁：后端 v6/official/fetch 91 passed；v5/recovery/delivery 281 passed；前端
  30 passed；TypeScript/Vite relay build 通过。证据和 scope 见
  `plans/2026-07-17-deepresearch-answer-contract-stability/execution-results.md`。
- 真实 UI 已覆盖 completed、partial、insufficient/generate-now 双击和重启/history；用户指定的 `zai-org/GLM-5.2` 在 Relay 中以 `sf-glm-5.2` 提供，基础模型与预分析模型均已切换。重启后的最终真实账户验收为 GLM 出站 5/5 HTTP 200、DeepSeek 出站 0、HTTP 402 为 0，UI 返回“GLM 最终验证通过”；2026-07-19 又暂时将 alias/canonical 上下文画像钉为 1M，源码 Tauri/Context usage 实证有效 950K、compaction 750K，并通过真实 SC-STATS-2 run `81090268…` 的 final/artifact/delivery 闭环。模型/上下文回归 `58 passed`；此前前端配置回归 `27 passed`、后端配置 `6 passed`。
- 最终 release identity 已重跑 completed、partial、generate-now insufficient 与同 userdata 历史恢复；最终门禁为后端 `815 passed`、前端 `822/822 tests passed`、Rust 73 tests，TypeScript/Vite/cargo check 全部通过。

## 2026-07-17 里程碑：DeepResearch v5 Win11-only 交付

- v5 已成为新 run 默认版本；建模、query、dimension analysis、证据准入/readiness、缺口补研、报告审计/修复、三态终态与 continuation lineage 已接入生产 durable workflow。
- 自动化验证：本轮 v5 核心 `254 passed`；DeepResearch/Search Gateway/Playwright 联合回归 `559 passed`，2 个 Chromium 启动/空闲回收时序失败隔离复跑 `2 passed`；前端 77 files / 816 tests、TypeScript、Vite production build、Rust check 通过。此前后端宽回归 1274 passed / 1 skipped；全后端套件的既知 vector embedder worker hang 仍未伪报为全量通过。
- Win11 安装版真机：教育现状与国家计划问题完成 v5 主链并诚实交付 `insufficient_evidence`；继续补研生成 child run，重启后两条历史与动作恢复。真实样本未达到完整报告准入门，因此报告人工质量门不适用。
- **质量故障修复与真实 UI 复验**：修复前五类矩阵的 101 documents / 0 admitted passage 作为失败基线保留。修复后 AI Top 10 `9670a357...`（5 来源、质量 75、`partial`）、国家统计局人口指标 `51781063...`（11/5 有效/第一方、4/4 核心、质量 80、`completed`）、产品比较 `2b79cd88...`（9/1 来源、质量 60、`partial`）均由真实 DeskPet UI 完成；DB/UI elapsed 误差 <2s，默认投影无 raw query/URL。TC-UI-NOW run `6ea6a3a4...` 的单次真实点击在约 0.227 秒内 observed，deadline 后唯一业务终态完成且硬失败 0。核心检索/建模/交付故障与立即生成主链已关闭，Gate F 总体为 **PARTIAL**；updater、卸载、立即生成恢复分支与完整固定场景审计仍待完成。
- 最终 NSIS：`DeskPet_0.6.0-beta.9_x64-setup.exe`，529,333,947 bytes，SHA-256 `1495E432F4314A9B83991724FDB411B1088FF13DFF1673B02FB6F56014F26A38`。内含 Playwright 1.61.0 / Chromium r1228，浏览器 exe SHA-256 `28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1`。
- 验证范围仅 Win11 x64；未启用、安装或依赖 Hyper-V/VM/ISO/Windows Sandbox，也未触发系统重启。Win10 兼容性保留为后续独立计划。
- **DeepResearch 节点级耗时遥测**：v5 每个 durable 节点的起止时间、耗时、attempt 与成功/等待/取消/失败终态已保存到 workflow DB/trace，并同步安全镜像到 structlog 与 `metrics.jsonl`；fetch 内部的 robots、Scrapling、HTTPX、抽取和浏览器/Jina fallback 有可轮转 metrics 分段，但尚未形成 durable child spans。修复了 v5 legacy stage metric 恒为 0 的观测错误。定向测试 `43 passed`、workflow 回归 `76 passed`、v5/effect 回归 `68 passed`；本次只增加观测，不调整现有超时与降级行为。

> **维护方式**: 本文件是项目完成度、里程碑、worktree 与已知问题的唯一事实源；模块生产链路由同目录专项架构文档维护。
> **用途**: 一页看清整个项目（所有并行工作流）的当前状态。新 session / 子代理
> 先读 [`index.md`](./index.md)，再由索引进入本文件或对应模块架构。

---

## 1. 项目一句话

本地部署的桌面语音宠物：Live2D 桌宠 + 工具调用 + 长期记忆 + 技能系统；正式语音链路正从
本地 VAD/ASR/TTS 迁移为中转站 Realtime 语音。Tauri (Rust shell) + Python backend + React 前端。

---

## 2. 活跃工作流（git worktree 并行开发）

> 项目用多 worktree 并行开发，每个 worktree 独立分支 + 独立 dev 端口
> （见 `scripts/dev-worktree.ps1`）。下表为各分支当前状态。
>
> **2026-07-23 实际工作树核对**：当前只保留 `F:\projects\deskpet` 的
> `master`。Agent Harness 实施产生的 48 个临时 worktree 已在最终交付
> `7429fd59` 合入 `master`（merge `aa7f3835`）后删除，对应 47 个临时
> `codex/*` 分支也已删除。下表其余条目是历史分支状态，不代表当前已挂载 worktree。

| Worktree / 分支 | 负责模块 | 状态 | 文档 |
|---|---|---|---|
| **master** | 主线 — beta-100 ready 集成线 | ✅ 活跃，持续 merge | `README.md` |
| `feat/companion-code-v2` | Slash 命令 + /goal + 多 agent team + 工具 partition + prompt cache | ✅ 全套实现 + 真桌宠 E2E PASS；**已 merge master**（`84b8ce0` merge superpowers B3；分支 tip 0 commits ahead of master）；功能 flag 默认 OFF | [plans/2026-05-25-companion-code-skill-upgrade/](../plans/2026-05-25-companion-code-skill-upgrade/) |
| `feat/fun-interactions-2026-05-31` | 12 个趣味交互（drag squash / tap burst / dizzy spin / time-of-day mood） | ✅ 已 merge 到 master (2f54960) | — |
| `fix/restore-ui-pack-2026-05-31` | UI 修复恢复（工作树 reset 丢失的 6 项） | ✅ 已 merge (fd55c9f) | — |
| `live2d-rewrite` | Live2D 渲染层重写 | 🟡 停滞（分支最后提交 2026-05-31；master 已「装回 Live2D SDK + Hiyori」走实用路线，本重写分支似被搁置/探索化，见 §3 mesh 引擎） | — |
| `worktree-memory-upgrade` | 记忆系统 v2 升级 | 🟡 停滞（Stage 0/1 已合 PR #2；分支最后活动 2026-05-23、0 commits ahead of master。记忆后续工作实际已转入 master 主线，见 §4 2026-06-01 严测 / 2026-06-02 审计 #1-#4） | [plans/2026-05-22-memory-system-upgrade/](../plans/2026-05-22-memory-system-upgrade/) |
| `feat/memory-stage2-followup-f1f2` | memory Stage 2 后续 F1/F2 + 真测挖出 F3/F4 | ✅ F1/F2/F3/F4 全修，单测全绿；**已 merge master**（分支 tip 0 commits ahead of master，旧「未 merge」标注已过时） | [plans/2026-05-24-memory-stage2-followup.md](../plans/2026-05-24-memory-stage2-followup.md) · [F3/F4 缺陷](../plans/2026-05-31-memory-tools-flag-gating-bugs.md) |
| `feat/multi-provider-management` | 多 LLM provider 管理 | 🟡 停滞/未启动（分支仅 1 个提交、最后活动 2026-05-11，内容只有 OpenSpec proposal，无实现代码） | — |
| `tool-last-mile-upgrade` | 工具调用 last-mile（artifact + receipt + verify gate） | ✅ 已合 master（详 v3 优化） | [plans/2026-05-23-tool-last-mile-upgrade/](../plans/2026-05-23-tool-last-mile-upgrade/) |

**端口隔离**（`scripts/dev-worktree.ps1 -BackendPort N -VitePort M`）：
- master: 8100 / 5173（默认）
- 各 worktree: 8200+/5273+（手动指定，避免冲突）

---

## 3. 核心功能模块完成度

| 模块 | 状态 | 关键文档 |
|---|---|---|
| **前端 UI / 暗色主题** | 🟡 **Workbench 单窗工作台已实现，r14 实质审计补测中** —— 透明桌宠壳、Live2D/sprite Canvas 与独立消息窗均已移除；当前为侧栏 + Chat/Skills/Artifacts/Settings 四视图。2026-08-11：账户 AuthAdapter/登录注册事件/侧栏账户入口和 Live2D 锁依赖、表情动作消息链全部删除；Vitest `533 passed`、Rust `74 passed`、companion `647 passed / 10 skipped`、MCP `21 passed`、typecheck/build/check 全绿；`kimi-k3` 真实出站 HTTP 200 并回显 `KIMI3_OK`，r12 的 402 阻塞已解除。r13 形式门达到 `READY_FOR_AUDIT`，但独立实质审计否决了设置持久化、几何异常分支、运行期断连、删除即时态和冷启动性能的证据充分性，故未 finalize；r14 正重新冻结并补真机 primary evidence。已退役的单钥匙 Keychain 模块、renderer IPC/binding 与 Rust `keyring` 依赖已移除；最新 `.app` 干净启动和打开设置页均无 macOS 授权弹窗。 | [UI 架构](./UI.md) · [workbench-ui](../plans/2026-08-04-workbench-ui/) |
| **DeepResearch v7 简化编排 / bundled Playwright** | ✅ **本轮 required 范围 PASS** — 新 run 默认 v7；六节点 manager graph 拆 2～6 个方向，每方向独立 child，弱结果诊断后最多续跑一次，再统一综合。单卡显示真实方向/状态/attempt/来源数，内部 attempt 不重复；标准文件卡四个动作、既有 `DeepResearch` 目录保存及同 userdata 重启恢复均经真机验证。完整多类别语义矩阵保留为后续候选。 | [架构](./DeepResearch.md) · [plan](../plans/2026-07-19-deepresearch-simplification/plan.md) · [results](../plans/2026-07-19-deepresearch-simplification/spike/result.md) |
| **语音管线** (Realtime/VAD/ASR/LLM/TTS) | 🟡 **旧链已安全关闭，Realtime 待接入** — `[voice].enabled=false` 出厂默认生效；普通启动不再导入、创建或加载 Silero VAD、faster-whisper、EdgeTTS/CosyVoice，两个前端窗口不连 `/ws/audio`、不申请麦克风，按钮明确提示等待 Realtime。误连返回 `voice_temporarily_disabled`，`/health.voice` 可观测。旧实现只保留显式开发兼容，不是生产入口；待 relay 提供 WebRTC/Realtime 契约后，新的 Realtime 入口必须进入完整 `ProductTurnPreparer`。 | [Harness 架构](./AGENT_HARNESS.md) · [Context OS V1 plan §12](../plans/2026-07-13-context-os-v1/plan.md) |
| **桌宠 supervisor** (P5-S1) | ✅ 生产可用 | `README.md` §桌宠 supervisor |
| **长期记忆 + 自动总结** (P4-S20-D / memory-v2) | ✅ Stage 1/2 ship；F1-F5 全修；严测 4 Phase（33 用例）；**2026-06-02 审计修复 #1-#4**：FATAL-A 自动 backfill 兜底 + FATAL-B 静默降级告警 + MemEval 字面vs改写召回（改写 Recall@5=1.0 证 dense 真工作）+ **出厂点亮 facts_extract/enhanced_retriever/cross_key_merge 语义事实记忆栈**（真机 E2E 待跑）| `README.md` §长期记忆 + [memory-system-status](../plans/2026-05-23-memory-system-status.md) + [严测 spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) + [审计+最佳实践](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| **工具层** (registry + 权限 + 熔断 + last-mile + v3) | ✅ 生产可用 — 2026-07-09 优化 `file_glob` 默认递归扫描：剪枝 `node_modules` / `__pycache__` / `.uv-cache` / `backend/assets` 等重型生成目录，返回 `skipped_dirs/skipped_count` 诊断元数据；显式 root 指向被跳过目录仍可访问，避免兼容性倒退；pytest `test_deskpet_tools_file.py` 33 passed。2026-07-08 补修 ArtifactCard 文件按钮：DeepResearch 报告目录加入 Tauri artifact 白名单，前端按钮增加 pending/success/error 状态反馈；真机点击 `打开` / `复制路径` / `在文件夹中显示` PASS。 | [tool-layer-optimization-v3](../plans/2026-05-24-tool-layer-optimization-v3/) · [file-glob 优化](../plans/2026-07-09-file-tool-scan-optimization/plan.md) |
| **Search Gateway + FetchExtractService** | ✅ **2026-07-15 cooldown 隔离完成** — 默认 ON 的进程内 Gateway 统一百度/DDG/Google CDP/Bing CDP 与可选 SearXNG；provider 共享健康状态已升级为按失败类型配置的 closed/open/half-open circuit，generation/token CAS 保证每个 open generation 仅一个 probe，所有 provider open 时按最早 eligible + 配置序号做受控救援。request budget/diagnostics 保持隔离；429 尊重有界 `Retry-After`，403 与 429 分流，probe cancel/失败/旧 token、cache hit 与安全 metrics 均有回归。连续真实政策→WebGPU run 仍产生 22 attempts / 2 probes 并完成，跨主题 cooldown 连坐已消除。 | [架构](./SEARCH_GATEWAY_DEEPRESEARCH.md) · [results](../plans/2026-07-15-search-quality-cooldown-support/results.md) |
| **DeepResearch v4 技术情报 + 可审计聊天进度** | ✅ **2026-07-15 完成并默认 ON** — 新 run 默认进入 immutable `deep_research/v4`，v1/v2/v3 保留历史与在途恢复；provider limiter/permit 复验/empty-aware rescue 消除宽 fan-out cooldown 连坐。宽主题使用稳定 taxonomy、实体去重和页面噪声过滤；报告采用质量优先的 3～8 项发布门，固定提供一页式执行摘要、组合建议、分主题核心变化/价值/成熟度/风险/日期/逐项引用与方法局限；证据质量不足时摘要和正文都只能“先补证据再决定 PoC”。`zero_candidates`/`insufficient_evidence` 不生成假报告或 Artifact；Session 13 阶段显示每一步“动作 + 结果 + 降级原因”并提供幂等 retry。Xiaomi 同进程最终连续 run `43e851a0...` 与 `59975eb1...` 均为 5 项/5 引用并通过当前 17 项专业报告门；旧 `66720bcf...` 及建议口径不一致的早期样本均降级。报告聚焦 `114 passed`；后端最后代码全量 `4643 passed / 10 known failures` 无新增；前端 `811 passed`、tsc/build PASS。 | [架构](./SEARCH_GATEWAY_DEEPRESEARCH.md#16-宽主题技术情报-v4-与专业报告2026-07-15) · [results](../plans/2026-07-15-deepresearch-wide-topic-reliability/results.md) · [testcase](../testcase/2026-07-15-deepresearch-wide-topic-reliability/deepresearch-wide-topic-manual-test.md) |
| **fake-completion VerifyGate** | ✅ 接电；**出厂默认 strict**（`config.py:293` `verify_gate_mode="strict"`，2026-06-23 `7fd79c83` shadow→strict）（+9 claim patterns 含 code 场景）;strict 真机不误杀 + 单测 31/31;**2026-06-22 修 shipped bug：ephemeral 救援子代理从不读 `[tools.verifier].ephemeral_subagent_model`→恒复用主 LLM**（`build_agent` 注入处直接 `local_llm or cloud_llm`）→新增 `_resolve_ephemeral_provider`（`backend/main.py`）按配置克隆专用 model provider（缺省/失败回退主 LLM）+ 15 单测全绿 + 真机 boot-log 实证 `model='sonnet' base='gpt-5.5'`（`772c4291`） | [v3 §WI-T2.1](../plans/2026-05-24-tool-layer-optimization-v3/00-PRD.md) + [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) + [ephemeral 真机验证](../plans/manual-results-2026-06-22-ephemeral-model/RESULTS.md) |
| **历史 Code 模式工作流纪律** (superpowers 全套) | ⚪ **仅保留历史证据/兼容读取，不是当前产品模式** — 旧 persona、plan 卡、偏好与 verify strict 的实现和既有 E2E 证据仍可追溯；2026-07-24 起新生产记录不再通过 Code mode、`task_type="code"` 或 code-only tool exposure 选择 persona/Profile/Driver/workspace。代码类请求与其他请求一样从 `agent.general` 开始，由模型显式 `workflow_spawn` 合法 child Profile。 | [历史 spec](../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md) · [当前 Harness](./AGENT_HARNESS.md) |
| **技能系统** (17 个 first-party Capability Skill Pack + 自动披露 + marketplace) | ✅ 生产能力基础可用 — first-party Skill 只经 immutable Capability Pack 与 Manager-backed snapshot resolver 提供；`skill_invoke`/slash 使用同一次 Run catalog capture 和 typed frozen scope，Personal Workflow 通过固定解释器与 Effect/UoW 执行。Task 13 已删除进程内 `ToolPathRecorder`、旧 Codifier/CandidateProposal、Presenter/Voice codify 回调和裸 candidate confirm；成长候选改走 durable evidence → candidate → evaluation → activation saga。legacy user Skill 仅作为 migration source/只读兼容面，不再拥有成长写 authority。 | [当前架构事实](./ARCHITECTURE.md#16-伴生智能体成长能力现状) · [Companion 模块](./COMPANION_GROWTH.md) |
| **Companion 长期成长** | ✅ **2026-07-27 Task 0～16 完成并默认生效** — `companion.db` v6、state.db v21、workflow.db v23，模型语义成长判定、唯一 Router、trusted control lease、PreferenceResolver、Runtime、动作策略、owner-aware Capability、candidate/evaluation/activation、失败吸收与重规划、Personal Workflow、V2 Reminder、durable 通知/history 与详情 UI 均已进入唯一 production path。旧 Codifier/ToolPath/Reminder writer 已退休。真实主消息页 S-1～S-5/S-8 `6/6 PASS`，确定性 S-6/S-7/S-9 `3/3 PASS`；deterministic smoke `DECISION: SHIP`。 | [模块架构](./COMPANION_GROWTH.md) · [真人结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/manual-results.md) · [plan](../plans/2026-07-24-human-anchored-companion-growth/plan.md) |
| **Agent Harness / 主消息页运行观察** | ✅ **schema v3 语义运行视图与 Session 模型一致性已完成** — workflow/state 完整事实经一致 read cut、keyset 和纯 reducer 生成 Root aggregate 与最多七类实际阶段；blocked 只认结构化 signal，完整 child failure/replacement/root terminal 链显示已接管并完成。左图、消息 activity、右侧 steps 共用一个 Session/root snapshot store；工具使用 default-deny 有界投影并分层折叠，raw payload 不进公共 contract。真实历史 Root 只读复跑为 366 facts/6 phases/29 logical tools/23 shell，projection complete。跨会话 full-surface 后端 29/前端 116，child provider 接线联测 66；S-SRV-1～5 Windows 真机矩阵全部 PASS。 | [Harness 架构](./AGENT_HARNESS.md) · [AgentLoop](./AgentLoop.md) · [testcase](../testcase/2026-08-03-session-model-run-visibility/manual-test.md) |
| **Office 文档生成** (PPT/Word/Excel) | ✅ 生产可用 — PPT 模板填充+AI整页生图+视觉评估闭环;**`ppt_pro` 新工具**(deepresearch 调研→大纲卡确认→首图实测判定:惊艳生图/模板兜底,2026-06-22 ship,旧 `ppt_create` 仍在岗作直传路径;**2026-06-24 真机 E2E PASS — 惊艳生图路径 × doubao-seedream-4.0 端到端跑通,gpt-image-2 全面下线**,`719a0b49`);**Word/Excel 升复杂档**(列表/段内混排/字色/页眉页脚页码/插图 · 数字格式/合并/逐格样式/多图表/嵌图);默认落 `OutPut/{PPT,Doc,Excel}` | [PPT 架构](./PPT.md) · `doc-edit`/`excel-generate`/`ppt-generate` SKILL.md + §4 里程碑 |
| **Agent Loop 优化 7 WI** (tool_choice硬约束/trace/收尾自查/Focus Chain/触发知识/SEARCH-REPLACE降级/ask_clarification) | ✅ 全实现 + 真机 E2E 全 PASS — 7 WI 全 100%(子代理逐批+终评+4代理对抗复审)；WI单测+BC回归全绿；真机抓修3真bug；WI-7 完整问答闭环真点击 PASS；**4代理对抗审计揪出 WI-5 真生效缺口(chat永不fan-out SkillComponent)→已修(`011aab5`)**；**2026-06-20 续修 WI-5 末环真 bug：main.py 漏把 knowledge_enabled 传给 SkillLoader 构造器→loader 恒滤掉知识片段(真机 total=12 而非 15)→已修+真机复验 `skill_auto_disclosed total=15 names=['windows-path-debug'] top_sim=0.950`**。默认 flag off→BC 安全 | [plan](../plans/2026-06-20-agent-loop-optimization/00-PLAN.md) · [架构档](./AgentLoop.md) · [testcase batch-a/b/c](../testcase/) |
| **上下文连续性 + 图片误触发防护** | ✅ 2026-07-12 完成 — L2 改为独立 newest-tail 并按精确 tool_call_id 保持工具组边界；当前 user row 按 message id 去重；L3/topic embedding 独立限时，超时不再抹掉 L2；`task/web_search` 始终携带同 session 有界连续尾部（8 条），修复自然检索追问丢失上文对象；ContextAssembler 改接 AgentLoop 同一 `deskpet_tool_registry_v2`，不再因旧 `tool_router` 把 `web_search` 静默筛空，并注入基于真实 schema 的工具可用性约束；短澄清轮收敛 `generate_image`，显式生图保持可用；accepted/pending receipt 不算完成证据，短澄清流式假声明延迟并确定性拦截。新增聚焦回归 `65 passed`；Computer Use 真机验证搜索追问实际调用 `web_search`，查询参数明确承接为“命运2 高阶暴君 脉冲步枪”，不再反问对象或声称无联网能力。 | [plan](../plans/2026-07-12-context-continuity-fix/plan.md) · [results](../plans/2026-07-12-context-continuity-fix/RESULTS.md) · [testcase](../testcase/2026-07-12-context-continuity/manual-test.md) |
| **Context OS V1** | 🟡 **核心代码、自动化与真 UI E2E-01～11 已通过；语音 E2E-12 blocked** — `context_os_v1` 已出厂默认 ON；prepared request、gap-free Session coverage、capability hydration、snapshot/CAS、单一压缩 owner、L3/Skill page-in、项目规则与 ContextTrace 已完成生产接线。Windows Computer Use 已完成 Default→OFF→ON 真点击回退验证，OFF 八类工具与 revision 51 golden 精确相等，停机后截图/日志哈希完整；整体不标 complete，因为 relay 尚未提供 VR-0 Realtime/ASR/TTS 能力。 | [plan](../plans/2026-07-13-context-os-v1/plan.md) · [results](../plans/2026-07-13-context-os-v1/results.md) · [testcase](../plans/2026-07-13-context-os-v1/testcase.md) |
| **Context OS 长工具 scope lease 修复** | ✅ **2026-07-23 完成并默认生效** — `EffectBatchExecutor` 在 prepared effect 执行期间 pin capability scope，settle 后重启 orphan TTL，消除 `deepresearch` 300s timeout 与 scope 300s TTL 碰撞造成的伪 registry-unavailable；真实 scope 缺失改报 `tool_capability_scope_expired`。中文显式“调研”（含时间线图产物）稳定路由 durable `deep_research@v7`，否定式“无需调研”仍留在 ReAct。聚焦回归 `51 passed`。 | [架构](./ARCHITECTURE.md#153-tool-capability-plane) |
| **Context OS V1 — completion iteration 2（gap-free coverage 主链）** | ✅ 2026-07-13 slice 完成 — `ContextRequestPlanner` 不再以 assembler prebuilt/L2 top-k 是否“能放下”决定历史来源，而是每轮无条件从 SessionDB 装载全部 eligible raw；当前 user row 只计 coverage 不重复注入。超窗时首个 provider 前强制执行 bounded coverage jobs，随后用同一 prepared tool set 重新 plan→重建 messages/coverage→re-budget，gap/overlap/stale/blocked 或残余 jobs 一律 fail closed。compact 后恢复 protected stable/task/path rules 与最近完整 raw causal groups。Context OS + build-agent/voice/config 相邻回归 `287 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Context OS V1 — completion iteration 1（activation/snapshot/compression）** | ✅ 2026-07-13 slice 完成 — capability activation 在 scope lock 内按 candidate revalidate→预算→snapshot CAS/commit ack→scope commit→local swap 原子排序，CAS 失败不改变 authoritative scope，取消在 DB 已提交时仅推进 diagnostic handle、不激活 candidate；初始请求对 active goal/workflow/receipt 与 explicit-new 长任务 create-or-CAS canonical projection+tool summary，protected task fragment 与 snapshot handle 同步进入 planner/scope；压缩模型改读 `[context.compaction].model` typed config，并在每个 compact cycle 前热读最新设置，显式模型失败不跨模型 fallback。Context OS 相邻聚焦回归 `243 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Context OS V1 — 按路径项目规则（Task 4.2）** | ✅ 2026-07-13 slice 完成 — `ProjectRulesComponent` 仅在 code/workspace scope 且 host 已验证 workspace root/active path 时加载；按 root→active 层级发现 `AGENTS.md`/rules 并近路径优先，严格拒绝 traversal/symlink 越界，按字符/token/文件数预算截断，记录相对来源、完整文件 SHA-256 与 reason；普通聊天零磁盘扫描，文件变更下一轮重读。自动化 `67 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Context OS V1 — transcript/control 因果元数据（Task 0.3B）** | ✅ 2026-07-13 slice 完成 — AgentLoop 在 Context OS 路径用 host-only sidecar 标记 assistant/tool transcript，同轮 tool call 与全部 results 共享 `causal_group_id`；goal/todo/subagent/self-check/evidence/completion 等 control 保留 `anchor_after`，旧路径仍执行原始 append 且 wire bytes 不变。Compressor 不再把 late control 按 system role 前移，并在 deepcopy/compact/rebuild 中保留 sidecar 与 causal group。聚焦回归 `64 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Agent harness governance** (runtime manifest + wiring/policy/runtime/call-site guards) | ✅ 当前由 `backend/deskpet/harness/bootstrap.py::HarnessManifest` 从真实 Kernel operations、Driver 和 `ProfileRegistry` 生成可执行契约；已删除 2026-07-09 阶段的手写 `backend/deskpet/agent/harness_manifest.py`，测试直接检查生产 composition，避免第二份静态事实源。早期 Round 1～5 的 ServiceContext、policy、constructor 与真实 WS handler 护栏继续作为历史回归。 | [早期 plan](../plans/2026-07-08-agent-harness-hardening/plan.md) · [当前架构档](../ARCHITECTURE/AGENT_HARNESS.md) · [testcase](../testcase/2026-07-08-agent-harness-hardening/manual-test.md) |
| **Agent Harness + 单主 Session 通用行动** | 🟡 **R7 基线完成；崩溃恢复门禁绿色，完整治理门仍待收口** — 当前只有一个主 Session；顶层固定 `agent.general`，多 root 可并行隔离，running-root 续聊进入 durable FIFO。模型通过 `workflow_spawn` 选 child Profile，durable ticket 唯一绑定 Driver；失败通过 TaskGoal/PlanVersion/Attempt/FailureSet 回到同一父模型。可执行能力包、CapabilityBuilder、Manual/Auto、UAC external wait 和 Godot `1.0.2` 已接入唯一 Effect/UoW/Presenter。2026-08-13 可靠性单命令门为后端 `57 passed`（含五类独立进程 `SIGKILL`）+ 前端重连 `12 passed`，覆盖授权/effect/child/终态/重连故障；完整 Harness 仍有历史 authority/parity fixture 与结构预算失败，因此暂不标全绿。 | [可靠性门禁](../scripts/acceptance/harness_reliability_gate.py) · [当前架构](./AGENT_HARNESS.md) · [R7 历史结果](../plans/2026-07-20-agent-harness-simplification/evidence/r7-main-thread/results.md) |
| **DeskPet 原生 Workflow Engine（AC-24~31）** | ✅ 2026-07-11 完成 — 生产、锁文件和冻结包移除 LangGraph/LangChain Core/LangSmith；原生内核支持静态 frontier、条件边/join/reducer、有界循环、fenced canonical-JSON checkpoint、节点重试、durable HITL、Trace/replay/fork/eval。三条长任务默认原生执行；checkpoint、终态、业务事件与 delivery 原子提交；dispatcher 恢复到期重试、pending delivery 和已决策未续跑任务；PPT 大纲 decision 可在 Session 中实时显示并在重启后回放。workflow `295 passed`，产品入口 `85 passed`，前端 tsc + `48 passed`，冻结构建/启动 PASS。Windows Computer Use run `33a8298c...` 完成交付；后续真测还验证 open decision 重启后恢复为可点击大纲卡。 | [plan](../plans/2026-07-11-native-workflow-engine/plan.md) · [testcase](../testcase/2026-07-11-native-workflow-engine/manual-test.md) · [results](../plans/manual-results-2026-07-11-native-workflow-engine/RESULTS.md) |
| **Durable Workflow + Trace 历史计划** | ✅ **已完成并由原生引擎接管** — 2026-07-10 的 LangGraph Wave 计划建立了 workflow.db、lease/CAS、checkpoint、HITL、Trace/replay/fork/eval 与三条产品图；2026-07-11 已完成 Native replacement，生产不再依赖 LangGraph，也不再保留‘Wave B-D 实现中’状态。当前执行 owner 为 RunKernel → WorkflowDriver → NativeWorkflowExecutable，Effect/Receipt/Artifact/Delivery 继续复用统一 UoW 与产品服务。 | [历史 plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) · [Native plan](../plans/2026-07-11-native-workflow-engine/plan.md) · [当前架构](./AGENT_HARNESS.md) |
| **PPT Pro durable workflow** | ✅ 当前经 RunKernel → WorkflowDriver → Native `ppt_pro` workflow 执行；research、outline decision、逐页 effect、render/preview/review、Artifact/final 都可恢复。大纲 UI 响应通过 Kernel durable decision fence 返回同一 run，不保留 legacy orchestrator 新请求旁路。R7 S-4 已真人验证确认前无 Artifact、确认后同 run 继续并唯一交付 PPT。 | [历史 Task 14 plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) · [R7 结果](../plans/2026-07-20-agent-harness-simplification/evidence/r7-main-thread/results.md) |
| **长任务 Session 进度 + PPT 整页图片（AC-19/20/21）** | ✅ 2026-07-11 完成 — DeepResearch 1/7-7/7、PPT Pro 1/12-12/12、Complex Code 1/9-9/9 投影到普通 Session；Code 真点击批准后完成。修复“创建”漏路由、路径内 `ppt` 抢占代码意图、Code Graph 旧 provider 401，并保留完整 provider fallback chain 与实际 provider ID Trace；代码 Session 可见性已修。PPT image-mode 真机交付 21 页 deck，每页 1 个 1792x1008 全幅 picture、0 文本 shape，无水印/黑块；短 deck 工具 schema 与 Durable Graph 均已改为 1-20 页并跨层自动化覆盖（完整短 deck 生图复测待补）；底图不再无条件裁掉底部 120px。后端宽回归 `478 passed`、聚焦 `77 passed`、前端 `767 passed`、tsc PASS。 | [plan](../plans/2026-07-11-ppt-session-progress-full-page/plan.md) · [testcase](../testcase/2026-07-11-ppt-session-progress-full-page/manual-test.md) · [results](../plans/manual-results-2026-07-11-ppt-session-progress-full-page/RESULTS.md) |
| **PPT 多构图 + Session 单卡动态进度（AC-22/23）** | ✅ 2026-07-11 完成 — `full_page_images` 新增六类确定性 deck-level planner/Pillow compositor、中文字体与文字 fit fail-closed、layout/spec/compositor/font-policy 版本化 hash。最终真实 run `371e34dd90c044c0963c748f7a3ce6c9` 交付 6 页整页图 deck，每页 1 picture，视觉审查 6/6 `ok`、无质量警告。Session live/history 统一按 run/seq reducer，单卡原地经历 running/waiting/revision/completed，重启仍恢复一张 100% 卡；大纲“修改”文案、确认、滚动锚定均经真实点击验证。strict full-page 禁止模板回退，并增加 provider 跨 checkpoint 重试与视觉警告终态。验证：后端 workflow/PPT `450 passed`、focused `73 passed`；前端 tsc PASS、focused `34 passed`；最终 PPT、montage 与 UI 截图已归档。 | [plan](../plans/2026-07-11-ppt-layout-progress-ui/plan.md) · [testcase](../testcase/2026-07-11-ppt-layout-progress-ui/manual-test.md) · [results](../plans/manual-results-2026-07-11-ppt-layout-progress-ui/RESULTS.md) |
| **`code_complex:v1` 兼容 Workflow** | ✅ Workflow 本身仍可恢复并保留 proposal/effect checkpoint、durable decision、ToolRegistry V2 与 UoW 语义；但没有 Code 工作台/模式入口。新请求先进入 `agent.general`，只有模型用 `workflow_spawn` 选择合法代码 Workflow Profile 后，ticket-bound child 才能运行它；`main._run_chat` accepted-async 与 Code persona 均不是生产路由 owner。 | [历史 Task 15 plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) · [当前 Harness](./AGENT_HARNESS.md) |
| **七步问题处理流水线 / IntentTriage** | ⚪ **历史实现保留，2026-07-27 已退出 Text/Voice 生产入口** — `intent_triage.py`、`route_intent()`、`plan_decision()` 与旧测试仍用于兼容和历史审计，但 `ProductVenueRunAdapter` 不再调用它们。取证、验证与收敛的 in-loop collaborator 可继续服务主 Agent；前置 short-circuit、clarification、system injection 和 plan admission 不再拥有执行 authority。 | [当前 Harness](./AGENT_HARNESS.md) · [历史 ship 用例](../testcase/2026-06-25-problem-pipeline-ship/manual-test.md) |
| **搜索 + Deep Research** (DeepResearch V8 + Phase-1/2) | ✅ 生产可用 — 统一 `search_provider`(多引擎降级队列；**当前默认队列 `("google-cdp",)`** 见 `search_provider.py:61`，必应/DDG/百度仅 opt-in，区域感知；下文 §6.0 改造后口径)+ 聊天 `web_search` 快查 + `deepresearch`(原 `research_run`) V8 管线(**multi-query/HyDE 扩展** → 多引擎搜 + **site: 定向官方域** → trafilatura+**JS 渲染兜底(cdp-edge 连系统 Edge 无头,治 JS/SPA 空壳站,opt-in)**+Jina 抽取 → **中文一手源直连(巨潮财报 PDF / 国标 openstd)**+**美股 EDGAR**(财报兜底) → 分层权威打分含中文源/源质量过滤含字典站剔除/LLM 精排/反思迭代/BGE-M3语义/报告落 DeepResearch/(安装目录下,2026-06-21 迁移)+index.md 索引);**不接付费搜索引擎**;Phase-2 + JS渲染 真机 UI 测全 PASS;**⚠️ 2026-06-20 专项调查**：所谓"新 ReAct 子代理"代码不在仓库(已丢失),`deepresearch`(原名 `research_run`，已于 `5b7d4e3`/2026-06-15 更名，代码中现为 `deepresearch`) 才是唯一在岗实现;挖到 recency 维度真 bug(default_extract 缺 date 字段→新鲜度恒为默认值);全部结论为**静态读码、未运行实测**;**⚠️ 2026-06-21 §6.0 搜索可靠性改造**：默认引擎队列改 `("google-cdp",)`（可达门控，VPN 通才用），去掉 Bing/DDG 默认主力（仅 opt-in）；新增国内稳定**百度/搜狗百科直连源**（默认开，通用主题主力）+ wikipedia/google 可达探测门控；真机揪出并修「搜索 0 结果时 early-return 跳过直连源」严重 bug + 「`config.config` 单例不存在致 `[research]` 配置开关全失效」bug；TC-A1~A6+X1 真机 windows-mcp E2E 全 PASS | **专项架构 [DeepResearch.md](./DeepResearch.md)** · 优化方案 [plans/deepsearch/](../plans/deepsearch/00-optimization-plan.md) · `deep-research` SKILL v0.2 + [Phase-2 测试](../testcase/2026-06-15-deep-search-phase2/deep-search-phase2-manual-test.md) |
| **Scrapling-first 网页抓取 + 金价抓取** | ✅ 生产可用 — 集成 `D4Vinci/Scrapling` fetchers。`web_fetch` 真实运行先走 Scrapling，再退回 httpx；deepresearch/default_extract 真实抓正文时先用 Scrapling 抓 HTML，再交给 trafilatura/JS render/Jina 既有链路；新增 `gold_price_lookup` 专用工具抓取 XAU/USD 与 USD/CNY 并估算人民币/克金价；新增 `scrapling_fetch` 通用抓取工具，作为 `web` 工具集能力暴露给聊天/research/web 任务。旧 `web_search` 空结果/robots/403/TLS 卡死时，普通 fetch/调研/金价路径都有 Scrapling 兜底。`web_fetch`/`web_extract_article`/`default_extract` 结果会显式透出 `fetcher`，且 `web_fetch` 把 `fetcher` 放在大段 `content` 前，避免 UI 预览看起来仍像旧 httpx；deepresearch 文件卡片文案改为“报告已保存，正在整理答复”。`AgentLoop` 现在会在 deepresearch 成功产出 `report_md` 且带引用或文件后，追加收束系统提示，并让下一轮 LLM 调用 `tools=None` + `tool_choice=none`，阻止报告生成后继续默认 `web_search/web_fetch`。验证：Scrapling live smoke passed；`pytest tests/test_deskpet_tools_web.py tests/test_scrapling_tools.py tests/test_research_scrapling_priority.py tests/test_js_render_flag_off.py tests/test_research_sources.py tests/test_task_kinds.py tests/test_problem_pipeline_config.py -q` 66 passed, 1 deselected；`pytest tests/test_deskpet_agent_loop.py tests/test_agent_loop_sentinel.py tests/test_wi1_tool_choice.py -q` 21 passed；`pytest tests/test_agent_loop_pipeline.py tests/test_task_drift_fixb.py tests/test_research_scrapling_priority.py tests/test_deskpet_tools_web.py -q` 30 passed, 1 deselected；`py_compile` passed；前端 `tsc -b` passed。 | `backend/agent/agent_loop.py` · `backend/deskpet/tools/web_tools.py` · `backend/deskpet/tools/research_tools.py` · `backend/deskpet/tools/scrapling_tools.py` · `tauri-app/src/components/MessageStreamPanel.tsx` |
| **DeepResearch source packs** | ✅ 生产可用 — deepresearch 搜索计划增加默认开启的 `source_packs`，对俄乌/Ukraine 高时效主题自动追加 ISW、UN、Reuters/AP/BBC/Al Jazeera 定向搜索；对金价/黄金主题追加 LBMA、World Gold Council、Investing 定向搜索。source-pack 查询去重、每子问题有上限、可用 `[research].source_packs=false` kill-switch 关闭；`ResearchReport.coverage.route` 暴露 `source_packs_enabled`、`source_packs_hit`、`source_pack_queries`，便于 UI/log/测试判定。deep-research skill 文案同步 source packs + Scrapling-first 抓取链路，强调外层不要手动 `web_search`/`web_fetch` 拼报告。2026-07-08 真机补测后将 source-pack 查询从“中文子问题 + site:”收口为 standalone authority-directed queries；二次复测修正自然语言调研入口：`web_search` task policy 显式暴露 `deepresearch` 且排序在 `web_search` 前，真实 UI 发送“请调研一下俄乌最近的局势...”后先出现 `deepresearch` 工具调用，后续补充 `web_search` query 全为英文独立查询（4 条，`has_chinese_site=False` / `has_any_chinese=False` / `has_site=False`），抓取结果包含 `fetcher=scrapling`。验证：`py_compile backend/deskpet/tools/research_tools.py` passed；`pytest backend/tests/test_deskpet_research_tools.py -q` 87 passed；`pytest backend/tests/test_deskpet_context_assembler.py -q` 42 passed；`pytest backend/tests/test_research_scrapling_priority.py backend/tests/test_scrapling_tools.py backend/tests/test_deskpet_agent_loop.py backend/tests/test_agent_loop_sentinel.py -q` 16 passed；`pytest backend/tests/test_task_drift_fixb.py backend/tests/test_research_sources.py backend/tests/test_search_provider.py -q` 69 passed；`pytest backend/tests/test_deskpet_research_tools.py backend/tests/test_research_scrapling_priority.py backend/tests/test_scrapling_tools.py -q` 91 passed；真机 UI E2E PASS：`plans/manual-results-2026-07-08-deepresearch-source-packs/RESULTS.md`。 | `backend/deskpet/tools/research_tools.py` · `backend/deskpet/agent/assembler/policies/default.yaml` · `backend/deskpet/agent/assembler/policy.py` · `backend/deskpet/skills/builtin/deep-research/SKILL.md` · `plans/2026-07-08-deepresearch-source-packs/plan.md` · `testcase/2026-07-08-deepresearch-source-packs/manual-test.md` |
| **任务漂移修复 (v1 软修 + v2 四阶段根治)** | ✅ 生产可用 — 桌宠对无关新请求不再漂回旧主题。**v1**(2026-06-20)组装期软修(Tier1 当前请求锚定 + L2 重定性标签 + Tier2 话题跳变截断含词法兜底)真机 PASS。**v2 四阶段全实现 + 核心真机 PASS**：**A** T0-1 deepresearch 原话夺权(层2纵深，`research_tools.py` request_topic + prompt 双锚)真机 TC-A1/A2 PASS；**B** T1-1 硬会话切分(层1根治，`task_scope.py` 新建会话 effective_sid=UUID，旧 `task-*` 历史兼容 + `session_switched` WS 事件+voice 全链路+前端「新话题」按钮交互收口)真机 TC-B1 PASS；**C** T1-2 L2 降级 external memory(page-in)+`/continue` 透传(512 passed)；**D** T0-3 关 Tier2(8 处 `topic_shift_gate`→false，Tier1 保留)真机 gate `topic_shift_gate=False+shift_path=off+relabel/anchor=True`(473 passed)。flag/sentinel gating，OFF=BC | [plans/2026-06-21-task-drift-fix-v2/](../plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md) · 真机 [testcase/2026-06-21-task-drift-v2-phase{A,B,CD}](../testcase/) |
| **登录方式：无账户登录，仅手动 provider** | ✅ 2026-08-11 —— 托管账号登录整套移除后，残留的前端 `AuthAdapter`/Login/Register scaffold、账户入口与旧登录诊断脚本也已删除。用户只在设置/首启向导填写 baseUrl + apiKey；身份走签名的本地 profile（`LocalAuthSnapshotProvider`），不再存在账户生命周期事件。历史 relay 方案仅作参考。 | [manual-provider-only](../plans/2026-08-09-manual-provider-only/00-PLAN.md) |
| **Slash 命令 + /goal + 多 agent** (v2) | ✅ 实现 + 真测；**已 merge master**（`84b8ce0`）；**2026-06-27 测试阶段出厂点亮**——`slash_commands`/`goal_mode`/`agent_parallel`/`plan_confirm_gate`/`preference_memory`/`subagent_driver`/`agent_team`/`subagent_nonblocking` 在 `config.py` 默认翻 **True**（不灰度），config.toml 同步 | [companion-code-skill-upgrade](../plans/2026-05-25-companion-code-skill-upgrade/) · [全量点亮 handoff](../plans/2026-06-26-agent-harness-alignment/HANDOFF-enable-flags.md) |
| **goal-completion 升级 (FP-1~FP-5 线)** | 🟡 FP-1 ✅（真机手测门 PASS）；FP-2 抗漂移 ✅（55焦点+280回归绿；R-T4 defer）；FP-3 自我纠错 ✅实现(WI-2.1~2.4+T6+R-T3+R-T6;286焦点/2459全suite绿;§7死循环上界+no_persona_leak+伪完成拦→二次通过+降级矩阵;off→shadow待go/no-go签核;手测门真产物撞写权限门同FP-2口径)；FP-4 记忆+人格 ✅实现(WI-3.1~3.4+B-10双写钩+修daily_decay从未调用bug;MemEval 491+retriever无回归;scope/pinned列)；FP-5 Skills 分级+自创 ✅后端(WI-4.0接通compaction★全回归/4.1 embedding自动披露/4.2重挂/4.3技能自创codifier不执行代码;flag off字节BC)；**5个FP后端实现全完成+R-T5字节基线守+480 goal-completion焦点测试绿**；FP-2/3/4/5真机手测门+FP-5前端确认卡批量补跑(spawn_task) | [10-EXECUTION-ROADMAP](../plans/2026-06-04-goal-completion-upgrade/10-EXECUTION-ROADMAP.md) · [FP-1/](../plans/2026-06-04-goal-completion-upgrade/FP-1/) · [FP-2/](../plans/2026-06-04-goal-completion-upgrade/FP-2/) |
| **pet animation UX** | ✅ v1 ship；pet-tier1 交互（星星粒子/努力工作气泡/拖拽回正）真机+preview 双 PASS | [pet-animation-ux](../plans/2026-05-24-pet-animation-ux/) |
| **桌宠渲染 / 形象** | ⛔ **已移除** —— Live2D SDK/Hiyori 资产、桌宠渲染组件、设置入口、pnpm 锁依赖、前后端 emotion/action 消息及语音标签解析均已删除；主窗从 HTML/CSS 到 React 挂载前均使用不透明工作台背景。历史文档中的 Live2D 里程碑只描述 DeskPet 上游，不是当前生产代码。 | — |
| **OSS 开源准备** | 🟡 进行中（BUSL-1.1 + SPDX + sanitize） | [oss-prep-handoff](../plans/2026-05-27-oss-prep-handoff.md) |

---

| **DeepResearch fan-out 默认开启** | ✅ 2026-07-11 完成 — 出厂配置、当前开发配置和代码缺省统一为 ON；至少 2 个子问题时按 research lane 有界并发（默认并发 2、最多 6 个），主线程统一综合/重排引用，保留 300s 总预算与递归守门。TOML 解析通过，fan-out/workflow/search 联合回归 `57 passed`。 | [fan-out plan](../plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md) |
| **DeepResearch 证据质量 + Session 完整报告** | ✅ 2026-07-12 完成 — 移除百度/搜狗百科默认直连，Agent Harness 增加官方生态定向 source pack，官方文档升为一手来源层级；rerank 503 时由确定性主题锚点门剔除明显串题材料。最终 Markdown 正文直接投影到 Session，附件卡不再显示 `$blob_ref`，异步 handoff 后输入区立即恢复空闲。抓取链路统一为 Scrapling-first（`web_fetch` 仅为兼容接口名，失败才回退 httpx），失败提示改为 `scrapling_fetch`。真机 run `6f5af6f0...` 显示 5069 字报告和 6 条相关引用，重启后仍可见；run `39e57ed0...` 验证后台运行时前台已恢复“发送”。 | [plan](../plans/2026-07-12-deepresearch-quality-session-report/plan.md) · [results](../plans/manual-results-2026-07-12-deepresearch-quality-session-report/RESULTS.md) |
| **DeepResearch 直接集成 Agent-Reach** | ✅ 2026-07-12 完成 — 不引入 LangGraph，也不自建第二套渠道注册表；固定 Agent-Reach 上游 commit，由薄 port 直接复用其 channel/config/doctor/read，DeskPet 原生 workflow 继续负责显式节点、共享状态、checkpoint、fan-out、Trace 和 Session 交付。强 DeepResearch 请求在 ReAct 前确定性进入工作流，显式公开 URL 稳定路由并进入既有证据评分；失败可观测且不阻断通用来源。自动化最终宽回归 `310 passed, 0 failed`；最终 PyInstaller clean build PASS，PYZ 含 34 个 Agent-Reach 条目，冻结包干净目录启动 PASS。Computer Use 真机 Session `f45f61f1...` 单卡从 3/7 更新至 7/7，完整引用报告同会话显示，关闭重开不重复；Trace `3edf400b...` 记录 GitHub 渠道、Jina Reader hit、无 degradation。 | [plan](../plans/2026-07-12-deepresearch-agent-reach/plan.md) · [testcase](../testcase/2026-07-12-deepresearch-agent-reach/manual-test.md) · [results](../plans/manual-results-2026-07-12-deepresearch-agent-reach/RESULTS.md) |

## 4. 最近里程碑（倒序）

| 日期 | 里程碑 |
|---|---|
| 2026-08-04 | **桌面游戏操作与小窗口 Context 稳定性修复 ✅** — 桌面输入按全局资源 lane 跨批次/Run 串行；`window_key` 支持有界单事务序列和纯暂停步；截图 base64 提升为有预算的多模态附件；AgentLoop 按冻结模型窗口压缩并在 provider 预检超限时做一次目标保真 rescue。最终自动化与 Windows 真机复测通过，Run `9dd6243c...` 仅一次三步按键调用并截图完成。 |
| 2026-07-30 | **Harness Inspector 用户时间线 ✅** — 默认页改为“Agent 执行过程”，只显示当前状态、用户需要做什么和按顺序更新的轻量步骤；工具步骤按需展开真实参数，技术账本默认折叠。相关前端 39 passed、production build 与唯一主实例真机交互通过。 |
| 2026-07-30 | **多步骤任务启动状态与公开进度修复 ✅** — 原生 Workflow child 首次 claim/recovery handoff 原子同步通用 Run 为 running 并设置 started_at；child attach 与进度交付改为事件驱动唤醒；`durable_task@v1` 新增九阶段公开进度、“多步骤任务”标签与安全循环事件 identity。启动兼容精确历史 capability fingerprint，cancel intent 不再阻断 Harness 初始化。相关后端 150 passed、前端聚焦 83 passed；唯一主实例 Session `dfcbaac7...` 真机显示“规划下一步 6/9”，双 Run 对账一致。 |
| 2026-07-30 | **桌宠透明主界面与消息栏宽度修复 ✅** — 主窗口恢复透明、人物画布与点击区居中；消息窗口从 440px 加宽为 700px、最小 640px，Harness 与聊天区同时可读。前端 882 tests、TypeScript/production build及当前源码 Windows 实机检查通过。 |
| 2026-07-29 | **全局 UI 暗色统一 ✅** — 新增统一暗色语义主题，主窗口、设置、Provider、新手引导、账户、能力中心、Skill Store、反馈及共享弹窗完成迁移；设置页退休 Supervisor/自动恢复入口。前端 99 files / 882 tests、TypeScript、Vite production build通过；当前源码 Windows 主要页面实机检查通过。 |
| 2026-07-29 | **Harness 完整测试恢复 0 failed ✅** — 修复 parity 对 `run_reserved`、reasoning activity/summary 的漏分类，并把 reasoning summary 的持久化和双 WS 发送纳入 current census，141 条 legacy 映射保持冻结；R4.5 历史 Companion 门禁不变，新增当前 Harness boundary 紧预算 raw/adjusted/core/Kernel=`151338/150829/42375/1285`、unknown=0、public ops=6；冻结 Skill 重挂准备移出旧 `AgentLoop`，AST span 从 3909 降至 3761（门禁 ≤3800），兼容与 fail-closed 语义保持。三类修复分别经子 Agent 对抗复测 PASS，完整 Harness 在 20 分钟外层等待下完成 `711 passed, 4 xfailed, 0 failed`（6m15s）。 |
| 2026-07-28 | **Harness 兼容边界隔离完成 ✅** — 当前 Presenter/投影门使用新名称，旧名只经 `deskpet.compat` alias 与 lazy shim 解析；compat 无业务逻辑，生产 Harness 对 compat、旧 task/grant/redaction 路径和历史 UoW 聚合导入为 0，turn trace 不再宣告已退出的 intent/plan 层。严格测试发现小 UoW view 只对比历史 aggregate 的假安全，推动改为直接对比真实 SQLite 参数名/种类/默认值并补齐固定关键字；最终 58 个唯一方法 call-shape mismatch=0，子 Agent `VERDICT: PASS`。最终前端 `98 files / 879 tests`、build PASS；源码 Tauri 真人点击确认 Voice disabled、`/ws/audio=0`，文字 Run `76102aa7...` 返回指定短句并回到空闲。 |
| 2026-07-28 | **跨 Run typed TaskReference 完成 ✅** — 旧“关键词触发 + 词汇挑行 + 无命中取最近4条”改为稳定 Run/task 候选与 `resolved/ambiguous/missing/not_requested` 四态。只有唯一解析才加载精确 Run 的末12行/16000字符；共享 scope 不串 Run、未知名称不回退、无身份旧行不候选、歧义只给摘要并要求澄清。严格测试推动修复共享 scope、截断预算和单候选误选3个缺口；最终组合 `146 passed`，子 Agent `VERDICT: PASS`。 |
| 2026-07-28 | **workflow.db → state.db 产品视图一致性门完成 ✅** — 下一轮 Context、历史刷新和会话列表预览统一在读取前通过 `SessionTerminalProjectionConsistencyGate`；当前 epoch 的全部根终态按 `workflow_event_id` 幂等补齐，不再受旧 8 条上限影响。epoch 删除/重建会重试，无法确认最新视图时后端 fail closed、前端保留最后已知数据。严格测试先发现并推动修复 ServiceContext 注册阻断；最终 Harness/接口/依赖 `68 passed`、额外积压/epoch `2 passed`、前端 WS `28 passed`、TypeScript build 与 manifest 通过，子 Agent `VERDICT: PASS`。 |
| 2026-07-28 | **Execution 核心依赖方向收口 ✅** — Task Context/TaskGrant durable contracts 移入 `deskpet.types`，trace/sensitive redaction 移入 `deskpet.security`；Execution 对 Agent/Permissions/Workflows/Capabilities/Companion/Memory 的静态与运行时依赖清零。旧路径保持完整 identity/`__all__`/pickle 兼容，生产只走 leaf；绝对/相对 import AST 门禁和 fail-closed 脱敏测试齐备。独立复测 `116 + 43 + 74 + 18 passed`，子 Agent 严格 PASS。 |
| 2026-07-28 | **ReAct 核心职责拆分 ✅** — 6411 行混合文件拆成 Driver 编排、AgentLoop/provider 适配、durable boundary/codec 三块，依赖单向且历史导出对象身份不变；生产 composition 和子代理工具直接引用新边界。ReAct/拆分/边界恢复相关 `69 + 69 + 55 passed`；构建 manifest 更新后发布/装配 `19 passed`，独立子 Agent 严格复测 PASS。剩余约 5k 行 Driver 作为后续 provider-admission/capability-lifecycle 提炼入口。 |
| 2026-07-28 | **Harness 产品入口与 UoW 依赖面拆分 ✅** — `ProductVenueRunAdapter.open()` 拆成 identity/workspace、Context/direct-run 和 venue 协调三段；Kernel、Runtime、ReAct、Workflow、工具、child、continuation、Supervisor、admission 改依赖完整精确签名的小 UoW view。底层仍是唯一 `SqliteExecutionUnitOfWork`/SQLite/写通道；AST 调用面与签名护栏防止漂移。独立验证 `49 + 83 + 106 passed`，子 Agent 严格复测 PASS。 |
| 2026-07-28 | **旧 Voice 安全关闭 ✅** — 默认 `voice.enabled=false`；后端冷启动不导入/实例化/加载 VAD、ASR、TTS，前端主窗口和消息窗口都不建立 AudioChannel、不申请麦克风；`/ws/audio` 与 `/health.voice` 提供稳定关闭状态。后端聚焦 `21 passed, 2 skipped`，前端聚焦 `4 passed`、全量 `877 passed`，tsc/build 通过，独立子 Agent 严格复审 PASS。Realtime 接入留待 relay 契约就绪后实施。 |
| 2026-07-28 | **Prepared Tool JSON 边界收口 ✅** — Frozen 参数统一经 `arguments_json()` 递归恢复为 canonical dict/list；投影失败在物理执行前形成 typed tool failure。125 passed；同一 default Session 新 Run `5735337e...` 真实启动 GemCollector Godot 编辑器并 completed，ledger `argv` 为 list、同源错误 0，旧失败 Run 保持不变。 |
| 2026-07-28 | **同 Session 指代与既有工作区复用修复 ✅** — 新 root 仅在显式指代时有界读取其他 root 的对话，Host 验证历史 task workspace 后注入精确路径；尾随 `&` GUI 启动脱离标准流。222 passed；真实 Run `6959c1e0...` 直接打开既有 GemCollector、无 workflow_spawn，并继续截图/按键。 |
| 2026-07-28 | **GLM-5.x 长流式响应 deadline 修复 ✅** — 固定 180 秒整次响应上限改为有效模型事件间的滑动无进展上限；119 passed、2 skipped。真实 `sf-glm-5.2` invocation 持续约 184 秒后正常 completed，未进入 provider unknown。 |
| 2026-07-27 | **单一 Context/Run 入口与授权资源闭环完成 ✅** — Text/Voice 跳过 IntentTriage/预先 plan，Session 根 Run 失败在下一轮组装前按 epoch 幂等回填；`workflow_spawn` 与全局授权工具均具确定性 selector，唯一裸 capability 名可安全规范化。聚焦 `177 passed`；真实 Session `e9d345e4...` / Run `6048fc27...` 完成原始桌面散文命令，六层 completed，文件真实落盘。 |
| 2026-07-25 | **Companion Task 14 确定性质量门完成 ✅** — 32 项可执行故障矩阵覆盖 ACK 前后、cutover、forget/profile、lease/child/terminal 与 survivor；聚焦 54、Companion 540、组合根 39 全绿，smoke=`DECISION: SHIP`。真实 ProductVenue/Kernel 的四类 p95 回退 `+5.442%～+6.202%`，同一 FULL writer、DML authority=1、transaction starters=53。LOC raw/adjusted/core/Kernel=`129997/129488/44063/1277`，public ops=6、unknown=0；SQLite 测试 worker 生命周期 warning 已修并用 warning-as-error 79 项复核。 |
| 2026-07-25 | **Companion Task 13 单一生产 authority cutover 完成 ✅** — durable journal 在 ingress drain 内切换到 `companion/generation=5`；V2 Reminder、Companion preference/runtime/event writer 成为唯一生产成长链，旧 Codifier/ToolPath/进程内 Reminder 退休。自动化：聚焦 166、Companion 476、Skill/Preference 127、Capability 251、Workflow 53、前端 851，tsc/manifest/diff check 通过；R4.5 预算转 Task 14。真实源码 Tauri Relay mode 在空 UUID owner inbox 真人点击问答成功，UI 回空闲且 `chat_v2_final` session 对账一致；精确清理 21-PID 树、释放 9749762048 bytes private memory，survivor=0、8100/5173 listener=0。 |
| 2026-07-25 | **Companion Task 12 durable 通知与主消息页详情完成 ✅** — profile inbox notification/outbox、route relocation、redaction/absent replay、live/history owner fence、memory 全链 exclude、`Vc + VpVector` 双读详情和三类独立决策已接生产组合。Backend `65 + 32 passed`、前端 `86 passed`、tsc 全绿；真实源码 Tauri 完成主消息页 live 卡片、F5 history 与 evidence modal，精确清理 32-PID 树且 survivor=0。生产成长 authority 仍为 legacy，Task 13 负责原子切换。 |
| 2026-07-22 | **Agent Harness R7 主消息线程与全量发布门完成 ✅** — 主消息页 S-1～S-7 真机全绿；稳定 request/turn identity、workflow decision fence、canonical history、busy 输入阻止、重启恢复和 typed failure 投影闭环。Harness 512、Workflow 703、完整 backend 5736，前端/Rust/last-mile 全绿；复杂度 `33,633/33,124/5,948/896`，performance comparison 11/11 PASS。 |
| 2026-07-21 | **Agent Harness R6 production activation 完成 ✅** — Product Venue/RunKernel 成为新请求唯一 owner，旧 product/AgentLoop/subagent/AutoResume execution bypass 删除；启动恢复、后端 request identity 接口、frozen provider/model/request capability、transport detach 语义闭环。Harness `495 passed, 4 xfailed`；parity `141/141`；LOC raw/adjusted/core/Kernel `32,913/32,404/5,949/900`。客户端 stable ID 生成/复用仍属 R7。 |
| 2026-07-21 | **Agent Harness R5.5 durable admission / cutover readiness 完成 ✅** — A-source commit `69c6980a` 固化 typed admission、严格重复决策语义、launch/recovery fence、canonical terminal 与 durable LiveRun；B 锁定 authority/parity/cutover/budget。final gate `raw/adjusted/core/Kernel=34,756/34,247/5,950/924`，starters23 / DML1 / fault39 / public ops6，parity `141/141`，完整 harness `471 passed, 8 xfailed`。所有 spike/test/benchmark 均按 worktree 精确清理到进程残留0；生产仍 `legacy/0`，下一步 R6 原子 activation。 |
| 2026-07-21 | **Agent Harness R5.5 A-source admission chain 完成 ✅** — dormant ProductVenue 的 plan approval 改为 Kernel/UoW durable admission；pending/accepted/claimed/launched 与 reject/cancel/expiry/launch-unknown 均可恢复，claim/cancel 共用启动锁和 SQLite CAS。canonical `run.final` 唤醒 observer，durable final 刷新唯一 LiveRun 且未完成 task 不再提前失联。幂等 recovery 复用 launch id，非幂等歧义唯一 fail-closed；Goal association 与 terminal delivery 同事务事实链。construction gate `raw/adjusted/core/Kernel=34,756/34,247/5,950/924`，生产仍 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R5.5 provider launch-token dormant seam 完成 ✅** — stable `launch_operation_id` 与精确 provider/adapter/version snapshot 从 ReAct collaborator 显式透传到真实 OpenAI-compatible HTTP header；无 token 保持旧 mocks/调用兼容，不支持幂等时禁止传输重试、stream→nonstream 重放与跨 provider fallback。focused `22 passed`，construction gate `raw/adjusted/core/Kernel=34,070/33,561/5,508/767` 全绿；`main.py` 未激活，生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R5.5 durable admission storage slice 完成 ✅** — 复用既有 continuation authority 持久化 typed admission；start/resolve/claim 与 ReAct/workflow 消费均为 fenced 原子边界，workflow replay 只在 `start_claimed=true` 时调度。authority 为 starters23 / DML1 / fault39，focused `104 passed`；construction gate `34,406/33,897/5,729/767`，生产仍 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R4.5 final Kernel/child convergence 完成 ✅** — 删除两层 child launcher 转发壳，统一 root/precreated/recovery launch 与 recovery/signal fenced consumption；补齐 heartbeat、stale fence、cancel/close/release 和 terminal-first race 证据。final gate `raw/adjusted/core/Kernel=33,925/33,416/5,498/767`，公开操作恰6，authority/owner/manifest 全绿；完整 harness `424 passed, 8 xfailed`，生产仍 `legacy/0`，下一步为 R6 原子 activation。 |
| 2026-07-21 | **Agent Harness R4.5 单 EffectBatchExecutor 完成 ✅** — 删除 `UnifiedToolExecutor` 转发边界，把 claim/reuse/reconcile、连续 safe 并发/unsafe barrier、late unknown 与 durable-after-signal ack 收进唯一 `EffectBatchExecutor`；ReAct Driver 保持 effect+continuation+event 原子 owner，失败零 ack、全 late 零 signal、mixed original indexes 与 shutdown final recovery 均有直接测试。core `5,580→5,542` 真净删38，adjusted total `33,466`、Kernel `811`；focused `63 passed`、完整 harness `412 passed, 8 xfailed`，最终 core≤5,500 继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 venue wrapper 真删除 slice ✅** — 删除 `ProductVenueOpenResult`，`open()` 直接返回 `ProductVenueRunSession | ProductVenueRunResult`；Voice 直接消费 session，Text execute 消费同一 union，保留承载 pre-Kernel/terminal 语义的 RunResult。`venues.py 406→386`，core 真净删 20（预算≥14），raw/adjusted/core 为 `33,953/33,444/5,532`；相关 `69 passed`、fault/schema `51 passed`，authority 与 transition 门绿，生产仍 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R4.5 单 HarnessSupervisor authority 完成 ✅** — 删除 ChildRunScheduler/RecoveryCoordinator 两套常驻 task 与 lifecycle，唯一 `HarnessSupervisor._task` 有界轮转 child command/signal、recovery、late-ready、delivery；startup/shutdown 顺序与 LiveRun final sweep 完整落地，late-ready id 在 limit 前过滤。Supervisor authority `2→1`，本切片 core `5,599→5,580` 净删19，adjusted total `33,504`、Kernel `811`；authority target 全绿、focused `97 passed`、完整 harness `404 passed, 8 xfailed`，最终 core≤5,500 继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 单 LiveRun authority 完成 ✅** — Kernel 通过通用 `bind_live_index` 把唯一 `BoundedLiveIndex._runs` 注入 Driver/legacy collaborator，删除三份重复 run map；LiveRun 分别持有 boundary 与 iterator，durable-first read、identity-safe clear、lock 外 `aclose()` 与 terminal/restart 无残留均有测试。run map `4→1`，adjusted total `33,531`、core `5,619`、Kernel `811`，transition/authority 门绿，扩大 focused `158 passed`；Supervisor `2→1` 与 core≤5,500 留给后续 slice。 |
| 2026-07-21 | **Agent Harness R4.5 authority-migration cohort LOC gate 修正 ✅** — `33,618` target 不变；source-hash fixture 将 `execution_uow.py + checkpoint_execution.py` 锁到 `796905b9` 的 blob/hash/group/reason 与 physical/raw-effective LOC。当前 raw total `33,973` 保持可见，cohort raw delta `+524` 减 physical delta `+15` 推导 overcount `509`，调整后 total `33,464`。公式、fixture drift、真实 move/copy/delete、snapshot 与 anti-compression 测试通过；total 门已绿，core/LiveRun/Supervisor 继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 typed transaction surface 完成 ✅** — execution UoW 的 activation、delivery、ReAct boundary、decision、tool claim、effect settle、run outcome、child signal、recovery scope 九类边界收敛为 typed composite API，生产 callers 全迁移后删除旧 starters，精确计数 `33→21`（硬门≤23）；DML authority 保持 `1`、fault matrix 保持 `39`、生产保持 `legacy/0`。当前 total `33,973`、core `5,552`、Kernel `812`，施工门全绿，完整 harness `389 passed, 8 xfailed`；最终 total/core/LiveRun/Supervisor authority 门继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 单 DML authority 与施工峰值门完成 ✅** — checkpoint execution SQL 全部进入 connection-bound `ExecutionTx`，adapter 不再直写 execution tables，DML authority `2→1`；新增 5 个故障窗后 fault matrix 达到 `39`。首批 core 真删除合并后 total `34,157`、core `5,582`、Kernel `812`，`--r45-transition-gate` 与 authority audit 全绿；最终 LOC/typed transaction/LiveRun/Supervisor 门继续执行，生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R5 Text/Voice 共用 test-only 产品链完成，R4.5 方案 A 获批 ✅** — Text/Voice 复用唯一 ProductVenueRunSession 与 Presenter 转换，真实 activation `open/1`、owner `kernel/1`、terminal close 无 active ref；harness `363 passed, 8 xfailed`，parity `141/141, unmapped=0`。两个真代码 spike 证明单纯 ExecutionTx seam 与 LiveRun map 合并不会自然降低 LOC，原 `core+UoW≤2,800/Kernel≤450` 作废；经 6 轮挑战定稿结构门，生产继续 `legacy/0`，当前进入 R4.5。 |
| 2026-07-21 | **Agent Harness R4 Workflow/Child/Delivery adapters 完成 ✅** — Router 与 WorkflowDriver 由同一 `ProfileRegistry` 生成；Workflow/Team/Subagent child 统一进 durable command/inbox 与唯一 scheduler；execution delivery 只由 UoW dispatcher claim/complete，Goal 根终态用冻结 id 投影。owner fences 覆盖 cancel/retry/fork，ReAct decision 边界原子可恢复；shutdown 的 readiness-pop/durable-settlement 两种先后顺序及短超时重启连续 `60/60` 通过。组合回归 `405 passed, 9 xfailed`，LOC `33,184 ≤ 33,228`、unknown 0，两份独立审计 PASS。生产仍为 `legacy/0`，R5 开始 Text/Voice test-only parity。 |
| 2026-07-21 | **Agent Harness R3 scoped EvidenceContext 完成 ✅** — completion evidence 精确绑定 run/turn/call/effect/target/artifact；唯一 resolver 为现有 `SqliteExecutionUnitOfWork`，直接 JOIN canonical run/effect，调用方不能传第二套 receipt 集合。run/turn/call/effect/artifact 任一不匹配均 unknown；当前 schema 无独立 target digest，传入时明确 unknown，禁止把 effect fingerprint 冒充目标摘要。legacy session gate 仅在无 scoped context 时保留到 R6。focused `57 passed`，完整 harness `249 passed, 9 xfailed`，LOC `32,986 ≤ 33,228`，unknown 0。 |
| 2026-07-21 | **Agent Harness R3 tagged Driver protocol 完成 ✅** — ports 收敛为且仅为 `DriverEvent`/`DriverSignal` 两个 tagged value class；旧 13 个 candidate/signal class 与 `DriverCandidate` union 删除，语义名只作为构造函数。Kernel/runtime/ReAct/Workflow/Child 全按 kind 消费，AST 防旧 taxonomy 复活；ports 147 行。focused `52 passed`，完整 harness `240 passed, 9 xfailed`，LOC `32,817 ≤ 33,228`，unknown 0；生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R3 ReAct capability 边界完成 ✅** — capability snapshot 随 durable React boundary 持久化并在恢复后继续生效；首次/恢复 AgentLoop 都用真实 `tool_names_filter` 限制模型 schema，provider 即使伪造越权 tool call 也会在 prepare 前二次 fail closed。显式空 snapshot=deny-all，旧无字段记录保持兼容。focused `17 passed`，完整 harness `239 passed, 9 xfailed`，LOC `32,965 ≤ 33,228`，unknown 0；生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R3 薄 Kernel + 有界 live runtime 完成 ✅** — `RunKernel` 收敛到 647 行，只保留六操作、route、生命周期、recovery lease 与 terminal authority；contracts、唯一 `BoundedLiveIndex`、无状态 `DriverRuntime` 均不持久化。每 run 历史上限 256、subscriber queue 上限 128，过期 cursor/慢消费者 fail closed；新进程 durable observe 从 UoW 重放，terminal/close 强引用归零。focused `18 passed`，完整 harness `237 passed, 9 xfailed`，LOC `32,935 ≤ 33,228`，unknown 0。 |
| 2026-07-21 | **Agent Harness R3 recovery lease/fence 地基完成 ✅** — workflow schema 升至 v8，为 durable run 增加独立于部署 `owner_generation` 的 recovery owner/epoch/expiry；claim/renew/release、未过期互斥、过期接管与 stale epoch 拒写全覆盖。recovery-fenced event append 在同一 `BEGIN IMMEDIATE` 中校验 lease 并写入，关闭 assert→write 的 TOCTOU 窗口；v7 历史字段迁移保持不变。focused `26 passed`，集成 harness `235 passed, 9 xfailed`，LOC `32,809 ≤ 33,228`。生产仍为 `legacy/0`，尚未启用 Kernel recovery。 |
| 2026-07-20 | **Agent Harness 简化 R2 Preparer/Presenter 完成 ✅** — `_run_chat` 保持生产入口与 execution owner，只把产品准备策略提炼为 typed 三阶段，把 legacy AgentEvent 投影提炼为 live/durable/domain registry；窄 `LegacyProductDomainSink` 隔离 WebSocket/peer/DB/plan waiter I/O，无 shadow 或双写。冻结 R0 census 内容不变；current mapping 141/141、57 个 WS sink 完整，删除 dual-send 任一 peer broadcast 会 fail-closed。集成验证：harness `232 passed, 9 xfailed`；LOC `32,490 ≤ 33,228`；相邻 R2 分支 `335 passed`；三生产模块 611 行。 |
| 2026-07-20 | **Agent Harness 简化 R0 机械基线完成 ✅** — 首次 WI-12 功能缩水方案已回退；新版计划经 5 轮挑战通过。R0 冻结 141 个旧产品 callsite、16 个直接 parity 行为与两份不可规避 LOC manifest；10,000 次真实 Kernel/UoW 生命周期 start/final/close 全量一致、强引用 0。组合回归 `176 passed, 9 xfailed`，生产路径未切换。 |
| 2026-07-18 | **DeepResearch v6 默认发布与答案契约稳定性完成 ✅** — 五类 intent、durable continuation/control/retention、三态终态与 exactly-once delivery 完成；最终 release identity 下真实 UI 覆盖 completed、partial、generate-now 双击和重启历史；后端 815、前端 822、Rust 73、tsc/build/check 全绿，基础模型为 `deepseek-v4-pro` 1M；release identity fixture 已纳入受控提交。 |
| 2026-07-17 | **DeepResearch v5 generate-now 单次真实控制主链 PASS** — Computer Use 真点击 run `6ea6a3a4...` 的“立即用现有证据生成”，command 约 0.227 秒 observed，30 秒 settle fence 后 settled/consumed；长 fetch 控制性取消被归类为业务降级结果，run completed、全部节点 succeeded、硬失败 0，唯一终态 `insufficient_evidence`。自动化控制/adapter `50 passed`，v5/control/terminal `226 passed`；重复点击/重连/强杀恢复仍 PENDING。 |
| 2026-07-15 | **DeepResearch v4 宽主题可靠性与专业报告完成 ✅** — 针对 Session `16bbb4ce...` 和旧质量门误判，新增 provider limiter/permit 复验/empty-aware rescue、稳定技术 taxonomy、实体去重、页面噪声过滤、质量优先 3～8 项报告契约、跨段采用建议一致性、成文质量门、诚实失败与幂等 retry。Xiaomi 同进程最新原题 runs `43e851a0...` 和 `59975eb1...` 均通过当前 17 项报告门；旧 `66720bcf...` 与早期不一致样本降级。最后代码后端全量 `4643 passed / 10 known failures`，无新增回归。 |
| 2026-07-15 | **项目事实源统一到 ARCHITECTURE ✅** — 原 `STATUS/status.md` 的 worktree、模块完成度、里程碑和已知问题迁入 `ARCHITECTURE/PROJECT_STATUS.md`；AgentLoop、DeepResearch、PPT 专项档同步归档到架构目录。`AGENTS.md`/`CLAUDE.md` HARD 纪律改为完成后更新模块架构 + PROJECT_STATUS，README、plans 与 acceptance 入口同步；`STATUS/` 仅保留历史链接兼容，禁止双写。 |
| 2026-07-15 | **Search Gateway cooldown 隔离 + DeepResearch v3 引用质量与逐步结果 UI 完成 ✅** — single-flight provider circuit 消除跨主题连坐；结构化 passage/atomic claim 与 fail-closed publish gate 将固定三类连续真测提升到 3/3 completed、mean support 1.0、P95 66.409s。Session 折叠态直接显示每一步动作、结果和诊断，展开态显示指标与下一步；Xiaomi 屏幕真机、Artifact delivery、后端/前端全量和 production build 全 PASS。 |
| 2026-07-15 | **Search Gateway + DeepResearch v2 + durable progress 完整真机 DoD ✅** — 默认 ON 的内置 Gateway、共享 Fetch、六分支 v2、13 阶段默认折叠气泡、重启恢复、双 run 隔离、历史/实时 Artifact 与 `final_assistant` 投递全部闭环。真实固定三类 benchmark 2/3 completed（失败样本保留）、P95 270.548s；Windows W01～W05 PASS。前端 800 tests + tsc/build；后端 4440 passed / 10 个既有范围外失败，较旧基线少 2 且无新增。 |
| 2026-07-14 | **Search Gateway / shared fetch backend WI-1～4 完成 ✅** — 无 Docker/API key/SearXNG 依赖的默认异步快搜主路已接通；可选外部 SearXNG、provider 降级、request-local 诊断、确定性聚合、缓存/冷却/取消/hydrate 与共享 FetchExtractService 均有自动化闭环，旧 DeepResearch v1 搜索列表契约保持兼容。 |
| 2026-07-14 | **Context OS V1 核心 E2E-01～11 真机通过，语音 E2E-12 等待 relay VR-0** — Windows Computer Use 已完成真实输入、ContextTrace、Code mode 与 Default→OFF→ON 回退；OFF 八类工具与 revision 51 golden 精确相等，截图/日志停机后哈希可复核。整体不标 complete：正式语音必须 relay-only，而当前中转站尚无 Realtime/ASR/TTS endpoint 或语音模型 alias。 |
| 2026-07-12 | **DeepResearch 直接集成 Agent-Reach 真机通过 ✅** — 固定上游 commit 并直接复用 channel/config/doctor/read；DeskPet 原生 workflow 继续拥有节点、checkpoint、fan-out、Trace 与 Session 交付，强调研请求在 ReAct 前确定性进入工作流。最终宽回归 `310 passed, 0 failed`，PyInstaller clean build 与冻结启动 smoke PASS。真机单卡 3/7→7/7、同 Session 完整引用报告和重开恢复 PASS；Trace 命中 GitHub 渠道的 Jina Reader，未发生 degradation。 |
| 2026-07-12 | **DeepResearch 证据质量与 Session 完整报告真机通过 ✅** — 修复无关百科证据在 rerank 503 时漏入、完整报告只存 blob/Session 仅显示短摘要、AsyncHandoff 后输入区持续“停止”三处问题。真机两轮报告均完整显示并带可点击引用；首轮 5069 字、6 条 Agent Harness 相关来源，完全重启后历史仍恢复。独立审计后将准入门强化为中英文主题锚点，拒绝漂移子问题自证，并隔离真实语义/部分 LLM 重排与普通词面分数。自动化：180 + 21 + 33 后端用例、35 前端用例与 tsc 全绿。 |
| 2026-07-11 | **DeskPet 原生 Workflow Engine 完成并真机通过 ✅** — 移除 LangGraph/LangChain Core/LangSmith 运行与打包依赖，三条长任务默认使用 `deskpet-native-json-v1` checkpoint。workflow `295 passed`、产品入口 `85 passed`、前端 tsc + `48 passed`、冻结构建/启动 PASS。普通 Session 真发 2 页 PPT、看到单卡进度和大纲卡、真实点击确认后完成交付；完全重启后 12/12 完成卡和附件仍恢复；open decision 也可由 durable event 在重启后恢复为可点击确认卡。 |
| 2026-07-11 | **DeepResearch 子调研 fan-out 默认开启 ✅** — 出厂配置、当前开发配置与代码缺省统一为 ON；宽主题拆出至少 2 个子问题时有界并发执行，默认 research lane 并发 2、最多 6 个子问题，子跑自动降档后统一综合引用。验证：TOML 解析通过，联合回归 `57 passed`。 |
| 2026-07-11 | **PPT 六类构图与 Session 单卡动态进度真机通过 ✅** — 最终真实 run `371e34dd90c044c0963c748f7a3ce6c9` 交付 6 页 full-page deck，覆盖封面底栏、左右互换、场景标题带与居中总结；每页 1 picture，视觉审查 6/6 `ok`。PPT 进度卡真实更新到 `12/12 / 100%`，修改大纲、再次确认、修订、滚动锚定与重启恢复均不刷屏。修复 provider 失败静默模板回退并补跨 checkpoint 重试。后端 `450 passed`，前端 tsc + `34 passed`，PPT/montage/UI 截图已归档。 |
| 2026-07-11 | **PPT 大纲语义、长任务 Session 实时进度与整页图片 PPT 真机通过 ✅** — PPT/DeepResearch/Complex Code 节点阶段进入普通 Session；Code Run `e3ff6a6307114554a7f588cba6952ba2` 真批准后 completed。PPT image-mode Run `a68bf00144d143efa98b552d3cba14d1` 真实交付 `deskpet-ppt-1783724870.pptx`，21 页均为单一全幅 picture，无文本 shape/水印/黑块。修复 Code 路由与 provider chain（含实际 provider ID Trace），并把短 deck 工具与 Graph 契约放宽为 1-20 页。验证：后端宽回归 `478 passed`、聚焦 `77 passed`、前端 `767 passed`、tsc PASS。 |
| 2026-07-10 | **PPT Pro session 内确认闭环修复并真机通过 ✅** — 根因是 durable outline decision 只在 Trace/Inspector 暴露，session 没有大纲卡与 resume 桥接，点击后也缺少即时反馈；现已把大纲卡投影到当前 session，session 决定映射到 open durable decision 并后台恢复，立即显示“正在生成 PPT”，同时将 Inspector 决定面板前置。验证：后端 `28 passed`、前端 `10 passed`、tsc 通过；独立 session `6af52d3e-ff14-47f6-9d7f-e80728ae79ad` 使用 Windows Computer Use 真点击“确认生成”，界面变为“已提交决定”并立即出现继续生成消息，run `f25296978c56472fbddc8d5cdfbe578a` 日志记录 `ppt_outline_decision_resolved`。 |
| 2026-07-10 | **Wave B Task 7 Durable HITL 生产闭环完成 ✅** — fenced saver 识别真实 LangGraph `__interrupt__` pending write，在同一 SQLite 事务创建 open decision、固定 interrupt/checkpoint identity、推进 run waiting 并释放 lease；`workflow_decision_resolve` nonce/version CAS 成功后从 DB 重读权威 response 并自动调用 runner resume，重复/过期请求不会触发执行。新增 graph→decision→IPC resolve→resume 集成测试，覆盖 store 重建、双击与过期；验证：聚焦/相邻 `29 passed`，`compileall` 通过。 |
| 2026-07-10 | **Wave E Task 19 diagnostics/retention foundation 完成 ✅** — 新增 caller-supplied retention policy 与 ClockPort 驱动清理器，固定 delivered terminal events/deliveries → evaluation/run tombstone → checkpoint owner/reachability → blob refs → orphan grace 顺序；30 天 full payload、180 天 evaluation/tombstone、24 小时 orphan 边界由调用方提供。live/nonterminal、active decision、undelivered delivery、retained evaluation 与 implementation hash 均受保护；启动期清 expired target reservations、temp/orphan blobs并报告 missing registered files；dry-run 不改 DB/文件，诊断路径限制为 blob-root 相对路径；redaction resource 缺失、畸形、空规则均 fail closed。验证：聚焦/相邻 `22 passed`，全 workflow regression `207 passed`。 |
| 2026-07-10 | **Wave D Task 15 Complex Code 生产接线完成 ✅** — 新增 runtime `ProposalPort`/`ToolDispatchPort` 与 capability/session snapshot；LLM provider call 转严格 `ProposalOutcomeV1`，prepared call/effect id 稳定且 outcome JSON-safe；`main._run_chat` 在 plan/AgentLoop 前按 `route_task` 分流，flags ON 的 code action accepted-async 启动 `code_complex:v1`，只结束当前 chat turn，launcher 持续持有 graph，read-only 保持 ReAct。验证：聚焦+相邻 graph `28 passed`，main harness `51 passed`，真实 registry snapshot smoke 通过。 |
| 2026-07-10 | **Wave D Task 15 Complex Code durable graph 完成 ✅** — v1 图覆盖 intake→clarify→plan→approval→proposal/effect→completion→test/audit→bounded fix→finalize；proposal checkpoint 固定 prepared args/stable call id，崩溃恢复不重复 LLM 且复用部分 effect；稳定 todo、硬预算、CapabilitySnapshot/dynamic HITL、terminal intents 与 ReAct 路由语料均有聚焦测试。验证：`22 passed`，全 workflow regression `180 passed`。 |
| 2026-07-10 | **Wave D Task 14 PPT Pro durable graph 完成 ✅** — 新增 v1 图与 PPT stage adapters：research_core 独立阶段恢复且不产生 DeepResearch terminal delivery；大纲使用 LangGraph interrupt 决策屏障并投影 legacy history；逐页 stable slide id checkpoint 防图片重复；render/preview/visual review 有界修订；publish 后才生成 success/error/cancel Receipt 与 Artifact/final/open intents。补齐生产 `PptRuntime` operation ports 与 startup effect/evaluator 接线，所有图片、渲染、预览、评估及文件 hash 均在专用 executor 执行，flag false 仍走 legacy。验证：生产接线 `5 passed`，graph+接线 `16 passed`，全 PPT regression `142 passed`。 |
| 2026-07-10 | **Durable Graph Workflows Wave A 地基完成 ✅** — 用 `$plan-test` 恢复 AC-1..AC-18 全范围并经 6 轮挑战收敛；固定 LangGraph 1.2.8 / checkpoint 4.1.1 / SQLite saver 3.1.0，完成 v17 migration closure、DeskPet graph contracts、独立 workflow.db schema、fenced async saver/run CAS/blob store、Trace/Evaluation 核心与确定性路由。验证：workflow 专项 `62 passed`，v17 迁移聚焦回归 `132 passed`，前端 `753 passed` + tsc/build 通过，Rust cargo check 通过。三条生产 graph、Inspector UI、回放评测和真机 E2E 继续按 [plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) 执行。 |
| 2026-07-09 | **file_glob 扫描降噪与加速完成 ✅** — 后端 workspace `file_glob` 默认递归扫描会剪枝重型/生成目录（`node_modules`、`__pycache__`、`.uv-cache`、`backend/assets` 等），减少 agent 文件工具噪声；返回去重排序的 `skipped_dirs/skipped_count` 便于诊断。挑战反馈补强显式 root 兼容边界：用户明确 `root="node_modules"` 时仍可访问，不把默认优化变成能力删除。测试：执行前 baseline `27 passed`；实现后 `backend/tests/test_deskpet_tools_file.py` `33 passed in 0.87s`，相邻 registry/search 工具 `31 passed`；无 UI 改动，windows-mcp 不需要。 |
| 2026-07-09 | **Agent harness hardening Round 5 一步到位 gate 完成 ✅** — 在 Round 4 AST 调用点基础上，`test_agent_harness_main_callsite_contract.py` 增加动态 `/ws/control` 执行：TestClient 真实发送 `chat` 和 `chat_v2`，仅替换 `build_agent`、broadcast/context-usage 副作用和 problem pipeline，断言 sentinel services 进入 `build_agent(...)`、pre-loop system injection 进入 fake agent messages、`_agent.run(...)` 带 runtime context、WS 发出 `chat_v2_final`。聚合验证：`112 passed in 6.96s`。 |
| 2026-07-09 | **Agent harness hardening Round 4 生产调用点补强完成 ✅** — 新增 `test_agent_harness_main_callsite_contract.py`，AST 检查 `main.py` 真实 chat 路径 `_agent = build_agent(...)` 是否传入 goal store/checker、context compressor、skill loader/matcher、tool path recorder、memory curator、evidence gate、pipeline problem/investigation/observability/convergence 参数；同时检查这些资源来自 `service_context`、evidence gate 受 pre-loop short-circuit 保护、`_agent.run(...)` 带 session/runtime context。聚合验证：`110 passed in 5.41s`。 |
| 2026-07-09 | **Agent harness hardening Round 3 补强完成 ✅** — 在 Round 2 runtime/wiring 测试基础上，新增 `test_agent_harness_contract_parity.py` 固定 `build_agent()`/`AgentLoop.__init__` 关键 harness 参数 parity、PipelineEvent WS payload shape、manifest runtime observability；扩展 runtime contract 覆盖 legacy `dispatch()` fallback。新增测试当场抓出并修复 manifest 观测词汇漂移：`completion_gates` 未列真实 pipeline events。聚合验证：`105 passed in 4.86s`。 |
| 2026-07-09 | **Agent harness hardening Round 2 补测完成 ✅** — 在首轮 manifest/docs/policy/service_context 护栏基础上，按用户反馈补强真实运行时测试：新增 `test_agent_harness_runtime_contract.py` 覆盖 `AgentLoop.run()` 工具调用回灌、EvidenceGate 阻断/取证/放行、subagent completion queue drain；新增 `test_agent_harness_build_agent_contract.py` 覆盖 `main.build_agent()` 到 `AgentLoop` 的 problem pipeline、subagent registry、iteration tracer 接线。聚合验证：`100 passed in 4.92s`。 |
| 2026-07-08 | **Agent harness hardening 完成 ✅** — 按 `$plan-test` 对 DeskPet harness 八个明显缺点逐项调研并落成治理切片：新增机器可读 `backend/deskpet/agent/harness_manifest.py`（request lifecycle / service wiring / defect→AC mapping）、`ARCHITECTURE/AGENT_HARNESS.md`、`docs/agent-harness-lifecycle.md`、计划与 testcase；新增 pytest 护栏覆盖 lifecycle owner、状态/恢复边界、ServiceContext 白名单、ContextAssembler policy fan-out、文档存在性。子代理挑战补出第 9 个相邻风险（event schema/WS/DB/receipt/frontend card 契约漂移）并纳入后续治理轨。本轮测试抓出并修真实策略 bug：`policy.py` 显式 `tools: []` 被错误默认成 `["*"]`，emotion policy 可能暴露全工具，已改为仅字段缺失才默认 `["*"]`。验证：新护栏 `36 passed`；相邻 `test_problem_pipeline.py/test_deskpet_context_assembler.py/test_subagent_nonblocking.py` `57 passed`。 |
| 2026-07-08 | **DeepResearch 报告卡片按钮恢复可用 ✅** — 用户反馈报告卡片“打开 / 在文件夹中显示 / 复制路径 / 另存为”点击无反应；根因是 deepresearch 报告落在 `DeepResearch/`，但 Tauri artifact 白名单只允许 `<user_data>/artifacts|downloads|OutPut`，invoke 被拒绝且前端无错误反馈。修复：Rust `artifact_ops` 增加 runtime `DeepResearch/` 根目录白名单（`DESKPET_DEEPRESEARCH_DIR` / `DESKPET_BACKEND_DIR` / 安装目录 / cwd 推导），React `ArtifactCard` 增加按钮 pending/success/error 状态。验证：`cargo test artifact_ops --lib` 7 passed；`vitest run src/code-panel/ArtifactCard.test.ts` 11 passed；`tsc -b` passed；真机 UI 点击 `复制路径` 显示“路径已复制”且剪贴板为 `F:\projects\deskpet\DeepResearch\...md`，点击 `在文件夹中显示` 显示“已在文件夹中定位”，点击 `打开` 成功拉起 Windows `.md` 打开方式选择器。 |
| 2026-07-08 | **DeepResearch 查询质量二次复测 PASS ✅** — 针对“中文子问题 + site:...”搜索质量弱的问题重新测试：重启 Tauri 源码后端（日志确认 `Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`），真实 DeskPet UI 在 `default` 会话发送“请调研一下俄乌最近的局势，给我带来源的简明结论”。UI 先出现 `调用 deepresearch`，数据库确认随后 4 条 `web_search` 参数均为英文独立查询：`Russia Ukraine war latest situation July 2026 Reuters...`、`Ukraine war latest battlefield situation July 2026 ISW...`、`UN Ukraine civilian casualties latest 2026...`、`NATO Ukraine aid latest 2026...`；判定脚本输出 `has_chinese_site=False`、`has_any_chinese=False`、`has_site=False`。同时工具结果抓到 ISW/AP/BBC/UNOCHA 等来源且多条 `fetcher=scrapling`。新增/复跑回归：`pytest backend/tests/test_deskpet_research_tools.py -q` 87 passed；`pytest backend/tests/test_deskpet_context_assembler.py -q` 42 passed。 |
| 2026-07-08 | **DeepResearch source packs 真机 UI 补测 PASS ✅** — 用真实 DeskPet UI 新建 UUID 会话 `a8204ba6-18df-4856-ae05-de3a8bdb5f9e`，粘贴“请调研一下俄乌最近的局势，给我带来源的简明结论”并点击发送；UI 出现 `deepresearch` 工具卡、报告文件卡和最终整理回复。日志确认 `name='deepresearch'`、`deepresearch_finalize_queued`，并出现 ISW/UN/Reuters/AP/BBC/Al Jazeera source-pack 查询；未出现后续独立 `name='web_search'` 工具调用。首轮手测发现 source-pack 查询由中文子问题拼 `site:` 导致命中质量弱，已改为 standalone authority-directed queries 并重跑 `pytest backend/tests/test_deskpet_research_tools.py -q` 87 passed。证据：`plans/manual-results-2026-07-08-deepresearch-source-packs/RESULTS.md`。 |
| 2026-07-08 | **DeepResearch source packs 默认开启 ✅** — 为 deepresearch 搜索计划增加 deterministic source packs：俄乌/Ukraine 高时效主题自动补 ISW、UN、Reuters/AP/BBC/Al Jazeera 定向搜索，金价/黄金主题补 LBMA、World Gold Council、Investing；查询去重、每子问题有上限、`[research].source_packs=false` 可关闭。`coverage.route` 新增 `source_packs_enabled` / `source_packs_hit` / `source_pack_queries`，skill 文案同步 source packs + Scrapling-first 抓取，并继续禁止外层手动 `web_search`/`web_fetch` 拼报告。验证：py_compile passed；deepresearch focused 87 passed；Scrapling/AgentLoop adjacent 16 passed；task-drift/research-sources/search-provider adjacent 69 passed；deepresearch+Scrapling focused 91 passed。 |
| 2026-07-08 | **deepresearch 成功后强制收束，不再默认补搜 ✅** — 用户截图确认 deepresearch 报告已保存后外层 AgentLoop 仍继续调用多条 `web_search`，根因是 deepresearch 结果只被当作普通 evidence，下一轮仍开放 `web_search/web_fetch` schema，模型会倾向补搜。修复：`AgentLoop` 新增 `_deepresearch_result_is_complete()`，兼容 v2 registry envelope 与 legacy 结果；当 `deepresearch` 返回 `ok=true`、有 `report_md` 且带 `citations` 或 `path/artifacts` 时，追加“deepresearch 已完成”系统收束提示，并排队下一轮 `tools=None` + `tool_choice=none`，让模型只能基于报告给最终答复。失败/空报告不触发，仍可继续补搜。验证：`py_compile agent/agent_loop.py` passed；`pytest tests/test_deskpet_agent_loop.py tests/test_agent_loop_sentinel.py tests/test_wi1_tool_choice.py -q` 21 passed；`pytest tests/test_agent_loop_pipeline.py tests/test_task_drift_fixb.py tests/test_research_scrapling_priority.py tests/test_deskpet_tools_web.py -q` 30 passed, 1 deselected。 |
| 2026-07-08 | **Scrapling 调研链路可观测性 + deepresearch 状态文案收口 ✅** — 用户反馈“deepresearch 已生成文件但还在处理”以及 UI 仍显示 `web_fetch`，复查日志确认外层工具名仍叫 `web_fetch`，但真实抓取已出现 `INFO:scrapling:Fetched...`；deepresearch 工具落盘报告后，外层 AgentLoop 还会继续综合/补充核验，因此旧文案误导为“任务已经结束”。修复：`web_fetch` 成功/回退结果都显式返回 `fetcher`，并把 `fetcher` 排在 `content` 前；`web_extract_article` 与 deepresearch `default_extract` 同步透出 `fetcher`；消息面板 deepresearch artifact 行改为“deepresearch 报告已保存，正在整理答复”。验证：`py_compile backend/deskpet/tools/web_tools.py backend/deskpet/tools/research_tools.py backend/main.py` passed；`pytest tests/test_deskpet_tools_web.py tests/test_scrapling_tools.py tests/test_research_scrapling_priority.py tests/test_js_render_flag_off.py -q` 22 passed, 1 deselected；`tauri-app` `tsc -b` passed。 |
| 2026-07-08 | **default 会话“只落库无回复”防卡死修复 ✅** — 用户在 `default` 里发送“请调研一下 俄乌最近的局势”后无反应；复查确认消息已写入 SessionDB（id=211），但后续没有 `assembler_task_classified/p5s2_chain_resolved/deepresearch`，说明主 `_run_chat` 在落库后、进入 agent loop 前被卡住。定位到 `_broadcast_default_chat_peers()` 注释写 best-effort，但实际逐个 `await peer_ws.send_json()` 且无超时；任一 stale/半死 peer WS 都可能把主聊天 task 卡在 user echo 广播，造成“数据库有用户消息但 UI/agent 没继续”。修复：peer 广播改为 `asyncio.wait_for(..., timeout=1.0)`，超时只 debug 记录并放行主任务。验证：`py_compile backend/main.py` passed；`pytest tests/test_main_task_scope_wiring.py tests/test_deskpet_session_db.py -q` 29 passed；重启 dev 后源码后端 startup complete，8100/5173 正常，message-panel/code/default control WS 均连接。 |
| 2026-07-08 | **Scrapling-first WebFetch / DeepResearch 抓取层 ✅** — 按用户要求把 Scrapling 从“金价特例工具”上提为网页抓取优先层：`web_fetch` 在保留 robots/rate-limit/block-cache 后真实页面抓取先调用 Scrapling，成功则直接返回 `fetcher=scrapling`，失败或疑似 blocked 页面再回退 httpx；deepresearch 的 `default_extract` 在真实运行路径先用 Scrapling 抓 HTML，再复用 trafilatura、JS render、Jina 与源质量过滤链路，抽取器标记为 `scrapling+trafilatura`。为避免测试 mock 网络误触真网，测试层显式关闭 Scrapling helper，并新增 web_fetch 与 deepresearch Scrapling 优先级回归。验证：`pytest tests/test_deskpet_tools_web.py tests/test_scrapling_tools.py tests/test_research_scrapling_priority.py tests/test_js_render_flag_off.py tests/test_research_sources.py tests/test_task_kinds.py tests/test_problem_pipeline_config.py -q` 66 passed, 1 deselected；`py_compile deskpet/tools/web_tools.py deskpet/tools/research_tools.py deskpet/tools/scrapling_tools.py` passed。 |
| 2026-07-08 | **Scrapling 金价抓取工具集成 ✅** — 按用户要求把 `D4Vinci/Scrapling` 直接集成进 DeskPet 工具层，而不是只依赖通用搜索。新增 `gold_price_lookup`：用 Scrapling Fetcher 抓取 Investing.com XAU/USD 与 USD/CNY 页面，抽取 `instrument-price-last`，返回美元/盎司、美元兑人民币、人民币/克估算价与来源 URL；新增 `scrapling_fetch` 通用抓取兜底工具，并把 `gold_price_lookup/scrapling_fetch` 加入 chat/research/web 工具暴露策略。依赖改为 `scrapling[fetchers]>=0.4`，PyInstaller spec 补 Scrapling/curl_cffi/browserforge 等 hiddenimports/datas。复盘 `8f577132-547c-41d6-b555-210aa746cbfd` 失败根因：通用 `web_search` 全引擎空结果，多个站点 robots/403/TLS 阻断，Investing.com 实际已能抓到价格但 agent 在 max_iterations 收敛前没有正常回传。验证：Scrapling live smoke 返回 XAU/USD 与 USD/CNY 并估算 CNY/g；`pytest tests/test_scrapling_tools.py tests/test_deskpet_tools_web.py tests/test_search_provider.py tests/test_task_kinds.py tests/test_problem_pipeline_config.py -q` 63 passed, 1 deselected；`py_compile` passed。 |
| 2026-07-08 | **消息气泡一键复制按钮 + 气泡选区 UI 收口 ✅** — 按用户要求在消息框底部补复制按钮：主消息面板的 user/assistant 普通对话把复制入口移到气泡下方，与时间同排显示；code 面板的 user/assistant/reasoning/slash/error/plan/skill/outline 等可读消息保留气泡下方复制入口；tool calling / tool result 轨迹不再显示复制按钮，避免工具卡片噪音。assistant markdown 复制源文本，保留代码块/列表/表格格式。复制控件改为纯 icon 微按钮（copy/check 图标，无文字标签），保留 tooltip/aria 反馈；用户消息气泡改为更沉稳的深蓝渐变，并为消息正文选区增加高对比青蓝高亮，解决鼠标拖选复制时看不清选中文字的问题。验证：`vitest run src/components/__tests__/MessageStreamMarkdown.test.tsx src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts` 20 passed；`tsc -b` passed；`vite build` passed。 |
| 2026-07-08 | **新话题按钮逻辑与 UI 交互收口 ✅** — 「新话题」按钮语义改为输入为空时创建空白新话题，输入非空时显示“作为新话题发送”并把当前草稿作为新 UUID 会话的第一条用户消息；按钮有创建中禁用态与 loader 图标，控制通道发送失败时恢复草稿并显示错误，不再吞掉用户输入。后端在 `session_switched/task_session_started` 后给 originator 补发解析后 UUID session 的 `chat_v2_user_echo`，修复新话题首条草稿可能只触发 assistant、当前窗口看不到 user bubble 的断层；消息面板 `sessions_list` 过滤同步接纳 UUID，会继续保留旧 `task-*` 仅作历史兼容，避免新 UUID 会话被列表隐藏后 UI 仍露出旧 `task-default-*`。验证：`vitest run src/code-panel/InputBar.chat.test.tsx src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts` 19 passed；`pytest backend/tests/test_main_task_scope_wiring.py -q` 6 passed；session 相关后端回归 38 passed；`py_compile backend/main.py` passed；`tsc -b` passed；`vite build` passed。 |
| 2026-07-08 | **新建会话 session_id 收口为 UUID ✅** — 按用户要求停止生成 `task-default-*` / `task-task-*` 这类可读但混乱的任务会话 id。`TaskSessionManager` 的 `/new`/`new_session` 路径改为生成标准 UUID，并维护父会话映射避免从 UUID 会话继续新建时丢失 default peer group；`SessionDB` 新增 `ensure_session(session_id, metadata)`，主聊天与语音新会话在广播/写消息前先写入 `sessions(id TEXT PRIMARY KEY)`，使 UUID 成为新建会话的 canonical 主键，`messages.session_id`、标题、删除、receipt 等继续引用同一个 UUID；消息面板错过切换事件时的兜底不再依赖 `task-` 前缀。旧 `task-*` 历史仍可读取/重命名/删除。验证：`pytest backend/tests/test_task_scope.py backend/tests/test_main_task_scope_wiring.py backend/tests/test_voice_task_scope.py backend/tests/test_deskpet_session_db.py -q` 35 passed；`py_compile` passed；`vitest run src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts` 14 passed；`tsc -b` passed；`vite build` passed。 |
| 2026-07-08 | **消息面板会话交互增强 ✅** — 按用户截图反馈统一顶部标题与历史列表的会话名称展示，当前会话/历史项均优先显示用户可重命名的自定义名称；`session_id` 从名称文本中拆出，改为黄色等宽徽标并开启文本选中，便于复制；历史会话删除改为先弹确认框，确认后才发送 `session_delete`，默认话题使用“清空”文案避免误删。验证：`node node_modules/typescript/bin/tsc -b` passed；`node node_modules/vite/bin/vite.js build` passed。 |
| 2026-07-08 | **异步 PPT 完成通知未实时显示修复 ✅** — 用户反馈 `task-default-5` 的“俄乌战争局势”PPT 已生成但 session 里没有显示“做完/返回完成状态”。复查确认后端 SessionDB 已有 tool artifact 与 `✨ PPT 做好啦...deskpet-ppt-1783473814.pptx`，但消息面板实时链路未稳定收到异步 `ppt_pro` 后台完成事件，用户只能看到大纲/空闲状态。修复：`_ppt_notify_chat_bubble` 与 `_ppt_artifact_push` 改为携带目标 `session_id` 广播到所有 control 窗口，而不是只发给单个猜测 websocket 后再依赖 peer group 转发；artifact push 改为先落 SessionDB 再广播。新增后端回归覆盖“只有 default + message-panel-main、没有 task sid 直连时仍广播完成事件”，新增前端 `session_messages_response` 回归确保历史恢复包含 artifact tool_result 与最终完成文案。验证：`pytest backend/tests/test_ppt_outline_wiring.py -q` 8 passed；`vitest run src/code-panel/ws.chat.test.ts` 3 passed；`tsc -b` passed；重启 dev 后真实 Computer Use 选择 `task-default-5`，消息面板显示 `📊 deskpet-ppt-1783473814.pptx` 与 `✨ PPT 做好啦，已自动打开...`。 |
| 2026-07-08 | **消息面板发送无回复 + 切历史 session 空流修复 ✅** — 复现用户问题：真实 UI 在消息面板发送文本后只显示用户气泡和“思考中”，后端 `state.db` 不写入、日志无 chat 入站；点击顶部历史 session 时也可能显示“消息流为空”。修复：`InputBar` 普通聊天不再把实际 `chat_v2` 发送包进无效的 `chatLimiter`，`codePanelWS.send()` 改为返回发送结果，交互消息在控制通道未连接时立即失败并清掉 thinking，避免永久转圈；`codePanelWS` 复用 HMR/global OPEN socket 时会同步 connected 状态并 flush 当前 outbox；`MessagePanelRoot` 在 `activeSid` 变化时主动拉 `session_messages_load` + `context_usage_request`，切历史会话会回填 SessionDB。验证：新增 `InputBar.chat.test.tsx`；`vitest run src/code-panel/InputBar.chat.test.tsx src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts src/stores/sessionsStore.test.ts` 33 passed；`tsc -b` passed；真实 Computer Use UI 复测：消息面板发送后后端出现 `POST https://chinzy.com/v1/chat/completions 200 OK`，UI 收到 assistant 回复并回到空闲；点击顶部 session 下拉切回“默认话题”后历史消息即时回填，不再空流。 |
| 2026-07-08 | **beta.9 release readiness audit: full build now works, release still blocked ⚠️** — Installed Visual Studio Build Tools C++ workload and completed a full Tauri release rebuild (`deskpet.exe` + NSIS) instead of reusing the old shell. Rebuilt the frozen backend into `F:\deskpet-build\dist` and confirmed the frozen runtime reaches `provider_registry_ready`, `p4_embedder_ready is_mock=False`, and `startup complete` under the longer 75s startup window. The new unsigned installer `DeskPet_0.6.0-beta.9_x64-setup.exe` is 2026-07-08 02:46 and silently installs a new Rust shell plus the new 00:42 frozen backend. Also fixed a release-window visibility bug in `window_geometry`: when startup monitor lookup fails, the main window now falls back to a safe `(100,100)` position instead of remaining offscreen. Do **not** publish beta.9 yet: `.env` contains `TAURI_SIGNING_PRIVATE_KEY` but its value length is 0, so the beta.9 `.sig` is still the old 2026-06-28 file; Computer Use still cannot target the transparent/titleless main window and the installed app did not produce a fresh backend startup log during E2E attempts. Current shareable build remains beta.8 until a real signing key is supplied, the installer is signed, `latest.json` is generated from the new `.sig`, real UI E2E passes, and GitHub/COS upload is completed. |
| 2026-07-07 | **Agent harness drift/queue bugs fixed ✅** — Review-driven harness cleanup: repo `config.toml` now ships `[tools.verifier].verify_gate_mode="strict"` so effective startup config matches `AppConfig()`/STATUS no-gray contract; `[skills]` subtables without `knowledge_enabled` now preserve the default ON knowledge injection; `await_subagents` now collects already-completed runs when `run_ids` is omitted and consumes completion-queue entries for explicitly awaited runs to avoid duplicate AgentLoop injection; `[features.problem_pipeline].analysis_model` in repo config now ships `deepseek-v4-pro` to match the tested dataclass default. Added focused regression guards for repo config effective values, skills subtable defaults, and subagent await/queue semantics. Verification: per-fix focused pytest passed; harness regression set `50 passed`. |
| 2026-07-07 | **PPT/图片生成 401 降级模板根因修复 ✅** — 用户追问“AI 配图服务不可用，之前可用”为何发生；复查本轮日志确认 `ppt_pro _render_pro start image_mode=True` 后连续请求 `https://chinzy.com/v1/images/generations` 全部 `401 Unauthorized`，因此 `ppt_pro gate reachable=False n_ok=0` 并降级模板。根因不是 `doubao-seedream-4.0` 模型下线，而是 `image_tools._resolve_endpoint()` 仍走旧 `llm_runtime.json`/`resolve_cloud_api_key()` fallback，未接入登录后的 `provider_registry relay-cloud` key；主聊天/DeepResearch 已拿到当前 key，所以同会话聊天可 200、图片却 401。修复：`image_tools` 新增 `set_endpoint_resolver()`，运行中后端优先使用注入的 provider-registry base_url/key；`main.py` 新增 `_refresh_image_endpoint_resolver()` 并修正 relay provider 解析为 `_reg.get_entry(RELAY_PROVIDER_ID)`，保证图片/PPT 生图与当前登录 relay provider 对齐。验证：`py_compile backend/main.py backend/deskpet/tools/image_tools.py`；`pytest backend/tests/test_image_config_section.py backend/tests/test_generate_image_tool.py backend/tests/test_image_probe_classify.py -q` 21 passed；PyInstaller frozen backend 重打并同步到 Tauri debug；桌面版重启日志确认 `image_endpoint_resolver_wired`、`relay_provider_ensured ... key_fp=6d8aeceb`、`Application startup complete`。 |
| 2026-07-07 | **PPT 大纲确认后“无回复”实为任务会话未切回修复 ✅** — 用户再次实测确认大纲后左侧无任何后续回复；复查真实运行栈确认后端实际已完成两次：`task-default-2` 最新轮次在 `ppt_outline_decision_resolved` 后跑完 `ppt_pro _render_pro image_mode=True pages=10`，9/10 张 AI 配图 200、1 张 400 后按纯色版式兜底，文件已落盘 `tauri-app/src-tauri/target/debug/userdata/OutPut/PPT/deskpet-ppt-1783421197.pptx`，会话库已有“✅ 大纲已确认，开始生成…”、“有 1/10 张配图没生成成功…”、“✨ PPT 做好啦…”和 artifact tool_result。根因在前端消息面板：本地 `activeSid` 只在 `task_session_started/session_switched` 瞬时事件切换；若面板窗口错过该事件，后续 `chat_response/tool_result` 虽写入 `task-*` store/DB，UI 仍停留在 `default`，用户体感就是确认后没回复。修复：`MessagePanelRoot` 增加对全局 `useSessionsStore.active_sid` 的同步，并在收到 `chat_response/chat_v2_final/tool_call/tool_result/ppt_outline_proposed` 且 payload session_id 为 `task-*` 时补切到任务会话。验证：确认最新 PPT 文件存在 3,050,610 bytes；`node node_modules/typescript/bin/tsc -b` passed；`node node_modules/vite/bin/vite.js build --mode relay` passed。 |
| 2026-07-07 | **PPT Pro 已生成但消息面板不显文件卡修复 ✅** — 用户实测“俄乌战争局势”PPT 确认大纲后看起来仍未生成；复查真实运行栈确认后端已收到 `ppt_outline_decision`、`ppt_pro render done(template) ok=True`，文件已落盘 `tauri-app/src-tauri/target/debug/userdata/OutPut/PPT/deskpet-ppt-1783409176.pptx`，会话库也已有“PPT 做好啦”与 tool artifact JSON。根因是前端展示链路：实时 `tool_result` 只把 `result` 文本塞进 store，丢掉 `artifacts[]`；左侧 `MessageStreamPanel` 又把工具结果压成一行“工具完成”，没有渲染 `ArtifactCard`，所以用户体感像没生成。修复：`code-panel/ws.ts` 对含 artifacts 的 tool_result 保留完整 envelope，历史回放从 JSON 反推 tool 名；`MessagePanelRoot` 透传 tool 原始结果；`MessageStreamPanel` 对 artifacts 渲染文件卡（打开/文件夹等 action 沿用现有 ArtifactCard）。验证：确认 PPT 文件存在 51,957 bytes；`tauri-app` 前端 `tsc -b` passed。 |
| 2026-07-07 | **桌宠 supervisor agent 默认关闭 + 开关持久化 ✅** — 用户追问此前要求关闭的 supervisor agent 为何仍在运行；复查发现出厂 `config.toml [supervisor].enabled` 仍为 true，前端 Settings 默认也按 ON 初始化，且 `supervisor_toggle` 只改运行时 watchdog、不写回 config，导致重启后继续启动。修复：`config.toml` 出厂默认改 `enabled=false`；`backend/main.py` 缺省兜底从 True→False，并新增 `_persist_supervisor_enabled()`，运行时开关只写 `[supervisor].enabled` 且 ack 返回 `persisted`；`SettingsPanel` 默认改 OFF；当前桌面版 `target/debug/userdata/config.toml` 同步改为 `enabled=false`。验证：`py_compile backend/main.py`；`pytest backend/tests/test_config.py backend/tests/test_p5s1_watchdog.py backend/tests/test_p5s1_supervisor.py -q` 42 passed；前端 `tsc -b` passed；PyInstaller frozen backend 重打并同步；桌面版启动日志确认 `p5_supervisor_disabled_via_config` + `Application startup complete`，未再出现 `p5_supervisor_watchdog_started`。 |
| 2026-07-07 | **PPT Pro 大纲卡卡住/失忆修复 + 深度调研质量门 + backend 重新打包启动 ✅** — 用户实测「俄乌战争局势」PPT 只回复“开始调研并拟大纲”，一直没有大纲确认卡；本地会话库确认旧轮次先写入 `PPT 没做成：LLM HTTP 401 Unauthorized`，随后又写入安抚性回复。根因三段：① 前台聊天已走登录后的 `relay-cloud` provider registry，但 PPT/DeepResearch 后台大纲链路仍复用启动时旧的 `research_tools` live LLM callable → 后台 401；② 大纲卡只广播给前端/写 `ppt_outline_history`，未作为 assistant 消息落库，追问“你给我大纲了吗”时上下文可能拿不到大纲正文；③ 初修为解决“久等无卡”曾把 research timeout 收到 45s 并允许 no-research outline，用户指出这会破坏“深度调研版 PPT”承诺，已追加修正。最终修复：`backend/main.py` 启动、`/config/cloud`、provider ensure/update/logout 后统一刷新 research live LLM 与 reranker；`_ppt_outline_propose` 将 `PPT 大纲确认 · ...` 正文写入 `messages` 并入 vector worker；`backend/deskpet/tools/ppt_tools.py` 默认 research timeout 恢复深度调研友好的 360s，新增 `pro_allow_no_research_outline/allow_no_research_outline` 且默认 False，调研无结果时不生成大纲，改为明确提示“先不生成大纲，避免把普通知识当成深度调研结论”，只有用户显式要求跳过调研才可开启。验证：`py_compile main.py/ppt_tools.py`；`pytest backend/tests/test_config_cloud_endpoint.py backend/tests/test_p5s2_provider_registry.py -q` 47 passed；`pytest backend/tests/test_ppt_outline_wiring.py backend/tests/test_ppt_pro_content.py backend/tests/test_ppt_pro_exec.py -q` 35 passed。PyInstaller frozen backend 重打并同步到 `tauri-app/src-tauri/target/debug/backend/deskpet-backend.exe`，桌面版已重启，日志确认 bundled backend + `research_live_llm_refreshed ('https://chinzy.com/v1','gpt-5.5')` + `Application startup complete`。 |
| 2026-06-29 | **桌宠消息大框 assistant 回复按 markdown 渲染（含 GFM 表格）✅** — 修用户实测发现的缺口：消息面板（`components/MessageStreamPanel.tsx` 的 `ChatRow`）此前把 LLM 回复当**纯文本** div 渲染（`{text}`）→ ```code```/`**bold**`/列表/表格标记全裸露，而 code 模式（`code-panel/MessageBubble.tsx` 的 `AssistantBubble`）一直用 react-markdown 渲染，两边不一致。把那套 react-markdown + 自定义 components（代码块高亮、本地文件链接走 `artifact_open`、列表/段落间距）抽成共享组件 [`components/MarkdownMessage.tsx`](../tauri-app/src/components/MarkdownMessage.tsx)，两处复用；assistant 走 markdown、user 输入保持纯文本（保留换行不被误解析）。**真测（windows-mcp）时发现额外缺口**：GFM 表格 react-markdown 默认不渲染（code 模式同样缺）→ 加 `remark-gfm` 插件 + 深色主题表格样式（描边/表头底色/横向滚动）。回归测试 [`MessageStreamMarkdown.test.tsx`](../tauri-app/src/components/__tests__/MessageStreamMarkdown.test.tsx) 4 测（代码块→`<code>`无裸围栏 / 加粗+列表→真元素无 `**` / 表格→真 `<table>`无裸管道 / user 纯文本）+ `tsc --noEmit` 全绿。**真机 E2E 已验**：同一金价 markdown 内容（原 bug 截图同源）现标题/加粗/代码块/表格全部正确渲染。 |
| 2026-06-28 | **frozen 后端 BGE-M3 embedder 静默降级 mock 修复（预存于所有发布版）✅** — 装机版 PyInstaller 后端启动时 embedder worker 加载失败、静默回退 mock embedder → 记忆/向量召回退化（≥beta.4 全中招，与空 Bearer 无关）。**在真二进制上逐层定位根因**：`from FlagEmbedding import BGEM3FlagModel` 在 frozen 下连撞三层——① FlagEmbedding 1.3.5 推理 import 链里 `abc/finetune/embedder/AbsDataset.py:5` 有一句**裸 `import datasets`**（纯训练依赖，spec 故意 `excludes` 以免拖 ~150MB pyarrow/pandas 并曾崩构建期分析）；② FlagEmbedding `__init__` eager import reranker 全家桶，MiniCPM modeling 定义期经 transformers docstring 装饰器调 `inspect.getsource` → frozen 无 .py 源码 `OSError`；③ 构建 tokenizer 时 `tokenizer_class_from_name` 动态枚举 `transformers.models.*`，XLMRobertaTokenizerFast 挂 MetaCLIP-2 名下、遍历撞 frozen 未打包的 `metaclip_2` 子模块 `ModuleNotFoundError`。**修复（单点零体积零 spec 改动）**：`embedder_worker._apply_frozen_compat()`（仅 `sys.frozen` 生效，dev no-op）在 import FlagEmbedding 前——注入只含 `Dataset` 的 `datasets` stub 到 `sys.modules`（dunder→AttributeError 让内省优雅降级、真 `__spec__` 过 `find_spec`、训练符号→RuntimeError）+ 容错包两处 transformers 调用。**真验**：全新 thin-bundle exe 裸跑 worker `is_mock=False`+真 encode 归一化 1024 维向量；完整 backend 启动 `probe-embedder.ps1` 日志 `BGE-M3 subprocess worker ready ... attempt=1` / `p4_embedder_ready is_mock=False` / 无 `No module named` / 无 mock 降级。单测 `test_frozen_embedder_compat.py` + 相关共 21 passed。证据 [01-acceptance-evidence](../plans/2026-06-28-frozen-embedder-datasets-fix/01-acceptance-evidence.md) · 根因 [00-ROOT-CAUSE-AND-FIX](../plans/2026-06-28-frozen-embedder-datasets-fix/00-ROOT-CAUSE-AND-FIX.md)。⏳ **发版未做**（bump+NSIS+签名+GitHub/COS 等用户确认）。 |
| 2026-06-28 | **装机版 userdata 路径安装目录绑定 + 空 Bearer 崩溃护栏 ✅** — 定位并修复用户装机版（F:\deskpet 自定义目录）聊天报 `Illegal header value b'Bearer '`（httpx `LocalProtocolError`）。**根因（非 keychain bug）**：relay-cloud provider 登录时才写进 `<user_data>/config.toml`，而 Rust（`paths.rs` 要求 `userdata/` 已存在才认 portable）与 Python（`paths.py` 主动 mkdir）两套 userdata 解析条件不一致、`spawn_once` 又**未注入 `DESKPET_USER_DATA_DIR`** → config.toml 路径**跨会话漂移** → `get_chain()` 读到空 → 回退 legacy 空 key 拼出非法 `Bearer `。（证据链：main.py `resolve_api_key(...) or "ollama"` 决定 keychain 读不出会发 `Bearer ollama` 而非空 → 空 Bearer 只能来自 legacy 兜底。）**修复 6 Phase**（codex gpt-5.5 并行实现 + 3 轮评估 94→97→**100%**）：① `paths.py` portable 解析确定化（去盘邻居回落、只认 `<install>/userdata`）+ 记忆化 + `.deskpet-portable` sentinel 固化绑定；② **Rust 单一事实源**——`portable_userdata_dir` create-then-return + `spawn_once` 注入 `DESKPET_USER_DATA_DIR`，Python priority-1 命中，双解析归一；③ `config.py _recover_orphaned_endpoints` 启动自愈（canonical 无可用 endpoint 时从 AppData/安装目录迁回 + `.pre-recover-bak`，存量用户无需重登）；④ `providers/openai_compatible.py::_client` 空/占位（含 `ollama` 非本地）key 抛友好 `LLMProviderError(error_class=empty_api_key)` 不拼空 Bearer + agent_loop chain 全失败 error_class 透传 ErrorEvent；⑤ 可观测 `config_loaded portable/env_pinned` + `provider_registry_ready n/enabled/ids`；⑥ spec 钉死 keyring frozen 后端。**单测**：新增 3 套（paths 记忆化/config 自愈/空 key 护栏 12 测）+ conftest autouse 隔离路径缓存，相关 99+ 全绿、全量 **3628 passed**（8 失败 git 验证为 pre-existing flaky/无关）。**windows-mcp 真机**（源码 backend + 隔离 userdata + 真点击+剪贴板中文）：`[backend_launch] Dev python` 跑我的源码 ✓ / `config_loaded portable=False env_pinned=True` ✓（Rust→Python env 归一）/ `provider_registry_ready` ✓ / **`LocalProtocolError` 全程 0 次**（多轮发送+401+连接失败均不崩→友好降级）★headline / **正常 agentic 聊天端到端跑通**（agent 调 `deepresearch` 工具→无回归、护栏不误伤有效 key）★。显式 empty_api_key 文案 + frozen 路径绑定走单测/重打包兜底（dev 机 companion 走 local_llm 恒有注入 key、token 对 /models 401，诚实标 env 约束）。证据 [RESULTS](../plans/manual-results-2026-06-28-userdata-path/RESULTS.md) · plan [2026-06-28-userdata-path-binding-fix](../plans/2026-06-28-userdata-path-binding-fix/00-PLAN.md) · 手测 [testcase](../testcase/2026-06-28-userdata-path-binding/manual-test.md)。**✅ 已发布 0.6.0-beta.8**（bump 4 文件 → backend bundle CPU venv 重打含 Python 修复 + cargo release 编 Rust + NSIS 386MB + Git Bash 单独签名；GitHub release `v0.6.0-beta.8` isPrerelease=false 资产齐全 + COS 双端 `latest.json=beta.8`、安装包 HTTP 200）→ 存量 beta.7 用户（含 F:\deskpet）设置→检查更新即可升级拿修复。frozen 路径绑定最终 E2E 可在更新后真机验。 |
| 2026-06-27 | **FactExtractor 内容哈希幂等去重（Layer 1）+ memory_write 工具去重 ✅** — 修真测发现的「重发完全相同的话 → facts 表累积重复事实」缺口（真测同场景修复前 +3，修复后 **+0**）。① `memory_write` 工具改时间戳 key→内容哈希 key + `find_active` touch 去重；② `FactExtractor.process_message` 加 **Layer 1 内容哈希幂等**——在 LLM 抽取前按归一化内容哈希短路（`_dedup_lock` 锁内抢占登记防并发 TOCTOU / LLM 异常+坏 JSON+持久化异常撤销占位 / 空数组与成功后刷新完成时刻 / `clear_content_cache` 自愈钩），命中打 `facts_extract_skip_dup` info 日志；config `extract_content_dedup` 默认 ON。**plan 经 4 轮对抗 + 竞品对标**（Claude 内容寻址 ID/写时去重、mem0 向量+LLM NOOP、Zep 双时态、Hermes 精确重复拒绝 → L1 内容哈希被 Claude+Hermes **双重验证为核心**；L2a token-overlap 实算证伪对短事实不可靠 → 降 Phase 2 spike）。**codex gpt-5.5 并行实现 + 3 轮评估至 100%**；单测 13 个全绿（含并发 T6/坏 JSON 撤销/空数组刷新/summarizer 不短路）+ 广义套件 109 passed；**windows-mcp 真机 ★ 全 PASS**（TC-1 三证 C0=363→C1=366→重发 C2=366+skip 日志 / 归一化 / 不误短路 / flag OFF BC / TTL 边界）。证据 [RESULTS-FACTEXTRACTOR-DEDUP](../plans/manual-results-2026-06-27-enable-flags/RESULTS-FACTEXTRACTOR-DEDUP.md) · plan [2026-06-27-factextractor-dedup](../plans/2026-06-27-factextractor-dedup/00-PLAN.md) · 手测 [testcase](../testcase/2026-06-27-factextractor-dedup/manual-test.md) |
| 2026-06-27 | **测试阶段全量点亮已开发能力（不灰度）✅** — 按 [`CLAUDE.md` §测试阶段：能力即开即用](../CLAUDE.md) + [HANDOFF](../plans/2026-06-26-agent-harness-alignment/HANDOFF-enable-flags.md)，把"已开发完成但出厂默认 OFF"的 A 表能力一次性翻 ON（路径1 改 `config.py` dataclass 默认 + config.toml 配套）。**33 个 flag**：`[memory.v2]` 语义事实记忆栈 17 个（facts_extract/rerank/enhanced_retriever/chunking/query_rewrite/reflection/cross_key_merge/memory_forget/entity_path/episodic_to_semantic/feedback_loop/goal_facts/light_write/persona_inject/goal_facts_hook/curation_nudge/auto_learnings）+ `[features]` 12 个（slash_commands/goal_mode/agent_parallel/plan_confirm_gate/preference_memory/subagent_driver/agent_team/subagent_nonblocking + 压缩四件套 ctx_observability/adaptive_compact_pct/summary_quality_loop/microcompact_size_aware）+ `[skills]` 3 个（knowledge_enabled/auto_disclosure/codify）+ `[tools.verifier].external_evaluator`。**关键修**：仓库根 `config.toml` 出厂种子里 `[memory.v2]` 有显式 `false`（feedback_loop/rerank/chunking/query_rewrite/reflection）会盖过 dataclass 默认坑新装用户 → 已全部翻 true；`_MIGRATABLE_SECTIONS` 追加 `("features",)`/`("skills",)`/嵌套 skills 段使新默认回灌存量 config。**B 表仍 OFF**（注明原因）：`workspace_memory`(code)/`forget.enable_natural_language`(危险无护栏)/`run_build`/`run_tests`(code)/`plan_read_only`(归 WI-1.2)/artifact 信封(未实装)。**验证**：全套 `pytest -m "not live"` **3603 passed**；翻 ON 引起的 18 个"陈旧默认=OFF 断言"契约测试全部精确更新到测试阶段新契约（g4_flag_matrix 重写为 A 表点亮/B 表保 False 等）；剩余 6 个失败经 git-stash 验证为 master 上 pre-existing（web_search 解析器×2/compaction wiring×2/artifacts 测试污染×1/agent_parallel 计时 flaky×1，与本次改动无关）。⚠️ 真机 windows-mcp 抽测待跑（goal_mode/knowledge_enabled/facts_extract）。 |
| 2026-06-27 | **v0.6.0-beta.7 发布上线 — 内嵌 Live2D 人物形象资源（COS + GitHub 双端验证）✅** — 修复历史发布只内嵌 hiyori、默认形象 estella 缺失致装机后桌宠回退占位图（程序化简笔画）的问题：把主 checkout 策展好的 6 个 Live2D 形象（estella 默认 + hiyori + Azuki-san + HoshinoAi + #Free# Snow Leopard + Estella-DG，~81MB）提交进构建树并打包，设置面板「桌宠形象」下拉可切换全部（vite `petModelsManifestPlugin` 扫目录生成 `models.json`，名字带空格/`#` 已 `encodeURIComponent`）；排除残缺无 `model3.json` 的 Design_genius_White。授权由产品方确认。**backend 自 beta.6 未变 → 复用 beta.6 瘦 backend bundle 免 PyInstaller 重打**；安装包 ~347MB（+43MB = 81MB 模型经 LZMA 压缩）。COS + GitHub 双端 `latest.json` **均报 `0.6.0-beta.7` 且签名一致**，`prerelease=false`。签名仍走 Git Bash + 空口令 + `< /dev/null` 防挂死（详 [release/README.md §5](../release/README.md)）。 |
| 2026-06-27 | **v0.6.0-beta.6 发布上线（COS + GitHub 双端验证）✅** — 基于修复后 master 出 beta.6 瘦包（~292MB NSIS，模型外置 COS）签名发布：COS 国内主源 + GitHub 备源 latest.json **双端均报 `0.6.0-beta.6` 且签名一致**，GitHub release `prerelease=false`（updater `/releases/latest/` 才认）。内容=relay 中转站账户收编完善(A–E)+冷启动收编 bug 修复(`37e40526`)+模型名/上下文档位显示修复+输入条两排式/消息面板极简重设计+`/goal` 任务工具动态可见性修复。**🐛 顺手修 master 漏提交 bug(`63e49a58`)**：上一工作流提交了 `visible_when` 调用方(main.py)+测试却漏提交实现(registry.py)→`goal_task_*` 4 工具静默不注册→`/goal` 任务图哑掉（302 测试转绿）。**踩坑**：签名私钥是 minisign **加密**私钥，PowerShell 传空口令不可靠致 `tauri build` 内嵌签名静默卡密码 prompt→改 Git Bash `npx tauri signer sign … < /dev/null` 单独签（详 [release/README.md §5](../release/README.md)）。 |
| 2026-06-26 | **relay 本地 apikey + provider 收编完成 + 真机端到端 PASS ✅** — 把 relay 登录从「旁路改单例 local_llm」收编进 `LLMProviderRegistry`，作为 `relay-cloud`(source=relay) 行被设置面板统一管理；顺手关闭 §5 P1 账号脱节。**A-E 五 WI 全实现**（codex 并行 + 每阶段子代理评估 100% + 真机循环）：WI-C 账户余额 ¥→USD bug 修复（陈旧硬编码，钱包早 USD 本位，真机 `$718.82`）· WI-1/2 registry 加 `source`/`account_ref` + `ensure_provider`(幂等/抢默认/key缺失强铸/priority去重) + WS `settings_providers_ensure`/`relay_logout`(抽 `relay_provider_ops` 可测，日志只打 key_fp) · WI-B device key 复用三态(`/v1/providers` reuse/meta/force + 按mode分槽dedup + prefix自愈降级 + 冷启动载缓存) · WI-3 `relayProviderRegistration`(单例+inflight串行+lastEnsured防TOCTOU+recover熔断+幂等四问[行在/账号/key在/base_url] + App.tsx 接线) · WI-4 relay 受限三态 UI(徽章/选默认/启停/拖拽/重置key按钮→recover) · WI-5 结构化错误(403 INSUFFICIENT_BALANCE[PR-1前 FORBIDDEN过渡] + 402 嵌套 + 401 INVALID/EXPIRED_TOKEN，仅 source=relay 分类) · WI-6 flag(`RELAY_MANAGED_PROVIDER`/`[features].relay_managed_provider`)+登出删key。**对接中转站 handoff**（不要长期 key 端点，改 `/v1/providers` 复用，回答 3 澄清：¥是陈旧bug/错误两表面/prefix 12字符待PR-6）。单测全绿(auth 127+registry 39+components 114+errors 14+relayErrorText 5)。**真机 windows-mcp E2E**：真坐标点击+真键盘——账户面板 USD★/手填 provider CRUD BC★/relay-cloud 收编出现+relay 徽章+重置key 按钮★/聊天真走 `p5s2_chain_resolved`(registry chain)→`chinzy.com/v1/chat/completions 200 OK`★。⚠️ device key 复用完整真测 + 401/403 自愈真测待中转站 PR-1/PR-6 灰度。证据 `plans/manual-results-2026-06-26-relay-provider-wiC/`。**🐛 登录测试补揪+修一个真 bug（`37e40526`）**：冷启动时 relay login 事件(restoreSession/auto-login)在 control WS connect **之前** emit → `registration.ensure` 在 'no channel' 中止 → **真实用户冷启动收编根本不触发**（之前真测'PASS'是边改边测的 **HMR 反复重挂掩盖**了该竞态）。修：App.tsx 加 connect-trigger(ws→connected+authed 重发 ensure，幂等)+`ensureOnce` channel 检查移到 syncDeviceKey 前(no channel 不浪费轮换)。**冷启动真机复验**：删空 config.toml relay-cloud→fresh 启动→`relay_provider_ensured` 精确 1 次(非 HMR churn)+relay-cloud 从零收编回+聊天 `chinzy 200 OK`+无 [reg] 警告（证据 `EVIDENCE-coldstart-fix.txt`）。 |
| 2026-06-26 | **BUG-B 意图路由 followup 3 阶段全完成 + windows-mcp 最终验收 ★5 全 PASS ✅** — 在七步流水线 P0(真问题误判闲聊短路)已修基础上，解 RESULTS §8.2 残留：**Phase 1 闲聊快路径**——新增共享词法模块 [`lexicon.py`](../backend/deskpet/agent/lexicon.py)（整句锚定 `is_obvious_chitchat` + 否决优先，R2 决策否决用问号 `[?？]` 非裸"吗"以放行整句"在吗在吗"），`intent_triage.analyze()` LLM 前插 allowlist 分支命中→`intent_triage.allowlist_hit`+短路**0 次 LLM**，`_safe_card` chitchat→factual_qa 修取证门漏洞（WI-2b）。**Phase 2 复活组装期 classifier**——`main.py` 注入 `OpenAICompatibleAgentLLM` shim（llm_timeout_s=8s 适配 gpt-5.5 thinking）+ classifier default `chat`→词法地板 fail-closed（确定寒暄→chat/code信号→code/否则→task，复用 lexicon），真 code/debug 问题不再恒拿 `chat` bundle。**Phase 3 收口**——WI-9b 预分析 prompt 去 hint 漂移 + WI-8a `_TASKTYPE_TO_PROBLEM` 单一来源护栏注释（仅 safe-fail fallback、IntentTriage LLM 唯一权威、禁接回当 hint 防 BUG-C；全量合并 deferred）。**每阶段循环**：单测全绿（intent_triage 43 + classifier/assembler 116）+ 子代理评估 100% + 手测文档 + windows-mcp 真机。**最终验收 ★5 全 PASS**：BUGB-1 `factual_qa short_circuit=False`✓/BUGB-2 中文 debug `debug short_circuit=False`✓/BUGB-3 闲聊 `allowlist_hit` 0 LLM✓/BUGB-4 坏 analysis_model `llm_failed`+不短路+裸 ReAct 答✓/BUGB-6 真 code `assembler_task_classified task_type=code`✓。收敛标准达成（★全过+组装 task_type 对真 code/debug 不再恒 chat+闲聊 0 LLM）。证据 [final RESULTS](../plans/manual-results-2026-06-26-bugb-final/RESULTS.md) · [plan](../plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md) · 手测 [P1](../testcase/2026-06-26-bugb-phase1/manual-test.md)/[P2](../testcase/2026-06-26-bugb-phase2/manual-test.md)/[final](../testcase/2026-06-26-bugb-phase3-final/manual-test.md) |
| 2026-06-25 | **七步流水线 Sprint2 完成 + WI-5(b) 默认配置全量 ★ 真测 10/10 PASS（上线门通过）✅** — Sprint2 经 R1/R2 对抗挑战至 EXECUTABLE-AS-IS，按 Y 方案重构（WI-1 取消闲聊短路、每条走 deepseek 预分析 + WI-2 收敛止损 + WI-4 默认 deepseek）。**真测抓修 + 落地多项**：① `intent_triage` safe-fail **绝不短路** + JSON 净化控制字符（`16758f8b`）；② **WI-4-C 预分析稳健化**：非流式预分析保留 strict schema（避 relay `stream+json_schema` 空 body，probe 4-6s 稳）+ 剥 thinking 模型 `<think>` CoT 前缀 + `_parse_contradiction` 防御性 int/float（防 LLM 乱填数值崩 `run_pre_loop`）。**默认配置 windows-mcp 全量 ★**：TC-1/2/3/4/5/9 + IDEM-1/3/4/5/6 全绿（闲聊短路·取证门 glob/grep·抓主要矛盾·异体自检·收敛止损 `error_max_turns` 诚实报告·kill-switch BC·澄清重启持久化·per-run 隔离·失败自愈）。**真测深挖 2 个 relay 上游问题**：(a) 账号脱节 P1（cloud-llm slot 用旧耗尽账号 key，登录从不同步）→ `.env` 固定 key 经 launcher env 注入绕过，followup [relay-cloud-key-sync](../plans/2026-06-25-relay-cloud-key-sync-followup.md)；(b) 账号并发上限过低 `Concurrency limit exceeded` → 诊断报告 [RELAY-ISSUE-REPORT](../plans/manual-results-2026-06-25-problem-pipeline-prod/RELAY-ISSUE-REPORT.md)→relay 侧已调高。证据 [RESULTS.md](../plans/manual-results-2026-06-25-problem-pipeline-prod/RESULTS.md) |
| 2026-06-24 | **七步问题处理流水线实现 + 真机 windows-mcp E2E 核心 PASS ✅** — 按毛选方法论把"收到问题→处理"落成显式七步（Step1+3 预分析合并/Step2 取证门控/Step4 弹钢琴/Step6 异体自检/Step7 收敛止损，仅 Companion）。**子代理并行实现 8 WI**（WI-0 config+context / WI-1 IntentTriage+编排器 / WI-4 三闸 / WI-3 plan companion / WI-6 agent_loop 接入 / WI-5 main.py 编排）；5 新模块 + 5 改造，flag off 全 BC（kill-switch）。**56 单测全绿** + 关 flag 2300+ pytest 不回归。子代理评估完成度 100%（VERDICT: COMPLETE）。**真机 E2E**（SendInput 真点击+UIA Type 真中文输入+截图+log grep）：TC-1 闲聊短路 0 LLM★/TC-2 取证 glob★/TC-3 抓主要矛盾★/TC-4 异体自检 strict★/TC-6 澄清多轮不断裂/TC-7 合并证明/TC-9 kill-switch 三闸全 None★/TC-10 safe-fail 全 PASS（TC-5 止损 best-effort 单测覆盖、TC-8 code 模式 env-limited）。**真机抓修 BUG**：预分析超时 6s→30s。证据 [RESULTS](../plans/manual-results-2026-06-24-problem-pipeline/RESULTS.md)。 |
| 2026-06-24 | **Code 模式入口暂关闭（聚焦主线程 Companion）+ 问题处理流水线 plan 定稿 EXECUTABLE-AS-IS ✅** — 产品侧关闭 Code 模式入口（`Toolbar.tsx CODE_MODE_ENTRY_ENABLED=false` + `App.tsx` 抑制 `code_mode_suggest`，翻 true 即恢复），`tsc -b` 通过 + **windows-mcp 真机截图确认工具栏 terminal 按钮消失**（证据 `plans/2026-06-24-problem-handling-pipeline-maoxuan/exec/toolbar-crop.png`）；待主线做实后另开 plan 优化 Code 模式重新上线。**问题处理流水线 plan**（毛选方法论锚的显式七步 + 取证门控/异体自检/收敛止损三道闸，只作用 Companion 主线）经 **5 轮子代理对抗迭代**收敛至 EXECUTABLE-AS-IS（codex 撞 Windows ConstrainedLanguage 沙箱墙 err1223 → 回退内置子代理）；尚未实现，待执行：[plan 目录](../plans/2026-06-24-problem-handling-pipeline-maoxuan/)。 |
| 2026-06-24 | **PPT 惊艳生图路径真机 E2E PASS × doubao-seedream-4.0；gpt-image-2 全面下线 ✅** — relay 下线 gpt-image-2（`8cb6b3d9`），图像默认切 `doubao-seedream-4.0`（真链路实测可用，`image_tools.py:44`）；`ppt_pro` 惊艳生图路径端到端跑通真机 PASS（`719a0b49`）；SKILL.md/注释/plan 清理残留 gpt-image-2 引用 + depth 不再作 LLM 参数（`58ee7a08`/`228a3d59`）。 |
| 2026-06-24 | **上下文管理 — 三处盲区修复 (Risk 1/2/3)，常见场景生产可用 ✅** — `ffd4f748` 修三处盲区（调查报告 `63683121`：3 针对性真测 + Risk1/2/3 处置）。结论按报告原口径：**主力（检索增强截断 + agent 回读）真机证明稳、常见场景可上生产**；B2 LLM 压缩 + 95% BLOCK 闸为休眠兜底（不影响安全）。⚠️ 诚实保留两处窄风险：单会话 256+ 次截断后不可重跑 stdout 的 ref 被 LRU 淘汰；BLOCK 闸分母只数 working_messages 漏算 base（小 window+大 schema 模型下可能不响）。 |
| 2026-06-24 | **dev 数据路径修复 — 源码跑时数据落 repo `backend/userdata`(G:)，不再污染 C: %AppData% ✅** — `8ada7269`，对齐 [[reference_dev_userdata_dir_on_g]] 记忆；dev 模式从源码跑时数据目录落仓库内，避免掉 C: 盘满。 |
| 2026-06-23 | **上下文优化收尾 — 1A-4 memory 归并 + HM-1 升 strict + TG-1 真机 PASS ✅** — 承接 [完整未处理清单](../plans/2026-06-22-context-and-agent-optimization/exec/REMAINING-CHECKLIST.md)（经子代理对抗挑战迭代）。**1A-4**：合并工具稳健性 3 记忆为 `feedback_tooling_robustness` + 删 3 已 ship 项目记忆（goal_completion/backend_orphan/self_update），索引→18 条全有效。**HM-1 strict**：真机确认 strict 下纯闲聊"你好呀今天天气"多轮 `stop_reason=end_turn` 不误阻塞 + goal/task 回合放行 → 出厂默认 `verify_gate_mode` shadow→**strict**（完成 decision① 目标，emit_receipts=True 满足 VG-INVARIANT-1，38 测绿），提交 `7fd79c83`。**TG-1 真机 PASS**：goal_mode ON → `goal_task_tools_registered_global count=4` → 设目标后主 agent **真调 goal_task_create ×3 建带依赖 DAG**（任务2 depends_on 任务1、任务3 depends_on 任务2，goal_id 反查解析成功），截图 `testcase/.../TG-1-goal-task-create-dag.png`。**1A-2**（裁 MCP 插件）= 须交互式 `/plugin`/`/mcp` 面板（会话无法驱动）+ 用户选保留项，据实留用户照做。**剩余真测**（更重/relay 依赖）：TG-2（需 App.tsx 开面板+并发权限请求）· code 模式 B2(1B-2~5)/CC-2(plan只读)（companion 压缩 inert 需 code 模式）· OH-2 召回确认（relay 间歇 504 拖慢，pin 机制已验）· OC-1（strip 防御使拒绝分支不可达，20 单测兜底）。 |
| 2026-06-23 | **出厂 feature-flag 不向存量 install 传播 — 缺口修复（非破坏性 key-merge / additive schema-migration）✅** — 承接上一条里程碑遗留②（`seed_user_config_if_missing` 只 ① 全新装整份 seed ② legacy `[llm.local/cloud]` 整份替换，**从不把 bundle 新增 key merge 进已存在的 unified 用户 config**）→ 任何新加到出厂 `config.toml` 的 flag（`curation_nudge`/`auto_learnings`/历史 `facts_extract`…）只对**全新安装**生效，存量 `%APPDATA%\deskpet\config.toml` 永远拿不到 → 功能对存量用户实际是暗的（`resolve_config_path` 优先读 AppData config=旧值）。**修**：`config.py` 新增 `_merge_missing_feature_flags()`（tomlkit 注释保真写回）——启动时把 bundle 默认里**用户缺失的 allow-listed flag key** 补进用户 config，**只补缺失、绝不覆盖**用户已改值/注释；写前 `.pre-migrate-bak` 备份；幂等；tomlkit 缺失/解析失败/写失败全 log+no-op **绝不抛**（不挡 backend 启动）。**allow-list 决策**（哪些自动补 vs 不动）：补=behaviour-flag/tuning 段（`tools.last_mile`/`tools.verifier`/`supervisor`/`companion`/`image`/`memory.v2[.facts/.forget]`/`context.manager`/`context.assembler`/`code_e2e`/`research`）；**不动**=`[llm*]`(endpoint/model/**api_key 绝不写**，运行时走 llm_runtime.json)/`[backend]`/`[billing*]`/`[asr/tts/vad/voice]`/`[memory].db_path`(用户路径)/`[[mcp.servers]]`(array-of-tables 标量 merge 不安全)。**测试**：`test_config_feature_flag_backfill.py` 13 测（缺失 flag 补上+用户自定义值保留+注释存活+整段缺失补+嵌套子表+排除段不碰+api_key 不写+幂等+备份+无 bundle/无 tomlkit 优雅降级+legacy 仍走整份替换不误入 merge），叠 legacy 8 测**共 21 绿**；config 全套 **186 passed**。**真配置验证 PASS**：`scripts/verify_feature_flag_backfill.py` 用**真仓库 config.toml** 当 bundle + 剥掉新 flag 的合成存量 config → 经真 `resolve_config_path()` → `curation_nudge=true`/`auto_learnings=true`/`every_n=2` 补上，`backend.port`/`llm.model`/`facts_extract` 值不变、`Strangler-Fig` 注释存活、二次运行不重写。pyproject + PyInstaller spec 加 `tomlkit` 依赖（pure-Python 自动入包）。**真机 GUI boot 真测 PASS**（[REPORT](../plans/manual-results-2026-06-23-backfill/REPORT.md)）：真 `npx tauri dev` 源码后端 + 隔离 userdata 预置"剥掉 flag 的真仓库 config"当存量 fixture（不设 `DESKPET_CONFIG`，走真 seed/merge 路径），boot 日志三证链 `[backend_launch] Dev python=...`（源码非 frozen，pitfall #8 判据）→ `feature_flag_merge_applied count=3 keys=[curation_nudge/every_n/auto_learnings] backup=...pre-migrate-bak`（迁移在活 backend 跑）→ `oh4_curation_nudge_wired every_n=2 auto_learnings=True`（运行 backend 读迁移后 flag=True 接电 curator）+ 落盘 config 实补 flag 且用户值/中文注释不变。真测踩坑：① leftover 手动 `python main.py` 栈持 cargo build 锁致首启卡死→写 `scripts/cleanup_deskpet_leftovers.ps1` 精准清；② PowerShell `*>>` 缓冲 native 输出→改 cmd `>> log 2>&1` 流式抓 backend 日志。✅ **GUI 真测 FIRE PASS（补跑，用户在场）**：真坐标点击+真中文输入（点「消息」开完整聊天窗→同会话连发 2 条真实用户消息）→ 轮1 agent 真调 `memory_write{"用户叫小王，是个程序员"}`+权限门「允许一次」+桌宠真回「我记住啦」→ 轮2 后日志判定 `oh4_curation_nudge sid=task-task-default-1-1 turn=2`——迁移点亮的 flag 驱动的 curation nudge **在 turn=2 真触发**，存量→迁移→wired→FIRE 全链贯通。真测踩坑：compact 面板无常驻文本框+WebView2 SendInput 焦点不进 DOM→须开完整聊天窗；每点「新话题」=新 session 计数器重置→须同会话连发 2 条；installed shell 启动即弹自更新器（killed 不装，用户安装版未改）。证据 [REPORT](../plans/manual-results-2026-06-23-backfill/REPORT.md)+[EVIDENCE-curation-fire.txt](../plans/manual-results-2026-06-23-backfill/EVIDENCE-curation-fire.txt)。 |
| 2026-06-23 | **WI-OH-4 curation nudge 生产死链修复 — 真因纠正：出厂 flag 漏配（非 facts_store 接线）✅ + #1 真机 boot-log 实证** — 承接 `task_455ba81e`（上一条 P2 记录的「facts_store 在 per-session build_agent register、lifespan 时为 None」机理**已被推翻**）。**真因三证定位**：① 生产日志 `tauri-dev7.log.err` 的 `p4_services_registered`+`memory_tools.bind: facts_store=FactsStore` 证 facts_store 非 None 且模块级 try(`main.py:1202-2366`)完整跑完——`build_agent` 1125 即 `return`，facts 构造(1295)/注册(1871) 全在 1175+ **模块顶层 import 期**跑，**不是 per-session**；② 仓库 + AppData `config.toml [memory.v2]` 段**都漏 `curation_nudge` key** → dataclass 默认 False → lifespan(`main.py:2648`) if 短路 → curator 永不构造（既无 wired 也无 skipped 日志）；③ standalone `load_config` 读仓库 config(临时加过 flag)=True，但运行 backend 经 `resolve_config_path` 优先读 AppData config(无 flag)=False → 诊断「config=True」是读错文件。**修**(`b8d57bf3`)：(a) `config.toml` 出厂点亮 `curation_nudge=true`+`every_n=2`+`auto_learnings=true`；(b) `main.py:2648` 改显式三分支 skip-log(`reason=flag_off/no_facts_store/no_llm_provider`)+facts_store `service_context` 兜底→杜绝静默死链复发；(c) 新增 `test_oh4_curation_wiring.py` 6 测守出厂 flag 点亮(真因回归)+`build_agent`→agent_loop 接电（补 `test_memory_curation.py` 直接构造 curator 测不到的接线链）。**30 测全绿**。**真机 windows-mcp E2E 全 PASS**（真坐标点击+剪贴板中文输入+截图+backend 日志，源码 backend+点亮 config）：#1 启动 `oh4_curation_nudge_wired every_n=2 auto_learnings=True`；#2 sid=default 聊天 → `oh4_curation_nudge turn=2` **且** `turn=4` 周期触发。**⚠️ 真测又抓出第 2/3 处死链（单测全绿生产死，同首 bug 模式，`920941e3` 修）**：**死链 B**=`main.py:_run_chat` 每回合 `build_agent` 重建 `_AgentLoop`，轮次计数器原挂 loop 实例→每回合归零→`every_n=2` 时 count 恒=1→nudge **永不触发**（单测复用单 loop 实例测不到）→计数器移到 `MemoryCurator` 单例 `bump_turn`；**死链 C**=构造 curator 漏传 `allow_learnings`→CC-5 learnings 暗装→传 `config.memory.v2.auto_learnings`。turn=2&4 双触发=计数器跨 4 次 loop 重建累加铁证。新增 `test_counter_persists_across_loop_rebuild` 守 B。证据 [manual-results-2026-06-23/RESULTS.md](../plans/manual-results-2026-06-23/RESULTS.md)。遗留②(出厂 flag 不向存量传播)已由上一行里程碑修复。 |
| 2026-06-23 | **上下文优化 P3（打磨 8 项）— 实现完成 + 子代理评 7/8 PASS（OH-3 caller 为前瞻扩展点）✅；真测随 P2 批量待 relay** — 承接 [plan](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md) §4 P3，全 flag 默认 OFF=字节 BC。**1B-3** 自适应 compact_at_pct（`compact_at_tokens_for(agentic)` 按本 run 工具数微调 clamp[0.6,0.95]）/ **1B-4** 摘要质量回路（困惑词法+L1 任务态快照回灌）/ **1B-5** size-aware microcompact（最近N条+累计字节≤M 防巨型 result 爆窗）/ **OH-3** 写入分级（manager.write(light=)+put_doc_light 原语+flag+测试就绪，caller 接线为前瞻扩展点——当前架构无干净高频低信息写入点，强接对话消息会损召回）/ **CC-5** auto-memory learnings（curation 扩 learning 慢衰减 category+注入）/ **CC-3** /run·/verify 内置 SKILL.md（user-invocable:false 不污染用户）/ **TG-2** 审批 UI 聚合面板（ApprovalCenterPanel 批量批准+gate list_pending 只读 WS，默认 enabled=false）/ **HM-2** 引用既有 skill-executable plan（无代码）。独立评估 7/8 PASS（OH-3 为正确扩展点）。提交 `ef5ff544`→`d5ea0799`。证据 [exec/P3-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P3-RESULTS.md)。windows-mcp 真测随 P2 批量待 relay 恢复。 |
| 2026-06-23 | **上下文优化 P2（4 个真缺口新建）— 实现完成 + 子代理评 100% ✅；windows-mcp 真测 relay 故障受限** — 承接 [plan](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md) §4 P2。**OH-4** 记忆 self-curation nudge（`memory/curation.py MemoryCurator` + agent_loop FinalEvent 后 fire-and-forget 每 8 轮→facts.upsert，flag `curation_nudge` 默认 OFF=BC，12 测）。**CC-2** plan mode 物理只读（`registry.py execute_tool` 按 `permission_category`(`_WRITE_PERMISSION_CATEGORIES`) 拦写类工具非硬编码名 + `plan_confirm_gate` 接入，flag `plan_read_only` 默认 OFF=BC，24 测）。**OC-1** 显式 depth 上界（env `DESKPET_SUBAGENT_DEPTH` + `check_spawn_depth` 5 入口守门，超 max(默认1硬上限3)→`SpawnDepthExceeded`，保留 strip 双保险，flag 默认 OFF=BC，20 测）。**OC-2** 背压累计指标（scheduler peak_concurrent/total_queued/total_rejected+lane_wait 分位 + 前端 SubagentProgressPanel 展示，纯增观测=BC，9 测+vitest20）。独立子代理评 P2=100%，65 单测+tsc0 全绿，4 项 BC 全验。提交 `d59fca6e`(OH-4)→`a19c4522`(OC-2)。**windows-mcp 真测受限**：测试当时 relay(chinzy.com) 持续 HTTP 500 故障，阻断 LLM 链路（deepresearch fan-out/curation 都需 LLM），已 ≥3 次重试；relay 恢复后批量真测：**OC-2 真机 PASS**（面板「峰值2·累计入队6·拒绝0」截图）+ **CC-3 真机 PASS**（knowledge_enabled→skill 12→17）+ OH-2 pin 再确认；**OH-4 真测抓出生产死链**（MemoryCurator 永不构造——facts_store 在 per-session build_agent register、curator 在 lifespan startup 构造时 facts_store=None；12 单测绿但生产死链，正是真测价值）→ spawn `task_455ba81e` 修。其余 flag-gated 项单测充分 + OFF=BC 默认态运行；compaction 项 companion 天然 inert。证据 [exec/P2P3-REALTEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P2P3-REALTEST-RESULTS.md) + [P2-TEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P2-TEST-RESULTS.md) · 手测 [testcase/2026-06-22-context-agent-opt-P2](../testcase/2026-06-22-context-agent-opt-P2/manual-test.md) |
| 2026-06-22 | **上下文优化 P1（点亮护城河+补观测）— 实现完成 + 子代理评 100% + HM-1/OH-2 真机 PASS ✅** — 承接 [plan](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md) §4 P1。**HM-1**（自我纠错全档点亮）：`config.py` tools.verifier `structured_reflection`/`verify_gate_mode`(off→**shadow** 稳妥档)/`emit_receipts` 三者原子翻 True（VG-INVARIANT-1 硬连锁）+ 清 verify_gate stub 注释 + metrics 白名单补 verify_replan_stagnant/verify_exhausted/ephemeral_pass/rescued；**真机 PASS**：`verify_gate_init mode=shadow patterns=9` + `ephemeral_verifier_model model=haiku` + shadow 不误阻塞 companion 闲聊。**OH-2**（偏好半衰期默认开）：`config.py:203 pref_decay` False→True + memory_write 加 `pinned` 参数走对话式 pin（硬前置同批）；**真机 PASS**：说「记住我喜欢 neovim」→ LLM 调 `memory_write{"pinned":true,...}` 实证。**TG-1**（方案A）：新建 `goal_task_create/get` 全局工具 + `build_global_goal_task_tools` + 坎解法(a)反查 `get_active_goal_context` + goal_mode 门控(默认 False=BC) + 清 goal_store 两处过期注释；7 新测+204 回归绿，goal_mode 真机待专项会话补验。**OH-1**=决策 no-op（不提升第五路 lane）。**CC-1**=0（重挂已实现）。独立子代理评 P1=100%。提交 `0de7c2df`(HM-1)→`2e9850bf`(OH-2)。证据 [exec/P1-TEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P1-TEST-RESULTS.md) · 手测 [testcase/2026-06-22-context-agent-opt-P1](../testcase/2026-06-22-context-agent-opt-P1/manual-test.md) |
| 2026-06-22 | **上下文优化 P0（1A 环境瘦身 + 1B-1 token 口径统一 + 1B-2 压缩可观测）— 实现完成 + 子代理评 100% + 1B-1 真机 PASS ✅** — 承接 [plan v1.1 EXECUTABLE-AS-IS](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md)。**1A**：全局+项目 CLAUDE.md 瘦身（-7.6K 字符≈2-2.5K token/会话），细节抽到 `~/.claude/knowledge-base/`（windows-mcp-e2e/codex-usage/context-compact-sop），4 条真测记忆合并；1A-2 裁 MCP 插件留用户照做（交互面板+重启）。**1B-1**（决策③方案B 不挂 flag）：6 处裸估算（main.py 三处 `/3.5`、skill.py 三处 `//4`、metrics.py）统一到 CJK-aware `count_text_tokens` + `test_token_unify_cjk.py` 9 绿；**真机 windows-mcp PASS**：ContextBreakdownModal 实测「CJK-aware tokens」文案 + 中文 history 137k(CJK 量级非 /3.5 低估)。**1B-2**（flag `ctx_observability` 默认 OFF=BC）：`ContextCompactedEvent`+metrics+WS+前端 toast，单测 56/56 绿；**toast UI 触发 env-limited**（5+ workaround，压缩在 companion 单工具回合下 prompt_tokens=17619>>6400 阈值仍不触发，疑触发器时序问题待 P3 复查）。独立子代理评 P0=100%。输入法突破：App switch 消息窗+Type(Unicode) 攻克 WebView2 中文输入。提交 `902104ec`→`3dd44360`。证据 [exec/P0-TEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P0-TEST-RESULTS.md) · 手测 [testcase/2026-06-22-context-agent-opt-P0](../testcase/2026-06-22-context-agent-opt-P0/manual-test.md) |
| 2026-06-22 | **修 VerifyGate ephemeral 模型 shipped bug + 真机验证 ✅** — 承接 [R2 对抗审查](../plans/2026-06-22-context-and-agent-optimization/)。配置项 `[tools.verifier].ephemeral_subagent_model`（`config.py:271`，默认 haiku + 白名单校验 VG-INVARIANT-5）**从未被消费**：自我纠错闭环升级 ephemeral 救援子代理时，`build_agent`（`main.py`）直接用 `local_llm or cloud_llm`，**没读 config**→用户/默认配的专用模型恒被忽略、永远复用主 LLM。修法：新增 `_resolve_ephemeral_provider(base, model_name)` 按配置克隆专用 model 的 `OpenAICompatibleProvider`（中转站按 id 路由，复用 base 连接参数），缺省/同名/克隆失败回退主 LLM（兜底保 BC）；log 改 structlog kwargs 让模型值可观测。新增 `test_ephemeral_subagent_model_wiring.py` 15 测（解析器 4 分支 + build_agent 接电 2 例）全绿 + 回归全绿。**真机 boot-log 档验证**：隔离 dev（Tauri spawn 源码后端 8200，配 `ephemeral_subagent_model="sonnet"` / `verify_gate_mode="shadow"`）→ 真 UI 发消息触发 → backend log 实证 `event='ephemeral_verifier_model' model='sonnet' base='gpt-5.5'`（用配的模型，非复用主 LLM）+ 同回合 `verify_gate_init mode='shadow'`。证据 [manual-results-2026-06-22-ephemeral-model/](../plans/manual-results-2026-06-22-ephemeral-model/RESULTS.md) |
| 2026-06-22 | **PPT Pro — 实施完成 + 真机验收 ✅** — 承接 [plan v1.3 LOCKED](../plans/2026-06-21-ppt-deepresearch-pro/00-PLAN.md)（经 6 轮 codex 对抗收敛）。新工具 `ppt_pro(topic, depth, pages, ...)` 确定性编排 **F1 deepresearch 充分调研 → F2 拟纲（双模式防回退）→ F3 大纲卡确认(可改) → F4 首图实测判定（gpt-image-2 可达→惊艳整页生图 / 不可达→模板兜底）**；双保险防二次烧图、独立 task 秒回、分阶段限时 600s、/停止级联取消。实施完成度 100%（WI-0~10 全实现 + 子代理两轮评估 96%→100%）；单测 221 passed + 冒烟 DECISION:SHIP。**真机 windows-mcp E2E** F1-F4 逻辑链路 PASS（deepresearch 真调研 / 大纲卡真渲染+SendInput 真点击确认 / 图像 403 回退判定正常 / deck 5 页落盘自动打开 WPS）。**发现并修复 2 个真 bug**（单测/冒烟测不出）：① chat 不加载 ppt-generate skill body→LLM 走 ppt_create 绕过 ppt_pro（强化 tool description 路由修复）；② 渲染在 executor 线程 hang（双根因：bundled 模板直传绕过外部 2.8GB 库 vision 选图 PIL 拼图阻塞 + `_render_pro` 传 SlideOutline 给 `ppt_create` 致 parse failed→asdict 转 dict）。**环境受限**：relay gpt-image-2 403 配额墙阻断剩余约 13 用例（待配额恢复补验）。提交 `7cd74739`→`bf971a45`。证据 [manual-results-2026-06-22-ppt-pro/](../plans/manual-results-2026-06-22-ppt-pro/) · 计划 [plans/2026-06-21-ppt-deepresearch-pro/](../plans/2026-06-21-ppt-deepresearch-pro/) · 模块档 [PPT.md](./PPT.md) |
| 2026-06-22 | **任务漂移 v2 — 四阶段（A/B/C/D）全实现 + 核心真机 PASS 收尾 ✅** — 承接 [01-implementation-plan](../plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md)（经 **7 轮 codex 对抗硬化** 定稿 EXECUTABLE-AS-IS）。三层根因全处置：**A** T0-1 deepresearch 原话夺权（层2纵深）真机 TC-A1 相邻领域(钠离子史→18 固态/0 钠离子)+TC-A2 跨域(电池史→Tokio/0 电池) PASS；**B** T1-1 硬会话切分（层1根治，`session/task_scope.py` effective_sid=`task-<base>-<seq>` + `session_switched`/`task_session_started` WS 事件 + voice 全链路 + 前端「新话题」按钮真机渲染）真机 TC-B1 `/new 区块链`→`session_id='task-default-1'` 新干净 scope(区块链 11/0 旧主题) PASS，389 passed+tsc0+vitest2；**C** T1-2 L2 降级 external memory(page-in)+`/continue` 透传 512 passed；**D** T0-3 关 Tier2（8 处 `topic_shift_gate`→false）真机组装 gate `topic_shift_gate=False+shift_path='off'+l2_truncated=False`（Tier2 整链关）+`relabel_applied=True anchor_applied=True`（Tier1 精准保留），473 passed 无破坏。每阶段闭环：codex(gpt-5.5)实现→子代理评估 100%(0 GAP)→windows-mcp 真机。HARD GATE 全验(Dev python+is_mock=False)。网络受限(deepresearch 0 引用不落盘=设计行为)故判定锚点用搜索 query 主题 + 组装期 `task_drift_context_gate` log 而非落盘报告。提交 `07edc0ee`→`f5e878a4`+证据 `ec6a864f`。真机证据 [testcase/2026-06-21-task-drift-v2-phase{A,B,CD}](../testcase/)（各 RESULTS.md + 截图 + log）。flag/sentinel gating，OFF=字节 BC |
| 2026-06-21 | **任务漂移 v2 阶段B — T1-1 硬会话切分 + voice 全链路 ✅ 后端+前端落地（单测/tsc 绿；真机 E2E 待跑）** — 承接 06-20 软修（组装期裁剪，D1 硬会话切分当时「暂缓」），本次真正落地后端 **硬会话切分**：新增 `backend/deskpet/session/task_scope.py`（`base_session_id` = 用户打开聊天窗的主会话；`effective_session_id` = `task-<base>-<seq>` 当前工作子会话；话题跳变 / 用户点「新话题」→ 切到新 effective_sid）+ `main.py` 切分点广播 `session_switched` / `task_session_started` WS 事件（含 old_sid/new_sid/reason）并 fan-out 给 default-chat peers + `agent_loop.py` sentinel 透传 + voice 全链路对齐（`voice_pipeline.py` 走同一 effective_sid，新增 `test_voice_task_scope.py` 193 行）。改 10 文件 +792 行，含 `test_task_scope.py` / `test_main_task_scope_wiring.py` / `test_agent_loop_sentinel.py`。flag/sentinel gating；**前端（响应 session_switched + 新话题按钮）已落地**（`6b927d38`，2026-06-22）：`App.tsx`/`MessagePanelRoot.tsx` 移除硬编码 `default`→`activeSid`；`ws.ts` dispatcher 响应 `session_switched`/`task_session_started`→`ensure(new_sid)`+`set_active`；`InputBar` 新话题按钮发 `{new_session:true}`；消息流/context/send/stop 全用 activeSid（BC：无切换 activeSid=default）。改 5 文件 +237 行；`tsc --noEmit` exit 0 + vitest 2 passed。**⚠️ 前端仅单测/tsc 绿，windows-mcp 真机 E2E 待跑**（无 manual-results 落盘）。提交 `0e6211e8`（后端）+ `6b927d38`（前端）。计划 [plans/2026-06-21-task-drift-fix-v2/](../plans/2026-06-21-task-drift-fix-v2/) |
| 2026-06-21 | **任务漂移 v2 阶段A — T0-1 deepresearch 原话夺权 + T0-2 fanout 隔离断言 ✅ 真机 PASS** — 在 06-20 task-drift 修复基础上加固 deepresearch 主题不漂：T0-1「原话夺权」让本轮用户原话在 deepresearch plan/synth 中夺取主题主导权；T0-2 fanout 子代理隔离断言（子问题间不串味）。真机 windows-mcp E2E **TC-A1 相邻领域**（18 固态电池/0 钠离子）+ **TC-A2 跨域**（Tokio/0 电池）搜索 query 铁证主题不漂 + fanout 6 子代理 + FixB；落盘 ENV-LIMITED（网络 0 引用）；单测 197+ 评估 100%。提交 `07edc0ee` / `15817813`。计划经 codex 7 轮对抗挑战收敛 EXECUTABLE-AS-IS（`b05ab20f`）。测试 [testcase/2026-06-21-task-drift-v2-phaseA](../testcase/2026-06-21-task-drift-v2-phaseA) |
| 2026-06-21 | **deepresearch 子代理 fan-out — Phase 1（WI-8 落盘迁移）✅ 真机 PASS** — 所有 deepresearch 报告从 `<user_data>/OutPut/Research/` 迁到**安装目录 `DeepResearch/`**（dev=repo 根，frozen=安装根 probe，env `DESKPET_DEEPRESEARCH_DIR` 可覆盖，**绝不回落 C 盘 AppData**）+ 维护 `DeepResearch/index.md` 倒序总索引（可点击相对链接/模式列 flat-vs-fanout/utf-8/原子写/asyncio.Lock 幂等）。`paths.deepresearch_dir()` + `_update_deepresearch_index()`。计划经 **5 轮 codex 对抗挑战收敛(v1.0 LOCKED，修 11 BLOCKING+8 MAJOR)**；实现经子代理评估 **100%**；**手测文档设计阶段抓出并修复模式列泄漏档位 bug**(cov["mode"]=深度档→改 subagent_fanout 判定)。TG-6 单测 12 passed + research 83 BC。**windows-mcp 真机 E2E**(干净重启 Dev python backend，relay 已连接)：真发 2 次中文调研→报告落 repo 根 DeepResearch/(非 OutPut/Research/非 AppData)、index 首建表头+第二份倒序在上、模式列 flat、中文不乱码、相对链接、artifact 结果卡片——TC-WI8-01/02/03/05/07/08 PASS(04/06 单测覆盖,09 env 单测覆盖)。证据 [RESULTS.md](../plans/manual-results-2026-06-21-wi8-deepresearch/RESULTS.md)。计划 [plans/2026-06-21-deepresearch-subagent-fanout/](../plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md)(经 5 轮 codex 对抗挑战 v1.0 LOCKED) |
| 2026-06-21 | **deepresearch 子代理 fan-out — Phase 2(WI-1~6/§5 fan-out 核心)✅ 真机 PASS** — plan 拆题后**每子问题派一个 research 子代理跑完整单问题 deepresearch**(复用§6.0 全管线)经 `SubagentScheduler` 有界并发(lane/global cap 背压)→ 主线程**统一 synthesize** 跨子报告出结论。`_run_subagent_fanout`(预算硬裁 cap=min(max_subq,conc*5)/per_subrun=min(150,240/waves) 可证≤300s tool 超时)+引用全局重编号+脚注 strip/rewrite+`_fanout_synthesize`+`_finalize_report_md`抽取(扁平 BC)。**递归守门单一共享常量** `task_kinds._FORBIDDEN_IN_KIND` 加 deepresearch,封 4 路径(kind/agent 显式 tools/agent_parallel-spawn_subagents/teammate)防子代理二次 fan-out。flag `[research].subagent_fanout`(依赖 `features.subagent_driver`)默认 OFF/字节 BC。子代理评 **100%**;单测 **TG-1~5 16 passed + 合并 261 passed(BC+driver+byte-level)0 fail**。**windows-mcp 真机 E2E TC-F1~F7 全 PASS**:真发宽主题→backend log **6×`subagent_scheduled kind=research run_id=default.dr-0..5`** + 前端「子代理并发·运行中5/6」面板**背压可见**(2跑3排队1完成) + index 模式列 **fanout**(6子问题)统一报告全局引用 + 递归守门**无嵌套 run_id** + flag OFF/1子问题→flat。网络受限(google TimeoutError)致内容偏弱属环境,机制全验证。证据 [RESULTS-phase2-fanout.md](../plans/manual-results-2026-06-21-wi8-deepresearch/RESULTS-phase2-fanout.md) · 手测 [testcase](../testcase/2026-06-21-deepresearch-subagent-fanout/manual-test-phase2-fanout.md) |
| 2026-06-21 | **桌宠子代理并发驱动 P0-P4 全实施 + 真桌宠 windows-mcp UI E2E V1-V5 全 PASS ✅** — 承接 [plan v0.3](../plans/2026-06-21-subagent-concurrency-driver/00-PRD.md)（经 **3 轮子代理对抗评审收敛**，R3 逐条核源码判 100% executable + 用户拍板全做）。给桌宠 agent **驱动子代理并发处理多种事务**的能力，复用现有三层基建（agent/agent_parallel/spawn_team）**加性扩展不重写**。**P0** `task_kinds` 事务分型（research/code/doc/web/fileops/general，工具子集已剔 spawn 类保 depth=1）+ `subagent_scheduler` lane-aware 双闸有界并发（global cap + per-kind lane，背压）+ config flag/服务槽；**P1** agent_parallel 路由进分型+调度（schema 加 kind、上限 4→8、新增 `_make_async_native_runner` 无线程池占用 F10）+ WS 进度出口；**P2** spawn_team 暴露为 LLM 工具（同构池）+ TeamStore/TaskGraphStore 接进 main.py + db 清理；**P3** 非阻塞 spawn_subagents/await_subagents + completion queue 回合边界注入（R2-1 取反守门）+ `/stop` 取消级联 + 前端 SubagentProgressPanel；**P4** 子代理质量守门 hook + per-kind 模型路由（新建 provider）。对标 **openhuman/hermes/openclaw 码级（8 模式）**。**全 flag 出厂 OFF（subagent_driver/agent_team/subagent_nonblocking），OFF=字节级 BC**。证据：单测 **320+ 全绿**（P0 23+P1/P2 86+P3 8+P4 5+前端 vitest 5+各阶段 BC 回归）；接线冒烟 `subagent_driver_smoke.py` **DECISION:SHIP**；**boot smoke 真 backend 启动（8399,dev）4 ready 锚点全亮**（`subagent_driver_ready global=4 lanes={research:2,code:2,fileops:3,doc:1,web:3,general:2}` / `agent_parallel_ready scheduler=True` / `agent_team_ready` / `subagent_nonblocking_ready`）+ 0 Traceback + Application startup complete；collect-only 3188 无 error；前端 tsc 0 err。dev config 已开 3 flag。**2026-06-21 真桌宠 windows-mcp 多事务并发 UI E2E V1-V5 全 PASS ✅**（干净会话 + dev 自动登录 + relay gpt-5.5 真多轮）：**V1★** 桌宠输入「同时①调研钠电②查SU7价③列season提纲」→`agent_parallel` 调用、`subagent_scheduled` kind=research/web/general 三 lane、metrics 250ms 内全 running 时间重叠、聚合回一条；**V2★** spawn_team 4 译文→`spawn_team team=… n=4`+team_task_claim×8(池清零)+team_task_update×4 done；**V3** 6 子任务→峰值并发=4(global cap)、2 排队晋升、6 全 completed 不丢、卡片"运行中4/6"实拍；**V4★** flag-off 字节 BC=158 pytest 全绿(test_scheduler_none_is_bc + byte_level_consistency)；**V5★** spawn_subagents(background) 立即返回→停止按钮→`subagent_cancel_all n=3`+running→failed 即时+取消后 0 LLM 出站。附带 E-2 错误隔离真机生效(SU7 web 子代理 failed→主代理兜底补查+聚合)。证据(截图+log/metrics 铁证)[manual-results-2026-06-21-subagent/RESULTS.md](../plans/manual-results-2026-06-21-subagent/RESULTS.md)。**2 个非阻断瑕疵全闭环(2026-06-21 真机复验,commit `0e190ae`)**：① ~~进度卡片仅 Code 模式挂载,桌宠主消息面板未挂~~ **已修**：SubagentProgressPanel 重写为 variant(light/dark)+可折叠(全done自动收起)+运行中实时计时+淡入动画,挂进 `components/MessageStreamPanel`(深色变体),走该窗口 control WS 喂 subagentStore(后端广播所有连接 main.py:1978)——真机发4竞品调研,主消息面板「消息·主线程」实时显「🤖子代理并发·运行中4/4」+running计时33s+queued排队中;② ~~排队中被取消的子代理不发终态致卡片卡 "queued"~~ **已修**:`subagent_scheduler.run()` 顶层 try/except 捕获排队阶段 CancelledError→补发 failed(reason=cancelled)终态+修计数泄漏(`261ba42`);并给运行期取消 inner-except 也补 reason=cancelled(`0e190ae`)→前端 🚫已取消(vs ❌失败)归一,真机停止后4行全🚫无一卡queued;顺带修计时秒/毫秒 elapsed bug。回归 `test_{queued,running}_cancel_*`+`genuine_failure`(后端 9 passed)+前端 store/panel(13)+54后端229前端全绿。commits `17a4b9e`→`933b967`+前端 `fa28fc8`→`261ba42`→`0e190ae`。 |
| 2026-06-21 | **deep-research §6.0 搜索可靠性改造 — 真机 windows-mcp E2E TC-A1~A6+X1 全 PASS ✅** — 承接 [plan](../plans/deepresearch-upgrade/)（codex 并行实现 + Lead 集成，经 5 轮子代理评估）。背景：旧搜索抓取常 0 源（必应/DDG 中国大陆被封/限流，spike 实测 11/13 全 0 源）。**改造**（`242406a`→`b05823b`）：① 默认引擎队列改 `_DEFAULT_ENGINE_QUEUE=("google-cdp",)` —— 去掉 Bing/DDG 默认主力（按用户「不用这两个」），新增 **google-cdp 引擎**（无头浏览器渲染谷歌 SERP 绕封禁）+ `_google_reachable()` 可达门控（有 VPN 才用、不通自动跳过不拖垮）；bing/ddg/baidu/bing-cdp/searxng 仍可显式 opt-in。② 新增**百度百科 / 搜狗百科直连源**（`research_sources.baidu_baike_search`/`sogou_baike_search`，默认开、国内稳定，通用/综述主题主力）+ `_reachable(host)` 可达探测（缓存可注入 clock）+ wikipedia 加可达门控。**真机揪出修 2 严重 bug**（单测/5 轮评审全漏，真机才暴露）：🔴 搜索 0 结果（SERP 被封）时 early-return 直接跳过直连源 → §6.0-A 在最需要时失效（`ab14e04`）；🔴 `search_provider._research_raw` 读 `config.config.raw` 但该单例根本不存在 → 恒 AttributeError 吞 → `[research]` 全部配置开关（search_engines/searxng_url/serp_hardening）静默失效、此前靠代码默认侥幸「看着对」（`b05823b`，改 load_config 健壮兜底 + 回退单测）。**真机 E2E**：TC-A1 单主题 PASS；TC-A2 连续 6+ 研究无一 0 源（对照 spike 病灶根治）；TC-A3 财报→cninfo 20 命中+巨潮 PDF 12 真抓；TC-A4 通用主题（退货率）→搜狗百科兜住非 0；TC-A5★ 百度百科 12+搜狗百科 12 真 API 调用、报告引 3 个 baike.sogou 源独立撑起通用主题、不靠 Bing/DDG；TC-A6 google-cdp 渲染参与（VPN 可达）+ 门控正确；TC-X1 乱码主题不崩/不编造/App 存活。144→147 测试绿。证据 [manual-results-2026-06-20-deepresearch-6.0/](../plans/manual-results-2026-06-20-deepresearch-6.0/) |
| 2026-06-21 | **dev 自动登录 — 本地永不手动登录 relay（仅 DEV，生产死代码）✅** — 用户诉求：本地测试不想每次登录；relay token 设计上会过期（access 1h/refresh 30d），无「永久 token」。方案（`2263ee1`）：`RelayEdition` 启动 `restoreSession` 失败时，若 `import.meta.env.DEV` 且注入了 `VITE_DEV_RELAY_EMAIL/PASSWORD`，则静默 `adapter.login()` 自动重登，登录框永不阻塞。**仅 `import.meta.env.DEV` 生效**；生产 build（DEV=false）整段死代码、凭据不进生产包；凭据走 gitignored `tauri-app/.env.local`（从 `LOCAL-DEV-CREDENTIALS.md`），代码无硬编码。需重启 vite 让 `.env.local` 生效。 |
| 2026-06-20 | **任务漂移修复（桌宠对无关新请求漂回上下文旧主题）— 真机 windows-mcp E2E PASS ✅** — 承接 [HANDOFF](../plans/2026-06-20-task-drift-fix-HANDOFF.md) + [fix-plan §8/§9](../plans/2026-06-20-task-drift-fix/00-fix-plan.md)（三轮深度调研 + 两子代理读码评估 18/18=100%）。**根因**:桌宠聊天单一 `session="default"` 永续会话从不按任务切分，最近 5 条原始历史（627 库 CATL 占压倒）被**零门控**提升成"正在进行的对话线"贴当前 user 前 → 新无关请求被旧主题压垮。**修复（codex gpt-5.5 双 worktree 并行实现 + Lead 集成）**:**Fix A** 组装层 `memory.py`/`bundle.py`/`assembler.py`/`policy.py` — Tier1 当前请求优先锚定（`_CURRENT_REQUEST_NUDGE` 经 `build_messages` 新 `late_system_nudge` 槽位插在 history 后/user 前）+ L2 历史重定性标签（`_L2_CONTEXT_LABEL` 入 l2_history 头部），Tier2 话题跳变语义截断（合取 `低相似∧≥16字∧无指代`+短/代词 anaphora 豁免）;**Fix B** 分发层 `agent_loop.py`/`research_tools.py` — 对声明 `user_request` 字段的工具 dispatch 处**无条件注入本轮原话**（避开 set_session_context 合并顺序陷阱），deepresearch `_PLAN/_SYNTH_PROMPT` 双锚。**真机 3 轮「有问题→修复→复测」迭代根治**（plan 推不出、真机才暴露）:① Tier1 软指令不足以压住对话惯性（anchor/relabel 已注入但 topic 仍漂 CATL）→ 开 Tier2 默认 + `len>50` 阈值漏判 32 字简短新任务改可配 `topic_shift_min_len=16`;② Tier2 embedding 实时 encode 恒 `encode_timeout`（BGE-M3 subprocess 被 vector-worker 回填 627 条+研究负载抢锁，撞 1500ms 组件 budget）→ 加**词法内容词重叠兜底**（embedder 超时→零延迟词法信号，跨域漂移 token 重叠≈0 必被抓）。**windows-mcp 真机硬证据**:**TC-1★** 627 CATL 库发"深度调研 Rust Tokio"→`task_drift_context_gate l2_truncated=True shift_path=lexical l2_count_in=5→out=1`、`p5s2 topic="Rust…async-std/smol/monoio/glommio"`、桌宠真抓 `github.com/smol-rs/smol`、报告落盘（**零漂移**）;**TC-4★** 追问不误伤——"它的竞品"→`l2_truncated=False`(代词豁免)+web_search 宁德/比亚迪、"继续"→`l2_truncated=False`(短路豁免)+run_shell 续 CATL 年报 PDF 分析（**上下文未失忆**）;**TC-8** CATL→asyncio 切换正确截断+连贯答 asyncio。Fix B `task_drift_user_request_injected req_len=32`。新增可观测锚点 `task_drift_context_gate`/`task_drift_user_request_injected`/`task_drift_sim_*`。**回归 679 passed**(新增 fixa 13+fixb 4 单测);证据 [testcase/2026-06-20-task-drift-fix/RESULTS.md](../testcase/2026-06-20-task-drift-fix/RESULTS.md)。**诚实 caveat**:Tier2 embedding 主路真机因锁竞争难稳定触发（恒走 lexical 兜底）,其正确性由 mock-embedder 单测覆盖;D1 硬会话切分暂缓（后端 12+前端 6 处改造面，Fix A 组装期裁剪已软性达成隔离）。 |
| 2026-06-20 | **WI-5 触发式知识注入 — 末环遗留 bug 根治 + 真机复验 PASS ✅** — 承接 [HANDOFF](../plans/2026-06-20-agent-loop-optimization/HANDOFF.md) §3。**根因**:`main.py:1400` 构造 `SkillLoader` 漏传 `knowledge_enabled` → loader 恒用默认 `False` → `reload()`(`loader.py:308`)把 3 个 `user-invocable:false` 知识片段挡在快照外 → `loader.all()` 永远只有 12 个常规技能 → matcher 无从匹配(此前 `d7da6e5`/`011aab5` 修的 assembler/SkillComponent config 流通是在「过滤本就为空的子集」,源头没放行下游再对也没用)。**诊断**:`diag_wi5.py` 直证 loader flag False→total=12 缺知识片段 / True→total=15 且 triggers 正确。**修复**:`_SkillLoader(...)` 加 `knowledge_enabled=bool(config.skills.knowledge_enabled)`(默认 False 保 BC)。**真机复验**(windows-mcp,DEV_MODE,keychain LLM key):boot `skill.reload_ok count=15`(原 12);发 code 句「windows 反斜杠路径报错」→ `skill_auto_disclosed total=15 strong=1 auto_loaded=1 names=['windows-path-debug'] top_sim=0.950`(对照修前同句 `total=12 strong=0 names=[]`),桌宠端到端真响应。**回归**:补 2 条真 SkillLoader-当-registry 测试(走 `loader.all()`,捕获本 wiring bug);`test_wi5_trigger_inject` 8 + WI 套 + assembler + agent_loop BC 全绿(73+34)。证据 [manual-results-2026-06-20-wi5-trigger-fix/RESULTS.md](../plans/manual-results-2026-06-20-wi5-trigger-fix/RESULTS.md) |
| 2026-06-20 | **Agent Loop 优化 7 WI 全实现 + 真机 E2E 全 PASS ✅** — 承接 [plan](../plans/2026-06-20-agent-loop-optimization/00-PLAN.md)(经 5 轮子代理对抗挑战 §13-§17 收敛)。**codex gpt5.5 三批并行实现 + Lead 集成验证**:**WI-1** tool_choice 协议级硬约束(provider 三方法透传 + agent_loop 三路径 tier3 强制 `none` + tier3+ 禁 nudge + verify_exhausted 末轮纯文本收尾 + relay 不支持 none 兜底);**WI-2** 结构化 trace(`IterationTracer` jsonl 每轮 I/O+完整 tool args,flag `[agent].iteration_trace_enabled`);**WI-3** code persona「收尾自查清单」+ ask_clarification 引导;**WI-4** Focus Chain(code 任务每 8 轮且<30 回灌 `[当前任务进度]` todo 快照);**WI-5** 触发式知识注入(`[skills].knowledge_enabled` + 3 知识片段,复用 auto_disclosure body-inline + protected 防压);**WI-6** edit_file fuzzy 降级(whitespace→anchor→did_you_mean + schema fuzzy 参数);**WI-7** ask_clarification 工具(后端独立 control 通道防 chat-cancel 竞态 §13.7 H1 + 前端 ClarificationDialog)。**真机抓修 3 真 bug**(单测/协议层未暴露,真机+全量回归才揪出):WI-2 flag 与 [context.assembler].trace_enabled 命名碰撞→改 iteration_trace_enabled / build_agent 无条件 `cfg.raw` 破坏 _CfgStub 老测试→getattr 安全取 / WI-6 fuzzy 未进 schema 模型不可控→补 schema。**windows-mcp 真机 E2E**:Batch A TC-0~6 PASS(WI-3 code 收尾自查清单真观测 + WI-2 trace jsonl 真生成含完整 args)/ Batch B WI-4 `wi4_todo_sync iter=8 n=8` 真触发 / **Batch C WI-7 完整闭环真点击**(agent 调 ask_clarification→ClarificationDialog 真弹窗→真鼠标点击选项答题→答案经独立 control 通道回灌→挂起 agent task 据答继续不被 cancel)。**子代理逐批评估 100% + 全量终评 7 WI 全 100%**;WI 单测 31/31 + 大面 BC 回归 247 passed。证据 [testcase batch-a/b/c](../testcase/) + [manual-results](../plans/manual-results-2026-06-20-batch-a/RESULTS.md)。**诚实 caveat**:WI-5 知识注入活化受已有 FP-5 auto_disclosure task-类型门控(仅 `task` 类含 skill,chat 不触发),逻辑 6 单测验证、live 接线确认,broadening 到 chat 列 follow-up;默认全 flag off → BC 安全。 |
| 2026-06-20 | **PPT 模板源迁外部大库 + 预览图视觉选模板 — 真机 E2E PASS ✅** — 用户要求删掉 git 跟踪的旧 3 套 bundled 模板,统一改用外部大库 `resources/PPT_Template`(250 套·2.8GB·gitignored)。因模板数以百计无法塞进 LLM schema 按名选,用户提议「让模型看预览图快速筛选」。**实现**(`596efb7`+`130a376`):新模块 `ppt_template_picker.py` — 库结构原语(库根/大类/stem→预览图映射)+ PIL 拼 contact sheet + 复用 `ppt_visual_review.vision_chat`(抽出的共享多模态原语)一次 vision 调用按主题选具体模板,全程优雅降级(无库/无预览/vision 挂 → 随机回退,类空 → None 回落 from-scratch);`ppt_tools` 删 `_TEMPLATES_DIR`/`_list_bundled_templates`,`_resolve_template_for_render` 大类名→预览图选,schema 描述改列大类。**兜底**:挑 3 套通用商务模板(现代商务汇报/水彩工作计划/极简PitchDeck,含预览图~24MB)放回 `ppt_templates/通用商务/`,`template_library_root` = 外部大库优先、缺失回退 bundled(打包 app/新机器不失效)。**测试**:picker+resolve 16 + ppt/visual/template 全套 89 passed;裸进程冒烟(主库+兜底)PASS。**真机 windows-mcp E2E**([report](../plans/manual-results-2026-06-20/REPORT-ppt-template-vision-pick.md)):桌宠输「用高级色风格做新能源电池技术发布会 PPT」→ backend log 铁证 `ppt_create(template="高级色")` → `pick_template: 220→采样90` → **真 vision POST chinzy 200 → `vision chose id=77 → (177).pptx`** → design-pages 填充 + 模板视觉闭环 2 轮修版 → 产物 5 页落盘 → 桌宠回「已用高级色模板生成 5 页」;渲染产物首页/内容页确认 (177) 设计 + 新能源主题内容。这是裸进程冒烟(无 keychain key)无法覆盖的 vision 选图真链路。 |
| 2026-06-19 | **L3 向量记忆召回偶发 TypeError 根因修复(int>dict 假象)✅** — 真机 dev 日志偶发 `memory_manager.l3_failed error="'>' not supported between instances of 'int' and 'dict'"`,被 manager safe-fail 兜住但导致该轮 L3 整层降级(桌宠"想不起"记忆)。**根因**:cbdf855(WI-2 task-scope-context-isolation)给 base `Retriever.recall` + `MemoryManager._safe_l3` 调用加了 `recency_half_life_days` kwarg,但漏给 wrapper `EnhancedRetriever.recall`。真机默认半衰期 7.0 + enhanced 召回器(rerank/chunk/embedder 插件需真 embedder,故 mock 不复现)→ manager 用该 kwarg 调 wrapper → `TypeError: unexpected keyword argument` → manager 的 `except TypeError` 误判为"测试假替身签名"→ 退化重调 `recall(query, {policy dict})` → dict 当 top_k → base `max(dict, 20)` → **int>dict**。**修法**(非吞异常):给 `EnhancedRetriever.recall` 补 `recency_half_life_days` 参数并透传 base + 加防漂移注释;safe-fail 保留作兜底。**红→绿**:新增 2 个回归测试(`test_memory_enhanced_retriever_integration.py`),修复前精确复现真机那条 `l3_failed(int>dict)` 日志,修复后 L3 命中。required 套件 51 passed + manager 16 passed。 |
| 2026-06-16 | **上下文压缩升级对标 Claude Code/Hermes/OpenClaw — Phase 1+2 全做完 ✅ + compaction 默认开启(WI-6)** — 承接 [compaction-bestpractice-upgrade plan](../plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md)(已过 2 轮子代理对抗评审)。**Phase 1**(`70d376d`+`e933e7d`):**WI-1** 触发改剩余 token buffer(`min(threshold, eff_win−output_reserve)`,output_reserve=max(8K,min(32K,window//32)) 随窗口自适应;effective_pct=None 退纯比例保 BC);**WI-2** microcompact(陈旧 tool_result 正文换占位、保壳+tool_call_id 不删整条,降到触发线下跳 haiku);**WI-3** 结构化摘要 7 段 schema + `_extract_prior_summary` 锚定增量(旧[压缩摘要]抽出作 prior-state 不混 transcript,防套娃) + 透传 prior 空中段边界;**WI-4a** 目标 always-on 单点注入(agent_loop 循环前注 role=system,删周期 anchor,compress 去重→[目标锚定]恒≤1;dedup 比"删 compress 注入"更优,3 个 goal_anchor 测试文件零改动 BC)。**Phase 2**(`e1ff9d3`+`12d4554`):**WI-4b** pre-flush 压缩前落任务态进 L1(每 run latch 限频,跨 session 记任务,踩 frozen-snapshot 语义);**WI-5** 收敛(USER.md cap+召回地基已在,确认即可);**WI-6** compaction 默认 False→True。**子代理 2 轮对抗评估 96%→修 2 必修(get_pending_tasks 真实现子目标数据源 + safe-fail 保 microcompact 收益)→100%**;新增单测 21+回归 699-850 passed。**windows-mcp 真机**(注入 DESKPET_BACKEND_DIR 跑当前码,gpt-5.5 窗口压到 8000 逼触发):一次 deepresearch 35 工具调用,`context_microcompact_only`×24(把上下文从19501压住~14-15K,最高频生效层)+`context_compacted`×2(结构化分段)+`wi4b_preflush_l1`×3(限频)+UX banner 104%;**★ case ② 压缩后追问桌宠仍答"调研宁德时代2024年报"(任务连续性根治)**。**诚实 caveat**:WI-4a 实机未验(/goal 未注册 goal,单测已证);haiku 摘要层偶发反射(microcompact 层无)。**关联**：压缩窗口此前按错模型算(P-B)已于同日 [根治](../plans/2026-06-16-effective-llm-model-resolution/00-PLAN.md)(`effective_llm_model` 统一解析有效出站模型，真机 5 TC PASS)。证据 [testcase](../testcase/2026-06-16-compaction-bestpractice-phase1/) |
| 2026-06-16 | **有效出站 LLM 模型解析统一 — 根治 P-B（压缩窗口按错模型算）✅ 真机 windows-mcp 5 TC PASS** — 承接 [effective-llm-model-resolution plan](../plans/2026-06-16-effective-llm-model-resolution/00-PLAN.md)（2 轮对抗评审）。背景：压缩窗口/阈值此前取自 config `[llm] model`（种子 gemma4:e4b→32K default），但实际出站是中转站 gpt-5.5（面板设 1M）→ 按错模型算阈值（32K 而非 1M）过早压缩浪费窗口（2026-06-16 测压缩 §3.5 记录的 P-B）。**修复**（`84e4c251`）：`config.py` 新增 `effective_llm_model(cfg)`（in-process 用，优先 `cfg.llm.local.model` 运行时覆盖、空回落 raw、再回落默认）+ `effective_llm_model_standalone()`（工具用，不依赖 main 单例，治 `_cfg.config` 不存在的老坑）；压缩窗口解析（`main.py:1384`）、stub、breakdown 探针读模型名统一走有效出站模型；压缩窗口不再 hardcode 32000（`f53a63b5`）改按主模型经 model_info 解析。**真机 windows-mcp 5 TC PASS**（`efce0f62`）：有效模型解析根治验证。手测文档 10 TC [testcase](../testcase/)（`aef5101b`）。 |
| 2026-06-16 | **deep-research JS 渲染兜底(Option C / cdp-edge) ✅ 真机 windows-mcp 11/11 TC PASS** — 用户要给抓取链路加"很好的爬虫"治 JS/SPA 空壳站。**调研三方案**(子代理带源):Crawl4AI(底层 Playwright+Chromium ~100MB,且 PyInstaller 冻结包 import 不到=仅 dev)/复用 Tauri 自带 WebView(三端零体积但需跨"后端↔前端↔webview"反向链路+动 Rust)/**CDP-系统Edge**(纯后端 websockets 连系统 Edge 无头,POC 实测 quotes/js 空壳 29→1071 字、豆瓣 44→1244 字)。**用户选 Option C** 分期落地:本期 Windows 用 CDP-Edge 纯后端(小稳可进冻结包),Tauri-WebView 三端统一作下一期([plan](../plans/2026-06-16-crawl4ai-fetch-tier/00-PLAN.md) §3.0,经 3 轮对抗评审揪出 frozen 包不可用/反向链路高复杂度等硬伤)。**实现**(`a520ef7`+`cd15844`,codex gpt-5.5 起草适配器+本人修集成):`research_cdp_edge.py`(常驻单例无头 Edge + CDP `Target.createTarget` 隔离渲染取 outerHTML,Semaphore(2),best-effort 返 None,structlog `cdp_edge_render` 锚点);`default_extract` 接入(本地渲染排 jina 前,双闸 trafilatura<300且原始HTML>20KB、单轮触发≤4 护 300s、命中跳 jina 去重、`extractor` 标记,渲染后 HTML 重扫 ai_generated);`_js_render_*` 走 `_research_raw()`;websockets 入 spec hiddenimports。**本人修 2 真 bug**:Page.navigate 对走系统代理外国慢站卡 5s→改非致命超时也继续轮询兜底;**渲染失败无条件杀常驻浏览器**(单次超时就下个URL冷启10-17s顶穿300s)→改仅连接级致命才 reset。**子代理两轮评估 92%→补 3 缺口→100%**(保活改连接级+ai_generated渲染后扫测+eval超时单测)。**windows-mcp 真机 11/11 PASS 0 bug**([testcase](../testcase/2026-06-16-js-render-cdp-edge/) + [RESULTS](../plans/manual-results-2026-06-16-js-render/RESULTS.md)):TC-01 渲染 4 站(cctv/douban 90K-254K字)进报告引用;TC-06 timeout=2 优雅降级;TC-11 渲染失败后 msedge 进程稳定不被杀(保活真机证);TC-07 jina 同开但渲染命中 URL jina 调用0(去重)。回归 246 passed。Tauri-WebView 三端主线 + crawl4ai dev 档为下一期(plan §10 F2/F3)。 |
| 2026-06-15 | **deep-research Phase-2 — multi-query/HyDE + 中文一手源直连(巨潮/国标) ✅ 真机 UI 测 5/5 PASS** — 用户要一次性做完 Phase-2 三项(均中国可直连):**①multi-query/HyDE**(`02fd984` `_expand_queries` 一次 LLM 产 ≤4 扩展 query:改写+假设答案,提召回)**②巨潮资讯直连**(`research_sources.cninfo_search`:上市公司公告 PDF→pypdf 抽正文)**③国标系统直连**(`openstd_search`:GB/T 标准号+名称+hcno)。意图路由 `direct_source_for`(财报→cninfo/国标→openstd);直连源构造 Passage 跳长度门、高新鲜度,cninfo.com.cn 入 TIER_1。**windows-mcp 真机 UI 测 5/5 PASS**([testcase](../testcase/2026-06-15-deep-search-phase2/) + [RESULTS](../plans/manual-results-2026-06-15-phase2/RESULTS.md)):TC-P2-01 扩展产 13+ query 含 HyDE/英文跨语种;TC-P2-02 报告引用 #1=cninfo 年报 PDF(抽出 199.76 亿利润分配等真数据);TC-P2-03 引用=openstd GB/T 44265-2024(真 hcno hash);TC-P2-04 普通主题无直连;TC-P2-05 `direct_sources=false` 开关 opt-out 生效。**真测发现并修复 5 个跨层 bug**(单测全绿但真机暴露):cninfo 长子问题 searchkey 命中 0→topSearch 公司解析(`d826348`)/openstd 长子问题命中 0→关键词清洗(`eb68d89`)/cninfo 噪声词+openstd hcno死链+name日期误选(`a4b5d52`)/deep 档 180s→300s 超时(`ee38fc4`)/**`[research]` 配置开关全失效**(`2b7baa1`:`_cfg.config.raw` 读法但 config 模块无 config 属性→AttributeError 吞→恒默认,关不掉;同款 bug 在 image_tools/ppt_tools 已 spawn_task 跟踪)。**opus 4.8 子代理独立审计**:5/5 真执行、证据充分(真出站 POST/GET+落盘报告,非脚本回放)、无灌水、红线合规。回归 147 passed。Phase-3(SearXNG+Playwright)待做。 |
| 2026-06-14 | **桌宠渲染/形象线 — 装回 Live2D SDK + Hiyori + 动态形象下拉 + pet-tier1 交互 ✅（真机+preview 双 PASS）** — 桌宠渲染路线在「零版权自研」与「实用优先」之间收敛：**装回 Live2D SDK + Hiyori 免费模型**(`3d07e06`，实用优先、放弃零版权立场)。**pet-tier1 交互**(2026-06-13)：点击星星粒子特效(`efda96e`)+ 努力工作气泡 + tool_use 期间持续 working 状态(`80bb8b5`)+ 拖拽 wobble；**拖拽回正根治**——startDragging 吞 pointerup 致立绘卡倾斜(`373806c`)+ 每帧 reset 参数防 ADD 累积致立绘永远歪(`c29ffef`)+ 禁双击最大化(`9fda72d`)；真机/preview 双 PASS 验收报告(`417c27c`)。**设置面板「桌宠形象」下拉**(2026-06-14)：选择后立即换模型(`93bf616`)→ 改 vite 插件动态扫描 `assets/live2d` 实时列出所有模型(`7d94c11`)+ 下拉文字改黑加粗(`a8f1602`)；新增 Azuki-san/HoshinoAi/estella/Snow Leopard 等 live2d 模型资产(未入 git)。**自研 WebGL2 网格变形引擎(`mesh` backend)** 立为探索方向(`plans/2026-06-13-mesh-engine-s3/00-PRD.md`，B2 自研零版权 + AI 分层资产 + 程序拼层，P0 资产可行性未验，仅 PRD)。 |
| 2026-06-14 | **deep-research 精排(LLM rerank,免下载) + 深搜最佳实践调研 + codex 2轮挑战迭代 ✅** — 用户问"能否用 gpt-4mini 代替本地模型免下载"。**澄清**: 聊天模型能做"重排"不能做"嵌入";bge-m3 嵌入保持本地(全局/每消息用),要加的 reranker 走中转站 gpt-4.1-mini。**LLM 重排落地**(`2a8a4cb`): research_run 召回打分后用廉价模型(gpt-4.1-mini)做 cross-encoder 式精排(候选池[N,24]控token,精排分进relevance维度重算composite仍与域名权威加权),`[research].reranker=llm(默认)/local/off`,coverage.reranker观测;main.py 注入桥(同base+key换model,localhost不注入);本地 bge-reranker 留 Phase-future 可选(资源核算: 0.6B同bge-m3 backbone,int8~0.6GB,纯本地零依赖vs SearXNG需Docker劝退;安装包不增、首启多下~0.6GB)。**深搜最佳实践调研**(`9701200`,子代理11WebSearch+2WebFetch带源): SearXNG自托管聚合+三级抓取(trafilatura/Jina Reader/Playwright)+本地bge-reranker+中文一手源直连API+gap-driven反思loop;路线图+方案选型表+落地优先级落 [plans/2026-06-14-deep-search-best-practices](../plans/2026-06-14-deep-search-best-practices/00-ROADMAP.md);新建 plans/index.md 总索引+README指引。**codex 2轮挑战迭代**(`33b6ee5`+`7b92d8a`): 第1轮抓 log→logger(NameError隐患,我之前live-llm/semantic也中招)/rerank无超时/失败误标coverage/低覆盖no-op/localhost守卫;第2轮确认修复+收紧小集合覆盖率阈值(≤3全量)+健壮loopback(urlparse+ipaddress)。新增rerank全套测试;回归84 passed。**后续补强**(codex 复评+用户驱动): 乱码源剔除(`62c4a8d` is_mojibake 检测三类编码乱码,codex 抓到 [^11] 整段乱码)+ **多引擎兼容性降级队列**(`ccff6b1` 治"唯一引擎 DuckDuckGo 中国大陆常被墙":默认 必应→DDG 队列,百度备选,某引擎被墙/限流自动降级下一个,`[research].search_engines` 可配;region 感知;新增 bing/baidu 解析)。**真机验证**(`72e97f5` 前): 深度调研预制菜→**必应 18 次请求(自动走 cn.bing.com 中国可达)、DDG/百度 0 次(必应成功不降级)**、gpt-4.1-mini 精排 1 次成功、报告落盘——多引擎降级队列实测生效。**P1-3 Jina Reader 二级抓取**(`72e97f5`,治实测"必应优先但报告仅4源":JS渲染站 trafilatura 抽空壳→调 r.jina.ai 跑真浏览器返 Markdown 救回正文,<300字触发,默认开可关,extractor 观测)。**Jina 真机暴露 r.jina.ai 国外需代理(直连 ConnectError),裸中国用户连不上→改 opt-in 默认关+超时8s**(`ae64d7e`)。**P1-2 site: 定向官方域**(`9398505`,Phase-1 收尾):政策/企业/学术子问题额外 site: 定向搜(政策→gov.cn/上市公司→cninfo/学术→arxiv),一手权威源进候选池(命中域名天然 TIER_1);纯 query 零成本可关。**Phase-1 基本完成**(多引擎队列+精排+site定向+源质量过滤);Phase-2 中文一手源 API / Phase-3 SearXNG+Playwright 待做。 |
| 2026-06-14 | **deep-research V8 真机桌宠端到端 PASS + computer_use 误路由根治 ✅** — 干净重启后真机 windows-mcp 发"帮我深度调研2025钠离子电池…带引用出报告"→桌宠**真调 `research_run(depth=deep)`**→V8 全管线跑通(plan→**8 次 DDG 中文搜索**→trafilatura 抽取→**反思迭代 2 轮**→relay 200×5 无 500→cite_check 通过)→**报告落 `OutPut/Research/*.md` 15.2KB**:顶部 V8 观测元数据(17 来源/11 域名/2 轮/velocity/引用自检通过)+ TL;DR/Background/Current/**Open questions·controversies**/What's next 五段 + **17 条真实引用**全是权威中文源(新华网/工信部/中国科大/东吴证券/前瞻/产业蓝皮书)——**中文区域修复完美生效**。**诊断发现**: 之前几次"手动编排 web_search/web_fetch + computer_use 误路由"根因=**旧 default 会话上下文污染**把 LLM 带偏(干净会话即正确)。**computer_use 误路由根治**(`3e2f842`): screen_* 工具此前无条件暴露在 LLM schema(只 handler 运行时返 disabled)→污染上下文一诱导就误调撞墙;改 flag OFF(默认)挂 sentinel requires_env→schemas() 隐藏(LLM 看不到不可能误调),dispatch 仍返 disabled(留测试+纵深),14 passed。deep-research SKILL 加强"必须 research_run 禁手动拼"防漂移。**源质量过滤**(`5c65fed`,codex 评审驱动——codex gpt-5.5 审钠电报告判"真实性5.5/需大改:DDG 中文结果里 sohu/百家号/网易号转帖含 AI 生成内容混进证据池撑关键数字"): 新增 SELF_MEDIA 集降 authority=2.0(低于 unknown,稳被真权威源压)+ is_ai_generated() 检测"包含AI生成"声明直接剔除该源 + synth prompt 官方源优先(政策/标准/企业数据引一手源,只有自媒体支撑须标"据X报道未核实")+ 来源层级标签 + 数据口径必分清(产量/出货/规划/预测/装机不混)。顺手修上线级 bug: prompt `{媒体}` 未转义致每次 synth KeyError 退化(→`{{媒体}}`)。回归 142 passed。 |
| 2026-06-13 | **搜索 + deep-research 升级到 DeepResearch V8 — live smoke 全通过 ✅** — 用户要优化桌宠搜索/调研,参考 `deepresearch-v8.0.skill`,**约束不接任何外部搜索引擎**(付费/免费第三方都不要),只用现有 DuckDuckGo + V8 方法论。**Part1 搜索地基**(`cb8b035`): 新建 `search_provider.py` 合并三处重复 DDG 抓取(research/code/chat),**修中文瘸腿** — `kl` 不再写死 us-en,按 query 语言切区域(CJK→cn-zh);桌宠聊天注册 `web_search` 工具(快速查找,深度调研走 research_run);两处旧抓取改薄封装。**Part2 research_run 升 V8 实质**(`6da212d`): `research_scoring.py` 分层权威 TIER1/2/3(含中文源 cnki/xinhua/36kr/zhihu/csdn + gov/edu/.cn 加成 + wikipedia)+recency×topic_velocity+来源多样性/集中度,替代关键词字面打分;4 维 composite;**反思迭代**(depth=deep gap-analysis→补搜第二轮);**BGE-M3 语义 relevance**(main.py 注入 embedder,mock 降级);**LLM 桥修复**(改 config.llm.local+keychain key,弃 providers[0] 丢 key 隐患;synth 2048→4096);**报告落 OutPut/Research/*.md**+artifact;depth 档 light/standard/deep。**Part3 SKILL v0.2**(`f6a5c5a`): V8 方法论(路由门 简单查找走 web_search、输出分档 brief/full/delta、limitations强制、非显然洞察、红线、coverage观测、报告路径告知用户)。**Live smoke 全通过**: 真 DDG 中文 query→cn-zh 5 条真中文源(含 gov.cn TIER1)/英文→us-en;research_run 真搜真抽(zhihu/toutiao 403 优雅入 errors 不崩)+V8打分+cite_check通过+报告落盘 711B。测试新增 search_provider 12+scoring 16+reflection/semantic 4;research/web/skill 回归 214 passed。计划 [2026-06-13-deep-research-v8/](../plans/2026-06-13-deep-research-v8/00-PLAN.md) |
| 2026-06-13 | **Word/Excel 升级到「复杂文档」档 — 真机 WPS 验收双 PASS ✅** — 用户问"能生成复杂 Word/Excel 吗",评估发现原仅中等复杂度(Word 只有标题/段落/表格/分页;Excel 只有公式/图表/条件格式/表头),拍板"都做"。**Excel**(`excel_tools.py`): number_formats(货币/百分比/千分位/日期)、merge_cells、cell_styles(底色/字色/边框/对齐/换行)、charts 多图表列表、images 嵌图。**Word**(`doc_tools.py`): list(项目符号/编号/缩进)、段内混排 runs、字体颜色/下划线、表头底色 `<w:shd>`、image 插图、页眉/页脚/真页码字段(PAGE field)。**路径**(`office_paths.py`): resolve_for_write 加 default_kind → 无路径时落 `<user_data>/OutPut/{Doc,Excel}`(对齐 PPT 体验),paths 不可用回退 temp;schema+两 SKILL.md 同步新字段+复杂示例+必报完整路径。**测试**: 新增 8 Word+7 Excel 用例全绿,现有 49 BC 不破,artifact/pdf/paths 回归 35 passed。**真机 WPS 验收**(`336350d`,生产函数直出复杂样张肉眼确认): Excel 合并标题/千分位/百分比/橙底合计行公式/嵌图/柱状+折线双图表全渲染;Word 页眉页脚真页码 1/2/嵌图/蓝色大标题/段内混排(蓝粗+红粗同段)/编号+彩色项目符号/粗体下划线/深蓝底白字表头/分页符全渲染。 |
| 2026-06-13 | **设置卡改造: 模型上下文窗口(下拉全量目录+窗口只读+压缩阈值可调+K/M单位) ✅** — 用户三连问驱动: ①"压缩线在哪设" ②"为什么就这几个模型" ③"生效窗口文案改上下文总长度+单位 K"。**①窗口只读+阈值可调**(`958c204`): context_window 改只读展示(模型属性,危险手输移除),仅压缩触发阈值 compact_at_pct 可调(0.50–0.95),写回 model_overrides.toml;顺带删掉自己 6-12 埋的重复 model_context_set handler(劫持了 p4_ipc 全量版导致设置卡保存坏了2天)。**②下拉接中转站全量**(`c7b7920`): 原下拉只列 BUILTIN 画像表6个;改为挂载经 code_models_list 拉中转站 live /models 全量目录(旁路订阅),下拉=目录全量∪builtin∪当前选中,选非builtin模型即时 resolve 显真实生效画像。**③文案+单位**(`e02afaf`): "生效窗口"→"上下文总长度",fmtTokens(n) 把 token 数转 K/M(32000→32K,400000→400K,1000000→1M,25600→25.6K)。tsc 0err+卡片 vitest 8 passed;真机 webview 重载后下拉列出~20中转站模型、卡片显示"上下文总长度:400K/compaction 80%(≈320K)"。 |
| 2026-06-13 | **上下文治理: 大工具结果外置(治本) + 消息面板可观测性 ✅** — 压缩机制评估(优:三明治保头尾/语义摘要/tool配对安全/goal锚定/阈值随模型;缺:摘要不可逆有幻觉风险/一刀切98%/同步阻塞/治标不治本/头尾按条数)→ 用户拍板先做方向1外置。**①外置落地**(`1228ed8`): 勘探发现截断+ref store+fetch_tool_result 机制已存在且 companion 可用,真缺口=留存额度过宽(window//25 → 400K 给 16K/条,真机调研一轮 4×web_fetch=64K 字符两三轮即压缩)+ref 纯内存重启失效。收紧 threshold=max(6K,min(12K,window//60))+head/tail 6K/2K→2.5K/0.8K(单条留存降 2/3);ref store 加磁盘 spill(<user_data>/cache/tool_refs/,LRU 淘汰/重启后仍可 fetch 取回,容量 400 文件自清,pytest 内禁用防污染)。广域回归 330 passed。**②工具轨迹进大消息面板**(`749d465`): 用户问"小气泡有工具过程大面板没有"——断点在面板派生层主动滤掉 tool 消息(广播/store 都通);修派生+ChatRow 渲染紧凑工具行(🔧调用/✅完成,灰底等宽)。**③「隐藏工具消息」开关**(`bb3320d`): header 🔧 按钮 toggle,localStorage 持久化。**④正文槽垂直溢出根治**(`2e42e48`): WPS 不执行 normAutofit 声明 → _fit_text_to_shape 填充后按槽高主动缩字号(行数×字号×1.36 估算,0.5 下限);本地复现用户三页溢出案例全修。 |
| 2026-06-12 | **PPT 视觉评估闭环真机 PASS ✅ — 桌宠真「亲眼看」每页→评审→自动修→复审 clean(用户最初设想落地)** — 用户两问题驱动: ①模板/图文 deck 没有基于视觉的逐页评估修正 ②image-2 排版单一只有左右版。**②版式多样化**(`d239b5d`,调研 Gamma/Deckary/Slidesgo 落地): 6 种版式 cover/split_left/split_right/top/card/quote,`_assign_image_layouts` 自动轮换(封面cover/结尾quote/内容页轮换+split左右交替),按版式给图 prompt 自动追加负空间指令+禁字后缀,`_set_fill_alpha` 注入 `<a:alpha>` 真半透明遮罩。**比例拉伸根治**(`36b3a11`): `_place_cover` object-fit cover 按槽位比例中心裁切(picture.crop_*)零变形 + 按版式生成贴比例尺寸(split→1024 方图)。**①视觉评估闭环**(`c12048f`): 新模块 `ppt_visual_review.py` — 每页渲染图 768宽 base64 → 多模态 gpt-5.5 chat/completions 结构化质检(溢出/截断/压主体/对比度/版式匹配) → `_apply_review_actions`(change_variant/shrink_text,font_scale 0.72 下限) → `_render_fromscratch` 重渲染,最多 2 轮;vision 失败静默降级零影响;仅 AI 图文 deck 走;`[ppt].visual_review` 可关。**真机端到端铁证**: 星空观测 2 页 deck → 2 张生图 200 → **vision 真通**: 第1轮 `issues=1` 发现「要点折行孤字"门"」→ shrink_text → 重渲染 → 第2轮复审 `issues=0` clean;review1/review2 截图对比孤字确实消失。**闭环扩展到模板路径**(`6cd6020`,用户抓到覆盖缺口——模板12页deck有格式问题但闭环只接了AI deck): review_slides 加 mode=template(动作集 shrink_text/change_page),`_render_with_design_pages` 加 banned_pages+page_map,change_page=ban该页设计页换页重填;真机验证 vision 发现「数字重叠/对比度/孤字」与用户报告完全一致,2轮 change_page×5+shrink_text×1。**装饰数字重叠根治**(`3cd4580`): 高级感模板族 1/2/3/ONE 大编号是衬英文短词的背景装饰,中文长句必撞且原清扫豁免了它们 → decor_nums 单列 + `_rects_overlap` 与已填槽重叠即清(不重叠保留);本地复现验证目录页重叠 ONE 精确清除。测试 78 passed(闭环 13)。 |
| 2026-06-11 | **图文 PPT 全链路真机 PASS ✅ — 桌宠自主「AI 配图惊艳 PPT」端到端闭环** — 真机 windows-mcp 发「带AI配图的精美PPT·深海探秘·4页」→ LLM 自主写 outline(image_full+image_prompt,按 schema 引导:封面 title+caption/内容页 title+4条精炼要点) → **异步秒回**(`bf15cc7`,agent 13s 结束回合不卡) → 后台串行 **4 张 gpt-image-2 真出图全 200**(60s必挂已根治:300s+绕代理) → 16:9 裁切 → image_full 渲染(封面底部暗带/内容页**左侧自控深色面板**`737a742`,长中文要点舒展不挤) → deck 落盘 7MB → A-5 预览 4 张 PNG → **notifier 推回桌宠「✨图文PPT做好啦!已自动打开~(4页)」+ 自动打开**。预览实看: 深海潜艇电影感封面+热泉生态内容页,惊艳+内容丰富双达成。配套: 内容丰富 combo(`2ff7abc` 模板多段文字+AI图换图位+清英文穿帮)、模板槽 autofit 兜底。`-k ppt` 65 passed。计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-11 | **PPT 视觉闭环 — 渲染眼睛 + 三轮看图迭代收敛 + A-5 预览进聊天 ✅** — 用户拍板「deskpet 要自己看每页效果反复调到完美」。**① 建渲染眼睛**：WPS COM(`Kwpp.Application`)实测可渲染 pptx→PNG(MS PPT COM 本机不可用)。**② 三轮看图驱动修复**(`aafd898`)：轮1 看出封面标题极淡(继承背景装饰字浅色)+162pt×10 字撑爆孤行+section/two_column 标题替换进页缘出血形状被裁切 → `_shape_mostly_in_canvas` 出血排除 + `_fit_font_pt` 字号自适应 + `_ensure_readable_text` 过淡标题加深；轮2 「核心能力」仍折行孤字 → 单行标题必不折行解析解；轮3 公式漏内边距退化 → 修正(可用宽=框宽−0.28in)×0.92。终态 4 页全达标(封面单行深色大字/标题完整/编号工整)。**③ A-5 预览闭环产品化**(`35daf51`,codex 半成品用户叫停后 Claude 亲手接手完成)：新模块 `ppt_render.py`(COM 渲染,探测缓存/env 开关/从不抛) + `ppt_create` 三引擎成功路径自动渲染每页 → `kind=image「预览 第N页」` artifacts 进聊天,用户/agent 直接看到每页效果;config `[ppt].preview_render` 可关;接手修 codex 的 globals() hack + config mock 错层假绿。测试 `-k ppt` 59 passed;真渲染冒烟 4 张;端到端真模板 → file+2 image 预览 PASS。**下一步立项**：agent 多模态看预览图自主评审重生成(美学级闭环,需多模态管线)。 |
| 2026-06-11 | **gpt-image-2 出图「60 秒必挂」根治 — 双 60s 杀手定位 + 真链路 2 连 LIVE PASS ✅（B 路径出图阻塞解除）** — 中转站侧 Caddy×RequestLog 证据链：服务端 28/30 成功、耗时 57~221s，桌宠 ~60s 自己掐线（且服务端断连后照样生成+按次扣 $0.15，钱花了图没人收）。本地真链路复测进一步挖出**第二个 60s 杀手**：httpx 默认 `trust_env=True` 跟随 Clash 代理（127.0.0.1:7897），出图期间连接零字节流动被代理 ~60s 掐空闲（read=300s 修完仍 ~64s `RemoteProtocolError`，与 Caddy「客户端 59.8s 断开」互为两面）→ **2026-06-09「relay 挂起不返回」系误诊**，B 路径"真出图卡外部 relay"结论修正。修复（`image_tools.py`）：①read 超时 70s→300s 与服务端路由 timeoutMs=300000 对齐 ②默认 `trust_env=False` 直连国内中转站（`[image].trust_env_proxy` 可改回）③读超时/504 不再重试防双倍扣费（连接断/SSL/502/503 真瞬时保留重试）④payload 加 `quality`（默认 medium 降耗时，`[image].quality` 可覆盖）⑤key 缺失裸 401 落警告日志（对应中转站 6-9 那次 401，脱 Tauri 环境跑即复现）。**真链路验收**：真 keychain key + 真 `_generate_png` 两连 PASS — 79.9s/1.3MB（[live 证据图](../plans/2026-06-11-image-timeout-fix-live.png)）+ **184.1s/2.2MB**（同时穿越旧 70s 超时线和代理 60s 掐线，旧代码必挂三连）。新 TG `test_image_timeout_300s.py` 11 条锁死语义，图像全套 41 passed |
| 2026-06-11 | **PPT A-4 设计页复用模式 — 模板真实视觉进成品（用户实看驱动两轮修复）✅** — 用户实测反馈 A-2 layout 填充出的是白板：国内「高级感」模板的设计全画在示例页、layout 是 bare 标准布局。A-4 改为商业 AI-PPT 标准做法**「保留设计页 + 内容替换进文字槽」**(`9681930`)：`_analyze_design_page` 按字号/长度分类 title/body 槽/label；`_set_text_keep_style` lxml 层换字保留字体字号颜色(多行 deepcopy `<a:p>`)；`_render_with_design_pages` 页分类→贪心选页→填充→未用槽清空→按 outline 重排→未用页 drop_rel 删除；接线 design 优先→<3 页回退 layout 填充→from-scratch。**WPS 实看冒烟产物抓出两个穿帮再修**(`8d38e9f`)：①封面双层艺术叠排(162pt 背景字+141pt 手写体)中文化后重叠 → subtitle 限 ≤60pt、>60pt 非标题大字归 decor ②内页残留 About Me 等英文主题词 → decor 填充后清文本(纯数字/≤2 字符步骤编号保留)；subtitle 无槽可放时从 decor 挑与标题垂直不重叠(<50%)的形状放(`_take_subtitle_from_decor`) — section 副标题不丢、封面不叠字。测试 `-k ppt` 55 passed；真实模板(高级感01)冒烟 4 页全替换+LOREM 清零+装饰编号保留 PASS；WPS 实看封面干净 ✅。用户 29 个模板勘探 27 个可用(`resources/PPT_Template/`,不入 git)。计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-11 | **goal-completion 测试线 4 待修 bug 修掉 2 个 + 2 复验全闭环（真机 windows-mcp 全 PASS）✅** — 承接 [HANDOFF-2026-06-11](../plans/2026-06-11-HANDOFF-goal-completion-testing.md)(40/40 TC+opus 二轮审计「接受归档」)。**① bug#1 TC-5.1 skill 自动披露零匹配**(`3526ac1`+`b439bbc`)：三层根因(SkillComponent 走 select() 把 task_types=[] 的 builtin 全滤掉→total=1 / lifespan sync build 调 async encode 静默 no-op / top_sim log 打 strong[0] 掩盖真分)→ auto ON 用全集 + `build_async` 预热(boot 锚点 `fp5_skill_matcher_prewarmed cached=12`) + **混合匹配**(SkillMeta.triggers 词法路+when_to_use 进 embedding,BGE-M3 短中文 query 区分度不够 8-query 校准实证;12 builtin 补全)。真机:`skill_auto_disclosed total=12 strong=1 auto_loaded=1 names=['deep-research'] top_sim=0.950` + **TC-5.7 连带解锁**(同 turn skill_invoke→compaction fired→`skill_remounted names=['recall-yesterday']`)。**② TC-4.5 复验+二阶修复**(`ac76d48`)：复验发现 upsert_replacing 只 supersede 最新一条→历史脏堆积永不自愈→改 supersede 全部 active 同 key;真机 `/goal`×2 → 15 条脏行一次自愈到 active=1+链 57→83→84。**③ bug#2 候选卡/inflight 吞消息**(`c9b31f9`+`fb25ac2`+`fc1e37c`)：真因三层——InputBar+**SessionGridView tile**(实际复现入口)两处 `Enter/按钮 inflight→stop()` 打好的字静默丢弃还打断 turn / 后端 codify 300s Future-await 内联 chat task 被同 sid 抢占连带杀 Future / **ws.ts send() socket 非 OPEN 直接丢消息**(UI 显气泡 backend 收不到=完整吞症状)→ 有文字永远发送(后端同 sid 抢占语义)+confirm 拆独立后台 task+ws outbox 队列重连 flush。真机:inflight 中发消息立即入库处理、卡 pending 中"日本的首都"秒答、点忽略 `confirm_received cid=23 reject`、超时路径 cid=21 整 300s 自动 reject(历经多 turn+页面 reload 仍存活)。pytest skill/codify/facts 全绿+tsc 0err+vitest 120。**④ bug#3 ArtifactCard**(`274eafe`)：真机重跑 ppt_create 逐层追,**查实误诊** — 卡片全链路正常渲染(envelope→WS→store→卡含打开/在文件夹中显示,截图 bug3-ppt-artifactcard-renders.png);原观察假象=tile 预览设计上滤 tool 气泡+Virtuoso 动态撑高错位。真子问题已修:emit_receipt 从不传 artifact_shas→receipt.artifacts 恒[]→现抄产物 sha256(verify gate 可对账)。**⑤ bug#4 强杀丢登录**(`7b9e11f`)：原假设(退出钩子落盘)推翻 — 真因 refreshSession 对任何 !res.ok 都 localLogout 擦 keychain,relay 504 撞上 boot 刷新即被擦;修复仅 400/401/403 才清 session,5xx/408/429 保留 token 重试(auth 32 passed)。**4 待修 bug 全部结案**(2 真修+1 误诊+1 真修),强杀重启×4 登录保留+gpt-5.5 链路回归 PASS。证据 [manual-results-2026-06-10-FP345/RESULTS.md](../plans/manual-results-2026-06-10-FP345/RESULTS.md) |
| 2026-06-11 | **PPT 精美主线 A 路径(模板填充)真机 E2E 全链路 PASS ✅** — 用户拍板「干净专业就够了」。A-2 模板填充机制(`4265c27`)+A-3 选择/发现wiring(`a515d8f`,bundled目录+按名解析+动态schema让LLM发现)+**中文布局别名**(`67b29d1`,国内「高级感」模板布局名多中文'标题幻灯片'/'两栏内容',补中英别名正确映射)+**drop_rel删示例页修复**(`1c430ee`,真机发现删示例页只remove(sldId)留底层部件→Duplicate name→WPS串显旧示例页'graphic designer';drop_rel根治)+**3个干净bundled模板**(`f590b1a`,jianyue-business/gaoji-minimal/gaoji-clean)+**env默认模板**(`78c0f06`,DESKPET_PPT_DEFAULT_TEMPLATE默认套模板不依赖LLM每次传)。**真机E2E**:桌宠输「做个AI发展的PPT要专业」→LLM调ppt_create(5页专业大纲)→自动套jianyue-business模板(日志确认布局名='标题幻灯片'等模板中文布局)→生成干净专业deck(封面+绿点装饰+内容填充正确)→桌宠回「做好啦~专业风格《人工智能发展概览》共5页」→WPS打开正常。勘探:用户29模板27个正规可填充;bare layout观感干净专业(炫酷设计在示例页,用户接受clean)。-k ppt 48 passed。计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-09 | **PPT 精美主线 B 路径 + 本地链接修复（代码+单测完成；B 真机 E2E 待跑·relay flaky 实锤）🟡** — 承接 [HANDOFF-2026-06-09](../plans/HANDOFF-2026-06-09.md)，用户拍板「A+B 都做」。**① 本地文件链接修复**(`cb370c6`)：LLM 写的 `[打开 PPT](C:\...\xxx.pptx)` 在 webview 打不开 → `MessageBubble.tsx` 新增 `isLocalFilePath`/`toLocalPath` 判定(盘符/UNC/file://‎/POSIX)→ 本地文件走 `invoke(artifact_open)` 系统应用打开，http(s) 保持外开;tsc 0err。**② B-1 图像 relay 健壮性 + 同步批量原语**(`c7a52d4`)：`image_tools.py` 漏接的裸 `ssl.SSLError`(UNEXPECTED_EOF) 显式归类瞬时可重试 + retry 2→4 指数退避(3/8/20) + `_save_image` 加 `time_ns()` 防批量同秒撞名 + 新增同步 `generate_images(prompts)` 原语(async worker 不返路径,PPT 拼图需确定性同步)。**③ B-2 整页生图模式**(`27c5558`)：`ppt_tools.py` 加 `image_prompt` 字段 + `ppt_create` 渲染前 `_autofill_image_prompts` 调 B-1 批量生图回填 `image_path` + 新增 `image_full` 全幅铺图布局(底部深色标题带) + `_PPT_SCHEMA` 加字段让 LLM 知道可用;dry_run/无 prompt 零网络调用、生图失败优雅降级。单测全绿(B-1 22 passed / B-2 `-k ppt` 39 passed)。**④ 真机验 B 受阻于 relay flaky**：起 app(backend 8100 + 真 BGE-M3 + relay /v1/models 200)，windows-mcp 真发「生成一张图」→ 消息收到+LLM 200，但 **relay 对 capability_gate 那次调用 504 Gateway Timeout** → 没暴露 generate_image 工具 → 只纯聊天(`tool_calls=0`)。**这是交接「relay 不稳」的实锤**(504 间歇，且整条链路都受波及，非仅图像)。relay 真出图待用户重发重试验证。**⑤ A 路径勘探**：参考 deck `.tmp/ai-education-deck.pptx`(pptxgenjs 好看版) **不能当模板**(仅 1 空 master + 1 个零占位符 'DEFAULT' 布局，全自由浮动图形)→ A-1 需真·PowerPoint 授权的 .pptx 模板(程序化难产出好模板)，待用户提供模板资产。**⑥ A-2 模板填充机制**(`4265c27`)：`ppt_create(template=.pptx)` 载模板用其 layout 加页填占位符(TITLE/BODY/PICTURE 等)、格式继承、无效优雅回退；5 测+`-k ppt` 44 绿。**⑦ A-3 模板选择 wiring**(`a515d8f`)：bundled `ppt_templates/` 目录 + 按名解析 + 动态 schema 让 LLM 发现可用模板；`-k ppt` 48 绿。**用户只需丢正规 .pptx 模板进 `backend/deskpet/tools/ppt_templates/` 即自动可用。⑧ B 双 bug 修复**(`93a695a`)：B-1 receipt 序列化炸(注入 `_image_worker` 进 args)→剔除 `_` 前缀键+default=str 兜底；B-2 工具路由(「生成图片」误走 web_fetch 网搜→images 接口 0 调用)→强化 generate_image/web_fetch 描述区分度(路由真验待 live test)；receipt/registr 141 绿。**两个 codex 并行实现(文件不重叠)。⑨ B 真机验证(windows-mcp)**：重启加载新代码后真发「生成图片」→ 日志 `name='generate_image' prompt='橘猫...'` **两次都正确调 AI 生图工具不再 web_fetch → 路由修复实测确认 ✅**。但**真出不了图**：根因 = **外部 relay `chinzy.com/v1/images/generations`(gpt-image-2) 挂起不返回**(sync 回退 4m39s 空档零 httpx 响应=请求挂起;async worker 同样卡这)。**deskpet 图像链路本身无 bug**(路由修好/worker 正常/sync 回退正常;之前怀疑的"worker not alive"是 sync 模式按设计不启动 worker,非 bug)。⑩ **快速失败调参**(`381fa82`)：relay 挂起时原 4×100s≈7min 才报失败 → 改 70s×2≈145s 快速告知(保留 ssl 瞬时重试)。**B 结论：deskpet 侧已尽;真出图卡在外部 relay 出图接口(需用户换可用图像 provider/model)。** 计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-07 | **两份手测文档全量执行 — 47/47 TC 达终态（41 PASS + 6 BLOCKED-带原因）✅** — 按 `/goal "跑完真机UI测试"` 建 PROGRESS.md 续跑机制(每条立即更新,后续 agent 可无缝接)。A组(goal-completion 22)=17 PASS+5 BLOCKED;B组(跨层bug回归 25)=24 PASS+1 BLOCKED。真机 windows-mcp 截图 PASS 11 条。**独立审计子代理终审🟢诚实可信(41 PASS 实地核验无虚标)**。6 BLOCKED 均带具体原因(LLM诚实性/verify-gate范围/压缩不触发/已知前端bug),**无新功能代码bug**。按审计补做 TC-5.2/5.7 压缩 workaround(单轮52k字)→压缩仍不fire→**新发现 FP-5 WI-4.0 压缩疑生产从不触发(潜在死链,已建调查 task_ae1af91b)** + 候选卡chat渲染bug(task_01be24af)。证据 [PROGRESS.md](../plans/manual-results-2026-06-07-full-run/PROGRESS.md) |
| 2026-06-07 | **goal-completion 真机 UI 测试 — 全 5 FP 共 9 个 TC windows-mcp PASS ✅（含 2 招牌）** — /goal "跑完真机UI测试" 续跑：HEAD(全9修复)重启，写权限门用 `permissions_auto_mode.json={enabled:true}` 放行(等价用户「本会话始终允许」)。**新增真机 PASS**：**TC-3.2 真完成放行**(agent 真写 hello-fp3.txt 16B+receipt→verify_gate 跑→无 nudge 放行,不误杀真完成)、**TC-4.1 重启跨会话召回★**(陈述"数据库用PostgreSQL/JSONB/并发"→抽取 facts(decision)→**taskkill重启清内存**→重启后准确召回 PostgreSQL+JSONB+并发,逐点一致)、**TC-4.3 偏好冲突**(乌龙→绿茶 supersede→推荐反映最新绿茶)。累计 9 真机 PASS 覆盖全 5 FP(5.3招牌/5.1/5.8/5.6/4.5/4.2/4.1★/4.3/3.2)。**抓出测试法限制(非bug)**：/goal 粘贴(Ctrl+V)不触发前端 slash 自动补全→走普通chat当任务;后端 slash 解析正常(键入触发)。剩余受限:TC-3.1(gpt-5.5太诚实拒绝伪造声明)、3.3/3.4(需slash键入法+诱导)、5.2/5.4/5.7(压缩多轮+relay间歇ReadError)。截图存 manual-results-2026-06-06-FP345/screenshots/。证据 [RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md) |
| 2026-06-06 | **goal-completion FP-5 自动披露 + FP-4 偏好注入 — 真机 PASS ✅ + 抓修 7 处系统性 fanout-gating 生产 bug**（续跑会话）— 真机跑 TC-5.1 强匹配载入 / TC-5.8 复用自创技能时，逐层定位到 FP-5 auto-disclosure（WI-4.1/4.2）虽"接线 ready"却在生产 code 会话**完全不生效**，连环抓修 **6 层跨层契约漂移**（单测全用 sync mock + 带 skills 的 config，全绿掩盖生产死链）：①SkillComponent 无观测日志 ②`assemble()` 传的 config dict 漏 skills 段→auto_enabled 恒 False ③`code` policy prefer 漏 skill→组件永不 fan-out ④code 会话靠文本分类(→chat 无 skill)→加 task_type_override="code" ⑤codify 生成的 SKILL.md 漏 task_types→select() 永远过滤掉自创技能 ⑥**SkillMatcher 同步调 async embedder.encode→拿到未await coroutine→缓存恒空→top_sim=0.0 永远零匹配**。修复后真机硬证据：`skill_auto_disclosed total=2 strong=2 auto_loaded=2 names=['meeting-minutes-to-ppt']`（自创技能正文真机自动预载进 prompt，TC-5.1/5.8 PASS）。**同根第 7 处**：`preference_profile` 组件也不在任何 policy→FP-4 偏好/画像注入(WI-3.2)生产全局死→补 chat/recall/task/code/plan/emotion policy，真机 `preference_profile_injected facts=2 task_type=code` 验证注入恢复。+2 async-embedder 回归测试守护生产契约。304+112 焦点测试绿。这是 `feedback_cross_layer_contract` 最深演绎：组件注册+flag开+单测绿，但 policy fanout 层逐个断。证据 [manual-results-2026-06-06-FP345/RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md)。**待**：TC-3.1~3.4(verify gate)/TC-4.1/4.3(跨会话召回·偏好冲突)/TC-5.2/5.4/5.7(压缩追目标/拒绝/重挂) 续跑(需 fresh context + 部分需 companion-chat venue) |
| 2026-06-06 | **goal-completion FP-5 技能自创招牌全链 — 真机 windows-mcp FULL PASS ✅ + 抓修 5 类生产 bug** — 真机模拟人工跑通完整闭环：多步任务→agent 完成全部工具→codify→propose→**前端绿色「✨新技能」卡真机渲染**→**真坐标 SendInput 点击「保存技能」**→后端 `skill_candidate_resolved cid decision=accept`→**SKILL.md 真落盘**(`<user_data>/skills/user/meeting-minutes-to-ppt/SKILL.md`,frontmatter `requires_script:false` 声明式不执行代码+author:self-codified+6步骤)。**veto-1 完全满足**(真截图+真点击+落盘证据)。**本会话抓修 5 类生产 bug**(独立子代理验收+真机诊断)：①**config 漏解析 `[skills.codify]`** → 技能自创整功能生产静默死(子代理 wiring 评审漏的 config-loader 层,boot 缺 `fp5_codify_wiring_ready` 定位)②**5 处跨层接线断裂**(services 未注册/ToolPathRecorder 从未构造+从未喂数据/build_agent 不传 skill_loader+matcher+recorder)+补 WI-1.6 record_tool→complete 喂数据 ③**relay ReadError 鲁棒性**(中转 relay 经代理掉流式连接致 agent 立即崩 → adapter 归可重试+registry 重试同 provider+流式路径掉链前干净重试,真机救活 agent 在烂代理下完成全部工具)④**前端 ephemeral-card bug**(skill_candidate 卡 message reload 时丢失 → set_messages 保留 awaiting 卡)⑤**方案 B**(codify hook 抽 helper,FinalEvent+ErrorEvent 双触发)。+TC-4.5 B-10双写钩(GC修复)+TC-5.6 trivial不弹卡真机PASS。全程 flag-OFF 字节基线退 0+156 测试绿+前端 tsc 0err,严守 veto-1 无伪造。21 commit。证据 [manual-results-2026-06-06-FP345/RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md)。**待**：TC-3.1/4.1 等其余 TC 续跑(relay 鲁棒性修复后可跑通) |
| 2026-06-05 | **goal-completion 升级 5 个 FP 后端实现全完成 ✅（FP-1 真机手测门 PASS + FP-2/3/4/5 后端全绿）** — 打通"断掉的目标线"闭环：用户说目标→【FP-1 持久化】重启仍在(真机 windows-mcp load_persisted restored=0→1 + UI /goal 查仍在,5截图)→【FP-2 抗漂移】re-anchor 决策点每5轮注入[目标锚定]+task图跨agent共享+handoff带goal+resume续目标→【FP-3 自我纠错】verify接goal_text对照(完成判定=客观证据三绿,人格禁入no_persona_leak)+结构化反思真重规划重试(§7死循环上界LLM调用≤max_iter+3)+高后果evaluator+R-T3降级矩阵+R-T6 shadow→【FP-4 记忆+人格】goal/decision/constraint facts(category-agnostic召回零改+MemEval 491无回归)+PreferenceProfileComponent(priority85/dynamic/Pin置顶/红线无谄媚)+修 daily_decay 从未被调用 bug+light写入快路+单向钩防import环→【FP-5 Skills】接通compaction到主loop(★全回归217绿,最高风险)+embedding强匹配自动载正文+压缩后重挂+技能自创codifier(只生成声明式SKILL.md不执行代码,用户确认门Future-await). **流程纪律**：先冻结§6契约/§7账本(实地核真) → 每FP superpowers sp-writing-plans 出 TDD 计划 → 子代理 TDD 实现(独立文件并行/同文件串行,子代理只实现主线统一提交防撞库) → spec 审查门 → 提交. **自查抓修真问题**：R-T5 字节门(拆 session_goals/goal_tasks 出共享_DDL,goal_mode OFF 不建表,基线退0)、I-1(mark_done落库防完成目标复活)、daily_decay 从未调度、goal_checker 失败 done=True→skipped(不假标完成). 全程 flag 默认 off 字节级 BC,5个FP后 R-T5 基线仍守. **待**：FP-2/3/4/5 真机 windows-mcp 手测门 + FP-5 前端确认卡(批量补跑,spawn_task,均后端全绿). 计划+证据 [plans/2026-06-04-goal-completion-upgrade/](../plans/2026-06-04-goal-completion-upgrade/)(FP-1~5/ + BLOCKERS.md) |
| 2026-06-05 | **v0.6.0-beta.2 发布上线 — 孤儿进程修复 + 自更新闭环 + 模型外置首启下载（真机 E2E 全 PASS ✅）** — 分支 `fix/backend-orphan-cleanup`（兼作构建/发布工作区）。**① 孤儿进程根治**：关桌宠后重开报「8100 被占用」根因=`kill_child()` 空操作（supervisor 已 take 走 Child）+ Win 不回收子进程；修法新增 `job_object.rs` 全局 Job Object(KILL_ON_JOB_CLOSE) + 主窗 Destroyed→app.exit。真机 E2E：只 `taskkill /F /PID <deskpet.exe>`（不带 /T）→ 5 个 backend 全被连带收割 + 8100 释放。**② 自更新闭环**：设置面板加「检查更新」按钮（`SettingsPanel`+`updaterError.ts`）；修 `release.yml` prerelease→false（原 endpoint 因 prerelease 永久 404）。**③ 模型外置（NSIS 化）**：`DESKPET_BUNDLE_MODELS=0` 瘦包 + CPU torch → 3.7GB 打不出 NSIS 变 **304MB**；新增 `model_provisioner.py` 首启从 **腾讯 COS** 直下 bge-m3+whisper（manifest 驱动 urllib，hf-mirror 实测与 hf_hub 0.36 不兼容故改 COS）+ 前端进度横幅；11 单测绿。**④ 签名密钥轮换**（旧口令遗失→新无口令 key 5E3B6A21）。**⑤ 发布**：本地签名构建 → 发公开 `deskpet` release(Latest) + COS；updater endpoints=COS 主 + GitHub 备，双端 200。**真机 E2E**：装 beta.2 → 首启从 COS 真下模型(37 文件落盘) + 孤儿修复复验 PASS。详 [PLAN](../plans/2026-06-05-nsis-model-externalization/PLAN.md)。~~未 merge master~~ **已 merge master**（`94c407b` Merge fix/backend-orphan-cleanup；后续已 bump beta.3，原「未 merge」标注已过时；修正于本次审计） |
| 2026-06-04 | **goal-completion FP-1 目标持久化地基 — 真机 windows-mcp 手测门 PASS ✅** — 先冻结 §6 goal_text 契约（定稿唯一 SessionGoal schema，解决 00-PLAN §6.1 与 blueprint §2 的 done/status·criteria·subgoals·max_iterations 分歧）+ §7 重试账本（实地核真 attempt 计数=per-session 共享额度·in-memory 重启清零）。superpowers sp-writing-plans 出 10-task TDD 计划 → 子代理实现：WI-1.1 Durable Goal Store（SessionGoal 扩字段 + session_goals 表 + SessionDB 3 薄方法 + bind/persist/load_persisted/get_goal_text）+ T1 increment_iteration 落库 + R-T1 lifespan 接电 + WI-1.6 ToolPath 录制 + R-T5 flag-OFF 字节基线 + R-T7 多 worktree 隔离。**自查抓修 2 真问题**：①R-T5 字节基线 FAIL（session_goals 在共享 _DDL 被常态 ensure 建表 → 违反 flag-OFF 字节护城河）→ 拆独立 `ensure_session_goals_table` 门控；②code-review I-1（mark_done 没落库 → 完成目标重启复活）→ 加 persist_done。后端 23 焦点 + 267 回归测试全绿 + R-T5 baseline 退 0。**真机手测门**（windows-mcp SendInput 真点击 + Clipboard 真输入 + 截图）：Code 面板输 `/goal 帮我整理本周三个会议纪要FP1测试` → session_goals 落库 → `taskkill deskpet.exe` + 杀 orphan vite/backend + fresh 重启 → backend log `load_persisted restored=0→1` → 重启后 .tmp 会话恢复 + `/goal` 查询 UI 显示「当前目标：帮我整理本周三个会议纪要FP1测试」（[05 截图](../plans/manual-results-2026-06-04-FP-1/screenshots/05-goal-status-restored.png)）。详 [FP-1/02-manual-test.md](../plans/2026-06-04-goal-completion-upgrade/FP-1/02-manual-test.md) |
| 2026-06-04 | **工具层「极其严格」手工测试 — windows-mcp 真机 40/40 全 PASS ✅** — 按 `testcase/tool-layer-RIGOROUS-manual-test.md`(40 TC)逐 case 真机测：windows-mcp SendInput 真点击 + 剪贴板真输入 + CDP9333 定位/验证 + backend 行为日志三重证据。覆盖全部工具 + 中间件横切(权限门按category上色：截图证 write_file橙/shell红/desktop_write橙 + 熔断3次OPEN + last-mile ArtifactCard多产物 + receipt+HMAC+duration_ms非0 + verify gate off/shadow/strict)+ 6个config重启TC(disabled_toolsets双层门控★回归/dangerous_allowlist/default_timeout设计陷阱/strict_unknown_toolset潜在bug/artifact_envelope ON-OFF/verify shadow)+ 健壮性(优雅失败/越界写OS拦/大输出截断/并发tile隔离不串台)+ 已修bug回归(非法permission_category)。**执行期发现并修复 2 个真 bug**：①doc_create 嵌套 element 格式渲染成字面 dict 字符串(`backend/deskpet/tools/doc_tools.py:_add_element`)②金黄 hint 卡因 last-mile envelope 嵌套不触发(`tauri-app/src/code-panel/MessageBubble.tsx:splitToolError`);均修+复测通过。建可复用 harness(testcase/_cdp.py + _send.py + _approve.py + deskpet-input.ps1 SendInput圣杯)。详 [tool-layer-RESULTS.md](../testcase/tool-layer-RESULTS.md)。回归单测已补(doc_tools 2 + splitToolError 3,全绿)+ 已提交 commit 4e8f449。 |
| 2026-06-04 | **语音对话同步到消息框修复 — 真机 A/B 闭环 PASS ✅** — bug：桌宠主窗口语音不进「消息·主线程」（语音落库但消息框看不到、重开也不补）。**一次根因**：`VoicePipeline` 只 audio_ws point-to-point、从不广播；修复复用文字同款 `_broadcast_default_chat_peers` 发 chat_v2_user_echo/final、skip originator（主窗口已 audio 显示不重复）。**真机暴露二次根因**：`VoicePipeline.control_ws` 是 audio 连接期快照，backend respawn 后 audio 常先于 control 重连 → 快照=None → 旧守卫 `... and self.control_ws and ...` 挡掉广播（落盘诊断实锤 `control_ws_none=true`）。**二次修复**：守卫去掉 control_ws 依赖 + main.py audio_channel 注入实时解析 originator 的闭包（`_control_connections.get(session_id)`，对齐文字路径的实时 `_ws`）。**TDD 16 单测全绿**（含 `test_broadcasts_regardless_of_control_ws_snapshot` 根因守护：control_ws=None 仍广播）；architect 子代理逐行审计 P0/P1 → GO-with-changes；诊断证 fan-out `control_keys` 含 `message-panel-main`、`originator_in_values=true`。**真机 A/B**：修复前真人语音停在 id2152 不进消息框（[11 截图](../plans/manual-results-2026-06-03-voice-msgpanel/screenshots/11-msgpanel-full.png)）；修复后真人语音「提醒买菜」(id2157/2158) **实时进消息框**（用户截图确认）。详 [fix-spec v2](../plans/2026-06-03-voice-msgpanel-sync/00-fix-spec.md) |
| 2026-06-04 | **多屏跨 DPI 拖动 + Live2D 角色渲染修复（master 直提）** — 用户实测桌宠在双屏（Samsung 主屏 dpr 2.13 + Xiaomi 副屏 dpr 1.42，webview dpr 异常比显示器 scale 高 ~1.42×）：①拖不回小屏（抖动+弹回）②拖几次只显示一半 ③拖到小屏角色右半被裁。**7 处根因**：`window_geometry.rs` 移除 on_resize clamp（跨 DPI 振荡）+ `pin_size`（逻辑尺寸跨屏舍入漂移 375→360→657）；`App.tsx` 边缘吸附 250ms 防抖 + 显示器局部坐标转换（非主屏 pickEdge 误判甩回主屏）；`Live2DCanvas.tsx` 角色列宽 cap 在视口内 + dpr 纳入 size 状态/matchMedia 触发画布重渲 + `modelReady` 触发异步模型加载后重渲 + scale 用基础尺寸（避免读已缩放 model.width 致模型爆炸）+ **删除重复 resize effect**（旧版拖动时覆盖修好结果）。真机 SendInput 拖动 + 白板背景截图验证：三星/小米 boot + 拖动后角色均从头到脚完整居中；10 次跨屏尺寸 pin 360×600 零漂移。详 [手测报告](../plans/manual-results-2026-06-03-multimon-drag/REPORT.md) |
| 2026-06-02 | **记忆系统审计 + 修复 #1-#4（master 直提，4 commit）**：3 路交叉验证（我的代码审计 + silent-failure-hunter 子代理 + 最佳实践调研子代理）。**#1 FATAL-A**(`4b700dd`)：lifespan 从不调 backfill_missing → 任何 embedding 缺口永久无声（"刚说的话下次不记得"）→ 加启动自动 backfill 兜底 + 修正虚假注释。**#2 FATAL-B**(`30cbcdf`)：检索/嵌入静默降级只 log.debug → retriever FTS / facts embed / enhanced_retriever 降级点升 warning（vector_worker 已 warning 故不动）。**#3 MemEval**(`56c9c59`/`48d8549`)：~18 双语"字面vs改写"召回对照，真 BGE-M3 改写 Recall@5=**1.0**（证 dense 语义召回真工作、非吃 FTS 字面红利）+ 修模型路径脆弱性（用户迁 F 盘后 C: 硬编码致 model_required 整批 ERROR → 新增 resolver）。**#4**(`4d8de40`)：冲突消解机制（mem0 merge/supersede + Zep 软失效 + 时序链）早已建好且 37 测试全绿，用户决策出厂点亮 facts_extract+enhanced_retriever+cross_key_merge（dataclass 默认仍 False 保字节契约；**真机 E2E 待跑**）。详 [审计+最佳实践报告](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| 2026-06-02 | **superpowers ③ verify_gate strict + code claim patterns — 真机不误杀 + 单测 9/9 PASS**：决策3"硬卡"。claim_patterns.yaml 补 4 条 code 场景(已创建/已修改/测试通过 + en)5→9;dev 翻 `verify_gate_mode=strict`(出厂仍 off)。关键判断:registry.execute_tool 对每个工具都 emit_receipt → 真调过的任务 claim 命中放行、裸声明(fake)拦。真机:STRICT_CHECK.md 任务 write_file→无 verify_gate_nudge→放行完成(未误杀);单测证 fake 无 receipt→拦、未来时→不误判、shadow→不拦。出厂是否翻 shadow 待定。详 [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) |
| 2026-06-02 | **superpowers Layer 1B 偏好记忆(BGE-M3) — 真机 E2E + 单测 7/7 PASS**：决策2 的"记下来后续相同直接做"。新组件 `preference_memory.py`（计划/意图两类 + BGE-M3 cosine + JSON 持久化 + list/clear）。计划记忆接 plan-confirm 门:用户点[执行]→record approved;相似任务 match 命中→自动确认跳过等待。真机:Task A(PREF_ALPHA)走门点[执行]记录→Task B(PREF_BETA 只改文件名)`plan_confirm_auto_approved score=0.936`无 awaiting 直接跑→文件创建。接线踩坑:ServiceContext register 有 allowlist(加字段+白名单)。flag 默认 OFF 出厂不构造。意图记忆(决策1)组件已支持待接线。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1b-preference-memory.md) |
| 2026-06-02 | **superpowers Layer ① plan-confirm 硬门 — GO+CANCEL 真机 E2E 2/2 PASS**：决策2 严格版——code 模式明确任务先出 plan + 等用户点[执行]再跑 ReAct。复用现成 `maybe_extract_plan`（plan.py 自标的 "future enhancement"），加确认门(后台 task await Future 不阻塞 recv loop，最小改动)+ `plan_confirm` WS + 前端 [执行]/[取消] 按钮。**调试挖出真问题**:grid tile 预览(SessionGridView)自己的 renderer 过滤掉了 "plan" 角色 → tile 内单独渲染确认栏修复。CDP 真机:GO→暂停(零 dispatch)→点[执行]→todo+list+write+read 执行→GATE_OK.md 建成;CANCEL→点[取消]→零执行+文件不创建。flag 默认 OFF 出厂字节级不变。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-plan-confirm-gate.md) |
| 2026-06-02 | **superpowers 工作流集成 code 模式 — Layer 1A 落地 + 真机 E2E 3/3 PASS**：用户反馈 auto 模式"问个问题就埋头乱改、不澄清、不验证就说完成"。调研发现根因是 code 模式纯 ReAct 无工作流骨架 + persona 通篇"优先使用工具"。**纠正一处诊断错误**：verify_gate/goal_checker 并非"孤儿代码"，agent_loop 早接好了，卡点是配置 flag 默认 off（本项目反复出现的模式）。**Layer 1A**：重写 `_CODE_MODE_PERSONA_TEMPLATE`（意图门→澄清→计划→执行→验证）+ dev 翻 `verify_gate_mode=shadow`+`emit_receipts`+`goal_mode`。**真机 CDP E2E**（test-research-helper tile，deepseek-v4-pro，全栈非注入）：TC-1 问模型→直答"deepseek-v4-pro"零工具(治#1)；TC-2 模糊派活→先澄清目标/范围/成功标准、零文件改动(治#2/#3)；TC-3 明确任务→todo拆步骤+写前看现状+**写后read_file读回验证**+报告校验结果(治#5)。决策：偏好记忆走 BGE-M3。Layer 1B(偏好记忆)+ shadow→strict 待做。详 [proposal](../plans/2026-06-02-superpowers-code-workflow/proposal.md) + [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1a.md) |
| 2026-06-01 | **工具层全功能手测（按 testcase 真桌宠 E2E）+ 修复 2 个真 bug**：A 类 5 例 CDP 真注入桌宠 WebView2（PPT/Excel/Word/web_fetch/能力门控，4 PASS + windows-mcp 真操作原生保存对话框）；B 类配置契约核对。**挖出并当日修复 2 bug**：① `doc_create` 生成空文档（element 格式契约 `{heading}` vs `{type,text}` 不匹配 → `doc_tools.py` 加归一）② `_load_tools` 漏读 `disabled_toolsets`/`dangerous_tools_allowlist`/`default_timeout_seconds` 等 WI-T5.1 字段（`config.py` 补读）→ 该 3 功能此前配了不生效。真桌宠闭环复测 disabled 生效（excel_create 0 调用，LLM 绕道）；pytest 59 passed。新增 testcase/ 手测体系。详 [tool-layer-test-report](../plans/manual-results-2026-06-01/tool-layer-test-report.md) |
| 2026-06-01 | **记忆系统严测（4 Phase 全收 / G1-G6 + 性能基线，33 新用例 master 直提）**：真机 GUI 终验推翻草率 PASS，挖出并**全修 F5**（facts.search/workspace.recall/find_by_entities 的 `LIKE '%整串%'` → 自然语言 query 永不命中）：① 分词 OR LIKE（`text_tokenize.py`）② memory_search 向量优先（真 BGE-M3）。G5 戳破 eval_gate hit@5 字面驱动（mock==real Δ=0）。G6 钉死 embedding 列真写入 + 写入并发不变量。性能基线：检索热路径 N=500 median 1-3.5ms（护栏非微基准）。CI 跑真 embedder。详 [memory-system-rigorous-test-spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) §8 |
| 2026-05-31 | memory Stage2 followup F1/F2 完成 + 真机 GUI 真测挖出并修复 F3（memory_search 误连坐 forget flag）/F4（code 工作记忆出厂默认开，保字节级契约）；单测全绿，未 merge |
| 2026-05-31 | companion-code v2（slash/goal/team/partition/cache）全套 + 真桌宠 WebView2 E2E PASS；fun-ux 12 交互 merge；dev-worktree.ps1 跑源码修复 |
| 2026-05-27 | OSS 开源准备（LICENSE / SPDX / 凭据脱敏 / CI 适配） |
| 2026-05-24 | 工具层优化 v3（VerifyGate 接电 + stubs 真实现 + ToolsConfig 扩展）；pet-animation UX |
| 2026-05-23 | 工具 last-mile 升级；memory-v2 Stage 2 |
| 2026-05-22 | beta-100 内测就绪；relay 登录集成；builtin skills |

---

## 5. 已知问题 / 测试纪律

- **dev 模式必须用 `scripts/dev-worktree.ps1`**（worktree）或 `dev-start.ps1`（主树）启动 —
  直接 `npm run tauri dev` 会用 stale 打包 exe（旧版本，缺新 endpoint）。详见脚本注释。
- **手工测试纪律**（CLAUDE.md HARD CONSTRAINT）：UI 改动必须 windows-mcp / CDP 真测，
  不能用单测 / 协议层替代。真桌宠 WebView2 测试用 CDP 9222（dev 默认开）注入真实输入。
- **DPI 坐标**：这台开发机 OS scale 150% + WebView dpr 2.13；SendInput 物理点击需正确
  换算（详 [16-sendinput-webview2-final-diagnosis](../plans/2026-05-25-companion-code-skill-upgrade/16-sendinput-webview2-final-diagnosis.md)）。
- **✅ ~~P1 — relay 登录与 backend cloud-llm key 账号脱节~~（2026-06-25 发现 → 2026-06-26 已修复）**：脱节根因是 relay 走旁路（`update_cloud_config` 改单例 `local_llm`，key 不落 registry），backend spawn 期固定的 `DESKPET_CLOUD_API_KEY` env / `deskpet-cloud-llm` slot 与登录账号脱节。**修复**：relay 登录后收编进 `LLMProviderRegistry`（`relay-cloud`/`source=relay`/`account_ref`），聊天经 registry chain **每请求按需读 key**（绕开固定 env，换账号无需重启）；多账号靠 `account_ref` 防串号、登出删 key。真机端到端验证 PASS（`p5s2_chain_resolved` + `chinzy.com 200 OK`）。详 [plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md](../plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md)（v7，A-E 全实现）+ 根因 [followup](../plans/2026-06-25-relay-cloud-key-sync-followup.md)。⚠️ device key 复用三态的**完整**真测待中转站 PR-6 开 `DEVICE_KEY_REUSE_ENABLED` flag（当前过渡态：每登录轮换一把 key，靠中转站 5-key 上限兜底）。
- 其它已知问题见 `README.md` §已知问题（Known Issues）+ `docs/beta/已知问题.md`。

### Follow-up backlog（待排期）

- 🔴 **P1 — DeepResearch Top N 报告完整性与 partial 主卡可见性** — 真实 run `9ed2660a...` 的 v7 流程、子方向进度、文件落盘和文件操作均正常，但“前十个 AI 大模型”报告未明确列出 10 个模型；后端业务状态已诚实为 `partial`，主进度卡却仍只显示 engine `已完成 / 100%`。根因是 v7 没有持久化显式数量约束，child/final 只验证 citation 合法性而不验证 N 个命名项与逐项引用，且 `business_status` 未进入 `workflow.final` 主卡投影。v7 已 immutable，后续应以新的 v8 保留六节点简单图并增加确定性数量/逐项引用门、一次有界修复和独立业务终态徽标；本轮仅登记，未开发。详见 [follow-up](../plans/2026-07-20-deepresearch-topn-quality-followup.md)。
- 🟡 **P2 — `run_shell` 瞬时文件事件审计** — 当前输出契约可拦截直接文件工具越界，并发现 shell
  执行后的全部最终残留；但同一次 shell 内“创建后立即删除”的 workspace 外文件需要 OS 级事件
  观察才能取证。后续按 Run/effect 关联 create/write/rename/delete，丢事件或权限不足必须明确显示
  coverage partial/unknown；该能力只增强审计，不冒充安全沙箱。详见
  [follow-up](../plans/2026-08-13-shell-file-event-audit-followup.md)。
- 🟡 **DeskPet 性能优化建议** — 来源：[会话记录](chatgpt-conversation://6a53c976-1254-83ec-8095-75d5656ee907)。本轮仅登记为后续修复入口；待单独评审建议、核对当前性能基线、拆分实施计划并完成自动化与真机验收后，再更新对应模块架构状态。

---

## 6. 文档索引

- **架构 / 模块文档**: [`ARCHITECTURE/index.md`](./index.md)
- **手工测试用例索引**: [`testcase/index.md`](../testcase/index.md)
- **README**（用户 + 开发者入口）: [`README.md`](../README.md)
- **项目级开发笔记**: [`CLAUDE.md`](../CLAUDE.md)
- **迭代 plan 目录**: `plans/2026-*`（每个迭代一个文件夹，含 PRD/TDD/manual-test/report）
- **OSS 准备**: [`plans/2026-05-27-oss-prep-handoff.md`](../plans/2026-05-27-oss-prep-handoff.md)

---

## 7. 如何更新本文件（HARD 纪律）

> **铁律**：任何任务一旦"通过测试完成"（pytest/vitest/cargo/手工 E2E 全绿），
> **必须在同一次交付内**同步更新对应模块架构文档与本文件 —— "跑过测试但没更新 ARCHITECTURE" = 任务未完成。
> 详见 [`CLAUDE.md` §ARCHITECTURE 更新纪律](../CLAUDE.md)。

完成以下任一事件后更新：
1. 一个 WI / slice / 功能模块跑通验收 → 更新 §3（🟡 → ✅ 或新增行）
2. 里程碑级完成 → 追加一行到 §4 最近里程碑
3. 一个 worktree 合并到 master → 更新 §2 表格状态
4. 发现新的项目级已知问题 / 测试纪律 → 更新 §5

每次更新都改顶部"最后更新"日期。生产链路和边界写对应模块架构；本文件只保留聚合状态、里程碑、已知问题和证据链接。`STATUS/` 兼容文件禁止新增正文。
