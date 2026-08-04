# R5.5 幂等性审查

> 审查日期：2026-07-21
> A-source 提交：`69c6980ae7297997fdffd3c49770adbf0c7e5c80`

| 边界 | 稳定身份 / fence | 重复结果 | 冲突结果 | 恢复结果 |
|---|---|---|---|---|
| Admission 决议 | run、decision、nonce、version、完整 `DecisionSignal` 指纹 | 完全相同的重放返回已持久化决议 | 响应改变或 allow/cancel 冲突时关闭失败 | 行动前重读权威 continuation |
| Launch claim | run、admission boundary version、recovery owner/epoch、稳定 launch operation id | 复用已有 claim | 拒绝过期 lease / version | 幂等 Provider 复用 operation id；非幂等歧义记为 `launch_unknown` |
| Driver start | 每 run 的 `start_lock`、权威 `RunRecord`、唯一 LiveRun task | 已运行或已终态时为空操作 | 未知 phase 关闭失败 | 只有 `launched` 非终态可重新进入对应 Driver |
| Terminal delivery | 终态转换和持久 delivery intent 在一个 UoW 提交 | 不重复投影已有终态 | reject/cancel/expiry 上的非终态 delivery 无效 | Goal / projection delivery 可跨重启 |
| Workflow admission | 预创建 execution 上的 typed admission claim | 重放 consume 不重复调度 | 缺失或过期 claim 关闭失败 | 只有已 claim 的 Workflow 可恢复 |
| 产品准备 | 准备前恢复可信请求身份 | 同一可信请求不重复准备 | 错会话身份被拒绝 | Kernel 保留最终 start-time TOCTOU 校验 |

覆盖测试：`test_r55_admission_kernel.py`、`test_execution_continuations_uow.py`、`test_run_kernel.py`、`test_product_venue_chain.py`。最终独立审查未发现阻塞级幂等或快速终态重复审批缺陷。
