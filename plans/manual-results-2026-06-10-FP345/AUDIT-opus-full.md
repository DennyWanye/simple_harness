# 独立审计报告（第二轮·全范围）— goal-completion 全部 40 TC 执行核对

> 审计员: opus 4.8（独立测试审计员，只读不改）
> 审计日期: 2026-06-11
> 审计范围: **全部三份 testcase 共 40 TC**（FP-1 8 + FP-2 10 + FP-3/4/5 22）
> 审计手段: 读 3 份 testcase + 3 份 RESULTS + 一轮 AUDIT-opus.md；ls 截图；git log/show；复跑 pytest；grep 修复源码逐行核对。**未重跑真机 GUI（符合任务约束）**。
> 一轮审计（前任）: `plans/manual-results-2026-06-09-FP-1/AUDIT-opus.md` 仅覆盖 FP-1/FP-2，本轮补齐 FP-3/4/5 并复核一轮结论 + 续测增量。

---

## ① 总判

| 指标 | 结论 |
|---|---|
| **40 TC 执行完整率** | **40/40 全部有交付判定**。分布：**真机 PASS 19**（含 2 变体）+ **后端/单测/历史引用 PASS 16**（均诚实标级）+ **BLOCKED 2**（与 testcase 预声明一致）+ **FAIL 2**（TC-2.1 已修→PASS / TC-5.1 待修，如实未掩饰）+ **被阻塞未触发 1**（TC-5.7，随 TC-5.1 连带）。 |
| **硬证据可复现性** | ✅ 高。复跑 pytest 全绿（49 + 64 + 1 + remount/upsert 套件）；4 刀修复 commit 全部在 git log 且 commit message 含 TDD 红→绿证据；3 处修复源码（`agent_loop._last_real_prompt_tokens` / `main._CmpShim` + `upsert_replacing` 调用 / `facts.upsert_replacing`）逐行核实存在；TC-2.1 截图（一轮 L2 漏项）已补真 PNG；自创 SKILL.md 真实落盘。 |
| **诚实性** | ✅ 优秀。两个 FAIL 全部敢报；变体/后端/历史引用/未触发一律带限定词与理由；TC-5.1 的根因甚至在生产源码注释里被自己写明（非事后辩解）。 |
| **可接受性** | ✅ **建议接受**。唯一系统性瑕疵：FP-3/4/5 06-10 轮多个 testcase 指定截图未留（详见 ③）。但这些 TC 的判定级别（后端/历史/未触发/FAIL）本就不依赖该截图，逻辑链由 pytest/log/DB/历史报告兜底，属「证据留痕完整度」而非「功能未验」。 |

**一句话**：第二轮全范围核对未发现任何「走捷径刷 PASS」或「修复声明造假」。FAIL 敢报（2 个）、修复可复验（4 刀 commit + 复跑全绿）、历史引用真实（SKILL.md 至今在盘）、变体判定有诚实限定。截图漏项是唯一需补的留痕瑕疵。

---

## ② 40 TC 总矩阵（testcase 要求 vs RESULTS 判定 vs 独立验证）

### FP-1（目标持久化地基，8 TC）— 复核一轮结论

| TC | RESULTS 判定 | 独立验证 | 本轮裁定 |
|---|---|---|---|
| 1.1 重启仍在 pass^k=3 | PASS 3/3 | 截图 tc-1.1-2/k2/k3-after-restart-PASS 全在盘 | **完整** |
| 1.2 iterations 恢复 | PASS | DB+log 链合理（一轮已核） | **完整** |
| 1.3 clear→abandoned | PASS | 逻辑链完整 | **完整** |
| 1.4 flag-OFF 不建表 | PASS(步骤1)，步骤2/3 诚实标脚本+单测覆盖 | 脚本钦定 🔵 手段；sha 差异属正常（live DDL） | **诚实偏差·可接受** |
| 1.5 多目标最新 | PASS | 截图 tc-1.5 在盘 | **完整** |
| 1.6 ToolPath 录制 | PASS(后端核对级) | test_tool_path_recording 套件绿；testcase 本就声明无生产成功日志 | **完整（符合分级）** |
| 1.7 goal_checker 接电 | PASS（截图缺失） | log+DB 链合理；**tc-1.7-goalcheck.png 仍不在盘**（一轮 L1 漏项未补） | **部分（缺截图）** |
| 1.8 USER_DATA_DIR 隔离 | PASS | 截图 tc-1.8-userdata-b 在盘 | **完整** |

### FP-2（抗漂移闭环，10 TC）— 含 TC-2.1 FAIL→PASS 续测增量

| TC | RESULTS 判定 | 独立验证 | 本轮裁定 |
|---|---|---|---|
| 2.1 压缩后不漂移（招牌 pass^k） | **FAIL→PASS**（4 刀修复后续测闭环） | 见 ③重点核验 a：4 commit 全在、源码核实、截图已补真 PNG | **诚实偏差→已闭环·可接受** |
| 2.2 任务图+TodoPanel | BLOCKED | 与 testcase 预声明一致（一轮 grep main TaskGraphStore=0 已证） | **完整（BLOCKED 符合预期）** |
| 2.3 DAG ready 调度 | PASS 11 | 一轮复跑通过 | **完整** |
| 2.4 跨 agent 共享任务图 | PASS(后端) | 一轮复跑通过；产品链路 BLOCKED 符合 testcase | **完整** |
| 2.5 handoff 带父 goal | PASS(后端) | 一轮复跑通过 | **完整** |
| 2.6 off-goal 分流 | PASS(marker) | 一轮复跑通过；LLM judge BLOCKED 符合窄实现 | **完整** |
| 2.7 resume 续目标 | PASS(单测+接线) | 一轮独立 grep main.py:2522/2537 行号精确命中 | **诚实偏差·可接受** |
| 2.8 并发不双占 pass^k | PASS 5/5 | 一轮单点复跑通过 | **完整** |
| 2.9 GD 漂移信号 | BLOCKED | grep GD_actions/GD_inaction=0 符合 testcase 预声明 | **完整（BLOCKED 符合预期）** |
| 2.10 compaction 前置 | PASS(双分支) | 与 2.1 互证 | **完整** |

### FP-3（自我纠错闭环，7 TC）

| TC | RESULTS 判定 | 独立验证 | 本轮裁定 |
|---|---|---|---|
| 3.1 伪完成→拦→真产物（招牌） | **PASS(变体)** | 截图 tc-3.1-goal-set/-artifactcard 在盘；变体理由（gpt-5.5 第一轮即被 nudge 推回，未自然产生伪完成）成立；诚实附「ArtifactCard 未渲染待查」 | **完整（变体合理）** |
| 3.2 真完成放行不误杀 | PASS | 截图 tc-3.2-passed 在盘 | **完整** |
| 3.3 goal 对照拦偏离 | PASS(行为级) | **无 tc-3.3-misalign.png**；`aligned=False` 字面 log 自承"未单独打（goal_checker 内嵌对照）"——行为级判定成立但留痕弱 | **部分（缺截图+log 锚点弱）** |
| 3.4 未来时不误判 | PASS(变体) | **06-10 无截图**（06-06 有 tc-3.4-future-tense-no-false-flag.png）；变体（推到真做）逻辑成立 | **完整（变体合理，截图在 06-06）** |
| 3.5 relay 故障降级 | PASS(自然故障) | test 兜底；自然采集 3 枚降级符合断言；人工注入未做诚实标 | **诚实偏差·可接受** |
| 3.6 重试上限不死循环 | PASS(后端) | test_verify_replan_retry 套件绿（复跑确认）；真机未自然触发诚实标 | **完整（符合 🟡 分级）** |
| 3.7 结构化反思注入 | PASS(后端) | test_structured_reflection 复跑绿；真机无伪完成场景诚实标 | **完整（符合 🟡 分级）** |

### FP-4（记忆+人格，7 TC）

| TC | RESULTS 判定 | 独立验证 | 本轮裁定 |
|---|---|---|---|
| 4.1 跨重启召回（招牌） | PASS | 截图 tc-4.1-sessionA/B 在盘；facts decision/constraint 精确召回 | **完整** |
| 4.2 改偏好下轮反映 | PASS | 截图 tc-4.2 在盘 | **完整** |
| 4.3 偏好冲突替换 | PASS | 截图 tc-4.3 在盘 | **完整** |
| 4.4 Pin 不衰减+调度接通 | PASS(🟡) | `p4_facts_daily_decay_startup mutated=60` 生产实证（原 bug 已修）；test_pin_and_pref_decay 复跑绿；UI pin 真点击未做诚实标 | **完整（符合 🟡 分级）** |
| 4.5 B-10 双写钩 | **PASS+FAIL→修复** | 见 ③重点核验 d：去重 FAIL 如实暴露 15 行堆积→commit 75af4bd（upsert_replacing）已修；TDD 测试在 test_goal_decision_facts.py（复跑 49 passed 含此）；钩本体历史 06-06 真机 PASS（截图 tc-4.5-b10-goal-set 在盘） | **诚实偏差→已修·可接受** |
| 4.6 flag-OFF 不写 goal facts | PASS(后端) | 钩条件含 flag(main.py:1411)+字节脚本；OFF 真机分支未单独跑诚实标 | **诚实偏差·可接受** |
| 4.7 人格红线 | PASS(后端) | no_persona_leak 类断言在 test_verify_goal_alignment/test_external_evaluator（复跑 64 passed）；真机诱导无法点击复现 testcase 🟡 预期 | **完整（符合 🟡 分级）** |

### FP-5（Skills 分级+自创，8 TC）

| TC | RESULTS 判定 | 独立验证 | 本轮裁定 |
|---|---|---|---|
| 5.1 强匹配自动载 | **FAIL(真缺陷)** | 见 ③重点核验 d：top_sim=0.000 缺陷**真实存在**——`backend/deskpet/skills/skill_matcher.py:118-128` 源码注释自承「生产 BGE-M3 encode 是 async+收 list，sync match() 调 encode(query) 期望 sync → except 吞 → 缓存恒空 → 零匹配」。如实未掩饰，标待修 | **完整（FAIL 如实）** |
| 5.2 压缩后追问原目标 | PASS | = FP-2 TC-2.1 闭环；截图引 FP-2 的 tc-2.1（真 PNG 在盘） | **完整（等价引用合理）** |
| 5.3 自创→保存→落盘→复用（招牌） | PASS(历史+本轮复证) | 见 ③重点核验 c：SKILL.md `meeting-minutes-to-ppt/SKILL.md`（712B，author=self-codified，requires_script:false）**至今在 fp345-userdata 在盘**；06-06 报告记「真坐标点保存(物理 1221,1053)→落盘」FULL PASS；本轮复证触发器+卡渲染≥5 次 | **完整（历史引用真实）** |
| 5.4 拒绝→不落盘 | PASS | 真点忽略×3 + reject log×3 + skills/user 空（与 5.3 历史落盘不矛盾：本轮独立 userdata-a） | **完整** |
| 5.5 候选 5min 超时 | PASS(后端) | 超时单测绿；真机 5min 等待未做 testcase 🟡 预期 | **完整（符合 🟡 分级）** |
| 5.6 trivial 不弹卡 | PASS | 本轮 trivial 全程零候选卡 | **完整** |
| 5.7 压缩后 skill 重挂 | **未触发(诚实标)** | 被 TC-5.1 缺陷阻塞（auto_loaded=0 无 skill 可重挂）逻辑成立；test_deskpet_skill_remount 15 passed 兜底（复跑确认） | **诚实偏差（阻塞）·可接受** |
| 5.8 保存后复用 | PASS(历史) | 06-06 已验复用；本轮未重复保存分支引用历史 | **完整（历史引用真实）** |

---

## ③ 四类重点声明核验

### a) TC-2.1 FAIL→PASS：4 刀修复真实性 + 证据链

✅ **真实、完整、可复验**。

- **4 刀 commit 全部在 git log**：刀1+2=`e6ee37f`（CJK 加权 + ASCII 3.5）、刀3=`4f7141d`（relay 真实 prompt_tokens 反馈回路）、刀4=`0800299`（compressor shim 修 AttributeError）；结果落盘 `dd5242d`。
- **源码逐行核实**：
  - 刀3：`agent/agent_loop.py:618` 定义 `_last_real_prompt_tokens`；`:1038-1039` 记录 `response.usage.input_tokens`；`:723-725` compaction 判定取 `max(estimate, last_real)`。**与声明分毫不差**。
  - 刀4：`main.py:1302/1307` `OpenAICompatibleAgentLLM as _CmpShim` 包装 `_compactor_llm`，且注释明示复用 codify（:1348）同款 shim。
- **commit message 含 TDD 红→绿**：刀3「test_real_usage_feedback_overrides_low_estimate 红→绿(15 passed)；回归 124 passed」——我**单点复跑该测试通过（1 passed）**，全套件 49 passed。
- **fired log + 压缩后追问证据链成立**：RESULTS 记 `p1_4_compaction_fired ... iter=8 reduction=0.9762` + 压缩后追问「我最初让你做什么」→ AI 精确指向第一条消息字面路径（零漂移）。
- **截图（一轮 L2 漏项）已补**：`tc-2.1-after-compact-still-on-goal.png` 现为真 PNG（1538×1083 RGB，155KB，时间戳 06-10 23:49 与续测吻合）。**逐层剥洋葱证据链（blade-3 让 should_compress 过→blade-4 卡 AttributeError→修通）成立**。

### b)「PASS(变体)」类（3.1 / 3.4）：变体理由是否成立

✅ **成立，非借口**。

- 共同根因（RESULTS 自己点明，且与 FP-3/4/5 §观察#4 一致）：**completion_nudge 强度高**，gpt-5.5 被「只口头不真做」诱导时第一轮即被 `goal_checker_nudge_injected` 推回去真做工具——这是**产品的好特性**，却让「伪完成/未来时」的测试构造难以自然发生。
- TC-3.1 变体：未自然产生伪完成，但**两次真调 ppt_create→3 页 .pptx 真落盘（python-pptx 验 slides=3）+ verify strict 全程在场无误杀**——招牌链的「真产物+不误杀」实质达成，变体只是「拦截前置（伪完成）未触发」。合理。
- TC-3.4 变体：「将会生成」声明后 goal **仍 active 被 nudge 推 2 轮，直到第 3 次真调工具才 done**——未来时**从未**被当成完成（正是 testcase 要防的），变体是「系统更强地推到真做」。合理。
- 唯一留痕弱点：TC-3.3 标「PASS(行为级)」但 `aligned=False` 字面 log「未单独打」+ 无 misalign 截图，依赖「goal 仍 active 未被偏离产物标 done」的行为推断。**判定可接受，但证据强度低于其它 PASS**。

### c)「历史引用」类（5.3 / 5.8 引 06-06）：历史证据真实性 + 引用合理性

✅ **历史证据真实存在，引用合理（非该重测）**。

- **SKILL.md 至今在盘**：`.tmp/fp345-userdata/skills/user/meeting-minutes-to-ppt/SKILL.md`（712B，Jun 6 18:41）。内容为合规自创 skill：frontmatter `name/description/when_to_use/requires_script:false/author:self-codified` + 6 步 body——**与 testcase TC-5.3 步骤6 要求字段完全吻合**。
- **06-06 报告坐实 FULL PASS**：`manual-results-2026-06-06-FP345/RESULTS.md` 记「✅✅✅ TC-5.3 招牌全链真机 FULL PASS：技能卡真机渲染→真坐标 SendInput 点击『✓ 保存技能』(物理 1221,1053)→SKILL.md 落盘」+ TC-5.8「被自动召回预载的正是自创保存的 skill」。
- **引用合理性**：TC-5.3 招牌全链的「保存→落盘→复用」**核心闭环已在 06-06 真机贯通且产物至今未失**，本轮复证触发器+卡渲染≥5 次（`skill_codifier.proposed cid=13 steps=6`）。本轮为测 TC-5.4 全点忽略故未重复保存分支——**引用历史而非重跑保存分支是合理工程取舍**，非掩盖。

### d) 两个 FAIL（4.5 去重已修 / 5.1 待修）：是否如实未掩饰 + 修复可复验

✅ **两个 FAIL 均如实暴露，无掩饰**。

- **TC-4.5 去重 FAIL→已修**：如实记「步骤5 去重 FAIL：同 key goal_<sid> 15 行全 active 堆积（upsert 纯 INSERT，B-10 直连无 extractor 兜底）」→ 修复 `commit 75af4bd`。**源码核实**：`facts.py:401 upsert_replacing` = `find_active(:410)→upsert→mark_superseded(:415)`；`main.py:1428` B-10 钩改调 `upsert_replacing`。TDD 测试在 `test_goal_decision_facts.py`（75af4bd +34 行），**我复跑 49 passed 含此**。可复验。
- **TC-5.1 待修 FAIL**：如实记「top_sim 恒 0 + total=1」标待修，**未硬凑 PASS**。缺陷**真实存在且被源码自己写明**：`skill_matcher.py:118-128` 注释明示「生产 BGE-M3 encode 是 async+收 list，但 sync match() 走 `encode(query)`（:98）期望 sync → except 吞 → 缓存恒空 → 自动披露永远零匹配(top_sim=0.0)」。RESULTS 标的「R-16 模式回归」与源码注释一致——**这是诚实自查的强信号（连根因都在生产代码留了注释）**。修复尚未做（标待修），符合 FAIL 语义。
- **连带阻塞 TC-5.7 如实标「未触发」**：auto_loaded=0 → 无 skill 可重挂，逻辑链成立，未谎称 PASS，单测 15 passed 兜底。

---

## ④ 漏项 / 需补清单

| # | 类型 | 位置 | 详情 | 建议 |
|---|---|---|---|---|
| L1 | 截图漏项（一轮遗留未补） | FP-1 TC-1.7 步骤5 | `tc-1.7-goalcheck.png` 仍不在盘 | 补 1 张或 RESULTS 显式注「截图遗漏，PASS 由 log+DB 支撑」。功能链成立。 |
| L2 | 截图漏项 | FP-3 TC-3.3 步骤4 | `tc-3.3-misalign.png` 未留 + `aligned=False` 字面 log 未单独打 | 行为级判定可接受但留痕最弱。建议补 misalign 截图，或加 goal_checker 内嵌对照的可 grep 锚点。 |
| L3 | 截图留痕（06-10 轮系统性） | FP-3/4/5 多 TC | 06-10 screenshots/ 仅 7 张（tc-3.1×2/3.2/4.1×2/4.2/4.3），testcase 指定的 3.3/3.4/3.6/4.4/5.1/5.2/5.3/5.4/5.5/5.6/5.7/5.8 截图未在本轮目录 | **多数可接受**：后端/历史/未触发/FAIL 级 TC 本不依赖截图，且 3.4 在 06-06 有图、5.2 引 FP-2 真图、5.3/5.8 引 06-06。**真机 PASS 但无截图者仅 TC-3.3/3.4（3.4 有 06-06 图）**——建议 06-10 RESULTS 顶部统一注明截图策略（真机 PASS 见本目录；变体/历史见交叉引用）。 |
| D1 | 诚实偏差（可接受） | 1.4 / 2.7 / 3.5 / 4.6 | 可选真机/故障注入未做，以脚本+单测+接线行号兜底 | 接受。均为 testcase 钦定 🔵/🟡 级手段或可选步骤，且已复跑/独立 grep 命中。 |
| N1 | 信息差（非缺陷） | TC-1.4 | sha256 复跑值与 RESULTS 记录不同 | 接受（一轮已核）。live DDL 字节随 schema/环境变，核心断言"no session_goals table"恒成立。 |

**无任何「testcase 写了但 RESULTS 完全没覆盖也没解释」的步骤**——所有未执行步骤均带显式诚实标注与理由。

---

## ⑤ 诚实性结论

1. **两个 FAIL 全部敢报、可复验**：TC-2.1（已修→PASS，4 刀 commit+源码+复跑全绿）、TC-5.1（待修，缺陷连根因都在生产源码注释里自承）。无一处「FAIL 硬凑 PASS」。
2. **修复声明 100% 属实**：4 刀 compaction 修复 + upsert_replacing 去重修复，commit 全在 git log、commit message 含 TDD 红→绿、源码逐行核实、pytest 复跑全绿（49+64+1+remount/upsert 套件）。
3. **历史引用真实合理**：自创 SKILL.md 产物至今在盘，06-06 招牌全链 FULL PASS 有据，引用而非重跑保存分支是合理取舍。
4. **变体/后端/未触发判定均带诚实限定词**：completion_nudge 过强导致伪完成/未来时难自然构造，是真实产品行为，变体理由成立非借口。
5. **唯一系统性瑕疵=截图留痕**：FP-3/4/5 06-10 轮多个指定截图未留，但判定级别不依赖该截图，逻辑链由 pytest/log/DB/历史报告/交叉引用兜底，属证据完整度而非功能未验。

**总结**：第二轮全范围（40 TC）核对，未发现走捷径、未发现修复造假、未发现 FAIL 掩饰。建议**接受归档**；归档前可选补 TC-1.7 / TC-3.3 两张截图并在 06-10 RESULTS 顶部统一注明截图交叉引用策略。
