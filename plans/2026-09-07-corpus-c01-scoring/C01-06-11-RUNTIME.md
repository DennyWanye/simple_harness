# C01-06/11 正式准备接线

2026-09-07；当前源码待窄审，两个新控制 NOT_RUN。无网络、无新制品；旧质量 FAIL 不变。

原事实源为 Memory `review-zh/successor-12x20/01-exact.md`，C01-06 第49–56行、C01-11 第89–96行。不改成员、gold、类别、阈值。两例公共 fixture 时钟仍 `2026-09-06T10:00:00+08:00`，不从答案导出日期。

| Case | 原 setup 的固定 SHA-256 | 实现 |
|---|---|---|
| C01-06 | `1ccbc6d8c455e6c4a60ac53ab1bdd98c14baed2c57fd4dbce59baa617640236c` | 真实任务创建 B（老师），同 memory ID 公开授权 REVISE 为 A（小周） |
| C01-11 | `4472b35fd4b2b00e1ed96342c0eb4e8a233e143253ffb4b68f7a405ca18cd2d3` | 复用既有 draft_filename seed，增加生产前台可信日期投影 |

## C01-06 专用接口

`backend/deskpet/quality/corpus_c01_revision_prepare.py`：

```python
batch = compile_c01_revision_setup(case_id, setup_text, scenario_clock=instant)
async with open_c01_revision_fixture(
    path=host_state_path, memory_path=memory_path, principal=principal,
    authority_ref=authenticated_fixture_ref, batch=batch,
    classification_policy=policy, supported_filter_policies=policies,
) as (manager, result):
    ...
```

仅接受 C01-06 原 setup 文本/hash、显式 aware clock；不接收 case/oracle/query。入口重新编译比较 batch，拒绝更改映射。沿 Host S1 admitted source、既有 FixtureAnalysisDelivery、真实 DurableMemoryJobRunner 完成 B CREATE；必须观察实际 ACCEPTED application 和 APPLIED，不能把 IDLE 当准备成功。

公开幂等 replay 实际 CREATE plan 取得原 mutation receipt，沿既有 CorpusRevisionAuthority 核实原 source/旧 payload/精确 target，再使用 application 的真实 committed_revision 执行 REVISE。公开 receipt 回读 B revision1、A revision2，且 graph 只含同 ID 的 A；最后 IDLE 只证明已 APPLIED 后没有剩余分析任务。没有生产模型、伪 SDKRun 或自然语言自授权限；此发行器只在 fixture builder 中绑定，退出关闭 manager，正式 main 重开时不带此发行器。

`result` 包括 `source_pair / labels / setup_hash / outcome / fixture_executions / ingestion_receipt / application / request / initial_plan / plan / old_receipt / new_receipt / old_receipt_ref / new_receipt_ref / graph / clock / batch`。两个 label 使用公开 receipt operation，含实际 ID、revision、content_hash。helper 是新隔离目录的一次准备；不把再次运行返回 IDLE 伪装成已重建成功。

## C01-11 可信 today

`PrimaryForegroundContextPort` 接受 `clock` callable（默认真实 time.time）及 `clock_timezone`（Host 默认 Asia/Shanghai）。实际 `main.py` 前台构造从已拥有的 HumanMemoryV7Runtime 传入 `semantic_clock`，因此沿现有 factory 注入的 fixture clock 与 Memory builder/typed recall 使用同一来源；缺 runtime 时明确拒绝初始化。

仅当前无 TaskScope 的前台 Context 路径在准备时采样一次，加入通用 system 时间、时区和 today。它在 token 预算及 PreparedSdkContextSnapshotV1 内容 hash 之前进入上下文，不是外层改写 Provider request；USER 与历史文本不设置时钟。时区折算来自可信时间戳与 ZoneInfo，不解析用户的“今天”或 oracle。旧已冻结 Context 不回写，物理 lease/资源/timeout 不变，1秒 recall 和原模型 parameters 不变。TaskScope Context 的独立日期投影不在本叶范围，不据此宣称所有时间题可运行。

共享 `corpus_scoring/session`/dispatcher 由 Hegel 所有，本叶不修改。两例须在本叶控制通过并接入 dispatcher 后解除旧 block；C01-11 的 `provider_clock_projection` 不得仅因有 seed 就标已实现。

## 两个必要新控制

`backend/tests/quality/test_corpus_c01_revision_clock.py`：

- `test_c01_06_real_job_revision_receipts_reopen_selected_head`：原文/篡改映射拒绝、实际 APPLIED、同 ID 修订与双 receipt、关闭后 public manager 重开、typed recall 仅选 revision2。这里用共同 predicate 检查真实选择，是接口控制，不是原题模型评分。
- `test_c01_11_trusted_clock_real_frozen_context_and_public_sdk`：真实 seed/Host FIFO/SDK Context 发送原 USER，确定性 Provider 捕获 system；不含 setup/答案。公开 SDK evaluated_at 同时钟；UTC 跨日及用户伪日期不替换 Host today，已发送首轮保持原日期。

复用真实 foreground 测试 builder，仅增加可选 clock 依赖参数；不替换 Context/visibility/Memory/queue 方法。该控制不声称完整 main factory 或真实模型质量；main 构造的小 hunk 后续随正式 dispatcher/installed 组合实际消费。旧绿不重跑。
