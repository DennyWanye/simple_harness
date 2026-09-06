# SDK 诊断异步快照导出

2026-09-07，Host source d60a94f4，当前installed H0710/M619/S0313。修复已观测MemoryManager.diagnostics_snapshot被同步dict(source())消费、产生未等待coroutine和snapshot_unavailable的问题。

新增export_async在原事件循环等待SDK公开aggregate snapshot；main内存初始化、SDK stack.start之后及旧turn清理完成之后显式await，同步runtime factory只注册来源。同步export兼容保留：异步结果不假装可读，关闭本次原生coroutine并明确snapshot_requires_async_export。没有后台常驻collector或跨loop读DB。

每awaitable使用0.5s取消deadline，不是同步source调用及取消清理的硬wallcap；wait_for可有受拥有任务，取消时传播CancelledError。普通异常仅记录稳定错误码、不导出异常正文。原有本地文件大小、权限、原子写入和ring限额复用。

首批 **5 PASS / 2 deselected，1.04s**：两个新增控制核真实已安装Memory SQLite开发实例的异步诊断准确写出（hash embedder，无真实模型）、timeout/cancel后collector结束；另三个受修改影响的同步导出/故障/缺SDK降级控制通过。不是整套测试或原生验收；main新增接线仍等下一新组合实际初始化观测，不重跑旧绿。Dirac限定源码审查无P0/P1。

原Memory SDK诊断sdk_version字段仍硬编码0.6.0；本次保持原SDK返回值，不能用它代替已安装distribution=0.6.19的身份核验，修正该SDK字段仍待办。本次没有改SDK版本/制品，也不宣称全操作审计完整。

共享资源PG83640，exit0/2.013s/peak136768KiB/minDisk4537MiB/remaining=[]/cleanup=null/stop=null。测试已结束，无资源拦截；防熄屏覆盖后续测试。

命令：当前installed target优先PYTHONPATH，经run_resource_bounded.py --rss-mib 2048 --seconds 180；pytest -q backend/tests/test_sdk_observability_host.py -k 'exports_are_safe or snapshot_fault or missing_sdk or async_snapshot'，basetemp位于下列ignored证据目录。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/sdk-async-snapshot/r1/command.log` | `8d26c0477bed61b3952fd9db2817ba1881f6c795ee48b29c691e6d470626beae` |
| `.local-test-evidence/2026-09-07/sdk-async-snapshot/r1/resource.json` | `7dc9535a13aead02cae1a05bcbcc5411a0b05719539beb24aa44e03d95d121d1` |
