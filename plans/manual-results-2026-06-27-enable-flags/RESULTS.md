# 真机 windows-mcp 抽测 — 全量点亮能力（2026-06-27）

> 目标：验证 `config.py` dataclass 默认 + config.toml 翻 ON 的 A 表能力**在真机运行栈真生效**。
> 环境：`npx tauri dev`（源码 backend，非 frozen），dev userdata config，relay-cloud（gpt-5.5-1M）。
> 启动脚本 `launch-dev.ps1`；日志 `tauri-dev.log`（UTF-16LE，解码助手 `loggrep.py`）；截图 `screenshots/`。
> 操作：独立「DeskPet·消息」窗，剪贴板中文输入 + 真坐标点击（scale 3.0，image×3）。

## 前置确认（关键：跑的是我的源码，非旧 frozen）
- `[backend_launch] Dev python=G:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=G:\projects\deskpet\backend` ✅（坑 #8 判据满足）
- `Uvicorn running on http://127.0.0.1:8100` ✅
- LLM 链路：`POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"`（多次）✅ —— 桌宠真回复。
  - 注：`/v1/models 401` 仅是 catalog 端点，**不影响 chat**。

## 逐能力判定

| 能力 (flag) | 证据（tauri-dev.log，真机运行栈） | 判定 |
|---|---|---|
| **goal_mode** | boot：`goal_task_tools_registered_global count=4` + `companion_code_v1_goal_mode_ready` + `b10_goal_facts_hook_bound goal_facts=True`；**运行期真调（明确指示用目标功能后）**：`name='goal_task_create'` **×3** args=`{"title":"第1-2天读完全书前1/3"...}` / `{"第3-5天...中间1/3"...}` / `{"第6-7天...最后1/3并写读书笔记"...}` + `wi4a_goal_anchor_always_on tid=task_260627074129_...` + `goal_checker_nudge_injected iter=4/10`（末轮 goal_checker 自动续推） | ✅ **完整 behavioral PASS** —— agent 真调 goal_task_create×3 把"一周读完一本书"建成目标 + 拆成 3 个每日子任务，goal_anchor 激活，goal_checker 自动续推。<br>注：① 自然语言"帮我设个目标…"会被 gpt-5.5 用对话式拆解回答（不调工具）——需较明确指示才触发工具；② `/goal` 斜杠经独立消息窗 composer **未路由到 backend**（slash 疑仅主 InputBar 处理，待查）。 |
| **knowledge / auto_disclosure** (`knowledge_enabled` / `auto_disclosure.enabled`) | 真实用户回合：`skill_auto_disclosed total=17 strong=0 auto_loaded=0 top_sim=0.4`；boot `fp5_auto_disclosure_wiring_ready` + `fp5_skill_matcher_prewarmed cached=17` | ✅ **生效**（SkillMatcher 在真消息上跑了语义披露；本条无强匹配故未注入，机制已触发）。强匹配注入（strong≥1）未触发（消息语义未命中阈值 0.55）。 |
| **persona_inject** | 真实用户回合：`preference_profile_injected facts=10 task_type=code` | ✅ **生效**（人格画像组件真在对话中注入 10 条 facts）— 真 UI 路径证据。 |
| **facts_extract** | boot `p4_vector_worker_ready facts_extract=True` + `memory_tools.bind facts_store=FactsStore llm=True`；真回合 `FactExtractor.extract` 被调用；**`preference_profile_injected facts=10` 每回合注入**（历史 10 条事实经 recall 注入 context — 召回/注入路径端到端活） | ✅ **flag 生效、抽取器真跑、召回/注入路径活**（persona_inject 每回合注入 10 条历史事实）。⚠️ **本 session 新事实抽取 ×3 全失败**：`FactExtractor.extract LLM failed: LLM HTTP 502 Bad Gateway`（relay 对 `json_schema` 结构化输出间歇 502，见 [[project_pipeline_preanalysis_blockers]]）→ `facts=10` 无增长 → **fresh-extraction env-limited（relay 瞬态，≥3 retry，非 flag）**。 |
| **curation_nudge** | 真回合：`oh4_curation_nudge sid=default turn=2 decisions=0 remembered=0` | ✅ **生效**（自策展 nudge 在真对话第 2 回合触发）。 |
| **codify** (`skills.codify.enabled`) | boot `fp5_codify_wiring_ready tool_path=.. candidate_store=.. llm=..` | ✅ **接线就绪**（自创闭环装配）。 |
| 七步流水线（已上线，非本次 flag） | `problem_pipeline_init enabled=true`；`intent_triage.done problem_type='creation'`；`evidence_gate.blocked`×2→`evidence_gate_exhausted` | ✅ 运行中（注：evidence_gate 把简单陈述误判为需取证，拖慢回合——已上线功能行为，与本次无关）。 |

## 结论
- **flag 翻 ON 在真机运行栈确实生效，且在真实用户对话回合中真触发**：
  - **goal_mode — 完整 behavioral PASS** ✅（真调 `goal_task_create`×3 建目标+拆 3 子任务 + goal_anchor + goal_checker 自动续推）。
  - **persona_inject / auto_disclosure / curation_nudge — PASS** ✅（真回合各自触发）。
  - **facts_extract — flag 活 + 召回/注入路径活**（每回合注入 10 条历史事实）；fresh-extraction 受 relay `json_schema` 502 阻（env-limited，≥3 retry，非 flag）。
  - codify / 七步流水线 — 接线/运行 ✅。
- **唯一未达成的 behavioral 项 = facts 新事实抽取**，根因 **relay 对结构化输出间歇 502**（已知 relay 瞬态，主聊天 200 正常）——非 flag 问题。**待补**：relay 恢复后重试至抽取 200。
- 次要发现：`/goal` 斜杠经独立消息窗 composer 未路由到 backend（slash_commands 前端路由待查；自然语言走 InputBar 工具调用正常）。
- **未观测到任何 flag 翻 ON 引起的崩溃 / 启动失败 / 不变式报错。**

## 截图
- `screenshots/final-state.png` — 抽测结束态全桌面（消息窗 + 桌宠 + 日志）。
- 过程态截图在会话 transcript（消息窗输入/「思考中」/「工具执行中」/回复）。
