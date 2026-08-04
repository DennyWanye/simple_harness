# FP-2「抗漂移闭环」手工测试报告（windows-mcp 真机 E2E）

> 日期：2026-06-05 ｜ 证据：[`plans/manual-results-2026-06-05-FP-2/`](../../manual-results-2026-06-05-FP-2/)
> 全 FP flag dev 配置：features.compaction_enabled=true · verify_gate_mode=strict · goal_facts=true · persona_inject=true。

## 环境
- 启动：DESKPET_CONFIG=.tmp/fp1-config.toml（全 FP flag 开）+ DESKPET_BACKEND_DIR + DESKPET_PYTHON（跑源码）。
- windows-mcp SendInput 真坐标点击 + Clipboard 中文输入 + DPI 物理像素。
- **执行期抓修 2 个真 bug**（见下「真机抓的 bug」）。

## MR-1.3 抗漂移 re-anchor（🔴 真机）— PASS

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 设目标 | Code 面板 .tmp 全 chat InputBar → Clipboard `/goal 帮我逐个审查并总结 .tmp 项目的配置文件FP2测试` → 发送 | ✅ goal_set「已设置目标：…（上限 10 轮）」（[01 截图](../../manual-results-2026-06-05-FP-2/screenshots/01-codepanel-state.png)） |
| 2 多步任务 | InputBar → Clipboard「用 grep 依次单独搜索 goal_mode/features/backend/memory/voice 5 个词各一次」→ Enter | ✅ agent 跑多轮 ReAct（13 LLM completions + grep 工具）（[03 截图](../../manual-results-2026-06-05-FP-2/screenshots/03-agent-multistep-reanchor.png) agent 工具执行中） |
| 3 re-anchor 验证 | grep backend log | ✅ **`wi13_goal_anchor_injected sid=code-ks4v3wdq tid=task_260605122651 iter=5`** — 决策点 re-anchor 在 **iter=5** 精确触发（`_GOAL_ANCHOR_EVERY=5`），在活跃 goal 会话的真多步任务中注入 `[目标锚定]`（[log 证据](../../manual-results-2026-06-05-FP-2/re-anchor-log-evidence.txt)） |

**硬证据**（re-anchor-log-evidence.txt）：
```
companion_code_v1_goal_mode_ready                 ← goal_mode ON
wi4_0_compaction_enabled context_window=32000 threshold=0.75   ← FP-5 4.0 接通(同栈)
goal_store_load_persisted restored=N              ← FP-1 持久化(同栈)
verify_gate_init mode=strict ...                  ← FP-3 verify strict(同栈, bonus)
wi13_goal_anchor_injected sid=code-ks4v3wdq iter=5  ← ★ FP-2 决策点 re-anchor 真机触发
LLM completions: 13 (多轮 ReAct)
```
**判定：PASS** — 真模拟人：真 UI 设目标 → 真 UI 发多步任务 → agent 真跑 ≥5 轮 ReAct → 决策点 re-anchor 在 iter=5 真机注入（log 实证）。WI-1.2 task图/WI-1.4 handoff/WI-1.5 resume 的并发 claim/charter/respawn 属后端单测项（55 焦点 + 280 回归全绿，照做不砍）。

## 真机抓的 bug（修到全过）
1. **context_compressor 缺 ServiceContext 白名单**（WI-4.0 接线 bug）：真机首跑 agent 报 `Unknown service 'context_compressor'`，compaction_enabled=true 时所有 code-mode 任务派发崩。**修**：`context.py _VALID_SERVICES` 加 `context_compressor` + 回归断言（commit）。单测直接注入 compressor 绕过 service_context 故没抓到——真机才暴露。
2. **端口冲突 crash-loop**（坑 #7 orphan 累积）：多次重启累积的 orphan backend python 互抢 8100（startup complete ×6 + Errno 10048）→ supervisor give up「启动失败」弹窗。**解**：杀全部 deskpet backend orphan + 释放端口 + 单次干净重启（0 端口冲突）。

## 截图清单
| 文件 | 内容 |
|---|---|
| 00-pet-toolbar.png | 桌宠 toolbar（定位 code-mode 图标） |
| 01-codepanel-state.png | /goal 设目标 → 「已设置目标…」确认 |
| 03-agent-multistep-reanchor.png | agent 多步任务执行中（re-anchor iter5 所在运行） |

## 结论
**FP-2 手测门 PASS** — re-anchor 决策点真机 iter=5 触发；全 FP flag 栈（compaction/verify-strict/goal-persist/persona）同栈共跑无 Unknown service。
