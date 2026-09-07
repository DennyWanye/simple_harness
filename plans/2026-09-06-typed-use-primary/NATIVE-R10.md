# 原生r10：短期暖态召回通过，冷态首查超时

最后更新：2026-09-06。Host `0bedaa87c3a18d7700df9e9666ffa766d1944450`，固定H077/M617，沿用18ec7194前端/Rust和原userdata。包含已独审合入的closure物理请求/恢复锁修复及短期FTS+VECTOR接线。Tauri管理唯一backend；真实gpt-5.5与实际WeMM，无人工补索引或SDK私有业务调用。

## 实际结果

- 冷态首查 **FAIL**：Host `3768e971-53d8-51ba-a9f7-403de57ebba1`，SDK `product-sdk-9b662c427361d8b36df5e46076620e71ca439cf386776d8389727338f2082e32`。UI实际输入短期无糖茉莉茶，核对参数后允许一次。工具 `call_oAriyexGWcOxKudbFDWukLyE` 报 `context_route_recall_timeout`，模型答无片段。不能将查询失败等同于无记忆；日志随后出现模型加载及embedding catchup完成，首查加载/预热路径继续定位。
- 暖态对照 **PASS（仅本场景）**：观察上述加载完成后，仅重试一次。Host `a5dfea98-1aaa-5dd5-b027-525155d5f28d`，SDK `product-sdk-ec2d94e6aa1fcaed0b4597f3cbafc1e4501e126840e0ff34c1f509b3c44510d3`。真实UI允许short=true/types=[]/query无糖茉莉茶；工具 `call_1VqW2k23IvEhnXAzWhB2oMy8` 成功，effect `effect-801ac45552daabdf66357c4753373f5e5318dd7b78afdbdf0b487bc4eb4bf8e6`，receipt `dadc0dde-fa30-5b13-82a6-0c9dd0f4e98b`；三个 `recall-item:58dca93d6dc408bd2a87a4c0:1/2/3` 可见。真实模型最终答“最初的测试名称是青竹九月，口味是无糖茉莉茶”。成功截图保存了本次工具引用和最终回答，另通过UI打开完整消息读取入口。
- 两轮均回到UI空闲/排队0，日志closure clean/provider_calls0。这里是无TaskScope的干净收尾，不代替新的Scope closure九项或真实TaskScope长旅程。

本次暖态结果验证了原r9空typed selection的FTS/VECTOR修复；原r9内部候选计数used不等于最终selected命中，其确定根因见[只读诊断](../2026-09-06-short-terminal-source/R9-RETURN-DIAGNOSIS.md)。没有为通过测试增大1s/2048预算，没有重跑长期召回/遗忘的旧绿。首冷查、质量分母、延迟p95与原长旅程仍未闭合。

## 资源与证据

场景完成后正常CmdQ。PG89400/native89405，runner exit0/parent0，668.068秒，峰1302400KiB，remaining=[]，cleanup_error=null。运行期最低磁盘476MiB，高于256MiB停止线；退出后1495MiB，满足默认1024MiB准入。没有覆盖资源限制或删除原始证据。

以下路径相对主候选，原始证据全部ignored。冷态AX显示失败；冷态截图当时处于旧历史滚动位置，不单独作为本次失败视觉证明。暖态成功截图已实际滚动到底部核对。

| 证据 | SHA-256 |
|---|---|
| .local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/launch.json | 03d7a3fe32324ae7e2de7222e2c8131030d5685b469f3d1409e958f7b4be8746 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/native.log | a21f6484d51cc334339d3ecc6e9ce0bbdc5476ff2378b82df063dc349b8934c2 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/short-cold-timeout.ax.txt | 437e32692c875740fecfc03f0497c33600286216a2db92dda27065d941464c2c |
| .local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/short-cold-timeout.png | fb91f29e1f338ef288bcd67f443428c90cc64d683f6b87b351facdc3721dda55 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/short-warm-success.ax.txt | de80439199fe29ed0e7748fb5e3d6c97d6b297c9449f27d935af661a7cd086de |
| .local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/short-warm-success.png | 23b0efe437eb67e680880ae192983b7fb7ccf826d82bbd5f44ed20de41639c36 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/short-tool-detail.ax.txt | d4bca7c11e17d6865ed299505784b311a12047897ae185132015867947151387 |
| .local-test-evidence/2026-09-06/native077617/launch-r3/resource.json | 1580379bb9e78319f535f117ddebcdcd75c4eeccb284ac5b92c6edc2e080af65 |
