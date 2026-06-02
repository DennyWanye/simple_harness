# v2 人工测试 — Companion + Code 升级 v2

**关联**: `10-tool-layer-upgrade-v2-proposal.md`
**环境**: deskpet-companion-v2 worktree（端口 8400 / 5473 隔离）
**执行方式**: windows-mcp 实机 + boot smoke 真路径

---

## ★ 三大一票否决用例

| 用例 | 硬证据 | 通过条件 |
|------|--------|---------|
| ★ MR-V2-0 zero regression | `pytest tests/ -q` 末行 ≥ 2061 + 0 failed | passed ≥ 2061 |
| ★ MR-V2-1 G2 Slash UI 真触发 | windows-mcp 启 Tauri + 输入 `/` 看 SlashDropdown 渲染 + ↑↓ 选 + Tab 接受 + screenshot | 截图含 dropdown + 选中高亮 |
| ★ MR-V2-2 G1 Team 真并发 | `manual_team_smoke.py` 跑通 — 3 teammate 真 claim → metrics.jsonl 真增 `team_task_*` event ≥ 3 条 | metrics + log 双证据 |

---

## 完整用例 MR-V2-0 ~ MR-V2-12

### ★ MR-V2-0 zero regression
**步骤**：v2 worktree 跑全套 backend pytest + frontend vitest
**期望**：≥ 2061 + 525 + 0 failed

### ★ MR-V2-1 G2 Slash UI 全套
**前置**：`[features] slash_commands = true` + 启 backend port 8400 + Tauri port 5473
**步骤**：
1. windows-mcp Screenshot 桌宠主界面
2. Click chat 输入框
3. Type `/`
4. Screenshot — 期望看到 SlashDropdown 含 12+ skill
5. Type `pp`
6. Screenshot — 期望 filter 到 ppt-* skill
7. Key ArrowDown ArrowDown
8. Key Tab
9. Screenshot — 期望输入框变成 `/<selected_skill> ` + ArgHintBar 显参数
10. Key Enter
11. Screenshot — 期望 chat 区返 skill_result

### ★ MR-V2-2 G1 Team 真并发
**步骤**：跑 `scripts/manual_team_smoke.py`
**期望**：
- TeamStore.create_task 真创建 3 task
- spawn_team 真 spawn 3 teammate
- claim_task 真原子（3 teammate 不抢同一 task）
- metrics.jsonl 真增 `team_task_created` × 3 + `team_task_claimed` × 3 + `team_task_done` × 3 (理想)
- 整体 < 60s 完成

### MR-V2-3 / 命令历史 ↑ 浏览
**前置**：先输 `/help`、`/goal x`、`/help` 三次
**步骤**：清空输入 → ↑ → 期望显示 `/help`（最新） → ↑ → `/goal x` → ↑ → `/help`（旧）
**期望**：3 个 history entries 正确

### MR-V2-4 / 命令历史只存 / 开头
**步骤**：输 "你好" + 输 "/help" + 清空 → ↑
**期望**：只显 `/help`（"你好" 没存）

### MR-V2-5 G3 Partition — 写工具串行
**前置**：标 unsafe 的 10 个工具列表
**步骤**：测试代码同时调 2 个 write_file 不同路径 → 看 timestamp
**期望**：第 2 个 write_file 必在第 1 个完成后才启动（串行）

### MR-V2-6 G3 Partition — 读工具并行
**步骤**：同时调 2 个 read_file → 看 timestamp
**期望**：2 个 read_file timestamp 差 < 200ms（并发）

### MR-V2-7 G3 混合保序
**步骤**：调 [read, write, read] → 期望返回顺序保持 [r1, w, r2]
**期望**：返回 list 顺序与输入 calls 顺序一致

### MR-V2-8 G4 Subagent prompt cache hit
**步骤**：spawn 2 个 subagent (fork mode) → hash 比对 system prompt bytes
**期望**：两个 hash 完全相同

### MR-V2-9 G4 fresh mode 不复用
**步骤**：spawn 2 个 subagent (fresh mode) → hash 比对
**期望**：两个 hash 不同（独立重建）

### MR-V2-10 Team claim 原子性（真并发）
**步骤**：10 个 teammate 同时 claim 同 1 task → 只能 1 成功
**期望**：9 个 claim 返 None，1 个返 task

### MR-V2-11 Team Mailbox + Permission queue
**步骤**：teammate A send_message to teammate B → B get_messages 真收
**期望**：mailbox 真持久化（SQLite 表落盘）

### MR-V2-12 feature flag 全 OFF 字节级一致
**前置**：3 flag 全 OFF（默认）
**步骤**：发普通 chat（不带 /）+ 调一个 read tool
**期望**：行为与 v1 ship 后字节级一致（无 slash 处理 + 无 team / 无 partition / 无 cache 改变）

---

## 退出标准

- ★ MR-V2-0/1/2 三大用例全 ✅
- 功能 bug = 0
- backend pytest ≥ 2061 + 0 failed
- frontend vitest ≥ 525 + 0 failed
- 至少 2 个 boot smoke 真路径硬证据（`manual_team_smoke.py` + `manual_slash_smoke_v2.py`）
- windows-mcp 实机至少跑 ★ MR-V2-1 + Screenshot 归档
