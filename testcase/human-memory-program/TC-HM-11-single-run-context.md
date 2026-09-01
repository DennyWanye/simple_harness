---
id: TC-HM-11
purpose: Verify one foreground ReAct Run, durable FIFO, semantic closure, and bounded causal Context assembly
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A3, HM-TO-A6, HM-TO-A8, HM-TO-R2, HM-TO-R7, HM-TO-R8, HM-S4-TO-VALUE, HM-S4-TO-FIFO]
tags: [human-memory, foreground-run, fifo, context, closure, restart]
entrypoint: long-running primary conversation
revision: 4
---

# TC-HM-11 — 单前台 Run、消息排队与动态 Context

## 前置

- 同一永久主对话准备不少于 24 个完整因果 turn group、两个 TaskScope、五天内外短时内容、四类长期记忆、并行工具调用与一个 1 MiB Tool Result。
- 分别运行 4k、8k、32k effective provider window；至少一次真实主模型 root run。
- FIFO/closure crash 使用 `fixtures/fault-matrix.json` 的 `foreground-fifo-closure` lane；fixture SHA-256
  `b4dcb2f39a2e5c2f7afeeb1dd496aa94587fe075c8772ea44fab880115bc74da`。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 启动长工具 Run，运行中连续发送两条普通消息，再发送 pause/stop。 | 普通消息先永久入账并 FIFO 等待；同一时刻只有一个 foreground ReAct Run；控制信号立即作用于当前 Run。 |
| 2 | 同时运行 extraction/index/projection Worker。 | Worker 可并发但不能产生 Agent effect、改 TaskScope 语义或启动第二 foreground Run。 |
| 3 | 当前 Run terminal 后观察后续消息。 | 按 durable FIFO 依序启动 root Run；冷重启后顺序不变，无丢失/重复。 |
| 4 | 检查每次真实 Provider 请求的 ContextSnapshot。 | protected instructions/current query/current tool continuation 保留；最近 10 个完整因果组不拆链，五天短时、TaskScope 临时投影与长期记忆按预算去重；大型结果用 typed summary+exact ref。 |
| 5 | 让 tool/file/test 客观事件改变任务，再令主模型漏调用、拒绝或产生 CAS 冲突。 | 客观事件不经 LLM 入账；closure gate 必须以合法 mutation 或显式 no_mutation 收口，失败可重试且不伪造 Markdown 成功。 |
| 6 | 对已冻结 snapshot 在 crash 后 replay。 | Host/Harness/provider adapter 的 payload hash 一致；实际 usage 不超 effective budget，低估 token 即 FAIL。 |

## S4 Host durable FIFO 自动化子 lane（required）

| 步骤 | 公开 Host 动作 | 预期结果 |
|---:|---|---|
| S4-1 | 对同一 subject 并发提交普通 turn `q1/q2/q3`，重放 `q2` 的同一 idempotency key，并模拟 lost ACK 重试。 | 全部先获得 durable evidence/queue receipt；sequence 严格 FIFO；`q2` 只有一个 canonical receipt 且不重复执行。 |
| S4-2 | 在 `q1` active 时发送 pause，再发 stop/cancel，同时尝试 claim 第二 foreground Run 和 background-worker claim。 | control 立即越过普通队列并绑定当前 Run；同 subject 第二 foreground 与 worker claim 稳定拒绝。 |
| S4-3 | 在 claim、terminal receipt 前后和 lease 恢复边界分别冷重启，然后重放待处理 delivery。 | active owner 不双启；terminal 一次入账后才 claim 下一 sequence；无丢 turn、无重复 Run、无乱序。 |
| S4-4 | 从公开 Auto-binding read 口读取当前 durable Run snapshot，然后重启再读。 | snapshot 是唯一 fact source，其 run/task_scope/binding/context lineage refs 在重启前后一致。 |

本子 lane 不断言 S5 Context assembler、semantic closure 或真实 provider 成功。

## 决定性证据

- foreground owner timeline、FIFO ledger、control receipt、Context partitions/token usage、tool causal pairs、closure records、snapshot/payload hashes。S4 原始输出只写 ignored `.local-test-evidence/`。
