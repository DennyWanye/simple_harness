# R2 Adversarial Audit — Round 1

> 日期：2026-07-20  
> Verdict：FAIL，禁止合并；实现分支正在返工。

## Blockers

1. R0 parity census 被当前调用点覆盖，却仍标记旧 base commit；`_send_both` 的 peer 计数由扫描器人工复制，删除真实 broadcast 仍可能假绿。整改：冻结 R0 fixture，增加逐项 old→new migration map，并递归验证 helper 的两个真实出口。
2. 初版 `ProductTurnPreparer` 直接执行 WS/peer、SessionDB、SessionActivity、全局 waiter 与 900 秒等待，不是 venue-neutral typed preparation；两个新文件约 1,064 行，接近把 `main.py` 1:1 搬家，并已挤占后续 adapter 的 1,000 行总预算。整改：Preparer 只返回 typed context/route/plan intents；R2 仍由旧入口持有 wait/cancel lifecycle，投影副作用归 Presenter。
3. `TurnInput.memory_policy` 被错误声明为字符串，但生产和 assembler 契约是 mapping；request/provider/capability refs 也未代表最终真实选择。整改：按真实生产输入建模并用生产形状测试。

## High findings

- billing 与 ContextAssembler feedback 仍在 `main.py`，Presenter 尚未覆盖完整收尾副作用。
- Presenter 只接受 legacy `AgentEvent`，漏 `ToolBatchEvent`、`SubagentCompletionEvent`、`ProviderChainFallbackEvent`，也没有中立 PresentationEvent adapter 边界。
- 初版测试缺 plan go/cancel/timeout/cleanup、ToolResult/handoff/error/compacted、activity/vector/codify/billing/feedback 与异常顺序，不能证明 100% parity。

## 已确认正确

- 生产仍只有旧 `_run_chat` 单次调用，没有 shadow、双跑或双写。
- ReAct 顺序保持 `prepare → route → plan → AgentLoop → present`。
- Code/DeepResearch 的旧 early return 仍在原 owner，未提前切换。

## 放行条件

- 冻结 census + 可验证 migration map 达到 141/141。
- Preparer venue-neutral，交互只返回 typed intents；Presenter 完整覆盖产品投影。
- Preparer + Presenter 为后续 migrated adapters 保留明确 LOC 余量。
- 补齐分支/事件/副作用失败 golden 后，focused、动态 WS contract、完整 harness、LOC 与架构文档全部通过。
