# Procedure公开适用性主复核与组合

最后更新：2026-09-06。Dirac已限定ACCEPT的ad189f52源码/3de9879f结果由主复核完整adapter、独立oracle、结果说明，并核9份原始证据hash一致；以1c690bdb合到独立runner。唯一bridge指纹清单冲突明确保留source、context-use、procedure三个oracle；原fixture/pin与四格预期不变。

组合固定1c690bdb的15项必要检查通过（5.14s），包括Procedure公开3项与受共同CaseManager/bridge改动影响的Context、source集成及10项严格分派检查。原Procedure正式四格PASS按Run3913be071c484d069b48082fc5cec12a独立保留，不拼旧182或其他分支结果作为新401全量。状态eligible的显式enum/授权REVISE映射不是自动观察晋升；Prospective、非法构造组合、完整projection及真实Host任务执行未由本片完成。

145默认共享锁，2GiB/180秒；PGID34266峰114096KiB，耗时5.383秒，exit0/无残留或cleanup错误。借用固定H073/M0613 artifact解释器，移除PYTHONPATH、-B、pytest自动插件禁用；测试DB和日志均在本树ignored目录。

实际子命令：`<M0613 artifact venv>/bin/python -B -m pytest testcase/human-memory-program/tests/test_typed_recall_procedure_public.py testcase/human-memory-program/tests/test_typed_recall_context_use_bridge.py testcase/human-memory-program/tests/test_typed_recall_source_oracle.py testcase/human-memory-program/tests/test_typed_recall_bridge.py::test_two_layer_dispatch_retains_exact_inventory_and_failures -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp .local-test-evidence/2026-09-06/procedure-combined/r1-db`。

| ignored相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-06/procedure-combined/r1/command.log` | `78d862d0fa4bbf816d053f1d8f9add362e69e5c152ae0ccfeb9e78e38455cf4d` |
| `.local-test-evidence/2026-09-06/procedure-combined/r1/resource.json` | `070ecf2831c3a580533fbdaab3090531e705af683fd3c8bda5d25361800a6e04` |
