# G6：Host 所需的最小 Memory priority 消费合同

2026-09-05，T1 消费需求定稿；**待 SDK 实现，Host 尚未调用，不改变当前冻结 candidate**。依据原 S5 Task7、S5B-AC-5/A11 及用户本轮调度最小化要求。Host基础提交 `6c8ebddd` 已完成自动验证，独立review待主协调；本文件不是新的AC或权限批准。

## 公开 API 的唯一新增输入

建议在公开 package root 导出严格枚举 `AnalysisPriority`，值仅 `ordinary|immediate`；具体导出命名由 SDK owner确认，消费语义如下：

```python
async def ingest_committed_evidence(
    envelope: SanitizedEvidenceEnvelope,
    receipt: SanitizedEvidenceReceipt,
    *,
    analysis_lineage: AnalysisLineage | None = None,
    analysis_priority: AnalysisPriority = AnalysisPriority.ORDINARY,
) -> EvidenceIngestionReceipt: ...
```

不加 priority authority/ref/token、二级 ledger、额外签名或第二套 action权限。priority只选择已合法admit工作的调度顺序；不改变 evidence admission、principal/scope/disclosure、mutation/action authority或认知写权限。现有返回receipt继续复用，不要求仅为priority新增receipt类型/查询API。

Host T5在已有action journal中绑定explicit remember/correct intent，在该轮terminal之后选择immediate并首次ingest；普通terminal batch用ordinary。首次选择及其evidence绑定必须durable，重试读取原选择，不能根据后来的请求重新推断。不能先ordinary入库再改成immediate，也不能双建job。T2本身只有request journal，不含这条生产接线。

当前旧facade不接受此参数；Host不能捕获TypeError后删除参数降级ordinary。SDK切换前T5继续BLOCKED。

## 必要持久化与兼容门

1. ordinary v1 job payload继续严格为 `schema_version/evidence_id/envelope_hash/source_hash`，所有原字节保持；不得往JSON中添加priority、schedule provenance或action ref。
2. 最小实现优先考虑jobs独立 `analysis_priority` 列，有限枚举CHECK、不可变trigger；在evidence/job首次ingest同事务固定，旧行只可迁移为ordinary。无需复制Host action journal或在Memory建priority授权账本。
3. 已存在evidence的replay必须**在early-return之前**比较priority：相同返回原receipt，不同稳定幂等冲突且零写。参数省略即ordinary，因此immediate的retry必须显式传原值；不能把省略解释为“任意已存值”。
4. 新列/新状态要求明确的Memory minor release及schema升级；具体版本号由SDK owner确定。open/integrity/recovery都要校验新schema，**旧0.6.3打开新状态稳定拒绝且不改原文件**，不能只改wheel版本、或让旧runtime继续按ordinary读取。SDK若复用别的持久化结构也必须满足同一拒绝门。
5. 这是一项版本化调度合同，不是新增权限。主线程为S3冻结的0.6.5 source/candidate保持独立，本线程不实现SDK、不构建/安装或改pin。

## Claim 顺序与冻结事实

- 首先按原协议reclaim已存在batch；既有batch的成员/请求/attempt/result/plan不能因为priority变化而重建。
- 同principal已有handed_off/result_committed时继续遵守0.6.3 active fence，先收敛固定base_revision的原plan；immediate不抢占它。
- 对剩余eligible pending groups先选immediate，再选ordinary；priority必须参与新batch的分组/成员选择，禁止两类混batch，即使同principal/Run/disclosure/batch key也如此。ordinary-only路径保留原排序/等待语义，不因新增列无谓改写其原始payload或durable result。
- immediate绕过普通max_batch_wait/batch-size等待条件，但不绕过not-before/retry eligibility、lease、最大attempt与unknown-call分类。
- 最终物化仍走原executor/delivery authority、原mutation权限与revision冲突检查。不得额外调用LLM、提高base_revision或改旧result来“修复”调度冲突。

## SDK 可交付给 Host 的决定性证明

| 验证 | 必须观察到的结果 |
|---|---|
| 默认/显式ordinary、迁移旧行 | 原v1payload byte/hash一致、旧证据及结果守恒；现有ordinary行为不回归 |
| immediate首次ingest、lost-ACK重开 | priority固定、原receipt/job复用；同priority幂等，不同priority（含省略）拒绝且零写 |
| 同batch key有两类pending，未到普通maxwait | immediate可claim且无ordinary成员；ordinary继续原等待；无双job |
| 旧batch reclaim或同principal apply失败 | 先恢复旧batch/固定plan，immediate保持pending；已durable result不新增Provider调用 |
| 非法值/试图更新列 | 拒绝，原payload/priority不变；不获得任何mutation/action/披露权限 |
| 新schema→旧0.6.3 opener | 使用exact旧runtime真实reopen稳定失败，原DB/hash不变；不能用改常量模拟 |

这些是原“高优先且幂等”的实现约束，不新增独立权限框架。SDK owner交付此公开API、schema/open门及决定性测试后，Host才恢复T5接线；T6披露/旧checkpoint问题仍独立阻塞。
