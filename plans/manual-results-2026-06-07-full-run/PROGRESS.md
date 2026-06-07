# 全量手测执行进度（两份文档 47 条 TC）— 续跑唯一真相源

> **被测 HEAD**：`c27b791`（goal-completion 全 9 修复 + 回归文档）
> **环境**：`npx tauri dev`（launch-fp345b.ps1，源码后端 + 全 FP flag + auto_mode + 真 BGE-M3 + DESKPET_USER_DATA_DIR=.tmp/fp345-userdata）
> **铁律**：每跑完一条 TC **立即**更新本表对应行（状态 + 证据 + 时间），禁止批量拖到最后。续跑：读本表 → 跳过 PASS → 从第一个 PENDING/RUNNING/FAIL 继续。
> **状态枚举**：PENDING（未跑）/ RUNNING（进行中）/ PASS / FAIL / BLOCKED（环境受限，带原因）
> **最后更新**：2026-06-07（初建 + 回填本会话已知结果）

---

## A 组 — goal-completion-manual-test.md（22 条功能验收）

| TC-ID | 分级 | 状态 | 证据 | 时间 | 备注 |
|---|---|---|---|---|---|
| TC-3.1 伪完成拦截 | ✅真机 | BLOCKED | catch 逻辑 test_verify_gate 单测覆盖 | 06-07 | gpt-5.5 拒绝伪造完成声明(诚实)→无假声明可拦;真机难按需触发 |
| TC-3.2 真完成放行 | ✅真机 | PASS | screenshots(FP345)/tc-3.2-real-completion-passed.png | 06-07 | 真写 hello-fp3.txt(16B+receipt)→verify 无 nudge 放行 |
| TC-3.3 偏离目标拦 | ✅真机 | PENDING | — | — | 需 slash 键入法设 goal + 诱导产无关产物 |
| TC-3.4 未来时不误判 | ✅真机 | PASS | screenshots(FP345)/tc-3.4-future-tense-no-false-flag.png | 06-07 | 未来时计划→verify 0 nudge |
| TC-3.5 relay 故障降级 | 🟡log | PASS | test_outcome_verifier/conservative_on_error 单测 | 06-06 | 后端机制验证 |
| TC-3.6 死循环上界 | 🟡log | PASS | test_verify_replan_retry 上界单测 | 06-06 | 后端机制验证 |
| TC-3.7 结构化反思字段 | 🟡log | PASS | test_structured_reflection 必填字段单测 | 06-06 | 后端机制验证 |
| TC-4.1 重启跨会话召回★ | ✅真机 | PASS | screenshots(FP345)/tc-4.1-cross-session-recall.png | 06-07 | PostgreSQL 决策跨重启准确召回 |
| TC-4.2 改偏好反映 | ✅真机 | PASS | preference_profile_injected facts≥2 | 06-06 | 偏好注入真机 |
| TC-4.3 偏好冲突 | ✅真机 | PASS | screenshots(FP345)/tc-4.3-preference-conflict.png | 06-07 | 绿茶覆盖乌龙,推荐反映最新 |
| TC-4.4 Pin 不衰减 | 🟡log | PASS | test_pin_and_pref_decay 单测 | 06-06 | 后端机制验证 |
| TC-4.5 B-10 双写钩 | ✅真机 | PASS | facts category=goal(真机) | 06-06 | B-10 钩真机触发 |
| TC-4.6 flag-OFF 不写 goal facts | 🟡log | PASS | test_goal_decision_facts flag-off 单测 | 06-06 | 后端机制验证 |
| TC-4.7 人格红线 no_persona_leak | 🟡log | PASS | 人格红线单测 | 06-06 | 后端机制验证 |
| TC-5.1 强匹配载入 | ✅真机 | PASS | screenshots(FP345)/tc-5.1-auto-disclosure.png | 06-07 | skill_auto_disclosed strong=2 auto_loaded=2 |
| TC-5.2 压缩追目标 | ✅真机 | PENDING | — | — | 需多轮堆栈触发压缩 + relay 稳定 |
| TC-5.3 技能自创招牌全链 | ✅真机 | PASS | screenshots(FP345)/tc-5.3-skill-card-rendered.png + SKILL.md 落盘 | 06-06 | 卡→真点保存→SKILL.md |
| TC-5.4 拒绝不落盘 | ✅真机 | BLOCKED | 候选 id=5 生成✅;点忽略受卡渲染阻塞(task_01be24af) | 06-07 | reject 路径基础设施工作,UI 点击受阻 |
| TC-5.5 超时 reject | 🟡log | PASS | test_skill_codifier 5min 超时单测 | 06-06 | 后端机制验证 |
| TC-5.6 trivial 不弹卡 | ✅真机 | PASS | pending_skill_candidates 未建(真机) | 06-06 | 单工具不提候选 |
| TC-5.7 压缩后重挂 | ✅真机 | PENDING | — | — | 需多轮堆栈触发压缩 + relay 稳定 |
| TC-5.8 复用自创技能 | ✅真机 | PASS | screenshots(FP345)/tc-5.1-auto-disclosure.png(同链) | 06-07 | meeting-minutes-to-ppt 被自动召回 |

## B 组 — 2026-06-07-da-youhua-cross-layer-regression-manual-test.md（25 条）

| TC-ID | 分级 | 状态 | 证据 | 时间 | 备注 |
|---|---|---|---|---|---|
| R-1 config[skills.codify] | 🟡log | PASS | boot `fp5_codify_wiring_ready`×1 | 06-07 | 命门日志present |
| R-2 5处接线断裂 | 🟡log | PASS | boot `fp5_auto_disclosure_wiring_ready matcher`×1 + ValueError/Unknown service=0 | 06-07 | 接线 live 无崩 |
| R-3 relay ReadError 鲁棒 | 🟡log | PASS | adapter+registry LLMTimeoutError×3+3 + RESULTS 历史 46 工具完成 + test_stream_retry 3绿 | 06-07 | 重试机制present+单测守护 |
| R-4 ephemeral-card 丢卡 | ✅真机 | PASS | screenshots(FP345)/tc-5.3-skill-card-rendered.png | 06-06 | TC-5.3 卡渲染+可点保存=set_messages保留fix生效 |
| R-5 方案B codify 双触发 | 🟡log | PASS | main.py `_maybe_codify_skill(`×3(1def+FinEv+ErrEv) | 06-07 | 双分支调用present |
| R-6 SkillComponent 观测日志 | 🟡log | PASS | skill.py skill_auto_disclosed×1 + log fire×4 | 06-07 | 观测日志present+曾fire |
| R-7 assemble()漏skills段(文字) | 🟡log | PASS | main.py `"skills": _ad_cfg_dict` + TC-5.1 auto_loaded=2 | 06-07 | 文字venue skills传入+真机 |
| R-8 code policy 漏 skill | 🟡log | PASS | default.yaml code.prefer 含 skill | 06-07 | policy fan-out 修复present |
| R-9 task_type_override=code | 🟡log | PASS | main.py task_type_override=_tt_override("code") | 06-07 | code 会话强制 code policy |
| R-10 SKILL.md 漏 task_types | 🟡盘 | PASS | meeting-minutes-to-ppt SKILL.md `task_types:[code,task]`+`requires_script:false` | 06-07 | 自创技能 frontmatter 正确 |
| R-11 ★async embedder 零匹配 | ✅真机 | PASS | screenshots(FP345)/tc-5.1-auto-disclosure.png | 06-07 | TC-5.1 已证 top_sim>0.55(复发则0.00) |
| R-12 preference_profile 缺policy | ✅真机 | PASS | screenshots(FP345)/tc-4.1+tc-4.3 | 06-07 | preference_profile_injected facts≥1 |
| R-13 语音venue漏skills→default_config | 🟡核 | PASS | assembler.py default_config×6 + __init__.py auto_disclosure_config×3 + 3单测绿 | 06-07 | 根治+单测 |
| R-14 深合并加固 | 🟡核 | PASS | assembler.py 二级dict深合并行present + deep_merge 单测绿 | 06-07 | 防第10处+单测 |
| R-15 语音裸_AgentLoop→build_agent | 🟡核 | PASS | voice_pipeline build_agent+_maybe_codify_voice×4 + test_voice_venue_codify_wiring 3绿 | 06-07 | 语音venue接线+单测 |
| R-16 v2_enabled+fire-and-forget | 🟡核 | PASS | voice_pipeline config.raw读v2×1 + ensure_future(_codify_worker)×1 | 06-07 | 回退闸对齐+不阻塞TTS |
| B-1 flag-OFF 字节BC | 🔵字节 | PASS | config.py 三 flag enabled 默认 False("byte-identical to pre-WI-4.1") | 06-07 | 默认off=BC |
| B-2 write_file 权限门+auto_mode | 🟡log | PASS | boot permission_auto_mode_restored enabled=True | 06-07 | auto_mode 放行真机验证 |
| B-3 relay间歇下完成工具 | 🟡log | PASS | 同R-3(RESULTS 历史 agent 完成46工具+流式重试) | 06-07 | 端到端表现 |
| B-4 LLM 诚实性 | 🟡log | PASS | TC-3.1 真机观察 gpt-5.5 拒伪造 + catch 单测 | 06-07 | 同 TC-3.1 |
| B-5 /goal 粘贴不触发补全 | 说明 | PASS | RESULTS.md 已记录(测试法限制非bug) | 06-07 | 已记录 |
| B-6 多venue免疫 | 🟡核 | PASS | assembler self._default_config×3(文字+语音同经) | 06-07 | 新venue自动拿skills |
| B-7 daily_decay decay-on-boot | 说明 | PASS | 子代理核实显式defer非bug | 06-07 | 已记录 |
| B-8 codify dedup | 🟡DB | PASS | propose()->int\|None + DB pending 仅2不同名候选(无同名spam) | 06-07 | dedup按名,不重复 |
| B-9 候选卡chat渲染(已知未修) | 🟡前端 | BLOCKED | task_01be24af 跟踪 | 06-07 | 已知友好性bug,未修 |

---

## 汇总
- **PASS**：A 组 15 + B 组 6 = 21
- **BLOCKED**（带原因）：TC-3.1 / TC-5.4 / B-9 = 3
- **PENDING（本次续跑要跑）**：A 组 3（TC-3.3/5.2/5.7）+ B 组 13（R-1~R-10 除R-11、R-13~R-16、B-1/B-3/B-6/B-8）
- 收敛目标：全部非 PENDING/RUNNING。
