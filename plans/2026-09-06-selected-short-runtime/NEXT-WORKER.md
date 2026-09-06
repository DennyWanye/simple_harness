# NEXT-WORKER：复用唯一后台 lane

2026-09-06，只读设计。核对主组合 `fe006f59b16e1ee2b2892cadc6e314de8dcc5634`
（含 selected-source `7fafe03a`）。不改 schema/ledger/SDK0612，不实现新模型 short 协议；未运行测试。

## 当前事实与最小接线

- `backend/main.py:8998–9070` 的 `_activate_memory_analysis_lane` 只创建一个
  `MemoryAnalysisLane`，默认 start；`memory_ingestion_outbox.py:429` 的 tick
  依次 `worker.run_once()` → `runner.run_once()`，现无 short 登记阶段。
  outbox delivered 只证明原 USER 入库/lineage 持久，不要求 analysis 已物化；
  APPLIED 也可能 NO_MUTATION，不能作为短来源登记的成功前提。
- 在这个 lane 注入共享 runtime/真实 conversation authority 的 short step，
  每轮 outbox 后、analysis 前最多处理一页；不创建第二轮询 task/另一个 manager。
  short 局部失败不得阻止本轮 analysis；全局 DB/authority 故障退避并明确 unavailable。
  保留 tick 现有返回契约，另返回/保存有界 short 进度供调度判断。
- 现 `short_indexing.py:reconcile` 全量枚举、先收集全部组，再逐源写入，不能直接
  塞进每个 tick。`conversation_registration.py:completed_run_ids` 无 LIMIT；
  `_group_tx` 已验证真实 terminal/S1/原 USER delivered lineage 和完整两消息结构。
  应抽单组登记方法并加分页入口，继续复用它，不能拆开或简化证明。

## 无新账本的分页与确认

1. **分页扫描所有 turn 身份，不先过滤 completed/delivered。** 每轮扫描固定 upper
   enqueue_sequence，以 `(subject, actual primary, sequence)` keyset 取下一页
   （建议16组预算，仅执行配置，不改原验收阈值）；每组完整处理，不能按 message 分页。
   未完成、未 delivered、旧无 producer、多消息/tool 分别记录有界 reason，扫描指针仍前进。
   init receipt/marker/epoch 与该页身份读取同 Host read TX 核验；每个 group 再按既有
   read TX 重验。释放 Host read TX 后才 await SDK 写入，不持锁跨存储调用。
2. **cursor 是可回绕扫描位置，不是持久成功水位。** 到 upper 后回绕，刷新 upper；
   新 delivery 可 wake lane，但不能只依赖通知。早入队/晚完成或晚 delivered 的低序号组
   在下一轮必重查，不能因较新组成功被永久跳过。回绕进入原 poll 等待，不以“扫描了
   pending/bad 组”制造 busy 无限循环；持续新消息也不能改变当前 upper 饿死旧段。
3. 原 Host terminal/outbox 是重建依据；SDK 公共 admission/registration 的实际 ACK
   才是登记确认。可用**有容量上限的进程内缓存**去掉同一 manager 生命周期中的重复写，
   键至少为 namespace、完整 registration refs/manifest、当前 manager 实例世代；
   只有全组 ACK 且 projection 成功后才能确认。pending/bad 不缓存为成功，失败不推进
   success；缓存淘汰只增加幂等重放，不能漏组。runtime close/rebuild 时清空，重启从头扫。
   不把缓存或 delivered receipt 冒充“本 Memory 文件已经登记”的持久事实。
4. 单组前提不合法/确定损坏：拒该组，记受控 reason，继续同页合法组；不伪造 missing
   child、不重新盖旧来源。组内第二次写失败可保留真实第一条 ACK，重试同一 refs；
   不称整组成功。全局 namespace/权限/DB 故障不吞成组成功，退避重试；取消直接传播。
5. 一页有实际登记进展后最多一次公共 `rebuild_short_horizon_projection`；另保留周期
   expiry/pruning 维护。ACK 丢失/投影失败须继续按原 refs 重放。**当前 SDK rebuild 本身
   是全投影操作**；Host LIMIT 只界定每步扫描/登记数量，不证明 SDK 总扫描成本、P99 或
   严格每 tick 耗时。实现前应查实际查询计划/测量，并给出超时/重试事实；必要的 SDK
   增量 projection 是独立缺口，不能用 Host 成功水位掩盖。

## 默认开启、退出与最小反例

做完即在现唯一 lane 默认接入，不新增 OFF/shadow。复用 `start/wake/close`；现在
`main.py:5751` 已先 await lane.close(5s)，但此段没有显式关闭 v7 manager。
实施需核清 manager 所有权：先停 foreground/所有该 manager 的消费者，再停 lane，
最后 await runtime.close；不得另建游离 task/遗留定时器。close 超时取消后 await 收尾；
已开始的 SDK 写按真实 ACK/幂等重放恢复，不捏造成功。manager reopen 清空优化缓存。

获得串行测试槽后只跑这些决定性控制：

- 真实11组通过生产 lane tick 自动登记，早组 hit/最近10排除；重复 wake/start 只有一个 task。
- seq1 先 pending、seq2+先 delivered；越过 seq1 后才 delivered，至少一次回绕后 seq1 真入索引。
  同测旧 turn 晚 terminal、启动时全 pending、扫描期间持续新增组。
- 首组坏 receipt/缺 child、完整四消息 tool、未完成组均不部分成功，下一合法组仍处理；
  namespace 故障整步 unavailable，不能混用新旧 primary。
- after source admission/第二 registration/投影前后/ACK 丢失 crash → close/reopen；同 refs
  幂等恢复、无重复 assistant analysis job，旧 USER lineage/receipts 保持原值。
- 扫描/登记/投影取消与退出，task/manager 实际清理；selected-hit 来源和最终 fresh policy
  仍严格拒绝被遗忘组，未选中坏组不污染合法 hit。

这是原短来源生产登记的后续范围，不把 worker 计划、两消息支持或测试夹具索引称为
完整 S3/S6 可用性；新模型 short 请求、多消息 producer、SDK projection 增量能力另列。
