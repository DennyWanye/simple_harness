# P0-2 自我纠错闭环 — 可执行实现 Blueprint

> 状态：**v1 — 待 spec 评审 / 进实现**
> 日期：2026-06-04 ｜ 作者：架构师子代理（只读代码后产出）
> 上游：[`00-PLAN.md`](./00-PLAN.md) §3（P0-2）、[`research/deskpet-self-audit/p0-2-self-correction.md`](../../research/deskpet-self-audit/p0-2-self-correction.md)
> 对标：hermes agentic JSON-mode 七段反思 ｜ best-practices §3 自验证/反假完成
> 锁定决策：2.4 外部 evaluator **纳入本轮但仅高后果目标触发**；P0-2 落地后 prod `verify_gate_mode` **off → shadow**。

---

## 0. 现状速览（已 Read 真实代码确认）

| 设施 | 文件:行 | 现状 | P0-2 用法 |
|---|---|---|---|
| verify 失败 nudge+continue | `backend/agent/agent_loop.py:955-1050` | 有回灌+continue，默认 `mode="off"` 整段跳过；`max_verify_nudges=2`；失败模板只说"call the missing tool"，**无 replan 引导** | 2.1/2.2 改这里的 rebound 段 |
| selfcheck tier1/2/3 | `agent_loop.py:65-138, 653-660` | iter%10 注纯文本三档，**无结构化字段** | 2.1 在 tier2+ 注结构化 schema 指令 |
| VerifyGate | `deskpet/agent/verify_gate.py` | claim 提取 + ledger 对账；`check(assistant_text, ledger)` **不接 goal_text**；`mode` ∈ off/shadow/strict；`consult_ephemeral_subagent` 走 `self.ephemeral_subagent` callable，**注入处恒为 None → stub**（`verify_gate.py:414`、`build_agent` 未传） | 2.3 加 `goal_text` 参数；2.2/2.4 落地 ephemeral callable |
| GoalChecker | `deskpet/agent/goal_checker.py` | `async check(goal_text, working_msgs)→(done,hint)`；LLM-judged；3 级 JSON 容错；safe-fail 返 `(True,"checker_error")` | 2.3 与 VerifyGate 合流 |
| outcome_verifier 4 件套 | `deskpet/agent/outcome_verifier.py` | file_exists/git_diff/build/test 机械校验，`run_outcome_verifiers()`；缺 toolchain skip、60s 超时不阻 | 2.3 客观证据来源（receipt 之外的第二层） |
| SessionGoalStore | `deskpet/agent/goal_store.py` | **纯内存**（P0-1 WI-1.1 要落 SessionDB）；`get(sid)→SessionGoal{text,done,iterations_used,max_iterations}` | 2.3 goal_text 来源（依赖 P0-1） |
| auto_resume（目标级重试） | `backend/agent/auto_resume.py` | `AutoResumeOrchestrator.handle_failure`；触发集 `{max_iterations,permanent_tool_error,circuit_open,hallucination}`；`max_attempts=2`；spawn fresh task 注 supervisor hint | 2.2 复用上限纪律；新增 `verify_exhausted` 触发 |
| circuit_breaker（工具级重试） | `backend/agent/circuit_breaker.py` | CLOSED/OPEN/HALF_OPEN，threshold=3 | 2.2 保持不动，仅引用其"上限"哲学 |
| build_agent 工厂 | `backend/main.py:640-747` | 接电 verify_gate/receipt_store/goal_store/checker；**ephemeral_subagent 未传** | 2.2/2.4 接电点 |
| LLM 字符串 call 适配 | `backend/main.py:601 _make_str_llm_call` | `(prompt:str)→str` async；provider=None 返 None | 2.1/2.2/2.4 复用，构造 ephemeral/evaluator 的 llm_call |
| config 默认 | `backend/config.py:250` | `verify_gate_mode="off"`；`max_verify_nudges=2`；VG-INVARIANT-0/1 校验 | 7. off→shadow 迁移点 |

**一句话现状**：DeskPet 已有「校验不过就回灌文本 nudge + continue」和「目标级 spawn 重试」两套独立机制，但缺三样：(a) 失败时**强制结构化反思字段**（只有自由文本），(b) **由反思驱动的一次真重规划重试**（现 ephemeral 是 stub、rebound 不含 replan），(c) **verify 接 goal_text**（VerifyGate 与 GoalChecker 两 if 分支并联不串联）。

---

## WI-2.1 — 结构化反思字段（强制 LLM 产出 error_analysis / execution_critique / task_replanning）

### 1. 目标文件
- **新建** `backend/deskpet/agent/reflection.py` — schema dataclass + prompt builder + JSON 解析（复用 goal_checker 的 3 级 fallback 思路）。
- **改** `backend/agent/agent_loop.py`：
  - 新增模块级常量 `_REFLECTION_INSTRUCTION`（结构化 schema 指令文本）。
  - selfcheck tier2/tier3 段（`agent_loop.py:653-660`）+ verify-gate rebound 段（`:1007-1013`）注入反思指令并解析回写结果。
- **改** `backend/config.py`：`VerifierConfig` 加 `structured_reflection: bool = False`（dev on / prod off，flag 渐进点亮）。

### 2. 结构化反思字段 schema

`reflection.py` 定义（借 hermes 七段，**裁剪到 DeskPet 必要的 3 段 + 2 辅助**，避免 token 膨胀）：

```python
@dataclass
class StructuredReflection:
    error_analysis: str        # 上一轮为何没达成/校验不过（根因，非复述报错）
    execution_critique: str    # 对自己已走路径的批判（是否走了惯性短路径）
    task_replanning: str       # 据批判产出的【新方案】（必须与上轮不同的具体动作）
    next_action: str           # 下一步要调的【具体工具+参数意图】（驱动 2.2 重试）
    confidence: float          # 0-1，自评新方案成功概率（<0.3 → 升级到 2.4 evaluator）
```

**怎么强制 LLM 产出**（按可靠性排序，DeskPet 走中转 gpt-5.5，不假定原生 JSON-mode 一定可用）：
1. **首选：system 指令 + 首 token 约束**。在 rebound system message 末尾追加 `_REFLECTION_INSTRUCTION`：
   > 你上一轮 end_turn 被拦截。在下一轮回复的**最开头**，必须先输出一个 JSON 对象（放在 ```json 代码块里），字段：`error_analysis / execution_critique / task_replanning / next_action / confidence`，**然后**再执行 task_replanning 里写的工具调用。不要复述缺失的 receipt，要给出与上轮不同的具体新方案。
2. **解析**：`reflection.py:parse_reflection(assistant_text)` 复用 goal_checker `_extract_json` 三级 fallback（直 parse → fenced block → 首个 `{...}`）。解析失败 → 不阻断（safe-fail），记 `reflection_parse_failed` metric，按旧文本 nudge 行为兜底。
3. **不强依赖 provider `response_format`**：中转站对 `response_format=json_object` 支持不一；本字段走 prompt 约束 + 宽松解析，与现有 goal_checker 同源（已验证可跑）。
4. **注入位置**：只在「verify 失败 rebound」与「selfcheck tier2/tier3」两个**已经要回灌**的点追加，不在正常轮次注入（零额外 LLM 调用、零正常路径开销）。

### 3. replan → 重试控制流（本 WI 只产字段，驱动在 2.2）
2.1 的产出是「`StructuredReflection` 被解析出来并落进 working_messages / metrics」。它**不自己触发重试**——把 `task_replanning`+`next_action` 作为下一轮 LLM 的输入，是 2.2 的「真重试」载体。2.1 的验收边界 = 「失败时字段存在且非空」。

### 4. ephemeral_subagent — 见 WI-2.2（共用一个落地）

### 5. verify 接 goal_text — 见 WI-2.3

### 6. 外部 evaluator — 见 WI-2.4

### 7. 边界 / 防回归 / flag
- flag `structured_reflection=False` 默认 → `_REFLECTION_INSTRUCTION` 不注入，selfcheck/verify 走原纯文本路径（**字节级 BC**）。
- 解析失败不得阻断 dispatch（safe-fail，对齐 verify_gate/goal_checker 现有纪律）。
- `confidence` 字段缺失 → 默认 0.5（不误触发 2.4）。
- **防回归红线（横切 §7.1）**：反思指令禁止出现"如果做不到就说已完成/换个说法过关"类表述——人格层 token 不得渗进反思（见 2.3 §5 的技术保证）。

### 8. 测试计划
- **单测** `tests/test_structured_reflection.py`：
  - `parse_reflection` 对 3 种 LLM 输出形态（裸 JSON / fenced / 前后夹文本）都能提全 5 字段。
  - 缺字段 → safe-fail 默认值，不抛。
  - flag off → agent_loop 不注入指令（断言 working_messages 无反思 system msg）。
  - flag on + verify 失败 → 注入指令文本含 `task_replanning`。
- **真机 E2E**（windows-mcp，对接 §8 HARD 纪律）：构造「声称生成 PPT 但没真生成」→ verify 拦 → 看 backend log 出现解析到的 `task_replanning` 字段（非纯文本 nudge）。
- **pass^k**：同一伪完成场景跑 k=5，每次都要解析出结构化字段（不能偶尔 fallback 到纯文本）。

### 9. build order
2.1 先于 2.2（2.2 消费其字段），与 2.3 可并行（schema 与 goal 接线解耦）。

---

## WI-2.2 — verify 失败 → 真重规划重试（task_replanning 驱动一次真重试 + ephemeral 落地）

### 1. 目标文件
- **改** `backend/agent/agent_loop.py:984-1050`：verify-gate 失败分支——把"只回灌缺 receipt 文本"升级为"回灌 2.1 反思指令 + 让下一轮带新方案重试"。
- **改** `backend/deskpet/agent/verify_gate.py:394-425`：`consult_ephemeral_subagent` 仍走 `self.ephemeral_subagent` callable（接口不动），但**真接一个 callable 进来**。
- **改** `backend/main.py:680-747 build_agent`：构造 `ephemeral_subagent` callable 并传给 `VerifyGate(...)`。
- **改** `backend/agent/auto_resume.py:96-101`：触发集加 `"verify_exhausted"`（verify 两次重试+ephemeral 都失败 → 升级目标级 spawn 重试）。

### 2. 结构化反思 schema — 复用 2.1（不重复定义）

### 3. replan → 重试的控制流（核心）

**三层重试上限，复用现有纪律，不新造计数器**：

```
第 0 层（工具级）  circuit_breaker：单工具连错 3 次 → OPEN（不动）
第 1 层（loop 内）  verify rebound：max_verify_nudges=2 次带【新方案】重进下一轮
                    └ 每次失败注入 2.1 结构化反思指令 → 下一轮 LLM 必须先产
                      task_replanning，再按新方案调工具 → 这就是"一次真重试"
第 2 层（loop 内救援）verify_nudges_used 达上限 → consult_ephemeral_subagent
                    （独立小 LLM 复核 ledger+failed_claims，给 final_verdict）
第 3 层（目标级）   ephemeral 仍 fail → emit ErrorEvent(reason="verify_exhausted")
                    → auto_resume.handle_failure 接管 → spawn fresh task
                    （max_attempts=2，带 supervisor hint）
```

**与现有 nudge 机制的合并/替换**（不是新增并行机制，是**增强**现有 rebound）：
- 现 rebound（`:1007-1013`）文本 = "call the missing tool"。
- 新 rebound = **原失败清单 + goal_text 对照（来自 2.3） + 2.1 反思指令**，三段拼一条 system message。
- `continue` 行为不变（仍重进下一轮），但下一轮 LLM 拿到的是「结构化重规划要求」而非「机械提醒」→ 这就把"最多 2 次文本 nudge"升级成"最多 2 次**带新方案的真重试**"。
- **不改 max_verify_nudges 默认值（2）**——复用现有上限，避免本地桌宠死循环。

**关键判定：什么叫"真重试"而非"复述"**（防 §7 假完成）：
- 第 2 次 rebound 时，比对本轮 `task_replanning` 与上一轮是否文本高度相似（简单 difflib ratio > 0.85 → 判为"复述未换方案"）→ 直接升级到第 2 层 ephemeral，不浪费第 2 次 nudge。落 `verify_replan_stagnant` metric。

### 4. ephemeral_subagent stub 落地

**方案：复用 `_make_str_llm_call` + 一个独立 verifier prompt**（不照搬 hermes 自动执行 Python；DeskPet 单机不引子进程沙箱）。

`backend/deskpet/agent/verify_gate.py` 新增模块函数：
```python
def make_ephemeral_verifier(llm_call):  # llm_call: (prompt:str)->str async
    """返回 VerifyGate.ephemeral_subagent 期望的 callable:
       payload{ledger_size, failed_claims, assistant_text} -> bool(final_verdict)
       True = 救援放行（确实做了，regex 漏判）/ False = 确实没做。"""
```
- prompt 契约：给 LLM「assistant 声称 X / ledger 里有哪些 ok 的工具调用 / 哪些 claim 没匹配上」，问「这些 claim 是否其实被某个工具调用覆盖了（同义改写）？」输出 `{"verdict":"pass|fail","reason":"..."}`。
- `make_ephemeral_verifier` 是 **sync wrapper**：`consult_ephemeral_subagent` 当前签名是 sync callable，内部用 `asyncio.run_coroutine_threadsafe` 或把 ephemeral 改成在 agent_loop 的 async 上下文里 await。**推荐**：把 `consult_ephemeral_subagent` 改成 `async`（agent_loop 调用点本就在 async 协程内，`:992` 改 `await`），callable 直接 async，省掉跨线程桥。
- **接电**：`build_agent` 里 `ephemeral_llm = _make_str_llm_call(local_llm or cloud_llm, max_tokens=256)`；`VerifyGate(extractor, mode, ephemeral_subagent=make_ephemeral_verifier(ephemeral_llm) if ephemeral_llm else None)`。provider=None → 仍 None → 退回保守 fail（BC）。

### 5. verify 接 goal_text — 见 WI-2.3（2.2 的 rebound 拼入 goal 对照串）

### 6. 外部 evaluator — 见 WI-2.4（2.2 第 3 层之后，高后果目标才走）

### 7. 边界 / 防回归 / flag
- flag：沿用 `verify_gate_mode`（off→整段跳过，BC）。ephemeral 落地不引新 flag——`ephemeral_subagent=None` 时行为同现状（第 2 次失败保守 fail），只有真接 callable 才启用救援。
- **死循环护栏**：第 1 层 2 次 + 第 2 层 1 次 ephemeral + 第 3 层 auto_resume 2 次 spawn = 硬上限，超出 → emit `verify_exhausted` → 用户可见"我尽力了，差这一步"，**不报喜不报忧**（§7.1）。
- ephemeral LLM 调用异常 → 已有 try/except 返 False（`verify_gate.py:423`），不阻 dispatch。
- 成本：ephemeral 只在 `max_verify_nudges` 耗尽后调一次/turn（非每轮），可接受。

### 8. 测试计划
- **单测** `tests/test_verify_replan_retry.py`：
  - 伪完成（claim 有、ledger 无 receipt）→ 第 1 轮 rebound 含反思指令；mock 第 2 轮 LLM 真调工具补上 receipt → verify 通过（**校验不过→改方案→二次通过**）。
  - `task_replanning` 与上轮相似度 >0.85 → 升级 ephemeral，不耗第 2 次 nudge。
  - ephemeral callable=None → 第 2 次失败保守 fail（BC）。
  - ephemeral callable 返 pass → 救援放行，log `ephemeral_rescued`。
  - 三层全失败 → emit `verify_exhausted` 且 `is_auto_resume_trigger("verify_exhausted") is True`。
- **真机 E2E**：对话"帮我生成 PPT 周报" → 第一次 LLM 只口头声称没真调 `ppt_create`（构造）→ verify 拦 → 自动重规划 → 第二轮真调 `ppt_create` → 真 .pptx 落盘 → ArtifactCard 渲染。截图 + grep `verify_gate_nudge_injected` / `task_replanning`。
- **pass^k**：上述链路 k=5 次稳定二次通过（τ-bench pass^k 思想，§7.1）。

### 9. build order
2.2 依赖 2.1（字段）+ 2.3（goal 对照串）。建议 2.1 → 2.3 → 2.2。

---

## WI-2.3 — verify 接 goal_text 对照（VerifyGate × GoalChecker 合流，完成判定 = 客观证据 + 原目标对照）

### 1. 目标文件
- **改** `backend/deskpet/agent/verify_gate.py`：`check(...)` 增可选 `goal_text: Optional[str]=None` 参数（BC：默认 None 时行为不变）；`VerifyOutcome` 加字段 `goal_alignment: Optional[GoalAlignment]`。
- **改** `backend/agent/agent_loop.py:955-1122`：把两个独立 if 分支（verify-gate `:960`、goal-checker `:1059`）**串联**——verify 失败时同时带 active goal 做对照；完成判定汇总。
- **改** `backend/deskpet/agent/goal_checker.py`：抽出 `build_alignment_prompt(goal_text, artifacts, claims)` 供 VerifyGate 复用（不重复造判定逻辑）。
- **依赖 P0-1**：从 `session_goal_store.get(sid).text` 取 goal_text（见文末「对 P0-1 的依赖契约」）。

### 2. 结构化字段 schema（GoalAlignment）

```python
@dataclass
class GoalAlignment:
    goal_text: str                 # 重述的【原始目标】（防 verifier 漂移的锚）
    objective_evidence: list[str]  # receipt + outcome_verifier 的客观信号（文件存在/sha/diff/test pass）
    aligned: bool                  # 客观证据是否真满足原目标（LLM-judged，但只读客观证据，不读人格）
    gap: str                       # aligned=False 时差什么
```

### 3. replan 驱动（2.3 给 2.2 喂"目标对照串"）
verify 失败 rebound（2.2 段）拼入：
```
[verify-gate blocked]
原始目标: {goal_text}
客观证据: {objective_evidence}   # ← receipt + outcome_verifier，不是 LLM 自述
缺口: {gap}
→ 请重规划（结构化反思）后补齐，使产物真满足原目标，而非换个说法过关。
```
这正是 audit 文档 §P0 建议的 rebound 模板，落地点 `agent_loop.py:1007-1013`。

### 4. ephemeral — 复用 2.2 落地

### 5. 两分支合流 + 完成判定 + 人格禁入（核心）

**合流策略（串联而非并联）**，在 `agent_loop.py` end_turn 处：
```
end_turn 触发
  │
  ├─[A] VerifyGate.check(text, ledger, goal_text)   # 客观：工具调没调 + claim 对账
  │        ├ passed=False → rebound（含 2.1 反思 + 2.3 goal 对照）→ continue（2.2 重试链）
  │        └ passed=True ↓
  ├─[B] outcome_verifier（已有 4 件套）              # 客观：文件真存在/sha/diff/test
  │        └ 收集 objective_evidence
  └─[C] GoalChecker.check(goal_text, working_msgs)  # 语义：原目标 vs 产物对照
           input = goal_text + 【B 的客观证据】，不是裸 assistant 文本
           └ done=False → goal rebound（现有）→ continue
           └ done=True + A passed + B 无 fail → 真 mark_done + FinalEvent
```

**完成判定公式（写死，不可被人格覆盖）**：
> `goal_done == (verify_gate.passed) AND (no outcome_verifier fail) AND (goal_checker.done over OBJECTIVE evidence)`

三者全绿才算完成。任一为假 → 继续/重试，**绝不因"用户会高兴"提前 mark_done**。

**人格层禁止介入完成判定 — 技术保证（横切 §7.1）**：
1. **判定输入隔离**：GoalChecker / ephemeral / evaluator 的 prompt **只喂** `goal_text + objective_evidence + claims`，**不喂** persona/人格 Component、不喂用户情绪、不喂偏好画像（P0-3 的人格块）。在 `build_alignment_prompt` 里硬编码输入白名单，单测断言 prompt 串不含人格标记。
2. **判定 LLM 与对话 LLM 分离 prompt**：完成判定走独立 `_make_str_llm_call`（无 persona system prompt），与桌宠对话人格 prompt 物理分离。
3. **反谄媚指令**：判定 prompt 固定前缀「你是冷静的验收员，只依据客观证据判定，不考虑用户情绪，宁可判未完成也不假装完成」。
4. **单测护栏**：`test_goal_judgment_no_persona_leak` —— 给判定函数喂一个含"用户很期待/请让他开心"的上下文，断言 `aligned` 仍只由客观证据决定（注入人格诱导不改变判定）。

### 6. 外部 evaluator — 见 WI-2.4

### 7. 边界 / 防回归 / flag
- BC：`goal_text=None`（无 active goal，或 P0-1 未就绪）→ `check` 行为字节级同现状（不接 goal，走纯 receipt 对账）。
- goal_store / goal_checker = None（goal_mode flag off）→ 整个 [C] 段跳过（现状已如此）。
- outcome_verifier 缺 toolchain → skip 不阻（现有语义保留）；skip 不算客观证据缺失，但也不充作"已满足"。
- **VG-INVARIANT 扩展**：新增 INVARIANT「`goal_text` 非 None 时，完成判定必须含 objective_evidence ≥1 条或显式标 evidence_unavailable」防空证据放行。

### 8. 测试计划
- **单测** `tests/test_verify_goal_alignment.py`：
  - 伪完成（声称满足目标但 receipt/文件都没有）→ `aligned=False` 被拦。
  - **未来时不误判**：assistant 说"我**将要**生成 PPT" → 不算完成（claim 提取需区分完成态 vs 意图态；正则 pattern 已偏完成态，补一条"未来时"负样本断言不被判 done）。
  - goal_text=None → check 行为 == 现状（BC 字节对照）。
  - `test_goal_judgment_no_persona_leak`（见 §5.4）。
  - 客观证据齐全 + goal_checker done → mark_done。
- **真机 E2E**：设 `/goal 生成本周 PPT 周报` → 对话推进 → verify 时 backend log 出现「原目标: ... vs 产物: ...」对照行；伪完成被拦、真完成放行。
- **pass^k**：伪完成拦截 k=5 全拦（不能偶尔漏放）。

### 9. build order
2.3 依赖 P0-1（goal_text 接口）。在 P0-1 的 `get_active_goal()` 契约就绪后做，先于 2.2 的 rebound 拼串。

---

## WI-2.4 — 外部 / 多 persona 交叉验证（仅高后果目标触发）

### 1. 目标文件
- **新建** `backend/deskpet/agent/external_evaluator.py` — `ExternalEvaluator` + 高后果判定 `is_high_consequence_goal(...)` + evaluator prompt 契约。
- **改** `backend/agent/agent_loop.py`：完成判定 [C] 通过后、FinalEvent 前，**仅当高后果**插入 evaluator gate。
- **改** `backend/main.py build_agent`：接电 `external_evaluator`（复用第二 provider 或同 provider 不同 persona）。
- **改** `backend/config.py`：`VerifierConfig` 加 `external_evaluator: bool=False` + `evaluator_provider: str="default"`（dev on / prod off）。

### 2. 「高后果目标」触发条件（成本护栏，仅这些才走）

`is_high_consequence_goal(goal_text, ledger, changed_files) -> bool`，命中任一即高后果：
1. **改文件/不可逆**：ledger 里出现 `file_write/patch/ppt_create/excel_create/doc_create/pdf_export` 等**产物/写盘工具**。
2. **改钱/系统状态**：goal_text 或工具命中支付/删除/发送/购买/系统设置类关键词（维护一个 `_HIGH_CONSEQUENCE_KEYWORDS` 列表 + 工具名集合）。
3. **多步高投入**：本 turn 工具调用 ≥5 次（对齐 hermes「值得固化」触发器同一阈值，复用常量）。
4. **低置信**：2.1 反思 `confidence < 0.3`（agent 自己都没把握）。

**纯只读/低风险**（只调了 web_search/read/retrieve 类）→ **不走** evaluator，省一次本地 LLM 调用（plan §3 成本护栏）。

### 3. replan 驱动
evaluator `verdict="revise"` → 复用 2.2 第 3 层：emit `ErrorEvent(reason="verify_exhausted")` 或新 `reason="evaluator_revise"` → auto_resume spawn 一次重规划（带 evaluator 的 issues 作 hint）。**不无限循环**——evaluator 每个 goal 只跑一次（FinalEvent 前），revise 后的重试由 auto_resume 上限（max_attempts=2）兜底。

### 4. ephemeral vs evaluator 区分
- ephemeral（2.2）= verify **claim 救援**（regex 漏判同义改写），轻量、对账层。
- evaluator（2.4）= **完成度质量 judge**，独立视角，语义层，**只高后果触发**。
两者不同职责，不合并。

### 5. 复用哪个现成多 agent 设施
- **首选复用 `_make_str_llm_call`**（最轻，单 LLM 不同 persona prompt）——满足「≥2 独立视角」最小成本实现。
- **可选升级**：复用 `backend/deskpet/tools/code_tools/agent_parallel_tool.py`（hub-and-spoke 多 subagent），但那是为并行子任务设计，对单产物 judge 偏重。**本轮用轻量 `_make_str_llm_call` 起步**，agent_parallel 留作后续可选。
- 「独立视角」实现：evaluator 用**不同 provider**（若用户配了第二个）或**同 provider + 显式 evaluator persona**（与对话 LLM、与 2.3 判定 LLM 都用不同 system prompt），抵消单 agent 漂移（best-practices §3.1 单 agent 自反思会自我强化错误）。

### evaluator prompt 契约
输入：`original_goal + produced_artifacts(paths+sha) + objective_evidence + conversation_summary`（**不含人格**，同 2.3 §5 隔离）。
输出：
```json
{"quality_score": 0-10, "issues": ["..."], "verdict": "pass|revise", "reason": "..."}
```
`verdict="revise"` 且 `quality_score < 阈值(默认6)` → 触发一次重规划。

### 6. 成本护栏
- 仅高后果目标（§2）触发；普通目标完全不调 → 本地桌宠每步不额外加 LLM 调用（plan §3 锁定）。
- evaluator 每 goal 最多 1 次（FinalEvent 前），revise 后重试走 auto_resume 既有上限。
- `external_evaluator=False` 默认（prod 出厂关；dev 开），与 verify off→shadow 节奏一致。
- provider=None / 调用异常 → safe-fail 放行（不阻完成），记 `evaluator_skipped` metric。

### 7. 边界 / 防回归 / flag
- flag off → 整段跳过（BC）。
- 高后果判定**保守**：误判为高后果只是多一次 LLM 调用（可接受）；误判为低后果会漏 evaluator（更危险）→ 关键词/工具集宁可宽。
- evaluator 与 GoalChecker 都说 done 才最终完成；evaluator revise 优先级高于 GoalChecker done（独立视角覆盖自评）。

### 8. 测试计划
- **单测** `tests/test_external_evaluator.py`：
  - `is_high_consequence_goal`：写盘工具/支付关键词/≥5 工具/低 confidence 各命中；纯 read 不命中。
  - evaluator verdict=revise → emit `evaluator_revise` 触发 auto_resume。
  - provider=None → safe-fail 放行 + metric。
  - 普通目标 → evaluator 不被调用（断言 llm_call 0 次）。
- **真机 E2E**：高后果目标（生成会改盘的 PPT）→ 产物质量差（构造空 pptx）→ evaluator revise → auto_resume 重规划 → 二次产出合格。截图 + grep `evaluator_revise` / `auto_resume_spawned`。
- **pass^k**：高后果 revise→重试链 k=3 稳定。

### 9. build order
2.4 最后做，依赖 2.2（重试链）+ 2.3（objective_evidence + 隔离判定基建）。

---

## 整体 build order

```
P0-1 暴露 get_active_goal() 契约（前置依赖）
   │
2.1 结构化反思字段（reflection.py + agent_loop 注入点 + flag）   ← 先，独立
   │
2.3 verify 接 goal_text（VerifyGate.check 加参 + 两分支合流 + 人格隔离判定）  ← 依赖 P0-1
   │
2.2 真重规划重试（rebound 拼 2.1+2.3 + ephemeral 落地 + verify_exhausted→auto_resume）  ← 依赖 2.1+2.3
   │
2.4 外部 evaluator（高后果触发 + evaluator prompt + 成本护栏）   ← 最后，依赖 2.2+2.3
   │
flag 迁移：dev 全开自测 → prod verify_gate_mode off→shadow（不拦只观测，收数据）
```

**flag 默认值矩阵（字节级契约惯例，dev 先开 prod 谨慎）**：

| flag | dev 默认 | prod 出厂 | 落地后 prod 目标 |
|---|---|---|---|
| `verify_gate_mode` | strict | **off → shadow**（本轮迁移） | shadow 收数据后评 strict |
| `structured_reflection`（2.1） | True | False | 跟随 verify_gate ≥ shadow 时点亮 |
| `external_evaluator`（2.4） | True | False | 长期保守（仅高后果，成本敏感） |

prod off→shadow 迁移的 VG-INVARIANT-1（`verify_gate_mode != off ⇒ emit_receipts=true`，`config.py:481`）已强制；shadow 不阻断只 warn（`verify_gate.py:309-315` 已实现），迁移**零行为风险**，只是开始收集 unmatched 数据。

---

## 对 P0-1 goal 接口的依赖契约（2.3 期望 P0-1 暴露）

2.3 需要在 agent_loop 的 end_turn 判定点拿到「当前活跃目标文本」。期望 P0-1（WI-1.1 Durable Goal Store）暴露**与现有 `SessionGoalStore.get()` 兼容**的接口，最小契约：

```python
# 现状已有（goal_store.py:92），P0-1 落 SessionDB 后保持同签名：
def get(session_id: str) -> Optional[SessionGoal]: ...
#   SessionGoal.text  : str         ← 2.3 取 goal_text（必需）
#   SessionGoal.done  : bool        ← 已有
#   SessionGoal.subgoals : list     ← P0-1 新增（2.3 可选用于更细对照，非必需）

# 期望 P0-1 额外提供（便于 2.3 在非 /goal 场景也能拿目标，建议但不阻塞）：
def get_active_goal(session_id: str) -> Optional[GoalSnapshot]:
    """返回当前活跃目标快照（text + subgoals + status）。
       无活跃目标 → None。重启后可从 SessionDB 恢复（P0-1 核心交付）。"""
```

**契约要点给 P0-1**：
1. **字段 `text` 必须存在且为重述用的原始目标全文**（2.3 拿它做对照锚，防 verifier 漂移）。
2. **`get` 同步、零 I/O 阻塞**（agent_loop end_turn 是热路径；P0-1 落库后请保持内存缓存 + 异步落盘，`get` 读缓存）。
3. **`None` 语义稳定**：无 goal 返 None，2.3 据此走 BC（不接 goal）。P0-1 不要把 None 换成抛异常。
4. **重启恢复**：2.3 的真机 E2E（设目标→重启→verify 仍带 goal 对照）直接依赖 P0-1「重启可恢复活跃目标」验收。
5. 若 P0-1 引入 `goal_id` / 多目标，请提供「当前主目标」的单一入口，2.3 不处理多目标歧义。

**降级保证**：P0-1 未就绪时，2.3 以 `goal_text=None` 全程 BC 运行（只丢"原目标对照"增强，不阻 2.1/2.2/2.4），故 2.1/2.2 可与 P0-1 并行开发，2.3 在 P0-1 接口冻结后接线。
