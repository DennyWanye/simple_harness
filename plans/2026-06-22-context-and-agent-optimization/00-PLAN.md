# DeskPet 优化总规划 — 上下文管理 + 对标系统升级（2026-06-22）

> **状态**: DRAFT v0.1（待子代理多轮对抗硬化到 EXECUTABLE-AS-IS）
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

### 3.1 openhuman → 记忆工程（实读后：核心已实现，补边角）
- WI-OH-1 五路混合检索 — ✅ **已实现**（`retriever.py:226` 4 路 RRF + `enhanced_retriever.py` 叠 facts/rerank/chunk/rewrite）→ **降级为「确认默认开 + 补 freshness 权重可调」或删除**
- WI-OH-2 PROFILE 人格半衰期 — 🟡 **核心已实现**（`facts.py:_CATEGORY_DECAY` + `set_pinned:882` + `preference_profile.py` 📌置顶注入）→ **仅补 PreferenceMemory JSON 衰减 + 对话式 Pin/Forget 入口**
- WI-OH-3 记忆写入分级 — 🟡 部分 → 补 light 路径（高频流跳 embedding）`memory/manager.py`
- WI-OH-4 记忆 self-curation nudge — 🔴 **真缺口**：agent_loop 周期性让 agent 自决该不该记

### 3.2 hermes → 自我纠错闭环（实读后：已完整实现）
- WI-HM-1 agentic JSON-mode 自我纠错 — ✅ **已完整实现**（`reflection.py:StructuredReflection`=error_analysis/critique/replan + `agent_loop.py:1395+` verify 守门 + stagnation difflib>0.85 检测 + ephemeral 升级 + `verify_gate.py:GoalAlignment` 重述原目标对照）→ **降级为「点亮 flag + 补观测 + 真机验证闭环真生效」**
- WI-HM-2 技能自创产可执行 — 🟡 已有独立 LOCKED plan → **引用 [plans/2026-06-22-skill-executable-function-call](../2026-06-22-skill-executable-function-call/)，不重复造**

### 3.3 claude（Claude Code）→ harness 机制补深
- WI-CC-1 skills 三级渐进披露 — 🟡 **大部分已实现**（`skill.py` auto_disclosure embedding 强匹配+body inline+预算+LRU；`loader.py:read_body`）→ **仅剩 compaction 后重挂一项**（⚠️ 接线状态需复核 `context_manager.py`，01/02 报告口径冲突）
- WI-CC-2 plan mode 只读权限模式 — 🔴 **真缺口**（`code_mode/` grep 0 命中只读权限切换）：规划期物理禁 Edit/Write
- WI-CC-3 `/verify`+`/run` bundled skill — 🟡 last-mile 已有 → 补一个面向真实桌宠运行验证的 skill + 启动配方
- WI-CC-4 hooks exit-2 — ❌ **不引入通用 hook**：DeskPet 运行时无 hook 层，已有 verify-gate end_turn 守门即等价物（避免过度工程）
- WI-CC-5 auto-memory — 🟡 L1 文件记忆/pre-flush 已有 → 评估是否补面向终端用户的轻量 auto-memory（低优先）

### 3.4 openclaw → 子代理调度打磨（8 模式已实现，仅余补强）
- WI-OC-1 depth 计数真生效 — 🔴 **真缺口**（现仅「剥 spawn 类工具」守门，无显式 depth 数值上界）
- WI-OC-2 背压/lane 指标可观测 — 🔴 **真缺口**（调度器无累计 metrics）→ 埋点 + 前端进度面板已有可承接

### 3.5 cc-haha → Task 任务图 + 审批聚合
- WI-TG-1 Task 结构化任务图 — ✅ **已实现且持久化**（`task_graph.py:TaskGraphStore` DAG+claim_ready + session_db `goal_tasks`/`session_goals` 落库）→ **仅补 goal_store 内存态落库一致性 + LLM `task_create` 工具暴露**（⚠️ 工具是否已暴露待核实）
- WI-TG-2 前端审批 UX 聚合视图 — 🟡 ⚠️ 前端实现度未读，落地前核实

---

## 4. 分期与依赖（草案，待 03 细化）

> 校准后分期：方向二多为「点亮 flag + 补观测」（轻），真新建只有 4 个缺口。

| Phase | 内容 | 依赖 | 价值 |
|---|---|---|---|
| **P0（立即，低风险，直接回应用户）** | 1A 全部（工作环境瘦身，缓解圈圈）+ 1B-1（token 收敛，仅 2 处）+ 1B-2（压缩可观测）| 无 | 直接缓解「圈圈满」+ 工程整洁 |
| **P1（点亮已实现的护城河 + 补观测）** | HM-1 点亮+观测 / OH-1 确认默认开 / OH-2 补 Pin 入口 / TG-1 补工具暴露 / CC-1 复核 compaction 重挂 | 先做现状复核（解 ⚠️） | 把已建能力真正用起来，低成本高收益 |
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

## 7. 待 review 的开放问题（交付时与用户确认）

1. **「圈圈」歧义**：用户指的是 **Claude Code 工作环境**（1A）还是 **DeskPet 运行时**（1B）？本 plan 两层都覆盖，review 时确认侧重。
2. **范围取舍**：方向二 5 个对标系统全做工程量巨大；是否按 P0→P3 分期，还是某几个系统优先？
3. **1A 是否落仓库**：Claude Code 工作环境优化（CLAUDE.md 瘦身 / MCP 裁剪）是否要写成 repo 内可复用的配置/脚本，还是仅给操作 SOP？
