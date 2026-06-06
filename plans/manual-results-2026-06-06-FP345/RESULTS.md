# 真机手测结果 — goal-completion FP-3/4/5（2026-06-06）

> 被测 commit：`e705373`（含 FP-5 接线修复 58fbac8 + B-10 GC 修复 3932637 + FP-3/4 小缺口 dd1d1e7）
> 环境：`npx tauri dev` 跑源码后端（`DESKPET_BACKEND_DIR=backend` 已确认 `[backend_launch] Dev python=...backend\.venv`，非 frozen）+ 全 FP flag（`.tmp/fp1-config.toml`）+ 真 LLM relay（chinzy.com gpt-5.5，登录态走 keychain `default.deskpet-cloud-llm`）+ 独立 `DESKPET_USER_DATA_DIR=.tmp/fp345-userdata`。
> windows-mcp 真模拟人点击/输入，截图存 `screenshots/`。

---

## ✅ 启动健康验证（boot log 硬证据）

| 检查点 | 证据 | 判定 |
|---|---|---|
| 跑源码非 frozen | log `[backend_launch] Dev python=...backend\.venv\Scripts\python.exe backend_dir=...backend` | ✅ |
| goal_mode ON | log `companion_code_v1_goal_mode_ready` | ✅ |
| FP-5 compaction 接通 | log `wi4_0_compaction_enabled threshold=0.75` | ✅ |
| FP-4 B-10 钩绑定 | log `b10_goal_facts_hook_bound` | ✅ |
| **FP-5 我修的新接线生效** | log `fp5_auto_disclosure_wiring_ready matcher=...`（SkillMatcher 构造成功） | ✅ |
| 无 Unknown service / Traceback | grep 无（codify hook 依赖的 tool_path_recorder/skill_candidate_store/llm_registry 已注册，否则 boot 即 ValueError） | ✅ |
| 真 LLM relay | log `GET https://chinzy.com/v1/models 200` + `model_context_resolved gpt-5.5` | ✅ |

> **意义**：FP-5 接线修复（5 处跨层断裂）在生产真机验证 —— 若 service 仍未注册，启动时 codify hook 的 `get()` 会抛 ValueError；现 boot 干净 + `fp5_auto_disclosure_wiring_ready` 证明 SkillMatcher/recorder/candidate_store 全部正确注册。

---

## ✅ TC-4.5 — B-10 双写钩 /goal set → facts category=goal（PASS，真机硬证据）★

**对应**：FP-4 手测门④ + 验证本会话的 B-10 fire-and-forget GC 修复（commit 3932637 `_fanout_tasks` 强引用）。

**真模拟人步骤**（全 SendInput 圣杯）：
1. SendInput 点击桌宠工具栏「跳过」关 onboarding（物理坐标 3343,1733）。
2. Win32 ShowWindow 激活 Code Mode 面板（handle 2952214）。
3. SendInput 点「+ 新项目」(1235,395) → native 文件夹对话框 → 双击 `fp345-proj` → 点「选择文件夹」(1517,1021) → **code session `code-cn8im6rt` 创建**。
4. Clipboard 设 `/goal 帮我整理本周三个会议纪要并生成PPT_B10真机测试` → SendInput 点输入框(1155,1170)聚焦 → **SendInput 键盘 Ctrl+V**（圣杯关键，见下）→ slash 补全识别 `/goal [text]` → SendInput 点「发送」(1644,1209)。

**证据**：
- 截图 `screenshots/tc-4.5-b10-goal-set.png`：UI 助手回复「**已设置目标：帮我整理本周三个会议纪要并生成PPT_B10真机测试（上限 10 轮）**」。
- `state.db` facts 表 `WHERE category='goal'` → **1 行** `('goal','user','goal_code-cn8im6rt',<目标文本>,'session')` —— **B-10 双写钩真机触发**，scope=session、key=goal_<sid> 全对。
- `state.db` `session_goals` → 1 行 `('code-cn8im6rt',<目标文本>,'active')` —— P0-1 goal store 同时持久化。

**判定 PASS**：goal_set 确认 + facts 出现 category=goal + session_goals 持久化 = 双源一致，B-10 fire-and-forget GC-safe 修复在生产真机有效（若 GC 丢条，facts 表会空）。

---

## 🔑 windows-mcp 圣杯突破（关键技术记录，供后续 TC 复用）

**WebView2/Chromium 键盘也必须 SendInput（INPUT type=1），不止鼠标！**
- 现象：SendInput **鼠标**点击 WebView2 按钮有效，但 `keybd_event` 的 Ctrl+V/Enter **无效**（输入框始终空）。
- 根因：WebView2 像忽略老 `mouse_event` 一样**忽略老 `keybd_event`**。
- 解法：键盘用 `SendInput` + `KEYBDINPUT`（type=1，wVk=0x11/0x56，dwFlags=2 为 keyup）。粘贴瞬间生效，slash 补全立即识别。
- 另一关键：WebView2 content 按钮点击前必须 `SetForegroundWindow`+`BringWindowToTop` 激活窗口，否则合成点击不被 content 处理（「+ 新项目」首次失败、激活后成功印证）。
- 坐标系：虚拟桌面 8640×2160 物理像素；主截图区映射 ×3.0（displayed×3=physical，右键命中桌宠经验证）；DPI 150%，`SetProcessDPIAware()` 后 SetCursorPos 用物理像素，GetCursorPos 回读一致。

---

## 🟡 剩余 TC（真机执行框架已打通，待续跑会话）

下列 TC 依赖**分钟级真 LLM agent 多轮运行**（gpt-5.5），单 TC 需多次截图轮询 + 可能撞 write_file 权限门。本会话已打通交互 harness（SendInput 圣杯键鼠 + Code session 创建 + /goal 真发送），但完整跑这些需独立专项会话的上下文预算：

| TC | 状态 | 备注 |
|---|---|---|
| TC-4.5 B-10 双写钩 | ✅ **PASS（本会话真机）** | 见上 |
| TC-3.1 伪完成→拦→重规划→二次PPT | 🟡 待续 | agent 已就该目标「思考中」运行；需等多轮 + ArtifactCard 截图 |
| TC-5.3 技能自创卡（招牌） | 🟡 待续 | 需 ≥5 工具多步任务触发 skill_candidate_proposed → 前端卡（接线已修+复评100%+前端 tsc 绿，但真机弹卡截图待补） |
| TC-4.1 重启后跨会话召回 | 🟡 待续 | 需会话A决策→重启→会话B召回 |
| TC-5.1/5.2 强匹配载入/压缩追目标 | 🟡 待续 | FP-2 已证 goal anchor；FP-5 自动披露待真机 |
| 其余 TC-3.x/4.x/5.x | 🟡 待续 | 见 testcase/goal-completion-manual-test.md |

> **续跑入口**：app 可保持运行（agent 正跑 code-cn8im6rt 会话的目标任务）；交互用本报告「圣杯突破」节的 SendInput 键鼠方法。Code Mode 窗 handle 每次启动变，用 Snapshot 查 `DeskPet · Code Mode`。输入框物理坐标随窗口，需 Snapshot 重取。
