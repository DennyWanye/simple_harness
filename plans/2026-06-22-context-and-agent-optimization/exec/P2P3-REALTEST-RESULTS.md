# P2+P3 批量 windows-mcp 真测结果 — 2026-06-23（relay 恢复后）

> 环境：Tauri dev + `DESKPET_BACKEND_DIR` 注入最新 P3 backend（日志确认 Dev python）。relay(chinzy.com) 从持续 500 间歇恢复到多数 200 OK（streaming 仍偶发 504→非流式回退）。测试 flag 经 dev config 开启，测完已还原。

## ✅ 真机 PASS

### OC-2 背压累计指标 — ✅ **PASS**（截图存档）
发"深度研究AI芯片市场"→deepresearch fan-out（log `subagent_scheduled kind=research run_id=default.dr-0/1/2...`）→ **SubagentProgressPanel 实测显示累计区「峰值 2 · 累计入队 6 · 拒绝 0」**——正是 peak_concurrent/total_queued/total_rejected 真机渲染。子代理并发 5/6 + dr-0~5。截图 `testcase/2026-06-22-context-agent-opt-P2/screenshots/OC-2-cumulative-metrics.png`。

### CC-3 /run·/verify 内置 skill — ✅ **PASS**
开 `[skills] knowledge_enabled=true` 重启 → live backend `skill.reload_ok count=17`（默认 12 → +5 知识 skill）→ `run-deskpet`/`verify-deskpet`（user-invocable:false 知识 skill）在 knowledge_enabled 下加载（builtin 目录两 SKILL.md 确在）。默认 OFF（knowledge_enabled=False）时不进 snapshot=BC。

### OH-2 对话式 pin（P1 项，本批再确认）— ✅
"记住我做PPT喜欢深色主题/周一开周会提醒周报"→LLM 真调 `memory_write{"pinned":true,"text":"用户平时做 PPT 喜欢深色主题..."}` + `{"...周一上午开周会，希望以后提醒准备周报..."}`——对话式记忆/pin 路径真机工作。

## 🐛 真测抓出真生产 bug（高价值）

### OH-4 记忆 curation nudge — 抓出生产死链 → 已 spawn 任务修
开 `[memory.v2] curation_nudge=true, every_n=2` + auto_learnings 真机测：聊 2 轮后 curation **未触发**（无 `oh4_curation_nudge` 日志）。精确定位根因：**`MemoryCurator` 在生产永不构造**——构造在 `lifespan` startup（main.py:2644），但 `facts_store` 在 `build_agent`（per-session,:1871）才 register，lifespan 时 facts_store=None → 构造条件不成立 → curator=None。**12 个单测全绿但因直接构造 curator，测不到此 wiring 死链**——这正是 windows-mcp 真测的价值（"单测绿但生产死链"）。
- 试过 `service_context.get("facts_store")` 无效（lifespan 时同样 None），已还原 main.py 到提交态。
- 正确修复需重排 lifespan/per-session 时序，已 spawn 任务 `task_455ba81e`（带完整诊断 + 接线层单测要求，防复发）。
- 旁证：memory_write 路径真机工作（偏好能捕获），仅 curation nudge 这条断。

## 🟡 unit-verified / best-effort（本批未单独真机触发）
- **OC-1** depth 上界：strip 已防嵌套→显式 depth 拒绝分支真机几乎走不到，20 单测已验，flag OFF=BC 默认态运行。
- **CC-2** plan 只读：需 code 模式 + plan_confirm_gate 挂起窗口抢跑写工具，24 单测已验。
- **1B-3/1B-4/1B-5** compaction 三件套：依赖压缩真触发（companion 模式压缩天然 inert,见 compaction-trigger-diagnosis.md），96 单测已验，OFF=BC 默认态运行。
- **TG-2** 审批聚合：默认 enabled=false，需开面板+并发 2+ 权限请求，vitest14 已验。
- **CC-5** learnings：依赖 OH-4 curator（同死链阻塞），随 OH-4 修复一并真机验。

## 小结
relay 恢复后批量真测：**OC-2 + CC-3 真机 PASS**，OH-2 pin 再确认；**OH-4 真测抓出生产死链**（curator 永不构造）已 spawn 修复任务——这是本轮真测最大价值。其余 flag-gated 项单测充分 + OFF=BC 默认态即运行态。compaction 相关项因 companion 压缩天然 inert（已诊断 by-design）真机难触发。
