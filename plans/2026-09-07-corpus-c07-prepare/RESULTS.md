# C07 分批控制结果

2026-09-07。固定 ade43237，借用原 H079/M619 target（路径见 CONTRACT），未换新 SDK、未创建环境。Dirac源码限定审无确定P0/P1，仅允许首跑，不是最终结果ACCEPT。

首跑约定6项：**1 PASS / 5 FAIL，2.01s**。原20 setup/compiler边界控制通过；其余5项均在同一测试断言失败：`MemoryMutationPlan.ordered_evidence_refs` 不存在。它们已从真实 prepare 返回，经原代码确认 ACCEPTED application 和非空 graph；尚未完成该用例的后续断言。06/14未运行到最近组/下一实际请求，不报通过。

原安装版公开DTO定义 `simple_harness/runtime/memory_protocol.py:2899` 中 Plan字段为 `evidence_refs`（2908），分析Request才用 `ordered_evidence_refs`。后继仅修测试，保留三方精确比较：Request有序引用 = Plan.evidence_refs = 真实已入库S1的EvidenceRef；不删断言、不改产品/SDK。该修正固定41296300并获Dirac源码窄审认可；修后5红的实际结果见下，编译边界1绿未重跑。

默认runner：PG76517，parent/returncode=1，stop_reason=null，elapsed=2.788s，peak=162592KiB，minDisk=3810MiB，remaining_group_members=[]，cleanup_error=null。资源槽已经释放并通知主，未自行重试。首跑命令沿 CONTRACT 的r1命令。

原始证据保留于本树 `.local-test-evidence/2026-09-07/corpus-c07-prepare/r1/`：

| 文件 | SHA256 |
|---|---|
| command.log | 8376486c6319ea1d949342a3b0aa059a7ef8502eb0d00c9171d9e3216d9f9cce |
| resource.json | ff8184bc650f0f57522c546fe4e1927da9c75e2d475a774bc9da9a4f1e67f9c0 |

没有20-ready结论。原06/14评分Provider相位缺口保留；helpers/source准备控制、评分生产接线及真实模型质量分开计。当前限定helper控制已完成，ARCH仅回写此范围，不标S3/20例评分完成。

## r2：只重跑原5红

用户重新交槽后，固定 **41296300**（业务仍ade43237），同一原H079/M619 target执行：**5 PASS / 1 deselected，3.59s**。首批1绿复用，共 **6个唯一控制通过**，不是两批执行次数相加。无真实Provider、模型、native、build或新安装。

- 20条原setup bytes/hash和输入边界：r1已绿；这里只是编译覆盖，不是20条实际准备/评分通过。
- C07-01/03/11：实际semantic/episode/Procedure admission、ACCEPTED application、非空public graph，关闭重开同节点回读通过。
- C07-06/14：各自非空干扰seed；错误recent角色在零请求时拒绝；原完整两角色组经实际Host/SDK提交；另一个当前输入触发的实际ProviderRequest含原分角色历史，未泄露干扰S1。使用确定性transport，仅证明helper与原Context assembler，不是模型质量，也不表示正式评分Provider相位已接好。

PG77451：parent/returncode=0，stop_reason=null，elapsed=4.268s，peak=170448KiB，minDisk=4768MiB，remaining_group_members=[]，cleanup_error=null。已立即通知主释放槽，不再跑任何旧绿。

实际命令与CONTRACT的r1载体相同，仅将证据/basetemp改为r2，并给pytest增加 `-k "not all_twenty_original_setups_and_input_boundary"`；资源仍默认共享锁/2048MiB/180s/默认磁盘门，无override。

原始证据在本树 `.local-test-evidence/2026-09-07/corpus-c07-prepare/r2/`：

| 文件 | SHA256 |
|---|---|
| command.log | a70a00658d016e3068c5b139b7f0cc5f7db6d2bc90a4063d55eed8147af84493 |
| resource.json | b6be93114cb73e351a54dd180e5ead0fa7681b92f1bf9c83b8ea0467b74855dc |

剩余：正式评分session的06/14 setup-only Provider→生产Provider相位；18标量的actualmain组合与全部20模型质量尚未执行。本次不切换主H0710、不提升readiness、保留原r1失败。结果已送Dirac限定终审，源码/测试修审不代替结果终审。
