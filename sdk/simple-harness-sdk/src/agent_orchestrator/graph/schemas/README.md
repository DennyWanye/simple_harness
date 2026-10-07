# 执行图对外合同 Schema

十份 Draft 2020-12 文件，随 SDK 包交付（原计划 §4 / 附录 E；推后第 3 批 U09 加执行过程两份）。生产代码不用它们校验，校验只走各自的 Python 边界 codec；两边对同一正负样本一致由 `tests/orchestrator/full_target/test_taskgraph_schema_contracts.py` 守住。

| 文件 | codec |
|---|---|
| network-document-v1 | `graph/network_codec.py` |
| preview-binding-v1 | `graph/execution_contracts.py` |
| followup-v1 | `graph/notification_contracts.py` |
| taskgraph-error-v1 | `graph/notification_contracts.py` |
| taskgraph-view-v1 | `graph/view_contracts.py` |
| taskgraph-explanation-v1 | `graph/view_contracts.py` |
| taskgraph-diff-v1 | `graph/structural_diff.py` |
| taskgraph-convergence-view-v2 | `graph/view_contracts.py` |
| taskgraph-execution-view-v1 | `graph/view_contracts.py` |
| taskgraph-execution-detail-v1 | `graph/view_contracts.py` |

## 修订记录

- 2026-10-07 新增 `taskgraph-execution-view-v1`（`taskgraph.execution_snapshot`，执行图主画面）与 `taskgraph-execution-detail-v1`（`taskgraph.execution_detail`，回合详情）（推后第 3 批 U09，HTN §17.2）。字段与上界照 `orchestrator/taskgraph_execution_view.py` 的真实输出；节点按 `kind`、回合条目按 `t` 分种，`anyOf` 表达。主画面的 `graph` 与 `read_token` 用 `$ref` 指 `taskgraph-view-v1`，不另抄一份；回合详情的 `node` 指主画面里的节点定义。Host 与前端都核这两份（Host 走 codec，前端读 Schema）。
- 2026-10-06 收敛视图 v1 → v2（第 2 批 T07）。HTN 补齐阶段 B 第 2 条给收敛视图加了 `blocked_notifications`（连败被挡的推进通知，界面"改计划进度"面板据此显示"重新发送"），但 v1 顶层 `additionalProperties:false` 只许五个字段。界面 `tauri-app/src/views/PlanChangePanel.tsx` 在读这个字段，所以合同升一版：`schema_version` 为 2，顶层加必填 `blocked_notifications`（每条 `message_id`、`row_version`、`kind`、`subject_key`、`error_code`、`attempts`）。开发期不留 v1。
- 2026-10-06 其余七份原样从 `plans/TaskGraph/v1/simpleharness-taskgraph-code-plan-kit.zip` 的 `schemas/` 搬入。
- 2026-10-06 v2 顺带给 `jobs[].row_version` 补上 `maximum: 9007199254740991`（v1 只有 `minimum: 1`）：八份合同里其余整数都有安全整数上界，codec 也一直这么核，补齐后 Schema 与 codec 对"越界整数"负样本的判法一致。
