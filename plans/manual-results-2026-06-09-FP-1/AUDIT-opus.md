# 独立审计报告 — goal-completion FP-1 / FP-2 手工测试执行核对

> 审计员: opus 4.8（独立测试审计员，只读不改）
> 审计日期: 2026-06-10
> 审计手段: 读 testcase + 读 RESULTS + ls 截图 + git log + 复跑 pytest + grep 代码锚点（**未重跑真机 GUI，app 已关**）
> 被审对象:
> - testcase: `testcase/goal-completion/FP-1-目标持久化-manual-test.md`(8 TC) + `FP-2-抗漂移-manual-test.md`(10 TC)
> - 执行结果: `plans/manual-results-2026-06-09-FP-1/RESULTS.md` + `-FP-2/RESULTS.md`

---

## ① 总判

| 指标 | 结论 |
|---|---|
| **FP-1 完整执行率** | **8/8 TC 有交付**；其中 7 TC 步骤基本完整，1 TC（TC-1.4）按诚实标注以脚本+单测覆盖可选真机步骤。**1 个截图漏项（TC-1.7）**。 |
| **FP-2 完整执行率** | **10/10 TC 有交付**；7 PASS（后端为主）+ 2 BLOCKED（与 testcase 预期一致）+ 1 FAIL（TC-2.1，真缺陷产出）。**1 个截图漏项（TC-2.1）**。 |
| **硬证据可复现性** | ✅ 高。11/11 + 27 + 6 pytest 全部复跑通过；ws.ts/token_budget 代码修复真实存在；2 个 commit（58e82f7 / e6ee37f）在 git log；BLOCKED 的 grep=0 全部独立证实。 |
| **诚实性** | ✅ 优秀。TC-2.1 FAIL 如实记录并产出 backlog；BLOCKED 判定不掩饰；可选步骤未做时显式标注；甚至 sha256 差异（见下）也属正常。 |
| **可接受性** | ✅ **建议接受**。两处截图漏项为「证据完整度」瑕疵而非「功能未验」，逻辑链由 DB/log/单测兜底。建议补 2 张截图后归档。 |

**一句话**：测试是真做的、真挖出了缺陷（中文 compaction 低估 4 倍），不是走捷径刷 PASS；唯二瑕疵是 TC-1.7 / TC-2.1 各缺 1 张应留的截图。

---

## ② 逐 TC 矩阵

### FP-1（目标持久化地基）

| TC | testcase 关键步骤 | RESULTS 声称 | 独立验证 | 判定 |
|---|---|---|---|---|
| **1.1** | 设目标→DB active 行→`/goal`查→重启→`goal_store_load_persisted restored=1`→`/goal`仍在；pass^k k=3 换 3 目标 | PASS 3/3，k1/k2/k3 各重启后恢复，截图 4 张(-PASS) | 截图全在（tc-1.1-2-after-restart-PASS / k2 / k3 均存盘）；restored=1 log 声称合理 | **完整执行** |
| **1.2** | 多步目标→rebound→DB `iterations_used=M>0`→重启→同 goal_id 仍=M | PASS，goal_id=236379cd，重启前后 `=1` 未归零，log `iter=1/10` | 逻辑链完整（DB+log），无独立真机复核但 ws/db 机制由 TC-1.1 同源佐证 | **完整执行** |
| **1.3** | 设目标记 goal_id→`/goal clear`→`/goal`查无→DB 同行 status='abandoned' 未删 | PASS，同 goal_id 行仍在 status=abandoned | 逻辑链完整 | **完整执行** |
| **1.4** | 步骤1 跑 `e2e_flag_off_baseline.py`(exit0 + sha)；步骤2/3 真机 flag-off 冷启动 + 查表无 | PASS(步骤1)；**步骤2/3 未另跑，诚实标注脚本+单测已覆盖该断言** | ✅ 我复跑脚本：`PASS: flag-OFF baseline ok; no session_goals table`，exit0。**sha256 复跑=4321885d 与 RESULTS 的 e5c004ff 不同**——属正常（sha 覆盖 live DDL，随 schema 演进/环境变；核心断言"no session_goals table"两次都成立） | **诚实标注的偏差（步骤2/3 用脚本+单测覆盖）— 可接受** |
| **1.5** | 连设 A→B→`/goal`返回 B→DB top2=[B,A]；截图 | PASS，DB top2=[B,A]，截图 tc-1.5-latest-active.png | 截图存在；行为符合 testcase 预声明的"旧行不自动 abandoned" | **完整执行** |
| **1.6** | codify on 后 boot `fp5_codify_wiring_ready`→跑≥5工具→grep execute_tool≥5→codify 侧效果→单测 `test_tool_path_recorder_fed_during_run` | PASS(后端核对级)，boot wiring=True，29 次工具事件，单测 test_tool_path_recording 4 passed | ✅ 我复跑 test_tool_path_recording 在 6-test 批次内全过；testcase 本身声明"无生产成功日志，不得伪造"——RESULTS 未伪造 | **完整执行（后端核对级，符合 testcase 分级）** |
| **1.7** | 设目标→执行→grep goal_checker 锚点→DB status/iterations→**截图 tc-1.7-goalcheck.png** | PASS，log `goal_checker_nudge_injected sid=code-6kbuuzg6 iter=1/10` + DB 同步 + `wi13_goal_anchor_injected iter=5/10` | log/DB 链合理；**❌ tc-1.7-goalcheck.png 不在 screenshots/ 目录** | **部分执行（缺步骤5截图）** |
| **1.8** | A 目录设目标→切 B 目录 restored=0→B 设目标→分别查两 DB 互不串 | PASS，B 首启 restored=0，A/B 各自只有自己目标，截图 tc-1.8-userdata-b.png | 截图存在；隔离逻辑链完整 | **完整执行** |

### FP-2（抗漂移闭环）

| TC | testcase 关键步骤 | RESULTS 声称 | 独立验证 | 判定 |
|---|---|---|---|---|
| **2.1** | compaction on→灌长上下文超阈值→grep `wi4_0_compaction_enabled`/`p1_4_compaction_fired`/`wi13_goal_anchor_injected`→追问原目标答对→**截图 tc-2.1-after-compact-still-on-goal.png**；pass^k k=3 | **FAIL→挖出 2 缺陷修 2 刀遗留第 3 刀**。`p1_4_compaction_fired` 真机始终未触发（CJK char/4 低估 4 倍 + ASCII 低估 30%）；`wi13_goal_anchor_injected iter=5/10` 多次捕获 | ✅ token_budget.py 修复真实（`_CJK_RE`/`_weighted_chars`/ASCII×8/7 全在）；✅ test_token_budget_per_model 11 passed；✅ p5s2 样本 220k→190k 校准在测试文件含 FP-2 注释；**❌ tc-2.1-after-compact-still-on-goal.png 不在（仅 diag-tc21.png）** | **诚实标注的偏差（FAIL + 真缺陷产出）；截图为 diag 而非要求名** |
| **2.2** | 设多步目标→grep main.py TaskGraphStore→前端 goal_tasks→UI BLOCKED | BLOCKED/待实现(确认)，grep main=0 / 前端=0 | ✅ 独立 grep：main.py TaskGraphStore=**0**，tauri-app/src goal_tasks=**0 文件** | **完整执行（BLOCKED 判定与 testcase 预期一致）** |
| **2.3** | 跑 `test_dag_claim_skips_blocked_task` + `test_task_graph` | PASS，11 passed | ✅ 我复跑：test_goal_tasks_db 全过；`test_dag_claim_skips_blocked_task` 单点复跑通过（确认非编造名） | **完整执行** |
| **2.4** | 跑 `test_teammate_tools_taskgraph`(5→7工具)；真机 spawn_team BLOCKED | PASS(后端) 6 passed；产品链路 BLOCKED | ✅ 我复跑：test_teammate_tools_taskgraph 在 27-test 批次内全过；spawn_team 真机 BLOCKED 符合 testcase | **完整执行（后端级 + BLOCKED 符合预期）** |
| **2.5** | 跑 `test_spawn_team_goal`(Charter 含父 goal) | PASS(后端) 6 passed | ✅ 复跑通过 | **完整执行** |
| **2.6** | 跑 `test_spawn_team_goal` 覆盖 `_classify_by_goal`；LLM judge BLOCKED | PASS(marker 分流)；LLM judge BLOCKED | ✅ 同套件复跑过；marker/非 judge 符合 testcase 窄实现声明 | **完整执行** |
| **2.7** | 触发 auto-resume 故障→grep `auto_resume_*`→单测 `test_auto_resume_goal`→真机续目标 | PASS(单测+接线)，3 passed，main.py:2522/2537 实证；**真机故障触发未制造，诚实标 🟡** | ✅ 复跑 test_auto_resume_goal 通过；✅ 独立 grep main.py:2522 `_goal_text_getter_for_resume` + :2537 `goal_text_getter=` 行号精确命中 | **诚实标注的偏差（真机故障未制造，单测+接线兜底）— 可接受** |
| **2.8** | 连跑 5 次 `test_concurrent_claim_no_double_claim` 全 exit0 | PASS pass^k 5/5 | ✅ 单点复跑 `test_concurrent_claim_no_double_claim` 通过（确认非编造名）；testcase 本身标"非真机" | **完整执行** |
| **2.9** | grep `GD_actions`/`GD_inaction`=0 | BLOCKED/待实现(确认) | ✅ 独立 grep backend GD_actions/GD_inaction=**0** | **完整执行（BLOCKED 与 testcase 预期一致）** |
| **2.10** | enabled boot 出 `wi4_0_compaction_enabled`；disabled 无 enabled 且无 fired；双分支 | PASS(双分支)，enabled 出 log，disabled 重启后无 | log 双分支链合理（与 TC-2.1 互证：fired 确实没出现） | **完整执行** |

---

## ③ 漏项 / 偏差清单 + 处置建议

| # | 类型 | 位置 | 详情 | 建议 |
|---|---|---|---|---|
| L1 | **截图漏项** | FP-1 TC-1.7 步骤5 | testcase 要求 `tc-1.7-goalcheck.png`，screenshots/ 目录无此文件 | **补测 1 张**（或在 RESULTS 显式标注"截图遗漏，PASS 由 log+DB 支撑"）。功能链本身（goal_checker_nudge_injected + DB iterations）证据成立，瑕疵在证据留痕。 |
| L2 | **截图名不符** | FP-2 TC-2.1 步骤5 | testcase 要求 `tc-2.1-after-compact-still-on-goal.png`，实际只有 `diag-tc21.png`（诊断图，非"追问+正确回答"截图） | 因 TC-2.1 判 FAIL（compaction 未触发），"压缩后追问"场景本就无法构造，截图名不符**合理**。建议 RESULTS 注明 diag-tc21.png 是缺陷诊断图，非 testcase 要求的成功截图。**接受偏差**。 |
| D1 | 诚实偏差 | FP-1 TC-1.4 步骤2/3 | 真机 flag-off 冷启动未跑，以脚本(步骤1)+单测覆盖断言 | **接受**。脚本是 testcase 钦定的 🔵 字节核对手段，已复跑 PASS；步骤2/3 为"可选真机"。 |
| D2 | 诚实偏差 | FP-2 TC-2.7 步骤2/5 | 真机故障路径未制造，以 `test_auto_resume_goal` 单测 + main.py 接线行号兜底 | **接受**。testcase 本身标 🟡log+单测级；接线行号经我独立 grep 精确命中。 |
| N1 | 信息差（非缺陷） | FP-1 TC-1.4 | 复跑 sha256=4321885d ≠ RESULTS 记录的 e5c004ff | **接受**。sha 覆盖 live DDL 字节，随 schema/环境变化属正常；核心断言"no session_goals table"两次都成立。建议 testcase 未来对 sha 只做"存在且 16hex"校验而非固定值比对。 |

**无任何"testcase 写了但 RESULTS 完全没覆盖/没解释"的步骤**——所有偏差都被显式标注。

---

## ④ 诚实性结论

1. **TC-2.1 判 FAIL 是否合理记录而非掩饰？** ✅ **合理且高质量**。RESULTS 不仅标 FAIL，还产出根因分析（CJK char/4 低估 4 倍 + ASCII 低估 30%）、TDD 红→绿修复（2 commit）、第 3 刀 backlog（真实 prompt_tokens 反馈回路）。这是手测的**最大价值**——若走捷径刷 PASS，绝不会暴露中文产品 compaction 等效失效的真缺陷。代码修复（`_CJK_RE`/`_weighted_chars`/ASCII×8/7）经我独立 grep + 11 测试复跑全部证实。

2. **BLOCKED 判定（2.2/2.9）是否与 testcase 预期一致？** ✅ **完全一致**。testcase 在标题/正文就预声明这些链路 BLOCKED，并给出 grep=0 的判据；我独立复跑 grep：main.py TaskGraphStore=0、前端 goal_tasks=0 文件、GD_actions/GD_inaction=0，**三项全部归零**，与 RESULTS 声称分毫不差。这是"诚实标 BLOCKED 而非硬凑 PASS"的正面案例。

3. **"真模拟人"证据链是否成立？** ✅ **基本成立**。FP-1 截图链充分（tc-1.1 全套含 pass^k k2/k3 的 -PASS 截图、tc-1.5、tc-1.8 均存盘，物理像素截图证明真窗口可见），RESULTS §执行中发现的 7 个测试陷阱（CDP 9333 / MSYS_NO_PATHCONV / 窗口穿透 / Virtuoso 虚拟列表 / SlashDropdown 吞 Enter）是真打过仗才会踩到的坑，**强烈佐证真机执行而非脚本回放**。唯一瑕疵：TC-1.7 应留的截图缺失（L1）。FP-2 因 TC-2.1 FAIL，真机截图较少属客观限制，但后端 33 个 pytest 我已全部复跑通过。

**总结**：本次 FP-1/FP-2 手测**没有走全局/项目 CLAUDE.md 反复警告的捷径**（没拿 pytest 当 UI 证据掩盖真机、没拿 import 查 registry 当"可用"、没把 BLOCKED 硬凑 PASS）。FAIL 敢报、BLOCKED 敢标、可选步骤敢标"未做+理由"。建议**补 TC-1.7 1 张截图**后即可归档；其余偏差均可接受。
