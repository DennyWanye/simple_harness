# C05 actual main 多轮准备与评分接线

2026-09-07，主H0710/M619/S0313；dispatcher e442993b、来源修复a4117ef6（d532c7d6）、fixture前置修复08ed1e72（cafe6637）。r3 **6 PASS / 74.03s**：1新control/physical来源校验及5实际main场景（四正向、一空候选负向）。

每例独立新进程。两真实TaskScope准备均走loopback HTTP→原SDK工具/公开审批→必要的真实marker写入/closure/update→原terminal与公开来源档案；同一ignored workspace root传两factory，冻结已核setup history prefix后关闭loopback，再准入独立评分Provider。原current与固定followup分轮发送，只按实际候选披露、原终态和未获得正式Scope授权的事实触发；C05-20首followup不切换，仅第二条开放精确resume。批准最终选择前重核此前候选真实source三元组和当前可见性，不从fixture label/gold选目标。

| case / 模式 | 实际结果 | scoring Runs | setup / scoring HTTP次数 | 已执行followup |
|---|---|---|---|---|
| C05-20 / empty | FOLLOWUP_UNMET | 1 | 4 / 2 | 0 |
| C05-04 / visible | COMPLETED | 2 | 9 / 4 | 1 |
| C05-09 / visible | COMPLETED | 2 | 14 / 4 | 1 |
| C05-14 / visible | COMPLETED | 2 | 9 / 4 | 1 |
| C05-20 / visible | COMPLETED | 3 | 4 / 5 | 2 |

评分HTTP返回固定控制响应，最终选择是实际披露的第一候选，可能与gold语义目标不同；该批验证的是执行/授权/阶段隔离，不是正确选任务的模型质量。统计只聚合scoring Runs并保留各自trace hash，绝不把setup和多Run拼成虚构单Run证据。空候选控是真成功search返回[]，准确FOLLOWUP_UNMET，无救场消息、无最终选择。非空但不可核验的policy False已由独立authority控制验证为OBSERVATION_FAILED，不混真零。

r1实际C05-04首setup已完成7次HTTP/6工具（含marker/closure），但历史提交把context_route控制ledger当物理ToolInvocationFact，primary_message_scope_source_mismatch。修复保留共同receipt/reservation/event/owner/hash核，只有完整原route ledger、raw调用及六字段payload匹配才能判定控制来源无physical Scope proof；物理工具原internal call/state校验不变。r2新增parser反例在业务前因测试虚构decision_id触FK失败，未运行后续5例；cafe仅建立真实Host record_route_decision前置，不关FK、不写SDK私库。r3才完整6绿，原两个FAIL批次均保留。无旧绿重复。

r3新parser还核原physical tool仍有原Scope proof、错raw/Run/tool及受损ledger读取均拒绝。原Scope分类错误不是USER Scope错绑；不修改旧v2编码、SDK制品或历史数据库。

资源：r1 PG85016 exit1/11.462s/peak558720KiB/minDisk4434MiB；r2 PG85509 exit1/1.081s/peak145744KiB/minDisk4429MiB；r3 PG85863 exit0/74.983s/peak593840KiB/minDisk4310MiB。均remaining=[]、cleanup=null、stop=null，无RSS/磁盘/时间拦截；五main child自然退出。原生与真实模型服务仍未验，caffeinate持续到全部测试结束。

命令：三批同current target优先PYTHONPATH和共享run_resource_bounded.py 2GiB/180s。r1 pytest -q -x backend/tests/quality/test_corpus_c05_phase.py；r2/r3前置新selector backend/tests/memory/test_procedure_scope_sources.py::test_real_route_control_ledger_has_no_physical_scope_and_mismatch_rejects，随后同5phase，-x首红停止，basetemp在各自ignored目录。

边界：只四C05的正式多轮来源准备及本次受控评分链，其余16完整准备/真实模型质量/原生/全操作审计仍未完成。原生Provider model_not_found不因这些控制变成通过。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r1/command.log` | `2fd8402aa12fb73c88146aa6221c8efbe51f7ffa2ac40476edd8541b9c1a4b37` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r1/resource.json` | `cd172132db42cb2e316f694acf4026db2b21c4cb55871e23316786c32e6ff092` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r2/command.log` | `3cf91d6508546a6bdab72121f91c24c04af6cf3f5277c571164027012d683b3d` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r2/resource.json` | `036ada085aabe5fb0835b569bf56dfaa1d132489fa9b6ec15aa701181bbf255d` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/command.log` | `6005085973f3c71845dea4cbc0fc8f7e9ae35e638db83678dfe943194b02b235` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/resource.json` | `faf48720b512f1794d37c3406861771d2a1e8bfc03dab51ce0421f7e241f50f4` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r1/python-crash.log` | `29393a020fdabf5fc2986a11812e3e5836346c598485c86f3850b1564e97ad69` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_empty_preview_does0/.local-test-evidence/C05-20/control.json` | `343d8781ec800a1d8434ac1167b8815e1b3430d7d377979021f49bcb459085ef` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_empty_preview_does0/.local-test-evidence/C05-20/execution.json` | `16607145824fc735fe27a2d73426c99ac431ce15cfe252a1d8408334176665b0` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc0/.local-test-evidence/C05-04/control.json` | `48a6668ed922b9de8c462da5a3d210c81ef10c8975b5650b410928502582b9bf` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc0/.local-test-evidence/C05-04/execution.json` | `61f6d11712ac766ce5a076f9331def79af5054f1fdee9404aa13f78787b4d8e7` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc1/.local-test-evidence/C05-09/control.json` | `4b1b5fc2699309ec804079bb28c37d03bed02feec866c8ab1849a6f55d901c09` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc1/.local-test-evidence/C05-09/execution.json` | `67abaeb689c3078b8425d339ef62e72baa72e9b326e6921a50c05e827fcb16b4` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc2/.local-test-evidence/C05-14/control.json` | `48a6668ed922b9de8c462da5a3d210c81ef10c8975b5650b410928502582b9bf` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc2/.local-test-evidence/C05-14/execution.json` | `d0694f7424a57ec81de00d9073782756acd38e984812228963a40a19ed1dc7f1` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc3/.local-test-evidence/C05-20/control.json` | `4905ca07d38d367addd404fb7e720e24f9d4662d589d63d4d6885d01f2542afe` |
| `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r3/tmp/test_actual_main_task_setup_sc3/.local-test-evidence/C05-20/execution.json` | `003578a1c6a3042c2083fc9961fe838a13b5d2524fd81f01cf1cb9f003cdc281` |
