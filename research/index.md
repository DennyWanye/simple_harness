# DeskPet 对标调研索引（research/index.md）

> 本目录存放为「DeskPet 升级 — 更好帮用户完成目标」做的对标调研。
> 每个子文件夹一份独立调研，供后续 agent 复用。新增调研请同步更新本表。
> 最后更新：2026-06-04

---

## 0. 调研背景与目的

DeskPet 现状（一句话）：本地部署的桌面语音宠物 = Live2D + 全本地语音管线(VAD→ASR→LLM→TTS)
+ 工具层(registry/权限/熔断/last-mile artifact/receipt/verify-gate) + 长期记忆(BGE-M3)
+ 技能系统(SkillLoader+14 builtin) + Slash/`/goal`/多 agent + code 模式(superpowers 工作流)。

**核心诉求**：让桌宠从「会聊天」进化为「真能帮用户把目标办成（goal-completion）」，
同时保住「有人格的数字伴侣」属性。本轮调研对标 4 个产品 + 1 个行业最佳实践，
找出 DeskPet **最需要优化的地方**。

---

## 1. 调研概览表

| 调研方向 | 文件 | 一句话定位 | 对 DeskPet 最高价值的借鉴点（Top） |
|---|---|---|---|
| **claude-code** | [claude-code/](./claude-code/README.md) | Anthropic 官方 agentic harness，DeskPet code 模式的**直接母版** | ① skills「渐进式披露三级」做彻底；② plan mode 作为**独立只读权限模式**+五选一审批闸；元原则=**软指令塑造意图、硬机制(权限/hook/verify)保证边界** |
| **openhuman** | [openhuman/](./openhuman/README.md) | 30.7k★ 开源桌面 super-agent（Tauri/Rust+React，技术栈与 DeskPet 同构）；强在**记忆工程**、弱在陪伴/情感 | ① 混合检索**五路加权**(graph+vector+keyword+episodic+freshness)；② **PROFILE.md 人格画像+偏好半衰期衰减(7–90天/Pin/Forget)**；③ 分层摘要记忆树+可编辑.md vault |
| **hermes-agent** | [hermes-agent/](./hermes-agent/README.md) | Nous Research「自我进化型」agent 框架（MIT，多通道 messaging） | ① **agentic JSON-mode**：把「错误分析→自我批判→重规划」做成 agent**必填结构化字段**；② **技能自创闭环**触发器(≥5工具调用/从错误恢复/被纠正/非显然workflow) |
| **cc-haha** | [cc-haha/](./cc-haha/README.md) | 国内 NanmiCoder 基于泄露 Claude Code 源码的桌面工作台（许可不清，仅借思路） | ① **Task 工具族**(TaskCreate/Update/List/Get，带依赖+跨子agent状态共享)替代扁平 todo；② 集中式审批 UX；③ 实时 diff 反馈环 |
| **最佳实践（goal-completion）** | [best-practices-goal-completion/](./best-practices-goal-completion/README.md) | 2024–2026 agentic 完成目标行业最佳实践（planning/自验证/主动性/确认门/陪伴权衡/评测） | ① `/goal` 加 **durable goal doc + 压缩前 re-anchoring** 抗目标漂移；② verify gate **重述原目标对照产物**、绝不采信模型自述；③ 警惕数字伴侣**谄媚式假完成**反模式 |
| **跨项目对比 + Gap 分析** | [comparison-gap-analysis/](./comparison-gap-analysis/README.md) | 5 份调研的综合：DeskPet 已领先 vs 最需优化，**优先级矩阵** | 见该文件 §3「最需要优化的 5 个方向」 |
| **DeskPet 现状自查** ★ | [deskpet-self-audit/](./deskpet-self-audit/SUMMARY.md) | 写 plan 前的代码事实校准：4 方向逐机制【已有/部分/真缺口】+ `file:line` 证据 | **修正**：五路混合检索✅已做好、半衰期核心✅已有（只缺 Pin）；真高杠杆缺口=`goal_store` 内存态、re-anchoring、handoff、结构化反思、goal/decision 记忆、技能自创 |

---

## 2. 关键结论速览（详见 comparison-gap-analysis）

**DeskPet 已领先对标项目的护城河（不必抄，要守住）**：
- ✅ **goal-completion 产物校验闭环**（last-mile artifact/receipt/verify-gate）—— openhuman/hermes/cc-haha **都没有**，它们「模型停发 tool call 即完成」。
- ✅ **全本地语音管线 + Live2D 表达力** —— 4 个对标项目无一覆盖（openhuman TTS 走 ElevenLabs 云）。
- ✅ **windows-mcp/SendInput Computer Use** —— 比 cc-haha 的 pyautogui 在 WebView2 下更鲁棒。

**最需要优化的 5 个方向（优先级倒序，详见 gap 分析）**：
1. 🔴 **目标持久化与抗漂移**（durable goal doc + Task 任务图 + re-anchoring + handoff checkpoint）
2. 🔴 **自我纠错闭环**（verify 不过 → 结构化 error_analysis/critique/replan → 自动重试，而非「停」）
3. 🟠 **记忆工程深化**（五路混合检索 + 人格半衰期 PROFILE + 写入分级）
4. 🟠 **Skills 从静态→分级披露 + 自创闭环**（省 context + 越用越会办事）
5. 🟡 **主动性真做**（perceive→decide→act→learn + 价值闸门，桌宠差异化命脉）

---

## 3. 如何复用本调研

- 后续要做某个方向的 plan / 实现，先读对应子文件夹 README（含机制细节 + 引用链接 + DeskPet 现状差距）。
- 新增调研：在 `research/<方向名>/README.md` 写文件，并在本表 §1 加一行。
- 综合判断 / 优先级争议：看 [comparison-gap-analysis](./comparison-gap-analysis/README.md)。
