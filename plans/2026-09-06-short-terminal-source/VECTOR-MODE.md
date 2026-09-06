# 短期请求完整检索通道：源码候选

2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/short-vector-mode`，基线 `e04627c41d4e59ee832983a074ce0672ef8249d0`。本批尚未运行测试，供主代理转 Dirac 独审；不代表 native 修复已验收。

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

测试只证明公共 SDK 至 Host fragments，本次不代替实际 WeMM、Provider 接收或 native UI。独审后通过共享 145 资源入口串行执行这一新测试；与 Carver／Hegel 协调槽，不重跑旧绿、不更换锁。原生短期叶由主后续复测。
