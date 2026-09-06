# 正常后台 worker 接通公共 generation

2026-09-06 补充冷加载恢复控：在固定生产源码161702be上只追加一个测试，未改默认 operation_timeout=5s 或 WeMM。公共小 embedder 的共享 shield 加载任务由事件延后完成，以0.5s缩放 deadline 跨两次超时；同时发出的两次 worker.step 被现有锁串行执行（peak=1），load task 只创建一次，两次超时后确认 cache 为空、_last_projection=None、generation_pending=True。放行加载后，不推进模拟维护时钟即可恢复 generation，实际 embed batch 成功一次。此为 worker 恢复控，不是实际 WeMM 8秒加载/线程清理验收。

r3 仅 `-k cold_shared`：1 PASS/4 deselected，5.60s；PG83348/173920KiB/6.232s/remaining=[]，共享锁释放。原四绿和 WeMM suite 未重跑。当前测试文件 SHA327f1c7269102f59c5aa7569c79776713db8c35bcdf84bbcd0e7f43ea3042108；r3/command.log SHA7b7b5d3df071169ae053b4fb828b102a0339c68afc908933623a8d719d29e062；r3/resource.json SHA0451d62a1a26b5bfe8c78de43bcbee17b0f2dd95738fc50670191605aba44eb4。下文四项结果和测试文件hash保留161702be时的历史版本。

最后更新：2026-09-06。后继基线 `02bf7dcc`；自有 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/short-index-generation`。02bf 来源叶由主转 Dirac ACCEPT，本叶单独待审。未改 Memory SDK、制品、原库或 WeMM 实现。

## 真实配置与最小生产变化

基线 `main.py:3352` 将实际 WeMMEmbedder 登记到 service context，`:8358` runtime getter 读取同一对象；`human_memory_v7.py:167` build_kwargs 仅过滤 hash/mock，将 WeMM 传入公共 builder 的 short_horizon_embedder。不是缺模型下载；本轮未加载模型。主已报告 r8 实际 WeMM load/embedding catchup，本文不将该观察替代修复后的 native 验收。

生产源码只改 `short_index_worker.py`：现有 MemoryAnalysisLane.tick → worker.step 正常通路中，projection 之后调用 `manager.rebuild_short_horizon_generation()`，共享原 worker 串行锁；projection+generation 共用既有 operation_timeout 的一个 asyncio.timeout。不是测试脚本手工替后台建索引。

- 新 ShortIndexStep.generation 返回 SDK 实际 build result；有 chunks 却未 activated 时明确失败。零 chunks 的 SDK empty 结果仍正常返回，不声称存在可查询向量。
- `_generation_pending` 在尝试前置位。generation 完成及故障点均成功返回后，才清 pending、更新 `_last_projection` 和确认新 registration cache。
- 失败、超时、取消或 generation 已提交但 Host 丢 ACK，均保留重试条件；即使所有 group 已在确认 cache，下一轮也不能跳过未完成 generation。重开 worker 重置内存游标/缓存后继续同公共通路。
- SDK `sqlite_v5.py:2323` 本来就按 active lineage+chunk manifest 复用 generation。Host 不造 generation ID、不私读 SQL 决定 embedding、不手动优化掉 SDK 调用。
- 未配置 embedder 的 runtime 仍由 SDK 明确报 short_horizon_embedder_required，不伪造成功。旧只提供 None 的 projection-only worker 夹具不能直接代表新完整通路；新控显式配置小 embedder。单独 PrimaryShortIndexingService.reconcile 仍只负责来源/projection，正常后台 worker 负责 generation。

本轮没有改变 WeMM 的物理加载/encode 线程取消契约；worker 的 asyncio timeout 不是“底层线程已经退出”的证据。实际 WeMM/native 由主继续验证。

## 必要新控与结果

`backend/tests/memory/test_short_index_generation.py` 使用真实 Host turn/outbox、runtime getter、Memory 公共 builder/rebuild/query 和二维测试专用 Embedder。测试 kind 显式为 test-short-worker-public，不冒充 WeMM、不运行真实 Provider 或下载/加载模型。

| 控制 | 实际结论 |
|---|---|
| 后台 worker 自动 generation → 公共 query → maintenance → manager reopen | 11 组形成 1 vector，公共查询实际 used_generation_id 命中；维护/重开返回同 generation，embedding batch 仅 1 次 |
| generation 异常 → 成功但 Host 丢 ACK → retry | 异常/丢 ACK 不确认新 cache；已提交 generation 重放复用，两个实际 batch（失败一次、成功一次），不第三次 embedding |
| 已确认全部 cache 后 maintenance generation 超时 | 不吞异常，pending 保留；下轮正常恢复 activated |
| 同上取消 | CancelledError 传播，pending 保留；下轮正常恢复 activated |

r1：2 PASS/2 FAIL，7.00s。两失败是测试改变 revision 却沿用旧 fingerprint，撞 SDK embedding_lineages.fingerprint 唯一约束；实际 WeMM fingerprint 包含 revision。只修该夹具第二版本 fingerprint，第一版本取值及已绿两项不变。r2 定向 interruption：2 PASS/2 deselected，5.05s；四项新控分批闭合，未重跑 SDK/Host 旧绿。生产源码在两批间未改。

r1 PG82848/215408KiB/7.668s，r2 PG82943/176784KiB/5.793s，均 remaining=[]；默认共享锁已释放。未验证真实 WeMM/native，也未宣称原 program 完成。

## 证据索引

raw 根 `/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/short-generation/`，只有 ignored raw；`run_controls.py` 使用既有 actual installed H077/M617 target。r1 原失败测试保留 `r1/test_source.py`。

命令为既有 primary-m0614/venv/bin/python 调共享 `scripts/run_resource_bounded.py --evidence-dir <rN> --rss-mib 512 --seconds 90 -- <同Python> -I -B <raw根>/run_controls.py <rN>`；r2 末尾加 `-k interruption`。无新环境、无 lock-file 覆盖。

| 成员/证据 | SHA-256 |
|---|---|
| backend/deskpet/memory/short_index_worker.py | dcf0e911b66d112e1fee99a0ba824381e173fd32e275808511e6d126787ba65f |
| backend/tests/memory/test_short_index_generation.py | 6bc783d9d7de6d5b49065e06eae0b48121eb0784e17e8261127937608e2dfae3 |
| r1/command.log | 8ec7ff582424ae525c8329c5fbd677e0b364e26fa8c61bfe6d95501e9e219839 |
| r2/command.log | 8510423e960b3890653301c3fbaf3b33f5904460ddecb3cb02a2a8a3845a8718 |
| r2/resource.json | 646b050295732817c9c6c24d7e359ec96036ce750a21936713985cfa858e4f39 |
