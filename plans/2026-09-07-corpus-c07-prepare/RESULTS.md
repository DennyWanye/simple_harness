# C07 首批控制

2026-09-07。固定 ade43237，借用原 H079/M619 target（路径见 CONTRACT），未换新 SDK、未创建环境。Dirac源码限定审无确定P0/P1，仅允许首跑，不是最终结果ACCEPT。

首跑约定6项：**1 PASS / 5 FAIL，2.01s**。原20 setup/compiler边界控制通过；其余5项均在同一测试断言失败：`MemoryMutationPlan.ordered_evidence_refs` 不存在。它们已从真实 prepare 返回，经原代码确认 ACCEPTED application 和非空 graph；尚未完成该用例的后续断言。06/14未运行到最近组/下一实际请求，不报通过。

原安装版公开DTO定义 `simple_harness/runtime/memory_protocol.py:2899` 中 Plan字段为 `evidence_refs`（2908），分析Request才用 `ordered_evidence_refs`。后继仅修测试，保留三方精确比较：Request有序引用 = Plan.evidence_refs = 真实已入库S1的EvidenceRef；不删断言、不改产品/SDK。修后5红 **NOT_RUN**；编译边界1绿不重跑。

默认runner：PG76517，parent/returncode=1，stop_reason=null，elapsed=2.788s，peak=162592KiB，minDisk=3810MiB，remaining_group_members=[]，cleanup_error=null。资源槽已经释放并通知主，未自行重试。首跑命令沿 CONTRACT 的r1命令。

原始证据保留于本树 `.local-test-evidence/2026-09-07/corpus-c07-prepare/r1/`：

| 文件 | SHA256 |
|---|---|
| command.log | 8376486c6319ea1d949342a3b0aa059a7ef8502eb0d00c9171d9e3216d9f9cce |
| resource.json | ff8184bc650f0f57522c546fe4e1927da9c75e2d475a774bc9da9a4f1e67f9c0 |

没有20-ready结论。原06/14评分Provider相位缺口保留；helpers/source准备控制、评分生产接线及真实模型质量分开计。当前未完成验收，不写ARCH完成状态。
