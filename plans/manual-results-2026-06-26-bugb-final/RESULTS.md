# BUG-B 最终验收（§6 全功能）— windows-mcp 真机手测结果

> **执行**：2026-06-26（本地 06:52–07:00）
> **被测代码**：master HEAD（含 Phase 1+2+3 全部改动；`[backend_launch] Dev python` 非 frozen，`assembler_classifier_llm_injected` 已接电）。
> **默认配置**：analysis_model=deepseek-v4-pro、非流式预分析、无 bypass env（BUGB-4 临时打挂后已还原）。
> **方式**：真坐标点击 + 剪贴板中文输入(Ctrl+V) + 真点发送 + 结构化日志事件 grep + 截图。
> 日志：`../manual-results-2026-06-26-bugb-phase1/tauri-final.log`（BUGB-1/2/3/6，clean）、`tauri-final-bugb4.log`（BUGB-4，临时坏 analysis_model）。

## 结果：plan §6 ★5 全 PASS，0 FAIL —— 收敛标准达成，可上线

| TC | ★ | 输入 | 硬证据（log）| 判定 |
|---|---|---|---|---|
| BUGB-1 | ★ | 光合作用为什么需要光 | `intent_triage.done problem_type='factual_qa' short_circuit=False`（非 safe-fail）+ `pipeline.pre_loop_done`（流水线真跑）+ 无 allowlist_hit | **PASS** |
| BUGB-2 | ★ | 我这段Python代码列表越界为什么报IndexError | `intent_triage.done problem_type='debug' short_circuit=False` + 无 allowlist_hit | **PASS** |
| BUGB-3 | ★ | 你好呀 | `intent_triage.allowlist_hit` + `pipeline.short_circuit problem_type=chitchat` + `pipeline_short_circuit`；该轮**无 done/llm_failed/parse_failed**（0 次 LLM）| **PASS** |
| BUGB-4 | ★ | （坏 analysis_model）帮我分析为什么这段排序代码会越界报错 | `intent_triage.llm_failed error='LLM HTTP 503'` + **无 pipeline_short_circuit** + 无 clarification_pause + `pre_loop_done`（裸 ReAct）+ 桌宠正常答"把排序代码和报错贴给我就行，我帮你逐行看。" | **PASS** |
| BUGB-6 | ★ | 帮我看这段 python 为何 IndexError | `assembler_task_classified task_type='code'`（非 chat，classifier 复活）+ `intent_triage.done problem_type='debug' short_circuit=False`（流水线真跑）| **PASS** |

## 收敛标准（plan §6）逐条
- ✅ **上述 ★ 全 PASS**。
- ✅ **组装期 `task_type` 对真 code/debug 不再恒 `chat`**：BUGB-6 `task_type='code'`；BUGB-2 组装 `code`。修复前 `llm_registry=None`+embed 撞锁 → 恒 `chat`。
- ✅ **闲聊 0 次 LLM**：BUGB-3 `allowlist_hit` + 该轮无 `intent_triage.done`/`llm_failed`（双向证 0 次预分析 LLM）。

## Phase 3 无回归确认
- WI-9b（预分析 prompt 去"系统已判定的初步任务类型" hint）后，BUGB-1 仍判 `factual_qa`、BUGB-2/6 仍判 `debug`/`code` —— deepseek 裸判未退化，分流与 Phase 1/2 一致。
- WI-8a（单一来源护栏注释）：`_TASKTYPE_TO_PROBLEM` 仅 safe-fail fallback 用（BUGB-4 safe-fail 走该兜底，仍**不短路**），正常路径不接回当 hint，无 BUG-C 复活。

## 证据
- 截图：`screenshots/FINAL-bugb1236-replies.png`（clean 四问回复）、`FINAL-bugb4-safefail-reply.png`（safe-fail 裸 ReAct 回复）。
- 日志：`../manual-results-2026-06-26-bugb-phase1/tauri-final.log`（allowlist_hit=1 / done(factual_qa,debug,debug) / short_circuit=1 / assembler code×2,chat×2 / llm_failed=0）、`tauri-final-bugb4.log`（allowlist_hit=0 / llm_failed=1 / pipeline_short_circuit=0）。

## 测后还原
- config.toml 已从备份还原（analysis_model 覆盖删，grep 0 命中），重启 clean（tauri-final-clean.log）确认 `problem_pipeline_init enabled=true` + `assembler_classifier_llm_injected`。

**结论**：BUG-B plan（Phase 1 闲聊快路径 + Phase 2 classifier 复活 + Phase 3 收口）整体 windows-mcp 真机最终验收 **★5 全 PASS、0 FAIL，收敛标准达成**。
