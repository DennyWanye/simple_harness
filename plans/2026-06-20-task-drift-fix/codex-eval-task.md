# CODEX 独立评估任务 — 任务漂移修复验收

你是 DeskPet 项目（G:/projects/deskpet，分支 master @ 91605e7）的**独立验收评估员**。前面由另一套 agent 完成了「任务漂移」bug 的修复 + 真机测试，现在要你**独立核验、批判性评估、不放水**。你与之前的工作**无共享上下文**，一切以代码 + 文档证据为准。

## 背景（被测 bug）
桌宠聊天单一 `session="default"` 永续会话从不按任务切分，最近 5 条原始历史（DB 里 627 条、CATL/宁德时代主题占压倒）被零门控提升成"正在进行的对话线"贴在当前 user 前 → 用户发一个**完全无关的新请求**（如"深度调研 Rust Tokio"）时，LLM 会**漂去研究上下文里的旧 CATL 主题**。修复目标：新请求不漂、且不破坏正常追问连续性。

## 你要回答的两个问题（各给明确结论 + 依据 + 评分）

### 问题 1：计划是否 100% 完成？
权威计划：`plans/2026-06-20-task-drift-fix/00-fix-plan.md`（重点 §3 机制、§8 风险分层 Tier1/Tier2、§9 改点表 A1-A6 / B1-B4、§8.2 决策）。
- 逐条核对计划改点 vs master 实际代码（下列文件），每条判 ✅已实现 / ⚠️偏差 / ❌缺失，给 `file:line` 证据：
  - Fix A：`backend/deskpet/agent/assembler/components/memory.py`、`bundle.py`、`assembler.py`、`policy.py`、`policies/default.yaml`
  - Fix B：`backend/agent/agent_loop.py`、`backend/deskpet/tools/research_tools.py`、`backend/main.py`
- **注意**：真机测试后计划有演进（见 `testcase/2026-06-20-task-drift-fix/RESULTS.md` §0「三轮迭代」+ 手测文档 v3 头部）——Tier2 从"默认关"改为"默认开"、`len>50` 改可配 `topic_shift_min_len=16`、Tier2 加了"词法重叠兜底"。评估"100% 完成"要把这些**真机驱动的演进**算作计划的一部分（是合理的迭代，不是偏离），但要判断它们是否**自洽、有测试覆盖**。
- 给完成度百分比（如 18/18=100%）+ GAP 清单（若有）。

### 问题 2：漂移 bug 是否真的被修复？证据链是否完善？
这是重点，要**批判性**：
- **读修复逻辑**：`memory.py` 的 Tier2 门控（`_topic_similarity` embedding 主路 + `_lexical_topic_shift` 词法兜底 + 合取判据 `低相似∧≥min_len∧非指代` + anaphora 豁免）+ Tier1 锚定/重定性 + Fix B 注入。判断逻辑是否真能堵住 bug、有没有逻辑漏洞或绕过。
- **审证据链**（`testcase/2026-06-20-task-drift-fix/RESULTS.md` + `TC-1-run1-log.txt` + 6 张截图文件名）：
  - 真机 TC-1（核心漂移）：`task_drift_context_gate l2_truncated=True shift_path=lexical 5→1` + `p5s2 topic=Rust(含 async-std/smol/monoio/glommio)` —— 这条证据**够不够硬**？能否证明"真不漂"而非偶然？
  - 真机 TC-4（追问不误伤）：两个追问 `l2_truncated=False` + 答 CATL —— 是否真证明了"没误伤连续性"？
  - 批判点（务必逐一表态，别替它圆场）：① 概率性漂移只真机跑了有限次（非连续 3+ 次同条件），结论可靠吗？② Tier2 **embedding 主路真机恒超时走了词法兜底**，embedding 路只有 mock 单测覆盖——这是不是证据链的洞？③ TC-1b/5b/9/10/11/12 等用例**没真机执行**（RESULTS 说由单测覆盖），可接受吗？④ 词法兜底（CJK-bigram 重叠 <15%）这个信号本身可靠吗，会不会误判/被绕过？
- **亲自跑测试核验**（用项目 venv，**只读评估、禁止修改任何文件**）：
  ```
  cd /g/projects/deskpet/backend
  .venv/Scripts/python.exe -m pytest tests/test_task_drift_fixa.py tests/test_task_drift_fixb.py -v
  .venv/Scripts/python.exe -m pytest tests/ -k "assembler or memory or policy or research or agent_loop" -q
  ```
  确认单测真绿、且测试本身**真覆盖了关键行为**（不是空测/灌水）。
- 给结论：bug **是否真修复**（是/部分/否）+ 证据链**完善度评分**（如 0-100）+ 最该补强的 1-3 个证据缺口。

## 输出格式
1. **问题 1 结论**：完成度 X/Y + 逐条核对表 + GAP。
2. **问题 2 结论**：bug 修复判定 + 证据链评分 + 批判点逐一表态 + 缺口清单。
3. **总评**：一句话——这个修复能不能放心交付，还差什么。

**约束**：只读评估，**不要修改/创建任何产品代码或文档文件**（可跑只读 pytest）。独立、严格、挑刺，发现问题直说，不替前面的工作圆场。
