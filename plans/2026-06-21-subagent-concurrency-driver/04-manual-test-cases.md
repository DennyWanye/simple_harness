# 04-manual-test-cases — windows-mcp 真机 E2E 用例

> 配套 [`00-PRD.md`](./00-PRD.md) §8 准入。**纪律（CLAUDE.md HARD CONSTRAINT）**：
> 每 case 必须 windows-mcp/CDP 真坐标点击 + 真输入 + 截图 + backend 日志铁证；
> **不接受** WebSocket 直注 / pytest / import 查内部状态 当 UI 测试证据（feedback_real_e2e）。
> 每动作先 declare：`坐标=(x,y) | 动作 | 期望`。截图存 `plans/manual-results-<date>-subagent/screenshots/`。

## 前置

- `DESKPET_DEV_MODE=1` + dev 自动登录（`tauri-app/.env.local` 凭据，2026-06-21 `2263ee1`）
- 跑当前 worktree 码：`DESKPET_BACKEND_DIR=<worktree>/backend` + `DESKPET_PYTHON=<.venv python>`（CLAUDE.md 坑 #8），日志确认 `[backend_launch] Dev python=...`
- config.toml 开 flag（按测试阶段）：`[features] subagent_driver=true`（P1）/ `agent_team=true`（P2）/ `subagent_nonblocking=true`（P3）
- 关键日志锚点：`subagent_scheduled kind=...`、`subagent_progress status=...`、`spawn_team team=...`、`subagent_cancel_all n=...`

---

## V1 ★（一票否决）多事务异构并发 — P1

| 字段 | 内容 |
|---|---|
| 前置 | `subagent_driver=true`，clean 重启 |
| 动作 | 桌宠输入框真输入：「同时帮我做三件事：①深度调研 2025 钠离子电池现状 ②查一下小米 SU7 最新售价 ③把『season 复盘』要点列成提纲」→ Enter |
| 期望 | LLM 调 `agent_parallel`，3 子任务 kind=research/web/general（或 doc）；backend 日志 `subagent_scheduled kind=research`、`kind=web`、`kind=general` **三条不同 kind**；3 个并发跑（时间重叠）；3 份结果聚合回**一条**桌宠消息；进度卡片真机可见（截图） |
| 判定 | 日志见 3 不同 kind + 并发重叠 + 聚合单消息 + 进度可见 → PASS |

## V2 ★ 同构任务池 — P2

| 字段 | 内容 |
|---|---|
| 前置 | `agent_team=true`；准备 4 个待办（如 4 句话各翻译） |
| 动作 | 桌宠输入：「把这 4 句话分别翻成英文：<4 句>，用团队并行做」 |
| 期望 | LLM 调 `spawn_team(task_descriptions=[4], kind=general/code)`；日志 `spawn_team team=team-...`、teammate claim 4 次、池清零（all done）；聚合 4 译文返回 |
| 判定 | 日志见 team 真起 + claim 池清零 + 4 结果 → PASS |

## V3 背压（global cap） — P1

| 字段 | 内容 |
|---|---|
| 前置 | `subagent_driver=true`，`[agent.concurrency] global_concurrency=4` |
| 动作 | 桌宠输入一次列 6 个独立子任务（如「分别查 6 个城市今天天气」） |
| 期望 | 日志：4 个 `status=running` 先出现、2 个 `status=queued` 等待，随后排队的转 running；**6 个全部 completed 不丢** |
| 判定 | 日志见 4 跑 2 排队 + 全完成 → PASS |

## V4 ★ flag OFF 字节级 BC

| 字段 | 内容 |
|---|---|
| 前置 | 三 flag 全 **false**（出厂默认） |
| 动作 | ①跑 `cd backend && python -m pytest`（agent_parallel/agent/team 现有套）②桌宠正常对话 + 触发一次 agent_parallel（若旧 flag 开） |
| 期望 | 现有测试全绿、无新失败；agent_parallel 走原扁平 gather（日志无 `subagent_scheduled`）；行为与现状一致 |
| 判定 | 测试 diff=0 + 无调度日志 → PASS |

## V5 ★ 取消级联 — P3

| 字段 | 内容 |
|---|---|
| 前置 | `subagent_nonblocking=true` |
| 动作 | 桌宠输入「后台帮我调研 3 个竞品」→ `spawn_subagents(background=true)` 立即返回 → 子代理 running 中 → 点桌宠/面板 `/stop`（或停止按钮） |
| 期望 | 日志 `subagent_cancel_all n=3`；3 个活子代理 Task.cancel；不再有后续 LLM 调用烧 token |
| 判定 | 日志见 cancel_all + 子代理停 → PASS |

---

## 边角用例

| ID | 场景 | 期望 |
|---|---|---|
| E-1 | 未知 kind（LLM 传 "translate"） | 回退 general 只读集，任务仍跑不崩 |
| E-2 | 单子代理失败（工具报错） | 该 result ok=false，其余正常聚合（错误隔离） |
| E-3 | relay 504 期间派并发 | 沿用现有重试链；最终失败优雅入 result.error，桌宠不卡死 |
| E-4 | 非阻塞结果回流 | spawn 后继续闲聊，下一回合边界桌宠冒泡「子代理完成」（回合边界注入，不中途打断） |
| E-5 | spawn_team 超时 | `timed_out=true`，返回已完成部分（不全丢） |
| E-6 | 进度卡片与普通 tool 卡共存 | 两者独立渲染不冲突（R7） |

---

## 报告格式（每 case）

```
case:    V1 / V3 / E-2 ...
flag:    subagent_driver=true ...
坐标:    (x, y) [物理像素]
动作:    输入「...」→ Enter
截图:    screenshots/<case>.png
log 证据: subagent_scheduled kind=research run_id=... | subagent_progress status=running ×4 status=queued ×2
判定:    PASS / FAIL / RETRY-N / SKIP（带理由 + 等用户确认）
```
