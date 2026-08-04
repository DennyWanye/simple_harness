# 05 · 测试计划 + flag 策略（kill-switch，无灰度 ceremony）+ 回退守护 + 回滚

> 对齐项目硬纪律：[[feedback_real_test_discipline]]（真机模拟人工 ≠ 脚本回放）、
> ★ 一票否决 = flag off 时零回归。验收以**硬证据**为准（截图 + backend 日志 + 测试输出）。

---

## 1. 测试金字塔

### L0 · kill-switch 回退守护（★ 一票否决，最高优先级）

> **决策1（round-3）**：测试环境无生产级 rollout ceremony——L0 降级为"现有 pytest 不回归即可"，**删掉字节级行为
> 快照 diff 那套**。仍保留"关掉 flag 能快速回退"作为 kill-switch 价值证明，但不再做快照比对仪式。

- **判据**：把 flag 关掉（`features.problem_pipeline.enabled == false`，kill-switch）时，全套现有测试不回归。
- **命令**：`cd backend && python -m pytest -q`（期望与落地前同样 PASS 数，当前基线 2300+）。
- **kill-switch 回退价值**：`enabled=false` 时所有新分支早 return、不构造 pipeline 组件、不发 `<标签>` 事件 → 等价于今天的链路；现有测试全绿即证明 kill-switch 可安全回退。**不做** `bc_baseline_snapshot.py` 字节级快照比对（测试环境无须）。
- **失败即阻断**：任何 L0 回归 = 本期不可 ship，必须先修到 flag-off 不回归。

### L1 · 单元测试（每步独立，新建测试文件）

| WI | 测试文件 | 覆盖 |
|---|---|---|
| Step1+3（合并，决策4） | `test_intent_triage.py` | IntentCard 解析（**含可选 `contradiction` 段**）；chitchat 纯规则短路（**不调 LLM** 断言）；ambiguity≥阈值→澄清出口；problem_type 从 ClassifierResult 派生正确；**复杂问题（debug/research/multi_task/creation）一次调用同时返回 intent + contradiction；简单 factual_qa 返回 `contradiction=null`**；attack_order 解析；**非闲聊只调 1 次 LLM**（`await_count==1`，证明 Step1+3 合一）；analyze 失败/超时→safe-fail 保守 card（contradiction=null） |
| Step2 | `test_evidence_gate.py` | needs_investigation 且无取证工具调用 → BLOCK；有 search/read 后放行；只数本 run 内新增 tool 消息（不误算 history 注入的旧 tool）；max_nudges 超限放行 + 记 exhausted |
| Step4 | `test_plan_companion.py` | companion 模式按 flag 生成计划；首步对准 principal contradiction；parallelizable 标注；**code mode 分支原样不动、行为不回归（决策2）** |
| Step6 | `test_self_check_gate.py` | 按 problem_type 选档（debug→严格/chitchat→skip）；VerifyGate+StructuredReflection 整合不双重对账；异体评分调独立 fresh-context provider（mock `_resolve_ephemeral_provider`，**model 取 `self_check_model` 留空=主 LLM**，决策3）；与旧 verify_gate 块互斥 |
| Step7 | `test_convergence_controller.py` | 量化收敛判据（principal 解 / ledger 平 / 资源触顶）；止损报告生成；TerminationGate 硬上限复用不回归 |
| 编排 | `test_problem_pipeline.py` | flag off（kill-switch）全短路（断言各组件 never called）；problem_type 路由决定哪几步跑；标签事件 payload schema；闲聊短路不调 LLM |

- **风格**：照现有 `test_verify_gate*.py`（monkeypatch LLM、mock provider）。LLM 调用一律 mock，不打真 relay。
- **★ 关键断言 — 闲聊路径 zero LLM call（守性能红线，对齐 04 风险#2）**：给 IntentTriage 注入一个 mock 的
  `llm_call`，断言闲聊路径下它 **`call_count == 0`**。具体写法（照 test_verify_gate 的 monkeypatch 风格）：
  ```python
  import asyncio
  from unittest.mock import AsyncMock
  from deskpet.agent.intent_triage import IntentTriage

  def test_chitchat_zero_llm_call():
      mock_llm = AsyncMock(return_value="{}")          # 若被调用会返回空 JSON
      triage = IntentTriage(mock_llm)
      # prior_task_type=="chat"/"emotion" → _TASKTYPE_TO_PROBLEM 派生 chitchat → 纯规则短路
      card = asyncio.run(triage.analyze("你好呀今天天气真好", prior_task_type="chat"))
      assert card.short_circuit is True
      assert card.problem_type == "chitchat"
      assert card.contradiction is None                # 闲聊无矛盾段（决策4）
      mock_llm.assert_not_called()                     # ★ 一票否决：闲聊绝不调 LLM
      assert mock_llm.call_count == 0
  ```
  对 `prior_task_type="emotion"`（"我心情不好"）再断一次 `call_count == 0`。**非闲聊路径**（如 `prior_task_type="code"`）
  反向断言 `mock_llm.await_count == 1`（确认合并预分析**只调一次** LLM 同时出 intent+contradiction，决策4），
  避免"全都不调"的假短路掩盖 bug；并对一条复杂问题断言返回的 `card.contradiction is not None`（矛盾段在同一次调用产出）。

### L1.5 · WI-5 澄清出口持久化单测（M-5 / round-3 MAJOR 补）

> 背景：澄清出口（`needs_clarification`）裸 emit `chat_v2_final` 后 `return`，不进 agent loop，**FinalEvent 收尾路径
> 不会触发**——澄清问题这条 assistant 消息必须由澄清出口分支**自己**持久化（04 §M3 改动 3a，已核实持久化 API
> @ main.py:7212-7219 = `_sdb.append_message(session_id=, role="assistant", content=)`）。漏持久化 → 下轮用户答澄清时
> history 重建缺这条澄清问题 → 多轮澄清断裂。本测守这一条：

  ```python
  # mock _sdb（AsyncMock），构造 needs_clarification=True 的 IntentCard（clarifying_questions 非空），
  # 走 PRE-LOOP 澄清出口分支后断言：
  _sdb.append_message.assert_awaited()                  # 至少落库一次
  _call = _sdb.append_message.await_args
  assert _call.kwargs["role"] == "assistant"            # 落的是 assistant 行
  assert _call.kwargs["content"] == "\n".join(card.clarifying_questions)  # content==澄清问题
  # 反向：模拟"下一轮"history 重建（用同 sid 拉 messages），断言其中含该澄清问题文本——
  # working_messages/history 含该澄清问题（多轮澄清不断裂的硬证据）。
  ```
  若 `_sdb is None`（无 SessionDB），澄清出口仍须 emit + set idle + return（持久化静默跳过），断言不抛异常。

### L2 · 集成 / live smoke（跨层契约，对齐 [[feedback_cross_layer_contract]]）

- `scripts/e2e_problem_pipeline_smoke.py`：起真 backend（dev python，不打 relay 用 stub LLM 或单次真调），发 3 类问题，断言：
  - WS 事件流含 `chat_v2_intent/contradiction/selfcheck/convergence`（flag on）。
  - 闲聊不触发 contradiction/plan 事件（短路证据）。
  - 字段单位/类型前后端一致（防 pytest+tsc 都过但契约漂移）。

### L3 · 真机 windows-mcp E2E（硬纪律，不可用脚本回放替代）

> 触发本项目 HARD CONSTRAINT。每个 case：Screenshot → 真坐标点击/真中文输入 → 截图 → backend 日志判定；动作前 declare `坐标=(x,y)|动作=|期望=`；证据落 `plans/manual-results-2026-06-24-problem-pipeline/screenshots/`。先按 [[reference_dev_test_credentials]] 登录、按 [[reference_dev_userdata_dir_on_g]] 设 dev 数据目录、按 pitfall #8 设 `DESKPET_BACKEND_DIR` 跑 worktree 代码。

| TC | 场景 | 期望（日志/截图硬证据） |
|---|---|---|
| **TC-1 闲聊不拖慢** | 桌宠发"你好呀今天天气真好" | 日志 `pipeline_step intent problem_type=chitchat short_circuit=true`；**闲聊 0 次 LLM 调用**（纯规则短路，无意图/矛盾/plan 调用）；回复延迟与 flag off 基线相当（截两次时间戳对比）。**性能红线口径（决策4）**：非闲聊每问题只多 **1 次 gpt-5.5 调用**（Step1+3 合并的预分析），闲聊 **0 次**——TC-1 验闲聊 0 次，另取一条非闲聊（如 TC-2）在日志确认预分析只调 1 次（非两次串行）。 |

> **TC-1 前置度量（classifier 落 chat/emotion 覆盖率）**：闲聊纯规则短路依赖组装期 `_bundle.task_type` 落
> `chat`/`emotion`（→`_TASKTYPE_TO_PROBLEM` 派生 chitchat）。**若 classifier 把闲聊误判成 `code`/`task`，短路失效→
> 闲聊被拖进完整流水线**（对齐 04 风险#2）。故 TC-1 前先跑一组**闲聊语料覆盖率度量**：取 ≥10 条代表性闲聊/情绪
> 输入（"你好"/"今天天气真好"/"我有点累"/"陪我聊聊"…），在 backend 日志确认 `task_type` 落 chat/emotion 的命中率
> （记一行 `intent_triage.shortcircuit reason=chitchat_rule task_type=<...>`）。命中率 < 阈值（建议 ≥80%）则需在 Step1
> 补一层闲聊兜底规则（如 LLM 重判后仍按 chitchat 短路），并把该度量数值写进 TC-1 报告（硬证据，不只 PASS/FAIL）。
| **TC-2 debug 取证门控** | "我的 XX 功能报错了，帮我看看"（无更多信息） | 日志 `evidence_gate blocked=true`（模型想直接下结论被拦）→ 模型转而调 read/grep 取证 → 放行；`<调查>` 事件出现 |
| **TC-3 多症状抓主要矛盾** | 抛一个含 2-3 个症状的复合问题 | 日志 `contradiction principal=<id> attack_order=[...]`；`<主要矛盾>` 事件；计划首步对准 principal；截图前端"主攻"卡片（若渲染） |
| **TC-4 异体自检** | 让桌宠完成一个可验证产物类任务，诱导其"假装完成" | 日志 `self_check heterogeneous=true passed=false`（异体子代理打回）→ 二次修正 → passed=true。**⚠️ round-3 MINOR② 修正（对齐决策3 诚实降级）**：`self_check_model` 默认留空=主 LLM gpt-5.5（中转站单模型不保证不同 model），故**不能断言 `model=` 不同**——验的是**fresh-context 独立子代理**而非 diff-model。硬证据改为：日志 `pipeline_external_evaluator_model model=<x> base=<y>`（确认评分走经 `_resolve_ephemeral_provider` 克隆出的**独立 provider 实例**、新开 context、非执行者本人），`model` **可以** == 主 LLM。仅当显式把 `self_check_model` 配成不同 model 时，才另断 `model=` ≠ 主 LLM（可选增强）。 |
| **TC-5 收敛止损** | 构造一个解不动的问题逼近迭代上限 | 桌宠输出**诚实止损报告**（"卡在哪+建议"）而非假装完成；日志 `convergence converged=false principal_resolved=false should_stop_loss=true`。**⚠️ round-3 MINOR②/③ 修正**：默认 `GateConfig` 硬上限**极大**（已核实 termination.py:76 `max_turns=10000`、:79 `wall_clock_seconds=None` 禁用、:80 `max_budget_usd=None` 禁用），自然跑**逼不出** budget/turns 触顶，且不存在 `stop_reason=budget` 这个值。两条二选一：**(a)** 真测前临时收紧 gate——给 backend 注入小 `max_turns`（如 4）或小 `per_tool_max_consecutive`（如 3，制造同工具同参重复触发 `hallucination`）构造触顶；**(b)** 直接验真实触顶 `stop_reason`（取值见 §6 注：`error_max_turns`/`error_tool_budget`/`hallucination`/`circuit_breaker_open`/`context_budget_block`/`all_providers_failed`/`permanent_tool_error`，**不是** `budget`）。报告里写明用了哪种构造方式 + 实际 `stop_reason` 值。 |

- **失败 retry ≥3 次不同 workaround 才能标"环境受限"**；跳过任何 case 须显式声明 + 理由 + 等用户确认。
- **中文输入 workaround**：STA Runspace + `Clipboard.SetText` + Ctrl+V；焦点不在目标窗口先 Click 聚焦。

---

## 2. flag 策略（决策1：测试环境直接全开，无灰度 ceremony）

> **决策1（round-3）：删除 shadow→light→strict 多档灰度**。本期是测试环境，**出厂即开全量验证**——`enabled=true`
> 且各子 flag 默认 on。flag **仅留作出问题时的 kill-switch + 单步调试开关**（如怀疑某步误伤就单独关那个子 flag、
> 或整条 `enabled=false` 一键回退）。不做生产级 rollout 阶段仪式。

- **出厂态**：`enabled=true` + `intent_triage/evidence_gate/plan_companion_enabled/self_check/observability_events` 全 on（见 03 §5 flag 表）。
- **kill-switch 用法**：线上若某类问题被误伤 → 单独关对应子 flag 调试；整体异常 → `enabled=false` 一键退回今天的链路（L0 守不回归）。
- **per-problem_type 差异化**（具体问题具体分析，**内置在 `self_check` 里，非灰度档**）：chitchat 永远短路（不调 LLM）；factual_qa 仅 Step1+3 预分析 + 轻量自检；debug/research/multi_task/creation 走全链 + 严格自检（含异体评分）。

---

## 3. kill-switch 回退守护清单（逐处，落地时勾选）

> 决策1：测试环境出厂即开，`enabled` 默认 true。本清单守的是 **kill-switch（关 flag）能干净回退**，**不再含 B2 存量
> backfill**（测试环境无存量迁移需求，已删）；config 仍需加 `[features.problem_pipeline]` 段 + 读取。

- [ ] `config.py` 新增 `ProblemPipelineConfig` 段且能被读取；`enabled` 默认 **true**、各子 flag 默认 on（字段默认值单测）；`analysis_model`/`self_check_model` 默认 `""`（留空=主 LLM，决策3）。
- [ ] **（B1）** `context.py:_VALID_SERVICES`（@:9）加 4 个 key + `ServiceContext` dataclass（@:89）加 4 个 `Any|None=None` 字段（problem_pipeline / pipeline_evidence_gate / pipeline_self_check_gate / pipeline_convergence_controller）。所有写入用 `register(name,obj)`（**禁止下标**，dataclass 无 `__setitem__`）；flag off 时也 `register(name,None)` 占位。单测：`register(...,None)` + `get(...)` 均不抛 ValueError。
- [ ] flag off（kill-switch）时：`main.py` PRE-LOOP 编排早 return（不构造 IntentTriage）。
- [ ] flag off 时：`agent_loop.py` 守门链走原 VerifyGate/external_evaluator 块（SelfCheckGate/EvidenceGate/ConvergenceController 不介入）。
- [ ] flag off 时：`plan.py` 走原 code-only 逻辑（companion 分支不进；code 模式分支无论 flag 都原样不动，决策2）。
- [ ] flag off 时：不发任何 `chat_v2_intent/contradiction/evidence_gate/selfcheck/convergence` 事件。
- [ ] flag off 时：现有 2300+ pytest 不回归（kill-switch 回退价值，替代原字节级快照比对）。

---

## 4. 回滚

- **代码级**：全部改动在 flag 后；线上异常 → 一条 config 改 `enabled=false` 即恢复（无需回滚部署）。
- **数据级**：本期不改任何持久化 schema（IntentCard/ContradictionMap 仅运行时内存 + WS 事件，不落库）→ 无数据迁移、无回滚负担。
- **commit 级**：每 WI 独立 commit，可单独 revert。

---

## 5. 完成判据（DoD）

1. L0 kill-switch 回退不回归（★ 一票否决：关 flag 后 2300+ pytest 不回归）。
2. L1 全部新单测绿；闲聊 0 次 LLM 断言通过；非闲聊预分析只调 1 次（Step1+3 合一）断言通过。
3. L2 live smoke 跨层契约一致。
4. L3 真机 5 个 TC 全 PASS（截图 + 日志硬证据），闲聊不拖慢有时间戳对比证据。
5. 更新 `STATUS/status.md` §3 新增"问题处理流水线"模块行 + §4 里程碑（对齐项目 STATUS 纪律）。
6. **测试环境出厂即开 ship（决策1）**：`enabled=true` 全量验证；无 shadow→strict 灰度仪式，flag 仅留 kill-switch + 单步调试。

---

## 6. 附：TerminationGate 真实 reason 取值集合（已核实 @ termination.py，TC-5/ConvergenceController 依据）

> `gate.summary()["reason"]`（已核实 termination.py:242-254）= `TerminationReason.value`，未 terminate 时合成 `"running"`。
> **全部取值**（已核实 `TerminationReason` 枚举 @ termination.py:30-52）：

| 类别 | value | 触发 |
|---|---|---|
| 自然收尾（非触顶） | `success` | end_turn 正常结束（`record_final_answer`→`terminate(SUCCESS)`，:225-226）|
| 自然收尾（非触顶） | `user_interrupted` | 用户中断 |
| 合成（未 terminate） | `running` | gate 尚未 terminate（`summary()` 兜底，:248）|
| 硬上限触顶 | `error_max_turns` | `turns_used >= max_turns`（默认 10000，:142）|
| 硬上限触顶 | `error_tool_budget` | 工具预算耗尽 |
| 硬上限触顶 | `error_wall_clock_exceeded` | 墙钟超限（默认 `wall_clock_seconds=None` **禁用**，:79）|
| 硬上限触顶 | `error_max_budget_usd` | 成本超限（默认 `max_budget_usd=None` **禁用**，:80）|
| 错误态触顶 | `permanent_tool_error` | 工具永久失败 |
| 错误态触顶 | `all_providers_failed` | provider 全挂 |
| 错误态触顶 | `context_budget_block` | 上下文预算阻断 |
| 错误态触顶 | `hallucination` | `per_tool_max_consecutive` 触发（默认 8，:165-166）= 真死循环主防线 |
| 错误态触顶 | `circuit_breaker_open` | 熔断 |

> **关键结论（喂 TC-5 + 04 §N5 MINOR③）**：
> ① **不存在 `budget` 这个 value**——TC-5 原期望 `stop_reason=budget` 是臆造值，必挂。
> ② 默认配置下 `max_turns=10000` + `wall_clock`/`max_budget` 均 `None`（禁用）→ 自然跑**逼不出**这些硬上限；
>    实操真死循环靠 `per_tool_max_consecutive=8`→`hallucination`。真测要么临时收紧 `max_turns`，要么制造同工具同参重复触 `hallucination`。
> ③ "正常收尾"的 reason 只有 `success` / `user_interrupted` / `running` 三个；其余**全是触顶/错误**（应让 `resource_capped=True`）。
>    故 04 §N5 把 `resource_capped` 改成鲁棒补集判据：`reason not in ("running","success","user_interrupted")` 即视为触顶。
