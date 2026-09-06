# 时间提醒生产 lane

2026-09-06。Host独立feat/prospective-runtime-lane，base7844cf67。冻结H078/M618不改，nonSELF c0fbe30a保留原分支；主原生期间只写源码/送审。

## 真实缺口

主r14真人21:00青竹清单提醒，SDK后台已pending且trigger_at=1788699600正确，Host registration/occurrence为0；到期后普通17+26只答43，主判native FAIL，原件/失败不改。源码main只compose A7 occurrence coordinator，没有登记consumer/ProspectiveScheduler生产调用；Memory runtime builder还未注入已有prospective signal authority。不是仅UI刷新或A7文案问题。

## 最小接线

复用S5cStore、HostProspectiveSignalAuthority、ProspectiveSignalStore、PublicRegistrationAuthoritySource、ProspectiveRegistrationConsumer、PublicTimeAuthoritySource和ProspectiveScheduler。Memory builder注入一个只按真实reference类别路由的Host resolver，各delegate仍验证完整owner/authority/hash；不由调用者提供grant，不续期旧grant。不新增ledger/schema/后台SDKRun/LLM。启动52由现官方Host初始化负责，lane不私造迁移。

compose_human_memory_runtime默认挂一个lazy ProspectiveRuntimeLane；唯一MemoryAnalysisLane.start/close拥有它的启动/取消join。时间任务有独立轻量async loop，避免analysis一次Provider慢调用/短索引编码阻止timer。每轮先recover/登记公共outbox一页(32)，再既存scheduler.tick(32)，周期2秒/明确wake。注册局部失败仍尝试恢复已持久timer；异常只记稳定阶段和类型、下轮复用原持久receipt，不造DLQ或skip成功。这里的页大小约束一次Host读取/调度项数，不承诺SDK总SQL成本/P99。无app关闭期间后台唤醒承诺；重启恢复已到期记录，同signal/occurrence身份。

同一可信runtime clock贯穿source grant/registration/timer，不修改SDK物理时间或旧hash。Memory负责current revision/lifecycle/suppression，Host负责真实登记/时间观察。timer apply提交但Host ACK丢失时，后继复用原reference走SDK exact replay；未知/不兼容/过期未消费保持真实错误。F01事件发布不在本片；A7继续按下一真实Provider请求current source/披露展示，timer触发不等于用户已看/ACK/完成。

前台系统说明一次时间提醒可由当前后台流程处理，当前请求结束后才可能产生候选；未得到实际持久登记/消费事实不得确认已设置、不得说无能力仅因为没有create-reminder工具。静态文案不是登记成功receipt，不为模型制造工具结果。

## 必要新控制（native期间未执行）

1. 实际compose/runtime公有Manager创建prospective pending后，唯一background lifecycle默认启动→真实登记ACK→到期public inbox一项；不直接手动consumer/timer绕生产接线。关闭/reopen同库唯一occurrence；APP退出不残留task。
2. analysis实际等待不挡timer；重复start一个任务，取消等待join，不关闭共享manager。注册失败不吞取消、不阻止已有timer恢复。
3. public apply提交后Host ACK失败→停止/新lane恢复，原reference相同、inbox单项；source suppress/未到期不触发。保留未交付不能说成功的文案约束。

只运行新增/实际失败所需控制，共享默认锁，主释放前不测试。源码/独审不当成r14 native转绿；后续原生由主统一验证。
