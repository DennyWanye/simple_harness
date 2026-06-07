# 全量手测执行结果汇总（两份文档 47 TC）

> 详细逐条状态见 [PROGRESS.md](./PROGRESS.md)（续跑唯一真相源）。
> 被测 HEAD `c27b791`+。环境：launch-fp345b.ps1 源码后端 + 全 FP flag + auto_mode + 真 BGE-M3。

## 终态：47/47（41 PASS + 6 BLOCKED-带原因，0 PENDING）
- **A 组 goal-completion-manual-test.md（22）**：17 PASS + 5 BLOCKED（3.1/3.3/5.2/5.4/5.7）。
- **B 组 跨层 bug 回归（25）**：24 PASS + 1 BLOCKED（B-9）。
- **真机 windows-mcp 截图 PASS = 11**：TC-3.2/3.4/4.1/4.2/4.3/4.5/5.1/5.3/5.6/5.8 + 5.2 goal-anchor 部分。截图见本目录 screenshots/ + ../manual-results-2026-06-06-FP345/screenshots/。

## 6 个 BLOCKED 的原因（无新功能 bug，均环境/范围/已知问题）
1. TC-3.1 — gpt-5.5 拒绝伪造完成声明（诚实），catch 逻辑单测覆盖。
2. TC-3.3 — goal_alignment 是 claim-vs-receipt（代码核实 verify_gate.py:343-396）非 goal-text 语义比对；需 slash 键入法。
3. TC-5.2 — goal-anchor 召回真机✅；严格"压缩后"需 context>75%（现50%），跨越需多轮+稳定 relay。
4. TC-5.7 — 同 5.2 需压缩 fire；remount 逻辑单测覆盖。
5. TC-5.4 — 候选生成✅，点忽略受候选卡 chat 渲染 bug（task_01be24af）阻塞。
6. B-9 — 候选卡 chat 渲染已知前端友好性 bug，未修（task_01be24af）。

## 本次执行新增
- 建 PROGRESS.md 续跑机制（每条立即更新，后续 agent 可无缝接）。
- B 组 25 条回归 TC 经文档自定方法（boot log + 代码/配置核对 + DB + 单测守护）逐条核实 → 24 PASS。
- A 组真机补跑 goal-anchor 召回（TC-5.2 部分证据）。

## 📌 子代理终审 + 压缩 workaround 补做（2026-06-07）
独立审计子代理终审：🟢 诚实可信，41 PASS 证据全实地核验为真、无虚标；裁定 TC-5.2/5.7 BLOCKED「过早」，建议试「单轮大粘贴堆 context 过阈」workaround。
**已按建议补做**：单轮粘贴 52k 字(~13k token)到 231 消息会话 → 压缩**仍未 fire**（整会话从无 `p1_4_compaction_fired`）。
**新发现（潜在死链，已建调查任务 task_ae1af91b）**：压缩触发 `estimated_tokens≥24000`（compressor 自带 32000×0.75）疑因 working_messages 窗口化 / 与 budget effective_window(可能800K) 口径错配，导致 FP-5 WI-4.0 压缩在生产实践中难/不触发。goal-anchor 行为本身✅（231消息+52k dump 后仍准确召回原目标 PPT_B10测试报告）。TC-5.2/5.7 严格链路因压缩不 fire 无法真机验，BLOCKED 理由已升级为「压缩潜在不触发 + 已建调查」。
