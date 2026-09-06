# S5c继任：实际Primary49至隔离50

最后更新2026-09-06。映射A11-schema50/v1；原A7/A8/A11功能判据不减。主9934d284合入为7934f719，披露48/入场拒绝49为实际增量，未发布S5c48后继为50。SQL为s5c/042_prospective_memory_actions_v50.sql，旧48文档及验收保留历史。

默认初始化仍49；S5c50只由显式入口调用，尚未main/startup启用。先核完整Primary49链，四张表、回签、recovery guard同事务追加，失败回滚、重开核DDL/chain。遇实际旧S5c表且版本非50，在普通repair/迁移之前只读拒绝s5c_unpublished_schema_incompatible，不能把实验47/48当正式Primary版本。

必要43项通过13.68秒，使用现有H073/M0614/S0313安装组合。首次改名脚本留了040_v50引用而真实文件042_v50，41FAIL/2PASS；修正路径后43PASS，原失败保留。另从历史c8395db1完整Host源码生成实际S5c48数据库（加载Host来源均为旧snapshot），现存历史S5c47取副本；新入口均拒绝，整个DB前后SHA256相同。未改原库，不是只改pragma的假旧库。

共享资源入口2GiB/180秒：r1 PG42954/exit1峰175744KiB/9.871秒；r2 PG43031/exit0峰199856KiB/14.175秒；r3-old PG43110/exit0峰165200KiB/.878秒，均remaining空、cleanup_error空。磁盘采样最低3694/3584/3540MiB。

命令：PYTHONPATH=backend/禁插件autoload，主primary-m0614/venv/bin/python -B经共享runner运行pytest -p pytest_asyncio.plugin -p no:cacheprovider -q，选择test_s5c_store.py、test_s5c_consumer.py、test_s5c_consumer_sdk.py，basetemp各ignored rN-db。旧库脚本使用-I -B verify_old.py，移除PYTHONPATH。未启模型/native。

下一步：公开SDK精确outbox来源、Host持久registration authority resolver、唯一scheduler/due/occurrence、snapshot呈现/ack/终态。当前公开outbox有trigger但无原scope/run/operation；不能虚构来源。过期未消费grant恢复仍需后继协议。独立审查待续，未合主组合，不称提醒功能完成。

| 文件或本机ignored证据 | SHA256 |
|---|---|
| backend/deskpet/memory/s5c_schema.py | b75850f758ead71fd1cf859a9462ad7dccca22d0477635116c81285e05e8e328 |
| backend/deskpet/memory/migrations/s5c/042_prospective_memory_actions_v50.sql | f0dde4b9a99361c30689e4f21f675087dbba9877e1de8747451fda4d2d6ba0ac |
| backend/tests/memory/test_s5c_store.py | 3a201013403f89656e7c6aa94436a136f09e8c93cb088093619e33f2d686605a |
| .local-test-evidence/2026-09-06/s5c50/r1/command.log | 72b3794a0448fdc572f9dd683fac016b345da8de2106174f27acf6211489b03d |
| .local-test-evidence/2026-09-06/s5c50/r1/resource.json | ace39dc09b8d63b29cffa9ed63ae519efab3af53eb5524a07a0582c25c01f668 |
| .local-test-evidence/2026-09-06/s5c50/r2/command.log | 0fb46bd65db7d1e1d8a3c72d8dfd86252d1ca828af6a1f28db2a818f8b168986 |
| .local-test-evidence/2026-09-06/s5c50/r2/resource.json | a7ada66984d5b4de48c255359e0c95f6f44f0a799ffdb774a0396266354dbaba |
| .local-test-evidence/2026-09-06/s5c50/r3-old/command.log | bc19c35e3275287b9a54dc53cc0d309f077928b954f544231baaa27485919069 |
| .local-test-evidence/2026-09-06/s5c50/r3-old/resource.json | 8160c77ed516c05372d4ad251460005e74bd39fef9ef1b731ec7bd039caffd84 |
| .local-test-evidence/2026-09-06/s5c50/verify_old.py | 5819d3fc2e9539301d54abf9f95f7aeca80377059a2864141565c14109c0252d |
| .local-test-evidence/2026-09-06/s5c50/old-rejections.json | d5e1b70097482cdb75573770d443e20bbaca5e00ba87c230d635f8f21c72b66a |
| .local-test-evidence/2026-09-06/s5c50/old48-origins.json | e468973c42f3c9fea9af6556eaa3093293647f63f364c73c9c3ad82d89afeb9f |
