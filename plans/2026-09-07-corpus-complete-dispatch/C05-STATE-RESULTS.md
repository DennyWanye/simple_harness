# C05 当前任务保持与完成任务只读

最后更新：2026-09-07。

主源码0f3b9243+801ce506，H0710/M619/S0313 installed，三个新控制分批通过。r1仅07首红1FAIL0.72s：原编译器把可信Host当前任务声明与USER句保留为unresolved，旧scalar入口直接拒绝，未启动main/模型。修复只接受精确原句，USER句真实enqueue，Host半句由原C实际setup来源、owned初始admission receipt和物理scope snapshot兑现，不把文本提升为SYSTEM。

r2原红及两未执行项 **3PASS47.18s**。07保留当前课程备课C，预览候选后下一Run按原f1确认切换；08已complete任务通过原公开search/resume只读，逐轮用原disclosure与setup终态/状态/字段核未改变。提前切换负控实际EXECUTION_FAILED/CorpusApprovalBlocked、仅1评分Run且events=[]，测试通过表示拒绝符合预期，不是任务成功，也不外推SDK自动拒切scope。

07正向2评分Run/4评分HTTP/9fixtureHTTP，08同2/4/9，07提前切换1/2/9；全部model_loaded=false/cleanup=[]。原04/09/14/20+empty未重复。正式C05当前覆盖6个场景与两个负控，不是完整20或自然语言搜索质量通过。

命令：当前target优先PYTHONPATH，经共享run_resource_bounded.py --rss-mib2048 --seconds180，pytest -q -x backend/tests/quality/test_corpus_c05_state_phase.py，r1/r2分别ignored basetemp。

r1 PG87968 exit1/1.561s/peak212448KiB/minDisk4197；r2 PG88533 exit0/47.933s/peak564528KiB/minDisk4063。均remaining=[]、stop=null、cleanup=null，三个child自然退出。不是资源准入阻塞。

| 本机证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c05-state/r1/command.log` | `203e47d498354ec0736ab7d184568937bef57f0c5027e52fca3fc4d7d3b1c236` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r1/resource.json` | `8856e9c690190aa19608f86d2d9fa63577cce2d878a593d37c5c21e0d7532c42` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/command.log` | `61cb7da02af41987c38a95cd72a3d4cf7d50c550b618de5ca1de68c02226956f` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/resource.json` | `036e741299421e639c8bc067005dc04451c8019dbb0bb66505d66dae435b7c7f` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/tmp/test_actual_main_active_previe0/.local-test-evidence/C05-07/execution.json` | `874d95843ee15707a3c9c9d88df4685f43e2ff387ee53104bdc206b0f6c288bc` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/tmp/test_actual_main_state_source_0/.local-test-evidence/C05-07/execution.json` | `768cb828e2af4d5bb29a2076b22c30ff116017b7e6c8848da987c8a6cbe625d7` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/tmp/test_actual_main_state_source_0/.local-test-evidence/C05-07/scoring-f1/execution.json` | `7d87b5fd1e0d0701ea2752aad1f662eaf1fb933bbf8be90c3b56774d7eabd35c` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/tmp/test_actual_main_state_source_0/.local-test-evidence/C05-07/scoring-initial/execution.json` | `79c6a132f4d4b05b9cbdaf784668b45ef2ef828e9fa65df0f33ff6128cf56a87` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/tmp/test_actual_main_state_source_1/.local-test-evidence/C05-08/execution.json` | `031d67fe92bf60f2728f9169080b7261908c726c0ca556874512697896e5ba6c` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/tmp/test_actual_main_state_source_1/.local-test-evidence/C05-08/scoring-f1/execution.json` | `d5b3f77e9135d5fccef832ca6dd22d0fe29d3489895d2d906d7331226dd59249` |
| `.local-test-evidence/2026-09-07/corpus-c05-state/r2/tmp/test_actual_main_state_source_1/.local-test-evidence/C05-08/scoring-initial/execution.json` | `b6ba65c7f970b6e789060a1bfdcd91842f94fbd7cd492d2eb879ba50c301e39f` |
