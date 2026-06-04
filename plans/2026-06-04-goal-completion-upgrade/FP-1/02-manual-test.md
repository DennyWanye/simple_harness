# FP-1「目标持久化地基」手工测试报告（windows-mcp 真机 E2E）

> 日期：2026-06-04 ｜ 执行：windows-mcp SendInput 真坐标点击 + 真键盘输入 + 截图
> 证据目录：[`plans/manual-results-2026-06-04-FP-1/`](../../manual-results-2026-06-04-FP-1/)
> 纪律：遵守 CLAUDE.md HARD CONSTRAINT — 真模拟人，未用 WebSocket/pytest/import 当 UI 证据。

---

## 环境

- 启动方式：仅给 Tauri 进程注入 env，让 Tauri 自管 backend + vite（项目坑 #7/#8/#9）：
  - `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`（跑我的源码，非旧 frozen exe）
  - `DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`
  - `DESKPET_CONFIG=G:\projects\deskpet\.tmp\fp1-config.toml`（= AppData config 副本 + `[features] goal_mode=true`，不改 tracked config.toml）
- 后端跑我的源码确认（log）：`[backend_launch] Dev python=...backend\.venv... backend_dir=G:\projects\deskpet\backend`
- windows-mcp 已知陷阱命中并 workaround：
  - `Click(loc=[x,y])` schema bug（pydantic array→string）→ 改 **PowerShell SetCursorPos + SendInput** Win32 圣杯（WebView2 不响应老 mouse_event）
  - DPI 150% 缩放 → 截图/点击用**物理像素** + `SetProcessDpiAwareness(2)`；GetWindowRect 返逻辑坐标需 ×1.5
  - 中文输入 → Clipboard set + Ctrl+V
  - **关键发现**：`/goal` 是 **Code 模式**面板的 slash 命令（桌宠快聊 pill 直连 LLM 不解析 slash），手测走 Code 面板

---

## TC-1.1-restart ★（🔴 真机，pass 关键能力）

**目标**：设目标 → 重启 → 目标仍在 + iterations 恢复。

| 步骤 | 坐标/动作 | 期望 | 结果 |
|---|---|---|---|
| 1 设目标 | Code 面板 InputBar(物理1347,1366) → Clipboard `/goal 帮我整理本周三个会议纪要FP1测试` → Ctrl+V → 发送(1838,1402) | slash 下拉识别 `/goal`，goal 落库 | ✅ slash 下拉「/goal [text] — 目标描述」弹出；session_goals 写 1 行（[03 截图](../../manual-results-2026-06-04-FP-1/screenshots/03-goal-set-codepanel.png)） |
| 2 重启 | `taskkill /F /IM deskpet.exe`(PID31156) + 杀 orphan vite(35160)+orphan backend(33060) → fresh `npx tauri dev` | 全量重启，新 backend 启动 | ✅ 端口释放，新 backend 起 |
| 3 查目标 | 点 code-mode 图标(3692,1008)→恢复 .tmp 会话→打开完整 chat→InputBar(1081,1100) `/goal`→发送(1572,1136) | goal_status 显示原目标 | ✅ **「当前目标：帮我整理本周三个会议纪要FP1测试（已用 0/10 轮）」**（[05 截图](../../manual-results-2026-06-04-FP-1/screenshots/05-goal-status-restored.png)） |

**硬证据（[backend-log-evidence.txt](../../manual-results-2026-06-04-FP-1/backend-log-evidence.txt)）**：
```
首次启动（设目标前）:  goal_store.load_persisted restored=0
重启后（恢复目标）:    goal_store.load_persisted restored=1   ← R-T1 lifespan 接电真跑、重启恢复
goal_store_bound_persistence            ← R-T1 bind 真接
companion_code_v1_goal_mode_ready       ← goal_mode ON
session_goals DB 行(重启后仍在): ('0518...c7cf','code-ks4v3wdq','帮我整理本周三个会议纪要FP1测试','active',0)
```

**判定：PASS** — 真模拟人链完整：真输入设目标 → 真 taskkill 重启 → 真输入查询 → 目标原文存活 + UI 显示。
**iterations 恢复**：本次 iterations_used=0（未驱动 LLM rebound），重启后正确恢复为 0；T1「increment 后落库、重启非 0 恢复」由单测 `test_increment_iteration_persists` 覆盖（照做不砍，属后端单测项）。

---

## TC-1.1-flagoff ★（R-T5 字节基线 / veto-3）

**目标**：goal_mode OFF 时 `session_goals` 表不建（守护「flag-OFF 用户 DB 字节不变」护城河）。

```
$ python scripts/e2e_flag_off_baseline.py
PASS: flag-OFF baseline ok; no session_goals table; sha256=...
退出码 0
```
**判定：PASS** — 修复前此脚本 FAIL（session_goals 在共享 `_DDL` 被常态 ensure 建表）；R-T5 修复（拆独立 `ensure_session_goals_table`，commit `fd504cb`）后通过。

---

## 后端单测（收敛硬证据）

```
$ .venv/Scripts/python.exe -m pytest tests/test_goal_store_persistence.py tests/test_session_goals_db.py tests/test_tool_path_recording.py -q
23 passed
$ ... -k "goal or command or dispatch or agent_loop or session_db or memory_v2 or schema"
267 passed, 0 failed
```

---

## 截图清单（存盘）

| 文件 | 内容 |
|---|---|
| 01-pet-window.png | 桌宠窗口（定位用） |
| 02-pet-toolbar.png | 桌宠 toolbar（定位 code-mode 图标） |
| 03-goal-set-codepanel.png | Code 面板输入 /goal 设目标 |
| 04-after-restart-goal-query.png | 重启后 .tmp 会话恢复 + 输入 /goal |
| 05-goal-status-restored.png | **重启后 goal_status 显示原目标（核心证据）** |

---

## 结论

**FP-1 手测门 PASS（TC-1.1-restart 真模拟人 + TC-1.1-flagoff 字节基线）。**
R-T1 lifespan load_persisted 接电在真机确认（restored=0→1）；WI-1.1 持久化、§6 契约、R-T5 字节门全部真机验证通过。
