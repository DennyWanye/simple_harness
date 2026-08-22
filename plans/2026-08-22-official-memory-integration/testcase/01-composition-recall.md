# SDK 组合与 durable recall 黑盒用例

## SDK-C01 — 极简官方组合、无 Memory 兼容与资源所有权

绑定：AC-1；TO-01。类型：automated public-contract。

1. 在 clean Python 环境仅安装待验 exact wheels，通过公开 Quickstart 构造可信 identity、一个
   `MemoryManager`，并将其直接传给官方 production runtime ports。
   - 预期：runtime 可启动并完成一轮对话；消费者无需实现 Memory Adapter，也无需手动 recall/append。
2. 以 `memory=None` 重复同一非 Memory 对话。
   - 预期：既有无 Memory 对话结果可用；没有 Memory stage、后台投递或 Memory 文件副作用。
3. 分别以默认 borrowed 与显式 runtime-owned 创建/关闭 runtime，再从消费者侧探测 manager 可用性。
   - 预期：borrowed manager 仍可用且消费者只需关闭一次；runtime-owned manager 被 runtime 恰好关闭一次；
     重复关闭不产生第二副作用或未处理异常。

主证据：clean-install 输出、公开 lifecycle probe、文件副作用清单、close counter。

## SDK-C02 — 每个 Turn recall 一次、冻结与恢复复用

绑定：AC-2；TO-02。类型：automated durability。

1. 用公开 conformance Memory double 为一个新 Turn 返回带唯一 canary 与 result hash 的有界 recall；
   记录公开调用计数，触发 provider retry、tool continuation。
   - 预期：同一 Turn recall 调用总数仍为 1；所有物理请求观察到相同 recall result hash。
2. 在 recall 完成后、provider 完成前终止进程，再用同一 execution 数据恢复。
   - 预期：恢复成功；不产生第二次 live recall；provider 仍消费相同 hash。
3. 开始下一个新 Turn。
   - 预期：只为新 Turn 新增一次 recall；上一个 Turn 的 canary/stage 不被错误复用。
4. 为root与连续两个continuation分别提供三个不同的content-addressed source ref，并在claim后/provider前、
   provider后/stage前各崩溃一次；再以相同continuation id但不同ref重放。
   - 预期：每个Turn只读取并恢复自己的ref/hash，绝不继承root；换ref重放稳定冲突。另一个不传ref的最小
     consumer continuation只得到当前message fallback，不需要产品durable cache。
5. 检查 provider 的公开/redacted request capture。
   - 预期：Memory 只处于 USER/untrusted data 分区；数量、字节和 deadline 均不超过请求硬上限。

主证据：per-turn recall counter、redacted request digest、stage/result hash、restart receipt。

## SDK-C03 — recall 失败、非法结果与 release 恢复

绑定：AC-2, AC-7；TO-02, TO-07。类型：automated fault。

1. 分别令公开 Memory double 在 recall 返回 timeout、transient error、corruption error、超界内容、
   错误 identity/lineage；每种故障启动一个新 Turn。
   - 预期：合法可信 identity 下的运行均以空 recall 降级并可完成；非法 identity 在 Memory 调用前拒绝；
     无异常文本、路径、token 或 Memory 内容泄漏。
2. 对其中一个降级 Turn 触发 provider retry、进程恢复。
   - 预期：同一 Turn 复用同一个 durable empty hash，不再次探测 live Memory。
3. 模拟 stage 已冻结但 recall release 暂时失败，重启 runtime 后恢复后台工作。
   - 预期：用户响应不受影响；release 最终成功且同一 result 只释放一次效果。
4. 扫描所有结构化事件与用户可见错误。
   - 预期：只含 ID/hash/count/bytes/duration/stable error code；没有内容、token、embedding 或绝对路径。

主证据：terminal result、empty hash、recall/release counters、重启 receipt、敏感字段扫描。
