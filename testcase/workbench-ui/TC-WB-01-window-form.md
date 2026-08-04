# TC-WB-01 — 主窗普通窗口形态

> 对应 AC：WB-1 ｜ 行为契约：B1
> manual_required: true（真机 MCP）
> 前置：使用**全新隔离 user-data 目录**首次启动（首启居中断言依赖冷数据）；mac 真机。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 用空的隔离 user-data 目录启动应用（dev 或打包版，记录方式），等待主窗出现。 | 主窗为**普通窗口**：有系统标题栏（mac 红黄绿三钮）；窗口内容不透明（无透出桌面背景）；无任何桌宠角色立绘/动画。 |
| 2 | 目测并记录窗口初始位置与尺寸（截图 + 系统窗口信息）。**单显示器**环境执行判定；若为多显示器，只记录观察结果，不做居中断言（裁决第 5 条）。 | 尺寸为默认 1000×700（±系统缩放取整误差）；居中判定口径（裁决第 5 条，`oracle-clarifications.md`）：窗口几何中心落在屏幕中心 **±10% 区域**内（横向偏差 ≤ 屏宽 10%、纵向偏差 ≤ 屏高 10%，截图手工判定）即 PASS。 |
| 3 | 查看 mac Dock / Cmd+Tab 应用切换器。 | 应用出现在 Dock 与 Cmd+Tab 列表中（skipTaskbar=false 的用户可见语义）。 |
| 4 | 拖拽标题栏移动窗口；拖拽窗口边缘/角缩放。 | 窗口可自由移动与缩放；可放大超过 1000×700（无 max 限制导致的卡住）。 |
| 5 | 持续向内缩小窗口到不能再小，记录最终尺寸。 | 最小停在 800×560（±系统缩放取整误差），不能缩得更小。 |
| 6 | 点击标题栏绿色最大化钮。 | 窗口可最大化/还原（maximizable）。 |
| 7 | 静态核对（脚本辅助，非判定主体）：`grep -n "macOSPrivateApi" tauri-app/src-tauri/tauri.conf.json`；`grep -n "\"transparent\"" tauri-app/src-tauri/tauri.conf.json` | macOSPrivateApi 零命中；main 窗 transparent 为 false 或未声明（默认 false）。 |

判定：步骤 1–6 全部满足才 PASS；任一形态项（标题栏/居中/默认尺寸/min 尺寸/Dock 可见/不透明）不符即 FAIL。
