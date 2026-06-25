# 七步问题处理流水线 — 生产上线验收 + 幂等专项 windows-mcp 真机 E2E 结果

> 被测：testcase/2026-06-25-problem-pipeline-production/manual-test.md（生产门 + IDEM-1~6 幂等）
> 方式：真机 windows-mcp（UIA Type 真中文输入 + SendInput 真点击发送 + 截图 + tauri-dev.log grep）
> 日期：2026-06-25　跑 worktree 代码（log 确认 `[backend_launch] Dev python=...\backend`，非 frozen）
> 主 LLM = gpt-5.5 via relay(chinzy.com)

---

## 0. 真机暴露并定位的 3 个真问题（E2E 的核心价值）

### BUG-A：relay 间歇 502 on `stream:True + json_schema strict`（致预分析挂超时）
- 现象：发非闲聊问题 → 预分析 LLM 调用挂起 90s 后 `intent_triage.llm_failed error=''`（asyncio 超时），主流式对话却正常 200。
- 定位（脚本 `backend/scripts/probe_*.py` 直连 relay 复现）：
  - `provider.chat_with_tools` **强制 stream:True**（openai_compatible.py:262-275，为绕代理掐 idle）。
  - relay 对 **gpt-5.5 + stream + json_schema strict** 间歇返回 **502/503**（probe 实测：同一请求一次 200/12s、一次 502/chunks=0）。
  - 对照：`非 stream + json_schema` 200/14s；`stream + json_object` 200；其它模型 `stream+json_schema` 多数 200（gpt-5.4-mini 10.5s / deepseek-v4-pro 18.8s）。
- 结论：**非 relay/网络全挂，而是 relay 对该特定组合不稳**；预分析挂超时时降级裸 ReAct（safe-fail 生效，见下）。
- 真测 workaround（决策3 口子）：`[features.problem_pipeline].analysis_model` 改用更稳模型（gpt-5.4-mini→后用 deepseek-v4-pro）→ 预分析恢复（`intent_triage.done` 真出）。

### BUG-B：组装期 TaskClassifier 对非闲聊问题 fail-open 成 `chat` → 被流水线误当闲聊短路 ★
- 现象：无 code 关键词的真实非闲聊问题（"光合作用…"、纯中文 debug 追问）被判 `task_type='chat'` → 派生 chitchat → `intent_triage.shortcircuit reason=chitchat_rule` → **整条流水线跳过**。
- 定位：classifier 3 层级联 rule→embed→llm→default('chat') 全失效：
  1. rule 层只认 code/报错/python/搜索/计划/情绪 关键词，无关键词的问题 miss；
  2. embed 层依赖组装期 embedder，**真机 4 次 `component='memory' status='timeout'`**（lock 竞争）→ 返回 None；
  3. **llm 层在 main.py:1977 `build_default_assembler(llm_registry=None)` 被彻底关掉**（且默认 `llm_model='claude-haiku-4-5'` 当前 relay 根本没有）；
  4. → 默认 `chat`（classifier.py:267-273）。
- 影响：以前 'chat' 只影响上下文组装无害；七步流水线上线后 'chat'→chitchat 短路 = **真实问题被静默丢进闲聊快路径，流水线形同虚设**。fail-open 到 chitchat。
- followup：已 spawn `task_742d3399`（修 classifier 词法兜底 / llm 层 / 短路前二次确认）。

### BUG-C：预分析 LLM 被 classifier 的 `chat` hint 带偏 → 级联误判 chitchat
- 现象：即便绕过早期纯规则短路，预分析 prompt 注入 `[系统初判类型] chat → chitchat`（intent_triage.py:210）后，LLM（gpt-5.4-mini / deepseek 都中招）继续返回 `problem_type=chitchat` → 下游 `short_circuit=True`。
- 即 BUG-B 的下游放大：classifier 错 → hint 错 → 预分析跟着错。
- 真测处理（用户拍板"关闭闲聊短路，逻辑后续 followup"）：加 env 开关 `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT=1`（intent_triage.py，默认未设=字节 BC），开关额外把 chitchat 派生在 **hint 前 + LLM 返回后**都重映射成 `factual_qa`，真正强制所有消息走完整非闲聊流水线。开关验证：发"光合作用…" → 修复后 `intent_triage.done problem_type=factual_qa`（不再短路）✓。

---

## 1. 结果汇总

> 经 BUG-A workaround（analysis_model=deepseek-v4-pro）+ BUG-C 开关（强制非闲聊）后，非闲聊流水线可稳定触发。

| TC | 维度 | ★ | 判定 | 硬证据 |
|---|---|---|---|---|
| **IDEM-1** | 重复发同一闲聊 N 次预分析副作用恒 0 | ★ | **PASS** | 连发同一闲聊×3 → `pipeline_short_circuit`×3 + `intent_triage.shortcircuit`×3；**`intent_triage.done`/`llm_failed`/`chat_v2_intent`/`evidence_gathered_set`/`self_check.done`/`convergence.stop_loss` 全=0**（预分析 LLM 副作用恒 0，无 IN-LOOP 残留）。截图 IDEM-1-* |
| **TC-1**(control) | 非闲聊只调 1 次预分析（决策4 合并） | ★ | **PASS** | 非闲聊 debug → `intent_triage.done` 恰 1 次，同行含 problem_type+has_contradiction（合并一次往返，非两次串行）|
| **TC-7** | factual_qa done=1 + 简单问题不填矛盾段 | | **PASS** | "光合作用…" → `intent_triage.done problem_type='factual_qa' ambiguity=0.07 clarify=False has_contradiction=False`（简单问题省 token 不填矛盾）。截图 TC-7-* |
| **TC-3** | 多症状抓主要矛盾（机制接通） | ★ | **PASS**(机制) | debug/复杂问题 `intent_triage.done has_contradiction=True`（×2：runE 排序 + runI 导出 Excel）。principal 选择质量按 doc 不评判 |
| **TC-6** | 澄清出口（needs_clarification） | | **PASS** | 多条 debug → `ambiguity≥0.7 clarify=True` → `pipeline_clarification_pause`；答澄清后**承接继续**（桌宠出"把 arr[i] 加边界限制"实质答复，`clarification_pause` 第2轮=0 不重复问）。`clarify_persist_assistant_failed`=0。截图 IDEM-3/TC-6-* |
| **TC-4** | 异体自检（非执行者打分） | ★ | **PASS**(best-effort) | is_prime creation 任务 → `self_check.done mode='strict' passed=True heterogeneous=False` + 启动 `pipeline_external_evaluator_model`（异体 provider 已克隆）。heterogeneous=False=任务一次就对没诱发打回（doc 允许的 best-effort 形态）。截图 TC-4-* |
| **TC-9** | kill-switch（enabled=false）零回归 BC | ★ | **PASS** | enabled=false 重启：**无 `problem_pipeline_init`**（pipeline 不构造）；发 debug 全程 `intent_triage`/`pipeline_*`/`evidence_*`/`self_check`/`convergence`/`chat_v2_*`=**0**；对话仍正常（chat_stream 工作）。测后还原 |
| **IDEM-5/TC-10** | safe-fail 不卡死 | ★ | **PASS**(organic) | BUG-A 触发时 organically：`intent_triage.llm_failed`(超时) → 降级裸 ReAct → 桌宠仍正常回复（出澄清卡），不卡死、无 traceback。截图 ENV-relay-* |
| **TC-2 / IDEM-2** | debug 取证门控 | ★ | **best-effort/未触发** | 4 次尝试：under-specified debug 问题 **Step1 澄清门（clarify=True）先于 Step2 取证门触发** → 走 `pipeline_clarification_pause` 没进 IN-LOOP evidence gate。gate 本身已接线（上次会话单测 `test_agent_loop_pipeline` PASS 覆盖）。真机难凑"specific 到不澄清 + 又确实 needs_investigation + 模型还想直接猜"的窄触发点（doc 已标 best-effort）|
| **TC-5 / IDEM-6** | 收敛止损 | ★ | **未测（需源码改 main.py:6576）** | 默认 max_turns=10000 自然跑逼不出；唯一可达 stop_loss=改源码 `else 16→4`（hallucination 走 ErrorEvent 不出 stop_loss，第2轮挑战已证）。本轮未做源码改 |
| **IDEM-3**(重启段) | 澄清持久化重启后不丢 | ★ | **部分**(未重启验) | 单 send 不双写 + 答澄清承接已验（见 TC-6）；**重启后 history 持久化**段未单独跑 |
| **IDEM-4** | 重启后 per-run 标记不残留 | ★ | **未测** | 需"埋 debug 取证态→重启→再 debug 验重新取证"，依赖 evidence gate 能触发（同 TC-2 受阻）|

---

## 2. 上线门结论（诚实口径）

- **已真机 PASS 的 ★**：IDEM-1（幂等）/ TC-1（合并预分析）/ TC-3（抓主要矛盾机制）/ TC-4（自检 best-effort）/ TC-9（kill-switch BC）/ IDEM-5（safe-fail）。
- **机制接通但真机触发受阻**：TC-2 取证门（被 Step1 澄清门拦截在前）/ TC-5 收敛止损（需源码构造）/ IDEM-3 重启段 / IDEM-4 → 这些**逻辑已由上次会话单测覆盖**，真机触发受 LLM 行为 + 上面 3 个环境/跨层 bug 影响。
- **3 个真 bug**（BUG-A relay 502 / BUG-B classifier fail-open / BUG-C hint 带偏）是本轮 E2E 的最大产出 —— 其中 **BUG-B 直接威胁流水线在生产的可用性**（真实非闲聊问题被误短路），已 followup `task_742d3399`。
- **上线建议**：BUG-B 修复前，七步流水线对"无关键词非闲聊问题"形同虚设；应优先修 classifier fail-open，再回归 TC-2/4/5 真机触发。

---

## 3. 真测环境处置（测后状态）

| 项 | 状态 |
|---|---|
| `analysis_model` | = `deepseek-v4-pro`（BUG-A workaround，gitignored userdata config；让预分析可用。可回 `""` 但 gpt-5.5 当前 relay 不稳）|
| `analysis_timeout_s` | = 90.0（relay 慢时给足时间，gitignored config）|
| `enabled` | 已还原 = true |
| `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` | = 1（仅本测 launcher 注入 + 代码默认 off；用户拍板暂关闲聊短路，逻辑 followup）|
| 代码改动 | intent_triage.py 加 env 开关（默认 off=字节 BC），已 commit |
| probe 脚本 | backend/scripts/probe_preanalysis_*.py / probe_stream_schema.py（relay 诊断，可复用）|

---

## 4. 证据文件

- 截图：screenshots/IDEM-1-*（闲聊幂等）/ TC-7-factual-* / TC-4-selfcheck-* / TC-3-TC-6-* / IDEM-3_TC-6-* / ENV-relay-*（safe-fail）
- 日志：tauri-dev-runA~runI.log（runA 默认 / runB-D 调 timeout+proxy / runE gpt-5.4-mini / runF killswitch / runG 开关验证 / runH-I deepseek）
- relay 诊断 probe：backend/scripts/probe_preanalysis_models.py / probe_preanalysis_exact.py / probe_stream_schema.py
</content>
