# 冒烟清单 — Workbench UI 改版（全表面各一枪）

> 用途：每次实现批次落地后的最快全表面点检（≈10 分钟）；不替代 testcase/workbench-ui/ 正式用例。
> 每项一行：命令或点击路径 → 预期。任一项 FAIL 立即停批次修复。

| # | 表面 | 一枪操作（命令 / 点击路径） | 预期 |
|---|---|---|---|
| 1 | 启动+窗体 | 启动应用 | 普通窗口出现：系统标题栏、不透明、无桌宠立绘；Dock 有图标 |
| 2 | Chat 视图 | 侧栏 💬会话 → 输入 `冒烟：回复"收到"两个字` → 发送 | 后端真实往返，消息流渲染出"收到"回复；mic 按钮为禁用占位 |
| 3 | Skills 视图 | 侧栏 🧩技能 | 页面（非弹窗）打开，能力列表渲染非报错 |
| 4 | Artifacts 视图 | 侧栏 📄产物 | 列表或空态占位正常显示；有产物时点一个「打开」→ 系统程序打开该文件 |
| 5 | Settings 视图 | 侧栏底部 ⚙️设置 | 页面打开；无「桌宠形象」区块；随机改一项→保存→无报错→改回 |
| 6 | 托盘 | 菜单栏托盘图标 → 依次点「隐藏主窗」「显示主窗」 | 三项文案为 显示主窗/隐藏主窗/退出 Simple Harness；隐藏后消失、显示后回来且状态不丢 |
| 7 | 几何记忆 | 拖拽窗口改尺寸+位置 → Cmd+Q 退出 → 重启 | 尺寸与位置恢复为拖拽后的值（不是默认 1000×700 居中） |
| 8 | companion 连接层 | `grep -aE "companion_action" <dev.sh 输出捕获文件>` + devtools 执行 `await window.__TAURI__.core.invoke("get_window_control_credential")`（同 TC-WB-04 步骤 5 口径） | grep 有命中且含 scope=companion_action、零 `window_scope_denied`；invoke 返回非 error。会话中若自然出现 companion 卡片则顺手确认一次作加分，非必需 |
| 9 | 单窗核对 | 冒烟全程留意 | 除系统对话框外始终只有一个应用窗口 |
| 10 | 静态一枪 | `grep -riE "petcanvas\|pet-anim\|pet-engine" tauri-app/src; grep -n "message-panel" tauri-app/src-tauri/tauri.conf.json` | 两条命令零命中（第一条允许纯注释行） |

执行记录：日期 / HEAD / 执行人 / 10 项 PASS-FAIL 一行结论，入 phase-4 账本。
