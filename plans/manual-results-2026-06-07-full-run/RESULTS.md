# 全量手测执行结果汇总（两份文档 47 TC）

> 详细逐条状态见 [PROGRESS.md](./PROGRESS.md)（续跑唯一真相源）。
> 被测 HEAD `c27b791`+。环境：launch-fp345b.ps1 源码后端 + 全 FP flag + auto_mode + 真 BGE-M3。

## 续跑后状态：47 条（42 PASS + 3 PENDING + 2 BLOCKED）
- **A 组 goal-completion-manual-test.md（22）**：17 PASS + 3 PENDING（5.2/5.4/5.7）+ 2 BLOCKED（3.1/3.3）。
- **B 组 跨层 bug 回归（25）**：25 PASS。
- **真机 windows-mcp 截图 PASS = 11**：TC-3.2/3.4/4.1/4.2/4.3/4.5/5.1/5.3/5.6/5.8 + 5.2 goal-anchor 部分。截图见本目录 screenshots/ + ../manual-results-2026-06-06-FP345/screenshots/。

## 剩余 2 个 BLOCKED 的原因（均为范围/触发限制）
1. TC-3.1 — gpt-5.5 拒绝伪造完成声明（诚实），catch 逻辑单测覆盖。
2. TC-3.3 — goal_alignment 是 claim-vs-receipt（代码核实 verify_gate.py:343-396）非 goal-text 语义比对；需 slash 键入法。

## 续跑重开 3 个 PENDING
- TC-5.2 / TC-5.7 — task_ae1af91b 已修：AgentLoop 压缩触发使用会话级 ContextManager compact_at_tokens；需真机补 p1_4_compaction_fired 链路。
- TC-5.4 — task_01be24af 已修：dashboard tile 候选卡可渲染可点；需真机点"忽略"并核无落盘。
- B-9 已转 PASS：前端 jsdom 覆盖候选卡渲染、ignore WS payload、本地 resolve。

## 本次执行新增
- 建 PROGRESS.md 续跑机制（每条立即更新，后续 agent 可无缝接）。
- B 组 25 条回归 TC 经文档自定方法（boot log + 代码/配置核对 + DB + 单测守护）逐条核实 → 24 PASS。
- A 组真机补跑 goal-anchor 召回（TC-5.2 部分证据）。

## 📌 子代理终审 + 压缩 workaround 补做（2026-06-07）
独立审计子代理终审：🟢 诚实可信，41 PASS 证据全实地核验为真、无虚标；裁定 TC-5.2/5.7 BLOCKED「过早」，建议试「单轮大粘贴堆 context 过阈」workaround。
**已按建议补做**：单轮粘贴 52k 字(~13k token)到 231 消息会话 → 压缩**仍未 fire**（整会话从无 `p1_4_compaction_fired`）。
**新发现（潜在死链，已建调查任务 task_ae1af91b）**：压缩触发 `estimated_tokens≥24000`（compressor 自带 32000×0.75）疑因 working_messages 窗口化 / 与 budget effective_window(可能800K) 口径错配，导致 FP-5 WI-4.0 压缩在生产实践中难/不触发。goal-anchor 行为本身✅（231消息+52k dump 后仍准确召回原目标 PPT_B10测试报告）。TC-5.2/5.7 严格链路因压缩不 fire 无法真机验，BLOCKED 理由已升级为「压缩潜在不触发 + 已建调查」。
