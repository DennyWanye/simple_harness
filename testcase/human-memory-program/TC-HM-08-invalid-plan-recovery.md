---
id: TC-HM-08
purpose: Verify invalid LLM plans and cross-repository initialization fail closed and recover without half state
status: active
surface: api
type: scripted
obligations: [HM-TO-A2, HM-TO-A3, HM-TO-A4, HM-TO-A7, HM-TO-A8, HM-TO-R2, HM-TO-R4, HM-TO-R6, HM-TO-R8]
tags: [human-memory, llm-adversarial, protocol, initialization, replay]
entrypoint: public SDK contracts and fresh Host data directory
revision: 3
---

# TC-HM-08 — 非法 LLM 计划、初始化与跨仓恢复

## 固定故障矩阵

- LLM：timeout、refusal/no-plan、乱序依赖、重复 operation、缺 evidence ref、非法枚举、字段错位、循环依赖、超长字段。
- Runtime：Memory/Harness/Host 版本不匹配、init 各事务边界 crash、outbox lost-ACK、embedding unavailable、并发 Worker。
- 所有 seam ID、runner command、seed、terminal oracle 以 `fixtures/fault-matrix.json` 为准；SHA-256
  `c67881ae20a3f6b442f1ac46db9e6e9a472edc3ec09079e8a42ef219e9b6bc6b`。缺少对应 runner 的 lane 是 NOT_RUN/BLOCKED，不能自由选择替代 seam。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 从空目录并发启动初始化并在每个 checkpoint kill/retry。 | 只产生一条可写主对话、完整 schema/index/worker state；其余 attempt 幂等收敛，无半初始化。 |
| 2 | 通过三仓公开 API 运行 consumer contract；逐一替换错误协议版本或 canonical 字段。 | 正确版本通过；不兼容版本在执行/写状态前 fail closed，reason 和版本可审计。 |
| 3 | 对 RecallPlan、MemoryMutationPlan、TaskScopeProposal/MutationPlan 逐项注入固定 LLM 变异。 | 仅合法、证据齐全、依赖无环的 plan 可提交；非法 plan 不扩大召回/权限且不留半状态。 |
| 4 | 对同一 canonical request 在相同与不同 recipient/environment/plan version 下 replay。 | 相同输入 hash/结果稳定；安全相关字段变化必改变 canonical hash，不误复用旧 decision。 |
| 5 | 扫描数据库、object/log/docs/vector input/ContextSnapshot 的 credential canary。 | key/token/cookie/认证材料与隐藏 reasoning 零命中；允许的受控 input/output hash 和结构化 decision 仍完整。 |

## 决定性证据

- 三仓 wheel/version/hash、init state、fault matrix、状态行数/hash、reason codes、public consumer output 和 canary scan。
