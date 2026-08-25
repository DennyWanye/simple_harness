# Project-scoped Sessions verification scenario matrix

所有场景都是 required；实际结果只能写入 gate run 账本，本文仅定义冻结前的黑盒矩阵。原始证据目标统一为
`.local-test-evidence/<YYYY-MM-DD>/<run-id>/<scenario-id>/`。

| scenario_id | testcase | obligations | required | ui | gate_type | required_lanes | min_root_runs | input_class | cold_start | expected_run_created |
|---|---|---|:---:|:---:|---|---|---:|---|:---:|:---:|
| S-PS-01 | TC-PS-01 | TO-A1, TO-R5 | true | true | contract | fresh | 1 | deterministic-project-picker | false | false |
| S-PS-02 | TC-PS-02 | TO-A2, TO-R1 | true | true | temporal-fault | fresh, temporal-fault | 2 | deterministic-session-create | false | true |
| S-PS-03 | TC-PS-03 | TO-A3 | true | true | negative-safety | fresh, history-upgrade | 2 | deterministic-projectless | false | true |
| S-PS-04 | TC-PS-04 | TO-A4, TO-R2 | true | true | negative-safety | fresh, temporal-fault | 3 | deterministic-authority-conflict | false | true |
| S-PS-05 | TC-PS-05 | TO-A5, TO-R3 | true | true | contract | fresh, concurrency | 1 | deterministic-sidebar | false | true |
| S-PS-06 | TC-PS-06 | TO-A6 | true | true | positive-value | fresh | 3 | deterministic-root-split | false | true |
| S-PS-07 | TC-PS-07 | TO-A7 | true | true | temporal-fault | fresh, temporal-fault | 2 | deterministic-relocation | false | false |
| S-PS-08 | TC-PS-08 | TO-A8, TO-R4 | true | true | history-upgrade | history-upgrade, temporal-fault | 2 | deterministic-migration | false | false |

`min_root_runs` 表示证明该场景所需的独立 root/attempt 数，不代表输入语义类别；本功能三维 applicability
均为 false。本轮目标平台为 macOS；Windows path identity probe 按用户 2026-08-25 的范围调整保留为后续工作，不阻断本轮 gate。
