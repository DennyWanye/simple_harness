---
id: TC-GS-08
purpose: Verify concurrent default Session creation and exact Skill install replay are collision-safe and idempotent
status: active
surface: integration
type: scripted
obligations: [TO-A3, TO-A6, TO-R1, TO-R4]
tags: [concurrency, idempotency, lost-ack, workspace]
entrypoint: public session-create and global-install services
revision: 1
---

# TC-GS-08 — 并发与幂等

## 步骤与预期

使用 `verification/fault-matrix.json` runner 的 concurrency/lost-ACK 模式、固定 seed `GS-CONCURRENCY-16-V1` 与稳定 request IDs；runner 创建 durable gate root-attempt correlation，但不得 admission Provider Run。

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 同一隔离 profile 并发提交 16 个不同普通 Session 默认创建请求。 | 16 个唯一 Session 与 canonical 目录，无覆盖、共享目录或 orphan allocation。 |
| 2 | 两个 Session 并发提交相同 owner/exact commit/member-set install request。 | 收敛到一个 active Skill version/generation 与一个稳定 terminal operation/receipt。 |
| 3 | 模拟 committed-before-ACK 后重放完全相同 request。 | 返回同一 receipt，binding/package/generation 计数不增加。 |
| 4 | 用同 request ID 提交不同 intent。 | 稳定冲突拒绝，既有 Session/catalog/receipt 不改变。 |

## 决定性证据

- 请求与 receipt correlation、唯一性/计数断言、目录树与 active generation 清单。
