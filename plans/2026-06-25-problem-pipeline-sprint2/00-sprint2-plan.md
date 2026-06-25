# 七步问题处理流水线 — Sprint 2 计划（修缺口 + 上线就绪 + UX）

> **状态**：📋 待 review（决策点见 §5）
> **前置**：Sprint 1 = 流水线落地（[plans/2026-06-24-problem-handling-pipeline-maoxuan/](../2026-06-24-problem-handling-pipeline-maoxuan/)）+ 本轮生产验收真测（[plans/manual-results-2026-06-25-problem-pipeline-prod/RESULTS.md](../manual-results-2026-06-25-problem-pipeline-prod/RESULTS.md)）
> **被测文档**：[testcase/2026-06-25-problem-pipeline-production/manual-test.md](../../testcase/2026-06-25-problem-pipeline-production/manual-test.md)
> **最后更新**：2026-06-25

---

## 0. 背景：Sprint 1 真测结论（为什么有 Sprint 2）

七步流水线代码已落地、单测全绿、机制本身真机验证正确（11 项 ★ PASS：闲聊幂等/合并预分析/取证门/抓主要矛盾/异体自检/kill-switch/澄清多轮/safe-fail/重启隔离…）。

**但真机 E2E 挖出 4 个真 bug，其中 2 个让"默认配置下用户真能用"不成立**：

| Bug | 一句话 | 严重度 | 现状 |
|---|---|---|---|
| **BUG-B** | classifier fail-open 成 `chat` → 真实非闲聊问题被误当闲聊短路、整条流水线跳过 | **P0 上线拦路** | followup task_742d3399 |
| **BUG-D** | Step7 收敛止损报告在 max_turns 触顶时**不触发**（路径不可达） | **P0/P1** | followup task_bc7826ba |
| **BUG-A** | relay 对 `gpt-5.5 + stream + json_schema` 间歇 502 → 预分析挂到超时 | P1 | 真测 workaround=换 analysis_model |
| **BUG-C** | 预分析被 classifier 的 `chat` hint 带偏继续误判 chitchat（BUG-B 下游放大） | P2 | 真测开关绕过 |

**关键结论（opus 4.8 核对）**：默认配置下因 BUG-B，流水线对"无关键词非闲聊问题"形同虚设。本轮 11 项 PASS 多在"关闭闲聊短路 + 关闭澄清门"非默认配置下取得，证的是**机制对**，不是**默认能用**。默认配置真实覆盖 ≈40%。

**Sprint 2 = 把"机制对"变成"默认能用 + 体验好 + 上线就绪"。**

---

## 1. Sprint 2 目标（验收口径）

1. **默认配置下，真实非闲聊问题不再被误短路**（修 BUG-B）→ 不开任何开关，发"光合作用…"类问题走完整流水线。
2. **触顶能诚实止损**（修 BUG-D）→ max_turns/死循环触顶时产出 Step7"卡在哪+建议"报告，而非截断收尾。
3. **预分析对 relay 不稳健壮**（修 BUG-A）→ relay 502 时快速 safe-fail，不挂满超时；延迟可控。
4. **闲聊短路逻辑重做**（用户拍板项）→ 正确性 + 体验（误判可控 + 非闲聊延迟可接受）。
5. **默认配置全量真测回归**（依赖 1~4）→ TC-1~10 + IDEM-1~6 不开关重跑取默认证据，补 TC-5/TC-8/IDEM-5。

---

## 2. 工作项（WI）

### WI-1 ★ 修 classifier fail-open（BUG-B）— P0 上线拦路
**问题**：组装期 TaskClassifier 三层级联 rule→embed→llm 全失效 → 默认 `chat`（classifier.py:267）→ 流水线误短路。
- rule 层只认 code/报错/python/搜索/计划/情绪 关键词（classifier.py:62-92）→ 无关键词问题 miss
- embed 层撞 embedder lock 竞争 1500ms 超时（真机 4× `status='timeout'`）→ 返回 None
- **llm 层 main.py:1977 `llm_registry=None` 彻底关掉**（且默认 model=claude-haiku-4-5 relay 没有）

**方案选项**（§5 决策点 D1）：
- **A（推荐）词法兜底**：rule/embed 无结论时，别默认 `chat`；加轻量词法启发式（疑问词「为什么/怎么/哪/吗」+ 技术名词 + 祈使动词 + 长度阈值）→ 兜底成非 chat（task/code），只有真没信号才 chat。**零额外 LLM 延迟、最小改动**。
- B 接 llm 层 + 换可用模型（gpt-5.4-mini）：准，但每条 rule/embed-miss 消息 +1 次 LLM 往返延迟（当初设 None 多半就为省这个）。
- C 短路前二次确认：intent_triage.py:188 chitchat 短路前，对"看着不像闲聊"的文本再走词法校验，别只信 prior_task_type。
- **组合 A+C** 最稳：分类兜底 + 短路前再保一道。

**验收**：默认配置（无开关）发"光合作用的暗反应在叶绿体哪个部位"等无关键词非闲聊问题 → `intent_triage.done`（走预分析），**不再** `intent_triage.shortcircuit reason=chitchat_rule`。补 classifier fail-open 回归单测。
**相关**：classifier.py / assembler/__init__.py:79-130 / main.py:1975-1977 / intent_triage.py:185-201。

---

### WI-2 ★ 修 Step7 收敛止损不触发（BUG-D）— P0/P1
**问题**：max_turns 触顶时 loop `range(max_iterations)`（agent_loop.py:823）先于 gate `allows_call`（:873，turns_used>=max_turns）耗尽（off-by-one，max_turns==max_iterations + record_turn 底部自增 termination.py:174）→ 走 loop-exhaustion warning（:2546）**绕过 ConvergenceController**（:878-906 的 stop_loss 到不了）；hallucination 又走 ErrorEvent（:2367）。**companion 默认下 Step7 止损路径实际不可达**。

**方案选项**（§5 决策点 D2）：
- **A（推荐）loop-exhaustion 也走 ConvergenceController**：循环正常耗尽时若 pipeline 开，调 `evaluate()` 出止损报告再收尾，而非只 warning。
- B 修 off-by-one：让 gate 在最后一轮能触发（allows_call 用 `>` 或 record_turn 移顶部）——小心别破坏迭代语义。
- C 把 hallucination/permanent_tool_error 等 allows_tool 触顶也接进 ConvergenceController（所有"非正常收尾"都出诚实止损）。
- **组合 A+C**：覆盖最常见的两类触顶。

**验收**：收紧 max_turns 真机触顶 → `convergence.stop_loss reason=error_max_turns` + 桌宠出"已做什么/卡在哪/建议"诚实报告；制造死循环 → hallucination 也出止损报告。补集成测试断言"loop 耗尽时也出 stop_loss"。
**相关**：agent_loop.py:647/823/873-906/2166/2546 / termination.py:138-174 / convergence_controller.py。

---

### WI-3 闲聊短路逻辑重做 + 延迟体验（用户拍板项）— P1
**背景**：当前闲聊短路 0 LLM 保闲聊快回，但两个体验问题：
- (a) **误判**：依赖 classifier（BUG-B），把真实问题误短路（WI-1 治根因，本 WI 治短路侧）。
- (b) **非闲聊延迟**：每条非闲聊问题先 +1 次预分析（gpt-5.5 thinking ~10-15s，relay 慢时更久）才进主 loop。

**方案选项**（§5 决策点 D3，可与 WI-1 合并）：
- **正确性**：短路判定别只信 classifier 一票——结合词法（WI-1 的 A/C）+ 短问题/明确情绪词才短路，拿不准默认走流水线（宁可慢一点别误短路）。
- **延迟（择一/组合）**：
  - ① 预分析换快模型/关思考（`analysis_model` 配快模型，~10-15s→1-2s；BUG-A workaround 也吃这个红利）
  - ② 非阻塞：桌宠先给轻反应（表情/"让我看看"气泡/先流式开口），预分析后台并行，结果出来再校正
  - ③ 分级：简单事实问答也走轻路径（不做完整七步），只复杂问题上完整流水线
**验收**：闲聊仍快（短路或轻路径）；非闲聊误短路率↓；非闲聊首响应延迟↓（②/③ 后桌宠"秒有反应"）。
**相关**：intent_triage.py:185-198 / main.py PRE-LOOP 6826-6890 / RESULTS 已知 UX 成本段。

---

### WI-4 预分析对 relay 不稳健壮化（BUG-A）— P1
**问题**：relay 对 `gpt-5.5 + stream:True + json_schema strict` 间歇 502/503（chat_with_tools 强制 stream openai_compatible.py:262）→ 预分析挂到 `analysis_timeout_s`(默认30s)满才 safe-fail。

**方案选项**（§5 决策点 D4）：
- A 预分析失败更快降级：检测到 502/503/连续无字节，别等满 timeout，立即 safe-fail（降感知延迟）。
- B `analysis_model` 默认换更稳模型（deepseek-v4-pro/gpt-5.4-mini 实测 stream+json_schema 稳）——但改了默认模型。
- C stream+json_schema 失败时 fallback 到 json_object 或 non-stream 重试一次（probe 实测两者都稳）。
**验收**：relay 502 时预分析在 ≤5s 内 safe-fail（非挂满 30/90s）；或 fallback 后成功。
**相关**：openai_compatible.py:252-275 / intent_triage.py:208-217 / main.py:1714（max_tokens/schema 绑定）。

---

### WI-5 默认配置全量真测回归（依赖 WI-1/2/4）— P1
不开任何 env 开关、analysis_model 回默认（或 WI-4 定的新默认），重跑：
- TC-1~10 + IDEM-1~6 全套，取**默认配置真证据**（当前多在非默认配置取得）
- 补本轮未做：**TC-5/IDEM-6**（WI-2 后真出 stop_loss）、**TC-8 code 模式**（真测或显式 env-limited）、**IDEM-5 自愈轮**（主动构造坏 analysis_model + 还原对照）
**验收**：testcase 100% 覆盖（PASS / best-effort / env-limited 标注清楚），默认配置可上线门全过。

---

### WI-6（可选）测试基建 + 清理 — P2
- 补集成测试：classifier fail-open 回归、loop-vs-gate 触顶谁先、短路正确性。
- 清理本轮临时物：`DESKPET_DISABLE_CHITCHAT_SHORTCIRCUIT` / `DESKPET_DISABLE_CLARIFICATION` 两个测试开关（WI-1/2/3 落地后删或转正式 config）；probe 脚本保留为 relay 诊断工具。

---

## 3. 排期 / 依赖

```
WI-1 (classifier fail-open) ──┐
                              ├──► WI-5 (默认配置全量回归) ──► 上线门
WI-2 (Step7 止损) ────────────┤
WI-4 (relay 硬化) ────────────┘
WI-3 (短路重做+UX) ── 与 WI-1 部分重叠，可合并 ──┘
WI-6 (测试基建) ── 贯穿
```
- **第一批（P0，并行）**：WI-1 + WI-2（两个上线拦路；可两个子代理并行）
- **第二批**：WI-4（relay 硬化）+ WI-3（短路重做，吃 WI-1 成果）
- **第三批**：WI-5 默认配置全量回归 → 上线门
- WI-6 贯穿

---

## 4. 风险

| 风险 | 缓解 |
|---|---|
| WI-1 词法兜底过度 → 把真闲聊也判成 task 拖慢 | 词法只在 rule+embed 都 miss 时兜底；保留短问题/明确情绪词短路；回归单测两侧都测 |
| WI-2 改 off-by-one 破坏现有迭代语义 | 优先选 A（加 ConvergenceController 调用，不动循环计数）；集成测试守 |
| WI-3 非阻塞预分析引入竞态（先回复后校正打架）| 先做①快模型（低风险），②非阻塞作为独立后续评估 |
| WI-4 改默认 analysis_model 影响其它读该模型的路径 | analysis_model 仅作用预分析；core 主对话不受影响 |
| relay 持续不稳 | WI-4 的 fallback + safe-fail 兜底；真测前先 probe 确认 relay 健康 |

---

## 5. ⭐ 待 Review 决策点（请拍板）

| # | 决策 | 选项 | 倾向 |
|---|---|---|---|
| **D1** | WI-1 classifier 怎么修 | A 词法兜底 / B 接LLM层 / C 短路前二次确认 / A+C | **A+C**（零延迟+双保险）|
| **D2** | WI-2 止损怎么接 | A loop耗尽走Controller / B 修off-by-one / C 全触顶接Controller / A+C | **A+C** |
| **D3** | WI-3 短路重做范围 | 只修正确性 / 正确性+延迟①快模型 / 全做(①+②非阻塞+③分级) | **正确性 + ①快模型**（②③作后续）|
| **D4** | WI-4 relay 硬化 + 默认模型 | A 快降级 / B 换默认模型 / C fallback重试 / 组合；analysis_model 默认是否换 deepseek | **A+C**；默认暂不换（留"") |
| **D5** | Sprint 范围 | 只 P0(WI-1,2)先上线 / P0+P1全做 / 全做含UX | 待定（看你节奏）|
| **D6** | 执行方式 | codex 子代理并行 / Claude 直接做 / 混合 | 待定 |

---

## 6. 范围外（不在本 Sprint）
- 七步流水线新增能力（只修缺口 + 上线就绪，不加新 step）
- code 模式分支改造（决策2 字节不动）
- relay 中转站本身的稳定性（上游，非项目侧）
</content>
