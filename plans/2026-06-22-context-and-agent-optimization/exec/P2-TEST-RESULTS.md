# P2 真测结果（windows-mcp 真机）— 2026-06-23

> 环境：Tauri dev + `DESKPET_BACKEND_DIR` 注入跑当前 checkout 后端（P2 提交后重启，日志确认 Dev python）。

## ⚠️ 关键环境受限：relay (chinzy.com) HTTP 500 持续故障
真测当时 **中转站 `chinzy.com/v1/chat/completions` 持续返回 HTTP 500 Internal Server Error**（日志连续 ≥10 次 500，2 次发送 deepresearch + 60s 恢复轮询均未回 200）。这是**外部 relay 故障，非 P2 代码问题**，阻断所有依赖 LLM 的真测（deepresearch fan-out / OH-4 curation nudge / 任何 agent 活动）。日志锚点：`agent_loop_stream_failed_falling_back error=LLM HTTP 500`。

> 同一 relay 故障也影响用户正常使用桌宠（所有对话都撞 500），不限于本测试。**relay 恢复后需补跑 P2 windows-mcp 真测。**

## 实现 + 评估状态（不受 relay 影响，已完成）
4 个真缺口实现 + 独立子代理评估 **P2=100%** + 单测全绿：
| WI | 单测 | OFF=BC | 真机锚点(relay恢复后验) |
|---|---|---|---|
| **OH-4** 记忆nudge | 12 passed | ✅ flag curation_nudge默认False→curator不构造 | 开flag+聊8轮→`oh4_curation_nudge`+facts新增 |
| **CC-2** plan只读 | 24 passed | ✅ flag plan_read_only默认False→不拦 | 开flag+code模式计划期→写工具deny+`plan_read_only_enter/exit` |
| **OC-1** depth上界 | 20 passed | ✅ flag subagent_explicit_depth默认False→靠strip | 开flag+多agent→`SpawnDepthExceeded` |
| **OC-2** 背压指标 | 9 passed+vitest20 | ✅ 纯增观测 | deepresearch fan-out→面板峰值/累计入队/拒绝 |

## OC-2 部分真机锚点已捕获（relay 500 前）
- 重启后日志确认 **`deepresearch subagent fanout ENABLED (scheduler wired)`** —— OC-2 调度器接线在真机生效（fan-out 已 wired）。
- 但累计指标的面板展示需 fan-out 真跑（LLM 依赖），被 relay 500 阻断，未能截到面板累计区。OC-2 前端展示由 vitest 20（含 metrics 缺省/覆盖/渲染）+ tsc 0 验证。

## 判定
- **实现 + 单测 + 独立评估 = 100%**（不受 relay 影响）。
- **windows-mcp 真机 = relay 故障 env-limited**：4 项 OFF=BC 默认态即当前运行态（无需 relay 验证默认）；ON 态/fan-out 观测待 relay 恢复补跑。OC-2 scheduler wiring 已在 log 确认。
- 这不是降低测试方式，是外部 relay HTTP 500 持续故障导致 LLM 链路不可用（已 ≥3 次重试/轮询）。relay 恢复后我可补跑。
