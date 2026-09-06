# Foreground permission lease / terminal convergence

2026-09-06，base8b7c05cb；独立feat/foreground-permission-lease。
保留guard d524完成分支与graph只读分支。原native r6长期物化成功；授权约8分钟后
allow触发foreground_lease_expired，刷新仍executing，Stop只到STOP_REQUESTED。
这是原红，未更改现场DB/旧receipt，graph不纳入本叶。

Host改动仅foreground_runtime.py：

- Runtime持有一个精确(host_run_id,owner,generation) lease keeper；不是每次observe的局部心跳。
  prepare、WAITING、STOP_REQUESTED与terminal观察均保持原TTL续约。WAITING仍可让短驱动返回，
  保留现有drain/权限API时序；仅内存keeper持续，未新增schema/ledger。
- 每次keeper生成随机incarnation+递增ordinal作为幂等键namespace，不作authority。
  续租/回收仍走原store BEGIN IMMEDIATE与精确owner/generation/expiry校验。
  keeper失租必须记录安全错误并使当前驱动停止；不写伪FAILED/STOPPED终态。
  terminal后、异常后、close撤销并join自有任务；STOP_REQUESTED不是终态。
- 相同owner唤醒时也用store可信clock验证lease；仅actualexpired走原reclaim_expired得到gen+1。
  不延长默认300s、不凭wallclock判fixture期限、不续活旧generation。
- 已绑定且query存在：核对SDK Run ID/session/request与Host计算身份、真实claimed/preparation、
  start intent和已持久SDK binding，再继续原Run控制/终态。跳过的是重造旧context/start，
  不是未来Provider/tool当前权限与source检查。query异常不得当None重新start。
  旧HUMAN响应仍按原Hostgeneration/decision nonce/version拒绝；不将旧grant迁移。
- 已有actualSDK terminal优先读取真实证据，不对terminal发送迟到cancel；SDKfailed→HostFAILED，
  SDKcancelled与真实Stop intent→HostSTOPPED。旧terminal/archive/failedrecords不改。
- 空闲keeper只在存在durable pendingcontrol或SDKterminal时唤醒，轮询仅兜底丢失通知；
  不因WAITING反复prepare。原primary鉴权/独立SDKlease不改。

7项新决定性controls（未执行）：实际installedSDK与生产权限适配器，实际Hoststore、
公开decision/真实SDKterminal→Hostreceipt；仅Provider为确定性fixture，无native/model。
两个真实WAITING跨先前expiry再allow；live/expiredWAITING Stop；实际SDKprovider失败后
过期同owner回收Stop保留FAILED；晚到旧heartbeat与外owner回收；query不可用/foreignidentity
拒绝零重启零伪terminal。所有case退出join keeper/driver。
冻结H075/M616及环境不改；仅借用原target消费，不宣称新独立install/pin证据。
