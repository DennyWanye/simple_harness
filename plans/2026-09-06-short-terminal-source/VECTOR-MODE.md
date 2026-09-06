# 短期请求完整检索通道：源码候选

2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/short-vector-mode`，基线 `e04627c41d4e59ee832983a074ce0672ef8249d0`。源码固定 `f8b2d41c51be73fd7bc9b1957cfc5f580d0b64d3`，新增公共链路控制 1 PASS；供主代理转 Dirac 独审，不代表 native 修复已验收。

## 最小生产修改

`backend/deskpet/memory/human_memory_v7.py::HumanMemoryV7Runtime.typed_recall` 的 RecallContext 检索模式：`typed_short=True` 时请求 `(FULL_TEXT, VECTOR)`，RecallPlan 沿既有字段接收同一元组。显式 long-only 和旧默认长期调用仍为 `(FULL_TEXT,)`。混合长短期同样请求这两个模式；SDK 的短期 lane 按请求纳入候选，不为长期编造额外 vector 实现。

预算仍为 8 items／16384 bytes／2048 tokens／1000ms。source reader、privacy、完整消息组、suppression、审计、出站投影均沿用原路径。没有改 SDK、installed 环境、原库、模型参数或 generation。

## 唯一新增公共链路反例

`backend/tests/memory/test_short_vector_mode.py::test_large_fts_small_vector_public_typed_fragments_and_long_only`：

1. 实际 Host foreground、outbox、公开 conversation registration 产生两组原始对话及十组近期对话；后台 worker 经公共 projection／generation 索引。Provider 为确定性测试 transport，embedder 为公开接口的小测试实现，不调用真实模型。
2. 查询词仅出现在大组；小偏好组仅匹配受控 vector。按真实公开 registration 的完整正文和 SDK 当前 canonical payload 成本公式断言：大组 tokens >2048、bytes <16384，小组满足两项预算；不改 SDK 数据或硬写候选结果。
3. 实际 runtime→公共 typed result→Host selected-source 核验→`project_recall_fragments`，要求小偏好完整进入唯一 fragment 且带真实 evidence 依赖，大组被预算裁掉。捕获调用用 `wraps` 保留 observation 参数能力检测，不替换 SDK 返回。
4. 同一公共 context／实际源／预算下，另建 full-text-only 公共 Plan 作原行为反例，要求空 items、truncated、budget_exhausted；不复制前一请求的 observation 身份。
5. 另一个 long-only 请求核验 context／plan 均仍为 FULL_TEXT。

测试只证明公共 SDK 至 Host fragments，本次不代替实际 WeMM、Provider 接收或 native UI。主通知 Hegel 释放槽并指定本控制优先后，通过共享 145 资源入口串行执行；Carver 继续源码准备，不重跑旧绿、不更换锁。原生短期叶由主后续复测。

## 必要控制结果与原始证据

固定源码未改，实际 installed H077/M617 与自有 Host 源码运行唯一新增测试：**1 PASS／4.74s**。两组真实公开输入的成本断言、FTS-only 空结果反例、FTS＋VECTOR 小组完整命中及来源依赖、原预算、long-only 模式断言全部执行通过。没有运行旧绿色控制或真实模型。

资源入口总耗时5.534s、峰174064KiB、PG89042 exit0、remaining=[]、cleanup_error=null；额外 `ps` 检查同 PG 无成员。共享锁已释放。首次 r1 因预先创建 evidence 目录被入口 `exist_ok=False` 拒绝，未启动测试 child；改用未创建的 r2 执行，未修改资源入口。

证据根目录（Memory 自有树，全部 ignored）：`/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/short-vector/`。

| 文件 | SHA-256 |
|---|---|
| run_control.py | b772c50428d646f40921bb151571f3832940d876efa53629615af1ec0e73fe3b |
| r2/command.log | 07e03b714d10ca8c27a57414c1fdc1537a460fcedf9804a16f9cc9bf20bfc684 |
| r2/resource.json | cc0f7025ed7831a42e7342a1885230345fa5c2f5695d168b3714145a0b02d543 |

命令使用 `primary-m0614/venv/bin/python` 执行 `/Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir <上述根>/r2 --rss-mib 512 --seconds 90 -- <同Python> -I -B <上述根>/run_control.py <上述根>/r2`。carrier 精确加载 `primary-077617/installed` 与本 Host backend；未改 installed、SDK、原 userdata 或预算。
