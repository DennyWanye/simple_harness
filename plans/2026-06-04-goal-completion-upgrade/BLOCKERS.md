# BLOCKERS / 状态交接 — goal-completion 升级

> **2026-06-05 真机手测进展（第二轮）**：
> - ✅ **FP-2 真机手测门 PASS**（[FP-2/02-manual-test.md](./FP-2/02-manual-test.md)）：全 FP flag dev 配置启动，真 UI 设 /goal + 多步 grep 任务 → agent 13 轮 ReAct → `wi13_goal_anchor_injected iter=5/10/15/20` 决策点 re-anchor 周期真机触发 + 3 截图。
> - 🐛 **真机抓修 2 bug**：① context_compressor 缺 ServiceContext 白名单（WI-4.0 接线，compaction_enabled=true 时 code-mode 任务全崩，已修+回归断言+commit）② 端口冲突 crash-loop（orphan backend 累积抢 8100，已清理流程）。
> - 🟢 **同栈验证**：compaction_enabled/verify_gate_mode=strict/goal_facts/persona_inject 全开同跑无 Unknown service；verify_gate_init 真机活跃（FP-3）；facts 表 scope/pinned 列已应用到 live DB（FP-4 schema）。
> - ⚠️ **FP-4 待查 real-machine 发现**：goal_facts_hook=true + /goal set 后，facts `category=goal` 表为空 → B-10 双写钩真机未触发（疑 flag 接线细节，类 context_compressor；WI-3.2 单测已过故是接线问题）。续跑前先查 main.py bind_on_goal_set 是否真被 goal_facts_hook 触发。
> - **剩余真机手测门（spawn_task 批量，app 现已可用+bug已修）**：FP-3 verify 伪完成→重规划真产物（写权限门 workaround）、FP-4 跨会话召回（+查 B-10 钩）、FP-5 压缩后追目标 + 技能自创确认卡（4.3c 前端待建）。


> **2026-06-05 终态更新**：**5 个 FP 后端实现全部完成 + committed**（FP-1~5 共 22 commit），后端单测/回归全绿、R-T5 字节基线守、480 goal-completion 焦点测试绿、MemEval 491 无回归。
> - ✅ FP-1 真机 windows-mcp 手测门已 PASS（load_persisted 0→1 + UI /goal 查仍在，5 截图）。
> - 🟢* FP-2/FP-3/FP-4/FP-5 后端全绿，**真机手测门 + FP-5 前端确认卡批量待补**（spawn_task 已建；用户对 FP-2 决策"接受现证据推进"已应用到 FP-3/4/5 同口径）。
> - **剩余唯一待办**：一次专项真机手测会话（FP-2 iter-5 anchor / FP-3 verify伪完成→重规划产物 / FP-4 跨会话召回+改偏好 / FP-5 压缩后追目标+技能自创确认卡）+ FP-5 4.3c 前端确认卡组件(Tauri/React)。环境/坑见下 + 各 FP-N/01-TDD-PLAN.md 手测门小节 + FP-5 WI-4.3 报告的前端 NOTE。
>
> ---
> 以下为 FP-2 当时的原始阻塞记录（已被上面终态吸收，保留供追溯）：
> 日期：2026-06-05 ｜ 状态：FP-1 完成过门；FP-2 实现+测试+提交完成，**手测门遇真实障碍待拍板**。

## 已完成（硬证据，全部 committed 到 master）

### ✅ FP-1 目标持久化地基 — 完整 + 真机手测门 PASS
- §6/§7 冻结 + WI-1.1 + T1 + R-T1 + WI-1.6 + R-T5/R-T7。
- 后端 23 焦点 + 267 回归全绿；R-T5 baseline 退 0。
- **真机 windows-mcp 手测门 PASS**：Code 面板 /goal 设目标 → taskkill 重启 → `load_persisted restored=0→1` → 重启后 UI /goal 显示原目标（5 截图，见 manual-results-2026-06-04-FP-1/）。
- roadmap §3 FP-1 行全打勾 + STATUS 已更新。

### ✅ FP-2 抗漂移闭环 — 实现 + 单测/集成测试完成（手测门未收口）
- WI-1.3 re-anchor（compress/compact goal_text + 软上限1500 + agent_loop 决策点每5轮 anchor）
- WI-1.4 handoff（spawn_team parent_goal_text + charter Parent Goal + off-goal 回收）
- WI-1.5 resume（auto_resume goal_text_getter + main.py 接线）
- WI-1.2 task graph（goal_tasks 独立 ensure + 原子 claim pass^k=5 + TaskGraphStore + 工具 + build_teammate_tools）
- spikes：T5（同进程→_write_lock claim）、T4（compress 签名冻结）、R-T2（软上限）落地；R-T4/R-T8 见下 defer。
- **测试**：55 焦点 + 280+ 回归全绿；R-T5 baseline 扩断言 goal_tasks 不建，退 0。
- **真机已验证**：重启后 backend 跑 FP-2 源码、`load_persisted restored=1`、**agent 确认在 goal 会话(code-ks4v3wdq)真跑 ReAct**（log: todo_write → write_file，见 manual-results-2026-06-04-FP-2/agent-ran-on-goal-session.txt + 01 截图）。

## 🚧 阻塞点（需你拍板）

**FP-2 手测门「MR-1.3 抗漂移」的 iter-5 决策点 anchor 真机捕获受阻：**
1. re-anchor 决策点在 agent_loop **每 5 轮迭代**注入 `[目标锚定]`。要真机捕获需 agent 在 goal 会话连跑 ≥5 轮。
2. **障碍 A — code-mode 权限门**：写任务（write_file 建 6 文件）在 **iter=2 撞权限门超时** `permission denied (source=timeout)`（写操作需人工审批，无自动批准）→ auto_resume 判 ask_user → agent 停在 iter 2，到不了 iter 5。
3. **障碍 B — 读/grep workaround 未提交**：改发 grep-only 5 步任务（绕权限门）时，dashboard 输入框坐标在上一个任务跑后偏移，SendInput 落空未提交（需每次重新 Snapshot 取坐标，本会话 context 预算已大量消耗于 FP-1+FP-2 实现+FP-1 手测门）。

**re-anchor 机制本身已被单测证明**（test_compactor_goal_anchor: anchor 注入；agent_loop anchor-at-iter5 单测），且代码确认 live 在运行栈。缺的只是「真机 iter-5 截图」这一条 veto-1 证据。

## 需你的决策
- **选项 1（推荐）**：新会话续跑 FP-2 手测门 —— dev config 给 write_file 加自动批准（permission auto-approve / dangerous_allowlist），重启后发写任务跑满 5+ 轮 → grep `[目标锚定]` + 截图；或发读/grep 多步任务并每步重新 Snapshot 取输入坐标。然后继续 FP-3/4/5。
- **选项 2**：接受 FP-2「实现+单测+真机 agent 在 goal 会话跑」为充分证据，re-anchor iter-5 截图改为后补；先推进 FP-3。
- **选项 3**：你指定其他验证方式。

## defer（已记，等确认）
- **R-T4 respawn pending-resume 队列**：main.py:2090-2097 redispatcher 未注册/ws=None 时静默 return（已确认）。WI-1.5 核心（resume 注入 goal_text）已工作；R-T4 是边角健壮性增强，改 WS 重连时序有回归面，建议独立小切片。详 FP-2/03-notes-defer.md。
- **WI-1.3 压缩路径 context_manager 接线**：决策点 anchor（每5轮）已是 re-anchor 主力；压缩路径注入为 belt-and-suspenders，可作增强。

## 当前运行态
- Tauri dev 后台运行中（task buz0evekx，FP-2 代码 + goal_mode on + .tmp 会话 goal active），backend :8100 / vite :5173。续跑可直接用；不续可 `taskkill /F /IM deskpet.exe` + 杀 8100/5173 orphan。
- 临时配置：`.tmp/fp1-config.toml`（AppData config 副本 + goal_mode=true）。启动 env：DESKPET_CONFIG/DESKPET_BACKEND_DIR/DESKPET_PYTHON（见 FP-1/02-manual-test.md）。
