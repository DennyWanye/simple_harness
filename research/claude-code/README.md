# claude-code 调研

> 调研对象：anthropics/claude-code（官方文档 https://code.claude.com/docs，原 https://docs.claude.com/en/docs/claude-code 已 301 迁移到该域名）
> 调研日期：2026-06-04
> 调研目的：为 DeskPet「code 模式 / 帮用户完成目标」能力对标优化提供母版参考。
> 适用读者：DeskPet 后续优化的 Claude 子代理 / 工程师。

---

## 0. 一句话定位

claude-code 是 Anthropic 官方的 **agentic coding harness**（终端 CLI + IDE 插件 + Desktop + Web 多端同引擎）。它把「读代码库 → 规划 → 改文件 → 跑命令 → 验证 → 提交」封装成一套**可被用户深度定制**的 agent 循环，靠 **skills / hooks / slash commands / subagents / plan mode / permission modes / memory / MCP** 八大机制把「让 Claude 真把活干完」工程化。DeskPet 的 code 模式工作流（意图门→澄清→计划确认→执行→verify gate）正是它的简化变体。

---

## 1. 项目概览

| 维度 | 内容 |
|---|---|
| 本质 | AI 编码助手 / agentic harness，理解整个代码库，跨多文件多工具完成任务 |
| 形态 | Terminal CLI（全功能）、VS Code/JetBrains 插件、Desktop app、Web（claude.ai/code）、iOS。**同一底层引擎**，CLAUDE.md / settings / MCP 全端共享 |
| 典型能力 | 写测试并修失败、跨项目修 lint、解合并冲突、升依赖、查 bug 根因并修、生成 commit / PR、CI 自动 review |
| 可组合性 | 遵循 Unix 哲学，可 `claude -p "..."` 管道化（`tail -200 app.log \| claude -p "..."`），进 CI / GitHub Actions / GitLab CI |
| 多端协同 | Remote Control（手机续接本地会话）、`--teleport` web→terminal、`/desktop` 交接、Slack `@Claude`、Routines（云端定时）、`/loop`（会话内轮询） |
| 扩展生态 | skills / hooks / subagents / MCP / plugins / output-styles，可经 plugin / managed settings 组织级分发；Agent SDK 可自建 agent |

关键认知：**claude-code 的「智能」不只在模型，更在这套 harness 把模型行为「工程化约束」的能力**——permission 是客户端强制的、hook 是确定性 shell 拦截的、skill 是按需加载的。这正是 DeskPet 要对标的部分。

---

## 2. 核心架构与关键机制

### 2.1 Agent loop（基本循环）

单轮 = 用户 prompt → Claude 决策调工具（Read/Grep/Glob/Edit/Write/Bash/WebFetch/Task/Skill/MCP...）→ 工具结果回上下文 → 继续决策或回答。围绕这个循环挂了一圈**生命周期钩子点**（见 hooks），以及**上下文预算管理**（见 2.7）。

### 2.2 Skills（渐进式披露 progressive disclosure）★ 重点

skills 是 claude-code 最值得抄的机制，已成为开放标准 [Agent Skills](https://agentskills.io)。

- **结构**：每个 skill 是一个目录，入口 `SKILL.md`（YAML frontmatter + markdown 正文）；可附 `reference.md` / `examples.md` / `scripts/*.py`（脚本执行而非加载进上下文）。
- **渐进式披露三级**（核心思想——省 context）：
  1. **启动时**：只把所有 skill 的 **name + description**（合计按模型上下文 1% 预算，单条上限 1536 字符）加载进上下文，让 Claude 知道「有什么可用」。
  2. **触发时**：Claude 判断 description 匹配当前任务，或用户 `/skill-name` 显式调用 → 才把 **SKILL.md 正文**整段载入。
  3. **按需时**：正文里引用的 `reference.md` 等附件，Claude 用文件工具**临时读**，不常驻。
- **frontmatter 关键字段**：`description`（决定何时自动触发，关键词要写全）、`when_to_use`、`disable-model-invocation`（只许用户手动调，用于 deploy/commit 等副作用操作）、`user-invocable: false`（只许 Claude 调，用于背景知识）、`allowed-tools`（激活时免授权用的工具）、`disallowed-tools`、`model` / `effort`（临时切模型/算力）、`context: fork` + `agent`（在子代理隔离上下文里跑）、`paths`（按文件 glob 才激活）、`hooks`（skill 级钩子）。
- **动态上下文注入**：正文里 `` !`git diff HEAD` `` 在发给 Claude **之前**执行 shell，输出替换占位符——所以 skill 拿到的是「实时数据」而非命令本身。多行用 ```` ```! ````。
- **生命周期**：skill 一旦被调，正文作为一条消息**留在上下文直到会话结束**（不会每轮重读）→ 所以要写「常驻指令」语气而非「一次性步骤」。compaction 后按 25K token 预算重挂最近调用的 skill（每个保前 5000 token）。
- **custom commands 已并入 skills**：`.claude/commands/deploy.md` 和 `.claude/skills/deploy/SKILL.md` 都生成 `/deploy`，等价。
- **存放级别**：enterprise > personal(`~/.claude/skills/`) > project(`.claude/skills/`)；plugin 用 `plugin:skill` 命名空间不冲突。支持 live reload（改 SKILL.md 当场生效）、monorepo 嵌套目录自动发现。
- **bundled skills（内置但 prompt 驱动）**：`/run`（启动并驱动真实 app 看效果）、`/verify`（build+run 确认改动生效，**不退回测试/类型检查**）、`/run-skill-generator`（记录本项目的启动配方到 `.claude/skills/run-<name>/`）、`/code-review`、`/debug`、`/loop` 等。**`/run` + `/verify` 这对组合是「对着真实运行的 app 确认，而不是只看测试」——和 DeskPet 的「真 E2E ≠ 脚本回放」纪律是一回事。**

### 2.3 Hooks（确定性强制层）★ 重点

hook 是**用户定义的、在生命周期固定点自动执行**的 shell 命令 / HTTP / LLM prompt / MCP tool / subagent。**这是 CLAUDE.md/skill「软指令」之外唯一的「硬强制」层**。

- **事件**（按 cadence）：
  - 每会话：`SessionStart` / `SessionEnd`
  - 每轮：`UserPromptSubmit` / `Stop` / `StopFailure`
  - 每次工具调用：`PreToolUse` / `PostToolUse` / `PostToolUseFailure` / `PermissionRequest`
  - 还有 `InstructionsLoaded`（调试加载了哪些指令文件）
- **三级过滤**：event → matcher（按工具名/MCP server，如 `"Bash"`）→ handler（`if: "Bash(rm *)"` 用 permission 语法窄化）。
- **决策控制**：
  - exit 0 = 成功，解析 stdout JSON；**exit 2 = 阻断**（stderr 反馈给 Claude，动作被拦）；其他 = 非阻断错误。
  - JSON 精细控制：`permissionDecision: "allow"|"deny"|"ask"|"defer"`、`decision: "block"`、`additionalContext`（往 Claude 上下文注入环境状态）、`systemMessage`（给用户警告）。
- **典型用途**（DeskPet 已部分实现）：编辑后自动 format、commit 前跑 lint、`PreToolUse` 拦 `rm -rf`、`UserPromptSubmit` 校验生产部署需审批。
- **强制力**：hook 是 client 强制的，**优先于 permission allow 规则**（exit 2 在 permission 评估前就阻断）；deny/ask 规则又不被 hook 的 "allow" 绕过——deny-first 始终成立。
- **多层配置**：user / project(shared) / project(local) / managed policy / plugin / skill-agent frontmatter。`allowManagedHooksOnly` 让组织只跑受管 hook。

### 2.4 Slash commands / Skill 调用

`/` + 名字调用。内置命令（`/help` `/compact` `/init` `/permissions` `/memory` `/agents` `/model` 等）执行固定逻辑；bundled skills（标 Skill）是 prompt 驱动让 Claude 自己编排。skill 支持 `$ARGUMENTS` / `$0`/`$1` / 命名参数 / `${CLAUDE_SESSION_ID}` / `${CLAUDE_SKILL_DIR}` 等替换。

### 2.5 Subagents / Task（隔离上下文委派）★ 重点

子代理 = 独立 context window + 自定义 system prompt + 受限工具 + 独立权限。**核心价值：把「会污染主对话」的探索/日志/大文件读放进子上下文，只回summary**（文档例子：子代理读了 6100 token 文件，只回 420 token 结果）。

- **内置子代理**：
  - **Explore**：Haiku 驱动、**只读**（禁 Write/Edit）、专做代码搜索/理解；调用时指定 thoroughness（quick/medium/very thorough）。
  - **Plan**：plan mode 下做代码库调研、只读、继承主对话模型。
  - **general-purpose**：全工具、可探索+改动+多步。
  - 还有 statusline-setup / claude-code-guide 等。
  - **Explore/Plan 故意跳过 CLAUDE.md + git status** 以省上下文；其余子代理都加载。
- **委派机制**：Claude 按子代理 description 决定何时委派；**子代理不能再 spawn 子代理**（防无限嵌套）。
- **持久记忆**：子代理可有自己的 `~/.claude/agent-memory/`，跨会话累积 codebase pattern / 复发问题。
- **skill ↔ subagent 双向**：skill 加 `context: fork` 在子代理跑（SKILL.md 正文当 prompt）；或子代理配 `skills` 字段把 skill 当背景知识预载（启动时注入全文）。
- **多 agent 并行**：lead agent 协调、派子任务、合并结果（`/en/sub-agents`）；要跑多个**完整独立会话**并一屏监控用 background agents（`agent-view`）；要会话间通信用 **agent teams**。worktree 隔离（`claude --worktree feature-auth`）防并行编辑冲突。

### 2.6 Permission modes + permission rules（权限模型）★ 重点

**两层**：mode 设基线，rule 在基线上叠加 allow/ask/deny。

- **permission modes**（Shift+Tab 循环 default→acceptEdits→plan）：
  - `default`：只读免问，其余每次首用问。
  - `acceptEdits`：自动批工作目录内文件编辑 + 常见 fs 命令（mkdir/touch/mv/cp/rm/sed）。
  - **`plan`**：只读探索，**不改源文件**——见 2.8。
  - `auto`（研究预览）：免提示执行，但**另一个 classifier 模型逐动作审查**，拦超出请求范围/陌生基建/被恶意内容驱动的动作；对话里说「别 push」会被 classifier 当 block 信号（但 compaction 删了那条消息就失效→硬保证要写 deny 规则）。
  - `dontAsk`：只跑预批工具，其余自动拒（CI 用）。
  - `bypassPermissions`：全跳过（仅容器/VM）；`rm -rf /` / `~` 仍作 circuit breaker 提示。
- **rule 语法**：`Tool` 或 `Tool(specifier)`；评估顺序 **deny → ask → allow**，首个匹配胜，deny 永远优先。`Bash(npm run *)` / `Read(./.env)` / `WebFetch(domain:x.com)` / `Agent(Explore)` / `mcp__server__tool`。claude-code 懂 shell 操作符——`Bash(safe *)` 不会放行 `safe && evil`，每个子命令独立匹配。
- **protected paths**：`.git` / `.claude` / shell rc / `.mcp.json` 等，除 bypass 外永不自动批。
- **强制性声明**：「permission 由 Claude Code（客户端）强制，不是模型。CLAUDE.md/prompt 只塑造 Claude **想**做什么，不改变**允许**做什么。」——这句是 DeskPet 工具层权限设计的核心原则。
- **settings 优先级**：managed > CLI args > local project > shared project > user。任一层 deny 不可被任何层 allow 覆盖。

### 2.7 Context 管理 / compaction（上下文预算）★ 重点

每会话从空白上下文起，靠两条线续命：CLAUDE.md（你写）+ auto memory（Claude 写）。上下文随读文件/工具结果膨胀，逼近 200K 时触发压缩。

- **启动加载顺序**（约前 20% 是 startup）：system prompt(~4.2K) → auto memory MEMORY.md(前 200 行/25KB) → 环境信息 → MCP 工具名(schema 默认 deferred，按需 tool-search 加载) → CLAUDE.md → rules → skill 索引。
- **auto-compaction**：把逐字对话换成**结构化 summary**。summary **保留**：「你的请求与意图、关键技术概念、查看/改动过的文件及重要代码片段、错误及修复方式、**待办任务（pending tasks）**、当前工作」；**丢弃**：完整工具输出、中间推理、读过的精确代码。
- **什么扛过 compaction**：project-root CLAUDE.md 会 compaction 后**从磁盘重读重注入**；子目录嵌套 CLAUDE.md 不自动重注（下次读该目录文件时才回来）；只在对话里给过的指令会丢→所以要写进 CLAUDE.md。
- **微压缩 / `/compact`**：手动 `/compact` 立即结构化总结。skill 内容按 25K 预算重挂。
- **任务跟踪**：长任务的「待办」被显式列为 compaction **保留项**之一——即 claude-code 用一份 TODO/task 清单贯穿长会话，压缩时优先保住「还没做完的事」，这是它「不会做着做着忘了目标」的机制基础。

### 2.8 Plan mode（先规划后执行）★★ 重点中的重点

plan mode 是 claude-code「避免乱改、先对齐再动手」的核心闸门，**DeskPet 的「计划确认」直接对标它**。

- **行为**：Claude 读文件、跑只读命令探索、**写出一份 plan，但绝不改源文件**。进入：Shift+Tab 或单轮前缀 `/plan` 或 `claude --permission-mode plan`。
- **审批闸**：plan 就绪 → Claude 呈现并问怎么继续，用户可选：
  1. 批准并进 auto 模式
  2. 批准并 acceptEdits
  3. 批准并逐条人工 review 每个编辑
  4. 带反馈继续规划
  5. 用 Ultraplan 浏览器化 review
- 批准即退出 plan mode 并切到对应权限模式开始改；`Ctrl+G` 可在文本编辑器里直接改 plan 再放行；批准时还能选先清规划上下文。可设 `defaultMode: "plan"` 让某项目默认先规划。
- **设计意图**：把「探索/对齐」和「执行」物理分离——只读阶段不可能误改，用户在动手前看到完整计划。Plan 子代理在此阶段做调研。

### 2.9 Memory / CLAUDE.md（跨会话记忆）

- **两套互补**：CLAUDE.md（你写规则）+ auto memory（Claude 写 learnings）。两者会话启动都加载，但**都是 context 不是强制配置**——要硬拦用 hook。
- **CLAUDE.md 层级**（按 load 顺序，广→窄）：managed policy → user(`~/.claude/CLAUDE.md`) → project(`./CLAUDE.md` 或 `.claude/CLAUDE.md`) → local(`CLAUDE.local.md`，gitignore)。沿目录树向上 walk 全部 concat；子目录的按需加载。
- **写法纪律**：单文件 < 200 行（越长 adherence 越差）；具体可验证（"用 2 空格缩进" 而非 "格式化好"）；用 `@path` import（但 import 仍启动时全载，不省 context）；HTML 注释 `<!-- -->` 会被剥离不耗 token。
- **`.claude/rules/`**：拆分指令到多文件；`paths:` frontmatter 让规则**只在 Claude 碰匹配文件时才载**（path-specific rules，省 context）。
- **auto memory**：Claude 自动存 build 命令/debug 洞察/偏好到 `~/.claude/projects/<project>/memory/MEMORY.md`（前 200 行/25KB 每会话载，topic 文件按需读）；按 git repo 隔离、跨 worktree 共享、machine-local。`/memory` 查看编辑。
- **AGENTS.md 兼容**：claude-code 只读 CLAUDE.md，但可 `@AGENTS.md` import 复用；`/init` 会读 AGENTS.md/.cursorrules/.windsurfrules 并合成 CLAUDE.md。

### 2.10 MCP 集成

Model Context Protocol = 连外部数据源/工具的开放标准（Google Drive 设计文档、Jira 工单、Slack、自定义工具）。MCP 工具默认只列名、schema deferred、按需 tool-search 加载（省 context）。权限用 `mcp__server__tool` 规则控制。`@server:resource` 可在 prompt 里引 MCP 资源。

---

## 3. 「帮用户完成目标」的机制（重点：避免假完成）

这是 DeskPet 最该学的部分。claude-code 用**五道闸 + 一条原则**保证「真把事办成」：

1. **Plan mode 先对齐**（闸 1，规划期）：探索与执行物理分离，只读阶段不可能误改；用户在动手前批准完整计划。→ 杜绝「方向错了还一路猛改」。

2. **TODO/pending tasks 贯穿长会话**（闸 2，记忆期）：待办任务是 compaction 的**显式保留项**，长任务压缩后仍记得「还没做完什么」。→ 杜绝「做着做着忘了目标 / 漏做子任务」。

3. **`/verify` + `/run` 对真实 app 确认**（闸 3，验证期）★：`/verify` 明确「build and run，确认改动做到了该做的，**不退回 tests 或 type checks**」；`/run` 启动并驱动真实 app 看效果；`/run-skill-generator` 记录本项目启动配方让验证可复现。→ 这就是「真 E2E ≠ 脚本回放 ≠ 单测绿」的工程化，**和 DeskPet CLAUDE.md 的 HARD CONSTRAINT 同源**。

4. **Hooks 确定性强制**（闸 4，执行期）：`Stop`/`PostToolUse` hook 可在「Claude 想收尾」时跑 lint/test，exit 2 阻断让它「没过门就别说完成」。permission deny + hook 比软指令可靠。→ 把「质量门控」从「希望 Claude 自觉」变成「客户端强制」。

5. **Auto mode classifier 审查**（闸 5，自治期）：免提示执行时，独立 classifier 逐动作审「是否超出用户请求、是否被恶意内容驱动」；用户口头边界（"别 push"）被当 block 信号；子代理在 spawn 前 / 运行中 / 返回时三点受审。→ 自治不等于放任。

**贯穿原则**：**软指令（CLAUDE.md/skill）塑造意图，硬机制（permission/hook/classifier/client 强制）保证边界**。文档反复强调「这些是 context 不是 enforcement，要硬保证用 hook/deny rule」——即 claude-code 不相信「模型说做完了就是做完了」，凡是要保证的就上确定性机制。

辅助：`@file`/`!command`/`/compact` 让用户把真实命令输出 ground 进上下文；worktree 隔离防并行污染；PR/commit 自动 review 兜底。

---

## 4. 对 DeskPet 的可借鉴点（逐条：借鉴什么 + 为什么 + 现状差距）

> DeskPet 已抄了一部分（skill 系统/工具层权限/熔断/last-mile verify gate/slash/`/goal`/多 agent/code 模式），下面重点指**没抄到位 / 可深化**的。

### 4.1 ★★ Skills 的「渐进式披露三级」做彻底
- **借鉴**：启动只载 name+description（带字符预算），触发载正文，附件按需读；正文常驻+compaction 重挂预算。
- **为什么**：DeskPet 桌宠是常驻进程、语音交互上下文窗口宝贵，14 个 builtin skill 若全文常驻会挤爆 context。三级披露是「skill 多但不爆 context」的唯一解。
- **现状差距**：DeskPet 有 SkillLoader + 14 builtin，但需自查：(a) 是否启动只注入 description 而非全文？(b) 有没有「单条 description 字符上限 + 总预算 + 溢出时丢最少用的」机制？(c) skill 附件（reference/scripts）是否按需读而非常驻？(d) compaction 后 skill 是否按预算重挂？这几点是 claude-code 的精髓，DeskPet 大概率只做了「能加载」没做「省 context 的分级」。

### 4.2 ★★ Plan mode 作为独立权限模式（而非仅一个流程步骤）
- **借鉴**：把「计划确认」实现为**只读权限模式** + 五选一审批闸（批准并自动/批准并逐条 review/带反馈继续规划/...），而非一句「确认吗 Y/N」。
- **为什么**：DeskPet code 模式已有「计划确认」，但若只是流程提示、底层权限没切只读，Claude 仍可能在规划期就改文件。claude-code 用 permission mode 物理保证「规划期不可能误改」。
- **现状差距**：DeskPet 需确认计划确认期是否**真切只读权限**（探索期禁 Edit/Write），以及审批是否提供「批准后用何种执行模式」的多选（影响后续是逐条确认还是放手干）。

### 4.3 ★ `/verify` + `/run` 风格的「对真实运行确认」内建 skill
- **借鉴**：做一个 DeskPet 版 `/verify`——「启动真实桌宠/真实 app，确认改动生效，明确不退回单测」，并配 `run-skill-generator` 式的「记录本项目启动配方」。
- **为什么**：DeskPet CLAUDE.md 已有「真 E2E ≠ 脚本回放」HARD CONSTRAINT，但那是**给 Claude 的纪律文字**；claude-code 把它**固化成可调用的 bundled skill + 可复现配方**，纪律变工具，不靠自觉。
- **现状差距**：DeskPet 的 last-mile 有 artifact/receipt/verify gate（后端层），但缺一个「面向真实 GUI 运行的 `/verify` skill + 启动配方记录」。可把现有 windows-mcp 真测经验（`UI_AUTOMATION_BREAKTHROUGH.md` 的 SendInput 圣杯）沉淀成 `run-deskpet` skill。

### 4.4 ★ Hooks 作为「质量门控」的确定性强制层
- **借鉴**：把「编辑代码必须跑测试」从 CLAUDE.md 软指令 → `PostToolUse(Edit)` / `Stop` hook，exit 2 阻断「没跑测试就收尾」。
- **为什么**：claude-code 反复强调「CLAUDE.md 是 context 不是 enforcement」。DeskPet 全局规范里大量「必须跑测试/必须真测」靠 Claude 自觉，**短路径偏置**下极易被绕过——hook 是唯一确定性兜底。
- **现状差距**：DeskPet 的 codingsys 已有 5 个 hook（危险拦截/质量门控/类型检查/日志/语音），方向对了；可深化的是 claude-code 的 **exit 2 阻断 + JSON `additionalContext` 注入** 精细控制，以及 `Stop`/`StopFailure` 事件（收尾时强制验证），而非只在 PreToolUse 拦危险。

### 4.5 ★ Subagent 的「只回 summary」上下文隔离 + 内置 Explore/Plan
- **借鉴**：探索/大文件读派给只读子代理（Explore 等价物），主对话只收 summary；子代理不能再嵌套。
- **为什么**：桌宠语音对话上下文极宝贵，「查代码库」这类一次性读不该污染主对话。
- **现状差距**：DeskPet 有多 agent team，但需确认是否有**「只读 Explore 子代理 + 只回 summary」的轻量委派**（区别于重量级 Lead-Expert 并行）。日常「调研一下 X」应走轻量 Explore，而非每次都开 worktree 并行。

### 4.6 ★ auto memory（Claude 自写 learnings）+ MEMORY.md 索引预算
- **借鉴**：除用户写的 CLAUDE.md，让桌宠**自动**把「踩过的坑/build 命令/用户偏好」写进 per-project MEMORY.md（前 N 行每会话载，topic 文件按需读）。
- **为什么**：DeskPet 项目 CLAUDE.md 的「踩过的坑」9 条是**人肉维护**的；claude-code 让 Claude 自己积累，减人工。DeskPet 已有 `~/.claude/projects/.../memory/MEMORY.md`（见本会话的 user memory），但那是 **codingsys 全局**的，不是 **DeskPet 桌宠运行时**给终端用户用的。
- **现状差距**：DeskPet 桌宠产品本身（给大众用户）应该有自己的轻量 auto-memory，记住「这个用户常让我做 X / 上次 PPT 风格」，而非每次冷启动。这是「更好帮用户完成目标」的长期记忆抓手（DeskPet 有 BGE-M3 长期记忆，可与之结合）。

### 4.7 「软指令塑造意图 / 硬机制保证边界」的总原则
- **借鉴**：审视 DeskPet 每条「必须 X」——能确定性强制的（权限/hook）就别只写进 prompt。
- **为什么**：这是 claude-code 全套设计的元原则，也是 DeskPet 反复「假完成 / 走捷径」的根因（LLM 短路径偏置）。
- **现状差距**：DeskPet CLAUDE.md 有大段 HARD CONSTRAINT 文字（手工测试纪律），但**文字约束 ≠ 强制**。值得逐条问「这条能不能变成 hook / 权限 / verify gate」。

---

## 5. 局限 / 不适用（claude-code 是开发者 CLI，DeskPet 是大众桌宠）

1. **目标用户反转**：claude-code 面向**开发者**，用户懂 permission mode / settings.json / shell。DeskPet 面向**大众**，绝不能要求用户配 `.claude/settings.json` 或理解 deny-first 规则。→ permission/hook 机制可学**内核**，但 UI 必须隐藏，默认值要替用户兜好。
2. **权限粒度过细**：claude-code 的 `Bash(git * main)` glob、protected paths、6 种 mode 是给专业开发者的安全护栏。DeskPet 是**单机桌宠**，项目 memory 明确「不要加沙箱护栏，只防手滑级破坏」（feedback_no_sandbox_constraints）。→ **不要照搬细粒度权限系统**，会过度工程化。
3. **代码库中心假设**：claude-code 一切围绕「一个 git 仓库」（CLAUDE.md walk 目录树、worktree、PR）。DeskPet 帮用户做的是**生活/办公任务**（生成 PPT、查资料、语音陪伴），没有「仓库根」概念。→ memory/skill 的「项目」边界要重新定义（可能是「用户」或「任务类型」而非「repo」）。
4. **交互模态不同**：claude-code 是文本 CLI / 编辑器，有 Shift+Tab 切模式、`Ctrl+G` 编辑 plan。DeskPet 是**语音 + Live2D**，「呈现 plan 让用户五选一审批」要重新设计成语音可达的交互（不能弹一大段文本让用户选 1-5）。
5. **auto mode classifier 成本**：每动作一次 classifier 调用、加 round-trip、计 token。DeskPet 本地优先 + 走中转站，这种「每步再调一次模型审查」成本/延迟对实时语音桌宠太重。→ 可借鉴**思路**（自治不放任），但实现要轻量化（规则优先、关键动作才审）。
6. **多端/云基建**：Remote Control / Routines / Web session / agent teams 是 Anthropic 托管基建支撑的，DeskPet 单机产品大多不适用。

---

## 6. 关键引用

- 概览：https://code.claude.com/docs/en/overview
- Skills（渐进式披露/frontmatter/动态注入/bundled `/run` `/verify`）：https://code.claude.com/docs/en/skills
- Hooks（事件/exit 2 阻断/JSON 决策）：https://code.claude.com/docs/en/hooks
- Subagents（Explore/Plan/general-purpose/隔离上下文/委派）：https://code.claude.com/docs/en/sub-agents
- Permissions（rule 语法/deny-first/managed/强制性声明）：https://code.claude.com/docs/en/permissions
- Permission modes（plan mode 五选一审批/auto classifier/protected paths）：https://code.claude.com/docs/en/permission-modes
- Memory（CLAUDE.md 层级/auto memory/`.claude/rules/` path-scoped）：https://code.claude.com/docs/en/memory
- Context window（启动加载/auto-compaction 保留项含 pending tasks/什么扛过压缩）：https://code.claude.com/docs/en/context-window
- Common workflows（plan/subagent 委派/test 验证 recipe/管道化）：https://code.claude.com/docs/en/common-workflows
- Agent Skills 开放标准：https://agentskills.io
- 文档总索引（发现全部页面）：https://code.claude.com/docs/llms.txt
