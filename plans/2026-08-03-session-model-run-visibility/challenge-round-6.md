# Plan challenge 第 6 轮

## 致命问题

- provider incarnation/revision 只能发现 provider 配置变化，不能发现两个窗口对同一 Session 的 binding
  lost update；若 set-model 不带 expected binding epoch，旧窗口仍能覆盖新窗口。

VERDICT: FAIL

## 计划修订结果

- Task 1 已要求 hydration/ack 返回 binding epoch，所有 set-provider/set-model/clear/inherit 请求都携带并
  CAS 校验 `expected_binding_epoch`；涉及 provider 时再校验 incarnation/revision。冲突只刷新，不写库。

上述修订进入第 7 轮独立挑战，第六轮 FAIL 不改写为 PASS。
