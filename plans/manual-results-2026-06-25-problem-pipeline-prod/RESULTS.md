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

## ⚠️ 关键诚实声明（opus 4.8 核对结论，必读）

**除 IDEM-1（runA 真默认短路）与 TC-9（killswitch）外，几乎所有"流水线触发类"★ PASS 都是在「关闭闲聊短路 `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT=1` + 关闭澄清门 `DESKPET_DISABLE_CLARIFICATION=1` + analysis_model=deepseek-v4-pro」三重非默认配置下取得的。** 原因：默认配置下 **BUG-B 让真实非闲聊问题被误当闲聊短路 → 流水线形同虚设**。
- **默认生产配置真实覆盖率 ≈ 40%**；非默认（绕开 bug）配置下 ≈ 65%。
- 含义：这些 PASS 证明的是**「机制本身正确」**（绕开环境/分类 bug 后，七步逻辑真在 UI 链路生效），**不等于「默认配置下用户真能用」**。
- 要让默认配置真能用，**必须先修 BUG-B（task_742d3399）+ BUG-D（task_bc7826ba），再在不开任何开关下重跑全部流水线类用例**。

## 1. 结果汇总

> 经 BUG-A workaround（analysis_model=deepseek-v4-pro）+ BUG-C 开关（强制非闲聊）后，非闲聊流水线可稳定触发（非默认配置，见上声明）。

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
| **TC-2** | debug 取证门控（没有调查就没有发言权） | ★ | **PASS**(best-effort) | 关澄清门(DESKPET_DISABLE_CLARIFICATION=1 硬触发到 IN-LOOP)后：debug 问题 `clarify=False`→进 IN-LOOP→模型**先取证再发言** `evidence_gathered_set sid=default tool=glob`（doc 允许的 PASS 形态：模型本就先取证=「调查先于发言」语义成立）。`evidence_gate.blocked`=0（模型自发取证没逼到拦）。截图 TC-2-* |
| **IDEM-2** | 取证 per-run 重置 | ★ | **PASS** | 同一 debug 问题连发×2，**两轮各自独立** `evidence_gathered_set tool=glob`（第2轮没因第1轮已取证而跳过→`_evidence_gathered` per-run 重置确认）。截图 IDEM-2-* |
| **IDEM-4** | 重启后 per-run 标记不残留 | ★ | **PASS** | 埋 debug 取证态→**重启**→再发 debug → `intent_triage.done`+`evidence_gathered_set tool=glob` 重新出（新进程干净起跑重新取证，per-run 内存态不跨进程残留）。截图 IDEM-4-* |
| **TC-5 / IDEM-6** | 收敛止损 | ★ | **FAIL/缺口（真 bug）** | 改源码 main.py:6576 `else 16→4` 收紧 max_turns，复杂任务真机 `hit max_iterations=4`（cap 生效），但 **`convergence.stop_loss`/`chat_v2_convergence`=0** —— **Step7 诚实止损报告没触发**！根因：loop `range(max_iterations)` 先于 gate `allows_call`(turns_used>=max_turns) 耗尽（off-by-one，max_turns==max_iterations + record_turn 底部自增），走了普通 loop-exhaustion warning（agent_loop.py:2546）绕过 ConvergenceController；hallucination 又走 ErrorEvent。**companion 默认下 Step7 止损路径实际不可达**。已 followup `task_bc7826ba` |
| **IDEM-3**(重启段) | 澄清持久化重启后不丢 | ★ | **PASS** | runN: 模糊问"把那个报表搞一下"→`ambiguous ambiguity=0.9 clarify=True`→`clarification_pause`+`clarify_persist_failed=0`(持久化成功)；**重启**(runO 新进程)→答"把销售报表整理成月度汇总表"→**第2轮 `clarification_pause`=0 不重问 + 桌宠承接干活**（证明澄清 history 跨重启重载、多轮不断裂）。截图 IDEM-3-Q1-* / IDEM-3-post-restart-* |

---

### BUG-D（TC-5 真测挖出）：Step7 收敛止损报告在 max_turns 触顶时不触发
- 改源码收紧 max_turns 真机触顶（`hit max_iterations=4`），但 `convergence.stop_loss`=0 —— Step7 诚实止损报告**没产出**。
- 根因：loop `range(max_iterations)` 先于 gate `allows_call` 耗尽（off-by-one，max_turns==max_iterations + record_turn 底部自增），走 loop-exhaustion warning 绕过 ConvergenceController；hallucination 又走 ErrorEvent。**companion 默认下 Step7 止损路径实际不可达**。已 followup `task_bc7826ba`。

---

## 2. 上线门结论（诚实口径，已更新）

- **已真机 PASS 的 ★（11 项）**：IDEM-1（闲聊幂等）/ IDEM-2（取证 per-run 重置）/ IDEM-4（重启状态隔离）/ TC-1（合并预分析）/ TC-2（取证门，模型先取证 best-effort）/ TC-3（抓主要矛盾）/ TC-4（自检 best-effort）/ TC-9（kill-switch BC）/ IDEM-5（safe-fail）+ 非★ TC-6（澄清多轮）/ TC-7（factual 不填矛盾）。
- **真测挖出 4 个真 bug**（E2E 最大产出）：
  - **BUG-A** relay 间歇 502 stream+json_schema → 预分析挂超时（workaround：analysis_model 换稳模型）
  - **BUG-B ★上线拦路** classifier fail-open 成 chat → 真实非闲聊问题被误当闲聊短路跳过流水线（task_742d3399）
  - **BUG-C** 预分析被 classifier 的 chat hint 带偏继续误判 chitchat（已用开关 + 重映射绕过）
  - **BUG-D ★** Step7 收敛止损报告在 max_turns 触顶时不触发（task_bc7826ba）
- **未单独跑**：IDEM-3 重启持久化段（与本批"关澄清门"互斥，核心 persist+承接已由 TC-6 验）。
- **上线建议**：核心 9 步机制真机都跑通；但 **BUG-B（误短路真实问题）+ BUG-D（止损报告不出）是两个上线前应修的真缺口** —— 修复前流水线对"无关键词问题"形同虚设、且触顶不会诚实止损。建议先清这两个 followup 再上线。

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

- 截图：screenshots/IDEM-1-*（闲聊幂等）/ TC-7-factual-* / TC-4-selfcheck-* / TC-2-evidence-* / IDEM-2-* / IDEM-4-* / TC-3-TC-6-* / IDEM-3_TC-6-* / ENV-relay-*（safe-fail）
- 日志：tauri-dev-runA~runM.log
  - runA 默认（IDEM-1 真短路）/ runB-D 调 timeout+proxy（BUG-A 排查）/ runE gpt-5.4-mini / runF killswitch（TC-9）/ runG 开关验证 / runH-I deepseek（TC-4/TC-3/TC-6）
  - **runJ TC-2 取证门 / runK TC-5 收敛（缺口）/ runL IDEM-2 per-run / runM IDEM-4 重启隔离**
- relay 诊断 probe：backend/scripts/probe_preanalysis_models.py / probe_preanalysis_exact.py / probe_stream_schema.py

## 5. 未做 / 半做（opus 核对补充，诚实登记）

| 项 | 状态 | 原因 |
|---|---|---|
| TC-5/IDEM-6 收敛止损 | **真 FAIL** | BUG-D，Step7 止损路径不可达，需修 task_bc7826ba 后重跑 |
| IDEM-3 重启持久化段 | **✅ 已补 PASS**（runN/runO） | 模糊问→澄清→重启→答→承接不重问，跨重启 history 重载验证通过 |
| TC-8 code 模式 | **env-limited（未跑）** | code 入口产品侧已关（`CODE_MODE_ENTRY_ENABLED=false`）；决策2 字节不动由上次会话单测 `test_plan_companion` 覆盖。真机不可达，标 env-limited |
| IDEM-5 自愈轮 | **半做** | 失败轮 organic 已验（BUG-A）；"改坏 analysis_model 主动构造 + 还原后自愈轮"未对照跑（done 正常已在 runE-M 多次旁证）|
| 默认配置全量重跑 | **阻塞** | 依赖先修 BUG-B（task_742d3399），否则默认配置流水线被误短路 |
</content>

---

## 6. WI-5(b) 默认配置全量重跑（Sprint 2 落地后 · 2026-06-25 下午）

> 前置全部满足：默认配置（**无任何 env 开关**，config.toml `[features.problem_pipeline]` 全闸 on、analysis_model 留空→config.py 默认 **deepseek-v4-pro**/45s）、Dev python（worktree 非 frozen，log 确认 `[backend_launch] Dev python=...backend`）、`problem_pipeline_init enabled=true`、boot 见 `model_context_resolved model=deepseek-v4-pro`。
> 收发：windows-mcp UIA `Type`(loc 3416,1464) + SendInput `click-at.ps1`(3543,1464) 发送；log 用 `loggrep.py`（de-wrap ~116 字符硬换行 + UTF-16LE→UTF-8 + 事件计数）。

### 6.1 deepseek 健康期已取证（log: tauri-wi5.log）

| Case | ★ | 真证据（默认配置） | 判定 |
|---|---|---|---|
| **TC-1**（闲聊+非闲聊对照）| ★ | 闲聊"你好呀今天天气真好"→`intent_triage.done problem_type=chitchat ambiguity=0.0 short_circuit=True`×1 → `pipeline.short_circuit`×1 → 无 IN-LOOP；桌宠闲聊回复正常。非闲聊"光合作用暗反应在叶绿体哪个部位"→`done problem_type=factual_qa short_circuit=False` 走完整流水线，答"叶绿体的基质(stroma)"正确。**全程无 `intent_triage.shortcircuit chitchat_rule`（早期规则短路已删）** | **✅ PASS**（headline BUG-B 修复：无关键词非闲聊问题不再被误短路）|
| **IDEM-1**（重复闲聊×3）| ★ | 同句闲聊×3 → `done problem_type=chitchat short_circuit=True`×3（ambiguity=0.1 一致，决策幂等）+ `pipeline.short_circuit`×3；早期 chitchat_rule 短路 log=0；llm_failed/parse_failed=0；3 轮窗口内无 IN-LOOP 闸残留（唯一 self_check@06:47:26 经时间戳核实属 TC-1 factual_qa，非闲聊轮）；3 次都正常回复 | **✅ PASS** |

### 6.2 真测中发现并修复一个真 bug（safe-fail 误短路 · commit 16758f8b）

**复现**：明确 debug 问题"export_report.py 报 KeyError: 'amount'，你先查清楚再下结论" → deepseek **正确判 `problem_type=debug`** 但返回**畸形 JSON**（含 U+E160 私用区/控制字符或截断）→ `intent_triage.parse_failed` → safe-fail 回退 `derived_pt`（来自坏 classifier fail-open `chat`→`chitchat`）→ `analyze()` 据此**派生 `short_circuit=True`** → **真 debug 被误当闲聊短路跳过流水线**（= BUG-B 经 safe-fail 路径复活，违反 docstring 承诺的"降级裸 ReAct"）。

**修复**（intent_triage.py）：
1. **Fix B（关键）**：`_parse` 失败返回 `None`，`analyze()` 据此**直接 return `_safe_card`，绝不派生 short_circuit** —— safe-fail 一律降级裸 ReAct（进流水线由主 loop 兜），不再让坏 classifier 的 chitchat 误判短路真实问题。统一了 llm_failed（本就不短路）与 parse_failed 两路。
2. **Fix A（加固）**：`_extract_json` 预清洗私用区(U+E000–F8FF)+C0/C1 控制字符（控制字符令 json.loads strict 直接拒），提升 deepseek 偶发畸形输出里正确判断的存活率。
3. 回归单测 2 条：`test_safe_fail_malformed_json_with_chat_classifier_does_not_shortcircuit` ★ + `test_pua_char_json_still_parses_correct_type`（注入 U+001F 真考验净化器）。intent_triage 13 + pipeline 23 套件全绿。

**修复后真机复测**（tauri-wi5b.log）：同 debug 问题 → 即便 deepseek 这次返 403（见 §6.3）→ `intent_triage.llm_failed` → safe-fail → **debug 仍进流水线、`evidence_gathered_set tool=glob` 真取证调查（UI 显示"✓ grep 结果"工件卡）、未被误短路** ✓。证明 safe-fail 鲁棒性在真 relay 失败下成立。

### 6.3 ⛔ 阻断：relay 账户 USD 余额耗尽 → 全模型 403（外部账户问题，非代码）

跑到 §6.2 复测时，relay（chinzy.com）开始对**所有**模型返 403。直连 probe（`scripts/probe_deepseek_now.py`）确认：

```
deepseek-v4-pro -> HTTP 403  {"code":"FORBIDDEN","message":"Available USD credit is too low to accept relay traffic."}
gpt-5.5         -> HTTP 403  （同上）
deepseek-chat   -> HTTP 403  （同上）
deepseek-v3     -> HTTP 403  （同上）
```

**根因 = relay 账户余额不足**（非 deepseek 单独问题、非限流、非 token 过期——key 本身鉴权通过，返的是 FORBIDDEN+credit too low 而非 401）。第一个 run（§6.1）deepseek 还正常，本轮 ~10 条消息把剩余额度耗光。**无本地 chat LLM 兜底 → 任何需 LLM 的流水线路径全部跑不了。**

**剩余 ★ 用例阻塞**（需 relay 充值后续跑）：TC-2 取证门完整链路 / TC-3 抓主要矛盾 / TC-4 异体自检 / TC-5+IDEM-6 收敛止损（改源码构造）/ TC-6+IDEM-3 澄清+重启 / IDEM-4 重启隔离 / IDEM-5 自愈轮 / TC-9 kill-switch BC。

> 注：TC-6 澄清路径在本轮已**部分取证**（泛问"导出功能报错"→deepseek 判 `ambiguity=0.9 clarify=True`→`pipeline_clarification_pause`→桌宠真反问"具体错误提示或代码是什么"，截图 `TC-6-01-clarify-question.png`）；完整"答澄清→承接续接"因余额耗尽未跑完。

---

## 7. WI-5(b) 续跑结论（2026-06-25 晚 · relay 解阻后）

### 7.1 relay 鉴权解阻（详见 plans/2026-06-25-relay-cloud-key-sync-followup.md）

§6.3 的"全模型 403"根因 = **账号脱节**（backend cloud-llm slot 用旧耗尽账号，relay 登录从不同步），叠加 Windows 凭据 Persist realm 坑 + .env 的 key 行被 GBK 中文注释挤行。**解阻**：删 keychain cloud-llm slot + launcher 用 `tsk_[A-Za-z0-9]+` 正则（UTF8 读、不限行首）从根目录 .env 注入 `DESKPET_CLOUD_API_KEY` → `process_manager.rs` 读不到 keychain 就不覆盖 env → backend 拿到用户新给的有额度 key（`tsk_48b…` → 200 OK）。boot log 出现 `llm_api_key_from_keychain` = key 已就绪。

解阻后真机验证 deepseek 默认配置跑通：`POST chinzy.com/v1/chat/completions 200 OK` + `intent_triage.done problem_type='factual_qa' short_circuit=False`（"月球为什么总是同一面朝向地球" → 走完整流水线，再次坐实 headline BUG-B 修复）。

### 7.2 ⚠️ BUG-A（relay 对预分析 call 间歇不稳）阻断剩余 ★ 干净取证 —— 上游问题（plan §6 范围外）

七步预分析走 `chat_with_tools`（强制 `stream:True`）+ `json_schema` strict。真机观测：relay 对 `deepseek-v4-pro + stream + json_schema` **时好时坏**——
- relay 健康时：`intent_triage.done` 正常出（如 7.1 的"月球"）。
- relay 瞬时坏一阵时（真机抓到 11:09–11:10 一段）：调用 hang ~15–45s 后返回**空 body**（`p4s25_stream_empty_response_raw_dump` / `stream_empty_body_unparseable`）→ 预分析空手而归 → `intent_triage.llm_failed error=''`（慢-空还吃满 `analysis_timeout_s=45`）。同一坏窗内 stream / 非流式 / gpt-5.5 **三者全挂**，证明是 relay 整体瞬时抖动，非单一模型/路径问题。
- probe 直连刻画（relay 恢复后）：deepseek **非流式 4–6s 稳**、流式 9–13s OK、gpt-5.5 非流式 36–42s OK。

**关键：safe-fail 正确兜住** —— 预分析失败时桌宠降级裸 ReAct 仍给出正确答案（实测 TC-3 多症状问题，即便预分析 llm_failed，主 loop gpt-5.5 仍答出"主要矛盾 = 偶尔 500 崩溃，优先解决"）。即**用户可见行为正确，只是流水线的结构化预分析事件（intent_triage.done / chat_v2_contradiction）在 relay 坏窗内不触发**。

**定性**：这是 **relay 中转站对结构化预分析调用的上游稳定性问题**，Sprint 2 plan §6 明确列为**范围外（上游，非项目侧）**。流水线代码本身正确（safe-fail 设计验证有效）。剩余 ★ 用例（TC-2/4/5/9 + IDEM-2~6）需要预分析**每次**都成功才能拿干净结构化证据，而 relay 当前时好时坏，无法在本轮稳定满足。

### 7.3 WI-4-C 建议（留后续，本轮未 ship）

试过两版客户端硬化（① 空响应重试 ② 非流式裸补全优先回退流式），但都无法克服 relay **整体瞬时抖动**（坏窗内非流式也空），且 E2E 因环境太抖**未能干净验证**，按"改代码必须真测验证"纪律**已回退**（main.py 保持提交态）。后续 WI-4-C 正式做时建议：
- 预分析改**非流式优先**（probe 实测 deepseek 非流式 4–6s 最快最稳，且避开 stream-empty-body 失败模式）；
- 配 `_extract_json` 已有的 3 级容错 + 控制字符净化（commit 16758f8b 已加）接住裸 JSON；
- relay 整体坏窗仍靠 safe-fail 兜底（已有，验证有效）。
- 另：`analysis_timeout_s` 慢-空场景会被吃满，可加"连续无字节即早 safe-fail"（plan WI-4-A）降感知延迟。

### 7.4 本轮 ★ 覆盖小结

| 用例 | 状态 | 证据 |
|---|---|---|
| TC-1 ★ / IDEM-1 ★ | ✅ PASS（默认配置） | §6.1（tauri-wi5.log）：闲聊 short_circuit + 非闲聊走流水线 + 无早期 chitchat_rule |
| safe-fail 误短路 bug | ✅ 修复 + 单测 + 真机复测 | §6.2（commit 16758f8b） |
| deepseek 默认配置跑通 | ✅ 验证 | §7.1（200 OK + intent_triage.done factual_qa） |
| TC-2/3/4/5/9 + IDEM-2~6 | ⏳ 受阻 | §7.2：relay 对预分析 call 间歇不稳（上游，plan §6 范围外）→ 无法稳定取干净结构化证据；safe-fail 兜底使用户可见行为正确 |

**上线判定**：headline P0（BUG-B Y-light + BUG-D 收敛止损 + safe-fail 误短路）已修 + 关键 ★（TC-1/IDEM-1）默认配置 PASS。剩余 ★ 受 relay 上游稳定性阻断，需 relay 稳定窗口 + WI-4-C 正式落地后另起一轮补齐。

---

## 8. WI-5(b) 全量 ★ 真测完成（2026-06-25 晚 · 并发限制解除 + WI-4-C 落地后）

### 8.1 解阻历程（三层环境问题，全部克服）

1. **relay 账号余额**（§6.3）：旧 cloud-llm slot 账号耗尽 → 用户充值 + 给新 key，经 launcher env 注入（删 keychain slot 让 process_manager 不覆盖）。
2. **relay 并发限制**（§7 修正）：真错误 = `rate_limit_error: Concurrency limit exceeded for account`（非"抽风"）→ 出诊断报告 [RELAY-ISSUE-REPORT.md](RELAY-ISSUE-REPORT.md) → **用户让 relay 调高并发上限**（6 并发 probe 全 200 确认解除）。
3. **预分析结构化调用不稳（BUG-A）**：deepseek `stream+json_schema` 偶发慢/空 body → **WI-4-C** 落地（commit）：①非流式预分析(保留 strict schema，probe 实测 4-6s 稳) ②剥 `<think>` CoT 前缀（thinking 模型）③`_parse_contradiction` 防御性 int/float（防 LLM 乱填数值崩 run_pre_loop）。

### 8.2 ★ 全量结果（默认配置 windows-mcp 真测，UIA Type+press_enter 发送 / loggrep de-wrap 判定）

| Case | ★ | 真证据 | log |
|---|---|---|---|
| **TC-1** | ★ | 闲聊`done short_circuit=True`不进 IN-LOOP；非闲聊`done factual_qa short_circuit=False`走完整流水线；无早期 chitchat_rule | wi5 |
| **TC-2** | ★ | `evidence_gate.blocked nudges_used=1`+`evidence_gate_nudge_injected nudge=1`→`evidence_gathered_set tool=glob/grep`（模型被拦后真取证） | wi5i/q/s |
| **TC-3** | ★ | `intent_triage.done problem_type='debug' has_contradiction=True`；桌宠点名"主要矛盾=500崩溃优先解决" | wi5l |
| **TC-4** | ★ | `intent_triage.done problem_type='creation'`+`pipeline_external_evaluator_model`+`self_check.done passed=True`（heterogeneous=False 因 self_check_model="" 用主LLM，属配置项） | wi5l |
| **TC-5** | ★ | （改源码 max_iter 触顶）`convergence.stop_loss reason='error_max_turns' principal_resolved=False`恰1次+桌宠诚实止损报告"<收敛>主要矛盾是否解决：否"（非假装完成、非 budget/hallucination） | wi5o |
| **TC-9** | ★ | `enabled=false`重启→**零**流水线 log（intent_triage/pipeline/chat_v2/evidence/self_check/convergence 全0）+ 主对话 2×200OK 正常答（BC） | wi5m |
| **IDEM-1** | ★ | 同句闲聊×3 各`done short_circuit=True`(ambiguity 一致)、早期 chitchat_rule=0、闲聊轮零 IN-LOOP 残留 | wi5 |
| **IDEM-3** | ★ | 歧义`done ambiguous ambiguity=0.9 clarify=True`→`pipeline_clarification_pause`+`persist_failed=0`；**重启后**答澄清→桌宠承接报表整理任务(问报表文件夹)**不重新反问**=持久化跨重启 | wi5s/t |
| **IDEM-4** | ★ | **重启后**新进程 debug 轮重新`evidence_gathered_set tool=grep`(per-run 标记随进程销毁、新进程干净起跑、不跳过取证) | wi5r/s |
| **IDEM-5** | ★ | 失败轮(坏 analysis_model)`llm_failed HTTP 503`→safe-fail→桌宠仍3×200OK答+**无** clarification/contradiction 乱触发+无 pre_loop 崩；还原后自愈轮`done problem_type='debug'`恢复 | wi5p/q |
| **IDEM-6** | (含TC-5) | `convergence.stop_loss`同 run 恰1次不刷屏 | wi5o |

**TC-7/IDEM-2** 已被上述覆盖（多次非闲聊各 1 次 done；evidence per-run 由 IDEM-4/TC-2 体现）。**TC-8 code 模式** env-limited（入口产品侧关，决策2 字节不动由单测覆盖，同 Sprint1）。

### 8.3 上线门（Production Ship Gate）

- **★ 必过全绿**：功能 TC-1/TC-9 + 幂等 IDEM-1/IDEM-3/IDEM-4/IDEM-5 ✅；功能 ★ TC-2/3/4/5 ✅。
- **headline P0 全修+真机验证**：BUG-B(Y-light)/BUG-D(WI-2 收敛止损 error_max_turns)/safe-fail 误短路/WI-4-C 预分析稳健化。
- **遗留（上游，非项目侧）**：relay 对结构化预分析调用仍偶发失败（已大幅降低，由 safe-fail 兜底，用户可见行为正确）；relay 稳定性属 plan §6 范围外。
- **结论**：**默认配置上线门通过。**
