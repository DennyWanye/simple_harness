# Prospective剩余6 lifecycle：公开结果

最后更新2026-09-06。执行源码ea0309525fca39bb7a24c3fcaea9c495dc797d99，base ea57e720；
首实现ec68f782被Dirac指出oracle目标连续性/正控来源P1，ea030952修复并获限定源码ACCEPT后才测试。
原P1为独立源码审查发现，不伪称曾运行ec68红测试；本轮测试首轮绿，无混源复测。

| 批次 | 实际结果 | PGID/退出 | 峰RSS KiB | 资源耗时 |
|---|---|---|---:|---:|
| tests-r1 | 1 passed in 1.17s | 37892/0 | 71184 | 1.306s |
| formal-r1 | 原6格6PASS/0FAIL/0BLOCKED，6OBSERVED | 37933/3 | 135536 | 1.091s |

唯一集成test方法包含6真实public正/负格与11篡改检查，不是17个pytest tests。
两个P1反例分别让返回receipt-view变成另一memoryID、candidate正控selected改旧revision1，
重算完整decision/result/item/replay hash并先通过check_execution_wire，再命中特定ID/来源绑定reason。
receipt-view承诺不等于view JSON hash，opaque receipt hash保留，不反造新的SDK原始receipt。

正式Run `b7b8fe83520d430b8c52d93f72f4bb4f`，dependency=[]；原格：
- eligibility/prospective-cancelled: PASS
- eligibility/prospective-candidate: PASS
- eligibility/prospective-completed: PASS
- eligibility/prospective-expired: PASS
- eligibility/prospective-in-progress: PASS
- eligibility/prospective-rescheduled: PASS

其余395未选，整体/层NOT_RUN/BLOCKED、exit3。前片13和本6各有固定源定向证据，
不是同一个19格Run，不能合旧182/source10/Procedure4声称新401全通过。主组合18tests未重跑。

## 实际路径与边界

in_progress/completed实际pending CREATE→public ACK→synthetic event matched/applied→exact authorized REVISE；
rescheduled实际pending ACK→REVISE→新revision的public outbox ACK；cancelled/expired实际授权状态更新。
expired原输入是event且无时间截止，**不是**外部定时到期signal；completed同样不声称真实外部任务执行完成。
candidate原合法CREATE/实际no_recall保留，再同ID合法pending+ACK公开正控，不用正控覆盖原负例。
所有REVISE actual receipt memoryID连续性、actual signal revision、完整源/hash/权限/recall query/clock/access/replay均独立校验。
无SDK private SQL、测试helper伪public、gold反造authority或原输入/阈值改写。
本片仍为synthetic scheduler SDK合同验证，不是Host提醒/Provider/native证明；projection与32非法组合原边界保留。

## 命令与环境

复用installed H073/M0613 own解释器：
`/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/venv/bin/python`。
无新venv/checkout/SDK overlay/模型/build/native。默认145共享OS锁入口：
`/Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --rss-mib 2048 --seconds 180`。
本次两批直接取得锁，无BUSY；未指定其他lockfile。

必要child：`<python> -m pytest testcase/human-memory-program/tests/test_typed_recall_prospective_lifecycle_public.py -q -p no:cacheprovider`。
环境PYTHONDONTWRITEBYTECODE=1、PYTEST_DISABLE_PLUGIN_AUTOLOAD=1。
formal child既有run_typed_recall_public_consumer.py，exact H073/M0613 wheel/source pins，原6个--cell、无--observe。
完整argv已记录下列command.json；launcher使用resource实际returncode，未以外层脚本exit0冒充formal exit0。

## ignored raw与SHA256

本树 `.local-test-evidence/2026-09-06/prospective-lifecycle/`，raw不Git。

| 相对路径 | SHA256 |
|---|---|
| tests-r1/command.json | dad27dfac20fca843639fc8c334a9481f5cc1a71ba1589d2e4807b8a9c67d459 |
| tests-r1/source.txt | 90564203429cf1c024c5c76ec21bb437db4b32f757272aa2a57466c63ea5b0ef |
| tests-r1/resource/command.log | b099717b0f4a9f62539b63fe5a8ebc7ae0aa524d60e983fbf07d0b6e38a1bf22 |
| tests-r1/resource/resource.json | 2c52ad398b56bc0e68fb98474d2354918e8ad5704e32ec6becf25b7fc37a3dea |
| formal-r1/command.json | 6af1f011b95104119e153e4706fc13318f7dd8a0d7ed3c478455acbab005c939 |
| formal-r1/source.txt | 90564203429cf1c024c5c76ec21bb437db4b32f757272aa2a57466c63ea5b0ef |
| formal-r1/resource/command.log | 276f0d0f8f2d9e4eb9704762def8d0a5cf7b896b6a51b436eb47b18c71e94c4b |
| formal-r1/resource/resource.json | 08fb420d5109958a572b79f34aafc92f9cbf29f4bbe9ceb392244439ac892e74 |
| formal-r1/public-data/bridge-summary.json | 6a7974c8e01acfa22c9a3448623d3cd540222c118ed9c0cfe2aa950ae97379f9 |

两resource均stop_reason=null、remaining_group_members=[]、cleanup_error=null；现场ps亦无两PGID。
自有进程结束、测试槽释放。后继合并bridge保留source/context_use/procedure/prospective/prospective_lifecycle所有oracle指纹。
