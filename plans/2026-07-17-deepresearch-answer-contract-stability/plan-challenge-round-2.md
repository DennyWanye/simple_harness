# Plan Challenge Round 2

> 结果：`VERDICT: FAIL`  
> 状态：2026-07-18 已逐项闭环，供 Round 3 只挑战新增/未闭环问题。

| Round 2 blocker | 闭环位置 |
|---|---|
| 写错 native 注入类/不存在 `NativeWorkflowRunner` | T1/T10/§3.3：改为真实 `WorkflowRunner -> RegisteredWorkflow.materialize -> CompiledWorkflow.bind -> NativeWorkflowExecutable`，新增 `NativeRuntimeDependencies` |
| launcher `_adapters` 仍是第二 owner | T1：service 持有唯一 `DeepResearchVersionRegistry`，launcher 删除 register/dict 并只读 service registry |
| effect 唯一 fingerprint 无法 attempt+1 | `v6-contracts.md` §4.1 + T1/T4：schema v4 logical effect fields/head、begin/commit/reconcile APIs、canonical result |
| deadline 恢复没有 upstream 前持久化点 | §4：deadline revision + effect attempt + reservation 在 `EffectJournal.begin_idempotent_read_attempt` 同事务；checkpoint 仅镜像 |
| committed page blob 只有 staging owner会被清掉 | §4.1/T4/T10：effect commit 同事务创建 `effect(effect_id)` owner并删 staging；checkpoint 只附加 owner |
| generate-now partial page 无 patch | T5：业务 fence/deadline 转 typed partial success patch；仅 shutdown/owner cancellation 传播 |
| v19 缺 migrator special branch/CHECK/fault steps | T10：`_V19_*`、transactional branch、marker ordering、column CHECK、逐步 fault rollback |
| ReflectionWorker 漏过滤 | T10：Reflection conversation-only；qaset 默认 conversation，测试-only 显式例外 |
| schema migrator 无 blob root不能 backfill v6 head | T1/T12：任何 pre-v4 v6 continuation fail closed；不 backfill，隔离开发 DB 重建 |
| content_ref 没有 SessionDB resolver | T10：注入 `CanonicalContentResolver`，outbox 不内联正文，blob bytes 原样投影，无 placeholder |
| required aggregate 无存储/query owner | T1 schema v4 delivery columns + T10 `delivery_aggregate(run,manifest)` 状态机 |
| sync public 与 async commit 双 owner | T10：async 验 manifest 后用 sanitized state 调同一 sync registry，并校验一致性 |
| v6 page transport identity 未冻结 | T4/T5：独立 `DurableV6PageReadEffectAdapter`、policy/tool spec/stable call ID；不改 v5 adapter identity |

本轮新增的 workflow schema v4 被前移到 T1，避免 T5/T10 在 T12 之前依赖尚未存在的表/列。
