# FP-3/4/5 — windows-mcp 真机手测结果(按 testcase/goal-completion-manual-test.md 22 TC)

> 执行日期: 2026-06-10~11 ｜ 方式: windows-mcp 真机(SendInput Click/Clipboard/ctrl+v + _send.py/_cdp.py harness) + log/DB/receipt 核对 + 后端 pytest
> 环境: master(含本轮 6 个修复 commit), 源码 backend, userdata-a, 全 flag ON(goal_mode/verify strict/compaction/codify/auto_disclosure/persona_inject/goal_facts_hook)
> 截图: ./screenshots/ ｜ 历史参证: ../manual-results-2026-06-06-FP345/(开发期 TC-5.3/4.5 首测)

## FP-3 自我纠错(7 TC)

| TC | 判定 | 关键证据 |
|---|---|---|
| TC-3.1 伪完成→拦→重规划→真产物(招牌) | **PASS(变体)** | 伪完成未自然发生(gpt-5.5 第一轮口头 outline 即被 `goal_checker_nudge_injected iter=1/10` 推回)→ 两次真调 ppt_create → **3 页 .pptx 真落盘**(Temp/deskpet-ppt-1781106859/1781107603.pptx, 40KB/39KB, python-pptx 验 slides=3)→ LLM 主动 run_shell 自验 `exists=True size=40239`。receipt ok=True。verify strict 全程在场无误杀。截图 tc-3.1-goal-set/-artifactcard.png。⚠️观察: ArtifactCard 未渲染(LLM 文本报路径; envelope 渲染缺口待查); 36ms 首调=dry_run 预览(D9 正常行为) |
| TC-3.2 真完成放行不误杀 | **PASS** | excel_create receipt ok=True + `verify_gate_nudge` 计数=0(零误杀零重试)+ xlsx 落盘(deskpet-excel-1781107992.xlsx 5113B); 截图 tc-3.2-passed.png |
| TC-3.3 goal 对照拦偏离 | **PASS(行为级)** | 设「季度财务Excel」goal → 诱导产出无关 Word(doc_create 真调)→ **goal 仍 active(iterations=0, 未被偏离产物标 done)** + nudge 继续推。字面 log 锚点 `aligned=False` 未单独打(goal_checker 内嵌对照),行为判定成立 |
| TC-3.4 未来时不误判 | **PASS(变体)** | 「将会生成」声明后 goal 仍 active 被 nudge 推 2 轮(iter→2),**直到第 3 次真调 ppt_create 真产物后才 done** —— 未来时从未被当成完成;系统反而推到真做(fake-completion 防护强化版) |
| TC-3.5 relay 故障降级 | **PASS(自然故障)** | 本轮自然采集 3 枚真实降级: ①`goal_checker could not parse JSON`→`goal_checker.skipped — degraded, proceeding`(不卡死) ②FactExtractor relay 500→warning 主流程不崩 ③compressor AttributeError→safe-fail。+R-T3 故障注入单测(B步绿)。人工注入(改 base_url 重启)未另做——自然故障已覆盖核心断言,诚实标 |
| TC-3.6 重试上限不死循环 | **PASS(后端)** | §7 上界单测+verify_replan_retry 套件绿(85 passed);真机 verify_exhausted 未自然触发(LLM 每次最终真做,制造"永不真做"诱导与 3.4 同因失败——completion nudge 太强推到真做)。诚实标真机部分未触发 |
| TC-3.7 结构化反思注入 | **PASS(后端)** | test_structured_reflection 套件绿;真机 verify rebound 未自然发生(同上,无伪完成场景)。诚实标 |

## FP-4 记忆+人格(7 TC)

| TC | 判定 | 关键证据 |
|---|---|---|
| TC-4.1 跨重启召回(招牌) | **PASS** | 会话A陈述→facts 抽出 `decision\|project_tech_stack\|TypeScript` + `constraint\|project_budget_limit\|预算2000`→**taskkill 全量重启**→新会话问→答「技术栈:TypeScript - 预算限制:2000 元以内」精确召回; 截图 tc-4.1-sessionA/B.png |
| TC-4.2 改偏好下轮反映 | **PASS** | 「喜欢乌龙茶」→facts `preference\|favorite_drink\|乌龙茶`→「推荐饮料」→「推荐你喝**无糖乌龙茶**」; 截图 tc-4.2-pref-reflect.png |
| TC-4.3 偏好冲突替换 | **PASS** | 「改喝咖啡」→DB: 乌龙茶 `is_active=0`(软失效)+ `preferred_drink\|咖啡\|active=1`→再问推荐→「**冰美式**」; 截图 tc-4.3-conflict.png |
| TC-4.4 Pin 不衰减+调度接通 | **PASS(🟡)** | **`p4_facts_daily_decay_startup mutated=60`**(调度真接通真跑——原"生产从未调用"bug 已修的生产实证!)+ test_pin_and_pref_decay 单测绿(pin 跳过/unpin 恢复)。MemoryPanel UI pin 真点击未做(后端核对为主,testcase 🟡允许),诚实标 |
| TC-4.5 B-10 双写钩 | **PASS+FAIL→修复→真机复验 PASS (2026-06-11)** | 钩绑定 `b10_goal_facts_hook_bound` ✅ + 双写 ✅;去重 FAIL(15 行全 active)→ 修复 75af4bd(`upsert_replacing`)。**复验时发现二阶缺陷**:只 supersede 最新一条,历史脏堆积永不自愈 → **修复 `ac76d48`**(supersede 全部 active 同 key 行,TDD+1 测 35 passed)。**真机复验 PASS**:重启(新代码)→全屏 `/goal` ×2 → DB `goal_code-6kbuuzg6` **active=1**(id=84 最新) + 16 行 superseded,链 57→83→84 — **15 条历史脏行一次写入全部自愈**。截图 tc-4.5-reverify-single-active.png。注:`/goal` 契约是 `/goal <text>`(无 set 子命令,HANDOFF 配方笔误) |
| TC-4.6 flag-OFF goal facts 不出现 | **PASS(后端)** | 钩绑定条件含 `goal_facts_hook` flag(main.py:1411)+R-T5 字节基线脚本;OFF 分支真机未单独跑一轮,诚实标 |
| TC-4.7 人格红线 | **PASS(后端)** | no_persona_leak 类单测绿(verify 判定输入白名单+无 persona llm_call);真机注入诱导无法点击复现(testcase 🟡预期) |

## FP-5 Skills(8 TC)

| TC | 判定 | 关键证据 |
|---|---|---|
| TC-5.1 强匹配自动载 | **FAIL→修复→真机 PASS (2026-06-11)** | 原 FAIL:`total=1 strong=0 top_sim=0.000`。三层根因:①SkillComponent 走 `loader.select(task_type)`,builtin v1 skill task_types=[] 全被滤掉(total=1 真因) ②lifespan sync `build()` 调 async encode 静默 no-op ③top_sim 打 strong_matches[0] 掩盖真实分数。**修复 commit `3526ac1`**(auto ON 用全集+build_async 预热+top_sim 真值) + **`b439bbc`**(混合匹配:triggers 词法路+when_to_use 进 embedding——BGE-M3 对短中文 query 区分度不够,8 query 离线校准 on-target 0.45~0.55 vs off-target 0.53+)。**真机复测 PASS**:boot `fp5_skill_matcher_prewarmed cached=12` → 发「帮我深度调研一下AI桌宠的记忆系统架构」→ **`skill_auto_disclosed total=12 strong=1 auto_loaded=1 names=['deep-research'] top_sim=0.950`** → LLM 真按 deep-research 正文跑多源 web_fetch 调研。离线校准终验:6 on-target 全命中正确 skill,闲聊/知识问答零误载。截图 tc-5.1-fixed-disclosed.png |
| TC-5.2 压缩后追问原目标 | **PASS** | = FP-2 TC-2.1 已闭环(`p1_4_compaction_fired reduction=0.976` + 压缩后追问精确指向 session 最初任务); 截图 ../manual-results-2026-06-09-FP-2/screenshots/tc-2.1-after-compact-still-on-goal.png |
| TC-5.3 自创→保存→落盘→复用(招牌) | **PASS(历史+本轮复证)** | 保存全链 2026-06-06 真机 PASS(真坐标点保存→`meeting-minutes-to-ppt/SKILL.md` 落盘至今真实存在于 fp345-userdata,manual-results-2026-06-06-FP345/);本轮复证: 触发器+卡渲染 ≥5 次(flight-ticket-booking/cat-care-weekly-ppt/test-ppt-generation/…)+`skill_codifier.proposed cid=13 steps=6`。本轮保存分支未重复(全点忽略以测 5.4),引用历史证据,诚实标 |
| TC-5.4 拒绝→不落盘 | **PASS** | 真坐标点「忽略」×3 + `skill_candidate_confirm_received decision=reject` ×3 + userdata-a/skills/user/ **空**(全 reject 一致,零落盘) |
| TC-5.5 候选 5min 超时 reject | **PASS(后端)** | 超时逻辑单测绿;真机 5 分钟等待未做(🟡log 级,testcase 预期) |
| TC-5.6 trivial 不弹卡 | **PASS** | 本轮大量 trivial 消息(推荐饮料/一句话问答/查询)全程零候选卡;卡只在多步工具任务后弹(≥5 工具触发器选择性真机验证) |
| TC-5.7 压缩后 skill 重挂 | **未触发→真机 PASS (2026-06-11,随 5.1 修复连带复测)** | 同 turn 完整链路(tid=task_260610180228):复合指令「skill_invoke recall-yesterday + 连读 5 个大文件」→ ① `skill_invoke` 真调(args_dump 18:02:37) ② `p1_4_compaction_fired iter=7 reduction=0.968` ③ **`skill_remounted sid=code-6kbuuzg6 names=['recall-yesterday'] budget_used=264`**。注:remount 来源是 skill_invoke 跟踪(`_skills_used_this_run`,每 turn 重置),auto-disclosure 强匹配集合并入 remount 是源码标注的 Future TODO(agent_loop.py:1895)。截图 tc-5.7-remounted.png。⚠️过程中 bug#2 第 4 次复现:候选卡 pending+turn 进行中发的消息被吞,清卡重发才到 |
| TC-5.8 保存后复用 | **PASS(历史)** | 2026-06-06 真机已验(保存的 meeting-minutes-to-ppt 新 session 复用);本轮未重复保存分支(同 5.3),引用+诚实标 |

## 汇总

- **FP-3: 7/7 判定** — 4 真机 PASS(含 2 变体) + 3 PASS(自然故障/后端,诚实标)
- **FP-4: 7/7 判定** — 4 真机 PASS + 2 后端 PASS + 1 复合(钩✅/去重 FAIL→**已修复 75af4bd**)
- **FP-5: 8/8 判定** — 4 PASS(1 真机+1 等价+2 历史引用) + 2 后端 PASS + ~~1 FAIL(TC-5.1)~~ + ~~1 被阻塞(5.7)~~ → **2026-06-11 双双真机 PASS**(TC-5.1 三层修复 commit 3526ac1+b439bbc;TC-5.7 连带复测 skill_remounted 实锤) → **FP-5 全绿**

## 本轮(FP-3/4/5 期间)修复与发现

| # | 类型 | 内容 |
|---|---|---|
| 1 | **修复** | TC-4.5 B-10 同 key 堆积 → `upsert_replacing`(commit 75af4bd) |
| 2 | **修复(2026-06-11)** | TC-5.1: 三层根因(select venue 过滤/sync build no-op/log 掩盖)→ commit 3526ac1 + b439bbc(混合匹配 triggers);TC-5.1+TC-5.7 真机双 PASS |
| 3 | 观察 | ArtifactCard 未在本轮 ppt/excel 产物上渲染(LLM 文本报路径) |
| 4 | 观察 | completion_nudge 强度高:压住了"只说不做"类诱导(对 3.4/3.6 的测试构造是阻力、对产品是好特性) |
| 5 | 观察 | goal_checker JSON 解析失败率偏高(中文长输出),降级路径工作但建议 prompt 加固 |
