# Task 15 — S-6 / S-7 确定性自动化证据

> 日期：2026-07-25
> 工作目录：`F:\projects\deskpet`
> Python：`F:\projects\deskpet\backend\.venv\Scripts\python.exe`
> 结论：S-6、S-7 自动化门 PASS；真人矩阵仍未因此完成

## 1. 本轮补强

只修改测试与证据，没有修改生产实现，也没有启动 Tauri、backend、Vite 或 launcher。

- `test_growth_signals.py`
  - 敏感 payload（access token、private key、raw args）在构造阶段拒绝；
  - 拒绝后 `growth_events` 行数仍为 0，证明不是“报错但已落库”。
- `test_growth_reflector.py`
  - 单一隐式上下文被确定性改写为 `insufficient_independent_evidence`；
  - abstain 结果不携带 target、candidate mode、diff 或 evaluation plan；
  - 只有模型自我判断、evidence 为空时，candidate schema 直接 fail closed；
  - 自定义 user source 不能把 genesis 改名为 `builtin_override`。
- `test_candidate_builder.py`
  - Candidate seed 再次验证 custom user source 不能冒充 builtin source。

S-7 的既有真链同时覆盖：

- trusted explicit request 在 general Builder 前建立 proposal/reservation/build；
- candidate-only finalize 不调用 Manager；
- 未经 governed admission 的 Skill output 返回
  `governance_admission_required`，publish 次数为 0；
- durable precreate/ack/handoff/replay 不重复启动 child 或重建 candidate。

## 2. 精确 S-6 / S-7 门

命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/companion/test_growth_signals.py::test_signal_payload_rejects_credentials_and_unnecessary_raw_args `
  backend/tests/companion/test_growth_reflector.py::test_reflector_abstains_on_one_implicit_context `
  backend/tests/companion/test_growth_reflector.py::test_model_self_assessment_without_evidence_cannot_become_candidate `
  backend/tests/companion/test_growth_reflector.py::test_custom_genesis_cannot_masquerade_as_builtin_override `
  backend/tests/companion/test_candidate_builder.py::test_candidate_seed_rejects_custom_source_masquerading_as_builtin `
  backend/tests/companion/test_candidate_builder.py::test_admission_routes_skill_before_general_builder_and_is_replay_stable `
  backend/tests/companion/test_candidate_builder.py::test_real_store_admission_and_build_state_machine_are_durable `
  backend/tests/capabilities/test_builder_validation.py::test_general_builder_cannot_publish_governed_entries `
  backend/tests/capabilities/test_builder_validation.py::test_candidate_only_finalize_issues_receipt_without_manager -q
```

结果：

```text
...........                                                              [100%]
11 passed in 3.27s
```

第一次运行有 1 个测试失败，原因是新增测试错误地期待 `ValueError`，生产代码实际正确抛出
typed `CandidateBuildIdentityError(candidate_builtin_override_fence_invalid)`。测试改为锁定 typed
异常后，同一命令 11/11 通过；没有修改生产代码来迎合测试。

## 3. 相关文件回归

命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/companion/test_growth_signals.py `
  backend/tests/companion/test_growth_reflector.py `
  backend/tests/companion/test_candidate_builder.py `
  backend/tests/capabilities/test_builder_validation.py -q
```

结果：

```text
..........................................                               [100%]
42 passed in 8.18s
```

## 4. S-7 durable coordinator / composition 回归

命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/companion/test_candidate_build_coordinator.py `
  backend/tests/companion/test_candidate_composition.py `
  backend/tests/companion/test_store_transactions.py -q
```

结果：

```text
..........................                                               [100%]
26 passed in 2.26s
```

## 5. 断言摘要

### S-6

- exact hypothesis：`The user may prefer every answer to be more lively.`
- 空 evidence candidate：拒绝，reason=`candidate_proposal_requires_evidence`。
- 单一隐式 context：abstain，reason=`insufficient_independent_evidence`。
- abstain mutation fields：全部为空。
- access token/private key/raw args：入库前拒绝。
- 拒绝后 `growth_events=0`。
- Reflector 模块没有 Manager、Registry 或 CapabilityStore mutation handle，因此上述拒绝路径
  不可能创建 activation request 或 binding。

### S-7

- exact logical target：`daily-three` / `daily-three-pack`。
- explicit Skill request：固定走 `candidate_only`。
- custom user source + `builtin_override`：proposal 层和 candidate seed 层均 typed reject。
- general Builder 未 governed admission 的 Skill output：Manager calls=0。
- candidate-only finalize：Manager calls=0，host-issued receipt 存在且 hashes 全量校验。
- durable state machine：proposal、reservation、build、child、handoff 可重放，candidate identity
  稳定。

## 6. Store schema / activation fence

命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/companion/test_store_schema.py::test_candidate_mode_check_rejects_mixed_source_target_fences `
  backend/tests/companion/test_store_evaluation_activation.py::test_mutation_replay_drift_and_genesis_fence_are_rejected -q
```

结果：

```text
....                                                                     [100%]
4 passed in 1.38s
```

这组数据库/激活边界补充证明 genesis、update、builtin_override 的 source/target fence
组合不能交叉冒充；genesis 的 expected-absent fence 在 mutation 重放时仍会重验。

## 7. 清理

- 本轮只运行短生命周期 pytest，没有启动应用或端口 listener。
- 精确查询命令行含 `backend/tests/companion/test_` 的 Python 测试进程：
  `python_test_survivor_count=0`。
- pytest 临时目录由 fixture 生命周期关闭；未创建手工 Scenario、Launch manifest 或
  cleanup-result，符合 S-6/S-7 的 `LaunchId=N/A` 契约。
