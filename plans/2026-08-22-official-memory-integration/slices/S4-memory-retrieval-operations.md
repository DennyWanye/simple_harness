# S4 — Memory 有界检索、Embedding lineage 与 SQLite 运维

<!-- slice-status: completed -->

## Release unit

- MUST AC：AC-5、AC-7（2/8）
- Tasks：10/10
- 高风险系统：retrieval、embedding、SQLite operations（3/3）
- 依赖：S3 fresh v4 schema

## 文件影响

| 文件 | 修改 |
|---|---|
| `src/simple_harness_memory/backends/sqlite.py` | FTS5、bounded vector candidate、backup/restore、writer/checkpoint policy |
| `src/simple_harness_memory/features/retriever.py`, `rrf.py` | 有界candidate融合和deadline |
| `src/simple_harness_memory/embedders/base.py` | production lineage contract |
| `src/simple_harness_memory/embedders/factory.py`, `bge.py` | production fail-fast、local-only model load |
| `src/simple_harness_memory/core/manager.py` | explicit development/production builders和operational APIs |
| `tests/integration/test_sqlite_*` | scale/concurrency/backup/corruption/drift |

## Tasks

### S4-T1 — FTS5 lexical candidate [AC-5]

- v4创建identity/scope-aware external-content FTS5表与transactional sync trigger；query先identity/scope filter再
  MATCH/ORDER/LIMIT，不允许`SELECT all`后Python substring。
- query plan test必须出现FTS virtual index；20k/100k fixture验证候选数不随总记录线性返回。

### S4-T2 — Bounded vector candidate与RRF [AC-5]

- vector路径必须先按identity/scope、generation、recent/FTS candidate id和SQL LIMIT裁剪；只对有界candidate
  解码/相似度计算。每层max candidates/results/bytes/deadline是config hard cap。
- RRF只融合有限列表，稳定tie-breaker；timeout返回truncated/timeout而非无限工作。

### S4-T3 — 完整embedding lineage [AC-5]

- Embedder暴露不可空的kind/provider/model/revision/dimension/normalization/format fingerprint；index generation
  和每个vector保存lineage id。
- recall只读active generation；mismatch稳定drift。可配置lexical-only degrade，不静默跳过后宣称完整结果。

### S4-T4 — Two-generation reindex [AC-5]

- reindex创建building generation，bounded page处理，支持restart cursor；完成后核对row count/dimension/hash和
  sample search，再单事务切active。失败保留旧active并可cleanup building。
- 不允许半完成generation成为live。

### S4-T5 — Production builder与无运行时下载 [AC-5]

- `MemoryManager.build_production`要求显式embedder和resource path；缺失fail fast。Hash/Mock只允许
  `build_development`/test fixture。
- BGE使用`local_files_only`/已解析本地revision目录；生产路径禁止Hub download和隐式网络。cloud embedder
  要求endpoint/model/dim/revision并对response dimension校验。

### S4-T6 — SQLite concurrency/version capability [AC-5, AC-7]

- 一个manager一个owner connection；协程写、checkpoint、backup通过统一lock；busy timeout/retry有deadline。
- initialize记录sqlite version capability。当前受影响runtime禁止第二active writer owner，stable fail-fast；
  测writer/checkpoint竞争、busy、lease cleanup和close。

### S4-T7 — Backup/restore/corruption [AC-5]

- backup调用online backup到新文件，生成manifest(schema/lineage/SHA/time)；restore只在manager closed时执行，
  临时打开验证integrity/FK/schema/lineage，再原子replace，失败保留原库。
- 数据页损坏、错误schema、错误hash、残留WAL均产生stable code且不泄露内容。

### S4-T8 — Slice gate与文档 [AC-5, AC-7]

- Python 3.11/3.12/3.13跑FTS5/WAL/backup capability、scale、concurrency、drift、reindex、corruption lanes。
- 固化实际候选阈值和性能结果；更新architecture/operations guide/README；finalize。

## Required scenarios

| ID | 必须证明 |
|---|---|
| S4-C1 | 20k/100k下query plan与returned candidate均有界 |
| S4-C2 | lineage漂移不混用，reindex失败不切active |
| S4-C3 | 生产缺embedder失败，BGE不联网下载 |
| S4-C4 | 多协程可用；受影响SQLite第二writer明确拒绝 |
| S4-C5 | online backup恢复一致；corrupt/错误manifest保留原库 |
| S4-C6 | close/busy/checkpoint均在deadline内收敛 |
