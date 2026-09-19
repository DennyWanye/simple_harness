你是 H1-H 独立核验员，不改生产代码、不提交。核验对象为 H1-H 实施提交及其 focused tests。

必须核对：

- 新旧协议入口分流；HTTP 字段是真行为，不接受源码字符串断言；
- 旧协议 `PlanningRejected` 字段/字节不变，且无 `PlanningDecision*` 事件；
- 新协议旧拒绝事件与 `PlanningDecisionEvaluated` 双发，载荷字段齐全；
- `attempt_ordinal` 从 0 开始，格式重试复用现有梯子且最多 1 次；
- `WAIT`/`NO_CHANGE` 是 `NO_STATE_CHANGE`，无 compile/PlanRevision；
- `BIND_EXISTING_GOAL`、`REPAIR/PROPOSE_SUCCESSOR` 被明确拒绝为 `DECISION_TYPE_NOT_ENABLED`；
- 同 request+ordinal+raw replay 不重复 compile/event；同 ordinal 不同 raw 是 Store identity conflict；
- 至少 12 个行为可区分 mutation 全部被 focused tests 杀死；
- 运行变更相关测试、ruff、full_target 全量、旧模式 560 条，并记录 0 新失败、sentinel ≤22、mutation ≥12 killed。

任何无法由现有 request/world/registry 无损构造 `AdmissionContext` 的路径都标为 blocker，不用空默认值补齐。
