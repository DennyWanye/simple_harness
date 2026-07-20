# R2 Results — ProductTurnPreparer / RunPresenter

> 日期：2026-07-20  
> 状态：PASS  
> 生产 execution owner：`legacy/0`（未切换）

## 结果

- `_run_chat` 仍是唯一生产入口，但产品准备已形成 typed `prepare_context → route_intent → plan_decision` 三阶段。
- legacy AgentEvent 的 live/durable/domain 投影统一由 `RunPresenter` 注册；WebSocket/peer/DB/plan waiter 只由窄 `LegacyProductDomainSink` 接触。
- Preparer 不执行 transport I/O 或长等待；旧入口仍持有生产 waiter/cancel lifecycle，因此 R2 没有 shadow、双跑、双写或 owner 切换。
- 冻结 R0 census fixture 内容零差异；新增 141/141 old→new source-hash mapping，删除 dual-send 任一 peer broadcast 会 fail closed。

## 对抗审查闭环

第一版因“近 1:1 搬家、census 覆盖旧基线、typed 输入错误、测试不足”被判 FAIL。返工后三个生产模块从初版约 1,064 行收敛到 611 行，并补齐 plan go/cancel/timeout/CancelledError cleanup、ToolResult/handoff/error/compaction、activity/vector/codify/billing/feedback 与 best-effort 失败 golden。

## 集成门禁

| 门禁 | 结果 |
|---|---|
| harness | `232 passed, 9 xfailed` |
| census | `141/141`, unmapped `0` |
| adjusted LOC | `32,490 <= 33,228`, unknown `0` |
| architecture docs | `1 passed` |
| R2 branch adjacent agent/context/problem/main | `335 passed` |
| Preparer + Presenter + legacy sink | `611 <= 1,000` physical lines |

实现提交：`ff4bcfca`（集成 cherry-pick）；事实源收尾：`61cefbdb`。
