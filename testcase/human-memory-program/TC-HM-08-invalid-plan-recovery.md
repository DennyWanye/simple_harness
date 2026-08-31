---
id: TC-HM-08
purpose: Verify invalid LLM plans and cross-repository initialization fail closed and recover without half state
status: active
surface: api
type: scripted
obligations: [HM-TO-A2, HM-TO-A3, HM-TO-A4, HM-TO-A7, HM-TO-A8, HM-TO-R2, HM-TO-R4, HM-TO-R6, HM-TO-R8, HM-TO-R9]
tags: [human-memory, llm-adversarial, protocol, initialization, replay]
entrypoint: public SDK contracts and fresh Host data directory
revision: 4
---

# TC-HM-08 rev4 — 非法 LLM 计划、关系完整性、初始化与跨仓恢复

## 固定故障矩阵

- LLM：timeout、refusal/no-plan、乱序依赖、重复 operation、缺 evidence ref、非法枚举、字段错位、循环依赖、超长字段。
- Runtime：Memory/Harness/Host 版本不匹配、init 各事务边界 crash、outbox lost-ACK、embedding unavailable、并发 Worker。
- 所有 seam ID、runner command、seed、terminal oracle 以 `fixtures/fault-matrix.json` 为准；SHA-256
  `b4dcb2f39a2e5c2f7afeeb1dd496aa94587fe075c8772ea44fab880115bc74da`。缺少对应 runner 的 lane 是 NOT_RUN/BLOCKED，不能自由选择替代 seam。
- 一等语义关系完整性 Oracle 以 `fixtures/semantic-relation-integrity-v1.json` revision 1 为准；它冻结
  positive endpoint、16 个 admission rejection、4 个事务 seam、重放、owner/endpoint lifecycle、重启损坏、
  trace/root 与原始证据永不物理删除；SHA-256
  `e56b8f88e0ed91a03a7b093c0d615a1b290089bd28464d4d9b8790b1552d8172`。正式执行前保持
  `NOT_RUN/BLOCKED`，不得把 self-check 当产品 PASS。
- integrity evidence verifier：`runners/run_semantic_relation_integrity_evidence.py`，SHA-256
  `1429e8fc06e78571917cc2e514f66db55a936d97d715ec60a772f05970d32bd8`。它要求冻结的 40 个 case
  各自提供 observed outcome/reason、前后五类 roots、五类 row cardinality、raw-evidence 前后 hash、reopen result、
  exact candidate identity 与本地 ignored artifact ref/hash；少一项或少一个 case 都不能 PASS。
- 关系 malformed wire 在进入 Memory SDK 前由 Harness 返回有界、credential-safe、稳定 reason code；Memory 调用数和
  durable delta 都必须为 0。Host 的 durable pre-admission audit 是 S5 独立能力，在 S5 完成前必须明确报告
  `NOT_RUN/BLOCKED_UNTIL_S5`，不能用 Memory 的 post-admission rejection audit 冒充。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 从空目录并发启动初始化并在每个 checkpoint kill/retry。 | 只产生一条可写主对话、完整 schema/index/worker state；其余 attempt 幂等收敛，无半初始化。 |
| 2 | 通过三仓公开 API 运行 consumer contract；逐一替换错误协议版本或 canonical 字段。 | 正确版本通过；不兼容版本在执行/写状态前 fail closed，reason 和版本可审计。 |
| 3 | 对 RecallPlan、MemoryMutationPlan、TaskScopeProposal/MutationPlan 逐项注入固定 LLM 变异。 | 仅合法、证据齐全、依赖无环的 plan 可提交；非法 plan 不扩大召回/权限且不留半状态。 |
| 4 | 对同一 canonical request 在相同与不同 recipient/environment/plan version 下 replay。 | 相同输入 hash/结果稳定；安全相关字段变化必改变 canonical hash，不误复用旧 decision。 |
| 5 | 扫描数据库、object/log/docs/vector input/ContextSnapshot 的 credential canary。 | key/token/cookie/认证材料与隐藏 reasoning 零命中；允许的受控 input/output hash 和结构化 decision 仍完整。 |
| 6 | 对 `applies_to` 关系逐项运行冻结的 positive/rejection 矩阵。 | 只允许 active、同 principal、exact revision 的 Semantic claim → Procedure/Prospective；非法 kind/type/self-loop/relation endpoint/依赖/evidence/classification 均在正确边界 fail closed，零半状态。 |
| 7 | 在 relation memory insert、knowledge row insert、commit/ack 边界逐一 fault，并做 exact/conflicting replay。 | 每个 seam 只允许 all-old 或 all-new；exact replay receipt byte-identical 且不重复，conflicting replay 稳定拒绝且零状态变化。 |
| 8 | SUPPRESS/CONTEST/SUPERSEDE relation owner 或任一 endpoint，再 close/reopen；随后注入 owner/domain/hash/FK 损坏。 | 非 active owner/endpoint 的 edge 立即退出；重启不复活；任何持久化完整性损坏均 fail closed 且公开图零泄露。 |
| 9 | 对拒绝、fault、replay、lifecycle、reopen 前后核对 evidence bytes、trace 与 manifest roots。 | 原始 evidence byte-identical、物理删除数 0；evidence→plan→operation→relation memory→knowledge row→graph edge 可追溯，所有指定 roots 可独立复算。 |

本 relation 增量只把步骤 6–9 作为 required；步骤 1–5 继续作为 program-wide regression，不重复计入 relation slice 的完成门。

## 关系完整性冻结命令

```bash
python3 testcase/human-memory-program/runners/run_semantic_relation_integrity_evidence.py --self-check
```

```bash
python3 testcase/human-memory-program/runners/run_semantic_relation_integrity_evidence.py \
  --harness-wheel <exact-harness-wheel> \
  --harness-wheel-sha256 <exact-harness-sha256> \
  --harness-source-commit <exact-harness-commit> \
  --memory-wheel <exact-memory-wheel> \
  --memory-wheel-sha256 <exact-memory-sha256> \
  --memory-source-commit <exact-memory-commit> \
  --evidence-index .local-test-evidence/<date>/<run>/relation-integrity-index.json \
  --artifact-root .local-test-evidence/<date>/<run>
```

## 决定性证据

- 三仓 wheel/version/hash、init state、fault matrix、relation integrity matrix、状态行数/hash、reason codes、public consumer output、manifest roots 和 canary scan。
