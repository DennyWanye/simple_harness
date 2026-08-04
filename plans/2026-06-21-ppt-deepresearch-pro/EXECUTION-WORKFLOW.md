# PPT Pro — 自主执行 Runbook（定时任务专用，自包含）

> 本文件由 2026-06-22 01:40(+0800) 的定时任务触发执行。触发的会话**无本对话记忆**，
> 一切以本文件 + plan 文档为准。**严格按本 runbook 执行，不要问用户、不要少做、不要降级测试。**

## 0. 你的身份与总目标
你是 Lead。把 [`00-PLAN.md`](./00-PLAN.md)（v1.3 LOCKED，已过 6 轮对抗 EXECUTABLE-AS-IS）**100% 实现并真机验收通过**。
新工具 `ppt_pro`：主题 → deepresearch 充分调研 → 拟大纲 → FP-5 风格独立大纲卡确认(可改/取消/复用历史) → 优先 gpt-image-2 惊艳生图 / 连不上回退模板。
配套真机用例见 [`02-manual-test.md`](./02-manual-test.md)。

## 1. 铁律（HARD，违反=未完成）
1. **写代码的子代理默认用 codex gpt-5.5**：`codex exec -m gpt-5.5 "<自包含任务:绝对路径+约束+验收点>"`（全局 CLAUDE.md 约定）。Claude 做 Lead（规划/审查/集成/真机验收），codex 做 Expert（实现）。同文件串行、独立文件并行。
2. **不可少做功能**：plan 的 11 个 WI + 全部 F1-F4 + 21 条风险闭环，一个不漏。有歧义/坑 → 你按最佳实践自己定，**不要问用户**。
3. **真机手测不可省略、不可降级**：必须用 windows-mcp **模拟人工在真实页面鼠标点击 + 键盘输入**。禁止用 pytest/WebSocket 注入/import 查状态/cmdkey 等任何「等价证明」替代（见根 `CLAUDE.md` §手工测试纪律 + 全局 §GUI 测试纪律）。每个动作前先 declare `坐标=(x,y)|动作|期望`，截图存盘，抓 backend log 判定。失败 retry ≥3 种 workaround 才能标「环境受限」并等用户确认（但本次无人值守 → 环境受限项详细记录、不算通过）。
4. **每完成一个验收即更新 STATUS**：`STATUS/PPT.md` + `STATUS/status.md`（根 `CLAUDE.md` §STATUS 更新纪律）。
5. **新文件即建即 `git add` + commit**（防沙箱回滚，见全局 memory）。master 直接开发（项目策略）。中文文件只用 Edit/Write（勿用 PowerShell Get-Content/Set-Content 链，会乱码）。

## 2. 真机环境准备（windows-mcp 测试前必做，按项目踩坑）
- **不要手动起 backend**：Tauri 自己 spawn backend 到 `DESKPET_BACKEND_PORT`。手动起会端口双占（os error 10048）。
- **跑 worktree/当前码必须给 Tauri 注入** `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend` + `DESKPET_PYTHON=<主 .venv python>`，日志确认出现 `[backend_launch] Dev python=... backend_dir=...`（否则跑的是旧 frozen exe = 白测）。
- **不要再手动起 vite**：`tauri dev` 的 `beforeDevCommand` 已起唯一 vite。
- **关桌宠后必 `taskkill /F /IM deskpet.exe`**（+ 杀残留 vite/backend），否则孤儿进程占端口（feedback_tauri_dev_cleanup）。
- **relay 登录**：dev 自动登录已配（`import.meta.env.DEV` + `tauri-app/.env.local` 注入 `VITE_DEV_RELAY_EMAIL/PASSWORD`，来自 gitignored `LOCAL-DEV-CREDENTIALS.md`）。若登录框阻塞，确认 `.env.local` 存在且 vite 重启过。LLM key 经 keychain。
- backend 日志：structlog 走 stderr → 落进 tauri dev 的重定向 log，抓那个 log。

## 3. 配置开关
dev config 开 `[ppt].pro_enabled=true`（默认就是 True）。其余 pro_* 默认值见 plan WI-0。

## 4. 执行结构（按 plan §5 依赖图分阶段）
把 plan 切成下列**阶段**，**每个阶段都要走 §5 的三步循环**（不是最后才测）：
- **阶段 S0**：WI-0 配置（`backend/config.py` 走 `standalone_config_section`，顺手修现有 `_cfg.config.raw` 坏读法）。
- **阶段 S1（后端核心，可并行 codex 多任务）**：
  - 线 A：WI-1 调研封装（import `_DEPTH_PRESETS`，deep 档，默认落盘）。
  - 线 B：WI-2 拟纲（双模式 image_prompt+充实 bullets / prev_slides 修订 / `_fallback_minimal_outline` / `_outline_to_markdown`）。
  - 线 C：WI-5 probe + WI-6b error_kind 细分（connectivity/model_unavailable/auth/quota/content/unknown，优先 status_code→error.code→多语言文案兜底+真实样例测试）+ WI-6 回退编排（`_degrade_to_template` 清 image_prompt + `_autofill_with_connectivity_gate` 全图失败兜底 + `_render_pro` + `ppt_create` 加 `skip_image_gen`）。
  - 线 D：WI-3 大纲卡后端（新 `ppt_outline_store.py`：`PPTOutlineWaiters` 幂等 pop/resolve + `ppt_outline_history` flag-gated 表 + save/list/get/mark；main.py `_ppt_outline_propose` + `_broadcast_control` 广播 + `ppt_outline_decision` 回灌首次生效+广播 `ppt_outline_resolved` + 启动 proposed→expired）。
- **阶段 S2（前端，可与 S1 并行）**：WI-4 `PPTOutlineCard.tsx`（确认/修改 textarea/取消/历史复用 + outline_id 去重 + 同进程 reload set_messages 保留 + 双面板挂载）+ ws.ts/sessionsStore.ts 接线。
- **阶段 S3（总装）**：WI-7 `ppt_pro` async handler 秒回 + 独立 `asyncio.create_task` 编排（identity-guard finally 清理 + 同主题 already_running/异主题 `_ppt_pro_cancel` 替换 + 分阶段限时 research360/confirm1800/render `wait_for(max(600,pages*120))` + `/stop` 取消）+ `set_ppt_pro_services` 注入（在 `_ppt_outline_propose` 定义后）+ `_register_ppt_pro_tool`（pro_enabled 才注册，timeout_seconds=60 秒回）+ §2.5 编排骨架。
- **阶段 S4**：WI-10 receipt/artifact 通道（`artifact_pusher` 发合成 `tool_result` envelope 带 artifacts[] 落 SessionDB + `receipt_reporter` emit_receipt）+ WI-8 SKILL.md（路由 ppt_pro + 修 stale 模板名 + LLM 见 status 不重复调）。

> 行号以实现时 grep 为准（plan 行号是 2026-06-21 快照）。每条 WI 的伪代码/验收见 plan 对应 WI 段。

## 5. 每个阶段的三步循环（CRITICAL，逐阶段、不可跳）
对**上面每一个阶段 S0..S4**，依次做：
**步骤①（完成度评估到 100%）**：本阶段所有 WI 实现 + 跑该阶段单测/`-k ppt`/research/clarify/tsc/vitest 自测全绿 → **派一个子代理**对照 plan 评估「本阶段是否 100% 完成」。子代理给出缺口清单 → 你补完 → **再派子代理评估**，**循环到子代理判定 100%** 为止。
**步骤②（手测文档 + 你迭代覆盖率）**：**派一个子代理**为本阶段功能生成**详细手工测试文档**（windows-mcp 真机用例：每条含坐标/动作/期望/log 证据/判定 + 边界 + 失败注入）。**你评估覆盖率**（能否测出本阶段各种 bug 与边界）→ 不够 → 让子代理迭代补 → **反复直到你确认覆盖充分**。文档存 `testcase/2026-06-22-ppt-pro-<阶段>/`，并在 `testcase/index.md` 登记一行。（可复用已有 [`02-manual-test.md`](./02-manual-test.md) 作种子。）
**步骤③（windows-mcp 真机手测 + 修 + 复测）**：你**严格按该手测文档**用 windows-mcp **模拟人工点击+输入**逐条测。**有问题就修（codex 实现/你集成）→ 复测，直到文档全部通过**。证据（截图+log）存 `plans/manual-results-2026-06-22-ppt-pro/`。
然后进入下一阶段，重复①②③。

## 6. 全部阶段完成后（总验收）
1. **派子代理对整个代码库评估**：plan 全部任务完成度是否 100%？给缺口 → 你补完 → **再派子代理评估**，循环到 100%。
2. **全功能 windows-mcp 真机手测**：覆盖 F1-F4 全链路 + plan §8/`02-manual-test.md` 的 TC-1~9（含 ★必过：TC-1 惊艳全链路 / TC-4 连不上回退模板 / TC-7 preempt 不杀确认链路 / TC-9 历史复用+持久化）。**全部模拟人工点击+输入**，全部测试成功；有问题修复后复测，直到全绿。
3. 更新 `STATUS/PPT.md` + `STATUS/status.md`（§3 模块完成度 + §4 里程碑），commit。

## 7. 完成标准（DONE 定义）
- plan 11 WI + F1-F4 全实现，子代理终评 100%。
- 单测/`-k ppt`/research/clarify/vitest/tsc 全绿。
- 每阶段 + 总验收的 windows-mcp 真机手测**全部 PASS（真人模拟点击/输入证据齐全）**；★必过项无一 FAIL。
- STATUS 已更新；所有改动已 commit。
- 产出一份总结报告 `plans/manual-results-2026-06-22-ppt-pro/RESULTS.md`（各阶段评估 100% 记录 + 手测证据索引 + 环境受限项如实标注）。

## 8. 无人值守注意
- 本次无人值守：凡需用户确认的（写权限门/确认卡），用项目既有的「本会话始终允许」等价机制放行（如 `permissions_auto_mode.json={enabled:true}`，见历史手测做法）；大纲卡确认在真机测试里由 windows-mcp **真点击**完成（这是被测功能本身）。
- 真出图要花钱（gpt-image-2 ~$0.15/张）——这是被测功能，按计划真出。F4 回退用例可临时令 relay images 不可达制造（改错地址/probe mock）测回退，测完恢复。
- 若 relay/网络硬不可用导致真机链路跑不起来：详细记录环境受限于 RESULTS.md，**不得**用脚本/协议层假装通过。
