# Prospective公开信号主复核与组合

最后更新：2026-09-06。原固定adapter/oracle4aee0cdb、测试收紧dd988b19及结果ea57e720已获Dirac限定ACCEPT；主复核公开注册/原trigger/来源/时间/实际revision与回放oracle、共同CaseManager变化，并核13个raw hash一致。以29479573合到runner，仅bridge指纹清单冲突，保留source/context_use/procedure/prospective四个oracle；原输入/pin/阈值不变。

固定29479573必要组合18 tests PASS / 6.31秒，覆盖Prospective3、Procedure3、Context与source两个实际集成及10个严格分派控制。正式13格按Run ae075cb1eaed43a3b8f8221160d2c874独立保留，不和其他批次拼成一次401。注册/事件输入明确synthetic SDK合同范围，非Host提醒或实际外部事件；原剩余6个lifecycle格继续实现。

145默认锁、2GiB/180秒，PGID37012峰114656KiB，elapsed6.675秒，exit0且无剩余进程/清理异常。借用既有exact H073/M0613 interpreter，移除PYTHONPATH，-B且关闭插件autoload；所有DB/log在ignored目录，不重复无变更绿色基线。

实际命令体：`<M0613 artifact python> -B -m pytest testcase/human-memory-program/tests/test_typed_recall_prospective_public.py testcase/human-memory-program/tests/test_typed_recall_procedure_public.py testcase/human-memory-program/tests/test_typed_recall_context_use_bridge.py testcase/human-memory-program/tests/test_typed_recall_source_oracle.py testcase/human-memory-program/tests/test_typed_recall_bridge.py::test_two_layer_dispatch_retains_exact_inventory_and_failures -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp .local-test-evidence/2026-09-06/prospective-combined/r1-db`。

| 本机ignored文件 | SHA256 |
|---|---|
| .local-test-evidence/2026-09-06/prospective-combined/r1/command.log | 87e667a36d2c772fe3bbed591e25dd2813d6af3e8e3a07f62e501676ec4a2c37 |
| .local-test-evidence/2026-09-06/prospective-combined/r1/resource.json | 39fb578cc3d712b0f0b72cb3d71b45220ca570eb08153c64816db41ce3d2d8af |
