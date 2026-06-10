# FP-2 抗漂移闭环 — windows-mcp 真机手测结果

> 按 [testcase/goal-completion/FP-2-抗漂移-manual-test.md](../../testcase/goal-completion/FP-2-抗漂移-manual-test.md) 逐 TC 执行。
> 执行日期: 2026-06-10 ｜ 方式: windows-mcp 真机(Click/Clipboard/ctrl+v) + CDP 9333 + 后端 pytest + log/DB 核对
> 环境: master(含本次 token_budget 两轮修复), 源码 backend, userdata-a, goal_mode+compaction_enabled=true

## 结果汇总

| TC | 范围 | 判定 | 关键证据 |
|---|---|---|---|
| TC-2.1 | 压缩后不漂移(招牌) | **FAIL → 挖出 2 个真缺陷,修 2 刀,遗留第 3 刀** | 见下「TC-2.1 详述」。行为侧 `wi13_goal_anchor_injected iter=5/10` 多次真机捕获(goal 锚定机制本体工作);**`p1_4_compaction_fired` 真机始终未触发**(缺陷所致,不伪造) |
| TC-2.2 | 任务图+TodoPanel | **BLOCKED/待实现(确认)** | grep main.py `TaskGraphStore`=0;前端 `goal_tasks`=0 文件 — 与 testcase 预期判定一致 |
| TC-2.3 | DAG ready 调度 | **PASS** | `test_dag_claim_skips_blocked_task`+`test_task_graph` 11 passed |
| TC-2.4 | 跨 agent 共享任务图 | **PASS(后端)** | `test_teammate_tools_taskgraph` 6 passed(工具 5→7 含 goal_task_*);产品链路 BLOCKED(testcase 已知) |
| TC-2.5 | handoff Charter 带父 goal | **PASS(后端)** | `test_spawn_team_goal` 6 passed(`## Parent Goal (do not drift)`) |
| TC-2.6 | off-goal 分流 | **PASS(后端,marker 分流)** | 同上套件覆盖 `_classify_by_goal`;LLM judge BLOCKED(按 testcase 预期) |
| TC-2.7 | resume 续原目标(窄版) | **PASS(单测+接线证据)** | `test_auto_resume_goal` 3 passed;`main.py:2522` `_goal_text_getter_for_resume`+`:2537` 注入实证;真机故障触发未制造(诚实标 🟡) |
| TC-2.8 | 并发 claim 不双占 | **PASS pass^k 5/5** | `test_concurrent_claim_no_double_claim` 连跑 5 次全过 |
| TC-2.9 | GD 漂移信号 | **BLOCKED/待实现(确认)** | grep `GD_actions|GD_inaction`=0 — 与 testcase 预期判定一致 |
| TC-2.10 | compaction 前置分支 | **PASS(双分支)** | enabled: boot `wi4_0_compaction_enabled context_window=32000 threshold=0.75`;disabled(config false 重启): 无 enabled log + 无 fired |

**FP-2: 7 PASS + 2 BLOCKED(按预期确认) + 1 FAIL(TC-2.1,真缺陷产出)。**

## TC-2.1 详述 — 真机挖出的 compaction 估算缺陷(本次手测最大价值)

**现象**: compaction enabled + 代码路径接电(agent_loop:712),但真机灌中文/markdown 上下文到 **real prompt_tokens=28k、33k、34k(超 32k 窗)** 时 `should_compress` 始终 False、`p1_4_compaction_fired` 不出现。

**根因 1(已修)**: `token_budget.estimate_tokens` 用 char/4 启发式 —— **CJK 1 字≈1 token 被低估 4 倍**(1000 汉字估 250)。DeskPet 是中文产品,重度中文会话 compaction 等效失效。
→ 修复: `_CJK_RE` 加权(CJK=4 等效 chars)。TDD 红→绿: 新增 3 测试(1000 汉字 250→1000)。

**根因 2(已校准)**: markdown/路径/代码密集英文实测 ~3 char/token(README/PLAN),char/4 仍低估 30%+(真机 34008 vs 估 <24000)。
→ 校准: ASCII ×8/7(≈3.5 char/token);边界测试样本 220k→190k 同步;回归 124 passed。

**遗留(第 3 刀建议,记 backlog)**: 两轮系数校准后真机 32.9k 仍未达估算阈值 —— 系数永远追不上内容分布。正确修法 = **用 relay 返回的真实 `prompt_tokens` 做反馈回路**(上一轮真实值校正下一轮估算/直接作为 should_compress 输入)。另一发现: compaction 检查只在 iteration 边界,单次长生成(essay 21k→33k)中途不查;新 turn 起步被 last_n 裁剪 → 触发窗口实际很窄。

**行为侧(与漂移直接相关)**: `wi13_goal_anchor_injected`(WI-1.3 决策点 goal 锚定)在 iter=5/10 等多次真机捕获 —— 「执行中不忘原目标」的机制主体在工作;追问「我最初让你做什么」后 LLM 行为仍围绕原任务推进(未漂移到无关事务)。

## 执行中额外发现(非本 FP 范围,如实记录)

| # | 发现 | 性质 |
|---|---|---|
| 1 | **skill 候选确认卡 pending 时,同 session 后续 chat 消息被静默吞掉且 reject 后不重放**(user 气泡显示已发、backend 不处理;点「忽略」后消息流立即恢复) | **真 bug**(4.3c 阻塞面),待修 |
| 2 | relay HTTP 500 → `FactExtractor.extract LLM failed` warning、主流程不崩 | R-3 降级矩阵真实证据 ✅ |
| 3 | `skill_candidate_confirm_received decision=reject` 链路工作 | FP-5 TC-5.4 同源证据 ✅ |
| 4 | real 34k > 32k 标注窗口,relay 仍 200 OK | gpt-5.5 实际窗口比 builtin 标注大/或 relay 端截断;model_info 校准可考虑 |

## 修改的代码(本 FP 手测期间)
- `backend/agent/token_budget.py`: CJK 加权 + ASCII 3.5 校准(2 commit 待提)
- `backend/tests/test_token_budget_per_model.py`: +3 CJK 测试
- `backend/tests/test_p5s2_token_budget.py`: 边界样本 220k→190k 校准
