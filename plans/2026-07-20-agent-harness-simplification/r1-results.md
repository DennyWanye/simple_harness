# R1 Results — 单一 UoW 与原子边界

> 日期：2026-07-20  
> 状态：PASS  
> 生产 owner：`legacy/0`（未切换）

## 结果

- v7 control plane、drain manifest、activation generation/fence 已进入现有 `SqliteExecutionUnitOfWork`。
- 删除重复的 Ledger、Decision、Effect、Continuation durable owner；短 ReAct 只保留 Kernel 有界内存索引，首个 durable boundary 才 promotion。
- ToolRegistry 已解除对 harness executor 的反向依赖；可信 `ToolExecutionContext` 与模型参数分离。
- §4.9 八类原子操作已完成：promotion、decision、grant/effect claim、effect settle、final delivery、child schedule、child terminal signal、parent apply/ack。
- Team task claim 与 stable child-command outbox 同事务；跨库使用可重放 saga，不伪造跨 SQLite 原子性。

## 门禁证据

| 门禁 | 结果 |
|---|---|
| fault matrix | `33 passed`，且 `required == tested == exported` |
| adjacent UoW/Decision/Child/Ledger/Team | `148 passed` |
| integrated harness | `217 passed, 9 xfailed` |
| architecture docs | `1 passed` |
| R1 LOC | `32,766 <= 33,228`，unknown classifications `0` |
| workflow/execution/team 宽回归 | `755 passed, 4 failed`；4 项均为 R0 已记录的 DeepResearch manifest identity/hash 基线漂移，失败集合未扩大 |

## 主要提交

- `a3730db1` durable runtime activation fence
- `97c558ae` continuation persistence into UoW
- `11e6410f` atomic promotion boundary
- `f80122fc` ToolRegistry dependency direction
- `6e9b820e` continuation/owner collapse
- `63223b19` eight atomic operations and restart matrix

## 后续约束

R1 为 crash-correct，但组合事务使 `execution_uow.py` 暂时增厚。R2～R4 必须继续拆除 compatibility surface 并复用 product-neutral records/SQL primitives；进入 R6 前，R5 的 combined core/UoW 机械门禁必须达到 `<= 2,800`。生产切换只允许发生在 R6 的单次 activation commit。
