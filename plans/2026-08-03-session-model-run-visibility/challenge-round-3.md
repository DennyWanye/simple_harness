# Plan challenge 第 3 轮

## 致命问题

1. state.db 仍只有 v22；binding lifecycle、Context Usage、workload audit、archive projection DDL 没有完整迁移版本链和启动执行顺序。
2. binding 清空/继承/重新绑定会产生 epoch ABA；provider incarnation 在 TOML、binding 在 SQLite，缺少 reconcile readiness barrier。
3. breaker 没有 error failure scope、open_until/cooldown、half-open 触发、额度恢复后的显式 reset。
4. `MessageStreamPanel.tsx` 仍从 raw Inspector 构造聊天行，漏改会导致 raw payload 和新 phases 重复展示。
5. SemanticPhase 只有 taxonomy，没有逐 fact/tool 的版本化映射优先级和 phase status reducer。

## 真架构问题

- Provider identity lifecycle 必须由持久 incarnation、durable binding tombstone/epoch、幂等 reconcile marker 和 product ingress readiness barrier 共同构成。
- 图和消息框必须只消费同一 public contract；raw ledger 只保留诊断引用。
- Provider 错误分类、failure scope、breaker key 和恢复策略必须是显式策略表。

## 次要问题

- audit 清理触发点/批量需冻结。
- manifest 默认上限必须保证 1,500 facts 通过。
- blocked 结构化 code 集合需枚举。

VERDICT: FAIL

## 计划修订结果

- Task 1/4 已冻结 v23～v26 迁移链、durable binding tombstone/epoch、provider incarnation reconcile
  marker 与 ingress readiness barrier。
- Task 3 已冻结 `ProviderFailurePolicyV1`、不同错误的失效域、cooldown/half-open/reset、取消与 retention。
- Task 8 已把 `MessageStreamPanel` 纳入同一 `HarnessPublicSnapshotStore`，删除生产 raw trace 重建路径，
  并增加旧 schema 安全 fallback 与 raw-field 禁用测试。
- Task 7 已冻结 `SemanticPhaseMappingV1` 的逐类优先级、稳定 phase id、状态 reducer 和未知映射语义。
- Task 5 已冻结 manifest 默认 TTL/LRU/row/byte 上限；Task 6 枚举 `RootBlockReasonV1`。

上述修订进入第 4 轮独立挑战，第三轮 FAIL 不改写为 PASS。
