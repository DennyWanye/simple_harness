---
id: TC-HM-04
purpose: Verify Procedure activation and Prospective trigger lifecycle remain distinct and permission bounded
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A5, HM-TO-R2, HM-TO-R6]
tags: [human-memory, procedure, prospective, trigger, permission]
entrypoint: primary conversation and Host scheduler
revision: 1
---

# TC-HM-04 — Procedure 与 Prospective 一等能力

触发故障使用 `fixtures/fault-matrix.json` 的 `prospective-occurrence` lane；fixture SHA-256
`c6ad25433d5fd5913708d5d2967fa85f98ad00511ec326e4b93ef7733535315f`。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 输入“以后发布都按这套检查清单；这次发布成功以后提醒我更新变更日志。”并给出清单。 | 产生 Procedure proposal 与 event-triggered Prospective proposal，而不是两个普通 Fact。 |
| 2 | 仅提供一次成功观察。 | Procedure 为 draft；不得因一次观察自动 active。 |
| 3 | 在 90 天内、相同 revision/applicability 下跨三个不同 TaskScope 提供成功 evidence。 | 仅低风险可逆步骤自动 active；发布、删除、付款、权限步骤仍要求用户确认。 |
| 4 | 触发一次“发布成功”事件并模拟 registration/occurrence/ack 各边界 lost-ACK 重放。 | Host 作为唯一 scheduler 只产生一个 occurrence；意图状态按 pending→triggered/settled 演化，pending 不丢。 |
| 5 | 改变工具或环境适用性后再次调用程序。 | 先检查适用性；drift 时停止自动应用并报告，不沿用过期程序。 |

## 决定性证据

- procedure/prospective 各自状态链、独立 TaskScope 成功证据、scheduler occurrence identity、权限 decision 和重复触发计数。
