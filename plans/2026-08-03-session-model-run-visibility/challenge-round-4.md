# Plan challenge 第 4 轮

## 致命问题

1. 015～018 迁移若落入现有默认 `executescript()` 分支，DDL、marker、user_version 不能保证同事务；
   Registry 仍在模块加载期构造，ingress readiness 也挡不住后台 router。
2. provider add/update/remove 涉及内存、TOML、keychain，只有 lock/change event 仍可能发布半提交状态；
   Session bind 与 remove/re-add 并发缺少同一 CAS/锁边界。
3. SemanticPhase 缺少跨库无序事实的确定性总排序；blocked 没有结构化 producer；历史 Run 若读取 live
   ToolSpec，会因插件升级/删除产生不同投影。

VERDICT: FAIL

## 计划修订结果

- Task 1/4 已冻结统一原子 migration runner、lifespan 唯一启动顺序、router readiness 自检，以及
  Registry 的 keychain stage → TOML durable commit → memory switch → event 协议和 binding CAS。
- Task 5/7 已增加 presentation-only 的 Root tool spec snapshot、legacy 固定 fallback、因果 DAG 拓扑
  排序和完整 phase 状态真值表。
- Task 6 已增加四个结构化 `RunBlockSignalV1` producer；无 signal 不得从文案推断 blocked。

上述修订进入第 5 轮独立挑战，第四轮 FAIL 不改写为 PASS。
