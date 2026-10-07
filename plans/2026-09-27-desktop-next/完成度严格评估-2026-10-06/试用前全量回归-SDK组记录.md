# 试用前全量回归 —— SDK 组记录（2026-10-08）

## 范围

- 仓库：主分支 HEAD 8030ec20b。
- 对象：SDK 逐文件重跑后仍红的 11 个文件，共 17 条。
- 重跑日志：`.local-test-evidence/2026-10-07/full-regression/sdk-rerun/`。
- 只改了 `sdk/simple-harness-sdk/tests/`。没有改 `src/`、清单、`.venv`。没有提交。

## 结论

| 类别 | 条数 |
|---|---|
| 环境 | 0 |
| 测试过时 | 17 |
| 产品缺陷 | 0 |

- 17 条全是测试没跟上产品的规划内改动。
- 没有删测试。被删功能只涉及个别断言，删的是断言，用例本身保留。
- 改后 11 个文件逐个单独跑全过（见末节）。

## 逐条

### 1. `tests/agents/test_context_journal.py`（1 条）

- 用例：`test_unknown_resume_reuses_the_frozen_request_without_a_new_selection`。
- 现象：召回探针的 `prewarm` 从没被调用，`recall.prepared == []`。
- 原因：a23ad799e（10-03，A′ 第 5 条）删了通用上下文的内置召回。`prewarm` 在 `src/` 里已无调用点。
- 归类：测试过时（被测功能已删）。
- 处理：按"开发期旧路径直接删"删掉三条召回断言和探针桩。保留本意：冻结请求恢复时不重选上下文、不重调模型、下一问照常走。

### 2. `assurance_exec/test_batch1_closeout_final_recheck.py`（1 条）

- 用例：`test_a01_expired_certificate_and_clock_rollback_block_the_closeout`。
- 现象：测试从证书表取的"全部证书"里多了 6 张 `assurance-point-use:*`，收尾到期清单里没有它们。
- 原因：1eddf79f0（10-07，推后第 1 批 A26）新增时点使用证书（PLAN/START/CONTEXT/DISCLOSE/RECOVERY）。它们在签发事务里就用掉，是历史。产品在 `live_usable_certificates` 和到期唤醒里按用途明确排除它们。
- 归类：测试过时。产品按设计做。
- 处理：测试取证书时同样按 `POINT_PURPOSES_SQL` 排除时点证书。到期、时钟回拨两项断言不变。

### 3. `full_target/test_between_cycles_host_duty.py`（5 条）

- 现象：桩对象缺 `_recovery`；补上后又缺 `_reconcile_actions`。
- 原因：
  - 0a10df5e0c（10-06，第 2 批车道 J）：`run()` 先看恢复是否降级，读 `self._recovery`。
  - 70fb74d85（10-03，HTN B3）：`run()` 改调 `self._reconcile_actions()`，不再走 `actions.reconcile`。
- 归类：测试过时（桩没跟上）。
- 处理：桩补 `_recovery=None`，`actions.reconcile` 换成 `_reconcile_actions`。五条断言不变。

### 4. `full_target/test_fixed_task_allowance_config.py`（1 条）

- 现象：`ValueError: Orchestrator needs the deployment's native runtime profiles`。
- 原因：a4442c922（10-03，A′ 删除批四）删了旧执行池。编排服务只接受部署给的原生执行池，`profiles` 必填。
- 归类：测试过时。
- 处理：按 `tests/orchestrator/p33/p33_world.py` 的 `native_options` 同一拼法传原生执行池。断言不变。

### 5. `full_target/test_publish_source_steps.py`（3 条）

- 现象：桩任务缺 `id`。
- 原因：8fcdb00cb（10-03，HTN E）准则只留一个来源。`_publish_source_steps` 改读现行要求书（`current_statements`），要用 `mission.id` 查要求书修订。
- 归类：测试过时。
- 处理：桩任务补 `id`；用 monkeypatch 让 `HtnStore.latest_requirements_revision` 返回空。这样产品代码按自身规则回落到章程。三条断言不变。

### 6～8. 三个旧版本库迁移测试（各 1 条，共 3 条）

文件：

- `test_schema_drop_selection_fragments.py`（第 30 版库升级，迁移 31）
- `test_schema_drop_conflicts_and_system_tail.py`（第 31 版库升级，迁移 32）
- `test_schema_drop_criterion_assessments.py`（第 32 版库升级，迁移 33）

现象：

- 前两个：重开库时报 `no such column: "reserved_cost_micros"`。
- 第三个：升级后多出 `method_library` 等新表，子集断言失败。

原因：

- 前两个的造库方式是"在当前库上重放旧迁移，再删掉 31/32 以后的记录"。重开时会重放迁移 38、40。迁移 38（f64e8adbe，删金额列）和迁移 40（0f8d8d9fd，建全库做法表）不能在已跑过它们的库上重放。
- 第三个断言"升级后的表是升级前的子集"。迁移 40、46（0a10df5e0c 恢复表）加了新表，断言不再成立。
- 产品迁移链仍完整：`Store._initialize_or_validate` 对任何旧版本库都备份后逐条升级，没有最低版本限制。实测第 30、31、32 版库都能一路升到当前第 48 版。

归类：测试过时（造库方式和断言没跟上）。按规矩"迁移链仍在产品里就修造库方式"，不删。

处理（三个文件同法）：

- 只跑前 N 条迁移，造一个真的第 N 版库（与原第三个文件同法）。
- 先只升一版，断言这一条迁移删掉的恰好是那几张表，不多不少，备份文件名对。
- 再按全部迁移打开，断言迁移链走得通、删掉的表不回来。

### 9. `gap_phase1/test_appworld_result_contract.py`（1 条）

- 现象：钉值 `worker-appworld-hierarchical-v2`，现为 `v3`。
- 原因：0f8d8d9fd（10-03，HTN C3）执行者提示词升版，AppWorld 分层执行者指向 v3。
- 归类：测试过时。
- 处理：钉值改为 v3。

### 10. `p34/test_role_for_task.py`（1 条）

- 现象：断言角色集合是 `{"critic","worker"}`，现只有 `{"worker"}`。
- 原因：eb7491046（10-03，A′ 删除批三）删了任务级 Critic 和 AppWorld critic 模板。
- 归类：测试过时（被测角色已删）。
- 处理：断言改为 `{"worker"}`。

### 11. `p35/test_provider_accounting_loop.py`（1 条）——重点看了记账

用例：`test_a_call_queued_for_the_only_slot_is_unbilled_and_a_cancel_never_hands_it_off`。

现象：取消前断言"排队那步（b）一条授权行都没有"失败。b 名下有一条 SETTLED、实际 150 token 的授权。

查证（临时打印库内授权行和各执行池调用表，打印后已用 cp 备份恢复）：

- a 那步（写 a.md）的调用被测试卡在半路，占着唯一槽位，授权 UNKNOWN、已交出 1 次。
- b 那步在 a 卡住**之前**已跑完第一次调用：交出 1 次、成功、按实际 150 token 结账。
- b 的第二次调用在排队：执行池里是 `claimed`、交出 0 次，**没有授权行**。
- 测试原假设"a 先拿到槽位、b 的第一次调用就在排队"。现在两步谁先拿槽位变了。b 的第一次先跑完是正常的公平调度。具体是哪次提交改了先后，没有逐个二分。`TaskGraph-补全-实施记录.md` 第 37 行已记它为"主分支同样失败的老失败"。

记账判断（"可以多算不可以少算但不冻结"）：

- 排队那次：取消后仍没有授权行，从没交出去，没到提供方。**没有多记**。
- b 已跑完那次：照实 150 token 结账，取消后不变。**没有少记**。
- a 卡住那次：放行后照实 150 token 结账（SETTLED）。**取消不丢账**。
- 两次尝试的预留几轮内都结清（SETTLED），首审尾款都 RELEASED，认领收干净。**没有冻结**。
- 结论：不是产品缺陷。是测试的时序假设过时。

处理（保留原意图，两种先后都成立）：

- 取消前：b 已有的授权必须全是已结清且有实际用量；排队那次调用不在授权表、交出 0 次。
- 取消后：b 的授权与取消前一样（排队那次没拿到授权）。
- 到达提供方的执行者调用数 = a 一次 + b 排队前跑完的次数。
- 等结账、取"在跑那步"的授权时排除 b。
- 其余断言（取消落地、预留结清、尾款释放、认领收净）不变。

## 产品缺陷清单

无。

## 改动文件（均在 `sdk/simple-harness-sdk/tests/` 下，未删文件）

1. `agents/test_context_journal.py`
2. `orchestrator/full_target/assurance_exec/test_batch1_closeout_final_recheck.py`
3. `orchestrator/full_target/test_between_cycles_host_duty.py`
4. `orchestrator/full_target/test_fixed_task_allowance_config.py`
5. `orchestrator/full_target/test_publish_source_steps.py`
6. `orchestrator/full_target/test_schema_drop_conflicts_and_system_tail.py`
7. `orchestrator/full_target/test_schema_drop_criterion_assessments.py`
8. `orchestrator/full_target/test_schema_drop_selection_fragments.py`
9. `orchestrator/gap_phase1/test_appworld_result_contract.py`
10. `orchestrator/p34/test_role_for_task.py`
11. `orchestrator/p35/test_provider_accounting_loop.py`

## 改后单独重跑（每个文件单独一次，`-q -p no:cacheprovider`）

| 文件 | 结果 |
|---|---|
| tests/agents/test_context_journal.py | 12 passed |
| tests/orchestrator/full_target/assurance_exec/test_batch1_closeout_final_recheck.py | 6 passed |
| tests/orchestrator/full_target/test_between_cycles_host_duty.py | 5 passed |
| tests/orchestrator/full_target/test_fixed_task_allowance_config.py | 2 passed |
| tests/orchestrator/full_target/test_publish_source_steps.py | 3 passed |
| tests/orchestrator/full_target/test_schema_drop_conflicts_and_system_tail.py | 2 passed |
| tests/orchestrator/full_target/test_schema_drop_criterion_assessments.py | 3 passed |
| tests/orchestrator/full_target/test_schema_drop_selection_fragments.py | 2 passed |
| tests/orchestrator/gap_phase1/test_appworld_result_contract.py | 3 passed |
| tests/orchestrator/p34/test_role_for_task.py | 2 passed |
| tests/orchestrator/p35/test_provider_accounting_loop.py | 9 passed |

只跑了这 11 个文件。没有跑整目录或全量。
