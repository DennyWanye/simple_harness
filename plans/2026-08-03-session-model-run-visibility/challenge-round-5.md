# Plan challenge 第 5 轮

## 致命问题

1. `root failed > blocked` 会吞掉 terminal block signal，且 provider/workspace preflight 在 Root 创建前失败
   时没有地方挂 signal。
2. Tool sidecar 只冻结 projector id/version；插件升级或删除后旧实现消失，历史 Root 仍会改变公开摘要。
3. 前端 Session binding 请求不携带用户所见 incarnation/revision，旧窗口请求仍可能跨 remove/re-add 的
   同名 provider ABA 边界。

VERDICT: FAIL

## 计划修订结果

- Task 6 把 `failed + valid block signal` 冻结为 blocked，并增加原子 `start_blocked_root()` 处理三类
  preflight 失败；无 durable Root 的非法请求不伪造节点。
- Task 5 在 workflow.db v29 冻结完整声明式 `ToolPresentationPolicyV1`，工具 outcome 同事务物化不可变
  public projection；解释器属于 core 且版本永久保留，不再依赖插件代码生命周期。
- Task 1 修改前后端 binding 协议，请求必须携带用户所见 incarnation/revision/binding epoch；缺失或冲突
  刷新列表并返回稳定错误。

上述修订进入第 6 轮独立挑战，第五轮 FAIL 不改写为 PASS。
