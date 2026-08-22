# Committed Turn、outbox 与删除屏障黑盒用例

## SDK-T01 — terminal truth table 与原子 crash windows

绑定：AC-3, AC-7；TO-03, TO-07。类型：automated fault/durability。

1. 通过公开 runtime 分别产生 completed、tentative tool result、parse failure、invalidated、cancelled、
   failed Turn；等待后台投递收敛。
   - 预期：只有 completed user→assistant Turn 产生一份可观察的 committed Memory record；其余为零。
2. 对 completed 路径分别在 terminal commit 前、terminal 已确认但后台投递前、Memory apply 后但确认前
   强制终止进程并恢复。
   - 预期：commit 前无用户响应/Memory；commit 后用户响应不回滚；最终每个 completed Turn 恰好一份 pair
     与一组派生 Facts，不出现半条 user/assistant。
3. 在 record 暂时失败时先观察已完成响应，再恢复可用性。
   - 预期：响应保持 completed；后台自动重试并最终收敛，无需消费者维护 retry 逻辑。

主证据：公开 terminal/receipt、Memory export count、fault timestamps、重启前后用户可见状态。

## SDK-T02 — 重复投递、并发 claim 与 payload conflict

绑定：AC-3, AC-7；TO-03。类型：automated concurrency/idempotency。

1. 让至少两个并发 dispatcher 对同一 committed Turn 竞争投递，并重复恢复三次。
   - 预期：接收端返回 applied/already-applied 的稳定结果；最终只有一份 pair/Facts。
2. 使用相同 idempotency key 与完全相同 payload 重放。
   - 预期：成功幂等，无新增消息、事实或后台工作。
3. 使用相同 key 与不同 payload 重放。
   - 预期：稳定 conflict/dead-letter；原记录不改变，用户已完成响应不回滚，日志不泄漏 payload。

主证据：并发投递 receipts、最终 export/count、conflict code、脱敏事件。

## SDK-T03 — delete/forget 后的迟到事件与新 Turn

绑定：AC-3, AC-4, AC-7；TO-03, TO-04。类型：automated erasure/recovery。

1. 开始 Turn A 并让其在 record 前暂停；对同一 principal 执行公开 delete，然后恢复 Turn A 的迟到投递。
   - 预期：迟到旧事件被稳定拒绝且不会复活内容或 Facts；重复恢复仍不复活。
2. delete 完成后开始 Turn B，同时让 recall 因 DB outage 无法取得正常结果；DB 恢复后允许 record 重试。
   - 预期：Turn B 主任务以空 recall 完成，并在恢复后可形成一份新 committed record。
3. 用与删除边界相等或倒退的可信时间 fixture 重放。
   - 预期：fail closed，保留可诊断重试状态；不得写入或复活。
4. 在 forget 与后台 fact job 竞争时重复相同步骤。
   - 预期：被 forget 的 fact 不会由迟到工作重新产生；其他授权内容不受影响。

主证据：delete/forget receipts、Turn A/B terminal 状态、最终 export、稳定拒绝码、重启结果。
