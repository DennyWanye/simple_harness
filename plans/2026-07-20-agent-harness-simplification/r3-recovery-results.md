# R3 恢复 Fence 加固结果

> 日期：2026-07-21
> 状态：通过；生产 owner 仍为 `legacy/0`。

## 生产事实

- 恢复所有权使用四字段 token：`run_id / owner / epoch / expires_at`。
- Runtime 在构造 Driver 恢复迭代器前、第一次 `anext` 前以及长操作期间通过 heartbeat 续租。
- ReAct continuation、effect claim/settlement、child 调度和 signal、event、terminal 事务都在自己的 `BEGIN IMMEDIATE` 内校验 token。
- Child boundary、child event 和 inbox acknowledgement 在一个事务中提交。
- Workflow 恢复在一个 SQLite 事务中校验 execution token 并 claim 原生 `RunFence`；Runner 复用预先 claim 的 fence，不重复 claim。
- 可恢复工具 effect 在外部执行前 claim。如果执行期间发生 owner takeover，旧 owner 的 settlement 抛出 `StaleRecoveryLease`，不能推进 boundary。
- ReAct resume 通过唯一转换重建 `DriverStart`，保留冻结的能力快照和工具 allow-list。
- Child accepted / terminal 以 host system message 进入模型输入，并与 child boundary、event、inbox ack 一起提交；`signal_id` 保证崩溃恢复时只对模型可见一次。
- 新 ReAct turn 默认使用请求级 `UNKNOWN` 完成证据。成功工具结果按 run / turn / call / effect / artifact 精确匹配；不匹配时关闭失败。
- 持久 boundary 保留恢复第二批工具所需的可信上下文、continuation version 和 recovery lease。

## 故障窗口证据

新增负向和并发测试覆盖：

- 同 owner claim 幂等
- 第一次 yield 前续租顺序
- 过期 continuation / terminal 写入
- 过期 effect claim
- Workflow handoff 回退与幂等
- 工具调用中途 takeover
- 过期 child boundary / event / inbox ack

## 门禁

| 门禁 | 结果 |
|---|---:|
| Recovery / Kernel / ReAct / child / workflow / tool / bootstrap | `71 passed` |
| ReAct / scoped evidence / Kernel 回归 | `58 passed` |
| Harness 全量 | `275 passed, 9 xfailed` |
| AgentLoop 邻接验证 | `59 passed` |
| Owner 审计 | `missing=[]` |
| Kernel | `631 <= 650` 行 |
| 调整后 orchestration LOC | `33,223 <= 33,228` |
| 未分类 | `0` |

本切片不包含生产激活或 owner 切换。
