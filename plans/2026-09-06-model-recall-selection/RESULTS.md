# 模型召回类型接线：验证结果

最后更新：2026-09-06。独立树 `simple_harness-model-recall-selection`，基于主组合78647bb0；最后合入af985127仅架构/结果文档，不改变测试产品源码。

## 已完成的局部行为

`context_route(memory_standalone)` 要求显式、非空、去重的长期记忆类型。Host保留身份、披露上下文及预算，实际Memory公共RecallPlan只请求模型所选类型，不自动扩成旧固定三类。显式长期请求不附带短期查询，也不误报短期服务不可用。内部直接调用省略类型时保留旧三类型和短期路径；它不计为模型预测。

成功路由在现有Host invocation的detail中保存类型枚举和model_proposal来源，与实际Run/call/effect及proposal_hash同一记录绑定；不会复制查询正文。该Host持久投影不是公共SDK审计页，不等于能回读完整原proposal或plan。缺失/非法选择在Memory访问前拒绝，已有拒绝日志继续保留；失败/超时attempt的完整类型投影仍未覆盖。

## 实际验证

最终含安全审计枚举的固定产品源码：**77 passed /50.57s，exit0**。独立子进程墙钟51.42s，峰值195888KiB（约191MiB），未触及2GiB/180秒限额，进程已结束。

使用主组合专用venv（H0.7.3/M0.6.12/S0.3.12），PYTHONPATH仅本叶backend，无SDK源码覆盖。这不是本叶独立安装/启动身份验收；制品逐文件身份由主组合既有100项组合记录单独提供。禁导入torch、transformers等本地模型；没有真实Provider、原生UI、构建或模型下载。

覆盖：真实工具服务到实际安装Memory公共执行（四个选型组合）、缺失/非法/重复参数拒绝、初始化前拒绝、固定now下同计划重放与改类型幂等冲突、持久类型投影回读、无召回路径、真实选中记忆的最终出站来源与late-forget拒绝、实际任务披露reader/恢复/文件终态，以及Memory attempt审计。

新类型透传用例的数据库为空，证明公共请求形状，不证明非空Prospective召回或类型质量准确率；出站用例使用MockTransport，不能称真实Provider测试。固定时间重放不承诺任意墙钟TTL重试。独立只读复核固定49249dbd限定ACCEPT：未发现P0/P1，核验8份日志/资源hash；未重跑测试。主组合已直接fast-forward到同一源码，无额外产品合并差异。

## 历史失败与修复

- 首次受影响组47PASS/14FAIL：10项被SDK不支持uniqueItems阻断初始化；1项误用了异常类型；3项任务恢复旧fixture缺少现在必需的披露reader。
- 删除不支持schema关键字但保留运行前重复检查，幂等负例改用SDK公开MemoryIdempotencyConflict后58PASS/3FAIL。同3项在未改主组合基线独立复现：10PASS/3FAIL。
- 修复的只是旧任务测试夹具：显式注入可观测披露reader，使用当前task_scope_id/source_hash结构，断言返回经过reader替换的内容；增加缺reader仍拒绝的两个负例。未放宽生产权限检查。这组fake reader仅协议证据；另加入真实scope runtime组验证实际来源链。
- 此后77PASS/50.46s；独审发现原日志仅哈希，再加成功类型枚举后重跑得最终77PASS/50.57s。两次77不相加，全部旧红保留。

## 命令与证据

工作目录为本叶。Python为 `/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python`。

```text
PYTHONPATH=backend <python> <本地bounded_pytest.py> 
 backend/tests/sdk_adapters/test_model_recall_selection.py
 backend/tests/sdk_adapters/test_context_route_tool.py
 backend/tests/sdk_adapters/test_no_recall_gate.py
 backend/tests/memory/test_cognitive_typed_barrier.py
 backend/tests/execution/test_primary_history_outbound.py
 backend/tests/execution/test_scope_disclosure_runtime.py
 backend/tests/operation_audit/test_memory_attempts.py
 -q -p no:cacheprovider --basetemp=<本次证据目录>/tmp
```

原始日志和资源记录仅在本机ignored `.local-test-evidence/2026-09-06/model-recall-selection/`；baseline在主组合相同目录。下表是相对索引，不上传原始产物。

| 文件 | SHA-256 |
|---|---|
| affected/identity-bounded.log | ce8581d21a410692028618c7fd39b0660e55952d75d488f5452d8d91f51efac0 |
| affected/identity-bounded-resource.json | 668a144d0ce3c977ff1ecf685ef319a82e4d1d455328b00edce95d52edba08b4 |
| affected-v2/identity-bounded.log | 3e578483592e56025c093a8e4d57972e8d79460bf63545fa66ad5073c91fbd85 |
| affected-v2/identity-bounded-resource.json | 4130498798356faec8518d2583cca7eb0496e0b67d9605f63b48a4d3e3272ceb |
| final/identity-bounded.log | 05611f9182aa42016371a430d8102c68d1caa3ead7fc1c12e596d292bfb39e34 |
| final/identity-bounded-resource.json | f4f74008b8239ead455a53e335788c749f3b8c4f8ca7b3b63d3e6df2f826e1d3 |
| audit-projection/identity-bounded.log | e486f5648dafc002bd137f1f8fc3ae8d4d30a6a10ea9af7745b7e38e14f76609 |
| audit-projection/identity-bounded-resource.json | 1a992cc8789479823a0e8b2d8e5d6ae5c5b24e1be293ecd60ec68c5f834845eb |
| 主组合 baseline/identity-bounded.log | c642463e3db96c1154191a278527b8edce863b681f0cf8147f08196520783e22 |
| 主组合 baseline/identity-bounded-resource.json | 30bae31fccb7476875ffcf060d49d6cc74a8f8ca245294e4b1df085cf9afee92 |

## 剩余边界

模型质量240题/两次独立运行、401矩阵、短期生产调度与来源、Procedure适用性、Prospective调度、受控审计UI及全部Agent操作覆盖继续独立推进。此叶不代表全计划完成；未切换主工作目录运行环境、未推送或发布。
