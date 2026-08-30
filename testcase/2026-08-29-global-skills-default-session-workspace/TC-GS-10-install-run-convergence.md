---
id: TC-GS-10
purpose: Verify Auto Skill installation retains durable authorization evidence and every failure converges the SDK Run and UI
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A9, TO-A10, TO-R3, TO-R6, HM-TO-R2, HM-TO-R4]
tags: [skill-install, auto-authorization, error-contract, run-convergence]
entrypoint: chat skill install and primary conversation health recovery
revision: 3
---

# TC-GS-10 — Auto Skill 安装与 Run/UI 收敛

## 步骤与预期

| 步骤 | 真人操作/故障注入 | 预期结果 |
|---:|---|---|
| 1 | 全新 Auto profile 从 Chat 安装合法固定 Skill 仓库。 | Git/包预检先完成；无人工确认；saga先 CAS 到 auto-decision-bound，再绑定真实 effect/handoff；resolver只在 handoff-committed时返回 auto-v1 receipt。intent、Manager receipt、runtime verification与activation全部可关联，UI只在 terminal success后显示成功。 |
| 2 | 新 lane 从 Chat 传入非 HTTPS GitHub URL和无 `SKILL.md` 仓库。 | 返回固定 `code/public_message/retryable/failure_receipt_ref/allowed_actions`；failure key不含 run/call/effect；相同 input+generation跨 replay/restart只执行一次 source resolve且返回 byte-equivalent rejection；零 publish/active binding。 |
| 3 | 用受支持 fault seam 令最后一次 Provider outcome 为 unknown，再发 status query。 | ack带匹配 query_id、递增/不旧于 observed 的 run_version、`state=waiting`、稳定 waiting_reason/allowed_actions；UI不再显示“正在安装”，保留 active_run_id并提供恢复或停止入口。 |
| 4 | 触发前端健康超时并选择/执行停止；注入 late stale ack和 cancel/completion race。 | 5秒无ack仅显示恢复态、不本地 idle；正式 SDK cancel收到 terminal ack后一次性清 inflight/active_run_id。stale ack被忽略，竞态按最高 durable run_version只产生一个 terminal projection。 |
| 5 | 在失败后于同一永久主对话启动新的 fresh root Run。 | 新 Run 正常执行；失败请求没有污染 global catalog、primary conversation 或 TaskScope 状态。 |

## 决定性证据

- UI 前后截图、root/primary-conversation/task-scope/effect/intent correlation、auto-v1 receipt hash payload、authorization saga与 versioned Run ledger终态、status/cancel query IDs及versions、错误 payload、source resolve count、publish/binding零增量和基于 monotonic clock 的 5 秒 UI 收敛时间戳。
