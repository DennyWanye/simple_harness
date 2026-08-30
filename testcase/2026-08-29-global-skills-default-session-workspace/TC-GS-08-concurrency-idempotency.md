---
id: TC-GS-08
purpose: Verify concurrent exact Skill installation and lost-ACK replay are collision-safe and idempotent
status: active
surface: integration
type: scripted
obligations: [TO-A3, TO-A6, TO-R1, TO-R4, HM-TO-R2, HM-TO-R4]
tags: [concurrency, idempotency, lost-ack, workspace]
entrypoint: public global-install service
revision: 2
---

# TC-GS-08 — 全局 Skill 安装并发与幂等

## 步骤与预期

使用 `verification/fault-matrix.json` runner 的 concurrency/lost-ACK 模式、固定 seed `GS-CONCURRENCY-16-V1` 与稳定 request IDs；runner 创建 durable gate root-attempt correlation，但不得 admission Provider Run。

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 两个独立 root Run 并发提交相同 owner/exact commit/member-set install request。 | 收敛到一个 active Skill version/generation 与一个稳定 terminal operation/receipt。 |
| 2 | 模拟 committed-before-ACK 后重放完全相同 request。 | 返回同一 receipt，binding/package/generation 计数不增加。 |
| 3 | 用同 request ID 提交不同 intent。 | 稳定冲突拒绝，既有 primary conversation/TaskScope/catalog/receipt 不改变。 |

## 决定性证据

- 请求与 receipt correlation、唯一性/计数断言、managed package tree 与 active generation 清单。
- TaskScope 自身的并发创建与 lost-ACK 由 `TC-HM-09`/`TC-HM-X01` 单独证明，避免与 Skill install 混成同一 oracle。
