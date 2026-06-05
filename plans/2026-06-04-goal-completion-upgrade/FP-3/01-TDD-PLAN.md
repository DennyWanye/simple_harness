# FP-3「自我纠错闭环」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps `- [ ]`. **最高风险 FP**（触 verify-gate 护城河 + 三套重试上限合并）。

**Goal:** verify 不过不再「停」，而是结构化反思 → 真重规划 → 自动重试；verify 接 goal_text 客观对照；高后果目标外部 evaluator 交叉验证。出厂 verify_gate prod **off→shadow**。

**Architecture:** 建立在 FP-1 `store.get_goal_text(sid)`（已落地，sync None-safe）+ 现有 VerifyGate/GoalChecker/outcome_verifier/auto_resume 之上。**不新造第四套计数器**——复用 §7 账本（freeze §2：attempt=per-session 共享额度）。

**Tech Stack:** 同 FP-1/2。测试用绝对路径 venv python（cd 被 harness 剥离）。

**权威依据：** [02-P0-2-self-correction-execution.md](../02-P0-2-self-correction-execution.md)（逐 WI blueprint）+ [FP-1/00-CONTRACT-FREEZE.md §2](../FP-1/00-CONTRACT-FREEZE.md)（§7 账本：per-session 共享、verify_exhausted 进 frozenset、死循环上界）+ [00-PLAN §14.2](../00-PLAN.md)（consult_ephemeral_subagent→async 冻结）。

---

## 冻结签名（并行前锁）
| 符号 | 冻结 | 现状 |
|---|---|---|
| `VerifyGate.consult_ephemeral_subagent` | 改 **async**；`check` 保持 sync；callable → `Callable[[Any],Awaitable[bool]]` | [verify_gate.py:394](../../../backend/deskpet/agent/verify_gate.py) sync, :418 `bool(self.ephemeral_subagent({...}))` |
| `VerifyGate.check` | 加 `*, goal_text: str\|None=None`（BC 默认 None） | :284 |
| `verify_exhausted` | ADD 进 `_AUTO_RESUME_TRIGGER_REASONS` frozenset；agent_loop verify 三层耗尽处 emit + return | [auto_resume.py:96](../../../backend/agent/auto_resume.py) |
| attempt 计数 | **per-session 共享额度**（verify_exhausted 与 max_iterations/circuit_open 共享 max_attempts=2，freeze §2.2）；上界测试按共享额度断言 | session_activity in-memory |
| flag 默认 | `structured_reflection=False`/`external_evaluator=False`（dev on/prod off）；`verify_gate_mode` prod off→**shadow** | config.py:250/259 |

---

## Task 0（build order 第 1 步 / T6）: 现有 ephemeral mock 改 AsyncMock
**Files:** `backend/tests/test_verify_gate.py`
- [ ] grep `test_verify_gate.py` 里所有 mock `ephemeral_subagent` 的 sync mock，改为 `AsyncMock`（consult_ephemeral_subagent 将转 async → `await sync_mock()` 会 `TypeError: object bool can't be used in 'await'`）。
- [ ] 跑 `test_verify_gate.py` 确认改后仍全绿（此时 consult 还是 sync，AsyncMock 不影响 sync 调用？——若现有测试直接调 sync consult，先只改"被 await 的 mock"；Task 3 转 async 后再核）。**这是 2.2 build order 第 1 步，最易漏的回归。**

---

## Task 1: WI-2.1 结构化反思字段（reflection.py + 注入点 + flag）
**Files:** `backend/deskpet/agent/reflection.py`(新建) · `backend/agent/agent_loop.py`（selfcheck tier2/3 + verify rebound 注入）· `backend/config.py`(VerifierConfig.structured_reflection) · 测试 `backend/tests/test_structured_reflection.py`(新建)

依据 blueprint [02 §WI-2.1](../02-P0-2-self-correction-execution.md)。
- [ ] **Step 1 失败测试**：`parse_reflection` 对 3 形态（裸 JSON / ```json fenced / 前后夹文本）提全 5 字段（error_analysis/execution_critique/task_replanning/next_action/confidence）；缺字段→safe-fail 默认（confidence 缺→0.5）不抛；flag off→agent_loop 不注入反思 system msg；flag on + verify 失败→注入指令含 `task_replanning`。
- [ ] **Step 2-4**：实现 `StructuredReflection` dataclass + `_REFLECTION_INSTRUCTION` + `parse_reflection`（复用 goal_checker `_extract_json` 三级 fallback）；agent_loop 在 verify rebound + selfcheck tier2/3 两点注入（**只这两个本就回灌点，零正常路径开销**）；config flag。BC：flag off 逐字节同旧。
- [ ] **Step 5** commit（子代理不 commit，主线统一）。

---

## Task 2: WI-2.3 verify 接 goal_text（VerifyGate×GoalChecker 合流 + 人格禁入）
**Files:** `verify_gate.py`(check 加 goal_text + VerifyOutcome.goal_alignment) · `goal_checker.py`(抽 build_alignment_prompt) · `agent/agent_loop.py`(两 if 分支串联 + 完成判定公式) · 测试 `backend/tests/test_verify_goal_alignment.py`(新建)

依据 [02 §WI-2.3](../02-P0-2-self-correction-execution.md)。
- [ ] **Step 1 失败测试**：伪完成(声称满足但无 receipt/文件)→`aligned=False` 拦；**未来时不误判**("我将要生成 PPT"→不算 done)；`goal_text=None`→check 行为字节同现状(BC)；**`test_goal_judgment_no_persona_leak`**：喂含"用户很期待/请让他开心"上下文→`aligned` 仍只由客观证据定（人格注入不改判定）；客观证据齐 + goal_checker done→mark_done。
- [ ] **Step 2-4**：`check(..., *, goal_text=None)` + `GoalAlignment` dataclass；`build_alignment_prompt(goal_text, artifacts, claims)` 硬编码输入白名单（**不喂 persona/情绪/偏好**）+ 反谄媚前缀"你是冷静验收员，只依客观证据，宁可判未完成也不假装完成"；agent_loop 串联 [A]VerifyGate→[B]outcome_verifier→[C]GoalChecker（喂客观证据非裸文本）；**完成判定公式写死**：`goal_done == verify.passed AND no outcome fail AND goal_checker.done(over objective evidence)`。VG-INVARIANT 扩展：goal_text 非 None 时完成判定须含 ≥1 objective_evidence 或标 evidence_unavailable。
- [ ] **Step 5** commit。

---

## Task 3: WI-2.2 真重规划重试（ephemeral async + rebound + §7 账本）
**Files:** `verify_gate.py`(consult_ephemeral_subagent→async + make_ephemeral_verifier) · `agent/agent_loop.py:984-1050`(rebound 升级 + verify_exhausted emit) · `auto_resume.py:96`(frozenset 加 verify_exhausted) · `main.py build_agent`(接 ephemeral callable) · 测试 `backend/tests/test_verify_replan_retry.py`(新建)

依据 [02 §WI-2.2](../02-P0-2-self-correction-execution.md) + freeze §2.3 死循环账本。
- [ ] **Step 1 失败测试**：伪完成→第1轮 rebound 含反思指令 + goal 对照(2.3)；mock 第2轮真调工具补 receipt→verify 通过(**校验不过→改方案→二次通过**)；`task_replanning` 与上轮 difflib ratio>0.85→判复述→升级 ephemeral 不耗第2次 nudge(`verify_replan_stagnant` metric)；ephemeral=None→第2次失败保守 fail(BC)；ephemeral 返 pass→救援放行 log `ephemeral_rescued`；**三层全失败→emit `verify_exhausted` 且 `is_auto_resume_trigger("verify_exhausted") is True`**；**§7 死循环上界测试**：永久失败任务断言总 LLM 调用 ≤ 明确上界 + 按 per-session 共享额度封顶 + 最终优雅降级不卡死。
- [ ] **Step 2-4**：consult_ephemeral_subagent 改 `async`，agent_loop:992 调用点加 `await`，callable→async；`make_ephemeral_verifier(llm_call)`（payload→bool，prompt 问"claim 是否被某工具调用同义覆盖"）；rebound 拼"原失败清单 + goal_text 对照(2.3) + 反思指令(2.1)"三段；difflib 比对防假重试；`verify_exhausted` 进 frozenset + agent_loop verify 三层耗尽处 emit ErrorEvent(reason=verify_exhausted)+return（**不在 goal_checker 块重复 emit**）；build_agent 接 `ephemeral_llm=_make_str_llm_call(...,max_tokens=256)`。
- [ ] **Step 5** commit。

---

## Task 4: WI-2.4 外部 evaluator（仅高后果触发）
**Files:** `backend/deskpet/agent/external_evaluator.py`(新建) · `agent/agent_loop.py`(FinalEvent 前高后果 gate) · `main.py build_agent` · `config.py`(external_evaluator flag) · 测试 `backend/tests/test_external_evaluator.py`(新建)

依据 [02 §WI-2.4](../02-P0-2-self-correction-execution.md)。
- [ ] **Step 1 失败测试**：`is_high_consequence_goal` 命中写盘工具/支付关键词/≥5工具/confidence<0.3；纯 read 不命中；evaluator verdict=revise→emit `evaluator_revise`触发 auto_resume；provider=None→safe-fail 放行+metric；**普通目标→evaluator 0 调用**(断言)。
- [ ] **Step 2-4**：`ExternalEvaluator` + `is_high_consequence_goal` + `_HIGH_CONSEQUENCE_KEYWORDS`/工具集；evaluator prompt（输入 original_goal+artifacts+objective_evidence，**不含人格**；输出 quality_score/issues/verdict）；仅高后果 + 每 goal 最多 1 次；反谄媚隔离同 2.3。
- [ ] **Step 5** commit。

---

## Task 5: R-T3 LLM 失败降级矩阵 + R-T6 shadow 可观测
**Files:** verify/goal_checker/evaluator 降级路径 + receipt 扩字段 · 测试
- [ ] **R-T3**（[00-PLAN §15.4](../00-PLAN.md)）：每个 LLM 依赖点失败不抛、返安全降级、记降级事实。关键第三态：**GoalChecker 超时→降级到仅客观证据判定 + 标 `goal_check=skipped`（不默认 pass/fail）**；2.1 反思畸形→降级机械 nudge；2.4 evaluator 超时→高后果保守拦+提示手动确认。故障注入单测（超时/畸形 JSON）。
- [ ] **R-T6**（[00-PLAN §15.5](../00-PLAN.md)）：receipt 扩 `shadow_verdict`/`actual_outcome`/降级率/`verify_latency_p95`；每个降级事实进 metrics。go/no-go strict 标准记文档（连续 N≥200：误杀<2% ∧ 漏放<5% ∧ 降级率<10% ∧ p95<3s）。
- [ ] commit。

---

## Task 6: 🚦手测门（windows-mcp 真机）+ 出厂迁移 + STATUS
> 复用 FP-1/2 harness。证据存 `plans/manual-results-2026-06-*-FP-3/`。**写权限门**：本 FP 手测门涉及真产物(ppt_create)，需处理 code-mode 权限审批（见 FP-2 BLOCKERS 经验：dev 可加 write 自动批准 / 或真机点批准）。

- [ ] **MR-2.2 真重规划（🔴 真模拟人）**：对话"帮我生成 PPT 周报"→构造第一次只口头声称没真调 ppt_create→verify 拦→自动重规划→第二轮真调 ppt_create→真 .pptx 落盘→ArtifactCard 渲染(截图链)。pass^k=5 后端 + 真机截图。
- [ ] **MR-2.3 verify 接 goal（🔴/后端）**：伪完成被拦/真完成放行可截图；`no_persona_leak` 后端 pass^k。
- [ ] **MR-2.x relay 故障注入（后端）**：超时/畸形 JSON 走对降级分支（R-T3）。
- [ ] **出厂迁移**：prod `verify_gate_mode` off→**shadow**（开始采集 R-T6 指标）；`structured_reflection` 跟随点亮。
- [ ] 全绿 → roadmap §3 FP-3 行打勾 + STATUS §3/§4 + 日期。

---

## 整体 build order
```
T6(ephemeral mock→AsyncMock 先) → 2.1 反思字段(独立) → 2.3 verify接goal(依赖FP-1 get_goal_text,已就绪)
→ 2.2 真重试(依赖2.1+2.3, ephemeral async + §7账本) → 2.4 evaluator(依赖2.2+2.3)
→ R-T3降级矩阵 + R-T6 shadow埋点 → 手测门 → prod off→shadow 迁移
```
**并行性**：2.1 与 2.3 独立可并行（reflection schema vs verify_gate goal 接线，不同文件）；2.2 必须在 2.1+2.3 后（消费其产出）；2.4 最后。T6 必须最先（防 await bool 回归）。
