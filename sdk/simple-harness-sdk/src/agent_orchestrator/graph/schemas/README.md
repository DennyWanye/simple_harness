# 执行图对外合同 Schema

八份 Draft 2020-12 文件，随 SDK 包交付（原计划 §4 / 附录 E）。生产代码不用它们校验，校验只走各自的 Python 边界 codec；两边对同一正负样本一致由 `tests/orchestrator/full_target/test_taskgraph_schema_contracts.py` 守住。

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

## 修订记录

- 2026-10-06 收敛视图 v1 → v2（第 2 批 T07）。HTN 补齐阶段 B 第 2 条给收敛视图加了 `blocked_notifications`（连败被挡的推进通知，界面"改计划进度"面板据此显示"重新发送"），但 v1 顶层 `additionalProperties:false` 只许五个字段。界面 `tauri-app/src/views/PlanChangePanel.tsx` 在读这个字段，所以合同升一版：`schema_version` 为 2，顶层加必填 `blocked_notifications`（每条 `message_id`、`row_version`、`kind`、`subject_key`、`error_code`、`attempts`）。开发期不留 v1。
- 2026-10-06 其余七份原样从 `plans/TaskGraph/v1/simpleharness-taskgraph-code-plan-kit.zip` 的 `schemas/` 搬入。
- 2026-10-06 v2 顺带给 `jobs[].row_version` 补上 `maximum: 9007199254740991`（v1 只有 `minimum: 1`）：八份合同里其余整数都有安全整数上界，codec 也一直这么核，补齐后 Schema 与 codec 对"越界整数"负样本的判法一致。
