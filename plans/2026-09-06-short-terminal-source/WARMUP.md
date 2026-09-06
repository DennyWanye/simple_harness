# r10 冷加载缺口：最小源码候选

2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/wemm-startup-warmup`，base `35b070985a8e6c8df1891ba95e32ebb7df3e3695`。本批只准备源码，磁盘准入待主确认，未启动测试、模型或 native。Procedure 的三处修改、两个新模块和旧契约保留 WIP，不属于此提交。

## 原因与范围

主 r10 报告首冷 query FAIL、同进程一次暖态 retry 实际 PASS：口味无糖茉莉茶、名称青竹九月；暖态 tool call `call_1VqW2k23IvEhnXAzWhB2oMy8`。主负责原生原始证据，本叶未重复查询或读取原库。

只读日志 `primary-candidate/.local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/native.log`：09:49:33.134Z 为 `context_route_recall_timeout`，09:49:36.076Z 才记录 SentenceTransformer 模型构造加载，09:49:43.773Z catchup 完成。模型构造日志不能证明共享 load task 到那一刻才开始；import/调度可能已在进行，不用这两个时间点猜线程启动时刻。

确定接线缺口是 `main.py` 的预热分支要求 callable `warmup`，而 WeMM 和其 Memory SDK Embedder 基类都无此方法。short worker 公共 generation 在相同 lineage／manifest 时直接恢复旧 cache、返回 replay，不调用 embed；启动 catchup 无待补向量同样不能证明模型就绪。已有 active generation 不等于本进程 query encoder ready。

## 生产增量

- `wemm_embedder.py::WeMMEmbedder.warmup` 新增公开异步方法，只委托已有 `_ensure_loaded`。使用同一实例、shared shield task 和物理加载锁，不额外 encode，不改 vector generation，不创建第二个模型。
- `main.py` 沿既有后台预热分支自动调用该方法；成功日志改取 `kind`，不再调用 WeMM 不存在的 `is_mock()`。不改启动任务的既有调度机制。

1s 查询预算、2048 token、worker timeout、隐私／来源检查、WeMM 模型资源与参数均保持。预热使加载不再必等第一次查询触发，但不是首查询 admission barrier；用户在 loading/failed 时提前查询仍可能按既有超时降级。暖态通过不能关闭该首冷准入边界，需主后继实际冷启动验证。状态读取仍不触发重试，预热失败不自动循环重载。

## 新控制准备，尚未执行

`backend/tests/memory/test_wemm_warmup.py` 两项使用已有 fake model factory，真实公共 warmup／共享任务／线程路径：并发预热和 waiter 取消只加载一次，预热零 encode、首查询复用；加载失败状态真实，后续显式 warmup 才重试。它们不代表真实权重加载或 main/native 首查询验收，也不重跑旧 WeMM suite。

源码供主转 Dirac 窄审；收到磁盘准入确认后再用默认共享资源入口运行必要新控制。原 r10 FAIL 与暖态 PASS 保留，不覆盖旧结论。
