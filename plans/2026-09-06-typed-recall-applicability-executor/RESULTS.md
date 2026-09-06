# 原 Procedure applicability 三格结果

最后更新2026-09-06。执行源码ecaeb50fa10644543f478a0b3cf806424b3b033a，Dirac限定源码ACCEPT后测试，首次绿，无伪造red。

| 批次 | 结果 | PGID/退出 | 峰RSS KiB | 资源耗时 |
|---|---|---|---:|---:|
| tests-r1 | 1 passed in 0.68s | 42123/0 | 73552 | 0.883s |
| formal-r1 | 原三格3PASS/0FAIL/0BLOCKED，3OBSERVED | 42137/3 | 135440 | 0.901s |

正式Run `67db4f2a02d544db83a20da646f8e16e`：procedure-applicability-match/mismatch/absent三格PASS。
其余398未选，整体NOT_RUN/BLOCKED、exit3；不能把旧各批并成本次完整401。
唯一pytest方法包含原三格、三个错误current context、一错authority revision，均命中特定拒绝reason，不是7个pytest tests。
实际公开CREATE/snapshot/recall与exact重放；原app-v2/app-v3/null、ELIGIBLE/INELIGIBLE、APPLICABILITY_STALE/REQUIRED保留。
实际公开negative decision为recall_no_eligible_memory，实际candidate读取后判no_recall；未把seed拒绝判PASS。
这是synthetic环境声明的SDK合同，不称Host环境探测、工具实际成功、观察晋升或提醒。
32非法组合、两projection旧wire/hash及完整canary/scope义务保持未闭合；无SDK/Host生产修改。

## 命令与边界

复用installed H073/M0613解释器：
`/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/venv/bin/python`。
默认共享锁入口`/Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --rss-mib 2048 --seconds 180`。
pytest child：`<python> -m pytest testcase/human-memory-program/tests/test_typed_recall_applicability_executor.py -q -p no:cacheprovider`。
formal child为既有run_typed_recall_public_consumer.py，exact H073/M0613 source/wheel pins、原三个--cell，无--observe。
完整argv见command.json；两批直接取得锁。无新venv/安装/模型/build/native，旧绿未重跑。
原始证据约数MiB，留本树`.local-test-evidence/2026-09-06/applicability-executor/`，不提交raw。

| 相对路径 | SHA256 |
|---|---|
| tests-r1/command.json | b213d9b773723b611779d959998caa874c163027b10910eb068787da9ce56290 |
| tests-r1/source.txt | 4647618841fec53fe769721c4643eee0e43297bf50b71a6b89eeea5dbc33e83c |
| tests-r1/resource/command.log | 212504876166ec78d6893ba7cb632e14878599d119e4df02390a6a5464c97ea0 |
| tests-r1/resource/resource.json | ef6e1869efd95916d1d42093544854068d296a0024ddad6e42755e3109709f11 |
| formal-r1/command.json | e7cca6914a6d35ae6f019fdb3bfc346feb158de3fee76ecb2741c93f5bbd5224 |
| formal-r1/source.txt | 4647618841fec53fe769721c4643eee0e43297bf50b71a6b89eeea5dbc33e83c |
| formal-r1/resource/command.log | 79418e9af6ee8b49e5f111a11acad0d61bfe251f540993793b3ea7032a6ae5b0 |
| formal-r1/resource/resource.json | dea21ff0bd0bd13021a2aca8c0340750ac4f66519c025604e9ec11a193527c66 |
| formal-r1/public-data/bridge-summary.json | 86531705defb2b80c911aa79ea07069ca1bdaf119bc4ff7af175e47998352163 |

两resource stop_reason=null、remaining_group_members=[]、cleanup_error=null；ps无两PGID，自有测试进程已退出，槽释放。
合并时保留全部既有execution指纹；本片修改文件已在既有compiler/adapter/oracle inventory内。
