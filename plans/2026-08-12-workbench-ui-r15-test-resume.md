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

### S08 / TC-WB-08 — FAIL（可逆子链已清理）

已通过的子链：

1. 设置以内容区页面呈现，没有 backdrop/浮层；Provider、模型状态、预算、数据目录、自启等
   主要能力可见，桌宠形象区不存在。
2. 通过真实 UI 新增一次性 `wb08-reversible-fixture`，地址为本机拒绝端口、模型为
   `wb08-test-model`；dummy key 在 secure field 与卡片均只显示掩码。停用后完全重启，fixture、
   模型、默认模型、停用态均保持。
3. 通过真实 UI 删除 fixture，离开设置再返回后仍不存在；原中转站顺序、启用态、模型未改变。
4. 自启原值为关闭，系统观察面无对应 LaunchAgent。打开后 UI=1，系统生成
   `/Users/denny/Library/LaunchAgents/SimpleHarness.plist` 且 `RunAtLoad=true`；重启后两者仍一致。
   恢复关闭后 UI=0、plist 消失，最终重启仍无残留。

阻断整体 PASS 的实测偏差：

1. testcase 将“模型上下文卡”定义为只读且不得有保存表单，但实际页面包含“压缩触发阈值”
   输入以及“保存到全局 / 保存压缩模型”按钮，步骤 2–3 与冻结口径冲突。
2. Provider 的删除按钮立即删除，没有出现 testcase 步骤 6 要求的确认动作。
3. Agent 有效执行预算原值 15。键盘改为 16 后输入框一度显示 16，但离开设置再返回以及重启
   后均回到 15，未完成后端持久化，步骤 7–8 FAIL。最终原值 15 已恢复。
4. 数据目录 D 为 `.testenv/r15-main`，专用空目录 D' 为 `/private/tmp/wb08-data.XToEVR`，并明确
   取消“同时移动现有数据”。UI 提示“下次启动从新路径读写”，但重启日志和 UI 当前生效均仍为
   D，D' 未生效。实现核对同时显示 macOS 持久化仍为 TBD，且旧变量
   `DESKPET_USER_DATA` 被优先级更高的 `DESKPET_USER_DATA_DIR` 覆盖。步骤 9–11 FAIL。

最终状态：fixture 不存在、预算=15、数据目录=D、自启=关闭、LaunchAgent 不存在；D' 已在应用
退出后移入废纸篓，可恢复。S08 以真实偏差判 FAIL，不以最终无残留冒充 PASS。

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

### S10 / TC-WB-10 — PARTIAL（步骤 5–10 PASS）

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

未完成：步骤 1–4 写死托盘退出路径；本轮 Computer Use 无法附着 macOS SystemUIServer
状态项，不能用红钮退出冒充托盘退出。故 S10 整体仍为 PARTIAL。

主要证据：

- `artifacts/r15-S10-05-invalid-500x640-fallback.png`
- `artifacts/r15-S10-06-dragged-valid.png`
- `artifacts/r15-S10-07-dragged-restored.png`
- `artifacts/r15-S10-08-valid-900x700.png`
- `artifacts/r15-S10-09-truncated-fallback.png`
- `artifacts/r15-S10-10-empty-fallback.png`
- 对应 `r15-S10-*.log`

### S12 / TC-WB-12 — PARTIAL

旧报告中 Rust 的三个 TCP 测试失败已在当前环境重跑消除：

- `cargo test --lib`：`74 passed / 0 failed`；
- companion：`647 passed / 10 skipped / 0 failed`。

证据：

- `artifacts/r15-S12-step2-cargo-test-rerun.log`
- `artifacts/r15-S12-step6-companion-rerun.log`

步骤 7 的 `644ab16` 与当前改版侧预热后二次冷启动性能对照仍未执行，不能判整项 PASS。

### S13 / TC-WB-13 — FAIL

复现方式：精确终止由当前 Tauri 启动的 backend，并用空 listener 占用 8100，阻止 supervisor
立即拉起新 backend；未 mock 前端状态。

观察结果：

- 短暂断连阶段，ChatView 显示“聊天通道已断开”与重试按钮，侧栏徽章为“未连接”；
- supervisor 快速自愈时可恢复连接并真实发送；
- 持续端口占用时，最终出现全屏“启动失败 / 端口 8100 已被其它程序占用”模态，遮住整个
  Workbench。AX 后方虽仍存在 ChatView，但用户无法操作输入和 ChatView 重试。
- 故障保持时点模态重试不会崩溃；释放 8100 后再次重试，backend 恢复，四视图可正常切换。

该全屏模态不能替代 testcase 要求的“Workbench 可见运行期断连、发送不黑洞、ChatView 重试”。
因此本轮如实判 FAIL。

证据：

- `artifacts/r15-S13-01-runtime-disconnected.png`
- `artifacts/r15-S13-02-port-blocked-modal.png`
- `artifacts/r15-S13-03-recovered.png`
- `artifacts/r15-app-cu.log`
- `artifacts/r15-vite-cu.log`

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

## 仍未完成或受阻

- **S08**：已执行并明确 FAIL；需修复只读卡契约、Provider 删除确认、预算持久化与 macOS
  数据目录切换后重测。
- **S10**：步骤 1–4 的托盘退出/重启几何链未执行。
- **S13**：已明确 FAIL，需产品修复后重测。
- **S14**：Computer Use 无法附着 SystemUIServer 状态项，托盘三项与显隐/退出尚未新测。
- **S16**：依赖红钮、Cmd+Q、托盘三条退出矩阵；托盘路径仍未新测。
- **S12 step 7**：baseline/current 冷启动性能对照仍缺。

## 额外发现

`npm run tauri:build -- --debug` 当前被 Tauri 版本检查直接阻断：

```text
tauri (v2.10.3) : @tauri-apps/api (v2.11.1)
```

本轮没有改依赖。UI 真测使用当前 `target/debug/simple-harness` 的哈希一致临时 `.app` 包装，
配单一 Vite 实例与源码 backend；没有使用旧 bundle 冒充当前代码。

## 收尾状态

- Simple Harness、backend、Vite 均已退出；8100/5173 无监听。
- 临时端口 blocker 已退出。
- S08 临时 Provider 已删除；专用 D' 与临时 `.app` 包装均已移入废纸篓；自启
  `SimpleHarness.plist` 已删除。
- S18 删除了 r15 测试 profile 内原有的全部测试会话，并按用例从空态新建 1 个验证会话；
  不涉及生产 profile。
- 未覆盖用户已有的 `baseline.md` 修改与 `tauri-app/vite.config.ts.bak`。
- 未提交、未 push。
