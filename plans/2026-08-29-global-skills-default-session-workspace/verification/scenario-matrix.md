# Global Skill / ordinary workspace scenario matrix

所有场景 required；历史 PASS 不代替当前 HEAD 执行。原始证据统一写入 `.local-test-evidence/2026-08-29/<run-id>/<scenario-id>/`。

| scenario_id | testcase | required | ui | gate_type | required_lanes | min_root_runs | input_class | cold_start | expected_run_created |
|---|---|:---:|:---:|---|---|---:|---|:---:|:---:|
| S-GS-01 | TC-GS-01 | true | true | positive-value | fresh,restart | 2 | deterministic-default-workspace | true | true |
| S-GS-02 | TC-GS-02 | true | true | negative-safety | selected,cancel,invalid | 2 | deterministic-folder-picker | false | true |
| S-GS-03 | TC-GS-03 | true | true | positive-value | settings,chat,idempotent-replay | 2 | deterministic-global-install | false | true |
| S-GS-04 | TC-GS-04 | true | true | positive-value | old-session,automatic,selected,in-flight | 4 | deterministic-global-snapshot | false | true |
| S-GS-05 | TC-GS-05 | true | true | cold-start | cold-start,restart,provider | 3 | deterministic-cold-recovery | true | true |
| S-GS-06 | TC-GS-06 | true | false | temporal-fault | adversarial-payload,temporal-fault,recovery | 0 | deterministic-fault-matrix | true | false |
| S-GS-07 | TC-GS-07 | true | true | negative-safety | old-session,automatic,selected,authorization | 3 | deterministic-descriptor-policy | false | true |
| S-GS-08 | TC-GS-08 | true | false | concurrency | concurrency,lost-ack,replay | 0 | deterministic-concurrency | false | false |
| S-GS-09 | TC-GS-09 | true | true | negative-safety | fresh,continuation,user-override,restart | 4 | deterministic-permission-policy | true | true |

`input_sensitive=false` 与 `llm_payload_driven=false`，所以重写自然语言不构成额外 required 场景；真实 Provider调用只证明生产 runtime/Skill链。`stateful_init=true`，S-GS-01/05/09 的 cold lanes 不得省略。
