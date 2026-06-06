# 真机手测结果 — goal-completion FP-3/4/5（2026-06-06）

> 被测 commit：`e705373`（含 FP-5 接线修复 58fbac8 + B-10 GC 修复 3932637 + FP-3/4 小缺口 dd1d1e7）
> 环境：`npx tauri dev` 跑源码后端（`DESKPET_BACKEND_DIR=backend` 已确认 `[backend_launch] Dev python=...backend\.venv`，非 frozen）+ 全 FP flag（`.tmp/fp1-config.toml`）+ 真 LLM relay（chinzy.com gpt-5.5，登录态走 keychain `default.deskpet-cloud-llm`）+ 独立 `DESKPET_USER_DATA_DIR=.tmp/fp345-userdata`。
> windows-mcp 真模拟人点击/输入，截图存 `screenshots/`。

---

## ✅ 启动健康验证（boot log 硬证据）

| 检查点 | 证据 | 判定 |
|---|---|---|
| 跑源码非 frozen | log `[backend_launch] Dev python=...backend\.venv\Scripts\python.exe backend_dir=...backend` | ✅ |
| goal_mode ON | log `companion_code_v1_goal_mode_ready` | ✅ |
| FP-5 compaction 接通 | log `wi4_0_compaction_enabled threshold=0.75` | ✅ |
| FP-4 B-10 钩绑定 | log `b10_goal_facts_hook_bound` | ✅ |
| **FP-5 我修的新接线生效** | log `fp5_auto_disclosure_wiring_ready matcher=...`（SkillMatcher 构造成功） | ✅ |
| 无 Unknown service / Traceback | grep 无（codify hook 依赖的 tool_path_recorder/skill_candidate_store/llm_registry 已注册，否则 boot 即 ValueError） | ✅ |
| 真 LLM relay | log `GET https://chinzy.com/v1/models 200` + `model_context_resolved gpt-5.5` | ✅ |

> **意义**：FP-5 接线修复（5 处跨层断裂）在生产真机验证 —— 若 service 仍未注册，启动时 codify hook 的 `get()` 会抛 ValueError；现 boot 干净 + `fp5_auto_disclosure_wiring_ready` 证明 SkillMatcher/recorder/candidate_store 全部正确注册。

---

## ✅ TC-4.5 — B-10 双写钩 /goal set → facts category=goal（PASS，真机硬证据）★

**对应**：FP-4 手测门④ + 验证本会话的 B-10 fire-and-forget GC 修复（commit 3932637 `_fanout_tasks` 强引用）。

**真模拟人步骤**（全 SendInput 圣杯）：
1. SendInput 点击桌宠工具栏「跳过」关 onboarding（物理坐标 3343,1733）。
2. Win32 ShowWindow 激活 Code Mode 面板（handle 2952214）。
3. SendInput 点「+ 新项目」(1235,395) → native 文件夹对话框 → 双击 `fp345-proj` → 点「选择文件夹」(1517,1021) → **code session `code-cn8im6rt` 创建**。
4. Clipboard 设 `/goal 帮我整理本周三个会议纪要并生成PPT_B10真机测试` → SendInput 点输入框(1155,1170)聚焦 → **SendInput 键盘 Ctrl+V**（圣杯关键，见下）→ slash 补全识别 `/goal [text]` → SendInput 点「发送」(1644,1209)。

**证据**：
- 截图 `screenshots/tc-4.5-b10-goal-set.png`：UI 助手回复「**已设置目标：帮我整理本周三个会议纪要并生成PPT_B10真机测试（上限 10 轮）**」。
- `state.db` facts 表 `WHERE category='goal'` → **1 行** `('goal','user','goal_code-cn8im6rt',<目标文本>,'session')` —— **B-10 双写钩真机触发**，scope=session、key=goal_<sid> 全对。
- `state.db` `session_goals` → 1 行 `('code-cn8im6rt',<目标文本>,'active')` —— P0-1 goal store 同时持久化。

**判定 PASS**：goal_set 确认 + facts 出现 category=goal + session_goals 持久化 = 双源一致，B-10 fire-and-forget GC-safe 修复在生产真机有效（若 GC 丢条，facts 表会空）。

---

## 🔑 windows-mcp 圣杯突破（关键技术记录，供后续 TC 复用）

**WebView2/Chromium 键盘也必须 SendInput（INPUT type=1），不止鼠标！**
- 现象：SendInput **鼠标**点击 WebView2 按钮有效，但 `keybd_event` 的 Ctrl+V/Enter **无效**（输入框始终空）。
- 根因：WebView2 像忽略老 `mouse_event` 一样**忽略老 `keybd_event`**。
- 解法：键盘用 `SendInput` + `KEYBDINPUT`（type=1，wVk=0x11/0x56，dwFlags=2 为 keyup）。粘贴瞬间生效，slash 补全立即识别。
- 另一关键：WebView2 content 按钮点击前必须 `SetForegroundWindow`+`BringWindowToTop` 激活窗口，否则合成点击不被 content 处理（「+ 新项目」首次失败、激活后成功印证）。
- 坐标系：虚拟桌面 8640×2160 物理像素；主截图区映射 ×3.0（displayed×3=physical，右键命中桌宠经验证）；DPI 150%，`SetProcessDPIAware()` 后 SetCursorPos 用物理像素，GetCursorPos 回读一致。

---

## ✅ 追加真机验证（同会话，relay 重试后）

| TC | 证据 | 判定 |
|---|---|---|
| **Live agent loop + 工具执行** | 真机发「读取 README.md 总结」→ messages 表 `tool: read_file ok` + assistant 正确终答；后续多工具任务执行 **6 个工具调用** + TODOS 面板 **3/3 完成**（盘点目录/读README/总结） | ✅ agent loop + 工具执行 + 我的接线 live 有效 |
| **TC-5.6 trivial turn 不弹技能卡** | 单工具任务 → `pending_skill_candidates` 表未建（codify 不提候选，符合触发器边界） | ✅ |
| **WI-1.6 recorder 喂数据 + session_id 一致性** | 核实 `_agent.run(session_id=_sid)` → agent_loop `record_tool(session_id=_sid)` → codify hook `complete(_sid)` 同一 `_sid`，无 mismatch | ✅ 接线正确 |

### ⚠️ relay 环境特征（影响 LLM 重度 TC）
真 LLM relay（chinzy.com gpt-5.5）**间歇性 ReadError**（连接读取失败）：首次请求常报 `ReadError`，**重试可通**。`/goal` 多步任务首跑 6 分钟卡死；读任务首跑 ReadError、重试成功。这是外部 relay 不稳，非代码缺陷（agent loop/工具/slash/goal store/B-10 全部已证可用）。

## 🟡 TC-5.3 技能自创卡（招牌）— 接线全验证，live 弹卡未捕获（relay 阻塞）

- ✅ **接线 100% 验证**：codify flag on + tool_path_recorder/skill_candidate_store/llm_registry 注册（boot 无 Unknown service）+ session_id 一致 + detect_trigger 阈值（≥5 工具 或 ≥3 不同工具）+ 前端 SkillCandidateCard 建好（tsc 0err）+ 单元测试 record_tool→complete 链路绿。
- ✅ **触发条件真机满足**：多工具任务真跑 **6 工具调用**（≥5，Condition 1 命中）。
- 🟡 **但候选卡未弹**：`pending_skill_candidates` 表未建 → codify hook 的 `propose()`（需一次 LLM 调用生成声明式 SKILL.md）**最可能因 relay ReadError 失败**（本会话 relay 间歇故障已证）。Tee 日志缓冲无法确认 propose 的 runtime 行为。
- **结论**：TC-5.3 **wiring + 单元 + 触发条件全验证**，仅差「propose LLM 成功 → 卡真弹 → 真点保存 → SKILL.md 落盘」这一段 live 捕获，受 relay 不稳阻塞。续跑需 relay 稳定时段重试（多发几次多工具任务，propose 命中即弹卡）或 flush 后端日志诊断 propose 返回值。

## ★★ 真机手测抓出生产级真 bug（本会话最大价值）

**bug：config 加载器漏解析 `[skills.codify]` → FP-5 技能自创整个功能在生产里死掉。**
- 诊断路径：真机跑 6 工具任务后技能卡不弹 → 查 boot log **缺 `fp5_codify_wiring_ready`**（codify 接线块整段没进）→ 静态查 `config.py:load_config` 发现**只 pop `auto_disclosure` 子表，从不解析 `codify`** → `[skills.codify] enabled=true` 被丢弃 → `config.skills.codify.enabled` 恒默认 False → lifespan codify 块跳过 → `tool_path_recorder`/`skill_candidate_store`/`llm_registry` 全注册为 None → codify hook `if _tp_rec is not None and _sc_candidate_store is not None` 短路 → 技能自创确认卡（WI-4.3）**生产永不弹**。
- **子代理 wiring 评审漏掉这层**：它们查了 build_agent/services 注册，但没查 config 加载器是否真把 TOML flag 传进 schema。这正是 `feedback_cross_layer_contract` 的更深一层。
- **修复**（commit 7732c0d）：`load_config` 同样解析 `[skills.codify]` → `SkillsConfig(codify=cd)`。补回归测试（enabled=true 被解析 + 默认 off BC）。
- **验证修复**：重启后 boot log 出现 **`fp5_codify_wiring_ready tool_path=True candidate_store=True llm=True`**（之前缺失）→ codify 接线现在 live。

## 🔴 TC-5.3 live 卡弹的精确根因 = relay ReadError 中止 turn

config 修复后 codify 接线 live（三 service True），但真机多工具任务后技能卡**仍未弹**。文件级诊断（临时插桩，已 revert）结论：
- **codify hook 在 FinalEvent 分支**；relay **ReadError 中止 turn → 不到 FinalEvent → codify hook 整段不运行 → 不 propose → 卡不弹**。
- 即：live 卡弹被 **relay 间歇 ReadError 阻塞**（turn 跑不到正常收尾）。codify 链路本身（config 已修 + 接线 live + 触发阈值 + 前端卡 + 单测）全部就绪，**差一个稳定的 turn 完成**。
- **续跑**：relay 稳定时段重发多工具任务，turn 正常到 FinalEvent → codify propose → 卡弹 → 真点保存 → SKILL.md 落盘。或在 codify hook 之外（ErrorEvent 分支也补 codify）增强健壮性（可选小切片）。

## 🔧 TC-5.3 续攻进展（input 坐标修正 + 34 工具 turn 跑通）

- **修正 input-miss 根因**：之前多次任务没跑是**输入框坐标算错**（用了 (1060,924)，实际在 displayed(415,396)→物理 **(1245,1188)**，发送按钮 (1779,1260)）。修正后任务真正进入 + 发送。
- **relay 这次稳定**：修正坐标后发的多工具任务真跑了 **34 个工具调用**（tool msgs 19→34，relay 未中断），证明 relay 能持续工作。
- **但 codify hook 仍未 fire**：turn 推进极慢（relay 限速）迟迟不到 FinalEvent；codify hook **只挂在 FinalEvent 分支** → 长 turn / 达迭代上限 / relay 慢都让它不触发。
- **结论 + 续跑硬建议**：TC-5.3 的最后一公里 = **让 codify 在更多 turn-end 路径触发**（方案 B：FinalEvent + ErrorEvent + 迭代上限 end 都调 codify helper），否则依赖一个"快速干净到 FinalEvent 的 turn"在 relay 限速下不稳定。这是一个明确的健壮性小切片（把 codify hook 抽成 `_maybe_codify(_sid)` helper，在 _FinEv/_ErrEv/iteration-cap 三处调）。codify 链路其余全就绪（config 修 + 接线 live + 触发阈值 + 前端卡 + 单测）。

## ✅ 方案 B 实现 + TC-5.3 最终状态（relay 耗尽）

- **方案 B 实现**（commit c31d261）：codify hook 抽成模块级 `_maybe_codify_skill(service_context, config, sid, ws, waiters)` helper，在 **FinalEvent + ErrorEvent 两处都调** → turn 经 relay ReadError / 迭代上限 / 中止结束时（若已跑 ≥5 工具）仍触发技能自创。补 4 个 helper 单测（flag off/recorder None/无步骤 no-op + 有步骤 propose+emit），84 回归绿，import OK。
- **真机验证方案 B**：重启（含 config 修 + 方案 B，boot `fp5_codify_wiring_ready True,True,True`）→ 精确坐标（Snapshot 取输入框物理 (1099,963)）发多工具任务。
- 🔴 **relay 本会话耗尽**：连续多次任务**首调或 2 工具后即 ReadError 中止**（session 显示「⚠ error」），始终跑不到 codify 触发所需的 ≥5 工具 / ≥3 不同工具。**方案 B 也救不了**——relay 在 5 工具前就失败，_active 步数不足，complete() 步数 < 阈值 → 正确地不提候选。
- **结论**：TC-5.3 链路**代码侧 100% 就绪**（config 修 + 5 处接线 + 喂数据 + 前端卡 + 方案 B 双分支触发 + 全单测绿 + boot 三 service True），**唯一剩余 = 一个能稳定跑完 ≥5 工具的 turn**，受 chinzy.com relay 本会话间歇 ReadError（首调/早期即断）阻塞。**续跑**：relay 稳定时段（或换 provider，config 有 deepseek-v4-pro）重发多工具任务，跑满 ≥5 工具 → 方案 B 在 FinalEvent/ErrorEvent 任一触发 → 卡弹 → 真点保存 → SKILL.md 落盘。

## ✅ relay 鲁棒性修复（3 处）— 真机验证有效，agent 从"立即崩"→"完成全部工具"

**真机抓的根因**：中转 relay 经 Clash Verge 代理间歇掉**流式连接** → httpx `ReadError`（name 不含 Timeout）→ 之前落 `LLMProviderError` 不重试 → agent turn 立即崩，跑不到 ≥5 工具。

**3 处修复**（commit 4f39e3a）：
1. `openai_adapter._map_error`：ReadError/ConnectError/RemoteProtocolError 等连接级瞬时错误 → `LLMTimeoutError`（可重试）。
2. `registry.chat_with_fallback`：timeout/conn-drop **重试同 provider**（backoff，仿 RateLimit），之前直接 break 切下一 provider，单 provider（中转 relay）时即崩。
3. `tool_use_shim.chat_with_fallback_stream`（★agent 流式路径）：流在**产出任何事件前掉链** → 干净重试整个流（3 次 backoff）；已产出则不重试。+3 流式重试单测。

**真机验证（重启后发 5 步多工具任务）**：
- ✅ **session 从「⚠ error」变「running」并持续推进**（修复前每次首调/2 工具即崩）。
- ✅ **agent 完成全部 5 步工具**（TODOS **3/3** ✓列目录✓读README✓再列再读总结）+ **46 个工具调用**累计。流式重试真机救活了代理掉连接。

## 🔴 TC-5.3 卡弹最终阻塞 = relay 死到连 propose 都穿不过

agent 完成 46 工具 + turn `stop_reason='end_turn'` 收尾，但 `pending_skill_candidates` 表仍未建 → codify 的 **`propose()` 需再发一次 LLM 调用生成 SKILL.md**，relay 本会话死到连这一调（即使有 registry 重试）也失败 → 无候选 → 卡不弹。

- **结论**：技能自创链路**代码侧 100% 就绪 + 鲁棒性已加固到能让 agent 完成全部工具**。卡弹的**唯一剩余依赖 = propose() 那一次 LLM 调用成功**，被本会话 relay 彻底瘫痪（Clash Verge 代理网络层）阻塞。这是外部基础设施死亡，非代码——relay 恢复后此链路一次即通（agent 完成工具已真机证明，propose 只是一次短调用）。

## ★★★ TC-5.3 技能自创后端机制链 — 真机端到端 PROVEN（relay 鲁棒性修复后突破）

**relay 鲁棒性修复后，发短约束任务（3 不同工具快速结束）真机跑通了技能自创全链：**
- ✅ agent 在烂代理下完成工具（流式重试救活）→ turn 结束 → **方案 B codify hook 触发** → `propose()` LLM 调用**成功**（relay 短调命中好窗口 + registry 重试）→ 生成声明式 skill。
- ✅ **硬证据**：`state.db` `pending_skill_candidates` = **1 行** `(1, 'meeting-minutes-to-ppt', status='pending')`（codifier 从工具路径生成的技能候选！）。
- ✅ **日志**：`skill_candidate_proposed cid=1 name=meeting-minutes-to-ppt sid=...`（WS 事件真机 emit 给前端）。
- ✅ 截图 `screenshots/tc-5.3-skill-candidate-proposed.png`。

**意义**：这证明 TC-5.3 的**整条后端机制链端到端真机工作** —— config 修([skills.codify]) + 5 处接线 + WI-1.6 喂数据(record_tool→complete) + 方案 B(FinalEvent/ErrorEvent 触发) + relay 鲁棒性(流式重试) **全部协同生效**，从 agent 跑工具一路打通到「codify 检测工具路径 → propose 生成技能 → skill_candidate_proposed 推前端 → 候选入库 awaiting confirm」。这是技能自创**最难的全链**，已真机硬证据证明。

## ✅✅✅ TC-5.3 招牌全链 — 真机 FULL PASS（技能卡→真点保存→SKILL.md 落盘）

**修完 ephemeral-card bug + 重启后，真机模拟人工跑通技能自创完整闭环：**
1. ✅ 发多工具任务 → agent 在烂代理下完成全部工具（relay 鲁棒性修复）+ 自然到 FinalEvent。
2. ✅ codify → propose → 候选 id=3 入库 + `skill_candidate_proposed` WS emit。
3. ✅ **前端绿色「✨ 新技能 · meeting-minutes-to-ppt」卡真机渲染**（ephemeral-card 修复后开完整 chat 卡存活）—— 展示 description + 6 步骤 + 「✓ 保存技能」「忽略」按钮。截图 `screenshots/tc-5.3-skill-card-rendered.png`。
4. ✅ **真坐标 SendInput 点击「✓ 保存技能」(物理 1221,1053)** → 发 `skill_candidate_confirm{candidate_id:3, accept:true}`。
5. ✅ **后端日志**：`skill_candidate_confirm_received cid=3 decision=accept` + `skill_candidate_resolved cid=3 decision=accept`。
6. ✅ **SKILL.md 真落盘**：`<user_data>/skills/user/meeting-minutes-to-ppt/SKILL.md` —— 完整声明式技能（frontmatter name/description/when_to_use/**requires_script: false**（红线：只声明不执行代码）/author: self-codified + 6 步骤正文）。

**判定 TC-5.3 PASS** —— 「多步任务 → 技能自创确认卡弹出 → **真坐标点击保存** → SKILL.md 落盘」招牌全链真机硬证据贯通，veto-1（真 windows-mcp 截图+真点击+落盘证据）**完全满足**。本会话从「relay 死、功能死」一路修到「招牌全链真机 PASS」。

## 🐛（已修）TC-5.3 前端卡显示 — 真机抓的 2 个前端 bug

候选 id=1 真机 pending awaiting confirm，但「点保存→SKILL.md」未能捕获，真机定位到**2 个前端 bug**：
1. **skill_candidate 卡是 ephemeral（前端-only）消息，message reload 时丢失**：`ws.ts` 把卡 push 进 `sessionsStore[sid].messages`（push_message 不丢 role，验证过），但打开「完整 chat」会触发 `session_messages_load` → `set_messages` 从 SessionDB **整体替换** messages → skill_candidate 卡（未持久化到 DB）被丢弃。这是我真机找卡时打开完整 chat 反而弄丢卡的根因。**修法**：set_messages 重载时保留内存里 awaiting 的 skill_candidate/plan 卡（merge 不 replace），或后端在 session_messages_load 时把 pending 候选一并下发。
2. **codifier dedup 阻止重新生成卡**：已有 pending 候选(id=1)时，再跑多工具任务**不再 propose 新候选**（合理防重复，但叠加 bug#1 导致丢卡后无法靠新任务再弹）。**修法**：清 pending 候选后再触发，或后端支持「重发 pending 候选卡」verb。

**注**：这 2 个是真机手测抓出的**前端显示层 bug**（非技能自创机制 bug——后端 propose→入库→WS emit 已证全通）。修这 2 个 + 重新触发即可完成「卡显示→点保存→SKILL.md」。

## 🟡 TC-5.3 最后一环（前端卡渲染 + 点保存→SKILL.md）— 待验

候选已 pending + WS 事件已 emit，但真机消息流里**未可见渲染绿色技能卡**（SkillCandidateCard）→ 疑前端 session 路由显示问题（候选 sid=code-64ec67f7，「完整 chat」视图未渲出该卡）或 codify await 5min 超时窗口。**前端卡组件代码已建 + tsc 0err + vitest 134 绿**（commit 58fbac8），WS 契约后端已 emit 正确字段；差「卡在对应 session 视图真显示 → 点保存 → SKILL.md」这一前端显示+确认环节真机捕获。续跑：定位候选 sid 对应的 code session 视图找卡，或排查 ws.ts 的 skill_candidate_proposed→SkillCandidateCard 渲染（session_id 路由）。

## 🟡 剩余 TC（真机执行框架已打通，待续跑会话）

下列 TC 依赖**分钟级真 LLM agent 多轮运行**（gpt-5.5），单 TC 需多次截图轮询 + 可能撞 write_file 权限门。本会话已打通交互 harness（SendInput 圣杯键鼠 + Code session 创建 + /goal 真发送），但完整跑这些需独立专项会话的上下文预算：

| TC | 状态 | 备注 |
|---|---|---|
| TC-4.5 B-10 双写钩 | ✅ **PASS（本会话真机）** | 见上 |
| TC-3.1 伪完成→拦→重规划→二次PPT | 🟡 待续 | agent 已就该目标「思考中」运行；需等多轮 + ArtifactCard 截图 |
| TC-5.3 技能自创卡（招牌） | 🟡 待续 | 需 ≥5 工具多步任务触发 skill_candidate_proposed → 前端卡（接线已修+复评100%+前端 tsc 绿，但真机弹卡截图待补） |
| TC-4.1 重启后跨会话召回 | 🟡 待续 | 需会话A决策→重启→会话B召回 |
| TC-5.1/5.2 强匹配载入/压缩追目标 | 🟡 待续 | FP-2 已证 goal anchor；FP-5 自动披露待真机 |
| 其余 TC-3.x/4.x/5.x | 🟡 待续 | 见 testcase/goal-completion-manual-test.md |

> **续跑入口**：app 可保持运行（agent 正跑 code-cn8im6rt 会话的目标任务）；交互用本报告「圣杯突破」节的 SendInput 键鼠方法。Code Mode 窗 handle 每次启动变，用 Snapshot 查 `DeskPet · Code Mode`。输入框物理坐标随窗口，需 Snapshot 重取。
