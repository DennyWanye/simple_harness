# HANDOFF — 打开"已开发但默认 OFF"的能力（测试阶段，立即开启不灰度）

> 给**全新 session** 的自包含执行单。冷启动即可干，无需本对话上下文。
> 产出来源：[`00-PLAN.md` §WI-0.0](./00-PLAN.md)（完整清单 + 证据，**别重抄，按需点开**）。
> 仓库根：`G:\projects\deskpet` ｜ 创建日：2026-06-27
>
> （注：handoff skill 默认建议存 mktemp 临时文件，这里改存仓库内以便新 session 直接引用；已 git add。）

---

## 0. 一句话任务

DeskPet 处于**测试阶段**：把**已开发完成（单测/验收过）但出厂默认 `False`** 的能力**全部立即翻 `True` 投入使用**，**不 shadow / 不灰度 / 不分批**。规则已写进 [`CLAUDE.md` §🚀 测试阶段：能力即开即用](../../CLAUDE.md)（HARD），务必先读。

---

## 1. 先读这些（按顺序，5 分钟进入状态）

1. [`CLAUDE.md`](../../CLAUDE.md) §🚀 测试阶段不灰度（HARD 纪律）+ §STATUS 更新纪律 + §🚨踩过的坑 #7/#8/#9（dev 启动别双占端口/必设 `DESKPET_BACKEND_DIR`）。
2. [`00-PLAN.md` §WI-0.0](./00-PLAN.md) —— **A 表（立即开）/ B 表（暂不开 + 原因）** 是本次的权威清单。
3. `backend/config.py` —— 所有 flag 的 dataclass 定义在这里。

---

## 2. 要做什么（A 表：立即翻 ON）

在 `backend/config.py` 把下列 dataclass 默认值 `False → True`（行号已核实，2026-06-27；改前请 grep 确认行号未漂）：

**`[memory.v2]`（`class MemoryV2Config` ~178）—— 语义事实记忆栈：**
`feedback_loop:184` · `facts_extract:185` · `rerank:186` · `enhanced_retriever:187` · `chunking:188` · `query_rewrite:189` · `reflection:191` · `cross_key_merge:193` · `memory_forget:194` · `entity_path:195` · `episodic_to_semantic:196` · `goal_facts:198` · `light_write:211` · `persona_inject:214` · `goal_facts_hook:217` · `curation_nudge:221` · `auto_learnings:230`
（`pref_decay:203` 本已 True，不动。）

**`[features]`（`class FeaturesConfig` ~445）：**
`slash_commands:478` · `goal_mode:479` · `agent_parallel:480` · `preference_memory:482` · `subagent_driver:490` · `agent_team:491` · `subagent_nonblocking:492`
（`plan_confirm_gate:481` 一并开；`relay_managed_provider:484` 本已 True。）

**`[skills]`：**
`knowledge_enabled:409`（`class SkillsConfig`）· `[skills.auto_disclosure].enabled:386` · `[skills.codify].enabled:401`

**`[context.manager]`（`class ...`）：**
`ctx_observability:508` · `adaptive_compact_pct:514` · `summary_quality_loop:520` · `microcompact_size_aware:524`

**`[tools.verifier]`（`class ToolsVerifierConfig` ~276）：**
`external_evaluator:309`

---

## 3. ⚠️ 三个必须做对的关键点（否则白改）

### 3.1 光改 dataclass 默认**不够**——config.toml 里若有显式 `false` 会盖过默认
仓库里有多个 `config.toml`（dev userdata / 出厂 bundle / dist），且经 grep 发现这些 flag **很可能已被显式写成 `false`**。`load_config` 读 config.toml 时，**显式值覆盖 dataclass 默认**；而 `_merge_missing_feature_flags()` **只补缺失键、绝不覆盖已存在的值**（STATUS 2026-06-23）。所以：
- **(a)** 改 `config.py` dataclass 默认（管全新安装 + 无该键的 config）。
- **(b)** 找到**你这次真机要跑的那个 config.toml**（dev 源码跑默认落 `backend/userdata/config.toml`，见 [[reference_dev_userdata_dir_on_g]]；设 `DESKPET_DEV_MODE=1`），把上述 flag 显式设 `true`，**或删掉那些 `= false` 行**让 dataclass 默认生效。
- **(c)** 找到**出厂 bundle 的 config.toml**（PyInstaller 打包/`seed_user_config_if_missing` 用的那份，grep 定位：`grep -rln "goal_mode" --include=config.toml backend/`，挑出**非 dist、非 worktree** 的那份权威种子），同步设 `true`，让新装用户也拿到。
- 先 `grep -nE "goal_mode|facts_extract|knowledge_enabled" <目标config.toml>` 看现状再改。

### 3.2 allow-list 必须加 `("features",)` 和 `("skills",)`
`backend/config.py` 的 `_MIGRATABLE_SECTIONS`（~813-826）**当前不含** `("features",)` / `("skills",)`（已核实）。不加的话，`[features]`/`[skills]` 的新默认**补不进存量用户 config**。请在该元组里追加：
```python
("features",),
("skills",),
```
（`("memory","v2")` / `("context","manager")` 已在表内，无需加。）

### 3.3 不要碰 B 表（暂不开，开了会坏或无关）
见 [`00-PLAN.md` §WI-0.0 B 表](./00-PLAN.md)：
- `[tools.last_mile]` artifact 信封 4 个（`267-271`）—— **实现未完成**，开了可能崩。
- `[memory.v2.forget].enable_natural_language`（~174）—— 自然语言遗忘**危险无护栏**。
- `[tools.verifier]` `run_build:296` / `run_tests:297` —— code 场景，**与主线程无关**。
- `[features].plan_read_only:483` —— 归 WI-1.2 自治档统一处理，本次别开（会让 plan 模式禁写，与"效率优先"冲突）。
- `[tools].strict_unknown_toolset:344` / `[memory.v2].workspace_memory:190` —— 非能力/属 code，跳过。

---

## 4. 验证（测试阶段开启的目的就是暴露问题）

1. **单测全绿**：`cd backend && <venv>/python -m pytest`（全 flag ON 后跑全套）。**有 flag 导致测试挂 = 真 bug，就地修或如实报告，不要因此把 flag 退回 OFF**（测试阶段纪律）。注意 config 有校验不变式（VG-INVARIANT 等，`config.py:650+`）——A 表不碰 `verify_gate_mode`/`emit_receipts`（本已 strict/True），应不触发；若校验报错按提示修。
2. **真机 windows-mcp 抽测**（按 [`CLAUDE.md`](../../CLAUDE.md) 真测纪律：真点真输入+截图+日志，dev 用 `scripts/dev-worktree.ps1` 或 `dev-start.ps1`，别手动起 backend 双占端口）：
   - `goal_mode`：设个目标 → 主 agent 真调 `goal_task_create` 建 DAG（日志 `goal_task_tools_registered_global count=4`）。
   - `knowledge_enabled`：发命中触发词的话 → 日志 `skill_knowledge_injected` / `skill_auto_disclosed`。
   - `facts_extract` + `enhanced_retriever`：说"我叫小王是程序员" → 下轮问"我是谁"召回命中。
   - `slash_commands`：发 `/` 命令生效。
3. 抽测发现的真 bug → 修 + 记录。

---

## 5. 收尾纪律（HARD）

- **STATUS 同步**（[`CLAUDE.md` §STATUS 纪律](../../CLAUDE.md)）：开启 + 验证通过后，更新 [`STATUS/status.md`](../../STATUS/status.md) §3 相关行（标"出厂默认 ON")，§4 追加一行里程碑"测试阶段全量点亮已开发能力"，改顶部日期。
- **提交**：新文件/改动及时 `git add`（[[feedback_commit_untracked_immediately]] 防沙箱回滚）。是否 commit 听用户的。
- **别做**：别加 shadow/灰度；别动 B 表；别手动起 backend 再起 Tauri（端口双占，坑 #7）；跑 worktree 代码必设 `DESKPET_BACKEND_DIR`+`DESKPET_PYTHON`（坑 #8）。

---

## 6. 建议下个 session 用的 skill

- 真机验证环节：直接用 **windows-mcp**（真测，见 `~/.claude/knowledge-base/windows-mcp-e2e.md`）。
- 若想把这步当一个正式变更走全流程，可用 **`/sp:verify`**（auto-verify 两层循环）收口。
- 不需要 spec-first（本质是 flag flip + 验证，非 3+ 文件新功能设计）。

---

## 7. 关键路径速查

- flag 定义：`backend/config.py`（dataclass 默认 + `_MIGRATABLE_SECTIONS` ~813）
- 真机要跑的 config：`backend/userdata/config.toml`（dev，`DESKPET_DEV_MODE=1`）
- 完整清单+证据：[`plans/2026-06-26-agent-harness-alignment/00-PLAN.md` §WI-0.0](./00-PLAN.md)
- 纪律：[`CLAUDE.md`](../../CLAUDE.md)（测试阶段不灰度 / STATUS / 踩过的坑）
- 状态档：[`STATUS/status.md`](../../STATUS/status.md)
