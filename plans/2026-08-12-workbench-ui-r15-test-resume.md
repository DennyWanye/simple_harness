# Workbench UI r15 续测记录（2026-08-12）

> 代码锚点：`7415f764993abefc9d78ba5d8b017984649fd339`
>
> 执行约束：按用户要求，本轮未启用 plan-test skill，也未创建或修改 gate ledger。
> `workbench-ui-20260811-r15` 当前只有 artifacts，没有 `plan-test-run.json`，因此本文只记录
> 可复核的测试事实，不宣称机器门 READY。

## 环境结论修正

旧报告中的“当前环境禁止所有 localhost TCP 监听”已经失效。本轮现场自检结果：

- `127.0.0.1` 与 `::1` 均可成功 bind/listen；
- Vite 正常监听 `5173`；
- Tauri 自行拉起源码 backend，正常监听 `127.0.0.1:8100`；
- 启动日志明确为
  `Dev python=/Users/denny/projects/simple_harness/backend/.venv/bin/python`
  与 `backend_dir=/Users/denny/projects/simple_harness/backend`。

因此此前把 S05/S08/S10/S13/S14/S16/S17/S18 统一标成“沙箱阻断”不再成立，必须按实际
用例结果分别判定。

## 本轮结果

### S05 / TC-WB-05 — PASS

已通过：

1. 通过真实 UI 建立 A/B/C 三个独立会话并分别获得 Kimi3 真回复：
   - A：`c2c9a6bf-f892-436c-bfcf-e24d45e1f4dc`
   - B：`1f2b4302-2642-452e-8d05-ecd041219909`
   - C：`9dacc14a-9724-40e1-aac8-d6e7744a3ce2`
2. 会话列表按新到旧排序，切换 A/B 时消息互不残留。
3. “＋新建会话”生成新 sid `c6e3e6eb-c91c-492a-8c28-007955415c45`，发送
   `新建会话往返验证` 后获得真实回复，切换后仍保持。
4. 将该会话重命名为 `S05重命名保持验证`，切换离开再返回后标题保持。

5. 用户即时确认后，通过真实 UI 删除 `S05重命名保持验证`；条目立即消失且后续批量清理中
   不再出现，未加载已删 sid。步骤 6 已闭合，TC-WB-05 全部通过。

证据：

- `artifacts/r15-S05-01-three-sessions.png`
- `artifacts/r15-S05-02-session-a.png`
- `artifacts/r15-S05-03-session-b.png`
- `artifacts/r15-S05-04-new-roundtrip.png`
- `artifacts/r15-S18-05-after-batch10.jpeg`（包含 S05 重命名会话的真实 UI 删除结果）

### S07 / TC-WB-07 — PASS（修复后复测）

生产 Harness 的 `execute_prepared` 路径现与旧 `execute_tool` 路径一致生成 artifact envelope；
相对路径只在可信的当前 Run workspace 内解析为绝对路径，原工具结果不被伪改。Tauri 文件动作
白名单增加 `<user_data>/workspace/`，没有放宽到任意磁盘路径。

Kimi3 真链路创建 `wb07-artifact-proof-2.txt` 后，消息流出现真实 ArtifactCard；点击“打开”由
TextEdit 显示精确内容 `absolute artifact path verified`，点击“在文件夹中显示”由 Finder 选中
目标文件。SessionDB 对应 tool message 为 `projection_kind=artifact_card`，路径为当前 workspace
内绝对路径。

证据：

- `verification/workbench-ui-20260812-fixes/artifacts/S07-real-chat-artifact-card.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S07-artifact-opened-in-textedit.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S07-artifact-show-in-finder.jpeg`

### S08 / TC-WB-08 — PASS（修复后复测）

初测暴露的删除确认、预算请求关联、macOS 数据目录持久化与模型上下文卡口径均已修复并复测：

1. Provider/模型/API key、数据目录、Agent 预算、自启为可编辑项；Embedder 为只读状态卡；
   模型上下文卡保留压缩阈值与压缩模型编辑能力，用例已同步实际产品边界。
2. 一次性 Provider 的新增、停用、完全重启保持和删除均通过；删除前出现明确确认对话框，
   dummy key 始终掩码，最终 fixture 无残留。
3. Agent 预算通过带 `request_id` 的请求/响应持久化，15→16 后离开页面和完全重启仍为 16，
   恢复 15 后再次重启保持 15。
4. 数据目录页同时展示“当前生效目录”和“下次启动目录”。选择专用 D' 后当前仍诚实显示 D、
   下次显示 D'；完全重启后 D' 成为当前目录。恢复 D 后再次重启，当前/下次均回到 D。
5. 自启打开后 LaunchAgent `RunAtLoad=true`，重启保持；恢复关闭后 plist 消失且最终无残留。

证据：

- `artifacts/r15-S08-01-settings-overview.jpeg`
- `artifacts/r15-S08-02-provider-added.jpeg`
- `artifacts/r15-S08-03-provider-restart-persisted.jpeg`
- `artifacts/r15-S08-04-provider-deleted.jpeg`
- `artifacts/r15-S08-05-budget-16.jpeg`
- `artifacts/r15-S08-06-data-budget-restart-result.jpeg`
- `artifacts/r15-S08-07-autostart-restart-persisted.jpeg`
- `artifacts/r15-S08-08-autostart-restored.jpeg`
- `artifacts/r15-S08-09-final-clean-state.jpeg`
- 对应 `r15-S08-*.log`
- `verification/workbench-ui-20260812-fixes/artifacts/S08-final-restored-settings.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S08-next-launch-directory-honest-state.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S08-next-launch-directory-restored.jpeg`

### S10 / TC-WB-10 — PASS

真实 user-data：`/Users/denny/projects/simple_harness/.testenv/r15-main`。

- 原始几何已备份，测试结束后 SHA-256 一致恢复：
  `8c81b258c36da5fbd4232d31a6cff6931249b10113ccd1ea4e834219ce46e5f7`。
- 写入 500×640：启动日志为 `load() returned None (no file or out of range)`；应用不崩，
  截图为 1000×701，证明拒绝旧值并回默认，不是 clamp 到 800×560。
- 从默认窗口真拖拽到 920×651：文件写入 920×651，重启日志与截图均恢复 920×651。
- 写入合法 900×700：启动日志明确 loaded 900×700，截图为 900×700，没有一律回默认。
- 写入截断 JSON `{"width":500`：不崩，回默认 1000×701。
- 写入 0 字节空文件：不崩，回默认 1000×701。
- 结束后恢复测试前的 1100×750 几何文件。

步骤 1–4 于 2026-08-12 由用户在当前 macOS 打包版现场完成并确认通过：非默认几何下经托盘
「退出 Simple Harness」完全退出，重新启动后继续恢复 `1100×750 @ (400,200)`。退出后独立
对账确认主进程、backend 与 8100 LISTEN 全部为零；随后由当前代码重新启动，启动日志明确
`loaded 1100x750 logical pos=Some(400),Some(200)`，Computer Use 截图像素为 1100×750。
结合上述现场真点击确认、系统对账与步骤 5–10 的既有实测，S10 全部 PASS。

主要证据：

- `artifacts/r15-S10-05-invalid-500x640-fallback.png`
- `artifacts/r15-S10-06-dragged-valid.png`
- `artifacts/r15-S10-07-dragged-restored.png`
- `artifacts/r15-S10-08-valid-900x700.png`
- `artifacts/r15-S10-09-truncated-fallback.png`
- `artifacts/r15-S10-10-empty-fallback.png`
- 对应 `r15-S10-*.log`
- `verification/workbench-ui-20260812-fixes/artifacts/S16-user-tray-exit-restart-geometry.jpeg`

### S12 / TC-WB-12 — PASS（步骤 7 经用户批准移除）

旧报告中 Rust 的三个 TCP 测试失败已在当前环境重跑消除：

- `npm test`：`71 files / 539 passed / 0 failed`；
- `cargo test --lib`：`79 passed / 0 failed`；
- `npm run typecheck`、`npm run build`、`cargo check`：退出码 0；
- companion：`647 passed / 10 skipped / 0 failed`（51.35 秒）。

证据：

- `artifacts/r15-S12-step2-cargo-test-rerun.log`
- `artifacts/r15-S12-step6-companion-rerun.log`

2026-08-12 用户明确批准忽略步骤 7；该性能对照不执行、不阻断交付，且不得再次启动带
Live2D 的 `644ab16` 历史基线。旧基线进程已退出，8100/5173 无残留监听。

### S13 / TC-WB-13 — PASS（修复后复测）

复现方式：精确终止由当前 Tauri 启动的 backend，并用空 listener 占用 8100，阻止 supervisor
立即拉起新 backend；未 mock 前端状态。

修复后观察结果：

- 运行期 backend 持续不可用时不再切到全屏启动失败遮罩；Workbench 保持可见。
- 顶部运行时故障横幅说明 backend 不可用并提供重试；ChatView 状态条显示断连，侧栏徽章按
  identity/chat 两链最差态显示“未连接”，输入/发送 fail closed，不吞消息。
- 连接 secret 尚未读取或空 listener 接受 TCP 但不完成 WebSocket 握手时，前端均有有界失败，
  不会永久停在 connecting。
- 故障保持时点击重试不崩；释放 8100 后点击重试恢复，四视图均可切换。
- 恢复后使用 Kimi3 真发送“请只回复：S13 恢复成功”，HTTP 200，消息流收到精确回复。

该结果闭合了 Workbench 可见运行期断连、侧栏最差态、发送不黑洞、重试恢复和真模型往返。

证据：

- `artifacts/r15-S13-01-runtime-disconnected.png`
- `artifacts/r15-S13-02-port-blocked-modal.png`
- `artifacts/r15-S13-03-recovered.png`
- `artifacts/r15-app-cu.log`
- `artifacts/r15-vite-cu.log`
- `verification/workbench-ui-20260812-fixes/artifacts/S13-runtime-backend-unavailable.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S13-recovered-kimi3-roundtrip.jpeg`

### S17 / TC-WB-17 — PASS

使用仓库已有的确定性 fixture 脚本把可删除测试 profile 补到：30 个会话、一个 80 字符无空格
标题、30 个 mtime 递增产物。随后全部通过真实 UI 验证：

1. 真拖拽窗口到 800×560，截图像素尺寸精确为 800×560；
2. 30 个会话可滚动到底部；排序为当前新会话在上、fixture #25→#01 在下；
3. 80 字符标题显示为 `压力测试标题ABC12…`，没有撑宽侧栏或产生横向滚动；
4. “＋新建会话”在最小尺寸与长列表下仍可见；
5. 点击长标题会话后正确加载 `规模造数会话#01 / 回执#01`；
6. 产物库显示“共 30 项”，AX 全序为 report-30.txt→report-01.txt；
7. 会话→技能→产物→设置在 800×560 下完整切换，无白屏；Vite 日志无
   `console.error`、Uncaught、TypeError 或 ReferenceError。

证据：

- `artifacts/r15-S17-seed.log`
- `artifacts/r15-S17-01-30-sessions-800x560.png`
- `artifacts/r15-S17-02-list-bottom-long-title.png`
- `artifacts/r15-S17-03-long-title-loaded.png`
- `artifacts/r15-S17-04-30-artifacts.png`
- `artifacts/r15-S17-05-four-view-cycle-settings.png`
- `artifacts/r15-S17-vite.log`
- `artifacts/r15-S17-app.log`

测试结束后窗口几何已恢复为 1100×750；fixture 会话与 30 个 report 文件保留在可删除的
`r15-main` profile，未在无确认情况下做永久删除。

### S18 / TC-WB-18 — PASS

用户即时确认后，在可删除的 `.testenv/r15-main` profile 中完成全部真实 UI 删除边界：

1. 打开会话 A（`c2c9a6bf-f892-436c-bfcf-e24d45e1f4dc`），消息流明确显示 A 内容；从
   当前打开态删除后，A 立即从列表消失，界面确定切到 `ea3996ca-99db-404b-b987-d928b54d6fac`，
   消息区没有 A 残留。
2. 删除后立即发送 `删后归属验证`，Kimi3 HTTP 200 真往返成功，回复归属当前非 A 会话。
3. 完全重启后 A 未复活，`删后归属验证` 及回复仍保存在正确会话。
4. 通过每次重新读取最新界面和确认框，依次删除剩余 29 个测试会话；最后一个删除后出现
   “暂无历史会话…新建会话/直接输入”的明确空态，无崩溃、白屏或旧消息残留。
5. 在零会话空态直接发送 `空态新建恢复验证`，自动创建唯一新会话
   `4de16984-eeb7-4a14-8663-5f1485df146d` 并获得 Kimi3 真回复。

证据：

- `artifacts/r15-S18-01-before-delete-A.jpeg`
- `artifacts/r15-S18-02-after-delete-A.jpeg`
- `artifacts/r15-S18-03-post-delete-roundtrip.jpeg`
- `artifacts/r15-S18-04-restart-persistence.jpeg`
- `artifacts/r15-S18-05-after-batch10.jpeg`
- `artifacts/r15-S18-06-after-batch20.jpeg`
- `artifacts/r15-S18-07-last-session-before-delete.jpeg`
- `artifacts/r15-S18-08-zero-session-empty-state.jpeg`
- `artifacts/r15-S18-09-empty-state-new-roundtrip.jpeg`
- `artifacts/r15-S18-app.log`
- `artifacts/r15-S18-restart-app.log`

### S14 / TC-WB-14 — PASS（用户现场真机确认）

2026-08-12 用户在当前 macOS 打包版现场完成托盘四步并明确确认通过：托盘菜单为
「显示主窗」「隐藏主窗」「退出 Simple Harness」三项；隐藏后应用仍运行且托盘仍在；显示后
主窗与原内容恢复；托盘退出后图标消失。退出后的独立系统对账确认主进程、backend 与 8100
LISTEN 均为零残留。该项不伪造 Computer Use 菜单截图，UI 动作证据来源明确登记为用户现场
手测确认，退出终态由进程/端口检查交叉验证。

### S16 / TC-WB-16 — PASS

- 路径 A 红钮：真实点击 macOS 红色关闭钮后，Simple Harness 主进程、backend 进程和 8100
  LISTEN 均为零残留；重启恢复 1100×750、位置 (400,200)。
- 路径 B Cmd+Q：真实键盘退出后同样全清；再次启动恢复同一几何。
- 路径 C 托盘退出与步骤 8 隐藏反证由用户在当前 macOS 打包版现场完成并明确确认通过。
  托盘退出后独立检查主进程、backend 与 8100 LISTEN 均为零；随后重启日志恢复
  `1100×750 @ (400,200)`，Computer Use 截图像素为 1100×750。隐藏路径则保持应用运行并可由
  托盘「显示主窗」恢复，证明隐藏语义独立于三条完全退出路径。

证据：

- `verification/workbench-ui-20260812-fixes/artifacts/S16-geometry-anchor-before-red-close.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S16-red-close-restart-geometry.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S16-cmdq-restart-geometry.jpeg`
- `verification/workbench-ui-20260812-fixes/artifacts/S16-user-tray-exit-restart-geometry.jpeg`

## 未完成边界

- **S10/S14/S16**：均已闭环；其中托盘 UI 动作为用户现场真机手测确认，退出终态与重启几何由
  独立进程/端口检查、启动日志和当前版本截图交叉验证。
- **S12 step 7**：用户已批准移除，不再是未完成项。
- **plan-test gate**：仍按用户要求暂停；未创建/修改 r15 ledger，不宣称机器门 READY。产品与
  testcase 层的 18 个场景现均已达到 PASS 或经明确批准退役的终态。

## 额外发现

Tauri JS API/CLI 已固定为与 Rust crate 匹配的 `2.10.1`，此前版本漂移阻断已消除：

```text
@tauri-apps/api 2.10.1 / @tauri-apps/cli 2.10.1
```

UI 真测使用当前 `target/debug/simple-harness` 与 bundle 内主程序 SHA-256 一致的当前 `.app`，
配单一 Vite 实例与源码 backend；没有使用旧 bundle 冒充当前代码。

## 收尾状态

- Simple Harness、backend、Vite 均已退出；8100/5173 无监听。
- 临时端口 blocker 已退出。
- S08 临时 Provider 已删除；专用 D' 与临时 `.app` 包装均已移入废纸篓；自启
  `SimpleHarness.plist` 已删除。
- S18 删除了 r15 测试 profile 内原有的全部测试会话，并按用例从空态新建 1 个验证会话；
  不涉及生产 profile。
- 未覆盖用户已有的 `baseline.md` 修改与 `tauri-app/vite.config.ts.bak`。
- 本轮产品修复、最新测试事实与架构状态在同一次交付提交；不 push，除非用户明确要求。
