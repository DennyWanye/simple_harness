# Identity、scope 与隐私生命周期黑盒用例

## SDK-I01 — personal/family/household 隔离与可信身份边界

绑定：AC-4, AC-7；TO-04, TO-07, TO-R2。类型：automated security/concurrency。

1. 建立 A/userA personal、A/userB personal、A family、B/userC personal/family 六组不同 canary，
   通过公开 recall/export 同时查询。
   - 预期：userA 只见自己的 personal 与 A family；userB 同理；B 永远不可见 A；并发查询结果不串线。
2. 通过正式 `MemoryManager.share_fact(MemoryPrincipal(...), fact_id)` 分享userA personal fact：重复同调用、
   userB/cross-household调用、随后forget source并触发late fact job。
   - 预期：重复返回同projection id且仅一行；越权稳定conflict；family projection保留`projection_of`，
     forget级联删除且late job不能重建。
3. 在普通消息、Memory 内容、tool output 和模型响应中放入伪造 actor/household/scope 指令。
   - 预期：可信 identity 与 scope 不改变；伪造文本仅作为不可信数据，不能获得额外结果。
4. 对已绑定 session 尝试更换 deployment、household 或 actor，再以原 identity 正常继续。
   - 预期：换绑请求在 recall/record 前稳定拒绝；原绑定与数据保持可用。
5. 尝试让 execution DB 与 memory DB 使用同一个已解析路径。
   - 预期：runtime 启动前 fail closed，且不创建或修改目标数据库。

主证据：完整可见性矩阵、并发 correlation IDs、rebind/spoof 错误码、文件 hash 前后对比。

## SDK-I02 — export/delete/forget/cascade 与 diagnostics 脱敏

绑定：AC-4, AC-7；TO-04, TO-07。类型：automated privacy lifecycle。

1. 对 A/userA 分别执行 personal export、family export；使用无权 actor 请求相同资源。
   - 预期：授权导出含明确版本、identity/scope 且仅含目标数据；无权访问返回零结果或授权错误。
2. 对 userA personal 执行 delete，随后 recall/export；再验证 userB personal、A family 与 B 数据。
   - 预期：目标消息、Facts、向量、recall snapshot 和 job payload 消失；非目标数据逐字节不变；
     最小 hash-only 防重放 receipt 可保留但不含原文。
3. 对单个 fact 执行 forget，并在后台工作重试、进程重启后查询。
   - 预期：该 fact 不再出现且不会复活，未授权范围不被级联删除。
4. 以 canary 覆盖 Memory 原文、token、embedding 数值、绝对路径，扫描 export 之外的 diagnostics/log。
   - 预期：事件只含允许的 ID/hash/count/bytes/duration/error code；全部敏感 canary 为零命中。

主证据：versioned exports、delete/forget count receipts、非目标数据 hashes、重启结果、canary scan。
