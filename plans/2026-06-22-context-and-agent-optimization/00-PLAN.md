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

### 3.1 openhuman → 记忆工程深化（伴侣感命脉）
- WI-OH-1 五路混合检索：在 `memory/retriever.py` 现有 BGE-M3 向量基础上，加 keyword/episodic/freshness 信号加权融合
- WI-OH-2 PROFILE 人格半衰期：新建/扩展 `memory/personalization.py`，偏好 7-90 天衰减 + Pin/Forget
- WI-OH-3 记忆写入分级：`memory/manager.py` 加 light 路径（高频流跳 embedding）
- WI-OH-4 记忆 self-curation nudge：agent_loop 周期性让 agent 自决该不该记

### 3.2 hermes → 自我纠错闭环（从「发现问题」到「自动补救」）
- WI-HM-1 agentic JSON-mode：verify gate 校验不过时强制产 `error_analysis / critique / replan` 结构化字段 → 自动重规划重试（`agent/agent_loop.py` + `verify/verify_gate.py`）
- WI-HM-2 技能自创产可执行：`skills/skill_codifier.py` 现仅产 Markdown body，扩展到可产 function-call 技能（与 plans/2026-06-22-skill-executable-function-call 对齐）

### 3.3 claude（Claude Code）→ harness 机制补深
- WI-CC-1 skills 三级渐进披露彻底化：启动只注入 name+description（字符预算）+ 触发载正文 + 附件按需 + compaction 后按预算重挂（`skills/skill_loader.py`）
- WI-CC-2 plan mode 只读权限模式：规划期物理禁 Edit/Write（permission mode 切只读），非仅流程提示
- WI-CC-3 `/verify`+`/run` bundled skill：面向真实桌宠/app 运行验证改动生效
- WI-CC-4 hooks exit-2 确定性强制深化：Stop/PostToolUse hook 阻断「没验证就收尾」
- WI-CC-5 auto-memory：让 agent 自动积累用户偏好/踩坑到 per-project 轻量 memory

### 3.4 openclaw → 子代理调度打磨（8 模式已实现，仅余补强）
- WI-OC-1 depth 计数真生效（递归守门从「剥工具」升到「显式 depth 上界」）
- WI-OC-2 背压/lane 指标可观测（调度器埋点 → 前端进度面板已有，补 metrics）

### 3.5 cc-haha → Task 任务图 + 审批聚合
- WI-TG-1 Task 结构化任务图持久化：`task/` 暴露 TaskCreate/Update/List/Get，带依赖、跨子 agent 共享，落盘
- WI-TG-2 前端审批 UX 聚合视图

---

## 4. 分期与依赖（草案，待 03 细化）

| Phase | 内容 | 依赖 | 价值 |
|---|---|---|---|
| **P0（立即，低风险）** | 1A 全部（工作环境瘦身）+ 1B-1（token 收敛）+ 1B-2（可观测）| 无 | 直接缓解「圈圈满」+ 工程整洁 |
| **P1（高杠杆差异化）** | 3.1 记忆深化（OH-1~4）+ 3.2 自我纠错（HM-1）| 无强依赖 | 伴侣感 + 完成质量 |
| **P2（harness 补深）** | 3.3 claude 机制（CC-1~5）+ 3.5 Task 图（TG-1/2）| CC-1 依赖 skill_loader 现状核实 | 长期复利 |
| **P3（打磨）** | 3.4 openclaw 补强（OC-1/2）+ 1B-3/4/5 + 3.2 HM-2 | 子代理驱动已 ship | 锦上添花 |

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
