# cc-haha 调研

> 调研日期：2026-06-04 | 来源：GitHub README（中/英）、knightli.com 技术分析、Web 搜索
> 仓库：https://github.com/NanmiCoder/cc-haha

---

## 1. 项目概览

- **是什么**：cc-haha（"Claude Code Haha"）是基于 **2026-03-31 从 Anthropic npm registry 泄露的 Claude Code 源码** 二次开发的项目。当前主形态已不是"本地跑 CLI"，而是一个 **跨平台（macOS / Windows / Linux）桌面工作台（desktop workbench）**，把 Claude Code 的会话、多项目、分支/Worktree、代码 diff、权限审批、模型供应商配置、Computer Use、H5 远程访问、IM 集成、定时任务全部塞进一个 GUI 应用。
- **谁做的**：国内开发者 **NanmiCoder（南米）**。
- **定位**：把"终端里的 Claude Code"升级成"带可视化 + 多通道接入 + 桌面自动化"的工作台。README 明确声明 **原始源码版权归 Anthropic，仅供学习研究**，非官方产品。亮点是 **自己补齐了 Computer Use**（官方泄露版里这块是闭源原生模块，作者用 Python bridge 重写）。
- **热度**：约 **12.2k star / 8.1k fork**（截至调研时；fork 比例异常高，说明大量人 clone 研究泄露源码）。活跃度高，迭代到 v0.2.6+，发布频繁（安全相关修复也在持续推）。
- **许可证**：含免责声明"原始源码版权归 Anthropic"，**非标准开源许可**——版权状态存在不确定性（leaked-source origin），生产借鉴需谨慎，**只看工程思路、不抄代码**。
- **技术栈**：TypeScript 99.3% | Electron + React + Vite（桌面 UI）| Bun（本地运行时）| React + Ink（终端 UI）| Commander.js（CLI 解析）| Anthropic SDK（主）/ LiteLLM（多模型桥）| MCP + LSP。

---

## 2. 核心架构与关键设计

### 2.1 分层数据流
桌面 App ←→ 本地 Claude Code CLI server ←→（Anthropic API **或** LiteLLM 代理）←→ 任意模型。
- **多模型**：用 **LiteLLM 作协议桥**，把 Anthropic Messages API 格式转成 OpenAI / DeepSeek / Ollama 等兼容协议。代价：extended thinking、prompt caching 在第三方模型上失效；复杂 tool_use 在 OpenAI function-calling 语义下可能不兼容。

### 2.2 Agent Loop（核心）
经典 **Plan → Reason → Act → Observe** 循环：
1. 用 **TodoWrite / Task 工具** 生成分步计划；
2. 推理下一步动作；
3. 调工具（读文件 / 写代码 / 执行命令 / screenshot）；
4. 观察工具输出，回填更新计划，进入下一步。
- **TodoWrite → Task 演进**：泄露版里能看到 **Task 工具族（TaskCreate / TaskUpdate / TaskList / TaskGet）** 取代单一 TodoWrite。区别：todos 是"让单个模型不跑偏"，**Task 则支持子 agent 间通信、依赖关系、跨 subagent 共享状态更新**——这是从"自我管理"到"多 agent 协同"的关键升级。

### 2.3 工具系统 & 权限模型
- 工具：文件读写、命令执行、WebSearch fallback、Computer Use（screenshot/zoom/鼠标/键盘/启动应用/剪贴板）等。
- **集中式审批流（centralized approval）**：危险命令、工具调用、AI 反问全部汇聚到桌面客户端**一个界面**统一审批，而不是散落在终端输出里。
- **有界授权（bounded access）**：H5 远程需显式开启 + 一次性 token 配对；Computer Use 需逐应用授权，**不给 blanket 权限**。v0.2.6 专门把 H5 从"临时开放"收回成"显式启用 + token 配对"。

### 2.4 Computer Use Bridge（亮点）
官方闭源原生层被替换为 **Python bridge**，用公开库 `pyautogui`（鼠标键盘）、`mss`（截图）、`pyobjc`（macOS）实现。Agent 走 **"截图→视觉识别坐标→点击/输入→再截图验证→迭代"** 的感知-决策环。

### 2.5 多通道接入
- **H5 远程访问**：一次性 token 配对，手机/外部设备浏览器接入。
- **IM 集成**：Telegram / 飞书 / 微信 / 钉钉——可远程审批和控制 agent。
- **定时任务 + token 用量统计** 面板。

---

## 3. 「帮用户完成目标」相关机制 / 工程实践（重点）

1. **Plan-Reason-Act-Observe 显式计划循环** + **Task 工具族**：把"目标"拆成可追踪、可更新、带依赖的任务列表，子 agent 间能共享进度——比纯 prompt 驱动更可控，不易跑偏。
2. **集中式审批 = 信任与进度的平衡器**：用户对危险动作有一票否决，但不打断主循环；审批界面统一，降低"agent 失控"焦虑，让用户敢放手让 agent 多走几步。
3. **可视化 diff 面板**：右侧实时显示改了哪些文件 + diff，用户不切工具就能 review，**缩短"agent 做了什么"的反馈环**。
4. **Worktree / 分支隔离**：新会话可选分支 + 独立 worktree，**多目标并行不互相污染**。
5. **多通道远程批准**：用户离开电脑也能通过 IM 推进/批准 agent 任务——"目标推进不被物理在场卡住"。
6. **Computer Use 闭环**：当目标超出代码范畴（要操作 GUI 应用），agent 用截图-动作环把"目标"延伸到任意桌面软件。
7. **Git-first 上下文**：file mention 尊重 `.gitignore`、过滤 `node_modules`/构建产物——**喂给模型的上下文更干净**，目标相关性更高。

---

## 4. 对 DeskPet 的可借鉴点

> 逐条：借鉴什么 + 为什么 + DeskPet 现状差距

### 4.1 ★ Task 工具族（TaskCreate/Update/List/Get）替代/补充扁平 todo
- **借鉴**：把目标拆成**带依赖、可跨 agent 共享状态**的结构化任务，而非线性 todo。
- **为什么**：DeskPet 核心诉求是"让桌宠更好帮用户完成目标"，多 agent 已有但任务编排偏即时；结构化 Task 让长目标可追踪、可恢复、子 agent 能协同。
- **DeskPet 现状差距**：DeskPet 有 `/goal` + 多 agent，但**目标的任务图（依赖/状态共享）是否持久化、是否暴露给子 agent 读写**值得对照 cc-haha 的 Task 模型补强。本仓库已有 `/goal` 条件/一票否决体系（见全局 CLAUDE.md），可把 Task 依赖图作为 goal 的执行层。

### 4.2 ★ 集中式审批流（统一权限界面 + 主循环不打断）
- **借鉴**：危险命令 / 工具调用 / agent 反问汇聚到**一个**审批入口；批准/拒绝不中断 agent 思考流。
- **为什么**：DeskPet 有工具层（registry + 权限 + 熔断 + last-mile），但桌宠形态下审批 UX 容易碎片化。
- **DeskPet 现状差距**：DeskPet 权限/熔断在后端较完整，但**前端是否有统一的"待审批"聚合视图**（让用户一眼看清 agent 想干啥、批量批准）可对标。注意：DeskPet 项目纪律是"不加沙箱护栏，只防手滑级"——所以借鉴**审批 UX 聚合**，不是借鉴重权限墙。

### 4.3 实时 diff / "agent 做了什么"可视化反馈环
- **借鉴**：右侧面板实时显示 agent 改动 + diff，无需切工具 review。
- **为什么**：缩短反馈环 = 用户更敢让 agent 多自走几步 = 更好完成目标。
- **DeskPet 现状差距**：DeskPet 有 code 模式 + ArtifactCard，但**桌宠主界面对"agent 正在改什么"的轻量可视化**（非全屏 IDE 式）可能不足。可做桌宠侧的"改动气泡/diff 小卡片"。

### 4.4 多通道远程推进（IM / H5）
- **借鉴**：离开电脑也能经 IM 推进/批准长任务。
- **为什么**：长目标常跨时段；桌宠若能"我在后台帮你跑，手机上批一下"会显著提升完成率。
- **DeskPet 现状差距**：DeskPet 是本地桌宠，**无远程/IM 接入**。可考虑轻量 H5 token 远程查看长任务进度（注意安全：一次性 token、显式开启，**不写 .env/secrets**，符合本仓库纪律）。

### 4.5 Worktree 隔离做"多目标并行"
- **借鉴**：每个目标/会话独立 worktree，互不污染。
- **为什么**：DeskPet 已用 worktree 做开发隔离（见 CLAUDE.md 拓扑），但**面向"用户目标"的运行时隔离**可借同思路。
- **DeskPet 现状差距**：开发期已有 worktree + 端口隔离；可把同一机制下沉成"用户多目标并行执行"的运行时能力。

### 4.6 Git-first / gitignore-aware 上下文采集
- **借鉴**：file mention 尊重 `.gitignore`、剔除 `node_modules`。
- **为什么**：和本仓库 MEMORY 里的 `feedback_context_overflow_bulk_reads`（批量读第三方依赖会爆 context）**完全同源**。
- **DeskPet 现状差距**：DeskPet 记忆是 BGE-M3 语义检索，但**喂给 LLM 的工作区文件采集是否默认 gitignore-aware** 值得确认。这是低成本高收益的对齐项。

### 4.7 Computer Use 的 Python bridge 思路（仅参考，DeskPet 已更强）
- **借鉴**：截图→视觉定位→动作→验证 的桌面自动化闭环设计。
- **为什么**：DeskPet 本仓库已有 windows-mcp + SendInput 圣杯方案（见全局 CLAUDE.md），**比 cc-haha 的 pyautogui 更鲁棒**（WebView2/Chromium 下 pyautogui 的老式 mouse_event 会失效）。
- **DeskPet 现状差距**：**DeskPet 此处更先进，无需借鉴实现**，仅可参考其"统一 Computer Use 工具集 + 逐应用授权"的产品化封装。

---

## 5. 局限 / 不适用

- **版权/合规风险**：基于泄露源码，许可不清晰。**绝不可抄代码进 DeskPet**，只提炼工程思路。
- **定位差异**：cc-haha 是**面向开发者的代码工作台（Electron + 终端血统）**；DeskPet 是**面向普通用户的语音桌宠**。许多"IDE 式"重界面（多 tab、diff 全屏、worktree 选择器）**不适合桌宠轻量形态**，需大幅裁剪。
- **多模型桥的代价**：LiteLLM 路由导致 thinking/caching 失效、tool_use 兼容问题——DeskPet 走中转站（gpt-5.5）相对单一，**不必引入这层复杂度**。
- **无本地语音管线**：cc-haha 完全没有 VAD/ASR/TTS/Live2D，**DeskPet 的核心差异化（全本地语音 + 桌宠表达力）cc-haha 完全不覆盖**，这块无参考价值。
- **Computer Use 实现较弱**：pyautogui/mss 在现代 WebView2/Chromium 下不如 DeskPet 的 SendInput 方案，**反向 DeskPet 更值得被它借鉴**。

---

## 6. 关键引用

- 仓库主页（中文）：https://github.com/NanmiCoder/cc-haha
- README 英文版：https://github.com/NanmiCoder/cc-haha/blob/main/README.en.md
- Releases（含 v0.2.6 H5 安全收紧）：https://github.com/NanmiCoder/cc-haha/releases
- 第三方技术分析（knightli.com）：https://knightli.com/en/2026/05/14/cc-haha-claude-code-desktop-workbench/
- 镜像仓库：https://github.com/NanmiCoder/claude-code-haha
- Claude Code Task/Todo 机制官方文档（佐证 Task 工具族）：https://code.claude.com/docs/en/agent-sdk/todo-tracking
