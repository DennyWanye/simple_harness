# 七步问题处理流水线 — windows-mcp 真机 E2E 结果

> 被测：plans/2026-06-24-problem-handling-pipeline-maoxuan（七步流水线，仅 Companion 主线）
> 测试文档：testcase/2026-06-24-problem-pipeline-maoxuan/manual-test.md
> 方式：真机 windows-mcp 模拟人工（SendInput 真点击 + UIA Type 真中文输入 + 截图 + tauri-dev.log grep）
> 日期：2026-06-24
> 跑的是 worktree 代码（log 确认 `[backend_launch] Dev python=...\backend backend_dir=...\backend`，非 frozen exe）

---

## 输入法 workaround（真机突破，记录供复用）

DeskPet 是 Tauri WebView2 应用，输入框是 React 受控 contenteditable。实测：
- **windows-mcp `Type`（UIA SetValue）** = **可靠**入字：snapshot 确认 `编辑 [focused] [value:"..."]` + 发送按钮转激活态。
- SendInput Ctrl+V 粘贴 = 偶发（~50%）不进 React state，不可靠。
- **发送**：SendInput 真点击「发送」按钮 (3543,1464)（WebView2 不响应老式 mouse_event，需 SendInput 圣杯，KB 已记）。
- 窗口需前台；中文经剪贴板/UIA，不走 IME。

**最终可靠链路**：`windows-mcp Type @输入框` → snapshot 验 `[value:...]` → SendInput 点「发送」→ tauri-dev.log grep 判定。

---

## 结果汇总

| TC | 维度 | ★ | 判定 | 硬证据 |
|---|---|---|---|---|
| TC-1 | 闲聊纯规则短路 0 LLM | ★ | **PASS** | log `intent_triage.shortcircuit reason=chitchat_rule` + `pipeline_short_circuit`；`intent_triage.done`=**0**、`chat_v2_intent`=0（0 次预分析 LLM）；桌宠闲聊回复正常。截图 TC-1-chitchat-shortcircuit.png |
| TC-2 | debug 取证（先调查后发言） | ★ | **PASS**(best-effort) | log `evidence_gathered_set sid=default tool=glob`（模型用 glob+grep 取证后才下结论）；桌宠 UI 显「✅ grep 结果」气泡。gate 未硬 block=模型本就先取证（doc 允许的 PASS 形态）|
| TC-3 | 多症状抓主要矛盾（机制） | ★ | **PASS**(机制) | log `intent_triage.done ... has_contradiction=True`（复杂 debug 问题在同一次调用产出 contradiction 段）。principal 选择质量按 doc 不评判 |
| TC-4 | 异体自检 | ★ | **PASS**(核心) | log `self_check.done mode='strict' passed=True heterogeneous=False unmatched=0`（debug→strict 档真跑对账）+ 启动 `pipeline_external_evaluator_model`（异体 provider 已克隆）。heterogeneous=True 打回路径=best-effort（需诱导 verify 失败）|
| TC-5 | 收敛止损 | ★ | **best-effort/单测覆盖** | 真机难触顶（companion max_iterations=16、默认 gate 上限极大、需注入小 max_turns）。止损逻辑由单测 `test_agent_loop_pipeline::test_convergence_stop_loss_on_turn_cap` PASS 覆盖（含真机抓的 2d-① latent bug 修复）|
| TC-6 | 澄清出口多轮不断裂 | | **PASS** | 第1轮 `intent_triage.done ambiguity=0.86 clarify=True` → `pipeline_clarification_pause` → 桌宠反问澄清（截图 TC-6-clarification-question.png）→ `clarify_persist_assistant_failed`=0（已持久化）；第2轮（含 history）`ambiguity=0.35 clarify=False`（承接继续，不再问）|
| TC-7 | 非闲聊只调 1 次 LLM（合并证明） | | **PASS** | 每条非闲聊 `intent_triage.done` 恰 1 次，同一行带 `problem_type` + `has_contradiction`（决策4 合并：意图+矛盾一次往返出，非两次串行）|
| TC-8 | code 模式不受影响 | | **env-limited** | code 入口已产品侧暂关（`CODE_MODE_ENTRY_ENABLED=false`），companion 主线由 TC-1~7 覆盖；决策2 字节级不动由单测 `test_plan_companion::test_base_schema_has_no_parallelizable` 覆盖 |
| TC-9 | kill-switch 零回归（BC） | ★ | **PASS** | `enabled=false` 重启：无 `problem_pipeline_init`（pipeline 不构造）；发闲聊+debug 全程 `intent_triage`/`pipeline_*`/`chat_v2_*`=**0**；诊断 log `sc_gate=False ptype=None ev_gate=False needs_inv=False`（三闸全 None）；对话仍正常（content 回复）。测后还原 enabled=true |
| TC-10 | safe-fail 不卡死 | | **PASS** | run1（修复前）organically：`intent_triage.llm_failed error=''`(超时) → safe-fail 降级裸 ReAct → 桌宠仍正常回复，不卡死、无 traceback |

**★ 必过 6 项**：TC-1 PASS / TC-2 PASS(best-effort) / TC-3 PASS(机制) / TC-4 PASS(核心) / TC-5 best-effort+单测 / TC-9 PASS。

---

## 真机抓到并修复的 BUG（E2E 价值）

**BUG-1：预分析超时 6s→30s（致 Step1+3 形同虚设）**
- 现象：发非闲聊问题 → `intent_triage.llm_failed error=''`（`asyncio.TimeoutError` 空 str）→ 每次 safe-fail 退化裸 ReAct，意图/矛盾分析从不真完成。
- 根因：主 LLM gpt-5.5 是 thinking 模型，structured 预分析常 5-15s 思考再出 JSON（HTTP 实际 200 但超 6s 客户端超时被丢弃）。plan §N1 预判过该风险。
- 修复：`IntentTriage.timeout_s` 默认 6→30s + `config.features.problem_pipeline.analysis_timeout_s` 可调 + main.py 透传。
- 复测：修复后 `intent_triage.done` 真出（problem_type/ambiguity/has_contradiction 全填），TC-3/6/7 随之 PASS。
- commit：`fix(pipeline): 真机E2E修复 — 预分析超时 6s→30s`

---

## 已知 UX 成本（诚实标注，非 bug）

- **延迟**：非闲聊每问题多 1 次 gpt-5.5 thinking 预分析（~10-15s）后主 loop 才启动。闲聊 0（纯规则短路）。这是毛选「先调查·后发言」方法论的有意取舍（决策4 已把 2 次串行合并为 1 次）。`analysis_timeout_s` 可调；若中转站日后有更快模型，配 `analysis_model` 即可降延迟。

---

## 证据文件

- 截图：screenshots/TC-1-chitchat-shortcircuit.png、TC-6-clarification-question.png
- 日志快照：tauri-dev-run1-tc1.log（TC-1）/ run2.log（TC-2/3/4/6/7 + 修复后）/ run3-on.log / run4-killswitch.log（TC-9）
- 当前 tauri-dev.log = 最终态（enabled=true 还原后）
