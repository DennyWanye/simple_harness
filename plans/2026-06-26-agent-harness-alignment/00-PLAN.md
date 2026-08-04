# 00 — DeskPet × Claude Code / Codex / Hermes 对齐优化 PLAN

> **目标**：把三大 agent harness（**Claude Code / Codex / Hermes Agent**）相对 DeskPet 的优势，
> 选择性吸收，把**桌宠 Companion 主线程优化成"最强能力 + 最高效率"的版本**，**代码级落地**。
> **底座**：[10-竞品调研](./10-competitor-research.md) · [20-缺口分析（代码核实）](./20-gap-analysis.md) · `STATUS/status.md`（已核实 ~97% 最新）。
> **状态**：**v1.2 — EXECUTABLE-AS-IS**（3 轮子代理对抗审查 + 2 轮用户反馈重构）。**除 WI-0.0 外仅规划**；**WI-0.0 已在 `config.py` working tree 落地**（测试阶段全量点亮已实施，uncommitted），其余 WI 暂不执行。
> **v1.2 测试阶段定调（2026-06-27，已记 `CLAUDE.md`）**：开发完成的能力**立即默认 ON、不灰度/不 shadow** → 新增 **WI-0.0 全量点亮表**（盘点所有"已开发但默认 OFF"的能力一次性开启）；删除原 WI-0.1 的 shadow-先行/批 A 批 B 渐进话术。
>
> **v1.1 用户反馈重构（2026-06-27）**：
> 1. 第三家 harness 由 OpenHands **改锚 Hermes Agent**（NousResearch，开源 MIT，2026-02）——其独门点：持久记忆 + **自主/自进化技能创建** + **本地推理**(Ollama/vLLM/llama.cpp) + 强结构化函数调用。原 OpenHands 提炼的通用开源-agent 机制（可逆压缩/浏览器/评测 harness）仍保留，标注为"开源 agent 旁证"。
> 2. **Code 模式不在本轮范围**（用户：code 入口保持隐藏不处理，目标先把主线程做到最强）→ **GAP-9 / 原 Phase 4 (RepoMap/AST) 整体移出本轮**，降级为附录"待重开 code 模式再做"。
> 3. **效率优先于审批**（用户：不想让用户各种审批，要高效完成用户想要的）→ 原 WI-1.2"审批策略轴"**反转为"高效自治档"**：**默认自治执行**，仅对**真高后果(支付/转账/删除/外发)+手滑级危险**确认；去掉 R2 给浏览器/技能加的常规确认卡（仅留高后果确认）。
> 4. 新增 **GAP-12 本地推理选项**（Hermes 旁证 + 桌宠"本地部署"身份契合）。
>
> **R1/R2 硬化历史**（仍有效）：R1 修 2 BLOCKING + 6 MAJOR（allow-list 回灌 / grader safe-fail 极性 / ephemeral model 三方争用 / execute_tool 门顺序权威表 / RulesComponent 装配点订正 / edit_file 抽函数回归硬门 / 批 A shadow-先行 / 前端接线 / GAP-7 第二半）；R2 修 1 MAJOR（WI-3.3 invoke_script 不可复用→新增 subprocess 执行器）+ 3 MINOR。锚点见 §附 A（门顺序表）/ §附 B（allow-list 强制）及各 WI 内"修正"标注。
>
> **纪律**：① 不少做功能——所有缺口全覆盖（GAP-9 除外：用户明确移出本轮，记附录）。② flag 门控、OFF=字节 BC（**例外**：WI-1.2 效率默认是**有意的行为变更**，见该 WI）。③ 每 WI 给 file:line 插入点。④ 不采纳沙箱类机制（[[feedback_no_sandbox_constraints]]），但保留手滑级 + 真高后果护栏。

---

## 0. 总览：Phase × 缺口 × 工作量

> 全部围绕 **Companion 主线程"最强能力 + 最高效率"**。code 模式相关（GAP-9）移出本轮（见附录 C）。
> WI 章节 ID 沿用下文原编号（不重排，避免交叉引用错位）；本表按 v1.1 优先级重新分组，"建议批次"列给执行序。

| 建议批次 | 缺口 | WI（章节 ID 不变） | 工作量 | 风险 |
|---|---|---|---|---|
| **批 0 — 全量点亮 + 效率（先做，体验立竿见影）** | GAP-11, GAP-6, GAP-7 | **WI-0.0 全量点亮已开发能力**（测试阶段不灰度）· **WI-1.2 高效自治默认** · WI-0.2 规则文件+知识注入 | 天级 | 低-中 |
| **批 1 — 质量 + 编辑** | GAP-2, GAP-1 | WI-2.1 子代理 grader+revise · WI-1.1 apply_patch 多-hunk | 周级 | 中 |
| **批 2 — 能力扩张（最强主线程）** | GAP-8, GAP-5(+Hermes 自进化), GAP-3 | WI-3.1 浏览器办事 · WI-3.3 自治/可执行技能 · WI-2.2 可逆 Condenser | 周+ | 中高 |
| **批 3 — 深化 + 扩展** | GAP-4, GAP-10, **GAP-12(新)** | WI-3.2 用户 hooks · WI-X.1 评测 harness · **WI-X.2 本地推理选项** | 周+ | 中 |
| ~~code 深化~~ | ~~GAP-9~~ | ~~WI-4.1 RepoMap/AST~~ **移出本轮** → 附录 C | — | — |

> 建议执行序：**批 0 先行**（WI-1.2 效率默认立刻提升体验）→ WI-X.1 评测 harness 尽早起步并与后续并行 → 批 1 → 批 2 → 批 3。
> 每个 WI 单独可 ship、可回退，互不阻塞。下文 WI 章节顺序未变，仅 **WI-1.2 内容反转**、**WI-4.1 标移出**、**新增 WI-X.2**。

---

## Phase 0 — 全量点亮已开发能力 + 规则文件（天级）

> **测试阶段纪律（用户 2026-06-27，已记 `CLAUDE.md`）：开发完成的能力立即默认开启，不灰度/不 shadow。** 故本 Phase 的"激活类"WI 一律**直接把出厂默认 flag 翻 ON**，不再"shadow 先行/批 A 批 B"。

### WI-0.0 — 全量点亮：已开发但默认 OFF 的能力一次性开启（核心，先做）

> **⚠️ 现状（2026-06-27 核实）**：本 WI **已在 `config.py` working tree 落地**（uncommitted）——A 表 flag 已全部翻 `True`（注释"测试阶段出厂点亮"），`_MIGRATABLE_SECTIONS` 已追加 `("features",)`/`("skills",)`/`("skills","auto_disclosure")`/`("skills","codify")`（注释"2026-06-27 全量点亮"）。故本 WI 由"待办"转为**"已落地 → 核对清单 + 补真测 + 消解两处协调"**。这是 plan 唯一一个"已含已执行代码"的 WI（其余仍纯规划）。

**背景**：盘点 `config.py` 发现大量已开发完成但出厂默认 `False` 的能力（Strangler-Fig 保守留的）。测试阶段立即开。下表 = 权威清单。

**A. 已点亮（核对 = 现已全 True，证据见 STATUS）**：

| 段 / flag (行号) | 能力 | 完成证据 |
|---|---|---|
| `[memory.v2]` `facts_extract:185` `rerank:186` `enhanced_retriever:187` `chunking:188` `query_rewrite:189` `cross_key_merge:193` `entity_path:195` `episodic_to_semantic:196` `reflection:191` `feedback_loop:184` `goal_facts:198` `persona_inject:214` `goal_facts_hook:217` `curation_nudge:221` `auto_learnings:230` `light_write:211` | 语义事实记忆栈（抽取/重排/RRF 增强/改写/跨 key 矛盾/entity/episodic→semantic/人格注入/自省 nudge/procedural 学习…） | STATUS §3：Stage1/2 ship + F1-F5 全修 + 严测 4 Phase 33 用例 + 2026-06-02 审计修复；"出厂点亮…语义事实记忆栈" |
| `[features]` `slash_commands:478` `goal_mode:479` `agent_parallel:480` `agent_team:491` `subagent_driver:490` `subagent_nonblocking:492` `preference_memory:482` `plan_confirm_gate:481` | Slash 命令 / goal 模式 / 多 agent 并行 / team / 子代理驱动 / 偏好记忆 / plan 确认门 | STATUS §2/§3："全套实现 + 真桌宠 E2E PASS；已 merge master"。**协调（MAJOR）**：`plan_confirm_gate`=plan 硬确认门，表面与 WI-1.2 "efficient 少弹窗"冲突；但它**仅 code 模式非平凡任务触发**（`config.py:457` 语义），本轮 code 入口隐藏→主线程不触发，无实际冲突。WI-1.2 实现时确保 efficient 档下若主线程意外命中 plan 门则 auto-confirm（对齐 `config.py:460` OFF 时 auto-confirm 语义） |
| `[skills]` `knowledge_enabled:409` · `[skills.auto_disclosure].enabled:386` · `[skills.codify].enabled:401` | 触发式知识注入 / 语义自动披露 / 技能自创闭环 | STATUS §3：SkillMatcher 混合披露 + WI-4.3 codifier 实现 |
| `[features]`（**订正：这 4 个在 `FeaturesConfig` 非 `[context.manager]`**）`ctx_observability:508` `adaptive_compact_pct:514` `summary_quality_loop:520` `microcompact_size_aware:524` | 压缩可观测 / 自适应 compact / 摘要质量回路 / size-aware microcompact | STATUS §4 2026-06-23：P3 实现完成 + 子代理评 7/8 |
| `[tools.verifier]` `external_evaluator:309` | 高后果异体评分 | FP-2.4 实现（external_evaluator.py）。**协调**：现已全局 ON，与 WI-2.1 grader 共享 `ephemeral_subagent_model`——pipeline evaluator 实例 `conservative_on_error=True`、grader 实例 `=False`（构造器默认即 False，已核实 `external_evaluator.py:217`），两实例隔离正确；WI-2.1 落地时 boot-log 实证三方 model 隔离 |

**B. 暂不开（带原因，做完/补护栏后立即开）**：

| flag (行号) | 不开原因 |
|---|---|
| `[tools.last_mile]` `artifact_envelope:267` `frontend_artifact_card:268` `tauri_artifact_ops:269` `outline_preview_default:271` | **实现未完成**（STATUS/架构测绘：artifact 信封"未实装"）→ 做完即开 |
| `[memory.v2.forget].enable_natural_language` (`MemoryV2ForgetConfig`, ~174) | 自然语言遗忘**危险/不可逆且默认禁用**→ 补护栏（确认门）后再开 |
| `[tools.verifier]` `run_build:296` `run_tests:297` | code 场景专属（跑构建/测试），与当前**主线程无关**→ 重开 code 模式时一并开 |
| `[features].plan_read_only:483` | 是 WI-1.2 自治档的依赖项，按 WI-1.2 统一处理（plan 模式只读硬门） |

**改动（现状：1+2 已在 working tree 落地，剩 3+4）**：
1. ✅ **已做**：`backend/config.py` A 表 flag 默认全 `→True`（"测试阶段出厂点亮"注释）。
2. ✅ **已做**：`_merge_missing_feature_flags()` 的 `_MIGRATABLE_SECTIONS` 已追加 `("features",)`/`("skills",)`/`("skills","auto_disclosure")`/`("skills","codify")`，存量 config 回灌已覆盖（原 BLOCKING-2 已闭环）。
3. **剩余：补 backfill 测试** `test_config_feature_flag_backfill.py`——证 A 表新默认（含 `[features]`/`[skills]`）能补进剥掉这些 key 的合成存量 config。
4. **剩余：逐能力真机抽测**（用 WI-X.1 评测 harness 批量回归 + windows-mcp 抽样）。⚠️ STATUS §3 记 memory.v2 栈"真机 E2E 待跑"、`auto_disclosure` 有"chat 不触发"已知局限——这两点是抽测重点。

**flag/BC（有意变更）**：测试阶段**有意改默认体验**（更多能力 ON），非 BC；每条仍保留 flag 可单独回退（出问题时关单条，不整体退）。
**验收**：① 全 flag ON 后 `pytest` 全套不崩（commit 前跑）。② backfill 测试证 `[features]`/`[skills]` 新默认补进存量 config。③ 真机：goal_mode→设目标真调 `goal_task_create`；knowledge_enabled→命中触发词注入知识；facts_extract→说"我叫小王"下轮召回；curation_nudge→多轮后落记忆。

---

### WI-0.1 — memory.v2 能力栈激活（GAP-11）→ **已并入 WI-0.0 A 表**

memory.v2 16 个 flag 的激活已统一进 **WI-0.0 A 表**（现已全 True，working tree 落地）。本节不再单列，真测重点见 WI-0.0 验收③。

---

### WI-0.2 — AGENTS.md / DESKPET-RULES 规则文件，每-run 重建注入（GAP-7）

**问题**：后端 grep 无 AGENTS.md / 规则文件加载机制（子代理已核实）。Codex 的"每 run 重建指令链、永不陈旧"是低成本高收益——给用户一个**项目级/全局可编辑、桌宠每次对话都遵守**的规则文件。

**改动**：
1. 新增 `backend/deskpet/agent/rules_loader.py`：
   - `load_rules(user_data_dir, cwd) -> str`：按优先级拼接 ① `<user_data_dir>/DESKPET-RULES.md`（全局，用户级）② `<cwd>/AGENTS.md`（项目级，若 code/办公任务有工作目录）。截断到预算（`max_chars=4000`，CJK-aware 用现成 `count_text_tokens`）。文件不存在→返 `""`（no-op）。
   - 纯函数、无 LLM、无 IO 异常外抛（OSError→log+返 ""）。
2. 装配点（**修正：无 `ChatOrchestrator` 类**；真实 system stack 由组件化 `ContextAssembler` 经 `build_default_assembler` 装配，`main.py:2013` 注册为 `context_assembler` 服务，persona 由 `PersonaComponent`（`deskpet/agent/assembler/components/persona.py`）注入）：**新增 `RulesComponent`** 挂进 `build_default_assembler` 组件链，**紧跟 `PersonaComponent` 之后插入**（保证 persona+rules 在组件链相邻、产出的 system message 连续；**注意**真实注册序是 Memory→Tool→Skill→Persona→Time…，memory 实际在 persona **之前**注册，故只锚定"紧跟 persona 之后"，不要写"memory 之前"），输出一条 `role=system`、`_is_rules=True` 的 message。`RulesComponent` 每次 assemble **重读文件、不缓存** = 永不陈旧。
3. **压缩保护硬约束**：规则 message 必须落在"开头连续 system 块"内才会被 `select_compactable_range`（`history_compactor.py:98-100` 的 sys_end while 扫描，子代理已核实）保住。故 RulesComponent 产出的 message 必须紧贴 system 栈（persona 也是 system，二者相邻即可），**不能被任何非 system 消息隔断**——实现时校验组件链里 persona+rules 连续。
4. `config.py` 新增 `[features].agent_rules_file`（默认 **True**，无文件即 no-op）+ `[features].rules_max_chars=4000`。**按附 B**：把 `("features",)` 加入 `_MIGRATABLE_SECTIONS`（否则该 flag 补不进存量 config）。
5. 验收加"无 RULES 文件时 system stack 字节不变"回归。

**flag/BC**：无文件存在时零效果；flag 可关。
**GAP-7 完整性（规则文件只是 GAP-7 的一半）**：Codex AGENTS.md = 本 WI 的"静态规则文件每-run 重建"；OpenHands microagent = "关键词命中→确定性注入知识片段"，**后者由仓库已有的 `[skills].knowledge_enabled` 覆盖**——它已在 **WI-0.0 A 表立即翻 ON**（测试阶段不 shadow）。故 GAP-7 两半齐全：规则文件（本 WI）+ 关键词知识注入（WI-0.0）。
**验收**：① 单测 `test_rules_loader.py`（优先级拼接/缺文件 no-op/超预算截断/OSError 优雅 + 无文件 system stack 字节不变）。② 真机：放 `DESKPET-RULES.md` 写"所有回复用 emoji 结尾"→桌宠对话遵守；删文件→恢复。③ WI-0.2b：开 `knowledge_enabled` → 命中触发词的对话注入对应知识片段（日志 `skill_knowledge_injected`）。

---

## Phase 1 — 编辑可靠性 + 自治档（周级）

### WI-1.1 — `apply_patch` 多-hunk 原子 diff 工具（GAP-1）

**问题**：`edit_file`（`deskpet/tools/os_tools/edit_file.py`）单 hunk，一次只改一处；批量改/跨文件改要多次调用，且无"新建+改+删"原子性。Codex `apply_patch` 是训练过的多-hunk unified-diff，token 省、可审、原子。

**改动**：新增 `backend/deskpet/tools/os_tools/apply_patch.py`，注册名 `apply_patch`，toolset=`os`，权限 `write_file`，`concurrency_safe=False`。
- **补丁格式**（采用 Codex/V4A 风格信封，对模型友好、训练覆盖好）：
  ```
  *** Begin Patch
  *** Update File: path/to/a.py
  @@ <可选定位锚>
  - old line
  + new line
  *** Add File: path/to/new.py
  + full new content line 1
  *** Delete File: path/to/gone.py
  *** End Patch
  ```
- **解析器** `parse_patch(text) -> list[Hunk]`：分段 Begin/End Patch；每段 Update/Add/Delete File；Update 段按 `@@`/`+`/`-`/` ` 行解析。
- **匹配复用**：Update File 的每个 hunk 复用 `edit_file.py` 现成 `_whitespace_fallback`（46-68）/ `_anchor_fallback`（71-107）/ `_did_you_mean_error`（110-120）+ `difflib` 容错。重构：把这三个纯函数抽到 `os_tools/_patch_match.py` 供两边共用，`edit_file.py` 改 import。
  - **回归硬门（修正：唯一一处 flag OFF 也改共享路径的改动）**：`edit_file` 是默认开的生产工具，抽函数破坏了"OFF=字节 BC"前提。故 **(a)** 抽函数作为**独立纯重构 commit**，与 apply_patch 新增 commit **分开**（便于 OFF 时 bisect）；**(b)** 验收**硬门** = `edit_file` 现有测试套全绿、抽前抽后 `edit_file.py` diff **仅 import 行变化**。
- **原子性**：先对所有文件**全部 dry-run 匹配**成功（任一 hunk 失配→整体回 `{ok:false, failed_hunk, did_you_mean}` 不写盘），再统一 `write_text`。`Add File` 目标已存在→拒；`Delete File` 不存在→拒。
- **write-scope（修正：`_write_scope_root` 不是函数，是 `args` 局部变量）**：真正复用源是 `from agent.write_scope import write_scope_check`（`edit_file.py:133` 即如此 import，`write_file.py:80`/`run_shell.py:220` 同款），对补丁内每个 path 各调一次。
- **审阅/应用**：返回结构化 diff（per-file `+/-` 行）。默认 `efficient` 自治档下**直接应用、不弹窗**（diff 仍落事件供事后查/撤销），仅当补丁命中高后果路径（删大量文件等）才经 WI-1.2 `requires_confirmation` 确认；前端渲染红绿（ArtifactCard 复用）。
- **大小护栏**：单补丁文件数 ≤ 20、总字节 ≤ 256KB（防爆窗），超限拒。

**flag/BC**：`config.py` `[features].apply_patch_tool`（默认 **False**，OFF 时不注册该工具，`edit_file` 不受影响=字节 BC）。**按附 B** 把 `("features",)` 加入 `_MIGRATABLE_SECTIONS`。注册点：`deskpet/tools/os_tools/registration.py`（仿 `edit_file` 注册，`visible_when` 跟随 flag）。
**验收**：① `test_apply_patch.py`：多文件多 hunk / 新建 / 删除 / 原子失败不写盘 / write-scope 拦截 / did_you_mean / 超大拒。② 真机：让桌宠"在 3 个文件里把变量 a 改成 b"→一次 apply_patch 原子完成 + 前端红绿审阅。

---

### WI-1.2 — 高效自治默认（GAP-6，**v1.1 反转：效率优先，不是加审批**）

**用户意图（point 4）**：不想让用户各种审批，要的是**高效完成用户想要的**。故本 WI 不是"加审批门"，而是**把默认行为从'逐项问'反转成'自动办，只在真危险时确认'**——同时保留一个"谨慎档"给少数想逐项确认的用户。

**问题**：DeskPet 现状 `permission_category`（registry：read_file/write_file/desktop_write/shell/network/mcp_call/skill_install）+ **逐工具权限弹窗**——桌宠每写个文件/跑个命令都弹窗，打断"办事"心流。

**改动**（**审批 UX 轴，非 OS 沙箱**，符合 [[feedback_no_sandbox_constraints]]）：
1. `config.py` 新增 `[features].autonomy_level`（枚举 `cautious | efficient | full`，**默认 `efficient`**——这是**有意的行为变更**，非 BC；老行为=`cautious`仍可选）。**按附 B** 把 `("features",)` 加入 `_MIGRATABLE_SECTIONS`。
2. `backend/deskpet/tools/registry.py` `execute_tool`：按**附 A 门顺序表**在 `circuit_breaker` 前插 `autonomy_gate(tool, level, args)`：
   - **`efficient`（默认）**：**绝大多数工具免确认直接执行**（写文件/desktop/普通 shell/network/mcp/技能/apply_patch 全自动）。**仅两类仍确认**：① **真高后果**（命中 `_HIGH_CONSEQUENCE_KEYWORDS` 超集：支付/转账/购买/删除/格式化/外发邮件微信短信/卸载/解绑/授权第三方登录——复用 `external_evaluator.py:31` 词表 + 扩浏览器/支付动作）；② **手滑级危险**（既有 `dangerous_tools_allowlist` + write-scope 越界）。
   - **`cautious`**：= 现状逐项弹窗（给想全程把关的用户）。
   - **`full`**：连真高后果也不弹（仅 write-scope/dangerous 兜底），给完全信任的用户（point 4"要"——最高自治档）。
   - 判定写成纯数据 `_AUTONOMY_RULES`（level × {auto / confirm_high_consequence / confirm_all}），易测。
3. **高后果判定单一来源**：所有"要不要确认"经一个 `requires_confirmation(tool, args, level)` 函数（复用 `_HIGH_CONSEQUENCE_KEYWORDS` 超集）——浏览器办事(WI-3.1)/可执行技能(WI-3.3)/apply_patch(WI-1.1) 都走它，**不各自再加确认卡**（去掉 R2 给浏览器/技能加的常规确认卡，只留高后果路径）。
4. 前端：设置面板"桌宠自治档"三档单选（`tauri-app/src/` 设置组件 + WS `settings_autonomy_level`）+ 主界面快捷 chip（复用模型 chip 基建 `02d24863`/`7309b311`）。高后果确认卡复用现有权限弹窗 UI（仅文案改"将执行可能不可逆的操作：<动作>"）。

**flag/BC（有意变更）**：默认 `efficient` **改变默认体验**（更少弹窗）——这是 point 4 要的，**非 BC**，但**手滑级 + 真高后果护栏不动**（write-scope/dangerous_allowlist/plan_read_only 恒生效）。想要老行为的用户设 `cautious`。
**验收**：① `test_autonomy_gate.py`（三档 × 各权限类别 + 高后果词命中强制确认 + write-scope/dangerous 恒拦 + 单一 `requires_confirmation` 来源）。② 真机：默认 `efficient` → 桌宠连续写 3 文件+跑命令**全程不弹窗**直接办完；说"删除我的 D 盘所有文件"→**仍弹高后果确认**。

---

## Phase 2 — fan-out 质量闭环 + 可逆压缩（周+）

### WI-2.1 — 子代理 grader + bounded revise 闭环（GAP-2）

**问题**：DeskPet 有 fan-out（`subagent_scheduler.py` / `agent_parallel` / `spawn_subagents`）但**无 per-subagent 评分-重做**；`external_evaluator` 只评主目标、仅高后果、flag off、无 revise 循环。Claude Performance Outcomes = grader 给每个子代理打分，不达 rubric 打回重做。

**改动**：
1. **泛化 evaluator（修正：非纯搬家 + safe-fail 极性必须独立）**：`external_evaluator.evaluate(original_goal, produced_artifacts, objective_evidence, conversation_summary) -> {quality_score, issues, verdict, reason}`（`:225`）的 persona 是写死的 `_EVALUATOR_SYSTEM_PERSONA`（148），**不接受 rubric 参数**。故重构 = 抽出 `grade(rubric_text, output, context, *, conservative_on_error) -> Verdict`，把 persona 模板改为可拼 rubric，复用 3 级 JSON 解析（`_parse_result:335-368`）+ safe-fail。新增 `backend/deskpet/agent/subagent_grader.py` 的 `SubagentGrader.grade_one(...)`。
   - **★ safe-fail 极性（BLOCKING 修正）**：现有 `evaluate()` 在 `conservative_on_error=True` 时失败返 `verdict="revise"`（FP-3 高后果保守阻断，`R-T3 §15.4`，pipeline evaluator 用此），与 grader 要的"失败→**收下**子代理结果"**正相反**。故 `SubagentGrader` **必须用独立的 `conservative_on_error=False` 实例**，**不复用** pipeline evaluator 实例。重构把 `conservative_on_error` 作参数透传，两调用点各传各值（pipeline=True / grader=False），互不污染。
2. **接线点（修正：移出 scheduler.run，放 fan-out 调用方）**：`subagent_scheduler.run()`（`:117`）只跑不透明 `coro_factory`、**不知产物语义**，不适合塞 grader。grader 接线放 **fan-out 发起方的 coro_factory 内部**（`spawn_subagents_tool` / `agent_parallel_tool` / deepresearch fan-out——它们拿得到 `task_spec` + `result`），scheduler 只透传进度。逻辑：
   - 子代理出结果 → 若 `grader_enabled` 调 `grade_one`；`verdict=="revise" 且 score<threshold 且 revise_round < max_revise(默认 1)` → `revise_hint` 作补充 prompt **重投同一子代理**（round+1）；否则收下。
   - 取消/超时/grader 失败 → safe-fail **收下原结果**（绝不卡死）。
3. **rubric 来源**：fan-out 发起方可传 `rubric` 字段；缺省通用 rubric（"是否完整回答子任务、有无明显遗漏/编造"）。
4. **成本护栏 + 模型（修正：避免与 verify/evaluator 三方争用）**：`max_revise` 默认 1。新增 `[features].subagent_grader_model`（缺省回落 `[tools.verifier].ephemeral_subagent_model`=haiku），经 `_resolve_ephemeral_provider(base, grader_model)`（`main.py`，与 verify 救援/异体评分**同一套**克隆+回落语义，STATUS `772c4291` 已建）装配，缺省/失败回落主 LLM。并发计入 scheduler semaphore lane。验收 boot-log 实证 grader model。
5. **前端/WS 接线（修正：补全闭环）**：WS 事件 `subagent_graded{run_id, score:int, verdict, revise_round, issues:[]}` + `subagent_revising{run_id, round}`；前端 `SubagentProgressPanel` 渲染分数徽章 + revise 轮次 + （可选）展开 issues。**人工介入口**：grader 判 revise 但 `max_revise` 已耗尽时，面板给"接受/再打回一次"按钮（复用现有 approval WS 通道）。

**flag/BC**：`config.py` `[features].subagent_grader`（默认 **False**，OFF=现状直接收下子代理结果，字节 BC）。**按附 B** 把 `("features",)` 加入 `_MIGRATABLE_SECTIONS`。
**验收**：① `test_subagent_grader.py`（grade_one 各 verdict / revise 一次后收下 / max_revise 上界 / grader 失败 safe-fail 收原结果 / 成本不超额）。② 真机：deepresearch fan-out 中故意让一个子问题答得差 → 日志见 `subagent_graded verdict=revise` + `revising round=1` + 重做后收下。

---

### WI-2.2 — history_compactor → 可逆 Condenser（GAP-3）

**问题**：`history_compactor.py` 有损——`inject_summary`（138 行）用单条摘要**替换并丢弃**中段原文，无法 replay/审计/回灌。OpenHands Condenser 插 marker 不删 event。

**改动**（保持现有摘要 UX，但原文可恢复）：
1. 新增 `backend/agent/condense_store.py`：`stash(session_id, marker_id, dropped_messages) -> None` / `recall(session_id, marker_id) -> list[dict]`。复用现成 `ToolResultRefStore`（`agent/tool_result_truncator.py:132`，含 `_spill_write/_spill_read:211/229`，LRU+JSON spill）同款模式，spill 到 `user_data_dir()/condense/`。
   - **有界（修正：防长会话磁盘暴涨）**：`condense_store` 设**单会话 stash 总量上限**（如 32MB / 64 marker），超限丢最早 marker（其 `recall` 返 `{ok:false, reason:"expired"}`），对齐 ref-store 有界语义。
2. 改 `history_compactor.inject_summary`（`:111-138`）：摘要 system message 带 `_condense_marker_id`（稳定 id `f"cmp-{start}-{end}-{hash}"`），替换前调 `condense_store.stash(...)` 存原文。**默认行为对模型不变**（仍只看摘要），仅多一份可恢复副本。
3. 新增工具 `recall_condensed`（toolset=code，read_file 权限）：按 marker_id 回灌被压细节（仿 `fetch_tool_result` ref 恢复语义）；摘要 system msg 提示"如需早期细节可 recall_condensed(marker=...)"。**前端**：recall 结果**静默回灌进 context**（不出大卡片，避免撑爆 UI）；仅在 trace 留记录。
4. GC：`condense/` 复用 `artifact_dir_retention_days`（**位于 `[tools.last_mile]` 段 = `ToolsLastMileConfig`，`config.py:272`**，非裸顶层，读取时注意段位）。

**flag/BC**：`config.py` `[context].condense_reversible`（默认 **False**，OFF=现状有损压缩字节 BC；ON 才 stash + 注册 recall_condensed）。`[context]` = `ContextManagerConfig`，已在 allow-list（`context.manager`）无需动。
**验收**：① `test_condense_store.py`（stash/recall 往返 / spill / GC）+ `test_history_compactor.py` 扩（ON 时摘要带 marker + 原文可 recall；OFF 时字节不变）。② 真机：长对话触发压缩后，问"我最早第一句说了什么"→agent 调 recall_condensed 取回。

---

## Phase 3 — 能力扩张（周+，需重真测）

### WI-3.1 — 浏览器办事：browser_use 从 "E2E 测试工具" 提升为 companion 一等能力（GAP-8）

**问题**：`deskpet/tools/browser_use_tool.py` 已有完整基建（browser-use + Playwright Chromium + 后台 job runner + JSON 持久化 + `start/poll/result` 动作），但定位是 **E2E 测试**（`toolset=e2e`，`[code_e2e].browser_use_enabled` 默认 false，仅 code-mode）。桌宠最该有的恰是"帮我上网办事"（查快递/订票比价/填表）。

**改动**（复用现有 90% 基建，主要是定位与门控）：
1. 新增 companion 侧注册：把 `run_browser_task` 也注册进 companion 可用工具集（toolset 从 `e2e` 复制一份 `web` 语义，或加 `companion_browser` 别名工具），**不动**原 e2e 工具。
2. 新 flag `[features].browser_companion`（默认 **False**）：ON 时 companion 主线可调。**效率默认下（efficient）：普通导航/查询自动执行不弹窗**；仅当任务命中高后果（下单/支付/授权登录/删除/退订）才经 WI-1.2 `requires_confirmation` 确认（"将执行可能不可逆的操作：<动作>"）——**不加常规"将打开浏览器"确认卡**（point 4 效率优先）。**按附 B** 把 `("features",)` 加入 `_MIGRATABLE_SECTIONS`。
3. 安全护栏（桌宠场景）：① 仅允许导航，**不自动提交支付/转账类**——命中危险词→强制人工确认每步。**词表 = `external_evaluator._HIGH_CONSEQUENCE_KEYWORDS`（`:31`）的超集**（该表为"完成度评估"设计，须补浏览器特有危险动作：下单/确认支付/授权登录第三方/删除/退订/解绑），新建 `_BROWSER_HIGH_CONSEQUENCE_KEYWORDS`。② 默认非无头（用户可见浏览器在做什么）。③ 任务超时/取消级联接 `/stop`（复用 job runner 现有取消）。
4. 结果作为 artifact 卡片回桌宠（复用 artifact 信封）。

**flag/BC**：默认 OFF=现状（仅 e2e 测试用），字节 BC。
**验收**：① 单测：companion 注册 + flag 门 + 高后果强制确认。② 真机 windows-mcp：开 flag→对桌宠说"帮我查一下今天上海到北京的高铁票"→浏览器可见执行→结果卡片回桌宠（按仓库 HARD 真测纪律：真截图+真日志）。

---

### WI-3.2 — 用户可扩展运行时 hooks（GAP-4）

**问题**：产品 agent 的"硬机制"全是硬编码 gate；用户/高级用户无法像 Claude Code 那样配 pre/post-tool/stop hook 做确定性自动化（如"每次写文件后自动 git add"、"禁止删 .env"）。

**改动**（轻量、单机、非沙箱）：
1. 新增 `backend/deskpet/agent/hooks.py`：`HookRunner`，事件点 `pre_tool` / `post_tool` / `pre_final(stop)` / `subagent_complete`。
2. 配置来源：`<user_data_dir>/hooks.toml`（用户级）声明 `[[hook]] event=... matcher=<tool 名 glob> command=<本机命令>`。每个 hook = 一个本机命令；DeskPet 传 JSON（tool 名+args）到 stdin，读 stdout/exit code：
   - `pre_tool` 命令 `exit 2` → **阻断该工具调用**（把 stderr 作为工具错误回 LLM，模拟 Claude exit-2 硬阻断）。其它 exit → 放行。
   - `post_tool` / `subagent_complete`：观察/副作用用，exit 非 0 仅 log 不阻断。
3. 接线：按**附 A 门顺序表**——`registry.execute_tool` 在 `disabled_toolsets`/`plan_read_only` 之后、`autonomy_gate` 之前调 `pre_tool`，dispatch 之后调 `post_tool`；`agent_loop` 收尾前调 `pre_final`。
4. **递归边界（修正）**：hook 命令是**终端隔离 subprocess**——它内部即使写文件/跑命令，也**不回灌进 agent 的 execute_tool、不触发二级 hook**（hook 不嵌套）。与 WI-3.3 的叠加：executable skill 经其工具入口走 execute_tool → **会**触发一次 `pre_tool matcher=skill_<name>` hook，这是**有意的安全叠加**（hook 能拦 skill 执行），在 WI-3.3 里注明。
5. 护栏（对齐桌宠"只防手滑"）：hook 命令默认 5s 超时；失败/超时 = log + 放行（绝不因 hook 故障卡死桌宠）；`hooks.toml` 不存在 = 零开销。
6. **复用 codingsys 经验**：hook 协议（stdin JSON / exit-2 阻断 / stderr 反馈）刻意对齐 Claude Code hooks，便于用户迁移已有 hook。

**flag/BC**：`config.py` `[features].runtime_hooks`（默认 **False**；OFF 或无 hooks.toml = 零开销，字节 BC）。**按附 B** 把 `("features",)` 加入 `_MIGRATABLE_SECTIONS`。
**验收**：① `test_hooks.py`（pre_tool exit-2 阻断 + 错误回 LLM / post_tool 不阻断 / 超时放行 / 无文件 no-op）。② 真机：配 `pre_tool matcher=write_file command=拦截 .env` → 桌宠尝试写 .env 被 hook 挡 + 桌宠如实告知。

---

### WI-3.3 — skill 产可执行脚本 + 自进化自创（GAP-5，**+Hermes 自进化角度**）

**问题**：`skill_invoke` 只注 Markdown body；`requires_script` 恒 false（STATUS §3 明列）。Claude Skills 能带脚本/sub-tools；**Hermes Agent 更进一步——自主/自进化技能创建**（self-improving）。DeskPet 现有 codify（自创闭环）**仅声明式、产物恒不可执行**（红线）。本 WI 两件事：(A) 让"用户安装的" skill 可带可执行脚本；(B) 让 codify 自创闭环可**提议**可执行技能（仍走用户确认门，不破坏"不自动执行未审脚本"红线）——逼近 Hermes 自进化但保留人类闸。

**改动**：
1. SKILL.md frontmatter 支持 `executable: true` + `entry: <脚本相对路径>`（`.py`/`.ps1`/`.sh`）+ `params_schema`（JSON Schema）。
2. `backend/deskpet/skills/loader.py`：现 `_register_skill_invoke_tool`（`:666`）只注册 `skill_invoke`。新增：解析到 `executable` skill 时**动态注册** `skill_<name>` 工具（toolset=`skill`，权限 `shell`——走 shell 审批门 + WI-1.2 审批策略 + WI-3.2 hooks）。
3. 执行器（**R2 修正：loader 现成 `invoke_script` 不可直接复用**）：复读 `loader.py:574-661` 确认 `invoke_script` 是**受限 Python 沙箱求值器**——硬编码只跑 SKILL.md 同目录固定 `script.py`（`:586`），`sys.executable -I -c <preamble+body>` 把 builtins 裁到 ~40 白名单（`:605-624`），`stdin=DEVNULL`、**不传 args**、**只能 Python、跑不了三方库/`.ps1`/`.sh`**。这与本 WI 要的 `entry:` 多语言 + `params` 传参**实质冲突**。故执行器 = **新增独立执行路径**（非复用 invoke_script）：
   - 用 `os_tools/run_shell.py` 同款 `subprocess` 基建跑 `entry` 指定脚本（`.py`/`.ps1`/`.sh`，按扩展名选解释器），`params` 经 argv + JSON-stdin 传入，超时复用 run_shell 超时，stdout 作工具结果。
   - 决策：executable skill **不走** invoke_script 的受限 builtins 沙箱（那是给 codify 声明式产物用的安全求值器）；executable skill 是用户经 `skill_install` 显式授权安装的，按 `shell` 权限 + 审批 + hook 三重门控（附 A），故可跑真子进程。`requires_script`（`SkillMeta:79`）字段已存在可复用标记。
   - ⚠️ 工作量重估：此项不是"复用搬家"，是**新写一个 subprocess 执行器**（约 1 文件 + schema + 注册），plan §0 工作量表 WI-3.3 相应上调。
4. **(B) codify 自进化（Hermes 角度，新增）**：自创闭环检测到"重复多步可固化"时，可**提议**一个 executable skill 候选（含 entry 脚本草案）→ 落 `<user_data>/skills/_proposed/` + 桌宠主动问"要我把这套流程固化成可一键调用的技能吗？"→**用户确认后**才标 `executable=true` 并装载。**红线守住**：未经用户确认的自创产物 `executable=false`、不进 execute_tool 工具集（对齐 STATUS §3 红线，不自动执行未审脚本）。
5. 安全门：① 仅 `skill_install` 权限门通过 / 用户确认的 codify 候选可标 executable。② flag 门控。③ 执行经 execute_tool → 受 WI-1.2 自治档（**efficient 下普通技能直接跑，仅命中高后果词才确认**，去掉 R2 的常规"首次执行确认卡"——point 4 效率优先）+ `pre_tool matcher=skill_<name>` hook（WI-3.2 有意叠加）。

**flag/BC**：`config.py` `[skills].executable_skills`（默认 **False**；OFF 时 `executable` 字段被忽略=现状纯 Markdown，字节 BC）+ `[skills].codify_executable_proposals`（默认 **False**，B 子能力独立 flag）。**按附 B** 把 `("skills",)` 加入 `_MIGRATABLE_SECTIONS`。
**验收**：① `test_executable_skill.py`（注册 skill_<name> / 跑脚本回 stdout / efficient 下普通技能不弹窗 / 高后果词命中确认 / 未确认 codify 候选恒 executable=false 且不进工具集 / flag off 忽略）。② 真机：装"算 BMI"executable skill → 桌宠调 `skill_bmi(身高,体重)` 直接跑回结果；codify 提议固化某流程 → 用户确认后变可调用技能。

---

## 横切轨道 — WI-X.1 跨模型 agent 行为回归评测 harness（GAP-10）

**问题**：DeskPet 验收重度依赖**昂贵脆弱的手工 windows-mcp E2E**（项目 #1 痛点，CLAUDE.md 大段纪律）。开源 agent（Hermes/OpenHands 等）以持续评测流水线（多模型、programmatic + LLM-based）兜底。一套轻量"场景回放 + 轨迹断言"harness 能把大量手工 E2E 自动化，**反哺所有批次的验收**——故建议尽早起步、与各批并行。

**改动**：
1. 新增 `backend/tests/agent_eval/`：
   - `scenarios/*.yaml`：每个场景 = `{user_messages: [...], model: <id>, asserts: [...]}`。asserts 支持：`tool_called(name)`、`tool_not_called(name)`、`tool_sequence([...])`、`gate_fired(verify_gate|evidence_gate|...)`、`final_contains(regex)`、`no_fake_completion`。
   - `runner.py`：用**真 AgentLoop**（非 mock），可注入 mock provider（确定性回放，无网络/无费）或真 relay（带 `@pytest.mark.live`）。捕获 `trace.jsonl`（WI-2 已有）+ 事件流断言。
2. **多模型 matrix**：`pytest.mark.parametrize` 跑同场景 × {gpt-5.5, deepseek-v4-pro, haiku}，catch 模型相关回归。
3. CI/本地：`scripts/agent_eval.py`（无网络默认套，`--live` 跑真 relay 套）。可挂 codingsys hook 在编辑 agent 代码后自动跑无网络套。
4. **复用现成资产**：场景可从既有 `testcase/*/manual-test.md` 半自动转写（很多手测用例本就是"发这句→期望调这个工具/这个 gate 触发"）。
5. **可行性 spike 先行（修正：别低估"真 AgentLoop + mock provider 确定性回放"）**：AgentLoop 含大量非确定分支（StuckDetector difflib / 三级自检 @10/20/30 / focus chain @8 / pipeline 预分析）。mock provider 要喂出**确定的多轮 tool_call 序列**才能让断言稳。故 **P0 spike**：先用 1 个最简场景（七步流水线 TC-1 闲聊短路 0-LLM）验证 mock provider 能驱动**真 AgentLoop 确定性跑绿** → 验证可行后再铺 5 个场景。若 spike 失败（确定性收敛太难），降级为"录制真 relay 响应序列回放"方案。

**flag/BC**：纯测试设施，不碰产品代码路径（除读 trace），零 BC 风险。**风险重估**：由"低风险"改为"**spike 验证前中风险**"（确定性回放可能是隐形坑），spike 通过后回落低风险。
**验收**：① 把 ≥5 个现存手测用例（如七步流水线 TC-1 闲聊短路 0 LLM / TC-2 取证门 / BUGB-3 allowlist 0 LLM）转成 yaml 场景跑绿。② 无网络套 < 60s 跑完。③ 多模型 matrix 至少覆盖 1 个真 relay 场景。

---

## 横切轨道 — WI-X.2 本地推理选项（GAP-12，**新增，Hermes 旁证**）

**问题**：DeskPet 语音管线全本地，但 **LLM 走中转站（relay 云端，gpt-5.5 等）**。Hermes Agent 主打**本地推理**（Ollama/vLLM/llama.cpp），契合桌宠"本地部署 + 隐私"身份，且断网/无额度时仍可用（降级而非瘫痪）。DeskPet 已有 `LLMProviderRegistry`（relay-cloud 收编就是经它），加一个**本地 provider 行**是顺势扩展，非重写。

**改动**：
1. `backend/llm/provider_registry.py`：新增 `source="local"` 的 provider 类型，连本地 OpenAI 兼容端点（Ollama `http://127.0.0.1:11434/v1` / vLLM / llama.cpp server）。复用现成 `OpenAICompatibleProvider`（中转站/relay 同款线缆层），仅 base_url + 无需 key。
2. 设置面板"模型"区加"本地模型"入口（复用 relay-provider 收编的 UI 模式 `relayProviderRegistration.ts` 同构）：探测本地端点可达→列出本地模型→可设为默认或链式兜底（relay 不可达时自动回落本地）。
3. provider chain：把本地 provider 放链尾作**断网/超额兜底**（efficient 体验：云端挂了桌宠仍能答，只是模型弱些）。
4. 能力分级：本地小模型工具调用能力弱→对本地 provider 降工具复杂度（可选 `local_simple_tools` flag，仅暴露核心工具子集，防小模型工具幻觉）。

**flag/BC**：`config.py` `[features].local_inference`（默认 **False**，OFF=现状纯云端，BC）。**按附 B** 把 `("features",)` 加入 `_MIGRATABLE_SECTIONS`。
**验收**：① 单测：本地 provider 注册 / 链尾兜底回落 / 端点不可达优雅降级。② 真机：起一个 Ollama → 设置面板出现本地模型 → 拔掉 relay → 桌宠经本地模型仍能对话。

---

## 附 A — execute_tool 门顺序（唯一权威表，WI-1.1/1.2/3.2 共享）

`backend/deskpet/tools/registry.py` `execute_tool`（现状 749-789）现有门序：`disabled_toolsets(726) → plan_read_only 写类硬拦(744-765) → circuit_breaker(772) → PermissionGate(777) → dispatch`。本 plan 三个 WI 共改这条热路径，**叠加后确定性顺序固定为**（新插入项加粗）：

```
disabled_toolsets
  → plan_read_only（只读模式硬拦，最高优先，`full` 档也拦）
  → **pre_tool hook（WI-3.2，exit 2 硬阻断，优先于自治档）**
  → **autonomy_gate（WI-1.2，按 cautious/efficient/full 决定是否确认；默认 efficient=绝大多数自动，仅真高后果/手滑确认）**
  → circuit_breaker
  → PermissionGate（既有逐工具权限弹窗；被 autonomy_gate 标 auto 的跳过弹窗）
  → dispatch（apply_patch 结构化 diff 在此产出）
  → **post_tool hook（WI-3.2，观察/副作用，exit 非 0 仅 log 不阻断）**
```

语义铁律：① **hook exit-2（用户确定性边界）永远赢过自治档**（即使 `full`，pre_tool hook 仍能挡）。② **plan_read_only 只读硬门赢过一切自治档**。③ **write-scope + dangerous_tools_allowlist 手滑护栏在任何自治档恒生效**（含 `full`）。④ hook 命令是**终端隔离 subprocess，不递归进 execute_tool、不触发二级 hook**。WI-1.1/1.2/3.2 实现时必须引用本表。

## 附 B — 跨 WI 通用纪律

- **测试阶段：开发好的能力立即默认 ON，不 shadow/不灰度**（用户 2026-06-27，已记 `CLAUDE.md`）。WI-0.0 把已开发完成的 OFF flag 一次性翻 ON；新做的能力也"做完即 ON"。**唯一仍 OFF 的**：实现未完成（artifact 信封）/ 危险无护栏（NL 遗忘）/ 主线程无关（code 专属）——见 WI-0.0 B 表。
- **新能力 WI 的 flag**：开发期可挂 flag 便于单独回退，但**完成即默认 ON**（不留 default False 等灰度）。OFF 仅作"出问题时单条快速回退"开关，不作"灰度上线"用。
- **flag 回灌 allow-list（BLOCKING 修正）**：`_merge_missing_feature_flags()` 的 `_MIGRATABLE_SECTIONS`（`config.py:813-826`，白名单语义）**当前不含 `("features",)` / `("skills",)`**。凡新增 flag 落 `[features]`/`[skills]` 的 WI（WI-0.2/1.1/1.2/2.1/3.1/3.2/3.3/X.2），**改动必须显式追加** `("features",)`/`("skills",)` 到 `_MIGRATABLE_SECTIONS` + 补 backfill 测试，否则新 flag 永补不进存量用户 config。`[memory.v2][.facts]`/`[code_e2e]` 已在 allow-list（WI-0.1/WI-3.1 复用成立）。
- **safe-fail**：任何新机制（grader/hook/condenser/browser/rules）故障/超时 → log + 放行/收下，**绝不卡死桌宠**（对齐仓库 safe-fail 纪律）。WI-2.1 grader 的 safe-fail 极性见该 WI（失败=收下，须用 `conservative_on_error=False` 独立实例）。
- **不动两个 agent 包的边界**：运行时 `AgentLoop` 在 `backend/agent/`，pipeline/grader/rules 在 `backend/deskpet/agent/`（两包都活、都被 git 跟踪——子代理已核实）。新模块按此归属。
- **真测纪律**：每个有 UI/出站行为的 WI 收官前按 CLAUDE.md HARD CONSTRAINT 走 windows-mcp 真测（真点真输入+截图+日志），不可用单测/协议层替代。WI-X.1 落地后，部分可降级为自动评测 + 抽样真测。
- **STATUS 同步**：每 WI ship 后按 CLAUDE.md §STATUS 纪律更新 `STATUS/status.md` §3/§4。

## 附 C — 本轮移出范围（GAP-9 code 模式深化）

**用户决策（point 2/3）**：code 模式入口保持隐藏不处理，本轮目标是把 Companion 主线程做到最强，故 **GAP-9（RepoMap / AST 符号索引 / 代码向量检索）整体移出本轮**。原 WI-4.1 设计草案存档备查（待将来重开 code 模式入口时再启）：
- `code_mode/repomap.py`（tree-sitter 抽符号 + PageRank + token 二分，移植 Aider）。
- `repo_map` / `code_search` 工具 + `vector_worker` 增"代码 chunk 索引"表 `code_vec`。
- `[code_mode].repomap` flag。
重开 code 模式时另开 plan；本轮不实现、不验收。

## 附 D — 未决问题（review 时与用户确认）
1. ~~第三家 = OpenHands？~~ → 已按你反馈改锚 **Hermes Agent**；若你心里的"hermes-like"另有所指（如别的开源 agent），告诉我再调。
2. 批次优先级：建议 **批 0 先行（WI-1.2 效率默认体验立竿见影）** + WI-X.1 评测 harness 尽早并行，是否 OK？
3. WI-1.2 默认档 `efficient`（自动办、仅真高后果确认）+ 提供 `full`（连高后果也不弹）——这个"高后果"边界（支付/转账/删除/外发/授权第三方登录）你认可吗？要不要再收紧/放宽？
4. WI-X.2 本地推理（Ollama/vLLM）优先级——是真要做的能力，还是先占位？（它对"最强主线程"是兜底而非提升，可放最后）
