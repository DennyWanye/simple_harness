# DeskPet 优化总规划 — 上下文管理 + 对标系统升级（2026-06-22）

> **状态**: **v1.0 EXECUTABLE-AS-IS**（R1 双子代理校准 + ground-truth 实读裁决 + codex 对抗 NOT-READY → R2 全收，无 BLOCKING/MAJOR 残留；2026-06-22 用户拍板 3 项决策已固化，无待决项。详见 §8）
>
> **用户决策（2026-06-22 已定，已固化进 plan）**：① 圈圈 **两层都做**（1A Claude Code 环境 + 1B DeskPet 运行时）；② TG-1 **方案 A**（新建 `goal_task_create` + 提升 TaskGraphStore 三件套为主 agent 全局可见）；③ 方向二 **全做 P0~P3**（含 OH-3/CC-3/CC-5/TG-2 等低优先项，一个不漏）。
> **依据**: [STATUS/status.md](../../STATUS/status.md)（2026-06-22 已校准）+ 三路调研（架构 / 对标系统 / 上下文管理）+ `research/` 对标资料
> **范围**: 两大优化方向 —— ①上下文管理（Claude Code 工作环境 + DeskPet 运行时双层）②openclaw/hermes/claude/openhuman 对标差距补齐
> **铁律**: 不可少做功能；技术项细化到「具体哪个文件、哪个函数、怎么改」；全部新功能 flag 出厂 OFF = 字节级 BC

---

## 0. 这份 plan 解决什么

用户两个诉求：

1. **「context 圈圈一直显示满、是不是不自动压缩了」+「上下文管理有没有不好用的地方、怎么优化」**
   → 拆成两层（关键区分，见 §2）：
   - **1A Claude Code 工作环境**（用户当前敲命令的这个 IDE 的 context 环 / 圈圈）—— 圈圈满≠不压缩，而是**固定开销**太大（系统提示 + 巨型工具/skill 目录 + CLAUDE.md/MEMORY.md），auto-compact 只压「对话历史」、**压不动**这部分固定开销。
   - **1B DeskPet 自身运行时**（桌宠 agent 跟 LLM 对话的上下文/compaction）—— 已有四层防线 + P-B 已修 + compaction 默认开，剩**精修项**（token 计数收敛 / 可观测 / 自适应阈值 / 摘要质量回路）。

2. **「对 openclaw/hermes/claude/openhuman 有什么要优化的」**
   → 逐系统对标，补齐 DeskPet 相对它们的差距（见 §3）。

---

## 1. 现状基线（来自 2026-06-22 调研，已校准）

### DeskPet 护城河（本 plan 绝不削弱）
- ✅ goal-completion 产物校验闭环（last-mile artifact / receipt / verify-gate）
- ✅ 全本地语音管线（VAD→ASR→LLM→TTS）+ Live2D 表达力
- ✅ 「真 E2E ≠ 脚本回放」「不糊弄自己」测试纪律
- ✅ 子代理并发驱动 8 模式（lane 队列 / 摘要隔离 / 事务分型）2026-06-21 真机 V1-V5 PASS

### 上下文管理现状（§1B 基线）
- 四层防线：①`l2_top_k=5` 截历史 → ②`BudgetAllocator` 裁组件 slice → ③`token_budget` BLOCK gate（≥95%）→ ④`ContextCompressor` 7 段结构化滚动摘要
- **P-B 已修**（2026-06-16 `84e4c251`）：压缩窗口现按真实出站模型解析（`config.py:effective_llm_model`），不再 hardcode 32000
- `compaction_enabled` 默认 **True**（`config.py:435`）
- 已完成 WI-1~6（2026-06-16 compaction 升级）：触发改剩余 buffer / microcompact / 7 段摘要 / 目标 always-on / pre-flush L1
- **剩余精修项**（非 bug）：token 计数 scatter 未全收敛、压缩可观测性弱、`compact_at_pct` per-model 固定不自适应、无摘要质量回路

### 对标系统现状（§3 基线）
| 系统 | 是什么 | DeskPet 已借鉴 | 主要差距 |
|---|---|---|---|
| **openclaw** | TS 单进程 agent 编排，hub-spoke 子代理，lane-aware FIFO 队列 | 8 模式全实现（2026-06-21） | 仅余打磨（depth 计数、背压指标可观测） |
| **hermes** | Nous 自我进化 agent，agentic JSON-mode 自我纠错 + 技能自创 | 周期记忆 nudge 思想 | 🔴 自我纠错闭环（校验不过→error_analysis→重规划→重试）；技能自创产可执行代码 |
| **claude**（Claude Code）| 官方 agentic harness，8 大机制 | slash/skill/工具权限/last-mile verify/多 agent/code 模式 | 🔴 skills 三级披露彻底化 / plan mode 只读权限 / `/verify`+`/run` skill / hooks exit-2 深化 / auto-memory |
| **openhuman** | 个人超级 agent（Tauri+本地+记忆树）| BGE-M3 向量检索为主 | 🔴 五路混合检索 / PROFILE 人格半衰期 / 写入分级 / 记忆 self-curation nudge |
| （cc-haha）| 国内桌面工作台（Task 工具族 + 集中审批）| `/goal` + 多 agent | 🟠 Task 结构化任务图持久化 + 审批 UX 聚合 |

---

## 2. 方向一：上下文管理优化

> 详见 [01-context-optimization.md](./01-context-optimization.md)（含每个 WI 的文件:行/函数签名/改法/测试点）

### 1A — Claude Code 工作环境 context 优化（直接回答「圈圈」问题）

**诊断（已量化）**：圈圈满的主因是**固定开销**，不是 auto-compact 失灵：
- 全局 `~/.claude/CLAUDE.md` ≈ 10.8K 字符（≈3-4K token）
- 项目 `CLAUDE.md` ≈ 11.4K 字符（≈3-4K token）
- `MEMORY.md` ≈ 5K 字符 + 27 个 memory 文件（recall 时按需注入）
- **最大头**：deferred 工具目录（~250 个工具名）+ skill 目录（~150 个）+ MCP 服务器说明（Blender/computer-use/windows-mcp 各一大段）
- auto-compact 只压「对话消息历史」，**对上述固定开销无能为力** → 所以一开新会话圈圈就接近满。

**优化动作**（1A 是「工作环境配置 + 文档瘦身」，非 DeskPet 代码改动；详 01 文档给出每条的具体操作）：
- WI-1A-1 CLAUDE.md 瘦身（全局 + 项目）：抽长段到按需加载的 skill/参考文件，主文件只留高频铁律
- WI-1A-2 裁剪未用 MCP 服务器 / 插件（Blender/部分 design 插件等非 DeskPet 必需的，按需启用）→ 砍掉工具/skill 目录的大头
- WI-1A-3 确认并配置 auto-compact（阈值 / 是否开启）+ 给出何时手动 `/compact` 的 SOP
- WI-1A-4 MEMORY.md / memory 文件治理（合并冗余、删过时，降低 recall 注入量）

### 1B — DeskPet 运行时上下文/compaction 精修

- WI-1B-1 token 计数 scatter 收敛：把残留裸 `len//4` / `_approx_tokens` 全路由到 `tokens.count_text_tokens()` + 防回归测试
- WI-1B-2 压缩可观测性：`context_compacted` 事件补字段（压前/压后 token、节省比、触发原因）+ 可选 dashboard
- WI-1B-3 自适应 `compact_at_pct`：按任务性质（多工具 agentic vs 纯对话）微调阈值（flag，默认沿用固定值）
- WI-1B-4 摘要质量回路：检测「压缩后用户问『刚才在干嘛』」→ 计摘要质量差 → 触发重摘（flag）
- WI-1B-5 microcompact 触发精修：从「全局清旧 tool_result」改启发式「只清 >N 轮前的」

---

## 3. 方向二：openclaw / hermes / claude / openhuman 对标优化

> 详见 [02-reference-systems-optimization.md](./02-reference-systems-optimization.md)（含每个 WI 的文件/函数/数据结构/测试点）

> ⚠️ **重大实现度校准（2026-06-22，实读代码后）**：方向二**大面积已实现**——调研 `research/` 档多写于 2026-06-04 前，而 DeskPet 此后已落 FP-4/FP-5/子代理并发 8 模式等。实读代码后，多数「差距」实为「已实现，仅差点亮 flag / 补观测 / 补入口」。下表每行标注真实现度。**真正需新建的缺口只有 4 个**（OH-4 / CC-2 / OC-1 / OC-2）。详见 [02-reference-systems-optimization.md](./02-reference-systems-optimization.md)。
>
> **这正是「不可少做功能」的正确解读**：不是把已实现的重写，而是把已实现但 OFF/未接的**点亮 + 补全 + 加真缺口**，一个对标点都不漏。

**WI 五类分类法（解决「口径不稳」，每个对标点归一类、无遗漏）**：

| 类 | 含义 | WI |
|---|---|---|
| **A 已实现+已测，仅点亮 flag/补观测** | 代码已在且有测试，默认 OFF 或缺观测 | HM-1（自我纠错，含清 stale 注释 + 查 ephemeral 专用模型是否真生效）· CC-1（skill 压缩后 `_remount_skills` 已实现且有测试）· OH-2（pref_decay 默认 off）· TG-1 goal 持久化（已落库） |
| **B 真缺口，新建** | 实读确认无等价物 | OH-4 记忆 self-curation nudge · CC-2 plan mode 物理只读权限 · OC-1 显式 depth 上界 · OC-2 累计背压指标 |
| **C 已有基础，补全/补入口/加固** | 核心在但差临门一脚 | OH-1（四路 RRF→决定是否默认开第五路 facts/entity）· OH-2 补用户 pin/forget 入口 · OH-3 写入分级 light 路径 · TG-1（新建 `goal_task_create` 工具 + 厘清三套任务概念定唯一目标 + 清 goal_store stale 注释） |
| **D 低优先新增/评估** | 锦上添花 | CC-3 `/verify` 真实运行 skill · CC-5 终端用户 auto-memory（评估） |
| **E 明确不做** | 避免过度工程 | CC-4 通用 hook 层（已有 verify-gate end_turn 守门即等价物） |

### 3.1 openhuman → 记忆工程（实读后：核心已实现，补边角）
- WI-OH-1 五路混合检索 — ✅ **已实现**（`retriever.py:226` 4 路 RRF + `enhanced_retriever.py` 叠 facts/rerank/chunk/rewrite）→ **降级为「确认默认开 + 补 freshness 权重可调」或删除**
- WI-OH-2 PROFILE 人格半衰期 — 🟡 **核心已实现**（`facts.py:_CATEGORY_DECAY` + `set_pinned:882` + `preference_profile.py` 📌置顶注入）→ **仅补 PreferenceMemory JSON 衰减 + 对话式 Pin/Forget 入口**
- WI-OH-3 记忆写入分级 — 🟡 部分 → 补 light 路径（高频流跳 embedding）`memory/manager.py`
- WI-OH-4 记忆 self-curation nudge — 🔴 **真缺口**：agent_loop 周期性让 agent 自决该不该记

### 3.2 hermes → 自我纠错闭环（实读后：已完整实现）
- WI-HM-1 agentic JSON-mode 自我纠错 — ✅ **已完整实现**（`reflection.py:StructuredReflection`=error_analysis/critique/replan + `agent_loop.py:1395+` verify 守门 + stagnation difflib>0.85 检测 + ephemeral 升级 + `verify_gate.py:GoalAlignment` 重述原目标对照）→ **降级为「点亮 flag + 补观测 + 真机验证闭环真生效」**
- WI-HM-2 技能自创产可执行 — 🟡 已有独立 LOCKED plan → **引用 [plans/2026-06-22-skill-executable-function-call](../2026-06-22-skill-executable-function-call/)，不重复造**

### 3.3 claude（Claude Code）→ harness 机制补深
- WI-CC-1 skills 三级渐进披露 — ✅ **已实现且有测试**（`skill.py` auto_disclosure embedding 强匹配+body inline+预算+LRU；`loader.py:read_body`；**compaction 后重挂 = `agent_loop.py:_remount_skills` 已实现 + `test_deskpet_skill_remount_after_compaction.py` 已覆盖**，codex 实读裁决 ⚠️ 已消解）→ 无接线工作，仅可选调优 `_remount_skills` 策略（D 类）
- WI-CC-2 plan mode 只读权限模式 — 🔴 **真缺口**（`code_mode/` grep 0 命中只读权限切换）：规划期物理禁 Edit/Write
- WI-CC-3 `/verify`+`/run` bundled skill — 🟡 last-mile 已有 → 补一个面向真实桌宠运行验证的 skill + 启动配方
- WI-CC-4 hooks exit-2 — ❌ **不引入通用 hook**：DeskPet 运行时无 hook 层，已有 verify-gate end_turn 守门即等价物（避免过度工程）
- WI-CC-5 auto-memory — 🟡 L1 文件记忆/pre-flush 已有 → 评估是否补面向终端用户的轻量 auto-memory（低优先）

### 3.4 openclaw → 子代理调度打磨（8 模式已实现，仅余补强）
- WI-OC-1 depth 计数真生效 — 🔴 **真缺口**（现仅「剥 spawn 类工具」守门，无显式 depth 数值上界）
- WI-OC-2 背压/lane 指标可观测 — 🔴 **真缺口**（调度器无累计 metrics）→ 埋点 + 前端进度面板已有可承接

### 3.5 cc-haha → Task 任务图 + 审批聚合
- WI-TG-1 Task 结构化任务图 — ✅ 存储+持久化已实现（`task_graph.py:TaskGraphStore` DAG+claim_ready + `goal_store.py` bind_persistence/persist/load_persisted 已落库）→ **决策方案 A**：`task_create` 工具实际**不存在**（仅 `goal_task_list/update` 且仅 teammate 可见）→ 新建 `goal_task_create` + 提升三件套为主 agent 全局可见 + 清 `goal_store.py:11-12` 过期「v1 不持久化」注释。R2 已厘清三套任务概念
- WI-TG-2 前端审批 UX 聚合视图 — 🟡 ⚠️ 前端实现度未读，落地前核实

---

## 4. 分期与依赖（草案，待 03 细化）

> 校准后分期：方向二多为「点亮 flag + 补观测」（轻），真新建只有 4 个缺口。

| Phase | 内容 | 依赖 | 价值 |
|---|---|---|---|
| **P0（立即，低风险，直接回应用户）** | 1A 全部（工作环境瘦身，缓解圈圈）+ 1B-1（token 收敛，仅 2 处）+ 1B-2（压缩可观测）| 无 | 直接缓解「圈圈满」+ 工程整洁 |
| **P1（点亮已实现的护城河 + 补观测）** | HM-1 点亮+观测+修 dead config / OH-1 决定第五路 lane / OH-2 补 Pin 入口+开 pref_decay / TG-1 新建 goal_task_create（方案A）/ CC-1 已实现计 0 | 多数无前置（CC-1 已消解）| 把已建能力真正用起来，低成本高收益 |
| **P2（4 个真缺口新建）** | OH-4 记忆 nudge / CC-2 plan mode 只读权限 / OC-1 显式 depth / OC-2 背压指标 | 各自独立可并行 | 补真空白 |
| **P3（打磨/低优先）** | OH-3 写入分级 / CC-3 `/verify` skill / CC-5 auto-memory 评估 / 1B-3/4/5 / TG-2 审批 UI / HM-2 引用既有 plan | 多数已 ship 基建 | 锦上添花 |

---

## 5. 全局约束（每个 WI 必须遵守）

1. **flag 出厂 OFF + 字节级 BC**：每个新功能挂 `config.py [features]` flag，默认 False；OFF 时与现状字节一致（回归测试守）。
2. **不加沙箱护栏**（`feedback_no_sandbox_constraints`）：deskpet 单机桌宠，只防手滑级破坏。
3. **测试纪律**：单测 + 真机 windows-mcp E2E（`feedback_real_e2e_not_script_replay`）。WI 落地需 STATUS 同步更新。
4. **不削护城河**：记忆/语音/产物校验/真测纪律是地基，所有改动加性扩展不重写。
5. **master 直接开发**（`feedback_deskpet_branch_strategy`），新文件即 `git add+commit`（防沙箱回滚）。

---

## 6. 验收标准（plan 可执行性门槛）

- [ ] 每个 WI 有：确切文件路径 + 函数/类名 + 改法（新增/修改/签名）+ flag 名 + 默认值 + 测试点（单测文件名 + 真机用例）
- [ ] 每个 WI 的「OFF=BC」可被一条回归测试验证
- [ ] 跨 WI 依赖显式标注，无环
- [ ] 子代理多轮对抗后无 BLOCKING / MAJOR 未决项
- [ ] 1A 给出用户可直接照做的操作步骤（含预估 token 节省量）

---

## 7. 决策记录（2026-06-22 已拍板）

1. ✅ **「圈圈」侧重** → **两层都做**：1A（Claude Code 环境瘦身，先做，立竿见影）+ 1B（DeskPet 运行时精修，跟进）。
2. ✅ **方向二范围** → **全做 P0~P3**：4 真缺口（OH-4/CC-2/OC-1/OC-2）新建 + 已实现项点亮 + 低优先项（OH-3/CC-3/CC-5/TG-2）也做，一个对标点不漏。
3. ✅ **TG-1 任务概念** → **方案 A**：新建 `goal_task_create` 工具 + 把 `task_graph_tools` 三件套（list/update/新 create）从「仅 teammate」提升为主 agent 全局可见；TaskGraphStore DAG+依赖+持久化已就绪，最贴近 cc-haha Task 工具族。已固化进 02 文档 TG-1。
4. ✅ **已建能力默认点亮（决策①，更激进）**：
   - **HM-1 自我纠错 = 全档默认开**（structured_reflection / verify_gate 非 off）——含陪伴档。
   - **OH-2 偏好衰减 = 默认开**（pref_decay=True）；**硬前置**：用户「pin/忘记某条偏好」入口**必须与衰减同一批上线**（否则桌宠自动淡忘而用户无法保留）。
   - **TG-1 goal_mode = 仍默认手动**（不全局默认开，长目标档手动启用）。
5. ✅ **1A 落地（决策②）→ 方案 B**：CLAUDE.md 抽出的长段规范化成**按需加载的可复用资产**（`~/.claude/knowledge-base/` 或参考 skill），CLAUDE.md 只留一行指针；不只给一次性 SOP。所有项目/新机器复用，圈圈瘦身长期不反弹。
6. ✅ **1B-1 token 收敛（决策③）→ 方案 B（直接改，不挂 flag）**：统一到中文友好口径 `count_text_tokens`，修「中文被低估→压缩偏晚」隐患；纯等价重构（//4→//4）本就无行为变化直接做；会改数字的部分直接改 + **强回归测试 + 真机 windows-mcp 确认压缩时机变化无害**。原拟的 `unified_token_count` flag **取消**。

---

## 8. 对抗迭代记录（供 review 看收敛过程）

本 plan 严格走「写 → 多轮对抗实读裁决 → 修订」直到无 BLOCKING：

| 轮次 | 方式 | 关键发现 | 处置 |
|---|---|---|---|
| **起草** | 3 路 Explore（架构/对标/上下文）+ Lead 综合 | 主纲基于 `research/`（多写于 2026-06-04 前）| 立 00/01/02 框架 |
| **R0 撰写** | 2 个 general-purpose 实读代码写 01/02 | **方向二大面积「其实已实现」**（research 滞后于代码）| §3/§4 实现度校准 |
| **R1-A ground-truth 裁决** | 1 个 gp 实读裁决 7 处 ⚠️ | HM-1/OH-1/OH-2/TG-1 已实现；1B-1 残留比想象多；CC-1 skill 在受保护分区 | R1 修订 01（token 6 处+flag）/02（CC-1 降级/TG-1 加大/HM-1 确认）|
| **R1-B codex 对抗** | codex gpt-5.5 只读审查（独立印证）| **NOT-READY**：CC-1 其实**已有 `_remount_skills` + 测试**；TG-1 还有第三套 `team_task_create`；CC-2 "禁 Edit/Write" 名不对（DeskPet 无此工具名）；HM-1 `ephemeral_subagent_model` 疑未真生效；测试点多已存在 | **R2 修订中**（本轮）|
| **R2** | gp 实读 spot-verify codex 全部 8 项 claim（全属实）+ 修订 02 + Lead 修 00 分类法 | CC-1 确认已实现+已测（推翻 R1 伪缺口判定）；TG-1 三套概念厘清；CC-2 真工具名 write_file/edit_file/run_shell/desktop_create_file + 拦截层；HM-1 揪出 `ephemeral_subagent_model` dead config 真 bug；OH-1/OH-2/OC-2 措辞收准 | ✅ 清空 BLOCKING/MAJOR；唯余 1 设计决策（TG-1 A/B）|

> **收敛趋势**：每轮都冒新坑但趋势收敛（符合 `feedback_codex_adversarial_plan_hardening`）。三个独立读码者（2 gp + codex）对「已实现 vs 真缺口」的判定已高度一致：**4 个真缺口 OH-4/CC-2/OC-1/OC-2 三方一致确认**。R2 后无 BLOCKING/MAJOR 未决，仅 TG-1 任务概念选型需 Lead 拍板（属产品/架构决策，非缺陷）。
>
> **附带产出（独立于本 plan 的 shipped 真 bug）**：`ephemeral_subagent_model`（`config.py:271` 配置项，默认 haiku + 白名单校验）**从未被消费** —— `main.py:936` 自我纠错 ephemeral verifier 直接用 `local_llm or cloud_llm`。已开独立任务跟踪修复。
