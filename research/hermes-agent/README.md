# hermes-agent 调研

> 调研日期：2026-06-04 ｜ 对标产品：DeskPet（本地桌面语音宠物）
> 仓库：https://github.com/NousResearch/hermes-agent
> 深度参考：DeepWiki / Hermes-Function-Calling 数据集 / 官方 docs

---

## 1. 项目概览

- **是什么**：Nous Research 出品的「自我进化型（self-improving）通用 AI Agent 框架」，
  对外定位 *"The agent that grows with you"*。核心卖点是一个**内建学习闭环**：
  从经验中创造技能（skill）→ 使用中改进技能 → 周期性 nudge 自己持久化知识 →
  搜索自己过往对话 → 跨会话构建对用户的深度模型。
- **谁做的**：Nous Research（开源 LLM 社区，以 Hermes 系列模型 + function-calling 数据集闻名）。
- **定位**：不是单纯 chatbot，而是一个**可跨平台部署 + 跨会话记忆 + 自主造工具**的 agent 运行时。
  既能纯 CLI/TUI 跑，也能接 Telegram/Discord/Slack/WhatsApp/Signal/Email 等多通道。
- **量级 / 活跃度**：GitHub 18 万+ star、3 万+ fork、1310 contributors（注：该数字疑似抓取夸大，
  实际以仓库为准）；最新 release v0.15.2（2026-05-29），16 个 release，持续活跃。
  代码 84% Python + 12% TypeScript。
- **许可证**：MIT。
- **技术形态对比 DeskPet**：Hermes 是「**云/服务器侧 + 多通道 messaging**」型 agent；
  DeskPet 是「**本地单机 + Tauri 桌面 GUI + 全本地语音管线**」型桌宠。
  二者 agent loop / 工具调用 / 记忆 / 技能 这几层高度可对标，**部署形态不同**。

---

## 2. 核心架构与关键设计

### 仓库结构
```
/agent       核心 agent 逻辑（run_agent.py AIAgent、memory_manager.py、prompt_builder.py）
/skills      程序性记忆系统（agent 自己造的 Python 技能）
/tools       40+ 内建工具实现
/providers   LLM provider 接入（OpenAI/Anthropic/OpenRouter/Nous Portal，200+ 模型）
/plugins     扩展层
/gateway     多平台 messaging 路由
/web         dashboard
/ui-tui      终端 UI（多行编辑 + slash 命令补全 + 流式工具输出）
```
关键文件：`run_agent.py`（主循环）、`cli.py`、`hermes_state.py`（会话状态）、
`model_tools.py`（工具调用框架 / 注册表）、`toolsets.py`（工具分组分发）。

### Agent Loop（`run_agent.py` 的 `AIAgent`）
一个**迭代到任务完成或预算耗尽**的对话循环，把「自然语言空间（用户意图）」
桥接到「代码实体空间（工具执行）」。每轮：
1. 把当前 context 发给 LLM
2. 收到 tool calls
3. 经 `handle_function_call` 执行工具、把结果塞回 context
4. 重复，直到 agent **显式 signal 完成**

带 **iteration budget**（迭代预算）防死循环，会话状态持久化到 `~/.hermes/sessions/`。

### 执行环境抽象（亮点）
terminal backend 把工具执行从「逻辑」解耦出「位置」：Local / Docker / SSH / Modal /
Daytona 都可在 `config.yaml` 的 `terminal` 块配置。serverless 后端 idle 时 hibernate。

### Function-Calling 格式（Hermes 的招牌，ChatML + XML 标签）
Hermes 系列模型用一套**非常清晰、可被任何模型复现**的 function-calling 约定：

**System 段**：把工具签名放进 `<tools></tools>`，并强制 LLM 用固定 pydantic schema：
```
You are a function calling AI model. ... Here are the available tools:
<tools> [{"type":"function","function":{"name":"...","description":"...","parameters":{...}}}] </tools>
Use the following pydantic model json schema for each tool call:
{"title":"FunctionCall","properties":{"name":{...},"arguments":{...}},"required":["name","arguments"]}
For each function call return a json object ... within <tool_call></tool_call> XML tags
```

**Assistant 发起调用**：
```
<tool_call>
{"name": "get_stock_fundamentals", "arguments": {"symbol": "TSLA"}}
</tool_call>
```
**工具回灌结果**（`tool` 角色 + `<tool_response>`）：
```
<tool_response>
{"name":"get_stock_fundamentals","content":{"symbol":"TSLA", ...}}
</tool_response>
```
角色用 ChatML `<|im_start|>system/user/assistant/tool<|im_end|>` 分隔。
另有 **JSON-mode**（裸 JSON，按 `<schema>` 输出）和 **agentic JSON-mode**（见 §3）。

### 记忆系统
- **持久事实**：`MEMORY.md` + `USER.md`，由 `agent/memory_manager.py` 管理，每次构建 context 时载入。
- **会话搜索**：SQLite **FTS5 全文搜索** + LLM 摘要，可检索历史会话。
- **用户建模（Honcho，可选）**：dialectic 框架，跨会话、12 层身份维度建模用户与 agent 的关系。
- **技能记忆**：兼容 agentskills.io 开放标准。

### System Prompt / persona 工程
身份/人格存 `SOUL.md`（OpenClaw 迁移时一并导入）；`prompt_builder.py` 把
身份 + 当前 context + 记忆 + 可用工具拼成每次请求。`/personality [name]` 切角色指令集。

---

## 3. 「帮用户完成目标」的机制（重点）

这是 DeskPet 最该看的部分。Hermes 在「真正办成事」上有 5 个互相咬合的机制：

### (1) 迭代循环 + 显式完成信号 + 预算护栏
循环**不是固定步数**，而是「跑到 agent 自己 signal 完成」，同时 iteration budget
兜底防止无限循环。→ 保证「没办完不会提前收手」，又不会卡死。

### (2) Agentic JSON-Mode：把「自我纠错链」写进结构化 schema（★最值得借鉴）
Hermes 训练数据里有一类专门的 agentic schema，强制 agent 在一次「执行思考」里
显式产出**七段反思**：
```json
"agent_execution": {
  "task_decomposition":  "拆解目标",
  "action_retrieval":    "选用哪个工具/动作",
  "code_execution":      "执行",
  "error_analysis":      "分析错误",
  "code_amendment":      "修订代码/参数",
  "execution_critique":  "对执行结果自我批判",
  "task_replanning":     "据批判重新规划"
}
```
这把「规划→执行→**错误分析→修订→自我批判→重规划**」做成了**模型必须填的字段**，
而不是寄希望于模型自发反思。这是 goal-completion 的核心抓手。

### (3) 技能自创闭环（learning loop）
任务完成后 agent 通过 `skill_manage` 工具**自我评估这条路径值不值得固化成技能**，
触发条件明确：① ≥5 次工具调用 ② 从错误中恢复过 ③ 被用户纠正过 ④ 非显然但有效的 workflow。
满足就把该流程**codify 成可复用的 Python 技能**，下次同类目标直接调用 → 越用越能办成事。

### (4) 周期性记忆 nudge（自决定记什么）
不是「全记」也不是「不记」，而是给 agent 一个**内部 system-level prompt**周期性追问：
「回看刚发生的，有什么值得持久化？」→ agent 自己判断写不写 `MEMORY.md`。
对长期目标推进很关键：跨会话不丢上下文。

### (5) Honcho dialectic = 链式自我批判
用户建模本身就是 **Initial Assessment → Self-Audit → Reconciliation** 三段串行推理，
即「自我批判链」用于提升建模质量。验证 / 自审思想贯穿全栈。

### (6) 中断 / 恢复 + cron 无人值守
会话持久化到 `~/.hermes/sessions/`，可从最后一个 verified state 恢复；
cron 调度支持定时/周期任务，无需用户在场也能推进目标。
还支持 **parallel subagent spawning** 把工作流拆给子 agent 并行做。

---

## 4. 对 DeskPet 的可借鉴点（逐条，具体到机制）

> 标注：**借鉴什么 + 为什么 + DeskPet 现状差距**

### 借鉴点 1 ★：把「自我纠错链」固化进结构化 schema（agentic JSON-mode）
- **借鉴什么**：让 DeskPet 的 agent loop 在每个执行步强制产出
  `error_analysis / execution_critique / task_replanning` 等字段，而非自由文本反思。
- **为什么**：DeskPet 核心诉求是 goal-completion。当前 last-mile 有 verify gate / receipt，
  偏「**产物层校验**」（artifact 生成对不对）；Hermes 这套是「**过程层反思**」
  （执行偏离目标时主动重规划）。两者互补——产物校验失败时，结构化的
  `task_replanning` 字段能驱动 agent 自动改方案重试，而不是直接报错给用户。
- **现状差距**：DeskPet 有 verify gate（结果校验），但缺「失败后强制结构化重规划」的循环字段；
  目前更像「校验不过就停」，缺 Hermes 的「校验不过→error_analysis→amendment→replan→retry」。

### 借鉴点 2 ★：技能自创闭环的「值不值得固化」触发器
- **借鉴什么**：DeskPet 已有 SkillLoader + 14 builtin skill，但技能是**人写的**。
  借鉴 Hermes 的**自动固化触发器**（≥5 工具调用 / 从错误恢复 / 被用户纠正 / 非显然 workflow）——
  让桌宠在完成一个多步目标后，自评这条路径要不要存成新技能。
- **为什么**：桌宠帮用户办成同类事（如「每周生成 PPT 周报」）应越用越顺。
  把成功路径固化能把「多步即兴」变成「一键技能」，直接提升后续 goal-completion 成功率与速度。
- **现状差距**：DeskPet 技能是静态注册，无「从经验自动造技能」闭环。需要先有
  **路径录制**（哪些工具按什么顺序调用 + 成功判定），再加固化触发器。

### 借鉴点 3：周期性记忆 nudge（agent 自决定记什么）
- **借鉴什么**：DeskPet 已有 BGE-M3 向量记忆 + 自动总结 + 事实抽取，
  但抽取偏「被动/规则触发」。借鉴 Hermes 的**主动 nudge**——周期性给 agent 一个
  内部 prompt：「回看刚发生的，有什么值得长期记住？」由 LLM 自己决定。
- **为什么**：规则抽取容易漏掉「非显然但重要」的信息（用户偏好、纠正、隐含目标）。
  让 agent 自决能抓到对长期目标推进有用、但规则覆盖不到的事。
- **现状差距**：DeskPet 是「自动总结+事实抽取」管线，更像 batch 抽取；
  缺一个「agent 主动反思该不该记」的 self-curation 环节。可作为现有 memory 管线的补充层。

### 借鉴点 4：iteration budget + 显式完成信号的循环纪律
- **借鉴什么**：Hermes 循环「跑到 agent signal 完成」+ budget 兜底。
- **为什么**：DeskPet 桌宠要「真把事办成」，不能固定步数就收。
  显式完成信号让没办完时继续，budget 防止本地资源被无限工具调用吃满。
- **现状差距**：DeskPet 有 /goal + 多 agent team + code 模式（意图门→澄清→计划→执行→verify gate），
  流程已不错；可检查 agent loop 是否有清晰的「完成信号 vs 预算耗尽」二分，
  以及预算耗尽时是否优雅降级（告诉用户进度 + 留可恢复 state）。

### 借鉴点 5：中断/恢复 + 从「最后 verified state」续跑
- **借鉴什么**：会话状态持久化到磁盘，可从最后一个已验证状态恢复。
- **为什么**：桌宠长任务（生成大文档、多步自动化）中途被关/崩溃，重启应能续，
  而不是从头来。对 goal-completion 体验是刚需。
- **现状差距**：DeskPet 有 control_ws 快照（最近修过），但需确认**长任务级**的
  checkpoint/resume 是否覆盖（不只是连接快照，而是「任务执行到第 N 步」可恢复）。

### 借鉴点 6：Hermes function-calling 格式作为「模型无关」工具协议参考
- **借鉴什么**：`<tools>`/`<tool_call>`/`<tool_response>` + 固定 pydantic schema 这套，
  是经大量数据集验证、**跨模型可复现**的约定。
- **为什么**：DeskPet 走中转站（gpt-5.5），但若想支持本地/其他模型，
  这套 XML+JSON 约定比依赖某家原生 function-calling API 更可移植。
- **现状差距**：DeskPet 工具层有 registry+权限门+熔断，已较完整；
  此点更多是「若要扩展模型无关性」时的协议参考，非紧急。

### 借鉴点 7：Honcho 式「链式自我批判」用于用户建模
- **借鉴什么**：Initial Assessment → Self-Audit → Reconciliation 三段串行自审，提升建模质量。
- **为什么**：桌宠「跨会话理解用户」需要高质量用户模型；单次抽取易偏。
- **现状差距**：DeskPet 记忆侧重「事实/向量」，缺「对用户画像做自我审计修正」的多段 pass。
  可作为长期记忆的增强方向（非 last-mile 优先级）。

---

## 5. 局限 / 不适用 DeskPet 的地方

1. **部署形态错配**：Hermes 重心是**多通道 messaging + 服务器/serverless（Modal/Daytona）**部署，
   DeskPet 是**本地单机桌面 + 语音优先**。gateway / web dashboard / 多 IM 接入这块对桌宠基本不适用。
2. **无语音管线**：Hermes 是文本/工具 agent，没有 DeskPet 的 VAD→ASR→LLM→TTS 全本地语音链与 Live2D 表达层。
   语音/口型/情感这条线 Hermes 给不了参考。
3. **自创技能的安全面**：Hermes agent 自己写 Python 技能并执行，在服务器沙箱里 OK；
   DeskPet 是单机桌宠，全局规范明确「**不加沙箱护栏，只防手滑级破坏**」——
   若引入「agent 自动造可执行技能」，要走 DeskPet 自己的权限门+熔断，**不要照搬 Hermes 的执行环境抽象**。
4. **star/contributor 数据存疑**：抓取显示 18 万 star/1310 contributors，量级可能被聚合页夸大，
   引用时以实际仓库为准，不要当硬事实写进对外材料。
5. **Honcho/Modal 等外部依赖**：Hermes 很多高级能力依赖外部托管服务（Honcho 用户建模、Modal serverless），
   与 DeskPet「全本地/隐私优先」定位冲突，借鉴时只取**机制思想**，不取**外部服务依赖**。

---

## 6. 关键引用

- 主仓库：https://github.com/NousResearch/hermes-agent
- README：https://github.com/NousResearch/hermes-agent/blob/main/README.md
- 架构深析（DeepWiki）：https://deepwiki.com/NousResearch/hermes-agent
- 官方文档：https://hermes-agent.nousresearch.com/docs/
- Function-calling 数据集（格式权威来源）：
  https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1
- Hermes-Function-Calling 仓库：https://github.com/NousResearch/Hermes-Function-Calling
- 第三方深析：https://mranand.substack.com/p/inside-hermes-agent-how-a-self-improving
- 关键文件路径（仓库内）：
  - `run_agent.py`（AIAgent 主循环）
  - `agent/memory_manager.py`（MEMORY.md/USER.md 管理）
  - `agent/prompt_builder.py`（system prompt 拼装 + SOUL.md）
  - `model_tools.py` / `toolsets.py`（工具注册表 + 分组）
  - `hermes_state.py`（会话状态持久化 → ~/.hermes/sessions/）
  - `/skills`（自创技能存储，兼容 agentskills.io）

---

## 附：一句话给后续 agent

Hermes 对 DeskPet 最高价值的两点是
**(A) 把「错误分析→自我批判→重规划」做成 agent 必填的结构化字段（agentic JSON-mode）**，
和 **(B) 技能自创闭环的「值不值得固化」触发器（≥5 工具调用/从错误恢复/被纠正/非显然 workflow）**。
前者补 DeskPet「verify gate 校验不过就停、缺自动重规划重试」的缺口；
后者把 DeskPet 静态的 14 builtin skill 升级成「越用越会办事」的动态技能库。
其余（多通道部署、Honcho/Modal 外部依赖、自动执行 Python 技能）与 DeskPet 本地/隐私/单机定位冲突，只取机制不取实现。
