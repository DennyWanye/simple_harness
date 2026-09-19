你是 H1-H 接线实施者。只在 SDK 独立 worktree 中工作，基线为 `main@5ac3f05` 加 HTTP 入口修复 `af56b90`。先读：

- `simpleharness-llm-native-htn-execution-plan-v2.zh-CN.md` §35–47；
- `LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md` §五–八及 2026-09-19 15:20 追加裁定；
- `H1-V2对照源码冲突检查-2026-09-18.zh-CN.md` §1、§5、§6；
- `planning/decision_codec.py`、`planning/decision_admission.py`、`planning/decision_adapter.py`、`storage/planning_decision_store.py`。

允许改动：`src/agent_orchestrator/api/missions.py`、`src/agent_orchestrator/orchestrator/event_handler.py`、`src/agent_orchestrator/orchestrator/hierarchical_dispatch.py`、必要的 H1-H focused tests，以及本任务书列明的 journal。不得改旧编译器语义、旧协议字节、H1-A–G 合同。

实施目标：

1. `planning_protocol_version` 从真实 HTTP request 进入 `MissionSpec`；缺省旧协议字节不变，未知/非字符串拒绝。
2. 旧协议走 `_collect_plan_hierarchical` 原路径，旧 `PlanningRejected` 字段与字节不变，旧任务不出现任何 `PlanningDecision*` 事件。
3. 新协议按 request ordinal 从 0 开始，复用现有“提案不可读→带 repair hint 重问”梯子，格式重试最多 1 次；不得新建第二套 retry loop。
4. 新协议主链为 codec → planning decision store → admission → adapter → 原有 ground/compile/commit；`WAIT`/`NO_CHANGE` 只持久化为 `NO_STATE_CHANGE`，不 compile、不产生 PlanRevision；`NO_CHANGE` 不走 `NoApplicableMethodDeclared`。
5. 新协议每次评价同时发旧 `PlanningRejected` 和 `PlanningDecisionEvaluated`；后者字段固定为 `decision_id, request_id, attempt_ordinal, decision_type, status, rejection_codes, canonical_hash`。旧协议不发新事件。
6. `BIND_EXISTING_GOAL` 与 `REPAIR/PROPOSE_SUCCESSOR` 本阶段只解码，准入回 `DECISION_TYPE_NOT_ENABLED`，不得静默执行或伪造旧提案。

先写行为测试再实现。若无法从现有 request/world/registry 记录无损组装 `AdmissionContext`，停止并在 journal 记录 blocker，不得用默认空值绕过准入。
