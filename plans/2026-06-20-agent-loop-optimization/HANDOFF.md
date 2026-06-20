# HANDOFF — Agent Loop 优化 + WI-5 知识注入遗留 bug

> 给**新 session** 接手用。最后更新：2026-06-20。
> 主 plan：[`00-PLAN.md`](./00-PLAN.md)（v1 + R1~R4 挑战修订 §13/§15/§16/§17）。

---

## 0. ⚠️ 工具调用稳健性（务必先读，避免重蹈覆辙）

**现象**：工具调用偶尔退化成纯文本打印出来，带乱码前缀（`call` / `care`），格式变成 `<invoke>`（少 `antml:` 命名空间），结果**工具没执行**（报 "malformed could not be parsed"）。

**根因**：往**单个工具调用塞大段/复杂多行内容**会撑爆生成、触发工具调用 XML 序列化错乱。**两个高频触发源**（本 session 实测）：
1. **Bash 内联 heredoc 脚本**（`cat > /tmp/x.py << 'EOF' …多行… EOF`）。
2. **超长多行 commit message**（`git commit -m "…十几行中文…"`）。

**解决（硬规矩）**：
- 写脚本：**先 Write 工具建文件 → 再 Bash 跑文件**（`python xxx.py`），绝不内联 heredoc。
- commit：用**单行短 message**（长说明写进文档，不塞 -m）。
- 每个工具调用保持短、简单。

---

## 1. 任务背景（已基本完成）

实施 [`00-PLAN.md`](./00-PLAN.md) 的 7 个 WI，用 **codex gpt5.5 子代理并行实现 + Lead（Claude）集成验证**，每批走「实现→自测→子代理评估100%→手测文档→windows-mcp 真机测试」。

| WI | 状态 |
|---|---|
| WI-1 tool_choice 协议级硬约束 | ✅ 100% 完成 + 真机 PASS |
| WI-2 结构化 trace（jsonl） | ✅ 100% 完成 + 真机 PASS |
| WI-3 code persona 收尾自查 | ✅ 100% 完成 + 真机 PASS |
| WI-4 Focus Chain todo 回灌 | ✅ 100% 完成 + 真机 PASS（`wi4_todo_sync iter=8`） |
| **WI-5 触发式知识注入** | ⚠️ **机制打通但有遗留 bug，见 §3** |
| WI-6 SEARCH/REPLACE fuzzy 降级 | ✅ 100% 完成 |
| WI-7 ask_clarification（后端+前端） | ✅ 100% 完成 + 真机完整闭环 PASS（真点弹窗答题） |

真机抓修的真 bug（已修）：WI-2 flag 命名碰撞、build_agent cfg.raw BC 回归、WI-6 fuzzy schema 缺失。

---

## 2. WI-5 的演进（已做的修复）

WI-5 = 用户消息含触发词（如 "ppt"）→ 自动把知识片段正文注入 LLM 上下文。3 个内置知识片段在
`backend/deskpet/skills/builtin/`：`ppt-tips` / `windows-path-debug` / `source-check`（均 `user-invocable: false`）。

**多代理对抗审计 + 真机测试发现并已修的两层 gap**：

1. **G1（commit `011aab5`）**：`chat` task_type 的 policy.prefer 不含 `skill`（`policies/default.yaml`）→ 闲聊路径永不 fan-out SkillComponent。
   修：`assembler.py` 在 `knowledge_enabled` 开启时给本轮 prefer 追加 `skill`（`dataclasses.replace` 出副本；默认 off→BC 安全）。

2. **config 流通（commit `d7da6e5`）**：仅 G1 不够——SkillComponent 读的是 assemble() 的 **per-turn config**，里面只有 `skills.auto_disclosure`、没有 `skills.knowledge_enabled`（默认只注入了前者）→ 知识片段被 `skill.py:130` 过滤掉。
   修：`build_default_assembler` 加 `knowledge_enabled` 参数 → 进 `_default_config` → merge 进 `ctx.config`；`main.py` 构造 assembler 时传 `knowledge_enabled=bool(config.skills.knowledge_enabled)`。

**真机已证实这两个修复生效**：开两个 flag 后，发 chat "帮我做个ppt" →
`assembler_task_classified ... prefer=[..., 'skill']`（skill 进了 prefer）+ `skill_auto_disclosed total=12 strong=1 ...`（SkillComponent 跑起来了，之前 count=0）。

---

## 3. ⚠️ 遗留 BUG（新 session 重点解决）：知识片段的 trigger 运行时不命中

**症状**（真机 `plans/2026-06-20-agent-loop-optimization/codex/tauri-dev7.log`）：
- chat 发 "帮我做个ppt…" → `skill_auto_disclosed total=12 strong=1 auto_loaded=1 names=['ppt-generate'] top_sim=0.950`
  —— 命中的是**常规技能 `ppt-generate`**（也含 "ppt" trigger），**不是知识片段 `ppt-tips`**。
- code 发 "我在 windows 下用反斜杠路径老是报错…" → `skill_auto_disclosed total=12 strong=0 auto_loaded=0 names=[] top_sim=0.479`
  —— **strong=0，完全没有 trigger 命中**，尽管 query 含 "windows"/"路径"/"反斜杠"，而 `windows-path-debug` 的 triggers 正是 `[路径, windows, Windows, 反斜杠, backslash, path]`。

**结论**：知识片段（user-invocable:false）的 **triggers 在运行时匹配器里没有生效**；常规技能（ppt-generate）的 trigger 却正常命中（top_sim=0.95）。所以知识注入**目前实际还是不工作**。

**已排除**：
- `loader.py` 确实解析 triggers（`backend/deskpet/skills/loader.py:379-397` 和 `:421-435`），SkillMeta 有 `triggers` 字段（`:86`），`knowledge_enabled` 时确实加载知识片段（`:303-308`）。
- `_is_disabled_for_model`（`skill.py:285`）查的是 `disable_model_invocation`（知识片段没设），**不**排除知识片段。
- 匹配器逻辑 `skill_matcher.py:201-256`：对**所有** skill 检查 `any(t.lower() in query_lower for t in triggers)`，命中即 sim=`_TRIGGER_SIM`(0.95)。逻辑上知识片段应能命中。

**待查假设（按优先级）**：
1. **`ctx.skill_registry.all()` 返回的 skill 对象是否真带 triggers？** `total=12` 说明候选有 12 个（9 常规+3 知识），但传给 matcher 的对象可能不是带 triggers 的 SkillMeta（可能是另一个 registry 包装的对象，triggers 丢了）。
   - 查 `main.py` 里传给 `assemble()` 的 `skill_registry` 到底是什么对象（是 SkillLoader 还是别的 registry？它的 `.all()` 返回啥？元素有 `.triggers` 吗）。
   - 对比：ppt-generate 的 trigger 命中了，说明常规技能对象有 triggers；为何知识片段对象没有？两者从同一 loader 来，差异可能在「knowledge_enabled 时额外加载的那批」对象构造路径不同（看 loader.py:303-308 加载知识片段那段，是否走了和常规技能不同的解析分支 → triggers 没填）。
2. **知识片段是否真在 `skills` 候选里？** total=12 不代表 3 个都是知识片段——可能是 9 常规 + 3 别的。需确认那 3 个知识片段真在候选 list 里且对象带 triggers。
3. matcher 的 `skills` 来自 `skill.py:96-109`（`registry.all()` 当 auto_enabled）——确认这个 registry 与 loader 一致。

**下一步诊断（用 Write 建脚本，别内联 heredoc！）**：
写一个 `diag_wi5.py`：用和 main.py 同样的方式构造 SkillLoader（看 `main.py` 里 `_skill_loader = SkillLoader(...)` 的真实构造参数）→ `loader.reload()` → 遍历 `loader.all()` 打印每个 skill 的 `name` / `triggers` / `user_invocable`，**重点看 3 个知识片段的 triggers 是不是空**。若 loader 层 triggers 是空 → bug 在 loader 加载知识片段的分支；若 loader 层有 triggers 但 registry/matcher 拿不到 → bug 在 registry 包装或 SkillComponent 取 skills 的路径。

---

## 4. 环境 / 如何跑（真机测试必读）

**测试凭据**：`LOCAL-DEV-CREDENTIALS.md`（gitignored，仓库不含）。

**启动 worktree 代码（含本批改动）**——绝不要手动起 backend，只给 Tauri 注入 env：
启动脚本已写好：[`codex/launch-dev.sh`](./codex/launch-dev.sh)，内容是
`cd tauri-app` + `export DESKPET_BACKEND_DIR=G:/projects/deskpet/backend` +
`export DESKPET_PYTHON=G:/projects/deskpet/backend/.venv/Scripts/python.exe` +
`export DESKPET_DEV_MODE=1` + `npx tauri dev`。

后台跑：`bash plans/2026-06-20-agent-loop-optimization/codex/launch-dev.sh > <logfile> 2>&1`（run_in_background + dangerouslyDisableSandbox）。

**HARD GATE**：启动日志必须出现 `[backend_launch] Dev python=G:/projects/deskpet/backend/...`；
若是 `Bundled exe=` 说明跑的是旧 frozen exe，**整轮作废**。

**停应用**：`taskkill /F /IM deskpet.exe` 常失效，改用 windows-mcp Process kill by name（force=true）。

**当前 app 状态**：本 session 结束时可能有一个 dev 实例在跑（log `codex/tauri-dev7.log`）。新 session 接手前先 kill 干净再重启。

**WI-5 两个 flag 当前状态**：用户要求**测试环境默认打开**，已在
`C:/Users/24378/AppData/Roaming/deskpet/config.toml` 设：
- `[skills] knowledge_enabled = true`（约 516 行）
- `[skills.auto_disclosure] enabled = true`（约 519-520 行）
（这是 runtime 配置，不是代码默认值；代码默认仍 false 保 BC。）

**驱动桌宠对话**（windows-mcp，中文走 Clipboard+Ctrl+V，不支持中文 IME 直接 Type）：
- companion 聊天输入框约屏幕 `(3439, 1514)`，发送按钮约 `(3711, 1512)`（截图缩放比 3.0，坐标会随窗口/分辨率变，每次先 Screenshot 校准）。
- code 模式：点桌宠面板顶部工具栏 terminal 图标（约 `(3702, 633)`，hover 出 tooltip「进入 Code 模式」）→ 开 code 仪表盘，输入框约 `(1149, 1020)`。

---

## 5. 测试命令

```
# WI 单测
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_wi1_tool_choice.py backend/tests/test_wi2_trace.py backend/tests/test_wi3_persona.py backend/tests/test_wi4_focus_chain.py backend/tests/test_wi5_trigger_inject.py backend/tests/test_wi6_edit_fallback.py backend/tests/test_wi7_clarify.py -q

# WI-5 真路径测试（assemble 级，验证 chat+knowledge_enabled→skill fan-out）
backend/.venv/Scripts/python.exe -m pytest "backend/tests/test_deskpet_context_assembler.py::test_wi5_knowledge_enabled_runs_skill_component_for_chat" -v

# BC 大面回归
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_deskpet_agent_loop.py backend/tests/test_p6_agent_loop_gate.py backend/tests/test_build_agent_verify_wiring.py backend/tests/test_byte_level_consistency.py backend/tests/test_deskpet_context_assembler.py -q
```

注意：直接 `python -c "import deskpet…"` 会 ModuleNotFoundError（要 cwd=backend）；pytest 能跑是因为 pyproject 配了 rootdir。

---

## 6. 关键文件 / commit

**WI-5 相关代码**：
- `backend/deskpet/agent/assembler/components/skill.py`（SkillComponent，知识注入主逻辑：行 86 读 knowledge_enabled / 130 过滤 / 175-232 强匹配+body inline / 224 `_is_knowledge` 标记 / 250 `skill_auto_disclosed` 日志）
- `backend/deskpet/skills/skill_matcher.py:201-256`（trigger+embedding 混合匹配）
- `backend/deskpet/skills/loader.py`（加载+解析 triggers；303-308 知识片段加载分支 ← **怀疑点**）
- `backend/deskpet/agent/assembler/assembler.py:217-233`（G1 修复：knowledge_enabled→prefer 加 skill）
- `backend/deskpet/agent/assembler/__init__.py:135-148`（config 流通修复：knowledge_enabled→default_config）
- `backend/main.py`（约 1672-1688：构造 assembler 传 auto_disclosure_config + knowledge_enabled）
- 知识片段：`backend/deskpet/skills/builtin/{ppt-tips,windows-path-debug,source-check}/SKILL.md`

**本批 commit（git log，倒序，最近的几个）**：
- `d7da6e5` fix(wi5): knowledge_enabled 注入 assembler default_config
- `011aab5` fix(wi5): knowledge_enabled 时 SkillComponent 对 chat 也 fan-out（G1）
- `4b2d25d` feat(agent): Batch C — WI-7 ask_clarification
- `8ddda29` feat(agent): Batch B — WI-4/5/6
- `6eb55f4` feat(agent): Batch A — WI-1/2/3
（完整见 `git log --oneline`，从 `6eb55f4` 到 `48d37dc` 是本次全部工作 + STATUS 更新。）

**手测文档 / 真机结果**：
- `testcase/2026-06-20-agent-loop-batch-{a,b,c}/`（用例，已登记 `testcase/index.md`）
- `plans/manual-results-2026-06-20-batch-{a,b,c}/RESULTS.md`（真机结果，含 WI-5 激活条件发现）

**STATUS**：`STATUS/status.md` §3 模块完成度 + §4 里程碑已更新（含 WI-5 缺口与修复记录）。

---

## 7. 新 session 建议的第一步

1. 先 kill 干净任何残留 deskpet.exe（windows-mcp Process kill）。
2. 按 §3「下一步诊断」用 **Write 建 diag 脚本**（别内联 heredoc），定位知识片段 triggers 在 loader 层 / registry 层 / matcher 层哪一环丢失。
3. 修复后真机复验：开两 flag、发含 "windows 路径反斜杠" 的话 → 期望 `skill_auto_disclosed ... names=['windows-path-debug']` 且 `knowledge_loaded_count≥1`、日志/上下文出现「知识片段（自动注入）」标题。
4. 补一条真路径单测覆盖「知识片段 trigger 命中→knowledge_loaded_count>0」（现有 `test_wi5_trigger_inject.py` 是构造级，可能也绕过了真实 registry，需核对）。
5. 完成后更新 STATUS + 本 HANDOFF。
