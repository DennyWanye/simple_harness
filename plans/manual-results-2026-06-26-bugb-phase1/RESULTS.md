# BUG-B Phase 1 — windows-mcp 真机手测结果

> **执行**：2026-06-26（本地 05:53–06:20）
> **方式**：真坐标鼠标点击 + 剪贴板中文/emoji 输入（Ctrl+V）+ 真点发送 + tauri-dev.log 结构化事件 grep。
> **被测进程**：worktree 当前代码（`[backend_launch] Dev python=...backend` 已确认，非 frozen）；`problem_pipeline_init enabled=true`。
> **默认配置**：analysis_model=deepseek-v4-pro、analysis_timeout_s=45s、非流式预分析、无 bypass env。
> **判定口径**：每条消息读其时间戳对应的 intent_triage/pipeline 事件（累计计数差）。
> 日志：`tauri-dev.log`（TC-A/B/C，clean 配置）、`tauri-dev2.log`（TC-D1，临时打挂 analysis_model）。

## 结果总览：15/15 TC PASS（★5 全 PASS），0 FAIL

| TC | ★ | 输入 | 关键证据（log） | 判定 |
|---|---|---|---|---|
| TC-A1 | ★ | 你好呀 | `allowlist_hit`(preview=你好呀) + `pipeline.short_circuit problem_type=chitchat` + `pipeline_short_circuit`；该轮 **done=0/llm_failed=0/parse_failed=0**（0 次 LLM）| **PASS** |
| TC-A2 | | 晚安 / 谢谢 | 各 +1 `allowlist_hit`，无新 `done`/`llm_failed` | **PASS** |
| TC-A3 | | 你好呀~ / 晚安啊 / 在吗在吗 | +3 `allowlist_hit`，无新 `done`；**在吗在吗 短路**（R2 决策验证：吗 未被裸否决）| **PASS** |
| TC-A4 | | 😄😄 / 。。。 | +2 `allowlist_hit`（纯 emoji/纯标点放行），无新 `done` | **PASS** |
| TC-B1 | ★ | 光合作用为什么需要光 | **无 allowlist_hit** + `done problem_type=factual_qa short_circuit=False` + `pre_loop_done injections=1`（流水线真跑）| **PASS** |
| TC-B2 | ★ | 刚那段为什么越界 / 我这段Python代码越界IndexError | 无 allowlist_hit、无短路；前者 `done problem_type=ambiguous`(context-free→clarify)，后者 `done problem_type=debug short_circuit=False has_contradiction=True` + pre_loop_done | **PASS** |
| TC-B3 | | 崩了 / 报错 / 卡死 | 三条 **均无 allowlist_hit**、无短路（故障词否决生效，全走 LLM）| **PASS** |
| TC-C5 | ★ | 你好，帮我看下这段为什么报错 | **无 allowlist_hit**（不被"你好，"前缀骗）+ `done problem_type=debug short_circuit=False` | **PASS** |
| TC-C6~C10 | | 谢谢那这个报错怎么办 / 在吗？我代码崩了 / hi 帮我 debug / 你好我想问下钠离子电池原理 / 请帮我查一下今天天气 | 5 条 **均无 allowlist_hit**、无短路（含 1 条 parse_failed→safe-fail 亦**不短路**）| **PASS** |
| TC-C11 | | 你好 / 你好啊在干嘛 | 你好 → +1 allowlist_hit（确定短路）；你好啊在干嘛 → **无 allowlist_hit**（保守假阴性），落 LLM 判 chitchat 短路。allowlist 无假阳性 | **PASS** |
| TC-D1 | ★ | （打挂 analysis_model=gpt-does-not-exist-zzz）帮我分析为什么这段排序代码会越界报错 | `intent_triage.llm_failed error='LLM HTTP 503'` + **无 pipeline_short_circuit** + 无 clarification_pause + `pre_loop_done`（裸 ReAct）+ 桌宠正常回复"把那段排序代码贴给我就行…"（无崩溃）| **PASS** |

## 一票否决项（★）逐条
- **TC-A1 闲聊 0 LLM allowlist 短路**：✅ allowlist_hit=1 + 该轮 done/llm_failed/parse_failed=0（双向证 0 次 LLM）。
- **TC-B1 真问题不误短路**：✅ allowlist_hit=0 + done factual_qa short_circuit=False + 流水线真跑。
- **TC-B2 中文 debug 不误短路**：✅ debug short_circuit=False（headline BUG-B 防回归点通过）。
- **TC-C5 allowlist 不被前缀寒暄骗**：✅ "你好，"开头的真求助 allowlist_hit=0，整句锚定 fullmatch + 否决优先生效。
- **TC-D1 safe-fail 不误判闲聊短路**：✅ llm_failed + **绝不短路**，桌宠裸 ReAct 仍答（WI-2b §0 取证门漏洞修复验证）。

## 证据
- 截图：`screenshots/TC-A1-02-reply.png`、`TC-A2-01-wanan-reply.png`、`TC-A3A4-emoji-dots-reply.png`、`TC-C-disguised-replies.png`、`TC-D1-safefail-reply.png`。
- 日志：`tauri-dev.log`（累计 allowlist_hit=9 / done=13 / short_circuit=10 / parse_failed=1 / **llm_failed=0**，clean 配置全程 0 预分析失败）、`tauri-dev2.log`（TC-D1：allowlist_hit=0 / llm_failed=1 / short_circuit=0）。
- grep 工具：`p1grep.py`（UTF-16LE 解码 + 逻辑行去 wrap + 事件计数）。

## 测后还原
- config.toml 已从 `config.toml.bugb-phase1-bak` 还原（analysis_model 覆盖行已删，grep 验证 0 命中 broken model）。
- 重启 clean 配置确认 `problem_pipeline_init enabled=true`（tauri-dev3.log）。

**结论**：Phase 1 windows-mcp 真机手测 15/15 PASS、★5 全过、0 FAIL。allowlist 在真实运行栈内：闲聊确定短路 0 LLM、真问题/伪装寒暄/故障句绝不误短路、safe-fail 不退化成闲聊短路。
