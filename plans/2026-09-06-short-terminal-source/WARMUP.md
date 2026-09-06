# r10 冷加载缺口：最小源码候选

2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/wemm-startup-warmup`，base `35b070985a8e6c8df1891ba95e32ebb7df3e3695`。源码固定 `270320d3`，两项新公共预热控制通过，尚待独审及初次 native 查询验证。Procedure 的三处修改、两个新模块和旧契约保留 WIP，不属于此提交，也未由本轮两控验证。

## 原因与范围

主 r10 报告首冷 query FAIL、同进程一次暖态 retry 实际 PASS：口味无糖茉莉茶、名称青竹九月；暖态 tool call `call_1VqW2k23IvEhnXAzWhB2oMy8`。主负责原生原始证据，本叶未重复查询或读取原库。

只读日志 `primary-candidate/.local-test-evidence/2026-09-06/native077617/primary-ui-tjdppbuo/native.log`：09:49:33.134Z 为 `context_route_recall_timeout`，09:49:36.076Z 才记录 SentenceTransformer 模型构造加载，09:49:43.773Z catchup 完成。模型构造日志不能证明共享 load task 到那一刻才开始；import/调度可能已在进行，不用这两个时间点猜线程启动时刻。

确定接线缺口是 `main.py` 的预热分支要求 callable `warmup`，而 WeMM 和其 Memory SDK Embedder 基类都无此方法。short worker 公共 generation 在相同 lineage／manifest 时直接恢复旧 cache、返回 replay，不调用 embed；启动 catchup 无待补向量同样不能证明模型就绪。已有 active generation 不等于本进程 query encoder ready。

## 生产增量

- `wemm_embedder.py::WeMMEmbedder.warmup` 新增公开异步方法，只委托已有 `_ensure_loaded`。使用同一实例、shared shield task 和物理加载锁，不额外 encode，不改 vector generation，不创建第二个模型。
- `main.py` 沿既有后台预热分支自动调用该方法；成功日志改取 `kind`，不再调用 WeMM 不存在的 `is_mock()`。不改启动任务的既有调度机制。

1s 查询预算、2048 token、worker timeout、隐私／来源检查、WeMM 模型资源与参数均保持。预热使加载不再必等第一次查询触发，但不是首查询 admission barrier；用户在 loading/failed 时提前查询仍可能按既有超时降级。暖态通过不能关闭该首冷准入边界，需主后继实际冷启动验证。状态读取仍不触发重试，预热失败不自动循环重载。

## 两项新控制结果

`backend/tests/memory/test_wemm_warmup.py` 两项使用已有 fake model factory，真实公共 warmup／共享任务／线程路径：并发预热和 waiter 取消只加载一次，预热零 encode、首查询复用；加载失败状态真实，后续显式 warmup 才重试。它们不代表真实权重加载或 main/native 首查询验收，也不重跑旧 WeMM suite。

主确认退出后磁盘满足默认准入，并指示槽空闲可运行后，本叶取得默认共享锁，两个新控制 **2 PASS／0.25s**；固定源码／测试未改，不重跑旧绿。没有导入真实模型包、读取权重、下载、encode 实际模型或运行 native。启动 main hook 为源码接线核对，本轮测试执行公共 warmup 的真实共享加载控制，不冒充 native startup。

PG93645 exit0、remaining=[]、cleanup_error=null；额外 ps 核同 PG 无成员。资源入口耗时0.868s、峰119616KiB、最低磁盘1462MiB，限制512MiB／90s，默认准入1024MiB，锁已释放。没有绕锁或修改准入阈值。

本工具环境没有 Hegel／Dirac 的直接子代理通信入口，任务列表只显示主任务；实际占槽由唯一默认 OS 锁裁定。源码及新证据供主转 Dirac 窄审，不声称已收到独审结论。原 r10 冷 FAIL 与暖态 PASS 保留，不覆盖旧结论。

原始证据全部 ignored，根为 `/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/wemm-warmup/`：

| 文件 | SHA-256 |
|---|---|
| run_controls.py | f60a42f5c452f8f92772f777ba16f344df43461640bde5af232abed8391c6965 |
| r1/command.log | fa665775964cf05a4b8c9ca6f3bb541d55819b03a8e9277e22b2aa4799804700 |
| r1/resource.json | 8a2e3259fc23e89d08dda1dda7da5eaab5f028f6b2647ca3df3c196464e5d948 |

命令：`<primary-m0614/venv/bin/python> /Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir <上述根>/r1 --rss-mib 512 --seconds 90 -- <同Python> -I -B <上述根>/run_controls.py <上述根>/r1`。carrier 只收集 `tests/memory/test_wemm_warmup.py`，加载实际 H077/M617 installed 和自有 Host backend，复用已有 fake factory 但不收集旧 WeMM 用例。没有安装或更换包。
