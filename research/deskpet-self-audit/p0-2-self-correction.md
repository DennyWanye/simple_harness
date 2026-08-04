# DeskPet 自我纠错闭环(P0-2) 现状审计

审计日期：2026-06-04
审计范围：`backend/` 下 agent_loop、verify_gate、outcome_verifier、auto_resume、supervisor 等关键文件
审计员：code-explorer sub-agent（只读，不修改代码）

---

## 结论速览表

| # | 检查项 | 状态 | 核心证据 |
|---|---|---|---|
| 1 | verify gate 失败后行为 | 🟡 部分 | 有 nudge 回灌 + continue loop，但默认 mode="off"，strict 才真阻断 |
| 2 | 结构化自我反思字段 | ❌ 缺口 | 无 error_analysis/execution_critique/replan 字段；仅有自由文本 selfcheck 消息 |
| 3 | 自动重试机制 | ✅ 已有 | 工具级：circuit breaker(CLOSED/OPEN/HALF_OPEN)；目标级：AutoResumeOrchestrator(max_attempts=2) |
| 4 | 外部验证/多 persona 交叉批判 | ❌ 缺口 | 无独立 evaluator；supervisor 只做"卡住检测"，不做"完成度交叉验证" |
| 5 | verify 重述原目标对照产物 | 🟡 部分 | VerifyGate 只核对 claim vs receipt(工具调没调)；不拉原始 goal text；GoalChecker 有 goal_text 但不接 verify_gate |

---

## 逐条详述

### 条目 1：verify gate 失败后的行为

**结论：🟡 部分实现**

verify gate 失败时有明确的"回灌 system message + continue loop"机制，代码在 `backend/agent/agent_loop.py:955-1045`。

失败分支流程：
1. `verify_gate.check(assistant_text, ledger)` 返回 `v_outcome.passed=False`
2. 累积 `verify_nudges_used += 1`
3. 若达到 `max_verify_nudges`（默认 2），调 `verify_gate.consult_ephemeral_subagent()`
4. ephemeral 仍 fail → 回灌 D8 schema system message，`continue` 重进下一 LLM 轮次
5. 超过 `max_verify_nudges` 且 ephemeral 也 fail → 才真退出（emit FinalEvent or 继续到 max_iterations 报错）

**核心缺口**：
- 默认 `verify_gate_mode="off"`（`backend/config.py:250`），真实部署不一定开启
- `ephemeral_subagent` 接口实现为 stub（`verify_gate.py:414`）：`if self.ephemeral_subagent is None: return False`，也就是说第 2 次失败就会一直用 fallback，实质只有一次 nudge 有效
- 失败后的回灌只告诉 LLM"你声称了什么但没有 receipt"，没有帮 LLM 重规划（缺少 replan 提示）

关键文件：
- `backend/agent/agent_loop.py:955-1045`（verify gate 回灌逻辑）
- `backend/deskpet/agent/verify_gate.py:260-425`（VerifyGate 实现）
- `backend/config.py:250`（`verify_gate_mode = "off"` 默认值）

---

### 条目 2：结构化自我反思字段

**结论：❌ 缺口**

在整个 backend 目录中，搜索 `error_analysis`、`execution_critique`、`task_replanning`、`replan` 均无命中。

现有的"反思"机制：
1. **selfcheck tier 消息**（`backend/agent/agent_loop.py:85-138`）：纯自由文本 system 消息，无结构化字段。tier1（iter=10）触发"反思 3 行"，tier2（iter=20）"警告"，tier3（iter=30+）"强制停"
2. **verify gate 回灌**（同上）：固定模板文本，不要求 LLM 产出结构化反思
3. **supervisor 诊断**（`backend/agent/supervisor.py`）：supervisor 输出是 JSON，但这是 supervisor 自己的诊断，不是 agent 的自我反思

**缺口描述**：
- agent 在 end_turn 前没有被要求产出 `{"error_analysis": ..., "replanning": ...}` 形式的结构化字段
- agent 失败重试时，新一轮 LLM 只看到 system message nudge，没有强制结构化分析步骤
- 这意味着 LLM 可能在重规划时仍然走同一条"习惯性路径"，而非真正分析失败原因

---

### 条目 3：自动重试机制

**结论：✅ 已有（两层，但层次不同）**

**工具级重试（circuit breaker）**：
- `backend/agent/circuit_breaker.py` 实现了完整的 CLOSED→OPEN→HALF_OPEN 三态机
- threshold=3 次失败开路；cooldown_seconds=60 后进入 HALF_OPEN 探测
- 这是"工具熔断"，防止 LLM 对同一坏工具无限重试
- 配合 `PermanentToolError`（`backend/agent/errors.py`）：永久错误直接 break loop

**目标级重规划重试（AutoResumeOrchestrator）**：
- `backend/agent/auto_resume.py` 实现了完整的重试 orchestrator
- 触发条件：`max_iterations / permanent_tool_error / circuit_open / hallucination`
- 流程：检查 attempt counter → supervisor.diagnose() → 注入 hint → spawn fresh chat task
- max_attempts=2（默认），`auto_resume_attempts` 存 SessionActivityStore

**verify gate 失败的重试**：
- `max_verify_nudges=2`（agent_loop 构造参数），触发后 continue loop 而非 spawn fresh task
- 这是"原 loop 内重试"，不是目标级重规划

**注意事项**：
- AutoResumeOrchestrator 的 supervisor hint 是 LLM 生成的自由文本，质量依赖 LLM
- verify gate 失败的重试上限（2 次）到达后，实质上会进入下一个 max_iterations 计数，最终超时

关键文件：
- `backend/agent/circuit_breaker.py`（工具熔断）
- `backend/agent/auto_resume.py`（目标级自动重启）
- `backend/agent/agent_loop.py:544-547`（verify_nudges_used 计数）

---

### 条目 4：外部验证/多 persona 交叉批判

**结论：❌ 缺口**

全库搜索 `evaluator`、`judge`、`cross_check`、`cross-check` 未见独立 evaluator 角色。

现有机制不满足"独立验证"：
1. **supervisor**（`backend/agent/supervisor.py`）：只做"卡住检测"（dead loop / 长时间无活动），不验证任务完成度，不对产物质量做 judge
2. **GoalChecker**（`backend/deskpet/agent/goal_checker.py`）：用同一 LLM provider 判断目标是否达成，并非独立 persona/模型；prompt 也只看最近 5 轮 assistant 消息
3. **agent_parallel tool**（`backend/deskpet/tools/code_tools/agent_parallel_tool.py`）：并发派多个 subagent，但是并行做不同子任务（hub-and-spoke），不是"对同一产物交叉批判"
4. **outcome_verifier**（`backend/deskpet/agent/outcome_verifier.py`）：4 件套（file_exists/git_diff/build/test），是机械校验，不是语义级 judge

**完整缺口**：没有任何代码实现"把 LLM A 的产出送给 LLM B 做独立质量评分"的机制。

---

### 条目 5：verify 是否重述原目标对照产物

**结论：🟡 部分（两组件独立存在，但未打通）**

**VerifyGate 的工作方式**（`backend/deskpet/agent/verify_gate.py:284-316`）：
- `check(assistant_text, ledger)` 接受当前 assistant 回复 + ReceiptLedger
- 只做 claim 提取（LLM 说"我创建了 X.pptx"）+ receipt 对账（工具确实调过 ppt_create）
- **不接受 original_goal 参数**，不把原始用户目标和产物做语义比对
- Verifier 本身可能漂移：如果 LLM 声称做了"原目标的等价替代"，VerifyGate 只看 receipt 是否存在，不判断是否真的满足了目标

**GoalChecker 的工作方式**（`backend/deskpet/agent/goal_checker.py`）：
- `check(goal_text, working_msgs)` 接受原始 goal text + 工作消息
- 确实把原目标和 LLM 产出进行 LLM-judged 比对
- 但这是**独立的 goal_store/goal_checker 路径**，通过 `/goal` 命令触发，与 verify_gate 完全分离

**打通缺口**：
- VerifyGate 校验"工具有没有调"，GoalChecker 校验"目标有没有达成"，两者并联不串联
- 如果 verify_gate mode=strict 失败时，没有同时带着 original_goal 做语义比对，只能知道"receipt 缺失"，不知道"目标偏离"

关键文件：
- `backend/deskpet/agent/verify_gate.py:284`（`check` 不接 goal 参数）
- `backend/deskpet/agent/goal_checker.py:145`（`check(goal_text, working_msgs)` 有 goal 但独立）
- `backend/agent/agent_loop.py:959-1050`（verify gate block） vs `1059-1122`（goal checker block，两个独立 if 分支）

---

## 给 plan 建议

基于以上审计，P0-2「自我纠错闭环」的主要建设方向：

### P0（最高优先）：打通 verify 失败 → 带目标重规划

当前：verify gate 失败只注入 "you claimed X but no receipt" 的文本。
建议：失败时同时带入 `session_goal.text`（若有）构造 replan 提示，格式：
```
[verify-gate blocked]
原始目标: {goal_text}
声称完成: {unmatched_claims}
缺失 receipt: {reason}
请重新规划: 先完成工具调用，再 end_turn
```
文件：`backend/agent/agent_loop.py:1001-1013`（修改 rebound 消息模板）

### P1：强制结构化反思 schema（agent 自我反思字段）

在 verify gate / selfcheck tier2+ 触发时，要求 LLM 下一轮必须产出：
```json
{"error_analysis": "...", "revised_plan": "...", "next_action": "..."}
```
再继续工具调用。可用 response_format=json_object 约束，或在 system message 中强制要求 JSON first-token。

### P2：ephemeral_subagent 真实接入

当前 `consult_ephemeral_subagent` 是 stub（返回 False）。
接入方式：复用 `goal_checker.llm_call` 模式，传 failed_claims + ledger 给小 LLM judge。
文件：`backend/deskpet/agent/verify_gate.py:394-425`

### P3（中期）：GoalChecker × VerifyGate 串联

verify gate strict 失败后，若有 active goal，自动调 GoalChecker 判断"失败是否影响目标达成"，把 GoalChecker 的 hint 合并进 rebound system message。
目前两者在 agent_loop 里是顺序的独立 if 分支（行 955 和 1059），可改为 verify 失败时同时触发 goal check。

### P4（长期）：独立 evaluator persona

高价值任务（如代码生成、文档生成）结束时，用第二个 LLM provider 做 evaluator 角色：
- 输入：original_goal + produced_artifacts + conversation summary
- 输出：`{"quality_score": 0-10, "issues": [...], "verdict": "pass|revise"}`
- verdict=revise → 触发 auto_resume 重规划

---

_文件路径：`G:\projects\deskpet\research\deskpet-self-audit\p0-2-self-correction.md`_
