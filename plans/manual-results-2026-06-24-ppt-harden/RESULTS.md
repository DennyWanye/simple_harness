# 生产硬化 + 回归真测 — ppt_pro（2026-06-24）

承接「能否上生产」评估,逐条做掉 4 项硬化。

## 代码硬化（已提交 9efaf449 / 测试全绿）

| # | 项 | 做法 | 验证 |
|---|---|---|---|
| 1 | 出图前磁盘预检 | `_disk_preflight`：出图落盘前查 `user_data_dir()` 盘剩余,不足按 ~(60+pages×8)MB 估算提前优雅失败给提示;空间未知绝不阻断 | 单测 3 例（低空间拦/充足放行/未知不拦 + `_render_pro` 短路） |
| 2 | 部分配图失败提示 | `_render_pro` 原丢弃 gate 的 n_ok;改为 reachable 但 n_got<n_want 时 notify「有 X/Y 张配图没生成成功,这些页用纯色版式」 | 单测：首图成功+次图失败→partial 提示含「没生成成功」「1/2」 |
| 3 | auto-open 诚实 | `_open_image_file` 已 never-throw;改 report 按打开结果给话——失败→「已保存到 <path>,请手动打开」 | 单测 2 例：open 成功说「已自动打开」/ 失败说「已保存到」 |

`test_ppt_pro_exec.py` **18 passed**（含修正 2 个 master 上 stale 测试 + 新增 5 个，全部穿过真实 `_render_pro`）。

## 真测（item 4）— 诚实结论

**真机实测确认（G: 源码 + G: 数据，本次新 paths 修复一并验证）:**
- ✅ 应用在 G: 数据/源码下干净启动（`Dev python=…backend`，`user_data_dir=G:\…\backend\userdata`，非 frozen）。
- ✅ 路由 `ppt_pro(image_mode=true, pages=8, theme=dark)` **两次**正确（两份 log line 235 / 229，`parse_ok=True`）。
- ✅ deepresearch 真跑（第一轮 5 个来源：google/艾瑞/teamviewer 实抓）。
- ✅ 大纲草稿生成（5525 字）。

**未能在本环境跑到 deck 落盘 —— 卡在 stale G: dev 环境（与硬化改动无关）:**
- 第 1 轮：G: 那条 Jun-22 旧默认会话上下文溢出（186%），加我中途误点「新话题」导致 UI 会话与任务会话 desync，大纲卡没推到所看会话。
- 第 2 轮（重启 + 先开新会话 + 只发一次）：聊天 ReAct 循环在 ppt_pro 调用后撞 `p5s2_token_budget_block window=8000 tokens=14987 ratio=1.87`，编排未能进入调研。该 `window=8000` **不在 G: config.toml**（config 里是 `context_window_tokens=200000`），疑为代码默认值;**今早 C: 那次 `model_context_resolved window=1000000` 未触发** → C:/G: 环境差异。

**判定**：3 项硬化代码本身已单测充分验证（穿过真实 `_render_pro`）;完整惊艳渲染路径 **2026-06-24 早些时候已在 C: 真机全程 PASS**（同一渲染代码）。本轮 G: 真测因 **stale dev 环境配置**（8000-token agent-loop 预算块）阻断,非产品/改动缺陷。

## 待办（环境/产品，独立于本次硬化）

- 🔎 **查 `p5s2_token_budget_block window=8000` 来源** —— 若 agent-loop 预算真有 8000 硬默认且不随 `context_window_tokens` 走,长会话用户会被误拦,值得修。（C: window=1000000 不复现 → 优先对比两份 config。）
- 🧹 G: `backend/userdata` 的 Jun-22 旧会话/config 偏 stale,后续真测建议先核对 config 或清旧默认会话。

## 证据
`tauri-dev.log` / `tauri-dev2.log`（两轮后端日志）· `screenshots/`（路由确认、大纲阶段、卡片 stall）· `launch-dev-g.ps1` / `send-prompt.ps1`（SendInput 圣杯：WebView2 既忽略老式 mouse_event 也忽略 SendKeys，键鼠都走 SendInput）。
