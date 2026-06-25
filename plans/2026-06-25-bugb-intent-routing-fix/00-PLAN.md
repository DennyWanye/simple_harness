# BUG-B 修复 plan v2 — 意图路由 followup（组装质量 + 闲聊快路径 + 单一来源）

> **状态**：📋 执行中（v2 = 经 R1+R2 两轮对抗挑战硬化 + 对齐 master 最新代码）。
> **创建**：2026-06-25　**v2 硬化/对齐**：2026-06-26
> **重要前提（已坐实）**：BUG-B 的 **headline P0「真问题被误判闲聊短路」已由 commit `16758f8b`(safe-fail 不短路) + `717b0424`/`67f78e15`(WI-4-C 非流式预分析) 修复**，默认配置全量真机 **10/10 ★ PASS、上线门通过**（`RESULTS.md §8`）。本 plan **不再解 P0**，只做 RESULTS §8.2 列出的非阻断残留：
>   - **P2 组装质量**：组装期 classifier 仍 fail-open 到 `chat` → 真 code/debug 问题拿到错误 persona/工具/skill bundle（**Phase 2**）。
>   - **P3 闲聊快路径**：闲聊也走 1 次预分析 LLM（成本/UX）（**Phase 1**）。
>   - 架构收口：两个意图判定器 + lossy 桥（**Phase 3**）。
> **关联**：commit `16758f8b`/`717b0424`/`67f78e15`/`c004c630`；`RESULTS.md §8`；记忆 `project_assembler_embedder_unreliable`。
> **R1/R2 存档**：`exec/challenge-r1.txt`、`exec/allowlist_probe.py`（已验证规格，19PASS/4安全假阴性）。

---

## 0. 当前真实代码状态（master，2026-06-26 已对齐）

| 事实 | 证据 |
|---|---|
| short_circuit 已 fail-closed | `intent_triage.py:212-215` 仅 `problem_type=='chitchat' and ambiguity<threshold` 才短路；LLM 来自真实返回(无 hint) |
| safe-fail 不短路 | `intent_triage.py:181-203` 三出口 `return _safe_card`，`short_circuit` dataclass 默认 False |
| WI-4-C 非流式已落地 | `intent_triage.py:166 non_stream=True` + `:224 _supports_non_stream()` 探测 llm_call kwarg |
| 预分析默认模型 | `config.py:436 analysis_model="deepseek-v4-pro"`；`analysis_timeout_s=45.0`(config.py:431) |
| bypass flag 已退役 | `DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT`/`_CLARIFICATION` 在 backend 全仓 **0 读取点**(git -S 证 `7cc79b39` 删除) |
| 组装期 classifier llm 层关闭 | `main.py:1998 build_default_assembler(llm_registry=None)` → `classifier.py:262 self._llm is not None` 为假 → 跳过 llm 层 → `default='chat'`(classifier.py:267-273) |
| classifier llm shim 已存在 | `OpenAICompatibleAgentLLM(provider=local_llm)`(`backend/agent/tool_use_shim.py:38` 有 `chat_with_fallback`)；范式见 `main.py:1868` codify。**注意 shim 忽略 model 参数(tool_use_shim.py:45)，实际跑 `local_llm.model`** |
| `_safe_card` 取证门漏洞仍在 | `intent_triage.py:243` chitchat derived_pt → `needs_investigation=False` → evidence_gate 跳过取证 |
| 测试套件 | `backend/tests/test_intent_triage.py` 15 函数；mock 方式 = `AsyncMock(return_value=<json_str>)` 直 mock llm_call callable；helper `_complex_payload()`/`_chitchat_payload()` |

---

## 1. 设计原则（横向调研 Claude Code/Codex/Hermes/OpenClaw → 5 原则）

1. **In-band，让在环能力模型决定**（IntentTriage 是唯一意图来源）。
2. **Fail-CLOSED 倒向能力侧**（短路侧已对；**组装侧 Phase 2 修**）。
3. **Single source of truth**（Phase 3 合并，删 lossy 桥）。
4. **便宜确定性层只做地板不做天花板**（记忆 `project_assembler_embedder_unreliable`）。
5. **快路径用高精度 allowlist（整句锚定），不是 block-list**。

> 来源：[OpenClaw](https://github.com/openclaw/openclaw)、[Hermes-Function-Calling](https://github.com/NousResearch/Hermes-Function-Calling)、[Claude Code vs OpenClaw](https://www.eigent.ai/blog/openclaw-vs-claude-code)

---

## 2. Phase 1 — 闲聊快路径 allowlist + safe-fail 兜底（P3，主改 intent_triage.py）

### 2.1 WI 列表
- **WI-1**：新增共享词法模块 `backend/deskpet/agent/lexicon.py`，实现 `is_obvious_chitchat(msg) -> bool`（整句锚定，规格 §2.2，基于 `exec/allowlist_probe.py` 清理版）。**Phase 2 的 WI-6 词法地板复用同一模块**（满足原则 3，避免双词表，R2-B4）。
- **WI-2**：`intent_triage.analyze()` 在 LLM 调用**之前**插入 allowlist 分支：命中 → 直接产出短路 IntentCard（`problem_type='chitchat'`, `short_circuit=True`, `needs_investigation=False`）+ 打 **`intent_triage.allowlist_hit`** 结构化日志（BUGB-3 唯一硬证据），**不调 LLM**。不命中 → 走现有 LLM 路径（不变）。
- **WI-2b**：`_safe_card` 对 `chitchat` derived_pt 兜底改 `factual_qa`（修 §0 取证门漏洞：真问题 LLM 挂 + prior='chat' 时不该跳过取证）。
- **WI-4**：回归测试 `backend/tests/test_intent_triage.py` 扩充：
  - 钉死已修行为（safe-fail 不短路 / 无 hint / 短路 fail-closed）—— 已有测试不动，确认仍绿。
  - allowlist 边界表（§2.3）全部新用例。
  - `allowlist_hit` 日志断言（可用 caplog/structlog capture）。
  - WI-2b：prior_task_type='chat' + llm 异常 → safe-fail card `problem_type` 不为 chitchat（兜底 factual_qa）且 `needs_investigation` 合理。

> WI-3（原"退役 bypass flag"）**删除**：flag 已不存在（R1/R2 证实），改为 WI-4 里加一条 `grep` 断言"仓内无 bypass 读取点"。

### 2.2 allowlist 规格（整句锚定，禁止子串；R2 已实跑验证）
```
is_obvious_chitchat(msg):
  s = msg.strip(); 空 → False
  纯 emoji/纯标点（无字母数字汉字）→ True
  否决项任一命中 → False（否决优先）：
    问号 [?？] | 疑问助词 [吗么] | 祈使/求助 (帮|请|给我|看下|看看|查|搜|写|改|修|生成|做|算|解释|分析|为什么|怎么|如何|啥|什么)
    | 故障/code (报错|debug|python|java|代码|崩|卡死|异常|error|bug|失败|不行)
  整句锚定 re.fullmatch( (招呼词) + 容许尾缀 ) → True；否则 False
  招呼词根: 你好|您好|嗨|hi|hello|早|早安|午安|晚安|晚上好|在吗|在不|谢谢|多谢|感谢|thx|thanks|拜拜|再见|哈喽|你好呀
  尾缀: [呀啊哟哦呢嘛吧~！!。.，,\s]*
```
> **R2 决策点（本 plan 拍板）**：`在吗`/`在不` 含疑问助词 `吗`，与"否决疑问助词"冲突 → **从招呼词根里把 `在吗`/`在不` 保留，但疑问助词否决只在"招呼锚定未命中时"才生效**（实现：先查否决项里**去掉** `吗么`，改为"问号 `[?？]`"否决兜底；`在吗在吗` 这类纯催促寒暄靠整句锚定放行）。即否决用 `[?？]`（真问句），不再用裸 `吗么`（否则误杀"在吗"）。验收用例钉死。

### 2.3 allowlist 边界用例表（WI-4 必覆盖）
| 输入 | 期望 | 理由 |
|---|---|---|
| `你好`/`谢谢`/`晚安`/`😄`/`。。。` | 短路 | 纯寒暄 |
| `你好呀~`/`晚安啊`/`在吗在吗` | 短路 | 寒暄+尾缀/催促 |
| `你好，帮我看下这段为什么报错` | 不短路 | 含祈使+故障词 |
| `崩了`/`报错`/`卡死` | 不短路 | 故障词否决 |
| `光合作用为什么需要光` | 不短路 | 含"为什么" |
| `在吗？我代码崩了` | 不短路 | 问号+故障词 |
| `谢谢，那这个报错怎么办` | 不短路 | 故障词+疑问 |
| `hi 帮我 debug` | 不短路 | 祈使+code |

### 2.4 Phase 1 自测
`cd backend && .venv/Scripts/python.exe -m pytest tests/test_intent_triage.py -q`（全绿，含新边界用例）。

---

## 3. Phase 2 — 复活组装期 classifier（P2 组装质量，主改 classifier.py + main.py）

### 3.1 WI 列表
- **WI-5**：`main.py:1998` 把 `llm_registry=None` 改为注入 `OpenAICompatibleAgentLLM(provider=local_llm)`（复用现成 shim，范式同 codify `main.py:1868`）。
  - **R2 命门**：shim **忽略** classifier 传入的 `model` 参数（tool_use_shim.py:45），实际跑 `local_llm.model`（即 relay 主模型）。故 classifier `llm_model` 默认值（`claude-haiku-4-5`）改不改都无效——保留或改成注释说明"shim 锁定 local_llm.model"。
  - **R2 命门**：classifier `_llm_tier` timeout 默认 **2.0s**（`classifier.py:_llm_timeout_s`），主模型 thinking 4-6s → 2s 必超时 → 接了等于没接。**必须把 build_default_assembler 传入的 classifier llm timeout 调到 ≥6s**（加构造参数或在 build_default_assembler 设）。
  - **R2 命门**：classifier llm 层走 `chat_with_tools`/stream？确认其调用对 relay 是否踩 BUG-A（stream+json_schema）。classifier `_llm_tier` 用的是 `chat_with_fallback(max_tokens=32)` 非 json_schema，风险低；若仍不稳，限定非流式。
- **WI-6**：classifier `default='chat'`（`classifier.py:267-273`）→ **词法地板**：调 `lexicon.is_obvious_chitchat()`（WI-1 同模块）；命中 → `chat`；否则 → 倒向能力侧（有 code 信号→`code`，否则→`task`），**不再无脑 chat**（fail-closed）。
- **WI-7**：embed 层超时（`classifier.py` 已 warning）→ 确认 fallback 落 WI-6 词法地板，非 chat。

### 3.2 Phase 2 自测
- `pytest backend/tests/test_*classifier* tests/test_deskpet_context_assembler.py -q`（全绿）。
- 启动 smoke：backend 起得来，log 出现 classifier llm 层真跑（注入后）。

---

## 4. Phase 3 — 单一意图来源收口（架构，可选但"不少做"→做）

- **WI-8**：调换 assemble() 与 triage 次序 / 让 IntentTriage 的 `problem_type` 成为组装 task_type 来源；评估能否删冗余 classifier llm 层 + `_TASKTYPE_TO_PROBLEM`（`intent_triage.py:43`）。**删桥须同步**把 `_safe_card`/`_parse` 兜底默认硬编码 `factual_qa`（否则失依据）。
  - **风险高**：若改动面过大/牵动 assemble 调用链，Phase 3 降级为"文档化单一来源方案 + 标注 deferred"，不强行重构（避免破坏已 10/10 PASS 的链路）。决策点：实现时若影响 >3 文件或动 assemble 主流程 → 拆出独立 plan，本轮只做 WI-8a（去重表/注释护栏）。
  - **✅ 决策（2026-06-26 执行）：触发降级 → 只做 WI-8a。** 理由：(1) 全量收口要动 `assemble()` 主流程 + main.py wiring + intent_triage + classifier ≥4 文件，超 3 文件门槛；(2) 与 **Phase 2 刚复活的 classifier llm 层（组装 bundle 选择，与意图路由是两个不同用途）直接冲突** —— 删它会回退 Phase 2 的 BUGB-6 修复。**WI-8a 已落地**：在 `_TASKTYPE_TO_PROBLEM`（`intent_triage.py`）加单一来源护栏注释，钉死"本桥仅 safe-fail fallback、IntentTriage LLM 裸判才是唯一权威意图来源、禁止重新接回正常路径当 hint（防 BUG-C 复活）"，全量合并标 deferred 独立 plan。
- **WI-9b**：修 `_PRE_ANALYSIS_SYSTEM`（`intent_triage.py` prompt 文本仍写"系统已判定的初步任务类型"，与去 hint 行为漂移）。**✅ 已修**：prompt 去掉"+ 系统已判定的初步任务类型"，与 Y-light 去 hint 行为对齐。

### 4.1 Phase 3 自测
全量 `pytest backend/tests/test_intent_triage.py backend/tests/test_*classifier* -q` + 启动 smoke 不回归。

---

## 5. 每阶段执行循环（用户硬要求）
每个 Phase 完成后**必须**依次：
1. **自测**（pytest 对应套件全绿）。
2. **子代理评估 100%**：派只读子代理对照本 plan 该 Phase 的 WI 逐条核实完成度；<100% → 补完 → 再评估，直到 100%。
3. **子代理生成手测文档** → 我评估迭代到"能测出该 Phase 各种 bug/边界" → 存 `testcase/2026-06-26-bugb-<phase>/manual-test.md` + 更新 `testcase/index.md`。
4. **windows-mcp 真机人工测试**（HARD：真坐标点击 + 真中文输入 + 截图 + tauri-dev.log 判定；动作前 declare `坐标|动作|期望`；失败 retry≥3 不同 workaround 才标 env-limited）。全部 PASS；有问题→修复→复测。

---

## 6. 最终验收（默认配置，全功能真机人工测试，不可省略/不可降级）
> 默认配置 = §0 表（无任何 bypass env、analysis_model=deepseek-v4-pro、非流式预分析）。真测 run 头部记录该清单。

| TC | 用例 | ★ | 硬证据（防假绿）|
|---|---|---|---|
| BUGB-1 | "光合作用为什么需要光" | ★ | **必须见 `intent_triage.done problem_type=factual_qa short_circuit=False`（非 safe-fail）**；无 short_circuit；流水线真跑 |
| BUGB-2 | 纯中文 debug"刚那段为什么越界" | ★ | `intent_triage.done problem_type∈{debug,factual_qa} short_circuit=False` |
| BUGB-3 | 闲聊"你好呀" | ★ | **`intent_triage.allowlist_hit`（WI-2 新事件）+ 短路 + 无 `intent_triage.done`/`llm_failed`（证 0 次 LLM）** |
| BUGB-4 | 打挂预分析(analysis_model 设 relay 不存在串) | ★ | `intent_triage.llm_failed` + 无短路 → 裸 ReAct 答对 |
| BUGB-6 | 真 code"帮我看这段 python 为何 IndexError" | ★ | **Phase 2 后**：组装 bundle `task_type` 非 chat（classifier 复活）+ 流水线真跑 |

**收敛标准**：上述 ★ 全 PASS；组装期 `task_type` 对真 code/debug 不再恒 `chat`；闲聊 0 次 LLM。

---

## 7. 风险 / 回退
- **R-踩踏**：另一进程曾同时写 master（已停、已合并）。每阶段前 `git log` 确认 HEAD 稳定；真机测试独占端口。
- **R-allowlist 误放行**：§2.2 整句锚定 + 故障词否决；假阴性安全。回退收窄词根。
- **R-Phase2 timeout/BUG-A**：timeout≥6s + 必要时非流式；shim 忽略 model 已知。
- **R-Phase3 重构过大**：超 3 文件/动 assemble 主流程 → 降级 deferred，不破坏已过门链路。
- **R-真机 relay 不稳**：用稳定窗口；safe-fail 保证不卡死。

## 8. 执行顺序
Phase 1 → 循环(§5) → Phase 2 → 循环(§5) → Phase 3 → 循环(§5) → 最终验收(§6) → 更新 `STATUS/status.md`(HARD)。
