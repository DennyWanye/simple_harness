# 通用行动与能力包平台 — Windows Computer Use full-audit

> 日期：2026-07-24  
> 当前状态：PARTIAL / BLOCKED（VS-1、VS-2 已通过；完整 required 矩阵尚未完成）  
> 产品入口：真实 DeskPet 主消息页；禁止 WebSocket/API 注入  
> 隔离实例：backend `18120` / Vite `15193` /
> user-data `F:\projects\deskpet\.e2e-universal-action\userdata`

## 1. 测试前事实

| 项 | 事实 | 状态 |
|---|---|---:|
| Godot | PATH 中无 `godot/godot4`；常见 Program Files/LocalAppData 路径不存在 | 缺失，S-1 前态有效 |
| Blender | `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`，FileVersion `5.1` | 已安装；S-2 只能验证探测/使用，不能声称验证安装 |
| fixture manifest | `fixture-manifest.json`，schema v1；UltraForge canary、Godot marker、UAC marker 前态均不存在 | 待启动前再次 PASS |
| 主实例 | 现有 5173/backend 主实例属于用户环境，不在本轮清理范围 | 保留 |
| 隔离端口 | 18120/15193 启动前无 listener | PASS |
| 严格 last-mile | 首轮 6 pass / 1 skip；设置 `DESKPET_NODE` 后复跑 7/7 PASS、0 skip，`DECISION: SHIP`；精确树退出并释放至少 7246.3 MiB private memory | PASS |

任何登录账号、密码、token、device key 均不得进入本目录、日志或截图。

## 2. 唯一启动方式

使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\run-isolated-tauri.ps1 -ScenarioBatch S1-S5
```

脚本只给 Tauri 注入源码 backend、隔离端口/user-data/workspace 和 fixture env；不单独启动
backend/Vite。必须在日志中看到 source `Dev python=...F:\projects\deskpet\backend`，看到
bundled backend 立即 FAIL。

## 3. 场景账本

| scenario | root run | engine/business | quality | evidence | 状态 |
|---|---|---|---|---|---:|
| VS-1 | `4eeb23a0d3f05fb38a32e1d8e7727054` | `agent.general` / ReAct | 模型查询真实目录，同 root 激活并调用 `file_write`、`run_shell`；通用 Shell fallback 启动记事本并截图 | sentinel `UA-VS1-A7-20260724T143200Z`；task `task-d6bbaa25f2a417b8e14e3d24c904d393`；final message 139 | PASS |
| VS-2 | `74e21a97b26852b3a22047b7fb2eb37a` | `agent.general` / ReAct | `hello.txt` 27 bytes；PowerShell 与 Git Bash 独立读回；首次校验失败后同 root 重规划成功 | sentinel `UA-VS2-A3-20260724T145000Z`；task `task-e3112209099f5cbbab21c2e297e90e2f`；final message 169 | PASS |
| B-1 | — | — | 能力中心真实投影 | — | PENDING |
| B-2 | `4eeb23a0d3f05fb38a32e1d8e7727054` | Manual / durable TaskGrant | 首次副作用前真实点击“允许一次”，后续同 task 动作用派生 grant；未覆盖新目录和安装类别 | permission decision、task scope 与 receipt 对账 | PARTIAL |
| B-3 | `74e21a97b26852b3a22047b7fb2eb37a` | Auto / durable policy | 设置页真实开启 Auto；同轮 0 个等待授权并完成写文件、双 Shell 校验；未覆盖 heartbeat 取消和重启恢复 | `authorization_policy_state(mode=auto,generation=1)`；`permission_auto_mode_set enabled=True` | PARTIAL |
| B-4A | — | — | UAC 拒绝后复查 | — | PENDING |
| B-4B | — | — | UAC 同意后复查 | — | PENDING |
| B-5 | — | — | 三 root 并行隔离/FIFO/精确取消 | — | PENDING |
| B-6 | — | — | 两轮隔离实例均按 launcher 父子树精确退出，未按进程名广杀；完整隐私矩阵未跑 | 第二轮 13 PID、survivor=0、18120/15193 listener=0、释放 8533.9 MiB；主 8100 保留 | PARTIAL |
| B-7 | — | — | 模型 Profile + ticket binding | — | PENDING |
| S-1 | — | — | 可玩 Godot 塔防 | — | PENDING |
| S-2 | — | — | 可打开 `.blend` + 合格渲染 | — | PENDING |
| S-3 | — | — | 浏览器 CRUD + 刷新持久 | — | PENDING |
| S-4 | — | — | integrity fail-closed | — | PENDING |
| S-5 | — | — | 自建并同 root/重启后复用 | — | PENDING |
| S-6 | — | — | failure → same-parent replan → 成功 | — | PENDING |

任一 required 行仍为 PENDING/PARTIAL/NOT RUN 时，总结论必须是 `FAIL/BLOCKED`，不能
用自动化旁证改成 PASS。

## 4. UI 动作证据

每次动作前在执行记录声明：

```text
坐标=(x,y)|动作=...|期望=...
```

每个 case 保存动作前/后截图、窗口标题/焦点控件、输入 sentinel、日志 offset、root/task/
Attempt/provider identifiers、只读 SQL、产物 hash 与业务质量结论。截图与报告不得含登录
凭据。

## 5. 进程清理账本

已完成两轮精确清理：

1. immutable admission 边界修复前实例：14 个精确 PID 全部退出，survivor=0，隔离端口归零，
   释放 8647.3 MiB private memory。
2. 最终 VS-2 实例：launcher PID `12140`，目标
   `18892,18760,21904,7052,21564,31488,24828,2772,27440,4992,9232,32668,12140`；
   13/13 退出，survivor=0，18120/15193 listener=0，释放 8533.9 MiB private memory。

两次清理后用户主实例 backend `8100` 均继续监听；未按 `deskpet.exe`、`python.exe` 或
`node.exe` 进程名广泛结束。

## 6. 本轮发现并闭环的生产缺陷

- durable admission / ReAct decision 持久化前未解冻 immutable product payload，
  导致 `mappingproxy` 越过 JSON 边界；现统一在 Venue、UoW 与 ReAct durable decision
  边界执行 `thaw_json`。
- `ProductDomainSink.store_plan` 将冻结的 steps/action categories 直接写 SessionDB，
  真实 Manual 计划生成后触发 `mappingproxy is not JSON serializable`；现先解冻完整
  payload，并增加生产适配层回归。
- 聚焦回归最终为 167 passed + 单个恢复时序用例独立复跑 PASS；完整
  `test_run_kernel.py` 为 62/62 PASS。压力组合中曾出现一次既有 aiosqlite 线程关闭竞态
  warning，未改变执行结果。

## 7. 未完成边界

required 的 B-1～B-7、S-1～S-6 尚未全部完成，因此本 full-audit 的总门仍为
`FAIL/BLOCKED`，不能把 VS smoke 与自动化旁证写成整份计划完成。B-4B 还要求用户亲自在
Windows Secure Desktop 处理 UAC；自动化不能代替该动作。
