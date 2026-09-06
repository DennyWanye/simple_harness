# Fixed626 formal bounded execution — 2026-09-06

固定 runner `626ff8d8c14d8fa3026f4a99296a91657a9ad869`，H073/M0613身份同RESULTS。
本次no --observe；11个新bounded invocation覆盖互不重叠的391public+10source原格。
这是同固定源分批requested-cell union，**不是单个full401 Run或质量/机器gate**；
不引入旧2格观察结果，不拼其他源码版本。无业务代码修复、无SDK改动。

## 当前判定

- Public391：182PASS /0FAIL /209BLOCKED，355OBSERVED，36executor未实现。
- Source10：0PASS /0FAIL /10BLOCKED，全部真实OBSERVED。
- 本次原401分批并集：182PASS /0FAIL /219BLOCKED；未闭合义务保留，不称全量通过。
- 219BLOCKED互斥分类：FIXTURE_INVALID_OR_INSUFFICIENT 122，ORACLE_GAP 61，EXECUTOR_UNIMPLEMENTED 36。
- 未发现正式FAIL，因此没有为转绿修改consumer/oracle/setup/API。setup拒绝是未达到原场景验收前提，不是对应负例PASS。
- 原applicability三格仍CELL_EXECUTOR_NOT_IMPLEMENTED；原dirty模块未复制/修改。

## 逐批实际资源与计数

全部使用主固定145baed3入口、默认同一OS锁、2048MiB/180秒。每批独立fresh目录，全部exit3，
因为未选格/未闭合义务仍在。所有process group均remaining=[]，无timeout/RSS/cleanup异常。

| Batch | Cells | PASS | FAIL | BLOCKED | PGID | Peak KiB | Seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| p00 | 2 | 2 | 0 | 0 | 28483 | 134848 | 0.655 |
| p01 | 45 | 11 | 0 | 34 | 28499 | 138736 | 4.504 |
| p02 | 45 | 36 | 0 | 9 | 28529 | 136368 | 4.096 |
| p03 | 45 | 37 | 0 | 8 | 28561 | 135680 | 4.096 |
| p04 | 45 | 37 | 0 | 8 | 28592 | 135776 | 4.108 |
| p05 | 45 | 26 | 0 | 19 | 28624 | 136160 | 4.107 |
| p06 | 45 | 0 | 0 | 45 | 28657 | 135888 | 4.108 |
| p07 | 45 | 11 | 0 | 34 | 28684 | 135888 | 3.881 |
| p08 | 44 | 8 | 0 | 36 | 28716 | 136272 | 2.796 |
| p09 | 30 | 14 | 0 | 16 | 28737 | 134704 | 1.312 |
| s00 | 10 | 0 | 0 | 10 | 28752 | 147904 | 2.175 |

## Source层证据边界

`/Users/denny/projects/simple-harness-memory-sdk-0613-runner-source` detached exact
`f2a6a706c5e3407e896ada3bd9e735cd9c0b77fd`、clean；source adapter执行前后核clean/source/wheel字节。
使用既有typed_recall_source_cases.py私有SQLite backend/fault injector/派生corruption接口，严格只在source层。
7格实际two-source no-fault对照、指定事务seam/ordinal、precommit old/postACK exact committed恢复；
3格actual member corruption后reopen拒绝。全部仍缺完整PK/nonfinal roots或完整member hash/rejection-layer oracle，
因此10格均BLOCKED，不能把部分business assertions当原AC完成，不能冒充public。

Public worker -I/site-packages H073/M0613，无SDKoverlay，H164/M72包文件核字节。
无模型/native/全suite/新wheel。source层证据不混入public。

## 每格分类与原始证据

每格cell_id、layer、batch、execution、acceptance、category、reason均保存在本机ignored
`cell-classification.json` 与可读 `cell-classification.md`；Git只保留本小结、每批Run/hash索引。
- `.local-test-evidence/2026-09-06/typed-recall-0613-formal/selection.json` SHA256 `a21171549b0a25515e89ffae846ff4e130a1f9dd6efb80bd3be6b6e41950352a`
- `.local-test-evidence/2026-09-06/typed-recall-0613-formal/run_batch.py` SHA256 `2b8b052224b446c996234a50b8d2f6b6e7b4af865b76c720a2bfc13a89e76714`
- `.local-test-evidence/2026-09-06/typed-recall-0613-formal/cell-classification.json` SHA256 `d9a00950473c1363ba2620325fd0296e8e05b23ed73e18d971d0aa37045e3f40`
- `.local-test-evidence/2026-09-06/typed-recall-0613-formal/cell-classification.md` SHA256 `a3c3d45986dd77f7ee9d4232baa7201940a354e4719c7f64268e4174d7c973e7`

| Batch | Actual Run ID | Summary SHA256 |
|---|---|---|
| p00 | d695fd0f1599499b84e5fd5763129404 | 874c172ea21f2e59facfaeded1cfa8cb8f32674c349c8cade2820f917e1a5bb8 |
| p01 | 7ad963bc71c841daabf0f621cff15a86 | 4c00bd793e80498b1a20dd9c3605cd50a8aabdcf87caa6b29f62876a4eeea90c |
| p02 | 019df69b0553461a8c3dc5996226929b | 5a3d35a5cb42a253d02f202cb0da23f8b2ebf78f53febcb091a66546189a319f |
| p03 | 8f0c2e013a984552acee59dca83b314f | eaf094610ead02a09edf637521747cd2a2506e159ead2bf855da977b165956ee |
| p04 | 9e9123245a194e0e98e685d14b683c50 | 73b69d2c64548d460262cebcbf1e847dc95542f7341f6c8d1f4e0ecb1711b21c |
| p05 | 593b232d2c294880bf2e804732fc52e8 | 339014f51129799719bcc24928296f5f51cecbb5c53de619ae497d87e50ff456 |
| p06 | bc1411f0e5634ac080d70b0ae7fb537f | 511a32fa9f0f96e3ac1b96842f14d7aceebe3cf0bf602d1a167357c11992de1f |
| p07 | 4274f3ce2a9a4f9385776f791b8b485b | 69743df107dad50b6c5adf07c592bc35f186bbb21141a09074b8dd3daef0ea49 |
| p08 | c04a4cb33077433889e34e400f7600a6 | b00980864adc31b0ba200f54a8466e6145ed10df6263c566142b6d063e8af222 |
| p09 | 4507b5b7faa2436c87c623ed9ea8bf32 | 22a1b17b64f3ff5da85c25f99245da10868c711b7cddb165f095a46d2625e002 |
| s00 | 222d8ea04f2647748e58ed80d2514a04 | b8fca47224c17d1cf4f8a27ba7a7d317b5a8f866e69a1f25ecddf74d405d6d44 |

所有完整命令在该ignored根目录 `<batch>-command.json`，每批raw位于 `<batch>/public-data/` 和 `<batch>/resource/`。

## 未闭合原因分组

- 36格 `CELL_EXECUTOR_NOT_IMPLEMENTED`；例如 `current-use/authority:classification_change`。
- 6格 `CONFLICT_DURABLE_GROUP_MEMBER_RESOLUTION_HASH_ORACLE_PENDING`；例如 `conflict-state/contest-create-distinct-evidence`。
- 10格 `CONFLICT_REJECTION_OR_PRECONDITION_REQUIRES_FULL_ORACLE`；例如 `conflict-state/contest-active-group-exists`。
- 3格 `CORRUPTION_EXACT_REJECTION_LAYER_AND_FULL_MEMBER_HASH_ORACLE_PENDING`；例如 `conflict-state/contested-tampered-cross-memory-reopen`。
- 6格 `CURRENT_USE_ORIGINAL_TWO_ITEM_EPOCH_CONTINUATION_ORACLE_PENDING`；例如 `current-use/authority:suppression`。
- 1格 `FROZEN_128_BYTE_PAGE_BOUND_CANNOT_FIT_PUBLIC_BINDING`；例如 `protocol/page-correct-binding`。
- 5格 `FULL_ORIGINAL_CELL_ADMISSION_PENDING`；例如 `eligibility/valid-until-null-unbounded`。
- 1格 `FULL_SOURCE_RECORD_CANARY_AND_CROSS_SCOPE_SETUP_NOT_ESTABLISHED`；例如 `selection-budget/projection:semantic`。
- 1格 `FULL_SOURCE_RECORD_CANARY_AND_CROSS_SCOPE_SETUP_NOT_ESTABLISHED;ORIGINAL_EPISODE_OCCURRED_INTERVAL_DIFFERS_FROM_PUBLIC_PROJECTION`；例如 `selection-budget/projection:episode`。
- 2格 `FULL_SOURCE_RECORD_CANARY_AND_CROSS_SCOPE_SETUP_NOT_ESTABLISHED;REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED`；例如 `selection-budget/projection:procedure`。
- 12格 `PUBLIC_CASE_PRECONDITION_REJECTED:MemoryValidationError:mutation_inference_cannot_be_authoritative`；例如 `eligibility/epistemic:episode:llm_inference:source_bound`。
- 6格 `PUBLIC_CASE_PRECONDITION_REJECTED:MemoryValidationError:mutation_observed_procedure_cannot_activate`；例如 `eligibility/epistemic:procedure:observed_behavior:repeated_observation`。
- 12格 `PUBLIC_CASE_PRECONDITION_REJECTED:MemoryValidationError:mutation_unknown_cannot_be_authoritative`；例如 `eligibility/epistemic:episode:unknown:source_bound`。
- 1格 `PUBLIC_CASE_PRECONDITION_REJECTED:ValueError:'eligible' is not a valid ProcedureLifecycleState`；例如 `eligibility/procedure-eligible-state`。
- 24格 `PUBLIC_CASE_PRECONDITION_REJECTED:ValueError:audit disclosure requires an AuditAccessDecision and audit recipient`；例如 `eligibility/disclosure:EXTERNAL_PARTY:AUDIT:PERSONAL`。
- 16格 `PUBLIC_CASE_PRECONDITION_REJECTED:ValueError:verified states require trusted typed observation evidence`；例如 `eligibility/epistemic:episode:llm_inference:repeated_observation`。
- 16格 `PUBLIC_CASE_PRECONDITION_REJECTED:ValueError:verified_external requires source_verified state`；例如 `eligibility/epistemic:episode:verified_external:repeated_observation`。
- 1格 `PUBLIC_EXCEPTION_HAS_NO_PER_INVOCATION_CANDIDATE_READ_WITNESS`；例如 `unsupported-replay/conflicting-replay`。
- 3格 `PUBLIC_EXECUTED_LANE_WITNESS_UNAVAILABLE`；例如 `selection-budget/vector-not-requested`。
- 30格 `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED`；例如 `eligibility/epistemic:procedure:explicit_user:repeated_observation`。
- 8格 `RETURN_CELL_COMPLETE_ATTACK_AND_READ_WITNESS_ADMISSION_PENDING`；例如 `protocol/cognitive-missing-revision`。
- 6格 `SHORT_COMPLETE_REGISTRATION_CLASSIFICATION_TIME_AND_ORIGINAL_HASH_BINDINGS_PENDING`；例如 `eligibility/short-chain-complete`。
- 7格 `SOURCE_FULL_STATE_PK_AND_NONFINAL_ROOT_BINDINGS_PENDING`；例如 `fault-recovery/fault:before-result-header`。
- 6格 `STATE_COMPLETE_RECEIPT_AND_PROTECTED_TRANSITION_BINDING_PENDING`；例如 `conflict-state/contested-dependent-partial`。

所有测试进程已结束，槽位释放。后续需先闭合明确fixture/oracle/executor缺口，再按新固定源定向验证，不能将本并集迁移为新版本成功。
