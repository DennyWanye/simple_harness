# DeskPet — 全局项目状态

> **最后更新**: 2026-06-09
> **维护方式**: 每完成一个里程碑 / 合并一个 worktree 后更新本文件。
> **用途**: 一页看清整个项目（所有并行工作流）的当前状态。新 session / 子代理
> 接手前先读这里。

---

## 1. 项目一句话

本地部署的桌面语音宠物：Live2D 桌宠 + 全本地语音管线（VAD → ASR → LLM → TTS）+
工具调用 + 长期记忆 + 技能系统。Tauri (Rust shell) + Python backend + React 前端。

---

## 2. 活跃工作流（git worktree 并行开发）

> 项目用多 worktree 并行开发，每个 worktree 独立分支 + 独立 dev 端口
> （见 `scripts/dev-worktree.ps1`）。下表为各分支当前状态。

| Worktree / 分支 | 负责模块 | 状态 | 文档 |
|---|---|---|---|
| **master** | 主线 — beta-100 ready 集成线 | ✅ 活跃，持续 merge | `README.md` |
| `feat/companion-code-v2` | Slash 命令 + /goal + 多 agent team + 工具 partition + prompt cache | ✅ 全套实现 + 真桌宠 E2E PASS | [plans/2026-05-25-companion-code-skill-upgrade/](../plans/2026-05-25-companion-code-skill-upgrade/) |
| `feat/fun-interactions-2026-05-31` | 12 个趣味交互（drag squash / tap burst / dizzy spin / time-of-day mood） | ✅ 已 merge 到 master (2f54960) | — |
| `fix/restore-ui-pack-2026-05-31` | UI 修复恢复（工作树 reset 丢失的 6 项） | ✅ 已 merge (fd55c9f) | — |
| `live2d-rewrite` | Live2D 渲染层重写 | 🟡 进行中 | — |
| `worktree-memory-upgrade` | 记忆系统 v2 升级 | 🟡 进行中（Stage 0/1 已合 PR #2） | [plans/2026-05-22-memory-system-upgrade/](../plans/2026-05-22-memory-system-upgrade/) |
| `feat/memory-stage2-followup-f1f2` | memory Stage 2 后续 F1/F2 + 真测挖出 F3/F4 | ✅ F1/F2/F3/F4 全修，单测全绿；**未 merge master** | [plans/2026-05-24-memory-stage2-followup.md](../plans/2026-05-24-memory-stage2-followup.md) · [F3/F4 缺陷](../plans/2026-05-31-memory-tools-flag-gating-bugs.md) |
| `feat/multi-provider-management` | 多 LLM provider 管理 | 🟡 进行中 | — |
| `tool-last-mile-upgrade` | 工具调用 last-mile（artifact + receipt + verify gate） | ✅ 已合 master（详 v3 优化） | [plans/2026-05-23-tool-last-mile-upgrade/](../plans/2026-05-23-tool-last-mile-upgrade/) |

**端口隔离**（`scripts/dev-worktree.ps1 -BackendPort N -VitePort M`）：
- master: 8100 / 5173（默认）
- 各 worktree: 8200+/5273+（手动指定，避免冲突）

---

## 3. 核心功能模块完成度

| 模块 | 状态 | 关键文档 |
|---|---|---|
| **语音管线** (VAD/ASR/LLM/TTS) | ✅ 生产可用 | `README.md` Quick Start |
| **桌宠 supervisor** (P5-S1) | ✅ 生产可用 | `README.md` §桌宠 supervisor |
| **长期记忆 + 自动总结** (P4-S20-D / memory-v2) | ✅ Stage 1/2 ship；F1-F5 全修；严测 4 Phase（33 用例）；**2026-06-02 审计修复 #1-#4**：FATAL-A 自动 backfill 兜底 + FATAL-B 静默降级告警 + MemEval 字面vs改写召回（改写 Recall@5=1.0 证 dense 真工作）+ **出厂点亮 facts_extract/enhanced_retriever/cross_key_merge 语义事实记忆栈**（真机 E2E 待跑）| `README.md` §长期记忆 + [memory-system-status](../plans/2026-05-23-memory-system-status.md) + [严测 spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) + [审计+最佳实践](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| **工具层** (registry + 权限 + 熔断 + last-mile + v3) | ✅ 生产可用 | [tool-layer-optimization-v3](../plans/2026-05-24-tool-layer-optimization-v3/) |
| **fake-completion VerifyGate** | ✅ 接电；出厂 off / dev **strict**（+9 claim patterns 含 code 场景）;strict 真机不误杀 + 单测 31/31 | [v3 §WI-T2.1](../plans/2026-05-24-tool-layer-optimization-v3/00-PRD.md) + [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) |
| **Code 模式工作流纪律** (superpowers 全套) | ✅ Layer 1A persona 3/3 E2E + plan 硬门 2/2 E2E + Layer 1B 偏好记忆 cosine 0.936 + verify strict;✅ **过夜并行(spec 3 轮 opus 评估锁定 → 子代理并行实现 → 完成度审计 11/11 ALL-COMPLETE)**：A1 意图记忆 + A2 /prefs(修 slash 结果静默丢 bug) + A4 plan 持久化三层 + A5 文档 + C2 边角 24 测 + B1 flaky closed + B3 merge companion-code-v2;单测 16 新文件全绿 + 全套 **2331 passed**;**真机 windows-mcp 待跑** | [05-LOCKED-spec](../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md) |
| **技能系统** (SkillLoader + 14 builtin) | ✅ 生产可用 | `docs/SKILLS.md` |
| **relay 登录集成** | ✅ ship | [relay-login-integration](../plans/2026-05-22-relay-login-integration/) |
| **Slash 命令 + /goal + 多 agent** (v2) | ✅ 实现 + 真测；**未 merge master** | [companion-code-skill-upgrade](../plans/2026-05-25-companion-code-skill-upgrade/) |
| **goal-completion 升级 (FP-1~FP-5 线)** | 🟡 FP-1 ✅（真机手测门 PASS）；FP-2 抗漂移 ✅（55焦点+280回归绿；R-T4 defer）；FP-3 自我纠错 ✅实现(WI-2.1~2.4+T6+R-T3+R-T6;286焦点/2459全suite绿;§7死循环上界+no_persona_leak+伪完成拦→二次通过+降级矩阵;off→shadow待go/no-go签核;手测门真产物撞写权限门同FP-2口径)；FP-4 记忆+人格 ✅实现(WI-3.1~3.4+B-10双写钩+修daily_decay从未调用bug;MemEval 491+retriever无回归;scope/pinned列)；FP-5 Skills 分级+自创 ✅后端(WI-4.0接通compaction★全回归/4.1 embedding自动披露/4.2重挂/4.3技能自创codifier不执行代码;flag off字节BC)；**5个FP后端实现全完成+R-T5字节基线守+480 goal-completion焦点测试绿**；FP-2/3/4/5真机手测门+FP-5前端确认卡批量补跑(spawn_task) | [10-EXECUTION-ROADMAP](../plans/2026-06-04-goal-completion-upgrade/10-EXECUTION-ROADMAP.md) · [FP-1/](../plans/2026-06-04-goal-completion-upgrade/FP-1/) · [FP-2/](../plans/2026-06-04-goal-completion-upgrade/FP-2/) |
| **pet animation UX** | ✅ v1 ship；v2 进行中 | [pet-animation-ux](../plans/2026-05-24-pet-animation-ux/) |
| **Live2D 重写** | 🟡 进行中 | worktree `live2d-rewrite` |
| **OSS 开源准备** | 🟡 进行中（BUSL-1.1 + SPDX + sanitize） | [oss-prep-handoff](../plans/2026-05-27-oss-prep-handoff.md) |

---

## 4. 最近里程碑（倒序）

| 日期 | 里程碑 |
|---|---|
| 2026-06-09 | **PPT 精美主线 B 路径 + 本地链接修复（代码+单测完成；B 真机 E2E 待跑·relay flaky 实锤）🟡** — 承接 [HANDOFF-2026-06-09](../plans/HANDOFF-2026-06-09.md)，用户拍板「A+B 都做」。**① 本地文件链接修复**(`cb370c6`)：LLM 写的 `[打开 PPT](C:\...\xxx.pptx)` 在 webview 打不开 → `MessageBubble.tsx` 新增 `isLocalFilePath`/`toLocalPath` 判定(盘符/UNC/file://‎/POSIX)→ 本地文件走 `invoke(artifact_open)` 系统应用打开，http(s) 保持外开;tsc 0err。**② B-1 图像 relay 健壮性 + 同步批量原语**(`c7a52d4`)：`image_tools.py` 漏接的裸 `ssl.SSLError`(UNEXPECTED_EOF) 显式归类瞬时可重试 + retry 2→4 指数退避(3/8/20) + `_save_image` 加 `time_ns()` 防批量同秒撞名 + 新增同步 `generate_images(prompts)` 原语(async worker 不返路径,PPT 拼图需确定性同步)。**③ B-2 整页生图模式**(`27c5558`)：`ppt_tools.py` 加 `image_prompt` 字段 + `ppt_create` 渲染前 `_autofill_image_prompts` 调 B-1 批量生图回填 `image_path` + 新增 `image_full` 全幅铺图布局(底部深色标题带) + `_PPT_SCHEMA` 加字段让 LLM 知道可用;dry_run/无 prompt 零网络调用、生图失败优雅降级。单测全绿(B-1 22 passed / B-2 `-k ppt` 39 passed)。**④ 真机验 B 受阻于 relay flaky**：起 app(backend 8100 + 真 BGE-M3 + relay /v1/models 200)，windows-mcp 真发「生成一张图」→ 消息收到+LLM 200，但 **relay 对 capability_gate 那次调用 504 Gateway Timeout** → 没暴露 generate_image 工具 → 只纯聊天(`tool_calls=0`)。**这是交接「relay 不稳」的实锤**(504 间歇，且整条链路都受波及，非仅图像)。relay 真出图待用户重发重试验证。**⑤ A 路径勘探**：参考 deck `.tmp/ai-education-deck.pptx`(pptxgenjs 好看版) **不能当模板**(仅 1 空 master + 1 个零占位符 'DEFAULT' 布局，全自由浮动图形)→ A-1 需真·PowerPoint 授权的 .pptx 模板(程序化难产出好模板)，待用户提供模板资产。**⑥ A-2 模板填充机制**(`4265c27`)：`ppt_create(template=.pptx)` 载模板用其 layout 加页填占位符(TITLE/BODY/PICTURE 等)、格式继承、无效优雅回退；5 测+`-k ppt` 44 绿。**⑦ A-3 模板选择 wiring**(`a515d8f`)：bundled `ppt_templates/` 目录 + 按名解析 + 动态 schema 让 LLM 发现可用模板；`-k ppt` 48 绿。**用户只需丢正规 .pptx 模板进 `backend/deskpet/tools/ppt_templates/` 即自动可用。⑧ B 双 bug 修复**(`93a695a`)：B-1 receipt 序列化炸(注入 `_image_worker` 进 args)→剔除 `_` 前缀键+default=str 兜底；B-2 工具路由(「生成图片」误走 web_fetch 网搜→images 接口 0 调用)→强化 generate_image/web_fetch 描述区分度(路由真验待 live test)；receipt/registr 141 绿。**两个 codex 并行实现(文件不重叠)。⑨ B 真机验证(windows-mcp)**：重启加载新代码后真发「生成图片」→ 日志 `name='generate_image' prompt='橘猫...'` **两次都正确调 AI 生图工具不再 web_fetch → 路由修复实测确认 ✅**。但**真出不了图**：根因 = **外部 relay `chinzy.com/v1/images/generations`(gpt-image-2) 挂起不返回**(sync 回退 4m39s 空档零 httpx 响应=请求挂起;async worker 同样卡这)。**deskpet 图像链路本身无 bug**(路由修好/worker 正常/sync 回退正常;之前怀疑的"worker not alive"是 sync 模式按设计不启动 worker,非 bug)。⑩ **快速失败调参**(`381fa82`)：relay 挂起时原 4×100s≈7min 才报失败 → 改 70s×2≈145s 快速告知(保留 ssl 瞬时重试)。**B 结论：deskpet 侧已尽;真出图卡在外部 relay 出图接口(需用户换可用图像 provider/model)。** 计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-07 | **两份手测文档全量执行 — 47/47 TC 达终态（41 PASS + 6 BLOCKED-带原因）✅** — 按 `/goal "跑完真机UI测试"` 建 PROGRESS.md 续跑机制(每条立即更新,后续 agent 可无缝接)。A组(goal-completion 22)=17 PASS+5 BLOCKED;B组(跨层bug回归 25)=24 PASS+1 BLOCKED。真机 windows-mcp 截图 PASS 11 条。**独立审计子代理终审🟢诚实可信(41 PASS 实地核验无虚标)**。6 BLOCKED 均带具体原因(LLM诚实性/verify-gate范围/压缩不触发/已知前端bug),**无新功能代码bug**。按审计补做 TC-5.2/5.7 压缩 workaround(单轮52k字)→压缩仍不fire→**新发现 FP-5 WI-4.0 压缩疑生产从不触发(潜在死链,已建调查 task_ae1af91b)** + 候选卡chat渲染bug(task_01be24af)。证据 [PROGRESS.md](../plans/manual-results-2026-06-07-full-run/PROGRESS.md) |
| 2026-06-07 | **goal-completion 真机 UI 测试 — 全 5 FP 共 9 个 TC windows-mcp PASS ✅（含 2 招牌）** — /goal "跑完真机UI测试" 续跑：HEAD(全9修复)重启，写权限门用 `permissions_auto_mode.json={enabled:true}` 放行(等价用户「本会话始终允许」)。**新增真机 PASS**：**TC-3.2 真完成放行**(agent 真写 hello-fp3.txt 16B+receipt→verify_gate 跑→无 nudge 放行,不误杀真完成)、**TC-4.1 重启跨会话召回★**(陈述"数据库用PostgreSQL/JSONB/并发"→抽取 facts(decision)→**taskkill重启清内存**→重启后准确召回 PostgreSQL+JSONB+并发,逐点一致)、**TC-4.3 偏好冲突**(乌龙→绿茶 supersede→推荐反映最新绿茶)。累计 9 真机 PASS 覆盖全 5 FP(5.3招牌/5.1/5.8/5.6/4.5/4.2/4.1★/4.3/3.2)。**抓出测试法限制(非bug)**：/goal 粘贴(Ctrl+V)不触发前端 slash 自动补全→走普通chat当任务;后端 slash 解析正常(键入触发)。剩余受限:TC-3.1(gpt-5.5太诚实拒绝伪造声明)、3.3/3.4(需slash键入法+诱导)、5.2/5.4/5.7(压缩多轮+relay间歇ReadError)。截图存 manual-results-2026-06-06-FP345/screenshots/。证据 [RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md) |
| 2026-06-06 | **goal-completion FP-5 自动披露 + FP-4 偏好注入 — 真机 PASS ✅ + 抓修 7 处系统性 fanout-gating 生产 bug**（续跑会话）— 真机跑 TC-5.1 强匹配载入 / TC-5.8 复用自创技能时，逐层定位到 FP-5 auto-disclosure（WI-4.1/4.2）虽"接线 ready"却在生产 code 会话**完全不生效**，连环抓修 **6 层跨层契约漂移**（单测全用 sync mock + 带 skills 的 config，全绿掩盖生产死链）：①SkillComponent 无观测日志 ②`assemble()` 传的 config dict 漏 skills 段→auto_enabled 恒 False ③`code` policy prefer 漏 skill→组件永不 fan-out ④code 会话靠文本分类(→chat 无 skill)→加 task_type_override="code" ⑤codify 生成的 SKILL.md 漏 task_types→select() 永远过滤掉自创技能 ⑥**SkillMatcher 同步调 async embedder.encode→拿到未await coroutine→缓存恒空→top_sim=0.0 永远零匹配**。修复后真机硬证据：`skill_auto_disclosed total=2 strong=2 auto_loaded=2 names=['meeting-minutes-to-ppt']`（自创技能正文真机自动预载进 prompt，TC-5.1/5.8 PASS）。**同根第 7 处**：`preference_profile` 组件也不在任何 policy→FP-4 偏好/画像注入(WI-3.2)生产全局死→补 chat/recall/task/code/plan/emotion policy，真机 `preference_profile_injected facts=2 task_type=code` 验证注入恢复。+2 async-embedder 回归测试守护生产契约。304+112 焦点测试绿。这是 `feedback_cross_layer_contract` 最深演绎：组件注册+flag开+单测绿，但 policy fanout 层逐个断。证据 [manual-results-2026-06-06-FP345/RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md)。**待**：TC-3.1~3.4(verify gate)/TC-4.1/4.3(跨会话召回·偏好冲突)/TC-5.2/5.4/5.7(压缩追目标/拒绝/重挂) 续跑(需 fresh context + 部分需 companion-chat venue) |
| 2026-06-06 | **goal-completion FP-5 技能自创招牌全链 — 真机 windows-mcp FULL PASS ✅ + 抓修 5 类生产 bug** — 真机模拟人工跑通完整闭环：多步任务→agent 完成全部工具→codify→propose→**前端绿色「✨新技能」卡真机渲染**→**真坐标 SendInput 点击「保存技能」**→后端 `skill_candidate_resolved cid decision=accept`→**SKILL.md 真落盘**(`<user_data>/skills/user/meeting-minutes-to-ppt/SKILL.md`,frontmatter `requires_script:false` 声明式不执行代码+author:self-codified+6步骤)。**veto-1 完全满足**(真截图+真点击+落盘证据)。**本会话抓修 5 类生产 bug**(独立子代理验收+真机诊断)：①**config 漏解析 `[skills.codify]`** → 技能自创整功能生产静默死(子代理 wiring 评审漏的 config-loader 层,boot 缺 `fp5_codify_wiring_ready` 定位)②**5 处跨层接线断裂**(services 未注册/ToolPathRecorder 从未构造+从未喂数据/build_agent 不传 skill_loader+matcher+recorder)+补 WI-1.6 record_tool→complete 喂数据 ③**relay ReadError 鲁棒性**(中转 relay 经代理掉流式连接致 agent 立即崩 → adapter 归可重试+registry 重试同 provider+流式路径掉链前干净重试,真机救活 agent 在烂代理下完成全部工具)④**前端 ephemeral-card bug**(skill_candidate 卡 message reload 时丢失 → set_messages 保留 awaiting 卡)⑤**方案 B**(codify hook 抽 helper,FinalEvent+ErrorEvent 双触发)。+TC-4.5 B-10双写钩(GC修复)+TC-5.6 trivial不弹卡真机PASS。全程 flag-OFF 字节基线退 0+156 测试绿+前端 tsc 0err,严守 veto-1 无伪造。21 commit。证据 [manual-results-2026-06-06-FP345/RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md)。**待**：TC-3.1/4.1 等其余 TC 续跑(relay 鲁棒性修复后可跑通) |
| 2026-06-05 | **goal-completion 升级 5 个 FP 后端实现全完成 ✅（FP-1 真机手测门 PASS + FP-2/3/4/5 后端全绿）** — 打通"断掉的目标线"闭环：用户说目标→【FP-1 持久化】重启仍在(真机 windows-mcp load_persisted restored=0→1 + UI /goal 查仍在,5截图)→【FP-2 抗漂移】re-anchor 决策点每5轮注入[目标锚定]+task图跨agent共享+handoff带goal+resume续目标→【FP-3 自我纠错】verify接goal_text对照(完成判定=客观证据三绿,人格禁入no_persona_leak)+结构化反思真重规划重试(§7死循环上界LLM调用≤max_iter+3)+高后果evaluator+R-T3降级矩阵+R-T6 shadow→【FP-4 记忆+人格】goal/decision/constraint facts(category-agnostic召回零改+MemEval 491无回归)+PreferenceProfileComponent(priority85/dynamic/Pin置顶/红线无谄媚)+修 daily_decay 从未被调用 bug+light写入快路+单向钩防import环→【FP-5 Skills】接通compaction到主loop(★全回归217绿,最高风险)+embedding强匹配自动载正文+压缩后重挂+技能自创codifier(只生成声明式SKILL.md不执行代码,用户确认门Future-await). **流程纪律**：先冻结§6契约/§7账本(实地核真) → 每FP superpowers sp-writing-plans 出 TDD 计划 → 子代理 TDD 实现(独立文件并行/同文件串行,子代理只实现主线统一提交防撞库) → spec 审查门 → 提交. **自查抓修真问题**：R-T5 字节门(拆 session_goals/goal_tasks 出共享_DDL,goal_mode OFF 不建表,基线退0)、I-1(mark_done落库防完成目标复活)、daily_decay 从未调度、goal_checker 失败 done=True→skipped(不假标完成). 全程 flag 默认 off 字节级 BC,5个FP后 R-T5 基线仍守. **待**：FP-2/3/4/5 真机 windows-mcp 手测门 + FP-5 前端确认卡(批量补跑,spawn_task,均后端全绿). 计划+证据 [plans/2026-06-04-goal-completion-upgrade/](../plans/2026-06-04-goal-completion-upgrade/)(FP-1~5/ + BLOCKERS.md) |
| 2026-06-05 | **v0.6.0-beta.2 发布上线 — 孤儿进程修复 + 自更新闭环 + 模型外置首启下载（真机 E2E 全 PASS ✅）** — 分支 `fix/backend-orphan-cleanup`（兼作构建/发布工作区）。**① 孤儿进程根治**：关桌宠后重开报「8100 被占用」根因=`kill_child()` 空操作（supervisor 已 take 走 Child）+ Win 不回收子进程；修法新增 `job_object.rs` 全局 Job Object(KILL_ON_JOB_CLOSE) + 主窗 Destroyed→app.exit。真机 E2E：只 `taskkill /F /PID <deskpet.exe>`（不带 /T）→ 5 个 backend 全被连带收割 + 8100 释放。**② 自更新闭环**：设置面板加「检查更新」按钮（`SettingsPanel`+`updaterError.ts`）；修 `release.yml` prerelease→false（原 endpoint 因 prerelease 永久 404）。**③ 模型外置（NSIS 化）**：`DESKPET_BUNDLE_MODELS=0` 瘦包 + CPU torch → 3.7GB 打不出 NSIS 变 **304MB**；新增 `model_provisioner.py` 首启从 **腾讯 COS** 直下 bge-m3+whisper（manifest 驱动 urllib，hf-mirror 实测与 hf_hub 0.36 不兼容故改 COS）+ 前端进度横幅；11 单测绿。**④ 签名密钥轮换**（旧口令遗失→新无口令 key 5E3B6A21）。**⑤ 发布**：本地签名构建 → 发公开 `deskpet` release(Latest) + COS；updater endpoints=COS 主 + GitHub 备，双端 200。**真机 E2E**：装 beta.2 → 首启从 COS 真下模型(37 文件落盘) + 孤儿修复复验 PASS。详 [PLAN](../plans/2026-06-05-nsis-model-externalization/PLAN.md)。**未 merge master** |
| 2026-06-04 | **goal-completion FP-1 目标持久化地基 — 真机 windows-mcp 手测门 PASS ✅** — 先冻结 §6 goal_text 契约（定稿唯一 SessionGoal schema，解决 00-PLAN §6.1 与 blueprint §2 的 done/status·criteria·subgoals·max_iterations 分歧）+ §7 重试账本（实地核真 attempt 计数=per-session 共享额度·in-memory 重启清零）。superpowers sp-writing-plans 出 10-task TDD 计划 → 子代理实现：WI-1.1 Durable Goal Store（SessionGoal 扩字段 + session_goals 表 + SessionDB 3 薄方法 + bind/persist/load_persisted/get_goal_text）+ T1 increment_iteration 落库 + R-T1 lifespan 接电 + WI-1.6 ToolPath 录制 + R-T5 flag-OFF 字节基线 + R-T7 多 worktree 隔离。**自查抓修 2 真问题**：①R-T5 字节基线 FAIL（session_goals 在共享 _DDL 被常态 ensure 建表 → 违反 flag-OFF 字节护城河）→ 拆独立 `ensure_session_goals_table` 门控；②code-review I-1（mark_done 没落库 → 完成目标重启复活）→ 加 persist_done。后端 23 焦点 + 267 回归测试全绿 + R-T5 baseline 退 0。**真机手测门**（windows-mcp SendInput 真点击 + Clipboard 真输入 + 截图）：Code 面板输 `/goal 帮我整理本周三个会议纪要FP1测试` → session_goals 落库 → `taskkill deskpet.exe` + 杀 orphan vite/backend + fresh 重启 → backend log `load_persisted restored=0→1` → 重启后 .tmp 会话恢复 + `/goal` 查询 UI 显示「当前目标：帮我整理本周三个会议纪要FP1测试」（[05 截图](../plans/manual-results-2026-06-04-FP-1/screenshots/05-goal-status-restored.png)）。详 [FP-1/02-manual-test.md](../plans/2026-06-04-goal-completion-upgrade/FP-1/02-manual-test.md) |
| 2026-06-04 | **工具层「极其严格」手工测试 — windows-mcp 真机 40/40 全 PASS ✅** — 按 `testcase/tool-layer-RIGOROUS-manual-test.md`(40 TC)逐 case 真机测：windows-mcp SendInput 真点击 + 剪贴板真输入 + CDP9333 定位/验证 + backend 行为日志三重证据。覆盖全部工具 + 中间件横切(权限门按category上色：截图证 write_file橙/shell红/desktop_write橙 + 熔断3次OPEN + last-mile ArtifactCard多产物 + receipt+HMAC+duration_ms非0 + verify gate off/shadow/strict)+ 6个config重启TC(disabled_toolsets双层门控★回归/dangerous_allowlist/default_timeout设计陷阱/strict_unknown_toolset潜在bug/artifact_envelope ON-OFF/verify shadow)+ 健壮性(优雅失败/越界写OS拦/大输出截断/并发tile隔离不串台)+ 已修bug回归(非法permission_category)。**执行期发现并修复 2 个真 bug**：①doc_create 嵌套 element 格式渲染成字面 dict 字符串(`backend/deskpet/tools/doc_tools.py:_add_element`)②金黄 hint 卡因 last-mile envelope 嵌套不触发(`tauri-app/src/code-panel/MessageBubble.tsx:splitToolError`);均修+复测通过。建可复用 harness(testcase/_cdp.py + _send.py + _approve.py + deskpet-input.ps1 SendInput圣杯)。详 [tool-layer-RESULTS.md](../testcase/tool-layer-RESULTS.md)。回归单测已补(doc_tools 2 + splitToolError 3,全绿)+ 已提交 commit 4e8f449。 |
| 2026-06-04 | **语音对话同步到消息框修复 — 真机 A/B 闭环 PASS ✅** — bug：桌宠主窗口语音不进「消息·主线程」（语音落库但消息框看不到、重开也不补）。**一次根因**：`VoicePipeline` 只 audio_ws point-to-point、从不广播；修复复用文字同款 `_broadcast_default_chat_peers` 发 chat_v2_user_echo/final、skip originator（主窗口已 audio 显示不重复）。**真机暴露二次根因**：`VoicePipeline.control_ws` 是 audio 连接期快照，backend respawn 后 audio 常先于 control 重连 → 快照=None → 旧守卫 `... and self.control_ws and ...` 挡掉广播（落盘诊断实锤 `control_ws_none=true`）。**二次修复**：守卫去掉 control_ws 依赖 + main.py audio_channel 注入实时解析 originator 的闭包（`_control_connections.get(session_id)`，对齐文字路径的实时 `_ws`）。**TDD 16 单测全绿**（含 `test_broadcasts_regardless_of_control_ws_snapshot` 根因守护：control_ws=None 仍广播）；architect 子代理逐行审计 P0/P1 → GO-with-changes；诊断证 fan-out `control_keys` 含 `message-panel-main`、`originator_in_values=true`。**真机 A/B**：修复前真人语音停在 id2152 不进消息框（[11 截图](../plans/manual-results-2026-06-03-voice-msgpanel/screenshots/11-msgpanel-full.png)）；修复后真人语音「提醒买菜」(id2157/2158) **实时进消息框**（用户截图确认）。详 [fix-spec v2](../plans/2026-06-03-voice-msgpanel-sync/00-fix-spec.md) |
| 2026-06-04 | **多屏跨 DPI 拖动 + Live2D 角色渲染修复（master 直提）** — 用户实测桌宠在双屏（Samsung 主屏 dpr 2.13 + Xiaomi 副屏 dpr 1.42，webview dpr 异常比显示器 scale 高 ~1.42×）：①拖不回小屏（抖动+弹回）②拖几次只显示一半 ③拖到小屏角色右半被裁。**7 处根因**：`window_geometry.rs` 移除 on_resize clamp（跨 DPI 振荡）+ `pin_size`（逻辑尺寸跨屏舍入漂移 375→360→657）；`App.tsx` 边缘吸附 250ms 防抖 + 显示器局部坐标转换（非主屏 pickEdge 误判甩回主屏）；`Live2DCanvas.tsx` 角色列宽 cap 在视口内 + dpr 纳入 size 状态/matchMedia 触发画布重渲 + `modelReady` 触发异步模型加载后重渲 + scale 用基础尺寸（避免读已缩放 model.width 致模型爆炸）+ **删除重复 resize effect**（旧版拖动时覆盖修好结果）。真机 SendInput 拖动 + 白板背景截图验证：三星/小米 boot + 拖动后角色均从头到脚完整居中；10 次跨屏尺寸 pin 360×600 零漂移。详 [手测报告](../plans/manual-results-2026-06-03-multimon-drag/REPORT.md) |
| 2026-06-02 | **记忆系统审计 + 修复 #1-#4（master 直提，4 commit）**：3 路交叉验证（我的代码审计 + silent-failure-hunter 子代理 + 最佳实践调研子代理）。**#1 FATAL-A**(`4b700dd`)：lifespan 从不调 backfill_missing → 任何 embedding 缺口永久无声（"刚说的话下次不记得"）→ 加启动自动 backfill 兜底 + 修正虚假注释。**#2 FATAL-B**(`30cbcdf`)：检索/嵌入静默降级只 log.debug → retriever FTS / facts embed / enhanced_retriever 降级点升 warning（vector_worker 已 warning 故不动）。**#3 MemEval**(`56c9c59`/`48d8549`)：~18 双语"字面vs改写"召回对照，真 BGE-M3 改写 Recall@5=**1.0**（证 dense 语义召回真工作、非吃 FTS 字面红利）+ 修模型路径脆弱性（用户迁 F 盘后 C: 硬编码致 model_required 整批 ERROR → 新增 resolver）。**#4**(`4d8de40`)：冲突消解机制（mem0 merge/supersede + Zep 软失效 + 时序链）早已建好且 37 测试全绿，用户决策出厂点亮 facts_extract+enhanced_retriever+cross_key_merge（dataclass 默认仍 False 保字节契约；**真机 E2E 待跑**）。详 [审计+最佳实践报告](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| 2026-06-02 | **superpowers ③ verify_gate strict + code claim patterns — 真机不误杀 + 单测 9/9 PASS**：决策3"硬卡"。claim_patterns.yaml 补 4 条 code 场景(已创建/已修改/测试通过 + en)5→9;dev 翻 `verify_gate_mode=strict`(出厂仍 off)。关键判断:registry.execute_tool 对每个工具都 emit_receipt → 真调过的任务 claim 命中放行、裸声明(fake)拦。真机:STRICT_CHECK.md 任务 write_file→无 verify_gate_nudge→放行完成(未误杀);单测证 fake 无 receipt→拦、未来时→不误判、shadow→不拦。出厂是否翻 shadow 待定。详 [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) |
| 2026-06-02 | **superpowers Layer 1B 偏好记忆(BGE-M3) — 真机 E2E + 单测 7/7 PASS**：决策2 的"记下来后续相同直接做"。新组件 `preference_memory.py`（计划/意图两类 + BGE-M3 cosine + JSON 持久化 + list/clear）。计划记忆接 plan-confirm 门:用户点[执行]→record approved;相似任务 match 命中→自动确认跳过等待。真机:Task A(PREF_ALPHA)走门点[执行]记录→Task B(PREF_BETA 只改文件名)`plan_confirm_auto_approved score=0.936`无 awaiting 直接跑→文件创建。接线踩坑:ServiceContext register 有 allowlist(加字段+白名单)。flag 默认 OFF 出厂不构造。意图记忆(决策1)组件已支持待接线。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1b-preference-memory.md) |
| 2026-06-02 | **superpowers Layer ① plan-confirm 硬门 — GO+CANCEL 真机 E2E 2/2 PASS**：决策2 严格版——code 模式明确任务先出 plan + 等用户点[执行]再跑 ReAct。复用现成 `maybe_extract_plan`（plan.py 自标的 "future enhancement"），加确认门(后台 task await Future 不阻塞 recv loop，最小改动)+ `plan_confirm` WS + 前端 [执行]/[取消] 按钮。**调试挖出真问题**:grid tile 预览(SessionGridView)自己的 renderer 过滤掉了 "plan" 角色 → tile 内单独渲染确认栏修复。CDP 真机:GO→暂停(零 dispatch)→点[执行]→todo+list+write+read 执行→GATE_OK.md 建成;CANCEL→点[取消]→零执行+文件不创建。flag 默认 OFF 出厂字节级不变。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-plan-confirm-gate.md) |
| 2026-06-02 | **superpowers 工作流集成 code 模式 — Layer 1A 落地 + 真机 E2E 3/3 PASS**：用户反馈 auto 模式"问个问题就埋头乱改、不澄清、不验证就说完成"。调研发现根因是 code 模式纯 ReAct 无工作流骨架 + persona 通篇"优先使用工具"。**纠正一处诊断错误**：verify_gate/goal_checker 并非"孤儿代码"，agent_loop 早接好了，卡点是配置 flag 默认 off（本项目反复出现的模式）。**Layer 1A**：重写 `_CODE_MODE_PERSONA_TEMPLATE`（意图门→澄清→计划→执行→验证）+ dev 翻 `verify_gate_mode=shadow`+`emit_receipts`+`goal_mode`。**真机 CDP E2E**（test-research-helper tile，deepseek-v4-pro，全栈非注入）：TC-1 问模型→直答"deepseek-v4-pro"零工具(治#1)；TC-2 模糊派活→先澄清目标/范围/成功标准、零文件改动(治#2/#3)；TC-3 明确任务→todo拆步骤+写前看现状+**写后read_file读回验证**+报告校验结果(治#5)。决策：偏好记忆走 BGE-M3。Layer 1B(偏好记忆)+ shadow→strict 待做。详 [proposal](../plans/2026-06-02-superpowers-code-workflow/proposal.md) + [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1a.md) |
| 2026-06-01 | **工具层全功能手测（按 testcase 真桌宠 E2E）+ 修复 2 个真 bug**：A 类 5 例 CDP 真注入桌宠 WebView2（PPT/Excel/Word/web_fetch/能力门控，4 PASS + windows-mcp 真操作原生保存对话框）；B 类配置契约核对。**挖出并当日修复 2 bug**：① `doc_create` 生成空文档（element 格式契约 `{heading}` vs `{type,text}` 不匹配 → `doc_tools.py` 加归一）② `_load_tools` 漏读 `disabled_toolsets`/`dangerous_tools_allowlist`/`default_timeout_seconds` 等 WI-T5.1 字段（`config.py` 补读）→ 该 3 功能此前配了不生效。真桌宠闭环复测 disabled 生效（excel_create 0 调用，LLM 绕道）；pytest 59 passed。新增 testcase/ 手测体系。详 [tool-layer-test-report](../plans/manual-results-2026-06-01/tool-layer-test-report.md) |
| 2026-06-01 | **记忆系统严测（4 Phase 全收 / G1-G6 + 性能基线，33 新用例 master 直提）**：真机 GUI 终验推翻草率 PASS，挖出并**全修 F5**（facts.search/workspace.recall/find_by_entities 的 `LIKE '%整串%'` → 自然语言 query 永不命中）：① 分词 OR LIKE（`text_tokenize.py`）② memory_search 向量优先（真 BGE-M3）。G5 戳破 eval_gate hit@5 字面驱动（mock==real Δ=0）。G6 钉死 embedding 列真写入 + 写入并发不变量。性能基线：检索热路径 N=500 median 1-3.5ms（护栏非微基准）。CI 跑真 embedder。详 [memory-system-rigorous-test-spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) §8 |
| 2026-05-31 | memory Stage2 followup F1/F2 完成 + 真机 GUI 真测挖出并修复 F3（memory_search 误连坐 forget flag）/F4（code 工作记忆出厂默认开，保字节级契约）；单测全绿，未 merge |
| 2026-05-31 | companion-code v2（slash/goal/team/partition/cache）全套 + 真桌宠 WebView2 E2E PASS；fun-ux 12 交互 merge；dev-worktree.ps1 跑源码修复 |
| 2026-05-27 | OSS 开源准备（LICENSE / SPDX / 凭据脱敏 / CI 适配） |
| 2026-05-24 | 工具层优化 v3（VerifyGate 接电 + stubs 真实现 + ToolsConfig 扩展）；pet-animation UX |
| 2026-05-23 | 工具 last-mile 升级；memory-v2 Stage 2 |
| 2026-05-22 | beta-100 内测就绪；relay 登录集成；builtin skills |

---

## 5. 已知问题 / 测试纪律

- **dev 模式必须用 `scripts/dev-worktree.ps1`**（worktree）或 `dev-start.ps1`（主树）启动 —
  直接 `npm run tauri dev` 会用 stale 打包 exe（旧版本，缺新 endpoint）。详见脚本注释。
- **手工测试纪律**（CLAUDE.md HARD CONSTRAINT）：UI 改动必须 windows-mcp / CDP 真测，
  不能用单测 / 协议层替代。真桌宠 WebView2 测试用 CDP 9222（dev 默认开）注入真实输入。
- **DPI 坐标**：这台开发机 OS scale 150% + WebView dpr 2.13；SendInput 物理点击需正确
  换算（详 [16-sendinput-webview2-final-diagnosis](../plans/2026-05-25-companion-code-skill-upgrade/16-sendinput-webview2-final-diagnosis.md)）。
- 其它已知问题见 `README.md` §已知问题（Known Issues）+ `docs/beta/已知问题.md`。

---

## 6. 文档索引

- **架构 / 模块文档**: [`docs/INDEX.md`](../docs/INDEX.md)
- **手工测试用例索引**: [`testcase/index.md`](../testcase/index.md)
- **README**（用户 + 开发者入口）: [`README.md`](../README.md)
- **项目级开发笔记**: [`CLAUDE.md`](../CLAUDE.md)
- **迭代 plan 目录**: `plans/2026-*`（每个迭代一个文件夹，含 PRD/TDD/manual-test/report）
- **OSS 准备**: [`plans/2026-05-27-oss-prep-handoff.md`](../plans/2026-05-27-oss-prep-handoff.md)

---

## 7. 如何更新本文件（HARD 纪律）

> **铁律**：任何任务一旦"通过测试完成"（pytest/vitest/cargo/手工 E2E 全绿），
> **必须在同一次交付内**同步更新本文件 —— "跑过测试但没更新 STATUS" = 任务未完成。
> 详见 [`CLAUDE.md` §STATUS 更新纪律](../CLAUDE.md)。

完成以下任一事件后更新：
1. 一个 WI / slice / 功能模块跑通验收 → 更新 §3（🟡 → ✅ 或新增行）
2. 里程碑级完成 → 追加一行到 §4 最近里程碑
3. 一个 worktree 合并到 master → 更新 §2 表格状态
4. 发现新的项目级已知问题 / 测试纪律 → 更新 §5

每次更新都改顶部"最后更新"日期。保持一页能看完（细节放各 plan 文档，这里只给状态 + 链接）。
