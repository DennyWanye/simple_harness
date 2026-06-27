# 测试阶段全量点亮已开发能力 — 生产上线手工测试（自包含·单文档可执行）

> **被测功能**：「测试阶段全量点亮」(2026-06-27)。把**已开发完成但出厂默认 `False`** 的 A 表能力一次性翻 `True`（不灰度）。
> 改动三处：① `backend/config.py` dataclass 默认 `False→True`（33 个 flag）；② 仓库根 `config.toml` 出厂种子清掉显式 `false` + 补段；③ `_MIGRATABLE_SECTIONS` 追加 `("features",)`/`("skills",)`/嵌套 skills 段（新默认回灌存量 config）。
> **点亮的能力**：`[memory.v2]` 语义事实记忆栈（facts_extract/rerank/enhanced_retriever/chunking/query_rewrite/reflection/cross_key_merge/memory_forget/entity_path/episodic_to_semantic/feedback_loop/goal_facts/light_write/persona_inject/goal_facts_hook/curation_nudge/auto_learnings）+ `[features]`（slash_commands/goal_mode/agent_parallel/plan_confirm_gate/preference_memory/subagent_driver/agent_team/subagent_nonblocking + 压缩四件套 ctx_observability/adaptive_compact_pct/summary_quality_loop/microcompact_size_aware）+ `[skills]`（knowledge_enabled/auto_disclosure/codify）+ `[tools.verifier].external_evaluator`。
> **B 表（dataclass 默认仍 False，本次未翻）**：`forget.enable_natural_language`(危险无护栏)/`run_build`/`run_tests`(code)/`plan_read_only`(归 WI-1.2)/artifact 信封 4 个(未实装)。
> ⚠️ **两个真相源别混淆**：`config.py` dataclass 默认 与 仓库根 `config.toml` 出厂种子是**两套独立真相**，运行期 effective = 种子值覆盖 dataclass 默认（缺键才落默认）。
> - `workspace_memory`：dataclass 默认仍 `False`（本次未翻，归 code），但**出厂 config.toml 种子 F4(2026-05-31) 早已 `true`** → 运行期 **ON**（非本次改动范围；本测不专门验它，但 IDEM-D「[memory.v2] 无残留 false」需把它算作 true 的合法项，别误判）。
> - `pref_decay`：dataclass 本就 `True`（早于本次），不在本次 33 个翻转清单里，但运行期 ON。
> - 本测口径：**只验本次从 False→True 的 A 表 flag**（下表 §1）；workspace_memory/pref_decay 作为"已 ON 的背景项"不专测、但不得在 IDEM-D 被误判为残留 false。
>
> **本文档定位**：**生产上线验收（production ship gate）—— 通过本文档 = 可上生产环境。**
> 单文档自包含：功能正确性（§3 TC）+「遍历 + 写副作用」幂等性（§4 副作用地图 + §5 IDEM）一份跑完即出上线判定。
>
> **对应改动**：`backend/config.py`（dataclass 默认 + `_MIGRATABLE_SECTIONS`）· 仓库根 `config.toml` · [HANDOFF](../../plans/2026-06-26-agent-harness-alignment/HANDOFF-enable-flags.md) · [00-PLAN §WI-0.0](../../plans/2026-06-26-agent-harness-alignment/00-PLAN.md)
> **最后更新**：2026-06-27

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 环境（必须先满足）

1. **进程清场**：`taskkill /F /IM deskpet.exe` + 杀残留 Vite node（坑 #1）。**不要手动起 backend / vite**（坑 #7/#9，Tauri 自管唯一 backend + 唯一 vite）。
2. **跑源码（非 frozen）**：只给 **Tauri 进程**注入 env（坑 #8），用 `plans/manual-results-2026-06-27-enable-flags/launch-dev.ps1`：
   - `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`
   - `DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`
   - `DESKPET_DEV_MODE=1` · `DESKPET_USER_DATA_DIR=G:\projects\deskpet\backend\userdata`
   - `DESKPET_CLOUD_API_KEY=<根目录 .env 的 tsk_ key>`（relay LLM 链路；见 [`LOCAL-DEV-CREDENTIALS.md`](../../LOCAL-DEV-CREDENTIALS.md)）
   - `NO_PROXY=*`（坑：Clash 7897 掐空闲长连，长耗时 LLM 误判挂起，见 [[project_clash_proxy_kills_idle_connections]]）
   启动后 log **必须**出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`；
   若见 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **日志落盘**：tauri dev 输出 `*>` 重定向到 `plans/manual-results-2026-06-27-enable-flags/tauri-dev.log`（backend structlog 走 stderr → Tauri pipe → 落进这份，坑 #7）。**UTF-16LE**，用 `loggrep.py` 解码 grep。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

> 触发词已命中（"用 windows-mcp 测试" / "真测"）。完整版见 `~/.claude/knowledge-base/windows-mcp-e2e.md`。

1. **真模拟人**：每个功能 case 必 Screenshot/Snapshot → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 日志判 PASS/FAIL。
2. **禁绕过（HARD）**：**不允许**用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`/`import` backend 查 registry、keychain/文件存在性 **替代**真点击作为**功能能力**证据。
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-27-enable-flags/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**：`Clipboard set <中文>` → Click 输入框聚焦 → `ctrl+a` → `ctrl+v` → 点「发送」按钮（见 [[project_deskpet_chat_e2e_harness]]）。**不要在用例间点「新话题」**（会触发空发污染计数）。
7. **例外（合法的非-UI 证据）**：§5 的 **IDEM-A/C/D 是 config 迁移机制幂等性**测试，无 UI 形态 → 用 **config.toml 内容 diff + 重启 boot log** 判定是合法且唯一的手段（不是拿 log 替代 UI 证明用户功能，而是这个机制本身就只有文件+日志两个观测面）。功能能力（TC-1~7）仍走真 UI。

### 0.3 ★ 上线一票否决项（任一 FAIL = 不可上线）

| Case | 验收点 | 类别 |
|---|---|---|
| **TC-1** | goal_mode 真生效：明确指示后桌宠真调 `goal_task_create` 建目标 + 拆任务（log `p5s2_tool_call_args_dump ... name='goal_task_create'` ≥1） | 功能(核心能力) |
| **TC-4(a)** | facts_extract flag 真接电（记忆栈生效最低门，确定性、不受 relay 502 影响）：boot `p4_vector_worker_ready` 含 `facts_extract=True` + `memory_tools.bind:` 含 `facts_store=FactsStore`/`llm=True` | 功能(核心能力·语义记忆栈) |
| **TC-7** | 全 flag ON 启动**零崩溃**：无 `ConfigError`/`VG-INVARIANT`、无"启动失败"弹窗，`Uvicorn running on ...:<DESKPET_BACKEND_PORT,主树默认8100>` | 功能(BC/稳定) |
| **IDEM-A** | 回灌幂等：backend 重启**第 2 次完全不再改写** config.toml（`feature_flag_merge_applied` **不出现** + 内容 hash 不变 + **mtime 不变**），用户自定义值/注释保留 | 幂等(遍历+写副作用核心) |
| **IDEM-C** | config.toml 显式 `false` **不被回灌覆盖**（backfill 只补缺失键、绝不覆盖已存在值）= 用户单条关闭逃生口有效 | 幂等(BC 安全) |
| **IDEM-D** | fresh install（无 config.toml）经 `seed_user_config_if_missing` 种子后 A 表 flag **全 ON**（走的是 `seed copyfile` 整段复制路径，与回灌不同；种子任一 flag 漏翻/拼错 = 全新用户拿到暗装能力）| 幂等/出厂(核心生产场景) |

---

## 1. 被测能力清单（A 表 = 应全部 ON）

> **flag 数对账（33）**：`[memory.v2]` **17**（含 `memory_forget`——PLAN WI-0.1 文案写"16"系漏计 memory_forget）+ `[features]` **12**（slash_commands/goal_mode/agent_parallel/plan_confirm_gate/preference_memory/subagent_driver/agent_team/subagent_nonblocking + 压缩四件套）+ `[skills]` **3**（knowledge_enabled + auto_disclosure.enabled + codify.enabled）+ `[tools.verifier]` **1**（external_evaluator）= **33**。`workspace_memory`(本次未翻)、`pref_decay`(本就 True) **不计入**。

| 段 | flag | 运行期可观测证据（log / UI） |
|---|---|---|
| `[features]` | goal_mode | boot `goal_task_tools_registered_global count=4`；运行期 `name='goal_task_create'` |
| `[features]` | slash_commands | 前端 `/` 命令路由（InputBar）|
| `[features]` | agent_parallel / subagent_driver / agent_team / subagent_nonblocking | boot 子代理调度装配 |
| `[features]` | 压缩四件套 ctx_observability/adaptive_compact_pct/summary_quality_loop/microcompact_size_aware | 长会话压缩可观测 |
| `[skills]` | knowledge_enabled / auto_disclosure | 真回合 `skill_auto_disclosed total=N` |
| `[skills]` | codify | boot `fp5_codify_wiring_ready` |
| `[memory.v2]` | facts_extract | boot `p4_vector_worker_ready` 行含 `facts_extract=True` + `memory_tools.bind:` 行含 `facts_store=FactsStore`/`llm=True`（成功抽取**不打日志**；失败才打 `FactExtractor.extract LLM failed`，见 TC-4 陷阱）|
| `[memory.v2]` | persona_inject | 真回合 `preference_profile_injected facts=N` |
| `[memory.v2]` | curation_nudge / auto_learnings | 真回合 `oh4_curation_nudge turn=N` |
| `[tools.verifier]` | external_evaluator | 高后果目标异体评分（边角能力，**无独立 boot 锚点日志**）→ 本测不专测，仅以 **TC-7 零崩溃**覆盖（开它不致启动报错）；如需专测须触发高后果目标场景，超本测范围 |

---

## 2. 总判定

- 全部 ★ 一票否决项（TC-1 / TC-4(a) / TC-7 / IDEM-A / IDEM-C / IDEM-D）PASS **且** §3 功能 TC 无 FAIL **且** §5 IDEM 无 FAIL → **DECISION: SHIP**（可上生产）。
- 任一 ★ FAIL → **DECISION: HOLD**，修复后整条重跑。
- **非★核心能力的约束力（防糊弄）**：TC-2(b) 技能强命中自动加载、TC-3 persona 注入、TC-4(b) facts 落库、IDEM-B 去重——这些虽因依赖 relay/语料未列★，但**若在 relay 健康下反复失败（非 502 env-limited）= HOLD 待查，不得标 PASS 糊弄**。仅当确属 relay `json_schema` 502 瞬态（≥3 retry 实证）才可标 env-limited 跳过。

---

## 3. 功能正确性 TC（windows-mcp 真测，逐条执行）

> 复用同一「DeskPet · 消息」窗会话（不点「新话题」）。每条：declare → 真输入 → 截图 → log grep 判定。
>
> **参考坐标（2026-06-27 实测，screenshot scale=3.0，image 像素×3=屏幕坐标；每次重启位置会漂，先 Screenshot 核对）**：
> - 桌宠窗「消息」按钮（开独立消息窗）≈ 屏幕 `(2709, 960)`。
> - 独立消息窗：输入框 ≈ `(2226, 1215)`，「发送」按钮 ≈ `(2520, 1215)`，聊天区滚动锚 ≈ `(2310, 950)`。
> - 输入法：`Clipboard set <中文>` → Click 输入框 → `ctrl+a` → `ctrl+v` → Click 发送。
> - **注意**：「default」会话历史很长，滚到最新回复不可靠 → **判定优先用 log grep**（截图作辅助/存档），别强求 UI 滚动读到底。

### TC-1 ★ goal_mode 真建目标 + 拆任务

1. **动作**：在消息窗输入框（剪贴板中文法）发送：
   `请用你的目标任务功能，正式新建一个目标「一周内读完一本书」，并拆成3个子任务真正登记进去。`
   - declare：`坐标=输入框(x,y)/发送(x,y) | 动作=粘贴+点发送 | 期望=桌宠调 goal_task_create`
2. **等待**：桌宠「思考中/工具执行中」→ 回复完成（composer 回「空闲」）。
3. **判定（log）**：
   - **主证据（这是★的判据）**：本回合段必有 `name='goal_task_create'`（≥1 次，args 含子任务 title）。**精确锚点**：该串由 `event='p5s2_tool_call_args_dump' level='info'` 行携带（INFO 级，默认日志级别即打，无需开 DEBUG），形如 `p5s2_tool_call_args_dump ... idx=0 name='goal_task_create' args_len=.. args_preview='{"title":"..."}'`。
   - **辅助锚点**：boot 段 `goal_task_tools_registered_global count=4`（注意 `count=4` 是源码写死的**字面量**字符串、非真实注册数，仅作"goal_mode 接线走到了"的辅助证据，**工具真注册成功以运行期真调为准**）+ `companion_code_v1_goal_mode_ready`。
   - **失败信号**：若 boot 段出现 `goal_task_tools_register_failed` → 注册失败，**FAIL**。
   - 加分：`wi4a_goal_anchor_always_on tid=...`、`goal_checker_nudge_injected iter=N/10`（末轮 goal_checker 自动续推）。
4. **预期结果**：✅ log 出现 ≥1 次 `p5s2_tool_call_args_dump ... name='goal_task_create'`（args 为每日子任务标题），桌宠 UI 回复确认已建目标并列出子任务。
   ❌ 若仅对话式拆解、log 无 `name='goal_task_create'` → 用更明确指示（如直白要求"用目标功能登记进去/拆成N个子任务"）重试（≥3 次不同措辞）再判。**注意**：自然语言"帮我设个目标…"易被 LLM 用对话式拆解回答而不调工具，需较明确指示。

### TC-2 knowledge / auto_disclosure 语义披露触发

> 源码日志格式（`assembler/components/skill.py`）：`skill_auto_disclosed total=%d strong=%d auto_loaded=%d names=%s top_sim=%.3f`（字段顺序：total→strong→auto_loaded→names→top_sim）。

1. **动作 (a) 机制活**：任意真消息（TC-1 那条即可）。
2. **判定 (a)**：本回合出现 `skill_auto_disclosed total=N`（N>0，= 披露器对该消息真跑了语义匹配）。
3. **动作 (b) 强匹配硬判据**：发一条**故意强命中内置技能**的消息，如：`帮我做一个产品介绍的 PPT，要封面和三页内容，配色专业一点。`（强命中 ppt 技能）。
4. **判定 (b)**：该回合 `skill_auto_disclosed` 行 `strong>=1` 且 `auto_loaded>=1` 且 `names=[...]` 非空（含 ppt 类技能名）。
5. **预期结果**：
   - ✅ (a) `total>0` → auto_disclosure 机制活（非★）。
   - ✅ (b) PPT 类强命中消息 `strong>=1 auto_loaded>=1 names` 非空 → **自动加载核心能力真生效**（这才是 knowledge/auto_disclosure 的价值，不能只验"披露器空转"）。
   - ⚠️ 若 (b) 反复 `strong=0`（换 ≥3 条明确技能词仍不强命中）→ 记为披露阈值/语料问题，标注待查，不当 PASS 糊弄。

### TC-3 persona_inject 人格画像注入

> ⚠️ **陷阱**：`preference_profile_injected` 日志**仅当注入条数 ≥1 时才打**（源码 `assembler/components/preference_profile.py`：`if not rows: return ...status=empty` **早返回、不打该行**）。所以 **facts 库为空的全新会话该行缺失是正常的**，**不能**按"每回合必现"判 FAIL。

1. **动作**：任意真对话回合（TC-1/TC-2 任一即可触发）。前置：facts 库**非空**（已有历史事实；可先确认 `backend/userdata/data/` 下 facts 表行数 ≥1，或本测在已积累事实的 dev userdata 下跑）。
2. **判定（log）**：
   - facts 库非空时：真回合出现 `preference_profile_injected facts=N task_type=...`（N≥1）。
   - facts 库为空时：该行**不出现**属正常（组件走 `status=empty` 早返回）→ 改查 facts 表行数=0 佐证"机制在、只是无事实可注"，**不判 FAIL**。
3. **预期结果**：✅ facts 库非空时每个真回合注入 N≥1 条历史事实（召回/注入路径端到端活）；facts 库为空时不强求该日志。

### TC-4 facts_extract 写入端抽取

> ⚠️ **关键陷阱**：`FactExtractor.extract` **成功路径不打任何日志**（源码 `deskpet/memory/facts.py` 唯一含该串的是 `log.warning("FactExtractor.extract LLM failed: ...")`，**只在失败时打**）。所以**绝不能**把 grep 到 `FactExtractor.extract` 当成功证据——抓到它恰恰说明**抽取失败**。flag 生效用 boot 接线证据判，抽取真发生用 facts 增长判。

1. **动作**：发送身份事实陈述：`请记住：我叫小王，是一名后端程序员，最爱用的编辑器是 neovim，主力语言 Rust。`
2. **判定（分两层）**：
   - **(a) flag 生效（确定性 boot 证据）**：boot 段 grep `p4_vector_worker_ready` 行含 `facts_extract=True`；且 grep `memory_tools.bind:` 行（**注意有冒号**，格式 `memory_tools.bind: facts_store=%s embedder=%s llm=%s NL=%s`）含 `facts_store=FactsStore` 与 `llm=True`（两字段**不相邻**，中间隔 `embedder=`，别写成连续串）。
   - **(b) 抽取真发生（relay 健康时）**：发陈述前 count facts 表行数=C0，发后等抽取完成再 count=C1，**C1 > C0**（新事实落库；用 facts 表行数判，**不用** `preference_profile_injected` 日志——N=0 时它不打，见 TC-3 陷阱）。
3. **预期结果**：
   - ✅ (a) 必过：boot 两条接线证据齐 → facts_extract flag 生效。
   - ✅ (b) relay 健康时 facts 数增长 → 抽取端到端 PASS。
   - ⚠️ 若 log 出现 `FactExtractor.extract LLM failed: ... 502 Bad Gateway`（或空错误体）→ **抽取失败**，根因 **relay 对 `json_schema` 结构化输出间歇 502**（已知 relay 瞬态，见 [[project_pipeline_preanalysis_blockers]]），**非本能力缺陷**；重试 ≥3 次仍 502 → (b) 标「env-limited(relay)」跳过、不算 FAIL；(a) 仍判 PASS（flag 接线在）。**不得把这条失败日志当"抽取器真跑=PASS"**。

### TC-5 curation_nudge 记忆自策展

1. **动作**：在**同一 backend 进程内（中途不 taskkill/不重启）**连续发送 ≥2 个真对话回合（TC-1~4 已满足）。**注意**：turn 计数器是进程内单例，重启归零 → 若中途重启过，turn 号会错位，本 case 须在不重启窗口内连发。
2. **判定（log）**：出现 `oh4_curation_nudge sid=%s turn=N decisions=%d remembered=%d`（**判完整串**，别只 grep `oh4_curation_nudge` 前缀——会误匹配 failure 路径的 `oh4_curation_nudge_failed`）。N 为 `curation_nudge_every_n_turns` 的倍数。
   - **注意 every_n 来源**：dev 真机用的 `every_n=2` 来自**仓库根/dev `config.toml` 种子**（`curation_nudge_every_n_turns = 2`），**不是** dataclass 默认（默认是 **8**，见 `config.py` `MemoryV2Config`）。所以 dev 跑应在 turn=2/4/6… 触发；若在"无 config.toml 纯 AppConfig 默认"的极端环境跑则是 8 的倍数——本测在 §0.1 dev userdata 下，按 2 判。
3. **预期结果**：✅ 每 2 回合触发一次 `oh4_curation_nudge`（自策展 nudge 按频率门控触发）。

### TC-6 codify 自创闭环装配

1. **动作**：检查 boot 段日志。
2. **判定（log）**：`fp5_codify_wiring_ready` + `fp5_auto_disclosure_wiring_ready` + `fp5_skill_matcher_prewarmed cached=N`。
3. **预期结果**：✅ 三条接线日志出现（codify + auto_disclosure flag 生效、技能预热）。

### TC-7 ★ 全 flag ON 零崩溃启动

1. **动作**：观察 0.1 的启动过程 + boot 日志。
2. **判定（log）**：
   - **无** `ConfigError` / `VG-INVARIANT-{0,1,5,6}` / `feature_flag_merge_parse_failed` 致命错误。
   - **无**桌宠「启动失败」弹窗（截图确认主界面正常出现、`已连接`）。
   - `Uvicorn running on http://127.0.0.1:8100`。
3. **预期结果**：✅ 全 33 flag ON 下 backend 正常启动、桌宠主界面正常、WS `已连接`，无任何不变式报错。

---

## 4. 副作用地图（「遍历 + 写副作用」逐段过 — 上线前必答）

> 对每段「遍历 + 写副作用」代码逐条回答 reviewer 五问。

| 代码段（遍历 + 写副作用） | ① 重复调用/重进会重复副作用吗 | ② 有「已处理」判断吗 | ③ 持久化了吗（重启认得） | ④ 失败可重试吗（不误杀） | ⑤ 幂等测试 |
|---|---|---|---|---|---|
| **`_merge_missing_feature_flags()`**（`config.py:861`）— 遍历 `_MIGRATABLE_SECTIONS` 把缺失 flag 写进用户 `config.toml`（**本次新增 `("features",)`/`("skills",)`/嵌套 skills 段**）| **否**：只补**缺失键**，已存在键/整段直接 `continue`（`if key in user_tbl: continue`，`config.py:910`）| **有**：`key in user_tbl` / `_dig_table` 整段存在判断 | **是**：写进 `config.toml` 落盘；二次启动读到键已存在 → 不再写（`added` 为空 → `return False` 不落盘）| **是**：tomlkit 缺失/解析/写失败 → `log.warning + return False` **不抛**、下次启动重试；写前 `.pre-migrate-bak` 备份 | `test_config_feature_flag_backfill.py`（幂等 + 二次不重写 + 用户值保留 + 注释存活 + 无 tomlkit 优雅降级）→ **§5 IDEM-A 真机复核** |
| **`.pre-migrate-bak` 备份覆盖**（`_merge_missing_feature_flags` 内 `shutil.copyfile(user_target, bak)`）— 写盘前覆盖备份 | **否**（仅当 `added` 非空才执行）| **有**：依赖回灌幂等——幂等成立则第 2 次无 added → 不再覆盖备份 → 用户原始 config 不丢 | 备份文件落盘 | 写失败前 try 内、不抛 | IDEM-A 间接守（幂等破=每次覆盖备份=次生数据丢失，故 IDEM-A 同时是备份安全门）|
| **`seed_user_config_if_missing` 整段 copyfile**（`config.py`，fresh install 从仓库根 `config.toml` 种子整份复制）| **否**：仅当 `not user_target.is_file()` 才种子；文件已存在直接跳过（不重复种子）| **有**：`user_target.is_file()` 判断 | **是**：种子写盘后存在 → 后续启动跳过 | 无源/复制失败 → 上层 log + 走默认 | §5 **IDEM-D**（种子后 A 表全 ON）|
| **relay provider 收编重写 `[[llm.endpoints]]`**（`relay_provider_ops`/`provider_registry`，`relay_provider_ensured`，**config.toml 第二个写者**，2026-06-27 IDEM-A 真测发现）| **会**：每次启动 atomic-replace 重写 providers 段（即使内容不变也换 mtime）| 弱：内容幂等（相同 provider → 相同字节）但每 boot 都重写文件 | **是**：写 config.toml | 写失败不致命 | 非本次改动；IDEM-A 已识别其只换 mtime 不改 backfill 键（hash 不变）。**对本次 flag 点亮无副作用交互**（不碰 `[memory.v2]`/`[features]`/`[skills]` 段）|
| **`memory_write` 工具 → FactsStore**（用户说"记住X"时 agent 调，写 facts 表）| **原会**：历史用 `key=memory_<时间戳>` 每次唯一 → 重复写堆行（IDEM-B 真测发现）。**2026-06-27 已修**：改内容哈希 `_stable_memory_key` + `find_active` 命中即 `update_value` touch | **有(修后)**：内容哈希 key + find_active 去重 | **是**：facts.db 落盘 | upsert 失败 → ok:false 不崩 | `test_g3_6_write_same_text_twice_is_idempotent`（同文本两次行数不增）+ §5 IDEM-B 真机 |
| **`facts_extract`(FactExtractor) → FactsStore**（后台结构化抽取，写 facts 表）| **部分**：LLM 把既有 facts 喂回让其判合并；同 (subject,key) 大多 supersede 合并，但 **LLM 抽取/消解非确定**（同内容两次抽出的 category/key 略不同 → 偶有跨 category 重复，如 employee_id 落 fact+profile）| 弱：LLM 驱动冲突消解（非确定）| **是**：facts.db | 抽取 LLM 失败 → 本条不落、下回合重抽 | IDEM-B 真机（修 memory_write 后残留 +3 来自此路 → 另议，非本次 flag/bug）|
| **`goal_task_create`**（建 goal task 行）| **会**：每次调用建新 task（无内建去重）→ 重复请求会建重复任务 | 弱：无「同标题已存在」去重 | **是**：goal_store 持久化（`goal_store_bound_persistence`）| 失败本条不建、可重发 | §5 IDEM-C-note（记录为已知行为，非阻断）|
| **`curation_nudge` → 写 learning**（每 N 回合）| **否**：`MemoryCurator` 单例 `bump_turn` 计数器 + `every_n` 门控，每周期至多 1 次 fire-and-forget | **有**：单例 turn 计数器（跨 loop 重建持续累加，非每回合归零）| 计数器在进程内单例（重启归零=干净起跑）| nudge 失败 fire-and-forget 不卡死 | `test_oh4_curation_wiring.py::test_counter_persists_across_loop_rebuild` |

**结论**：唯一真正「写持久副作用 + 跨重启」的是 **`_merge_missing_feature_flags` 写 config.toml** → 必须 IDEM-A 真机验幂等（★一票否决）。facts/curation 有去重/计数门控；goal_task_create 无去重但属用户显式请求语义、记为已知非阻断行为。

---

## 5. 幂等 IDEM 用例

### IDEM-A ★ 回灌幂等：backend 重启第 2 次不再改写 config.toml

> 验「遍历 _MIGRATABLE_SECTIONS + 写 config.toml」的核心幂等性。无 UI 形态 → config 内容 diff + boot log 判定（见 §0.2 第 7 条例外）。

1. **造存量 config（剥 flag）**：备份当前 `backend/userdata/config.toml`；从中删掉**新增**的几个键（如 `[features]` 的 `ctx_observability`/`adaptive_compact_pct` + `[skills]` 整段），模拟「补丁前的存量用户 config」。记其内容 hash（`sha256`）。
   - declare：`动作=编辑 config.toml 删指定键 | 期望=造出缺键存量 config`
2. **第 1 次启动**：跑 `launch-dev.ps1` → grep boot log：
   - **预期**：`feature_flag_merge_applied count=N keys=[...features.ctx_observability.../skills...] backup=...pre-migrate-bak`（把缺的键补回）。
   - config.toml 内容 hash **变化**（补了键），且原有用户自定义值（如 `curation_nudge_every_n_turns=2`）+ 中文注释**保留**。
3. **记录补后状态**：记 `config.toml` 补后内容 `sha256` **和 `mtime`（精确到 ns，PowerShell `(Get-Item ...).LastWriteTime.Ticks`）**。
4. **第 2 次启动**（taskkill + 重跑 launch-dev.ps1）→ grep boot log + 比对文件：
   - **主判（backfill 幂等）**：`feature_flag_merge_applied` 事件**完全不出现**（源码 `_merge_missing_feature_flags` 在无缺失键时 `not added` → 直接 `return False`、**根本不打该日志、不写盘**，所以**不存在 `count=0` 这个形态**；出现该事件即 FAIL）。
   - **主判（内容幂等）**：config.toml 内容 `sha256` **与补后完全相同**（backfill 的键零改写）。
   - **mtime 注意（2026-06-27 真测发现）**：`mtime` 在**全栈启动**下**会变**——但**不是 backfill 干的**：`relay provider 收编`（`relay_provider_ensured` → `provider_registry` 每次启动重写 `[[llm.endpoints]]` 段，内容相同 atomic-replace → 换 mtime）是 config.toml 的**第二个写者**（见 §4 新增行）。故 mtime 变化**仅当**能在同次 boot log 找到 `relay_provider_ensured` 时可接受（= relay 写的、非 backfill）；若 mtime 变**且** `feature_flag_merge_applied` 出现 → 才是 backfill 幂等破坏。单测 `test_idempotent_second_run_no_change` 的 `st_mtime_ns` 断言成立是因它**隔离跑** backfill（无 relay 写者），全栈下需用 hash + merge-event 两个主判，mtime 仅作旁证。
5. **预期结果**：✅ backfill 副作用（merge 写 config + 覆盖 `.pre-migrate-bak`）只在第 1 次发生；第 2 次「已处理」（键已存在）被识别 → `feature_flag_merge_applied` 不出现 + 内容 hash 不变；持久化（config.toml）跨重启认得；用户值/注释保留。
   ❌ 若第 2 次出现 `feature_flag_merge_applied`、或内容 hash 变 → backfill 幂等破坏，HOLD。（mtime 单独变、hash 不变、且有 `relay_provider_ensured` → 是 relay 第二写者，PASS）

### IDEM-B facts 抽取去重（同一事实发 2 次 facts 数不翻倍）

> baseline **用 facts 表行数直接 count，不用 `preference_profile_injected` 日志**（N=0 时该日志不打，无 baseline 可比，见 BLOCKING-A）。facts 表在 `backend/userdata/data/`（SQLite，memory.db 的 facts 表）；可用 `python` 一行 `SELECT count(*) FROM facts`（这是**度量手段**，非"拿脚本当 UI 证据"——本 case 验的是去重副作用次数，本就只有 DB 行数这一个观测面）。

1. **记 baseline**：count facts 表行数 = C0。
2. **动作**：同一会话发送同一身份陈述 2 次（间隔等抽取完成）：`我叫小王，是后端程序员，喜欢 neovim。`
3. **判定**：第 1 次后行数 = C1（净增 ΔC=C1−C0）；第 2 次后行数 = C2。
4. **预期结果**：✅ `C2 − C1 < ΔC`（理想 C2≈C1，cross_key_merge/upsert 合并同一事实、不重复落行）→ 同一事实重复发不翻倍。
   ⚠️ relay `json_schema` 502 致两次抽取都失败（C0=C1=C2 不增）→ 本 case env-limited（同 TC-4），标注后跳过判定、不算 FAIL（无抽取成功则无从验去重）。

### IDEM-C ★ config.toml 显式 false 不被回灌覆盖（用户逃生口）

> 探针**故意选 `goal_mode`**：它有确定性 boot 锚点 `goal_task_tools_registered_global`（开/关一眼可判），且不在 `_validate_flag_invariants` 的硬连锁里。**勿用 `[tools.verifier]` 系 flag**（`verify_gate_mode`/`emit_receipts`/`ephemeral_subagent_model` 有 VG-INVARIANT 连锁，改它们会改变失败语义、甚至 ConfigError 拒启动，污染本探针）。

1. **动作**：在 `backend/userdata/config.toml` `[features]` 显式设 `goal_mode = false`（造「用户主动关闭单条」），记 hash。
2. **第 1 次启动** → grep boot log + 检查 effective 值：
   - **预期**：boot 段 `goal_task_tools_registered_global` **不出现**（goal_mode 实际 OFF）；`feature_flag_merge_applied` **不含 goal_mode**（已存在键不被补/不被覆盖）。
3. **复原**：把该行删掉（恢复落 dataclass 默认）→ 重启 → `goal_task_tools_registered_global count=4` 回来。
4. **预期结果**：✅ 用户显式 `false` 被尊重（backfill「只补缺失、绝不覆盖已存在值」）；删行后默认 True 生效。= 测试阶段单条 kill-switch 逃生口有效。

### IDEM-D ★ fresh install 全 ON（出厂种子点亮）

> ⚠️ **操作性硬提醒**：§0.1 的 `launch-dev.ps1` 把 `DESKPET_USER_DATA_DIR=...\backend\userdata` **写死**。本 case 必须**覆盖**它指向一个**新建空目录**，否则种到已有 config.toml 的主 userdata → seed 直接跳过（`seed_user_config_if_missing` 仅当 `not user_target.is_file()` 才种）→ **测了个寂寞**。seed 源 `_bundle_default_config_path()` dev 分支返回 `backend/../config.toml`（仓库根），与 userdata 目录无关，所以隔离目录不影响种子源。

1. **造隔离空目录**：新建 `G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\idemD-userdata`，确认其下**无** `config.toml`。
2. **动作**：改启动命令把 `DESKPET_USER_DATA_DIR` 指向该空目录（其余 env 同 §0.1）启动 → 触发 `seed_user_config_if_missing` 从仓库根 `config.toml` 种子。
   - declare：`动作=改 DESKPET_USER_DATA_DIR=<空目录> 启动 | 期望=seed 整段 copyfile 种子`
3. **判定**：
   - 该空目录下新生成 `config.toml`；其 `[memory.v2]`/`[features]`/`[skills]` A 表 flag **全 `true`**（`[memory.v2]` 顶层 bool 无任何 `false`——注意 `workspace_memory=true` 合法，`forget.enable_natural_language` 在 `[memory.v2.forget]` 子表、不算顶层残留）。
   - boot log 各能力接线全在（`goal_task_tools_registered_global`/`fp5_*`/`p4_vector_worker_ready facts_extract=True`）。
4. **预期结果**：✅ 全新安装出厂即点亮（无需用户手动开）；种子任一 A 表 flag 漏翻/拼错都会在此暴露。
5. **收尾**：测完**删除** `idemD-userdata` 目录，恢复 §0.1 主 userdata 启动，**不要复用**该隔离目录继续后续测试。

---

## 6. 判定汇总表（执行时填）

| Case | 类别 | 预期 | 实测 | 截图 / log 证据 | PASS/FAIL |
|---|---|---|---|---|---|
| TC-1 ★ | goal_mode | name='goal_task_create' 真调 | | | |
| TC-2(a) | auto_disclosure 机制活 | skill_auto_disclosed total>0 | | | |
| TC-2(b) | 技能强命中自动加载 | strong>=1 auto_loaded>=1 | | | |
| TC-3 | persona_inject | facts 非空回合 injected facts>=1 | | | |
| TC-4(a) ★ | facts_extract 接电 | boot facts_extract=True+bind | | | |
| TC-4(b) | facts 落库(relay 健康) | facts 表行数增长 | | | |
| TC-5 | curation_nudge | oh4_curation_nudge turn=N(完整串) | | | |
| TC-6 | codify | fp5_codify_wiring_ready | | | |
| TC-7 ★ | 零崩溃启动 | 无 ConfigError + Uvicorn up | | | |
| IDEM-A ★ | 回灌幂等 | 第2次不改写(hash+mtime) | | | |
| IDEM-B | facts 去重 | 不翻倍 | | | |
| IDEM-C ★ | 显式 false 不覆盖 | 用户值被尊重 | | | |
| IDEM-D ★ | fresh install 全 ON | 出厂点亮 | | | |

**最终判定**：`DECISION: SHIP / HOLD`（填）
