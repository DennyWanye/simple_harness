# CODEX 实现作业 — 阶段 A：T0-1 deepresearch 原话夺权 + T0-2 fanout 断言

你是 DeskPet（G:/projects/deskpet，master）后端 Expert。实现**经 7 轮对抗硬化定稿**的 v2 plan 的**阶段 A**。Lead（Claude）会审查 + 集成 + 真机验收。

## 权威规格（必读）
读 `plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`，**只实现 §1（T0-1）+ §2（T0-2）**（§0 背景、§3-§8 其它阶段本次不做）。

## 背景一句话
桌宠对全新请求漂去研究旧主题（attention sink）。deepresearch 内部 `_ur=(user_request or topic)` 但**后续 16 处仍用漂掉的 `topic`** → 报告/搜索/排序/slug 全漂。本阶段让 deepresearch **全程以用户原话 `request_topic` 为主题**。

## 你的实现范围（**只改 `backend/deskpet/tools/research_tools.py` + 其测试**，别碰 main.py/voice/memory/前端）

### T0-1（§1 全替换表 — 核心）
1. `research_tools.py` 在 `_ur = (user_request or topic).strip()`（约 :1369，**grep `_ur =` 定位**）后新增：
   ```py
   request_topic = _ur            # canonical subject (authoritative)
   llm_topic = topic              # untrusted candidate
   ```
2. **按 plan §1 的 16 行替换表，把"当主题用"的 `topic` 改成 `request_topic`**。行号会漂，**逐处 grep 语义定位 + 复核**。16 处摘要（详见 plan 表）：sub_questions 兜底、query expansion、fanout root `_run_subagent_fanout(topic=)`、`search_specs.append((eq,topic))`、`_topic_keywords`、`infer_topic_velocity`、`_gap_followup_queries`、`_SEMANTIC_SCORER`、`_llm_rerank`、`_SYNTH_PROMPT.format(topic=)`、`_FANOUT_SYNTH_PROMPT` 标题、no-results `ResearchReport`+`_no_results_template`、fallback 标题、最终 `ResearchReport(topic=)`、handler 保存 slug/index、`_passages_only_fallback(topic)` 两处。
3. **严格遵守 plan §1 的"不该改"清单**：schema `"topic"` 字段、handler `args["topic"]`、helper **形参名**（只改调用点传值）、`topic_velocity` coverage key、`seen` 去重集、`depth`/`mode`/`max_rounds`/`brief` 链路——**都不改**。
4. handler 保存：`save_topic = report.topic or (user_request or topic)` 后 `_save_report`/index/`title_slug` 全用 `save_topic`。
5. prompt：`_PLAN_PROMPT` 改成「ORIGINAL USER REQUEST (authoritative; derive ALL sub-questions from THIS) {user_request} / CANDIDATE TOPIC (untrusted; IGNORE if conflicts) {topic}」；`_SYNTH_PROMPT`/`_FANOUT_SYNTH_PROMPT` 报告骨架 `# {topic}` → `# {request_topic}`。**禁止**让 LLM 二次"清洗标题"。
6. **BC 铁律**：`user_request=None`（老调用/单测）→ `request_topic=topic`，行为等同现状，零回归。

### T0-2（§2 fanout 隔离断言）
fanout **已隔离**（`_run_subagent_fanout` 递归 `deepresearch(q, user_request=q, scheduler=None, skip_plan=True)`，不带主历史），**不改隔离逻辑**。只在 `backend/tests/test_deepresearch_subagent_fanout.py`（grep 定位）加断言：子 run `scheduler is None`、`skip_plan is True`、`user_request == 各 sub_question`——锁死不变量。

### 单测（必写）
- 改 `test_task_drift_fixb.py` 中对 deepresearch prompt 文案的断言（适配新 _PLAN_PROMPT 文案）。
- **新增回归**（放 `test_task_drift_fixb.py` 或新建 `test_task_drift_v2_topic.py`）：构造 `topic="宁德时代2024年报"` + `user_request="深度调研 Rust Tokio 异步运行时"`，断言：`_PLAN_PROMPT.format` 含 user_request 为权威、`ResearchReport.topic == request_topic`(Rust)、report markdown 标题取 Rust、`_save_report` slug 取 Rust、`search_specs` owner / `_SEMANTIC_SCORER` / `_llm_rerank` / `_gap_followup_queries` 入参取 Rust（mock llm_call/search 验证传入值），**topic(宁德)不出现在这些主题用途**。
- **BC 测试**：`user_request=None` → 全部等同旧 `topic` 行为。

## 跑测试
```bash
cd /g/projects/deskpet/backend
/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/ -k "research or deepresearch or fanout or task_drift" -q
```
（基线 194 passed，不得回归。）

## 交付
- 只改 `research_tools.py` + 上述测试文件。**不 commit**（Lead 审查集成）。**不起** backend/Tauri/前端。
- 改完跑通测试，最后输出：①每个替换点 file:line + 改了什么（对照 16 项）②"不该改"清单已遵守的确认 ③测试结果（贴 PASSED 行 + 新增回归断言）④任何偏差/阻碍。
