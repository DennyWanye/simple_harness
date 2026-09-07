# Host52 / H076 / M617 consumer 新控制

2026-09-06。base `5e0b1d47`；独立分支 `feat/prospective-m617-integration`。仅修 S5cStore：public dependency 嵌套 immutable JSON 显式转容器后仍完整 canonical 字节/hash比较；accepted registration 扫描使用经构造验证的51/52 cursor table。无新DDL、无SDK变更、无grant放宽。

6个新增用例分批通过，未跑旧time2+3/schema5/大集合。不是同一6项全跑结果，不拼历史全量；测试完成后仅文档/提交，无新增测试进程。

- 实际Manager创建8条同时间公开registration，再真实REVISE；读取实际ID确认 invalidation < 中间合法entry < 原registration，不推导SDK ID、不写SDK SQL。依赖先prepare-only/SDK ACK，Host ACK后或Memory apply后响应丢失，过期重开同ref恢复；中间entry和原cursor继续、下一轮零重发。
- 旧catalog7.2按SDK已用initializer技巧，仅替换冻结catalog/初始化探针；CREATE→生产Host注册→真实timer TIME_DUE→REVISE全走public Manager。它不是installed616运行。公开migrate7.2→7.3后，真实r2 not_required在Host TX before/after、SDK commit未Hostcommit三窗口恢复；r2无Hostgrant/无apply，真实r1 invalidation及后续registration继续。未私读写Memory SQL。
- 默认ProspectiveSourceJournal捕获Manager settle观察：captured_bound、原DTO JSON、observation_hash、receipt_hash精确绑定；SDK ACK丢失窗口保留首次观察缺失，恢复调用捕获，未伪补首调用。
- 来源错误owner/payload、dict冒typed V2、V2错subject均拒绝且零prepare/cursor/apply。终局真实receipt的owner/source/payload/signal-result错绑、dict冒typed receipt在重放时拒绝，原receipt/cursor不变；不声称Host能独立认证任意调用方伪造的新SDK回执。

## 原失败与定向复验

| batch | 结果 | 事实 |
|---|---|---|
| m617-r1 | 5FAIL | dependency嵌套mappingproxy被canonical拒绝；52 timer误扫旧cursor，tick0 |
| m617-r2 | 1PASS4FAIL | 两生产窄修后，host_ack绿；其余为oracle：pending满页需下一tick、真实r1额外ACK |
| m617-r3 | 1PASS3FAIL1deselected | sdk_ack绿；终局oracle错误把非prospective evidence outbox也当signal |
| m617-r4 | 3PASS2deselected | 限定原prospective topic，三终局控及receipt负例通过 |
| m617-r5 | 1PASS5deselected | 新source typed/owner/payload控制通过 |

不以早期失败算业务拒绝通过。保留所有raw；后续只重试真实失败或新增控制。各批parent exit1/1/1/0/0，remaining_group_members全[]、cleanup_error全null，最高194656KiB；无模型/native/build/install。

## 命令与身份

复用 primary-m0615 Python依赖环境，以`-I -B`插入两个固定installed target：
H076 `simple-harness-sdk-short-context-revision/.local-test-evidence/2026-09-06/prospective-rescheduled/artifact-076/installed-target`；
M617 `simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/prospective-settlement/artifact-617/installed`。
launcher断言metadata 0.7.6/0.6.17及所有已加载SDK模块位于相应installed，无src overlay；未重复制品成员扫。

资源入口：`/Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --evidence-dir <batch> --rss-mib 2048 --seconds 180 -- <python> -I -B <launcher> <batch> <selection>`，默认共享OS锁与磁盘保护。
launcher：`.local-test-evidence/2026-09-06/prospective-timer/run_m617.py`；pytest仅`backend/tests/memory/test_prospective_consumer_m617.py`、no cache、单进程asyncio插件。
r1/r2无selection；r3 deselect host_ack；r4 `-k legacy_terminal`；r5 `-k source_typed_union`。

## 证据索引

所有路径相对本树；原raw不入Git。独审待Dirac固定源反馈。H077组合/native/event/recurrence/presentation均不在本叶验证范围。

- `.local-test-evidence/2026-09-06/prospective-timer/m617-r1/command.log` SHA256 `432a64f9eba87a4120c04318b7dc9e2ff6948ffc70e58a6b316b896f2405d1b7`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r1/resource.json` SHA256 `d10af7e9d84d39db12a19d4bebeacd394ba56fb7aa665da29eafc9231fb59986`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r1/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r2/command.log` SHA256 `6d51ae447b69044d9b713375bdbcdb6e74b321b312a43e6636e0a49519e5de5b`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r2/resource.json` SHA256 `2fb5918b07de79cb27cd26663d010563079998f0830cb686f64af2198fc05bc7`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r2/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r3/command.log` SHA256 `96b9aaeeab5dc43b97e36ba4cd48d913eacd62a20416f3d40af6be0d20e711f9`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r3/resource.json` SHA256 `1979cd6e8bf5900f6aa5df14a056af82b4aea1daec73266eceb9e72369ddded1`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r3/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r4/command.log` SHA256 `dc8f5d15deca346172f5daf5d3400577155292db2b1dc33b4167899711a7b146`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r4/resource.json` SHA256 `fe59057abf709993fb34a963882d7613da85c188e7a9cfcf31e4715fe6d6c098`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r4/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r5/command.log` SHA256 `f1f22ef45c1b70de0e023f4ffeb8972ed79baca5fa09c008ff043aa87e880398`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r5/resource.json` SHA256 `f8dc3e2f77bf83b862ab855c7be2e03199c32dffe592c4c6c8adcd0027e41bc0`
- `.local-test-evidence/2026-09-06/prospective-timer/m617-r5/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
