# r11 首次 encode 超时：同实例启动 priming

2026-09-06，源码候选，未运行测试或真实模型。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/wemm-startup-prime`，base `082f68c0`；Procedure WIP 保留，不纳入本叶。

## 最早差异与测量边界

主 r11 日志 `primary-candidate/.local-test-evidence/2026-09-06/native077617/primary-ui-dnpdvuk9/native.log`：load-only `p4_embedder_ready` 在10:06:49.272454Z；首工具实际授权后 attempt 开始10:08:46.157467Z，失败10:08:47.231342Z，约1.074秒；其后首个 encode batch 才报告完成、耗时1.44秒。首个 `tool.invoked` 在10:07:44.001405Z，之后还有第二个 invoked／authorized，不把这段约62秒间隔归因 encode。日志是全局交错输出，缺少 encode 与 call ID 的直接结构绑定；其顺序支持首次冷 encode 超预算，不把 tqdm 粒度冒充完整线程时间线。

实际 tool call `call_dhaL3YhIhm5NlJpsRi4B6rhy`，Run `2cced912-b041-5f7d-95c9-2e5beae0c61c`，SDK Run `product-sdk-aa2d6e533de537b49e499b4f89ef540bccab95a048a871dfb8b783525b2acb87`。主保留首查 FAIL、未重试；load-only 成功不能关闭首次查询失败。

## 最小实现

`WeMMEmbedder.warmup()` 由 load-only 变为共享 `_warmup_task`：原 `_ensure_loaded` → 同一 `_encode_owned` 队列／物理锁执行一次固定句子 → 丢弃返回向量。固定文本为“这是一条用于初始化文本编码器的固定测试句子。”，无用户输入、身份或记忆数据。不调用 MemoryManager、recall、generation 或任何数据库写入。原 startup hook 不变；`p4_embedder_ready` 只有整个 warmup 完成后才打印。

并发 warmup 复用同一任务；取消外部 waiter 不取消物理加载／priming，不再排一份；成功后重复调用零额外 encode。失败明确保留未 primed，后续显式 warmup 可重试，无自动重试循环；若模型已加载，不重新创建模型。完成回调释放 task 引用，沿原异常观察规范。

原 `state/is_ready` 仍描述模型加载状态，新增 `warmup_state/is_primed` 描述启动 priming 状态，不混同；阶段日志区分 load 与 prime 耗时，不输出文本。prime 耗时包含等待原 encode 队列和一次编码，不伪称纯 kernel 时间。

查询1s／2048预算、来源及privacy门、模型参数、实例数、SDK和旧业务向量均不改。priming不是查询 admission barrier，也不保证所有长度输入都可在1s内完成；真实新进程首次查询仍由主验证，不能手动先失败再重试当完成。

## 必要控制准备

`test_wemm_warmup.py` 两项原 load-only 控制按变更后的契约修订，增加一个 prime 失败控制，共三项待执行：

- 并发预热＋priming期间取消 waiter＋查询排队，只有一次固定句 encode、一个模型，物理encode peak1；成功后幂等。
- 实际加载错误保持 failed，显式重试后才加载／prime，状态读取不重试。
- 模型加载成功但真实输出维度校验使 prime 失败，is_primed=false；显式重试复用已加载实例，成功后幂等。

只使用已有假模型工厂与真实任务／线程／队列，不加载模型权重；旧 WeMM suite 不重跑。源码供主转 Dirac，测试待主退出原生并释放默认资源锁／确认磁盘准入；未声称绿或 native 完成。
