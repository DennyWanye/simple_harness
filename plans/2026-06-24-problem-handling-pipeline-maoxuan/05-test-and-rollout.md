# 05 · 测试计划 + 灰度 + BC 守护 + 回滚

> 对齐项目硬纪律：[[feedback_real_test_discipline]]（真机模拟人工 ≠ 脚本回放）、
> ★ 一票否决 = flag off 时零回归。验收以**硬证据**为准（截图 + backend 日志 + 测试输出）。

---

## 1. 测试金字塔

### L0 · BC 基线守护（★ 一票否决，最高优先级）

- **判据**：`features.problem_pipeline.enabled == false` 时，全套现有测试零回归。
- **命令**：`cd backend && python -m pytest -q`（期望与落地前同样 PASS 数，当前基线 2300+）。
- **字节级行为基线**：对 3 条代表性输入（闲聊 / debug / 多任务）在 flag off 下录"消息序列 + 工具调用序列 + 事件流"快照，落地后逐字节比对。脚本 `plans/2026-06-24-problem-handling-pipeline-maoxuan/exec/bc_baseline_snapshot.py`（实施时建）。
- **失败即阻断**：任何 L0 回归 = 本期不可 ship，必须先修到零回归。

### L1 · 单元测试（每步独立，新建测试文件）

| WI | 测试文件 | 覆盖 |
|---|---|---|
| Step1 | `test_intent_triage.py` | IntentCard 解析；chitchat 纯规则短路（**不调 LLM** 断言）；ambiguity≥阈值→澄清出口；problem_type 从 ClassifierResult 派生正确 |
| Step2 | `test_evidence_gate.py` | needs_investigation 且无取证工具调用 → BLOCK；有 search/read 后放行；只数本 run 内新增 tool 消息（不误算 history 注入的旧 tool）；max_nudges 超限放行 + 记 exhausted |
| Step3 | `test_contradiction_analyzer.py` | ContradictionMap 解析；触发条件（仅 debug/research/multi_task/creation）；简单/闲聊不触发（省 token 断言）；attack_order 输出 |
| Step4 | `test_plan_companion.py` | companion 模式按 flag 生成计划；首步对准 principal contradiction；parallelizable 标注；code mode 行为不回归 |
| Step6 | `test_self_check_gate.py` | 按 problem_type 选档（debug→strict/chitchat→skip）；VerifyGate+StructuredReflection 整合不双重对账；异体评分调独立 provider（mock `_resolve_ephemeral_provider`）；与旧 verify_gate 块互斥 |
| Step7 | `test_convergence_controller.py` | 量化收敛判据（principal 解 / ledger 平 / 资源触顶）；止损报告生成；TerminationGate 硬上限复用不回归 |
| 编排 | `test_problem_pipeline.py` | flag off 全短路（断言各组件 never called）；shadow 模式跑但不阻塞；problem_type 路由决定哪几步跑；标签事件 payload schema |

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
      card = asyncio.run(triage.triage("你好呀今天天气真好", prior_task_type="chat"))
      assert card.short_circuit is True
      assert card.problem_type == "chitchat"
      mock_llm.assert_not_called()                     # ★ 一票否决：闲聊绝不调 LLM
      assert mock_llm.call_count == 0
  ```
  对 `prior_task_type="emotion"`（"我心情不好"）再断一次 `call_count == 0`。**非闲聊路径**（如 `prior_task_type="code"`）
  反向断言 `mock_llm.await_count == 1`（确认正常路径确实调了一次 intent LLM），避免"全都不调"的假短路掩盖 bug。

### L2 · 集成 / live smoke（跨层契约，对齐 [[feedback_cross_layer_contract]]）

- `scripts/e2e_problem_pipeline_smoke.py`：起真 backend（dev python，不打 relay 用 stub LLM 或单次真调），发 3 类问题，断言：
  - WS 事件流含 `chat_v2_intent/contradiction/selfcheck/convergence`（flag on）。
  - 闲聊不触发 contradiction/plan 事件（短路证据）。
  - 字段单位/类型前后端一致（防 pytest+tsc 都过但契约漂移）。

### L3 · 真机 windows-mcp E2E（硬纪律，不可用脚本回放替代）

> 触发本项目 HARD CONSTRAINT。每个 case：Screenshot → 真坐标点击/真中文输入 → 截图 → backend 日志判定；动作前 declare `坐标=(x,y)|动作=|期望=`；证据落 `plans/manual-results-2026-06-24-problem-pipeline/screenshots/`。先按 [[reference_dev_test_credentials]] 登录、按 [[reference_dev_userdata_dir_on_g]] 设 dev 数据目录、按 pitfall #8 设 `DESKPET_BACKEND_DIR` 跑 worktree 代码。

| TC | 场景 | 期望（日志/截图硬证据） |
|---|---|---|
| **TC-1 闲聊不拖慢** | 桌宠发"你好呀今天天气真好" | 日志 `pipeline_step intent problem_type=chitchat short_circuit=true`；**无** contradiction/plan LLM 调用；回复延迟与 flag off 基线相当（截两次时间戳对比） |

> **TC-1 前置度量（classifier 落 chat/emotion 覆盖率）**：闲聊纯规则短路依赖组装期 `_bundle.task_type` 落
> `chat`/`emotion`（→`_TASKTYPE_TO_PROBLEM` 派生 chitchat）。**若 classifier 把闲聊误判成 `code`/`task`，短路失效→
> 闲聊被拖进完整流水线**（对齐 04 风险#2）。故 TC-1 前先跑一组**闲聊语料覆盖率度量**：取 ≥10 条代表性闲聊/情绪
> 输入（"你好"/"今天天气真好"/"我有点累"/"陪我聊聊"…），在 backend 日志确认 `task_type` 落 chat/emotion 的命中率
> （记一行 `intent_triage.shortcircuit reason=chitchat_rule task_type=<...>`）。命中率 < 阈值（建议 ≥80%）则需在 Step1
> 补一层闲聊兜底规则（如 LLM 重判后仍按 chitchat 短路），并把该度量数值写进 TC-1 报告（硬证据，不只 PASS/FAIL）。
| **TC-2 debug 取证门控** | "我的 XX 功能报错了，帮我看看"（无更多信息） | 日志 `evidence_gate blocked=true`（模型想直接下结论被拦）→ 模型转而调 read/grep 取证 → 放行；`<调查>` 事件出现 |
| **TC-3 多症状抓主要矛盾** | 抛一个含 2-3 个症状的复合问题 | 日志 `contradiction principal=<id> attack_order=[...]`；`<主要矛盾>` 事件；计划首步对准 principal；截图前端"主攻"卡片（若渲染） |
| **TC-4 异体自检** | 让桌宠完成一个可验证产物类任务，诱导其"假装完成" | 日志 `self_check heterogeneous=true passed=false`（异体子代理打回）→ 二次修正 → passed=true；确认评分 provider ≠ 主 LLM（`model=` 不同） |
| **TC-5 收敛止损** | 构造一个解不动的问题逼近迭代上限 | 日志 `convergence converged=false principal_resolved=false stop_reason=budget`；桌宠输出**诚实止损报告**（"卡在哪+建议"）而非假装完成 |

- **失败 retry ≥3 次不同 workaround 才能标"环境受限"**；跳过任何 case 须显式声明 + 理由 + 等用户确认。
- **中文输入 workaround**：STA Runspace + `Clipboard.SetText` + Ctrl+V；焦点不在目标窗口先 Click 聚焦。

---

## 2. 灰度路径（对齐 verify_gate shadow→strict 经验）

```
阶段0  enabled=false                    纯回退，仅合代码，L0 守零回归
阶段1  enabled=true, *.mode=shadow      跑全步但不阻塞、只发事件+日志（观测真实触发率/误判率）
       observability_events=true        前端可选渲染"思考过程"
阶段2  self_check.mode=light            轻量门控（取证门控 on、自检轻量、收敛报告 on）
       evidence_gate=true
阶段3  self_check.mode=strict           全门控 + 异体自检（仅对 debug/creation 类）
```

- **每阶段升级前置条件**：上一阶段真机 E2E 全 PASS + shadow 日志确认"闲聊不触发、误判率 < 阈值"。
- **出厂建议**：阶段0（off）ship 代码，阶段1（shadow）作为下一里程碑灰度，不一次到 strict（防误伤）。
- **per-problem_type 差异化**（具体问题具体分析）：chitchat 永远短路；factual_qa 仅 Step1+轻自检；debug/research/multi_task/creation 走全链。

---

## 3. BC 守护清单（逐处，落地时勾选）

- [ ] `config.py` 新 flag 全部默认 False/off（`ProblemPipelineConfig` 字段默认值单测）。
- [ ] **（B2）** `config.py:_MIGRATABLE_SECTIONS`（@:775）末尾加 `("features","problem_pipeline")`——嵌套子表**必须单列一条**（逐 key 回填对 sub-table `continue` skip，已核实 :870-871；同 `("memory","v2")`/`("memory","v2","facts")` 双条）。否则存量 install 永远拿不到默认 off → 灰度开不了。单测：对缺该段的存量 config，backfill 后整段 `[features.problem_pipeline]` 被 copy 进来（含默认值）。
- [ ] **（B1）** `context.py:_VALID_SERVICES`（@:9）加 4 个 key + `ServiceContext` dataclass（@:89）加 4 个 `Any|None=None` 字段（problem_pipeline / pipeline_evidence_gate / pipeline_self_check_gate / pipeline_convergence_controller）。所有写入用 `register(name,obj)`（**禁止下标**，dataclass 无 `__setitem__`）；flag off 时也 `register(name,None)` 占位。单测：`register(...,None)` + `get(...)` 均不抛 ValueError。
- [ ] flag off 时：`main.py` PRE-LOOP 编排早 return（不构造 IntentTriage/ContradictionAnalyzer）。
- [ ] flag off 时：`agent_loop.py` 守门链走原 VerifyGate/external_evaluator 块（SelfCheckGate/EvidenceGate/ConvergenceController 不介入）。
- [ ] flag off 时：`plan.py` 走原 code-only 逻辑（companion 分支不进）。
- [ ] flag off 时：不发任何 `chat_v2_intent/contradiction/evidence_gate/selfcheck/convergence` 事件。
- [ ] L0 字节级快照比对通过。

---

## 4. 回滚

- **代码级**：全部改动在 flag 后；线上异常 → 一条 config 改 `enabled=false` 即恢复（无需回滚部署）。
- **数据级**：本期不改任何持久化 schema（IntentCard/ContradictionMap 仅运行时内存 + WS 事件，不落库）→ 无数据迁移、无回滚负担。
- **commit 级**：每 WI 独立 commit，可单独 revert。

---

## 5. 完成判据（DoD）

1. L0 零回归（★ 一票否决）。
2. L1 全部新单测绿；闲聊不调 LLM 断言通过。
3. L2 live smoke 跨层契约一致。
4. L3 真机 5 个 TC 全 PASS（截图 + 日志硬证据），闲聊不拖慢有时间戳对比证据。
5. 更新 `STATUS/status.md` §3 新增"问题处理流水线"模块行 + §4 里程碑（对齐项目 STATUS 纪律）。
6. 灰度阶段0 ship；shadow 灰度计划记入下一里程碑。
