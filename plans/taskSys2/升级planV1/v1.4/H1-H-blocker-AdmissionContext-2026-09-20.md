# H1-H 接线调查：AdmissionContext 字段映射待实现

日期：2026-09-20

## 结论

当前 SDK 主线 `5ac3f05890a4c5160e2f103a01f863753ea5d498` 已有
`PlanningDecisionStore`、codec、admission 和 adapter 合同，但生产 Planner 链尚未把它们接起来。
现状说明 builder 尚未实现，不能把“当前没有 builder”当作项目 blocker。H1-H 应继续实现逐字段绑定；只有某一字段确认没有权威来源时，才把该字段单独记为 blocker。

## 证据

- `src/agent_orchestrator/orchestrator/event_handler.py::_create_planner_intent`
  目前创建的是既有 `DispatchIntent`；它没有创建或持久化 `PlanningRequestBinding`，也没有调用
  `PlanningDecisionStore.insert_planning_request`。
- `_hierarchical_planner_package` 只构造并 seal 现有 Planner package；没有保存新协议要求的
  `planning_subjects`、`visible_refs`、package/prompt hash 与 request identity 的完整绑定。
- `src/agent_orchestrator/orchestrator/event_handler.py::_collect_plan_hierarchical` 仍直接调用
  `new_mode.apply_planner_reply`，没有 codec → store → admission → adapter 的新协议分支。
- `AdmissionContext` 要求当前 plan/requirements/scope revision、package/prompt 绑定、启用 decision
  types、methods/predicates、active method instances、obligations/resolutions/shareable goals、
  authorization/capabilities/budget/operations/plan shape 和 retry budgets；现有 Planner intent
  入口没有一份已持久化且可重放的请求快照可逐项提供这些值。
- `PlanningDecisionStore` 的 `insert_planning_request`、`record_planning_decision` 等方法本身存在，
  但当前生产入口没有调用点。

## 禁止的绕过

- 不用空的 `visible_refs`、methods、budget、plan shape 或默认 revision 伪造上下文。
- 不把当前 package 临时重算成 request binding 的替代品。
- 不让新协议直接调用旧 `apply_planner_reply`，否则会绕过 admission 和 durable decision identity。

## 下一步实现

在允许的 H1-H 文件内增加一个 request-binding/context builder：从同一次 Planner package、当前
hierarchical world/network、scope epochs、method applicability、预算和 operation ledger 取得值，
以同一 request snapshot 持久化 `PlanningRequestBinding`，再让新协议入口读取该 binding 构造
`AdmissionContext`。builder 必须逐字段失败关闭；缺任何权威来源继续记录 blocker，不用默认值。
