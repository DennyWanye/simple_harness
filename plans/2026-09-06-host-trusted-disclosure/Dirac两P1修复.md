# Dirac 两处 P1：入场拒绝与出站代次复核

最后更新：2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，`feat/host-trusted-disclosure`，基线 `955a19cd5b1ac566445e8bd6f83da2b1e7c1eff1`。C12调查暂停；本叶未合主，待主/Dirac复核。没有新树、环境或SDK修改，没有模型、真实网络Provider/native调用。

## 原红与实际修复

1. G1下真实enqueue A → 第二个签名控制连接真实configure G2 → enqueue B。原955实际driver在A的 `draft_lineage` 抛 `host_disclosure_binding_stale`，A/B均永久QUEUED，send0。修复仅捕获binding_stale/legacy_policy_changed两个确定性拒绝；在BEGIN IMMEDIATE及现有writer/recovery fence内重新回读最老候选、原turn hash、精确持久binding及当前head，证明拒绝后原子追加Host入场拒绝记录。异常文本本身不能形成事实。A没有Host Run、SDK Run、SDK终态或Memory receipt；B由实际foreground完成，重开后A/B均不重跑。
2. 出站G1 resolve后，真实history policy已给allowed=True但受事件屏障阻塞返回；另一签名控制连接提交G2，释放checker。原955进入一次HTTP MockTransport。修复在checker及其数据库清理结束后用独立新连接/新snapshot再次resolve同一SDK run/request，且完整DisclosureContext必须等于首次结果。陈旧token拒绝被原有typed preflight投影为明确FAILED，transport0；未改配置的邻居仍进入transport1（刻意transport异常保持原unknown语义）。

`bound_record_tx` / `HostHistorySourceAuthority`不变：历史来源按绑定时配置验证；当前执行另判head，禁止用最新head重写原来源。原13来源用例在本轮重跑，含真实11组foreground/outbox/非空short/history及换head后来源receipt一致。

## 最小持久契约与schema

新增 `041_foreground_admission_rejections_v49.sql`，目标48→49；旧040及更早SQL不改。`foreground_admission_rejections`为A类append-only恢复表，字段turn_id（主键/FK）、subject、rejection_hash、rejection_json；自动注册已有recovery fence。新记录明确schema、原turn/candidate hash、bound_token、observed_current_token、固定reason、recorded_at，不复制用户正文、不签发权限。

旧turn head/transition约束要求SETTLED必须有Run，因此保持旧QUEUED入队事实，用独立拒绝记录表示实际终止。FIFO候选读取逐个验证该记录、主体/完整hash/不可变两代配置/无Run冲突后跳过；prepare/claim共用该候选读取。重复收尾回读原记录；G3不能改写G2拒绝观察。提交前失败回滚、提交后失败重试不重复。

公开 `queue_snapshot` 对此类turn投影 `state=REJECTED`，附 `rejection_reason`，不宣称SDK FAILED；其余state不变。primary pending计数排除已拒绝turn，read revision包含新表尾，消息读取保留真实用户输入并校验拒绝记录，不制造助手回答。schema启动校验实际表、append-only触发器与恢复分类；v48→49迁移及缺触发器拒绝已测。

改动接口：`reject_stale_candidate(store,candidate)`为Host内部事务入口；`read_admission_rejection_tx(db,turn_id,subject)`回读历史拒绝事实。未新增model schema字段或模型自授权限入口；不改context_route/context_authority、Memory SDK、冻结r4/旧语料/阈值。

## 验收与限制

新增6项：实际A/B推进重开；慢checker换代send0/不换代send1；commit前/后故障与幂等回读、foreign-subject/篡改拒绝；v48升级及缺trigger拒绝。必要邻居为原8绑定、13来源、12出站、队列与primary read API；最终共94项通过。

本轮不是240质量PASS、非SELF可执行或完整外发授权完成。最后复核覆盖“checker等待期间已提交G2”的反例；没有持有跨物理发送全过程的配置锁，不把最后读取之后发生的撤权竞态称为已原子封闭。当前task scope切换/claim之后其他准备异常的通用收尾不在本次新增机制范围内。真实HTTP网络与native未测。

所有测试复用主M614环境及145baed3资源入口，默认共享OS锁，2048MiB/180秒；无并行build。r1/r2/r3均无remaining_group_members或cleanup_error，另用ps核PGID均无行，槽已释放。原始证据留本树ignored目录，不提交。

| 批次 | 结果 | PGID / elapsed / 峰KiB |
|---|---|---|
| disclosure-p1-r1-red | 2失败；955两P1原红 | 39817 / 2.178s / 181200 |
| disclosure-p1-r2-fix | 2通过；首次定向修复 | 40182 / 2.375s / 179888 |
| disclosure-p1-r3-neighbors | 94通过；最终41.51秒 | 40284 / 42.212s / 222944 |

## 命令

工作目录为自有树。后续重跑换新ignored批次目录，BUSY不换锁。r1/r2仅选test_trusted_disclosure_races.py，r3为下列最终组合；r1红例保留在原log，其测试文件随后加入正控和收尾验证，不能用最终文件hash冒称原红文件hash。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=/Users/denny/projects/simple_harness-corpus-clock/backend \
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0614/venv/bin/python -B \
/Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py \
--evidence-dir /Users/denny/projects/simple_harness-corpus-clock/.local-test-evidence/2026-09-06/disclosure-p1-r3-neighbors/resource \
--rss-mib 2048 --seconds 180 -- \
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0614/venv/bin/python -B -m pytest -p pytest_asyncio.plugin -q \
backend/tests/memory/test_trusted_disclosure_races.py \
backend/tests/memory/test_trusted_disclosure.py \
backend/tests/memory/test_trusted_disclosure_sources.py \
backend/tests/execution/test_primary_history_outbound.py \
backend/tests/execution/test_foreground_queue.py \
backend/tests/memory/test_primary_read_api.py \
--basetemp=/Users/denny/projects/simple_harness-corpus-clock/.local-test-evidence/2026-09-06/disclosure-p1-r3-neighbors/stores
```

## 最终源码与原始证据指纹

| 路径（本树相对） | SHA-256 |
|---|---|
| backend/deskpet/execution/admission_rejection.py | 72f18484da5a175bcc43d1bfb6451b0b4799fa13c65e291a8b7093f136cfa3c8 |
| backend/deskpet/execution/foreground_queue.py | 3255548e5951fb0fb3021a4e2baa4212f03956adf3f713023e781a022d172d2e |
| backend/deskpet/execution/foreground_runtime.py | 4d774fbf74bbf84b801aa9a9bf8b459a7f880e2becafe62809dcfad74ca96692 |
| backend/deskpet/execution/primary_dependencies.py | 81fc9d3e3364c3589a0d3475d27256d0908fe1d0946f37a7e0b49fc601ec117d |
| backend/deskpet/memory/human_memory_service.py | 93687ed45d9255c6eb9de614e7d2cc6b9a277fb61dcdd399170a7f639ba31cb7 |
| backend/deskpet/memory/migrator.py | 66103e4e5b2e563c27806b41942f06e16fbdc0834e39ffcf687b5b05e34b65a4 |
| backend/deskpet/memory/primary_read_model.py | d7e8f9c2296f827aa598aa89e8835ac668fdac81d200000b960006bda4a5b2c3 |
| backend/deskpet/memory/schema.py | c13a1ca1d42236ab40bb2452689180e543c3a1e85000bed3529d13bd30faa280 |
| backend/deskpet/memory/migrations/041_foreground_admission_rejections_v49.sql | a3ef2069c57c463f0898835709a7c7d2aa1e73e443ad13e913de500d150d9a54 |
| backend/tests/memory/test_trusted_disclosure.py | 459355ee9aebd7b0ab508c0b60fc9624cca0b51d0dc9391426ac06fc9b7f4294 |
| backend/tests/memory/test_trusted_disclosure_races.py | c495326fc5704492ae44a5d20651a3a35e3ff7ce1401aa45ebeecaf7a8f932cd |
| .local-test-evidence/2026-09-06/disclosure-p1-r1-red/resource/command.log | 62aeeb7155f06fd0dc1c1e8d859454cf623303c7b814f368d612e6cf835eb8f0 |
| .local-test-evidence/2026-09-06/disclosure-p1-r1-red/resource/resource.json | 98107c99617bf23b9f1aaa3acef9316d6d8a656771016847a488c48224e40d11 |
| .local-test-evidence/2026-09-06/disclosure-p1-r2-fix/resource/command.log | 98481a7b87c7df6f2bca031757a6de8d8fbf6d521426106464ef1003ab66309e |
| .local-test-evidence/2026-09-06/disclosure-p1-r2-fix/resource/resource.json | 07581c68056482ba42d8c2fd1cb875ec0ae45149f23ee5f77a60af6b169a106a |
| .local-test-evidence/2026-09-06/disclosure-p1-r3-neighbors/resource/command.log | f71df6dff501f4b0933eba67ba14d1e128ea0edfde826f387edc620ee05fda1c |
| .local-test-evidence/2026-09-06/disclosure-p1-r3-neighbors/resource/resource.json | 982875372a0ccc635eb39a4970cd7d322a92ec28897002e653ab95fb7ffa99d7 |
