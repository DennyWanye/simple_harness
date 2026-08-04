# TC-WB-02 — message-panel 第二窗口彻底移除

> 对应 AC：WB-2 ｜ 行为契约：B2
> manual_required: true（含脚本辅助步骤）

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 启动应用，进入默认视图（应为聊天视图）。 | 聊天/Harness 观察内容直接呈现在主窗内容区，**不需要**点击任何按钮弹出第二个窗口。 |
| 2 | 在整个 UI（侧栏/头部/各视图）中寻找旧「▶ 消息」类打开消息面板的入口。 | 不存在任何"打开消息面板独立窗口"的入口。 |
| 3 | 正常使用一轮（切换四视图、发一条消息、打开设置）后，用系统手段核对窗口数：mac Mission Control / `Cmd+\`` 循环窗口，或 `osascript -e 'tell application "System Events" to count windows of (first process whose frontmost is true)'`。**盲区注明**：osascript 只统计**可见**窗口，数不到隐藏 webview——补一枪写死探针：devtools 控制台执行 `(await window.__TAURI__.window.getAllWindows()).length`。 | 可见窗口计数 = 1（不含系统弹窗/文件对话框）**且** `getAllWindows()` 长度 = **1**（含隐藏 webview 的权威计数）——两个观测面都满足才算过。 |
| 4 | 脚本核对配置：`grep -n "message-panel" tauri-app/src-tauri/tauri.conf.json tauri-app/src-tauri/capabilities/default.json` | 零命中：tauri.conf 不再声明 message-panel 窗口，capabilities windows 仅 main。 |
| 5 | 脚本核对 command 面：`grep -rniE "open_message_panel|close_message_panel|dock_message_panel|toggle_message_panel" tauri-app/src tauri-app/src-tauri/src` | 零命中（四个 Tauri command 及全部前端调用点删除）。 |
| 6 | 脚本核对残留引用：`grep -rn "message-panel\|message_panel" tauri-app/src tauri-app/src-tauri/src` | 仅允许剩余：controlWs 控制会话 sid 常量 `message-panel-main`（历史命名）与注释；其余命中即 FAIL。 |

判定：步骤 1–6 全部满足才 PASS。
