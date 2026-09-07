# C01-20 新候选显式复验 r4：EXECUTION_FAILED

2026-09-07。Host `eaa72b51`，Harness0.7.10 / Memory0.6.19 / Service0.3.13。在新增nullable安装态组合通过后，明确复验原失败链，保留r3原始失败；此次不是新的唯一语料，也没有隐式自动重试。

实际仅1次Provider请求被交出，约1.756s后返回HTTP400，SDK分类 `ProviderRequestRejectedError` / `provider_request_rejected` / retryable=false。没有模型响应、工具提议、路由或A/B召回，SDK终态failed、worker `EXECUTION_FAILED`。request_ref `7eccb488e2649972`；日志UTC 2026-09-06T18:49:59请求、18:50:01拒绝。未返回token用量，不能记作0 token。

公开attempt观察完整，但prediction observation不完整。packet的extra_proposed_types=0仅是缺少响应的派生值，不能解读为模型没有多提议或质量合格。当前旧SDK在状态拒绝处抛错，未保留结构化响应正文/private cause；具体HTTP400原因未知，不能归咎于nullable或relay转换。后继只增加有界诊断，先明确取得错误原因，再决定修复。

PG76962自然exit0 / 9.136s、峰456688KiB、minDisk3769MiB，remaining[]、cleanupnull、stopnull。资源wrapper成功仅表示自然收尾，业务仍失败；不是内存准入/预算拦截。

240条历史仍是3个不同case尝试、0通过、237未实际评分；C01-20已有两次不同候选尝试，分别保留。完整当前候选评测和原生验收未完成。防熄屏持续至整个测试结束。

| 本机原始证据相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r4/C01-20/execution.json` | `a883a9313a4135679db40464540af2bd42e456ab710f536a073203f7bb8cc1a5` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r4/C01-20/review-packet.json` | `29571208826715a408b0bcb8eb8515cb8d9d9d0ded45bb8fcb76a74b4df801bf` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r4/C01-20/observation-trace.json` | `0149a03a889e9be8eda393684dddd4a02536a96f3fde6a8eeb516a6b3c9d1d1c` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/resource-r4/resource.json` | `c4999fa3be2fc8c3411c7be49432d2c47b192efd2db33fa98c97d891041a4284` |
