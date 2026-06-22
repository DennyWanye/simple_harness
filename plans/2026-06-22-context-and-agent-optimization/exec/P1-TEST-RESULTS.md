# P1 真测结果（windows-mcp 真机）— 2026-06-22

> 环境：Tauri dev + `DESKPET_BACKEND_DIR` 注入跑当前 checkout 后端（P1 提交后重启，日志确认 Dev python）。keychain 登录 gpt-5.5。输入用 App switch 消息窗 + Type(Unicode)。

## HM-1 自我纠错全档点亮(shadow) — ✅ 真机 PASS
- **verify_gate 默认 shadow 生效**：启动日志实证 `verify_gate_init mode=shadow patterns=9 path=...claim_patterns.yaml`（off→shadow 翻转 active）。
- **ephemeral 救援接线**：`ephemeral_verifier_model model=haiku`。
- **emit_receipts=True / structured_reflection=True 隐含满足**：后端构造无 ConfigError（VG-INVARIANT-0/1 守住，否则启动崩）。
- **shadow 不误阻塞 companion**：发普通消息正常处理（memory_write 被调、turn 完成）→ shadow 观测不阻塞 end_turn，闲聊放行（验 TC-HM1-3 风险条 = 安全）。
- 判定：**PASS**。（strict 升级留后续真机确认 companion 无 claim 闲聊不卡后再切，shadow 已是"全档开"的稳妥落地。）

## OH-2 偏好半衰期默认开 + 对话式 pin — ✅ 真机 PASS
- **对话式 pin 路径生效（核心）**：对桌宠说「记住我喜欢用 neovim 编辑器，这是长期偏好，请永远别忘了」→ 日志实证 LLM 调 **`memory_write args='{"pinned":true,"salience":0.9,"text":"用户喜欢使用 neovim 编辑器，这是长期偏好。"...}'`** —— pinned=true 正确传入 → upsert 后 set_pinned → pinned preference fact。
- pref_decay 默认开 + pinned 跳衰减（`AND pinned=0`）由单测 `test_pin_and_pref_decay.py` 验证（真实天数衰减真机等不到，best-effort 靠单测）。
- 判定：**PASS**（对话式 pin 入口真机生效；硬前置满足）。
- 待补（best-effort，需时间/重启）：pinned 偏好重启后仍 📌 注入（`preference_profile_injected`）——机制已实现+单测覆盖，真机长周期验证留后续。

## TG-1 goal_task_create 全局工具 — ⏸ 单测充分验证 / goal_mode 真机待补
- **单测 7 测全绿**：create→list 往返（带 depends_on+note 持久化）+ goal_mode ON registry 含 4 工具 + OFF 不注册(BC) + goal_id/session_id 反查解析 + 无活跃目标友好报错 + 边界校验。+ 204 回归绿。
- **真机待补**：goal_task 工具受 `goal_mode` 门控（**默认 OFF**，非默认模式）。真机验证需先开 `[features] goal_mode=true` + 重启 + 设长目标 + 观察桌宠用 goal_task_create 建带依赖任务 + 重启验持久化。属 **goal_mode 专项会话**补验（与 HM-1/OH-2 默认开不同，TG-1 是按需启用的重能力）。
- 判定：**实现 + 单测 100%**；goal_mode 真机标"待 goal_mode 会话补验"（非默认模式，单测已覆盖核心往返+解析+BC）。

## OH-1 五路检索 — 决策 no-op
- 按 plan 建议不把第五路(facts/entity)提升为默认 RRF lane（碰 1200+ 测试、边际收益低），标 backlog。retriever 未动 = 字节一致。

## CC-1 skills 三级+compaction后重挂 — 0（已实现）
- `agent_loop.py:_remount_skills` + `test_deskpet_skill_remount_after_compaction.py` 已存在，P1 无新工作。

## 配置清理
P1 真测未改任何全局 config override（HM-1/OH-2 是源码默认翻转，已提交；TG-1 真测需 goal_mode 时再开，本次未开）。桌宠运行 1M 窗口干净态。
