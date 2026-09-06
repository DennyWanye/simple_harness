# Provider 冷终态清理窄修

日期：2026-09-06。基线 `2c8c57c6`；仅 Host，H077/原 userdata 不变。

## 原反例与定位

r8 原日志 `simple_harness-primary-candidate/.local-test-evidence/2026-09-06/native077617/primary-ui-j_8ulfwm/native.log:83` 在 closure clean 后记录旧 SDK Run `product-sdk-195446e77bdc07ff908abdcb01018b600e51f4560b9cca492cc60b1963b85b4a` 的 KeyError。日志没有 traceback；以下为对应生产源码路径，不冒称现场堆栈证据。

冷恢复查询已有 Run 后直接 `_finish_bound`，不执行 Provider freeze/bind；真实 SDK 终态核验、Host durable terminal 提交之后，Provider cleanup 对空 `SdkRunBindingRegistry` 执行 `mark_terminal` 会抛 KeyError，阻断后续 tool cleanup 和 effect gate release。先前 cold 测试的 ProviderPort.mark_terminal 是空实现，未覆盖真实生产 resolver。

## 最小变更与边界

只修改 `_ProductSdkProviderBindingResolver.mark_terminal`：先维持合法 terminal state 校验，仅 binding 与 authority 缓存均不存在时返回；已有 binding 或不一致缓存仍走原严格清理，不捕获任何内部异常。不会重建旧 Provider authority、伪造终态或授权。现有 tool cleanup 和 Host durable terminal 提交顺序不变。

## 必要单项控制（待执行）

扩展现有 `test_real_old_host_expiry_stop_cold_new_stack_public_recovery`，换成真实 `ProductForegroundProviderPort` + `main._ProductSdkProviderBindingResolver` 的冷进程空注册状态，保留实际旧 H075 生产 fixture、H077 公共恢复和 H077/M616 既有载体。

独立断言：实际 FAILED durable terminal/公开 proof 相符、未重建上下文、零 Provider 调用、effect gate 已释放、第二新 stack 无工作重放；空注册非法 state 拒绝；已有真实 binding 的同名 KeyError 不被吞，恢复清理函数后正常释放。此项是受此次修复影响的旧 cold 控制，不重复其它已绿项，不冒充完整 main/native。

当前仅源码固定及 diff 格式检查；等待 Dirac 预审和 native 测试槽释放，未运行 pytest/build/install。通过后回写架构及实际分批结果。
